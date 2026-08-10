from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace

import numpy as np

from half_linac.src.apps.dispersion_correction.knobs import SymmetricKnobSet
from half_linac.src.apps.dispersion_correction.models import (
    JointCorrectionResult,
    JointCorrectionStep,
    JointResponseAnalysisResult,
    MultiPlaneDispersionMeasurement,
    RunConfig,
)
from half_linac.src.apps.dispersion_correction.solver import (
    condition_number,
    solve_bounded_correction,
)
from half_linac.src.apps.dispersion_correction.workflow import (
    AchromatWorkflow,
    WorkflowCancelled,
)


LogCallback = Callable[[str], None]
CancellationCallback = Callable[[], bool]
ProgressCallback = Callable[[str, int, int], None]
PreflightCallback = Callable[[object], None]
MeasurementCallback = Callable[[int, int, str, MultiPlaneDispersionMeasurement], None]


class JointResponseAnalyzer:
    """Measure a stacked ηx/ηy Q response and prepare a read-only preview."""

    def __init__(
        self,
        config: RunConfig,
        *,
        log_callback: LogCallback | None = None,
        cancellation_callback: CancellationCallback | None = None,
        progress_callback: ProgressCallback | None = None,
        preflight_callback: PreflightCallback | None = None,
        measurement_callback: MeasurementCallback | None = None,
        machine=None,
    ) -> None:
        analysis = config.section.joint_response_analysis
        if not analysis.enabled:
            raise ValueError("This section has no joint response analysis configuration")
        if config.measurement.plane != "xy":
            raise ValueError("Joint response analysis requires a two-plane measurement")

        # The derived runtime config exposes analysis knobs to the machine adapter.
        # It is never passed to correction/apply APIs.
        runtime_config = replace(
            config,
            knobs=analysis.knobs,
            section=replace(config.section, diagnostic_only=False),
        )
        self.config = config
        self.analysis = analysis
        self.workflow = AchromatWorkflow(
            runtime_config,
            machine=machine,
            log_callback=log_callback,
            cancellation_callback=cancellation_callback,
            progress_callback=None,
            preflight_callback=preflight_callback,
        )
        self.log_callback = log_callback
        self.cancellation_callback = cancellation_callback
        self.progress_callback = progress_callback
        self.measurement_callback = measurement_callback
        self._initial_knob_values: dict[str, float] | None = None
        self._automatic_generation: tuple[int, int] | None = None

    def run(self) -> JointResponseAnalysisResult:
        knob_names = tuple(knob.name for knob in self.analysis.knobs)
        knob_set = SymmetricKnobSet(self.analysis.knobs)
        machine = self.workflow.machine
        total = 1 + 2 * len(knob_names)
        self._progress("Measuring joint baseline", 0, total)
        baseline_snapshot = machine.snapshot()
        baseline_knobs = machine.get_knobs(knob_names)
        if self._initial_knob_values is None:
            self._initial_knob_values = dict(baseline_knobs)
        baseline = self.workflow.measure_dispersion(
            self.config.measurement.samples_per_step
        )
        if not isinstance(baseline, MultiPlaneDispersionMeasurement):
            raise RuntimeError("Joint analysis did not receive ηx and ηy measurements")
        self._progress("Joint baseline measured", 1, total)

        matrices = {
            "x": np.zeros((len(self.config.measurement_bpms), len(knob_names))),
            "y": np.zeros((len(self.config.measurement_bpms), len(knob_names))),
        }
        scan_steps = knob_set.scan_steps()
        completed = 1
        self.workflow._progress_depth += 1
        try:
            for column, knob_name in enumerate(knob_names):
                self._check_cancelled()
                step = np.zeros(len(knob_names), dtype=float)
                step[column] = scan_steps[column]
                measurements: dict[str, MultiPlaneDispersionMeasurement] = {}
                for sign, label in ((1.0, "+scan"), (-1.0, "-scan")):
                    self._progress(
                        f"{knob_name} · {label}",
                        completed,
                        total,
                    )
                    values = knob_set.add_step(baseline_knobs, sign * step)
                    self._apply_knobs(knob_set, values)
                    machine.wait_stable()
                    self._check_cancelled()
                    if not machine.is_safe():
                        raise RuntimeError(
                            f"Machine unsafe during {label} of {knob_name}"
                        )
                    measured = self.workflow.measure_dispersion(
                        self.config.measurement.samples_per_step
                    )
                    if not isinstance(measured, MultiPlaneDispersionMeasurement):
                        raise RuntimeError("Joint Q scan lost one measurement plane")
                    measurements[label] = measured
                    completed += 1
                    self._progress(
                        f"{knob_name} · {label} complete",
                        completed,
                        total,
                    )
                    machine.restore(baseline_snapshot)
                    machine.wait_stable()

                for plane in ("x", "y"):
                    plus = measurements["+scan"].for_plane(plane)
                    minus = measurements["-scan"].for_plane(plane)
                    matrices[plane][:, column] = (
                        plus.values_mm - minus.values_mm
                    ) / (2.0 * scan_steps[column])
                self._log(f"Measured joint response column: {knob_name}")
        finally:
            self.workflow._progress_depth -= 1
            machine.restore(baseline_snapshot)
            machine.wait_stable()

        result = self._build_result(
            baseline,
            matrices,
            knob_names,
            knob_set,
            baseline_knobs,
            baseline_snapshot.device_values,
        )
        self._progress("Joint response analysis complete", total, total)
        return result

    def _build_result(
        self,
        baseline: MultiPlaneDispersionMeasurement,
        matrices: Mapping[str, np.ndarray],
        knob_names: tuple[str, ...],
        knob_set: SymmetricKnobSet,
        baseline_knobs: Mapping[str, float],
        baseline_device_values: Mapping[str, float],
    ) -> JointResponseAnalysisResult:
        bpm_index = {
            name: index for index, name in enumerate(self.config.measurement_bpms)
        }
        rows = self.analysis.targets
        row_indices = np.asarray([bpm_index[item.bpm] for item in rows], dtype=int)
        matrix = np.vstack(
            [
                matrices[item.plane][row_indices[index], :]
                for index, item in enumerate(rows)
            ]
        )
        values = np.asarray(
            [
                baseline.for_plane(item.plane).values_mm[row_indices[index]]
                for index, item in enumerate(rows)
            ],
            dtype=float,
        )
        valid = np.asarray(
            [
                baseline.for_plane(item.plane).valid[row_indices[index]]
                for index, item in enumerate(rows)
            ],
            dtype=bool,
        )
        targets = np.asarray([item.target_mm for item in rows], dtype=float)
        tolerances = np.asarray([item.tolerance_mm for item in rows], dtype=float)
        if not np.any(valid):
            raise RuntimeError("No valid joint response target observations")

        normalized_matrix = matrix[valid, :] / tolerances[valid, np.newaxis]
        normalized_residual = (values[valid] - targets[valid]) / tolerances[valid]
        current = knob_set.vector_from_mapping(baseline_knobs)
        initial = knob_set.vector_from_mapping(
            self._initial_knob_values or baseline_knobs
        )
        delta, singular_values, condition = solve_bounded_correction(
            normalized_matrix,
            normalized_residual,
            self.config.solver.svd_cut,
            self.config.solver.gain,
            knob_set.limits(),
            self.config.solver.max_step_fraction,
            current,
            initial,
            self.config.solver.regularization,
        )
        predicted = values + matrix @ delta
        device_deltas = knob_set.device_deltas(
            knob_set.mapping_from_vector(delta)
        )
        physical_baseline = {
            name: float(baseline_device_values[name])
            for name in device_deltas
            if name in baseline_device_values
        }
        physical_targets = {
            name: physical_baseline[name] + float(device_deltas[name])
            for name in physical_baseline
        }
        ratios = (
            singular_values / float(np.max(singular_values))
            if singular_values.size and float(np.max(singular_values)) > 0
            else np.zeros_like(singular_values)
        )
        retained_rank = int(np.count_nonzero(ratios > self.config.solver.svd_cut))
        if retained_rank:
            u_matrix = np.linalg.svd(normalized_matrix, full_matrices=False)[0]
            controllable = u_matrix[:, :retained_rank]
            uncontrollable = normalized_residual - (
                controllable @ (controllable.T @ normalized_residual)
            )
            uncontrollable_rms = float(
                np.sqrt(np.mean(uncontrollable * uncontrollable))
            )
        else:
            uncontrollable_rms = float(
                np.sqrt(np.mean(normalized_residual * normalized_residual))
            )
        predicted_residual = (
            predicted[valid] - targets[valid]
        ) / tolerances[valid]
        result = JointResponseAnalysisResult(
            matrix=matrix,
            target_names=tuple(item.name for item in rows),
            target_bpms=tuple(item.bpm for item in rows),
            target_planes=tuple(item.plane for item in rows),
            target_values_mm=targets,
            tolerances_mm=tolerances,
            baseline_values_mm=values,
            valid=valid,
            knob_names=knob_names,
            baseline=baseline,
            delta_knobs=knob_set.mapping_from_vector(delta),
            baseline_device_values=physical_baseline,
            target_device_values=physical_targets,
            predicted_values_mm=predicted,
            singular_values=singular_values,
            retained_rank=retained_rank,
            condition_number=condition_number(singular_values)
            if singular_values.size
            else condition,
            normalized_rms_before=float(
                np.sqrt(np.mean(normalized_residual * normalized_residual))
            ),
            normalized_rms_after=float(
                np.sqrt(np.mean(predicted_residual * predicted_residual))
            ),
            uncontrollable_rms=uncontrollable_rms,
        )
        self._log(
            "Joint response retained "
            f"{retained_rank}/{min(normalized_matrix.shape)} modes; "
            f"normalized RMS {result.normalized_rms_before:.6g} → "
            f"{result.normalized_rms_after:.6g}. Recommendation is preview-only."
        )
        return result

    def apply_recommendation(
        self,
        recommendation: JointResponseAnalysisResult,
        *,
        iteration: int = 1,
    ) -> JointCorrectionResult:
        if self.config.section.diagnostic_only:
            raise PermissionError(
                "Joint recommendations cannot be applied from a diagnostic section"
            )
        self.workflow._require_write_ready()
        machine = self.workflow.machine
        knob_set = SymmetricKnobSet(self.analysis.knobs)
        snapshot = machine.snapshot()
        initial = recommendation.baseline
        try:
            self._verify_reviewed_baseline(recommendation, snapshot.device_values)
            values = knob_set.add_step(
                machine.get_knobs(tuple(recommendation.knob_names)),
                tuple(recommendation.delta_knobs.values()),
            )
            machine.set_knobs(values)
            target_writer = getattr(machine, "set_device_targets", None)
            if recommendation.target_device_values and callable(target_writer):
                target_writer(recommendation.target_device_values)
            else:
                machine.apply_device_deltas(knob_set.device_deltas(values))
            machine.wait_stable()
            self._check_cancelled()
            if not machine.is_safe():
                raise RuntimeError("Machine unsafe after joint correction step")
            measured = self.workflow.measure_dispersion(
                self.config.measurement.final_samples
            )
            if not isinstance(measured, MultiPlaneDispersionMeasurement):
                raise RuntimeError("Joint verification lost one measurement plane")
            trial_snapshot = machine.snapshot()
            rms_after = self._normalized_rms(measured)
            required_improvement = float(
                self.config.solver.min_step_improvement
            )
            accepted = (
                np.isfinite(rms_after)
                and rms_after
                < recommendation.normalized_rms_before
                * (1.0 - required_improvement)
            )
            reason = (
                "Joint residual improved"
                if accepted
                else "Joint residual did not improve enough; initial state restored"
            )
            if not accepted:
                machine.restore(snapshot)
                machine.wait_stable()
                measured = initial
                rms_after = recommendation.normalized_rms_before
            step = JointCorrectionStep(
                iteration=iteration,
                response=recommendation,
                measured_after=measured,
                normalized_rms_after=rms_after,
                accepted=accepted,
                reason=reason,
                device_values_before={
                    name: float(snapshot.device_values[name])
                    for name in recommendation.target_device_values
                    if name in snapshot.device_values
                },
                device_values_trial={
                    name: float(trial_snapshot.device_values[name])
                    for name in recommendation.target_device_values
                    if name in trial_snapshot.device_values
                },
                restored=not accepted,
            )
            return JointCorrectionResult(
                success=accepted,
                reason=reason,
                initial=initial,
                final=measured,
                steps=(step,),
            )
        except Exception:
            machine.restore(snapshot)
            machine.wait_stable()
            raise

    def run_automatic(self) -> JointCorrectionResult:
        if self.config.section.diagnostic_only:
            raise PermissionError(
                "Automatic joint correction is unavailable in a diagnostic section"
            )
        initial: MultiPlaneDispersionMeasurement | None = None
        final: MultiPlaneDispersionMeasurement | None = None
        steps: list[JointCorrectionStep] = []
        for iteration in range(1, self.config.solver.max_iter + 1):
            self._check_cancelled()
            self._automatic_generation = (
                iteration,
                self.config.solver.max_iter,
            )
            recommendation = self.run()
            if initial is None:
                initial = recommendation.baseline
                self._measurement(
                    iteration,
                    self.config.solver.max_iter,
                    "initial",
                    initial,
                )
            applied = self.apply_recommendation(
                recommendation,
                iteration=iteration,
            )
            step = applied.steps[0]
            steps.append(step)
            final = applied.final
            self._measurement(
                iteration,
                self.config.solver.max_iter,
                "accepted" if step.accepted else "rejected",
                final,
            )
            if not step.accepted:
                break
            if step.normalized_rms_after <= 1.0:
                break
        self._automatic_generation = None
        self._progress(
            "Automatic joint correction complete",
            1,
            1,
        )
        if initial is None or final is None:
            raise RuntimeError("Automatic joint correction produced no measurement")
        success = any(step.accepted for step in steps)
        return JointCorrectionResult(
            success=success,
            reason=(
                "Automatic joint correction completed"
                if steps[-1].accepted
                else (
                    "Automatic joint correction stopped; the last rejected "
                    "generation was restored"
                )
            ),
            initial=initial,
            final=final,
            steps=tuple(steps),
        )

    def _normalized_rms(
        self,
        measurement: MultiPlaneDispersionMeasurement,
    ) -> float:
        bpm_index = {
            name: index for index, name in enumerate(self.config.measurement_bpms)
        }
        residuals = []
        for target in self.analysis.targets:
            item = measurement.for_plane(target.plane)
            index = bpm_index[target.bpm]
            if item.valid[index]:
                residuals.append(
                    (item.values_mm[index] - target.target_mm)
                    / target.tolerance_mm
                )
        if not residuals:
            return float("nan")
        values = np.asarray(residuals, dtype=float)
        return float(np.sqrt(np.mean(values * values)))

    def _verify_reviewed_baseline(
        self,
        recommendation: JointResponseAnalysisResult,
        current: Mapping[str, float],
    ) -> None:
        for name, reviewed in recommendation.baseline_device_values.items():
            if name not in current:
                raise RuntimeError(f"Reviewed quadrupole {name} is unavailable")
            tolerance_getter = getattr(
                self.workflow.machine,
                "quadrupole_readback_tolerance",
                None,
            )
            tolerance = (
                float(tolerance_getter(name))
                if callable(tolerance_getter)
                else 1.0e-9
            )
            if abs(float(current[name]) - reviewed) > tolerance:
                raise RuntimeError(
                    f"Quadrupole {name} changed after joint response measurement; "
                    "remeasure the response before applying"
                )

    def _apply_knobs(
        self,
        knob_set: SymmetricKnobSet,
        values: Mapping[str, float],
    ) -> None:
        self._check_cancelled()
        self.workflow.machine.set_knobs(values)
        self.workflow.machine.apply_device_deltas(knob_set.device_deltas(values))

    def _check_cancelled(self) -> None:
        if self.cancellation_callback is not None and self.cancellation_callback():
            raise WorkflowCancelled("Operation aborted")

    def _progress(self, stage: str, current: int, total: int) -> None:
        if self.progress_callback is not None:
            if self._automatic_generation is not None and total > 0:
                generation, generations = self._automatic_generation
                stage = f"Generation {generation}/{generations} · {stage}"
                current = (generation - 1) * total + current
                total *= generations
            self.progress_callback(stage, current, total)

    def _log(self, message: str) -> None:
        if self.log_callback is not None:
            self.log_callback(message)

    def _measurement(
        self,
        iteration: int,
        total: int,
        state: str,
        measurement: MultiPlaneDispersionMeasurement,
    ) -> None:
        if self.measurement_callback is not None:
            self.measurement_callback(iteration, total, state, measurement)
