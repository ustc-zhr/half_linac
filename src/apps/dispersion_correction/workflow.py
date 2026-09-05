from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import time

import numpy as np

from half_linac.src.apps.dispersion_correction.knobs import SymmetricKnobSet
from half_linac.src.apps.dispersion_correction.calibration import (
    effective_delta_for_integer_actuator_step,
)
from half_linac.src.apps.dispersion_correction.machine.base import MachineInterface
from half_linac.src.apps.dispersion_correction.machine.epics import EpicsMachine
from half_linac.src.apps.dispersion_correction.machine.offline import OfflineMachine
from half_linac.src.apps.dispersion_correction.models import (
    BPMReading,
    CorrectionRecommendation,
    CorrectionResult,
    CorrectionStep,
    DispersionMeasurement,
    MultiPlaneDispersionMeasurement,
    ResponseMatrixResult,
    RunConfig,
    SafetyStatus,
)
from half_linac.src.apps.dispersion_correction.physics import compute_effective_dispersion, momentum_delta, robust_average
from half_linac.src.apps.dispersion_correction.preflight import run_live_preflight
from half_linac.src.apps.dispersion_correction.safety import evaluate_safety
from half_linac.src.apps.dispersion_correction.solver import (
    response_mode_counts,
    response_result,
    solve_bounded_correction,
)


LogCallback = Callable[[str], None]
CancellationCallback = Callable[[], bool]
ProgressCallback = Callable[[str, int, int], None]
PreflightCallback = Callable[[object], None]
CorrectionMeasurementCallback = Callable[
    [int, int, str, DispersionMeasurement],
    None,
]


class WorkflowCancelled(RuntimeError):
    """Raised at a safe workflow boundary after an abort request."""


def create_machine(config: RunConfig) -> MachineInterface:
    backend_type = config.backend.type.lower()
    if backend_type == "offline":
        return OfflineMachine(config)
    if backend_type == "epics":
        return EpicsMachine(config)
    raise ValueError(f"Unsupported backend type: {config.backend.type}")


class AchromatWorkflow:
    """One-click effective dispersion correction workflow."""

    def __init__(
        self,
        config: RunConfig,
        machine: MachineInterface | None = None,
        log_callback: LogCallback | None = None,
        cancellation_callback: CancellationCallback | None = None,
        progress_callback: ProgressCallback | None = None,
        preflight_callback: PreflightCallback | None = None,
        correction_measurement_callback: CorrectionMeasurementCallback | None = None,
    ) -> None:
        self.config = config
        self.machine = machine if machine is not None else create_machine(config)
        self.log_callback = log_callback
        self.cancellation_callback = cancellation_callback
        self.progress_callback = progress_callback
        self.preflight_callback = preflight_callback
        self.correction_measurement_callback = correction_measurement_callback
        self.knob_names = tuple(knob.name for knob in config.knobs)
        self._progress_depth = 0
        self.last_live_preflight = None

    def measure_dispersion(
        self,
        samples: int | None = None,
    ) -> DispersionMeasurement | MultiPlaneDispersionMeasurement:
        report_progress = self._progress_depth == 0
        if report_progress:
            self._require_write_ready()
        self._progress_depth += 1
        try:
            return self._measure_dispersion(samples, report_progress)
        finally:
            self._progress_depth -= 1

    def _measure_dispersion(
        self,
        samples: int | None,
        report_progress: bool,
    ) -> DispersionMeasurement | MultiPlaneDispersionMeasurement:
        self._check_cancelled()
        samples = int(samples if samples is not None else self.config.measurement.samples_per_step)
        delta = momentum_delta(
            self.config.energy_knob.delta,
        )
        if self.config.energy_knob.round_actuator_step_to_integer:
            delta = effective_delta_for_integer_actuator_step(
                delta,
                self.config.energy_knob.calibration,
            )
        energy0 = self.machine.get_energy_setpoint_delta()
        try:
            self._check_cancelled()
            if report_progress:
                self._progress("Setting +Δp/p", 0, 5)
            self.machine.set_energy_delta(energy0 + delta)
            self.machine.wait_stable()
            self._check_cancelled()
            if report_progress:
                self._progress("Sampling +Δp/p", 1, 5)
            plus = self._average_bpm(samples)

            self._check_cancelled()
            if report_progress:
                self._progress("Setting -Δp/p", 2, 5)
            self.machine.set_energy_delta(energy0 - delta)
            self.machine.wait_stable()
            self._check_cancelled()
            if report_progress:
                self._progress("Sampling -Δp/p", 3, 5)
            minus = self._average_bpm(samples)
        finally:
            if report_progress:
                self._progress("Restoring energy", 4, 5)
            self.machine.set_energy_delta(energy0)
            self.machine.wait_stable()

        target_by_bpm = dict(
            zip(
                self.config.target_bpms,
                self.config.section.target_dispersion_mm,
            )
        )
        measurement_bpms = self.config.measurement_bpms
        measurements = tuple(
            compute_effective_dispersion(
                bpm_names=measurement_bpms,
                plus=plus,
                minus=minus,
                delta=delta,
                plane=plane,
                target_values_mm=tuple(
                    target_by_bpm.get(name, 0.0)
                    for name in measurement_bpms
                ),
                target_mask=tuple(
                    name in target_by_bpm
                    for name in measurement_bpms
                ),
            )
            for plane in self.config.measurement.planes
        )
        for measurement in measurements:
            rms = (
                measurement.measured_rms_mm
                if self.config.section.diagnostic_only
                else measurement.rms_mm
            )
            self._log(
                f"Measured eta_{measurement.plane} RMS: {rms:.6g} mm"
            )
        if report_progress:
            self._progress("Measurement complete", 5, 5)
        if len(measurements) == 1:
            return measurements[0]
        return MultiPlaneDispersionMeasurement(measurements)

    def build_response_matrix(self, knob_set: SymmetricKnobSet | None = None) -> ResponseMatrixResult:
        self._require_correction_section()
        report_progress = self._progress_depth == 0
        if report_progress:
            self._require_write_ready()
        self._progress_depth += 1
        try:
            return self._build_response_matrix(knob_set, report_progress)
        finally:
            self._progress_depth -= 1

    def _build_response_matrix(
        self,
        knob_set: SymmetricKnobSet | None,
        report_progress: bool,
    ) -> ResponseMatrixResult:
        self._check_cancelled()
        knob_set = knob_set or self._knob_set()
        base_knobs = self.machine.get_knobs(self.knob_names)
        base_snapshot = self.machine.snapshot()
        total_steps = 1 + 2 * len(self.knob_names)
        if report_progress:
            self._progress("Measuring baseline", 0, total_steps)
        base_measurement = self.measure_dispersion(self.config.measurement.samples_per_step)
        completed_steps = 1
        if report_progress:
            self._progress("Baseline measured", completed_steps, total_steps)
        matrix = np.zeros(
            (len(self.config.measurement_bpms), len(self.knob_names)),
            dtype=float,
        )
        scan_steps = knob_set.scan_steps()

        try:
            for column, knob_name in enumerate(self.knob_names):
                self._check_cancelled()
                step = np.zeros(len(self.knob_names), dtype=float)
                step[column] = scan_steps[column]

                if report_progress:
                    self._progress(f"{knob_name} · +scan", completed_steps, total_steps)
                plus_knobs = knob_set.add_step(base_knobs, step)
                self._apply_knobs(knob_set, plus_knobs)
                self.machine.wait_stable()
                self._check_cancelled()
                if not self.machine.is_safe():
                    raise RuntimeError(f"Machine unsafe during +scan of {knob_name}")
                d_plus = self.measure_dispersion(self.config.measurement.samples_per_step)
                completed_steps += 1

                if report_progress:
                    self._progress(f"{knob_name} · -scan", completed_steps, total_steps)
                minus_knobs = knob_set.add_step(base_knobs, -step)
                self._apply_knobs(knob_set, minus_knobs)
                self.machine.wait_stable()
                self._check_cancelled()
                if not self.machine.is_safe():
                    raise RuntimeError(f"Machine unsafe during -scan of {knob_name}")
                d_minus = self.measure_dispersion(self.config.measurement.samples_per_step)
                completed_steps += 1
                if report_progress:
                    self._progress(f"{knob_name} measured", completed_steps, total_steps)

                matrix[:, column] = (d_plus.values_mm - d_minus.values_mm) / (2.0 * scan_steps[column])
                self.machine.restore(base_snapshot)
                self.machine.wait_stable()
                self._check_cancelled()
                self._log(f"Measured response column: {knob_name}")
        finally:
            self.machine.restore(base_snapshot)
            self.machine.wait_stable()

        result = response_result(
            matrix,
            self.config.measurement_bpms,
            self.knob_names,
            base_measurement,
        )
        self._log(
            "Measured response matrix:\n"
            + np.array2string(result.matrix, precision=8, suppress_small=False)
        )
        self._validate_response_quality(result)
        if report_progress:
            self._progress("Response complete", total_steps, total_steps)
        return result

    def run(self) -> CorrectionResult:
        self._require_correction_section()
        self._require_write_ready()
        self._progress_depth += 1
        try:
            return self._run_correction()
        finally:
            self._progress_depth -= 1

    def apply_recommendation(
        self,
        recommendation: CorrectionRecommendation,
    ) -> CorrectionResult:
        """Apply one reviewed correction step, remeasure, and roll back on rejection."""

        self._require_correction_section()
        self._require_write_ready()
        self._progress_depth += 1
        try:
            return self._apply_recommendation(recommendation)
        finally:
            self._progress_depth -= 1

    def apply_design_targets(
        self,
        target_values: Mapping[str, float],
        *,
        reviewed_baseline: Mapping[str, float],
        max_changes: Mapping[str, float],
    ) -> dict[str, object]:
        """Apply reviewed lattice-design K1 targets with rollback on failure."""

        self._require_correction_section()
        self._require_write_ready()
        targets = {str(name): float(value) for name, value in target_values.items()}
        baseline = {
            str(name): float(value)
            for name, value in reviewed_baseline.items()
        }
        limits = {str(name): float(value) for name, value in max_changes.items()}
        required = set(targets)
        if not required:
            raise ValueError("At least one design K1 target is required")
        if set(baseline) != required or set(limits) != required:
            raise ValueError(
                "Design K1 review requires matching baseline, target, and limit devices"
            )
        if not all(
            np.isfinite(value)
            for value in (*targets.values(), *baseline.values())
        ):
            raise ValueError("Design K1 baseline and targets must be finite")
        if not all(
            np.isfinite(value) and value > 0
            for value in limits.values()
        ):
            raise ValueError("Design K1 change limits must be finite and positive")
        for device in targets:
            change = abs(targets[device] - baseline[device])
            if change > limits[device] + 1.0e-15:
                raise ValueError(
                    f"{device} design change {change:g} exceeds configured "
                    f"limit {limits[device]:g}"
                )

        initial_state = self.machine.snapshot()
        self._validate_reviewed_device_baseline(
            initial_state,
            baseline,
            operation="Design K1",
        )
        baseline_orbit = self._average_bpm(
            self.config.measurement.samples_per_step
        )
        target_writer = getattr(self.machine, "set_device_targets", None)
        if not callable(target_writer):
            raise RuntimeError("The selected backend cannot write explicit K1 targets")

        try:
            self._check_cancelled()
            self._progress("Applying design K1 targets", 1, 3)
            target_writer(targets)
            self.machine.wait_stable()
            self._check_cancelled()
            if not self.machine.is_safe():
                raise RuntimeError("Machine unsafe after applying design K1 targets")
            safety = evaluate_safety(
                self.config.safety,
                baseline_orbit,
                self._average_bpm(self.config.measurement.samples_per_step),
            )
            if not safety.ok:
                raise RuntimeError(safety.reason)
            final_state = self.machine.snapshot()
        except Exception as exc:
            try:
                self.machine.restore(initial_state)
                self.machine.wait_stable()
            except Exception as restore_exc:
                raise RuntimeError(
                    f"{exc}; design K1 rollback failed: {restore_exc}"
                ) from exc
            raise RuntimeError(
                f"{exc}; pre-write quadrupole setpoints restored"
            ) from exc

        self._progress("Design K1 applied", 3, 3)
        return {
            "operation": "design-k1",
            "baseline_values": baseline,
            "target_values": targets,
            "final_values": {
                name: float(final_state.device_values[name])
                for name in targets
            },
            "max_orbit_change_mm": safety.max_orbit_change_mm,
        }

    def restore_correction_state(
        self,
        target_values: Mapping[str, float],
        *,
        reviewed_baseline: Mapping[str, float],
        max_changes: Mapping[str, float],
    ) -> dict[str, object]:
        """Restore reviewed pre-correction quadrupole targets safely."""

        self._require_write_ready()
        targets = {str(name): float(value) for name, value in target_values.items()}
        baseline = {
            str(name): float(value)
            for name, value in reviewed_baseline.items()
        }
        limits = {str(name): float(value) for name, value in max_changes.items()}
        required = set(targets)
        if not required:
            raise ValueError("At least one pre-correction target is required")
        if set(baseline) != required or set(limits) != required:
            raise ValueError(
                "Correction restore requires matching baseline, target, and limit devices"
            )
        if not all(
            np.isfinite(value)
            for value in (*targets.values(), *baseline.values())
        ):
            raise ValueError("Correction restore baseline and targets must be finite")
        if not all(
            np.isfinite(value) and value > 0
            for value in limits.values()
        ):
            raise ValueError("Correction restore limits must be finite and positive")
        for device in targets:
            change = abs(targets[device] - baseline[device])
            if change > limits[device] + 1.0e-15:
                raise ValueError(
                    f"{device} restore change {change:g} exceeds configured "
                    f"limit {limits[device]:g}"
                )

        pre_restore_state = self.machine.snapshot()
        self._validate_reviewed_device_baseline(
            pre_restore_state,
            baseline,
            operation="Correction restore",
        )
        baseline_orbit = self._average_bpm(
            self.config.measurement.samples_per_step
        )
        target_writer = getattr(self.machine, "set_device_targets", None)
        if not callable(target_writer):
            raise RuntimeError(
                "The selected backend cannot restore explicit quadrupole targets"
            )

        try:
            self._check_cancelled()
            self._progress("Restoring pre-correction quadrupole targets", 1, 3)
            target_writer(targets)
            self.machine.wait_stable()
            self._check_cancelled()
            if not self.machine.is_safe():
                raise RuntimeError(
                    "Machine unsafe after restoring pre-correction targets"
                )
            safety = evaluate_safety(
                self.config.safety,
                baseline_orbit,
                self._average_bpm(self.config.measurement.samples_per_step),
            )
            if not safety.ok:
                raise RuntimeError(safety.reason)
            final_state = self.machine.snapshot()
        except Exception as exc:
            try:
                self.machine.restore(pre_restore_state)
                self.machine.wait_stable()
            except Exception as restore_exc:
                raise RuntimeError(
                    f"{exc}; correction-restore rollback failed: {restore_exc}"
                ) from exc
            raise RuntimeError(
                f"{exc}; pre-restore quadrupole setpoints restored"
            ) from exc

        self._progress("Pre-correction state restored", 3, 3)
        return {
            "operation": "restore-correction",
            "baseline_values": baseline,
            "target_values": targets,
            "final_values": {
                name: float(final_state.device_values[name])
                for name in targets
            },
            "max_orbit_change_mm": safety.max_orbit_change_mm,
        }

    def _apply_recommendation(
        self,
        recommendation: CorrectionRecommendation,
    ) -> CorrectionResult:
        if recommendation.measurement.bpm_names != self.config.measurement_bpms:
            raise ValueError("Recommendation BPMs do not match the current configuration")
        if recommendation.response.knob_names != self.knob_names:
            raise ValueError("Recommendation knobs do not match the current configuration")
        if tuple(recommendation.delta_knobs) != self.knob_names:
            raise ValueError("Recommendation knob order does not match the current configuration")
        if not recommendation.ready:
            raise ValueError(recommendation.reason)

        self._progress("Preparing reviewed correction", 0, 4)
        initial_state = self.machine.snapshot()
        initial_knobs = self.machine.get_knobs(self.knob_names)
        knob_set = SymmetricKnobSet(self.config.knobs, initial_knobs)
        step_vector = knob_set.vector_from_mapping(recommendation.delta_knobs)
        target_knobs = knob_set.add_step(initial_knobs, step_vector)
        if not knob_set.within_total_limits(target_knobs):
            raise ValueError("Reviewed correction exceeds configured cumulative limits")

        self._validate_recommendation_baseline(
            initial_state,
            recommendation,
        )
        baseline_reference = self._average_bpm(
            self.config.measurement.samples_per_step
        )
        steps: list[CorrectionStep] = []
        trial_state = None
        try:
            self._check_cancelled()
            self._progress("Applying reviewed quadrupole targets", 1, 4)
            self._apply_reviewed_knobs(
                knob_set,
                target_knobs,
                recommendation,
            )
            self.machine.wait_stable()
            trial_state = self.machine.snapshot()
            self._check_cancelled()
            if not self.machine.is_safe():
                raise RuntimeError("Machine unsafe after reviewed correction step")

            safety_status = evaluate_safety(
                self.config.safety,
                baseline_reference,
                self._average_bpm(self.config.measurement.samples_per_step),
            )
            if not safety_status.ok:
                raise RuntimeError(safety_status.reason)

            self._progress("Remeasuring dispersion", 2, 4)
            trial_measurement = self.measure_dispersion(
                self.config.measurement.final_samples
            )
            target_rms = recommendation.measurement.rms_mm * (
                1.0 - self.config.solver.min_step_improvement
            )
            accepted = trial_measurement.rms_mm <= target_rms
            steps.append(
                CorrectionStep(
                    iteration=1,
                    gain=self.config.solver.gain,
                    delta_knobs=dict(recommendation.delta_knobs),
                    accepted=accepted,
                    reason=(
                        "Accepted"
                        if accepted
                        else "D_eff RMS did not improve enough; initial state restored"
                    ),
                    rms_before_mm=recommendation.measurement.rms_mm,
                    rms_after_mm=trial_measurement.rms_mm,
                    measurement_before=recommendation.measurement,
                    measurement_after=trial_measurement,
                    knobs_before=dict(initial_knobs),
                    knobs_trial=dict(target_knobs),
                    device_values_before=dict(initial_state.device_values),
                    device_values_trial=dict(trial_state.device_values),
                    restored=not accepted,
                )
            )
            if not accepted:
                self._progress("Restoring initial state", 3, 4)
                self.machine.restore(initial_state)
                self.machine.wait_stable()
                final_knobs = self.machine.get_knobs(self.knob_names)
                final_measurement = recommendation.measurement
                reason = steps[-1].reason
            else:
                final_knobs = self.machine.get_knobs(self.knob_names)
                final_measurement = trial_measurement
                reason = "Accepted reviewed correction"

            result = CorrectionResult(
                success=accepted,
                reason=reason,
                initial=recommendation.measurement,
                final=final_measurement,
                initial_knobs=dict(initial_knobs),
                final_knobs=final_knobs,
                steps=tuple(steps),
                response=recommendation.response,
                safety=SafetyStatus(ok=True, reason="OK"),
            )
            self._progress("Reviewed correction complete", 4, 4)
            return result
        except WorkflowCancelled:
            if trial_state is not None and not steps:
                steps.append(
                    CorrectionStep(
                        iteration=1,
                        gain=self.config.solver.gain,
                        delta_knobs=dict(recommendation.delta_knobs),
                        accepted=False,
                        reason="Aborted during trial; initial state restored",
                        rms_before_mm=recommendation.measurement.rms_mm,
                        measurement_before=recommendation.measurement,
                        knobs_before=dict(initial_knobs),
                        knobs_trial=dict(target_knobs),
                        device_values_before=dict(initial_state.device_values),
                        device_values_trial=dict(trial_state.device_values),
                        restored=True,
                    )
                )
            return self._restore_after_abort(
                initial_state,
                recommendation.measurement,
                initial_knobs,
                steps,
                recommendation.response,
            )
        except Exception as exc:
            if trial_state is not None and not steps:
                steps.append(
                    CorrectionStep(
                        iteration=1,
                        gain=self.config.solver.gain,
                        delta_knobs=dict(recommendation.delta_knobs),
                        accepted=False,
                        reason=f"Trial failed; initial state restored: {exc}",
                        rms_before_mm=recommendation.measurement.rms_mm,
                        measurement_before=recommendation.measurement,
                        knobs_before=dict(initial_knobs),
                        knobs_trial=dict(target_knobs),
                        device_values_before=dict(initial_state.device_values),
                        device_values_trial=dict(trial_state.device_values),
                        restored=True,
                    )
                )
            return self._failure_result_after_restore(
                exc,
                initial_state,
                recommendation.measurement,
                initial_knobs,
                steps,
                recommendation.response,
            )

    def _run_correction(self) -> CorrectionResult:
        total_steps = self.config.solver.max_iter + 2
        self._progress("Preparing correction", 0, total_steps)
        initial_state = self.machine.snapshot()
        initial_knobs = self.machine.get_knobs(self.knob_names)
        knob_set = SymmetricKnobSet(self.config.knobs, initial_knobs)
        baseline_reference = self._average_bpm(self.config.measurement.samples_per_step)

        initial_measurement = self.measure_dispersion(self.config.measurement.samples_per_step)
        self._correction_measurement(
            0,
            self.config.solver.max_iter,
            "initial",
            initial_measurement,
        )
        self._progress("Initial D_eff measured", 1, total_steps)
        best_measurement = initial_measurement
        best_state = self.machine.snapshot()
        last_response: ResponseMatrixResult | None = None
        steps: list[CorrectionStep] = []
        safety_status = SafetyStatus(ok=True, reason="OK")

        try:
            for iteration in range(1, self.config.solver.max_iter + 1):
                self._check_cancelled()
                self._log(f"Starting iteration {iteration}")
                refresh_response = last_response is None or self.config.solver.response_update == "every_iteration"
                if refresh_response:
                    self._progress(
                        f"Iteration {iteration}/{self.config.solver.max_iter} · response",
                        iteration,
                        total_steps,
                    )
                    response = self.build_response_matrix(knob_set)
                    last_response = response
                    solve_measurement = response.measurement
                else:
                    response = last_response
                    solve_measurement = best_measurement
                    self._log("Reusing response matrix from the first iteration")

                current_knobs = self.machine.get_knobs(self.knob_names)
                valid_rows = (
                    solve_measurement.correction_valid
                    & np.all(np.isfinite(response.matrix), axis=1)
                )
                if not np.any(valid_rows):
                    steps.append(
                        CorrectionStep(
                            iteration=iteration,
                            gain=self.config.solver.gain,
                            delta_knobs={},
                            accepted=False,
                            reason="No valid BPM rows for solve",
                            rms_before_mm=best_measurement.rms_mm,
                            measurement_before=solve_measurement,
                            knobs_before=dict(current_knobs),
                        )
                    )
                    break

                self._progress(
                    f"Iteration {iteration}/{self.config.solver.max_iter} · solving",
                    iteration,
                    total_steps,
                )
                delta_knobs, singular_values, condition = solve_bounded_correction(
                    response.matrix[valid_rows, :],
                    solve_measurement.residual_values_mm[valid_rows],
                    self.config.solver.svd_cut,
                    self.config.solver.gain,
                    knob_set.limits(),
                    self.config.solver.max_step_fraction,
                    knob_set.vector_from_mapping(current_knobs),
                    knob_set.vector_from_mapping(initial_knobs),
                    self.config.solver.regularization,
                )
                self._log(
                    "Solved correction: "
                    f"du={delta_knobs.tolist()}, singular_values={singular_values.tolist()}, cond={condition:.6g}"
                )
                if not np.any(np.abs(delta_knobs) > 0):
                    steps.append(
                        CorrectionStep(
                            iteration=iteration,
                            gain=self.config.solver.gain,
                            delta_knobs=knob_set.mapping_from_vector(delta_knobs),
                            accepted=False,
                            reason="SVD correction was zero",
                            rms_before_mm=best_measurement.rms_mm,
                            measurement_before=solve_measurement,
                            knobs_before=dict(current_knobs),
                        )
                    )
                    break

                accepted = False
                self._check_cancelled()
                trial_knobs = knob_set.add_step(current_knobs, delta_knobs)
                step_mapping = knob_set.mapping_from_vector(delta_knobs)
                state_before = self.machine.snapshot()

                if not knob_set.within_total_limits(trial_knobs):
                    raise RuntimeError("Bounded solver produced a knob value outside configured limits")

                self._progress(
                    f"Iteration {iteration}/{self.config.solver.max_iter} · applying",
                    iteration,
                    total_steps,
                )
                self._apply_knobs(knob_set, trial_knobs)
                self.machine.wait_stable()
                trial_state = self.machine.snapshot()
                self._check_cancelled()
                if not self.machine.is_safe():
                    self.machine.restore(state_before)
                    self.machine.wait_stable()
                    steps.append(
                        CorrectionStep(
                            iteration=iteration,
                            gain=self.config.solver.gain,
                            delta_knobs=step_mapping,
                            accepted=False,
                            reason="Machine unsafe after trial step",
                            rms_before_mm=best_measurement.rms_mm,
                            measurement_before=solve_measurement,
                            knobs_before=dict(current_knobs),
                            knobs_trial=dict(trial_knobs),
                            device_values_before=dict(state_before.device_values),
                            device_values_trial=dict(trial_state.device_values),
                            restored=True,
                        )
                    )
                else:
                    current_reference = self._average_bpm(self.config.measurement.samples_per_step)
                    safety_status = evaluate_safety(
                        self.config.safety,
                        baseline_reference,
                        current_reference,
                    )
                    if not safety_status.ok:
                        self.machine.restore(state_before)
                        self.machine.wait_stable()
                        steps.append(
                            CorrectionStep(
                                iteration=iteration,
                                gain=self.config.solver.gain,
                                delta_knobs=step_mapping,
                                accepted=False,
                                reason=safety_status.reason,
                                rms_before_mm=best_measurement.rms_mm,
                                measurement_before=solve_measurement,
                                knobs_before=dict(current_knobs),
                                knobs_trial=dict(trial_knobs),
                                device_values_before=dict(state_before.device_values),
                                device_values_trial=dict(trial_state.device_values),
                                restored=True,
                            )
                        )
                    else:
                        self._progress(
                            f"Iteration {iteration}/{self.config.solver.max_iter} · validating",
                            iteration,
                            total_steps,
                        )
                        trial_measurement = self.measure_dispersion(self.config.measurement.samples_per_step)
                        target_rms = best_measurement.rms_mm * (1.0 - self.config.solver.min_step_improvement)
                        if trial_measurement.rms_mm <= target_rms:
                            self._correction_measurement(
                                iteration,
                                self.config.solver.max_iter,
                                "accepted",
                                trial_measurement,
                            )
                            best_measurement = trial_measurement
                            best_state = self.machine.snapshot()
                            steps.append(
                                CorrectionStep(
                                    iteration=iteration,
                                    gain=self.config.solver.gain,
                                    delta_knobs=step_mapping,
                                    accepted=True,
                                    reason="Accepted",
                                    rms_before_mm=solve_measurement.rms_mm,
                                    rms_after_mm=trial_measurement.rms_mm,
                                    measurement_before=solve_measurement,
                                    measurement_after=trial_measurement,
                                    knobs_before=dict(current_knobs),
                                    knobs_trial=dict(trial_knobs),
                                    device_values_before=dict(state_before.device_values),
                                    device_values_trial=dict(trial_state.device_values),
                                )
                            )
                            accepted = True
                            self._progress(
                                f"Iteration {iteration}/{self.config.solver.max_iter} accepted",
                                iteration + 1,
                                total_steps,
                            )
                            self._log(f"Accepted iteration {iteration} gain={self.config.solver.gain:g}")
                        else:
                            self._correction_measurement(
                                iteration,
                                self.config.solver.max_iter,
                                "rejected",
                                trial_measurement,
                            )
                            self.machine.restore(state_before)
                            self.machine.wait_stable()
                            steps.append(
                                CorrectionStep(
                                    iteration=iteration,
                                    gain=self.config.solver.gain,
                                    delta_knobs=step_mapping,
                                    accepted=False,
                                    reason="D_eff RMS did not improve enough",
                                    rms_before_mm=best_measurement.rms_mm,
                                    rms_after_mm=trial_measurement.rms_mm,
                                    measurement_before=solve_measurement,
                                    measurement_after=trial_measurement,
                                    knobs_before=dict(current_knobs),
                                    knobs_trial=dict(trial_knobs),
                                    device_values_before=dict(state_before.device_values),
                                    device_values_trial=dict(trial_state.device_values),
                                    restored=True,
                                )
                            )

                if not accepted:
                    self._log(f"Stopping at iteration {iteration}: no accepted trial step")
                    break
        except WorkflowCancelled:
            return self._restore_after_abort(
                initial_state,
                initial_measurement,
                initial_knobs,
                steps,
                last_response,
            )
        except Exception as exc:
            return self._failure_result_after_restore(
                exc,
                initial_state,
                initial_measurement,
                initial_knobs,
                steps,
                last_response,
            )

        try:
            self._check_cancelled()
            self._progress("Final verification", total_steps - 1, total_steps)
            self.machine.restore(best_state)
            self.machine.wait_stable()
            self._check_cancelled()
            final_measurement = self.measure_dispersion(self.config.measurement.final_samples)
            self._correction_measurement(
                len(steps),
                self.config.solver.max_iter,
                "final",
                final_measurement,
            )
            final_knobs = self.machine.get_knobs(self.knob_names)
            safety_status = evaluate_safety(
                self.config.safety,
                baseline_reference,
                self._average_bpm(self.config.measurement.samples_per_step),
            )
        except WorkflowCancelled:
            return self._restore_after_abort(
                initial_state,
                initial_measurement,
                initial_knobs,
                steps,
                last_response,
            )
        except Exception as exc:
            return self._failure_result_after_restore(
                exc,
                initial_state,
                initial_measurement,
                initial_knobs,
                steps,
                last_response,
            )
        success = (
            safety_status.ok
            and final_measurement.rms_mm > 0
            and initial_measurement.rms_mm / final_measurement.rms_mm >= self.config.solver.success_min_improvement
        )
        reason = "Accepted" if success else "Improvement below success threshold"
        if not safety_status.ok:
            reason = safety_status.reason
        if not success:
            try:
                self.machine.restore(initial_state)
                self.machine.wait_stable()
                final_knobs = self.machine.get_knobs(self.knob_names)
                final_measurement = initial_measurement
            except Exception as exc:
                reason = f"{reason}; initial-state restore failed: {exc}"
                safety_status = SafetyStatus(ok=False, reason=reason)
        result = CorrectionResult(
            success=success,
            reason=reason,
            initial=initial_measurement,
            final=final_measurement,
            initial_knobs=initial_knobs,
            final_knobs=final_knobs,
            steps=tuple(steps),
            response=last_response,
            safety=safety_status,
        )
        self._progress("Correction complete", total_steps, total_steps)
        return result

    def _average_bpm(self, samples: int) -> BPMReading:
        readings = []
        for index in range(samples):
            self._check_cancelled()
            readings.append(self.machine.read_bpm(self.config.measurement_bpms))
            if index + 1 < samples and self.config.measurement.sample_interval_s > 0:
                time.sleep(self.config.measurement.sample_interval_s)
        return robust_average(readings)

    def _knob_set(self) -> SymmetricKnobSet:
        return SymmetricKnobSet(self.config.knobs, self.machine.get_knobs(self.knob_names))

    def _apply_knobs(self, knob_set: SymmetricKnobSet, knob_values: Mapping[str, float]) -> None:
        self._check_cancelled()
        self.machine.set_knobs(knob_values)
        self.machine.apply_device_deltas(knob_set.device_deltas(knob_values))

    def _apply_reviewed_knobs(
        self,
        knob_set: SymmetricKnobSet,
        knob_values: Mapping[str, float],
        recommendation: CorrectionRecommendation,
    ) -> None:
        self._check_cancelled()
        self.machine.set_knobs(knob_values)
        target_writer = getattr(self.machine, "set_device_targets", None)
        if recommendation.target_device_values and callable(target_writer):
            target_writer(recommendation.target_device_values)
            return
        self.machine.apply_device_deltas(knob_set.device_deltas(knob_values))

    def _validate_recommendation_baseline(
        self,
        snapshot,
        recommendation: CorrectionRecommendation,
    ) -> None:
        if self.config.backend.type.lower() != "epics":
            return
        if not recommendation.baseline_device_values:
            raise RuntimeError(
                "Reviewed correction has no quadrupole baseline; run live checks and recompute"
            )
        required = set(recommendation.device_deltas)
        if set(recommendation.baseline_device_values) != required:
            raise RuntimeError(
                "Reviewed correction does not contain a baseline for every quadrupole"
            )
        if set(recommendation.target_device_values) != required:
            raise RuntimeError(
                "Reviewed correction does not contain a target for every quadrupole"
            )
        self._validate_reviewed_device_baseline(
            snapshot,
            recommendation.baseline_device_values,
            operation="Reviewed correction",
        )

    def _validate_reviewed_device_baseline(
        self,
        snapshot,
        baseline: Mapping[str, float],
        *,
        operation: str,
    ) -> None:
        tolerance_reader = getattr(
            self.machine,
            "quadrupole_readback_tolerance",
            None,
        )
        for device, reviewed_value in baseline.items():
            if device not in snapshot.device_values:
                raise RuntimeError(
                    f"{operation} baseline is missing current readback for {device}"
                )
            tolerance = (
                float(tolerance_reader(device))
                if callable(tolerance_reader)
                else 1.0e-6
            )
            actual = float(snapshot.device_values[device])
            if abs(actual - float(reviewed_value)) > tolerance:
                raise RuntimeError(
                    f"{device} changed after review: reviewed={reviewed_value:g}, "
                    f"current={actual:g}, tolerance={tolerance:g}; refresh and review again"
                )

    def _restore_after_abort(
        self,
        initial_state,
        initial_measurement: DispersionMeasurement,
        initial_knobs: Mapping[str, float],
        steps: Sequence[CorrectionStep],
        response: ResponseMatrixResult | None,
    ) -> CorrectionResult:
        reason = "Aborted; initial state restored"
        final_knobs = dict(initial_knobs)
        restored = True
        try:
            self.machine.restore(initial_state)
            self.machine.wait_stable()
            final_knobs = self.machine.get_knobs(self.knob_names)
        except Exception as exc:
            restored = False
            reason = f"Aborted; initial-state restore failed: {exc}"
        self._log(reason)
        return CorrectionResult(
            success=False,
            reason=reason,
            initial=initial_measurement,
            final=initial_measurement,
            initial_knobs=dict(initial_knobs),
            final_knobs=final_knobs,
            steps=tuple(steps),
            response=response,
            safety=SafetyStatus(
                ok=restored,
                reason="Restored after abort" if restored else reason,
            ),
        )

    def _failure_result_after_restore(
        self,
        error: Exception,
        initial_state,
        initial_measurement: DispersionMeasurement,
        initial_knobs: Mapping[str, float],
        steps: Sequence[CorrectionStep],
        response: ResponseMatrixResult | None,
    ) -> CorrectionResult:
        reason = str(error)
        final_knobs = dict(initial_knobs)
        try:
            self.machine.restore(initial_state)
            self.machine.wait_stable()
            final_knobs = self.machine.get_knobs(self.knob_names)
        except Exception as restore_exc:
            reason = f"{reason}; initial-state restore failed: {restore_exc}"
        return CorrectionResult(
            success=False,
            reason=reason,
            initial=initial_measurement,
            final=initial_measurement,
            initial_knobs=dict(initial_knobs),
            final_knobs=final_knobs,
            steps=tuple(steps),
            response=response,
            safety=SafetyStatus(ok=False, reason=reason),
        )

    def _require_write_ready(self) -> None:
        if self.config.section.model_only:
            raise PermissionError(
                "This dispersion section is model-only; machine measurement and correction are blocked"
            )
        if self.config.backend.type.lower() == "offline":
            return
        if self.config.backend.mode != "write_enabled":
            raise PermissionError("Machine writes require backend.mode=write_enabled")
        result = run_live_preflight(self.config, self.machine)
        self.last_live_preflight = result
        if self.preflight_callback is not None:
            self.preflight_callback(result)
        if not result.ok:
            blockers = (*result.static.blockers, *result.blockers)
            raise RuntimeError("Live preflight failed: " + "; ".join(blockers))

    def _require_correction_section(self) -> None:
        if self.config.section.diagnostic_only:
            raise PermissionError(
                "This dispersion section is measurement-only; quadrupole "
                "response and correction are disabled"
            )
        if self.config.measurement.plane == "xy":
            raise PermissionError(
                "Joint x/y response and correction are not enabled in this "
                "measurement-only implementation stage"
            )

    def _validate_response_quality(self, result: ResponseMatrixResult) -> None:
        singular_values = np.asarray(result.singular_values, dtype=float)
        if singular_values.size == 0 or not np.all(np.isfinite(singular_values)):
            raise RuntimeError("Response matrix has no finite singular values")
        largest = float(np.max(singular_values))
        retained_rank, required_rank, target_count, knob_count = response_mode_counts(
            result,
            self.config.solver.svd_cut,
        )
        if retained_rank == 0:
            raise RuntimeError(
                "Response matrix quality check failed: "
                "no SVD modes were retained "
                f"at svd_cut={self.config.solver.svd_cut:g}"
            )
        if retained_rank < required_rank:
            ratios = singular_values / largest
            self._log(
                f"Response matrix is rank-reduced: retained {retained_rank}/"
                f"{required_rank} required SVD modes for {knob_count} knobs and "
                f"{target_count} target BPMs at svd_cut="
                f"{self.config.solver.svd_cut:g}; singular-value ratios="
                + np.array2string(ratios, precision=6, suppress_small=False)
                + ". The truncated-SVD solver will use only the retained modes."
            )

    def _check_cancelled(self) -> None:
        if self.cancellation_callback is not None and self.cancellation_callback():
            raise WorkflowCancelled("Operation aborted")

    def _log(self, message: str) -> None:
        if self.log_callback is not None:
            self.log_callback(message)

    def _progress(self, stage: str, current: int, total: int) -> None:
        if self.progress_callback is not None:
            self.progress_callback(stage, current, total)

    def _correction_measurement(
        self,
        iteration: int,
        total: int,
        state: str,
        measurement: DispersionMeasurement,
    ) -> None:
        if self.correction_measurement_callback is not None:
            self.correction_measurement_callback(
                iteration,
                total,
                state,
                measurement,
            )
