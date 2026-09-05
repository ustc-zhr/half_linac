from __future__ import annotations

import argparse
import signal
import sys
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

import numpy as np

_REPO_BOOTSTRAP_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file()
)
if str(_REPO_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_BOOTSTRAP_ROOT))

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

from half_linac.src.shared.machine_profile import (
    AppContext,
    LimitRange,
    MachineProfileError,
    SolenoidCenteringPreset,
    SolenoidCenteringScanRange,
    effective_limit,
    load_app_context,
    require_workflow_write_allowed,
    resolve_channel,
    resolve_write_target,
    WriteTarget,
)
from half_linac.src.apps.solenoid_centering.profile_runtime import write_scan_result


NOISE_FLOOR = 1e-15
SCORING_MODE_SLOPE = "slope"
SCORING_MODE_TRAJECTORY_LENGTH = "trajectory_length"
SCORING_MODES = (SCORING_MODE_SLOPE, SCORING_MODE_TRAJECTORY_LENGTH)


class StopRequested(RuntimeError):
    """Raised when a running scan is asked to stop."""


class MotionVerificationError(RuntimeError):
    """Raised when a device does not reach the requested setpoint."""


class StateDriftError(RuntimeError):
    """Raised when the machine no longer matches a scanned baseline."""


class RestoreFailed(RuntimeError):
    """Raised when one or more devices cannot be restored safely."""

    def __init__(
        self,
        message: str,
        *,
        outcome: RestoreOutcome | None = None,
        operation_error: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.outcome = outcome
        self.operation_error = operation_error


class ScalarIO(Protocol):
    def read(self, pv_name: str) -> float:
        ...

    def write(self, pv_name: str, value: float) -> None:
        ...


@dataclass(frozen=True)
class ResponseScore:
    score: float
    slope_x: float
    slope_y: float
    offset_x: float
    offset_y: float
    scale_x: float
    scale_y: float
    rms_x: float
    rms_y: float
    residual_rms_x: float
    residual_rms_y: float
    mean_x: float
    mean_y: float
    std_x: float
    std_y: float
    mode: str = SCORING_MODE_SLOPE
    slope_score: float = 0.0
    trajectory_length: float = 0.0


@dataclass(frozen=True)
class CandidateResult:
    axis: str
    round_index: int
    hcorr: float
    vcorr: float
    corrector_value: float
    solenoid_values: tuple[float, ...]
    bpm_x_means: tuple[float, ...]
    bpm_y_means: tuple[float, ...]
    score: ResponseScore


@dataclass(frozen=True)
class AxisScanResult:
    axis: str
    round_index: int
    candidates: tuple[CandidateResult, ...]
    best: CandidateResult


@dataclass(frozen=True)
class ScanTermination:
    code: str
    reason: str
    iterations_completed: int
    early: bool
    blocks_recommendation: bool = False


@dataclass(frozen=True)
class RestoreOutcome:
    status: str
    errors: tuple[str, ...] = ()

    @property
    def is_verified(self) -> bool:
        return self.status == "verified"


@dataclass(frozen=True)
class CenteringResult:
    preset_id: str
    original_solenoid: float
    original_hcorr: float
    original_vcorr: float
    recommended_hcorr: float
    recommended_vcorr: float
    best_score: float
    axis_scans: tuple[AxisScanResult, ...]
    scoring_mode: str = SCORING_MODE_SLOPE
    baseline_candidate: CandidateResult | None = None
    relative_improvement: float = 0.0
    recommendation_available: bool = False
    recommendation_status: str = "not_evaluated"
    preflight: dict[str, Any] | None = None
    selected_devices: dict[str, str] | None = None
    scan_config: dict[str, Any] | None = None
    termination: ScanTermination | None = None
    restore: RestoreOutcome | None = None
    operation_status: str = "completed"
    schema_version: int = 5

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RangeCheck:
    label: str
    element_id: str
    pv_name: str
    planned_low: float
    planned_high: float
    limit_low: float | None
    limit_high: float | None
    first_scan_low: float | None = None
    first_scan_high: float | None = None
    requested_points: int | None = None
    feasible_points: int | None = None
    clipping_allowed: bool = False

    @property
    def has_limit(self) -> bool:
        return self.limit_low is not None and self.limit_high is not None

    @property
    def is_ok(self) -> bool:
        if not self.has_limit:
            return False
        assert self.limit_low is not None
        assert self.limit_high is not None
        in_limit = self.planned_low >= self.limit_low and self.planned_high <= self.limit_high
        enough_points = self.feasible_points is None or self.feasible_points >= 2
        return in_limit and enough_points


@dataclass(frozen=True)
class ReadbackCheck:
    label: str
    element_id: str
    setpoint_pv: str
    readback_pv: str | None
    setpoint: float
    readback: float | None
    tolerance: float | None

    @property
    def is_ok(self) -> bool:
        return (
            self.readback_pv is not None
            and self.readback is not None
            and self.tolerance is not None
            and abs(self.setpoint - self.readback) <= self.tolerance
        )

    @property
    def detail(self) -> str:
        if self.readback_pv is None:
            return "readback PV unavailable"
        if self.readback is None or self.tolerance is None:
            return "readback verification is not configured"
        return (
            f"set={self.setpoint:g}, readback={self.readback:g}, "
            f"|delta|={abs(self.setpoint - self.readback):g}, tolerance={self.tolerance:g}"
        )


@dataclass(frozen=True)
class PreflightReport:
    machine_id: str
    backend: str
    preset_id: str
    solenoid_pv: str
    solenoid_readback_pv: str | None
    hcorr_pv: str
    vcorr_pv: str
    bpm_x_pv: str
    bpm_y_pv: str
    original_solenoid: float
    solenoid_readback: float | None
    original_hcorr: float
    original_vcorr: float
    hcorr_readback: float | None
    vcorr_readback: float | None
    bpm_x: float
    bpm_y: float
    solenoid_points: int
    corrector_candidates: int
    bpm_samples: int
    estimated_duration_s: float
    range_checks: tuple[RangeCheck, ...]
    readback_checks: tuple[ReadbackCheck, ...]
    readback_verification_configured: bool

    @property
    def is_ready(self) -> bool:
        return (
            self.readback_verification_configured
            and all(item.is_ok for item in self.range_checks)
            and all(item.is_ok for item in self.readback_checks)
        )

    def as_text(self) -> str:
        lines = [
            "READY" if self.is_ready else "NOT READY",
            f"machine={self.machine_id} backend={self.backend} preset={self.preset_id}",
            f"solenoid setpoint {self.solenoid_pv} = {self.original_solenoid:g}",
        ]
        if self.solenoid_readback_pv:
            lines.append(
                f"solenoid readback {self.solenoid_readback_pv} = {self.solenoid_readback:g}"
            )
        lines.extend(
            [
                f"HCOR {self.hcorr_pv} = {self.original_hcorr:g}",
                f"VCOR {self.vcorr_pv} = {self.original_vcorr:g}",
                f"BPM X {self.bpm_x_pv} = {self.bpm_x:g}",
                f"BPM Y {self.bpm_y_pv} = {self.bpm_y:g}",
                f"corrector candidates up to {self.corrector_candidates}, "
                f"solenoid points={self.solenoid_points}, "
                f"bpm samples/point={self.bpm_samples}",
                f"estimated duration ~= {self.estimated_duration_s:.1f} s",
            ]
        )
        for item in self.range_checks:
            if item.has_limit:
                limit = f"[{item.limit_low:g}, {item.limit_high:g}]"
            else:
                limit = "unconfigured"
            clipped = (
                item.clipping_allowed
                and item.requested_points is not None
                and item.feasible_points is not None
                and item.feasible_points < item.requested_points
            )
            if item.is_ok and clipped:
                status = "CLIPPED TO LIMIT"
            elif item.is_ok:
                status = "OK"
            elif not item.has_limit:
                status = "LIMIT UNCONFIGURED"
            elif item.feasible_points is not None and item.feasible_points < 2:
                status = "INSUFFICIENT CANDIDATES"
            else:
                status = "OUT OF LIMIT"
            if item.first_scan_low is not None and item.first_scan_high is not None:
                planned = f"requested [{item.first_scan_low:g}, {item.first_scan_high:g}], " \
                    f"effective [{item.planned_low:g}, {item.planned_high:g}]"
                if item.requested_points is not None and item.feasible_points is not None:
                    planned += f", candidates {item.feasible_points}/{item.requested_points}"
            else:
                planned = f"planned [{item.planned_low:g}, {item.planned_high:g}]"
            lines.append(f"{status} {item.label} {item.element_id}: {planned}, limit {limit}")
        for item in self.readback_checks:
            status = "OK" if item.is_ok else "NOT VERIFIED"
            lines.append(f"{status} {item.label} {item.element_id}: {item.detail}")
        return "\n".join(lines)


def relative_scan_points(center: float, scan_range: SolenoidCenteringScanRange) -> np.ndarray:
    if scan_range.steps <= 0:
        raise ValueError("scan_range.steps must be positive.")
    return center + np.linspace(
        float(scan_range.relative_from),
        float(scan_range.relative_to),
        int(scan_range.steps),
    )


def _bounded_candidate_values(
    center: float,
    scan_range: SolenoidCenteringScanRange,
    limits: tuple[float, float] | None,
) -> tuple[float, ...]:
    values, _clipped_low, _clipped_high = _bounded_candidate_info(
        center,
        scan_range,
        limits,
    )
    return values


def _bounded_candidate_info(
    center: float,
    scan_range: SolenoidCenteringScanRange,
    limits: tuple[float, float] | None,
) -> tuple[tuple[float, ...], bool, bool]:
    requested = tuple(float(value) for value in relative_scan_points(center, scan_range))
    if limits is None:
        return requested, False, False
    machine_limit = LimitRange(low=limits[0], high=limits[1])
    relative_limit = LimitRange(
        low=min(scan_range.relative_from, scan_range.relative_to),
        high=max(scan_range.relative_from, scan_range.relative_to),
    )
    application_absolute = relative_limit.relative_to_absolute(center)
    try:
        selected_limit = effective_limit(application_absolute, machine_limit)
    except MachineProfileError:
        return (), True, True
    assert selected_limit.low is not None
    assert selected_limit.high is not None
    low, high = selected_limit.low, selected_limit.high
    tolerance = max(1.0, abs(low), abs(high)) * 1e-12
    feasible = tuple(value for value in requested if low - tolerance <= value <= high + tolerance)
    clipped_low = any(value < low - tolerance for value in requested)
    clipped_high = any(value > high + tolerance for value in requested)
    return feasible, clipped_low, clipped_high


def _at_limit(value: float, limits: tuple[float, float] | None) -> bool:
    if limits is None:
        return False
    low, high = limits
    tolerance = max(1.0, abs(low), abs(high)) * 1e-12
    return abs(value - low) <= tolerance or abs(value - high) <= tolerance


def _at_clipped_edge(
    value: float,
    candidates: tuple[float, ...],
    *,
    clipped_low: bool,
    clipped_high: bool,
) -> bool:
    if not candidates:
        return False
    tolerance = max(1.0, *(abs(candidate) for candidate in candidates)) * 1e-12
    return (
        clipped_low and abs(value - min(candidates)) <= tolerance
    ) or (
        clipped_high and abs(value - max(candidates)) <= tolerance
    )


def evaluate_solenoid_response(
    solenoid_values: Iterable[float],
    x_samples,
    y_samples,
    *,
    scoring_mode: str = SCORING_MODE_SLOPE,
    noise_floor: float = NOISE_FLOOR,
    reference_solenoid: float | None = None,
) -> ResponseScore:
    scoring_mode = normalize_scoring_mode(scoring_mode)
    sol = np.asarray(list(solenoid_values), dtype=float)
    x = _as_2d_samples(x_samples, "x_samples")
    y = _as_2d_samples(y_samples, "y_samples")
    if len(sol) != x.shape[0] or len(sol) != y.shape[0]:
        raise ValueError("solenoid_values, x_samples, and y_samples must have the same point count.")
    if len(sol) < 2:
        raise ValueError("At least two solenoid points are required for slope scoring.")

    x_means = x.mean(axis=1)
    y_means = y.mean(axis=1)
    x_stds = x.std(axis=1, ddof=1) if x.shape[1] > 1 else np.zeros(x.shape[0])
    y_stds = y.std(axis=1, ddof=1) if y.shape[1] > 1 else np.zeros(y.shape[0])
    scale_x = _noise_scale(x_stds, noise_floor)
    scale_y = _noise_scale(y_stds, noise_floor)

    if reference_solenoid is None:
        reference_solenoid = float((sol[0] + sol[-1]) / 2.0)
    if not np.isfinite(reference_solenoid):
        raise ValueError("reference_solenoid must be finite.")

    local_sol = sol - float(reference_solenoid)
    fit_order = 2 if len(sol) >= 3 else 1
    x_coefficients = np.polyfit(local_sol, x_means, fit_order)
    y_coefficients = np.polyfit(local_sol, y_means, fit_order)
    x_fit = np.polyval(x_coefficients, local_sol)
    y_fit = np.polyval(y_coefficients, local_sol)
    residual_x = x_means - x_fit
    residual_y = y_means - y_fit
    if fit_order == 2:
        slope_x, offset_x = x_coefficients[1], x_coefficients[2]
        slope_y, offset_y = y_coefficients[1], y_coefficients[2]
    else:
        slope_x, offset_x = x_coefficients
        slope_y, offset_y = y_coefficients
    centered_x = x_means - x_means.mean()
    centered_y = y_means - y_means.mean()
    slope_score = float(np.hypot(slope_x / scale_x, slope_y / scale_y))
    trajectory_length = _trajectory_length(x_means, y_means)
    score = (
        slope_score
        if scoring_mode == SCORING_MODE_SLOPE
        else trajectory_length
    )

    return ResponseScore(
        score=score,
        slope_x=float(slope_x),
        slope_y=float(slope_y),
        offset_x=float(offset_x),
        offset_y=float(offset_y),
        scale_x=float(scale_x),
        scale_y=float(scale_y),
        rms_x=_rms(centered_x),
        rms_y=_rms(centered_y),
        residual_rms_x=_rms(residual_x),
        residual_rms_y=_rms(residual_y),
        mean_x=float(x_means.mean()),
        mean_y=float(y_means.mean()),
        std_x=float(np.mean(x_stds)),
        std_y=float(np.mean(y_stds)),
        mode=scoring_mode,
        slope_score=slope_score,
        trajectory_length=trajectory_length,
    )


def coordinate_descent(
    initial_hcorr: float,
    initial_vcorr: float,
    corrector_scan: SolenoidCenteringScanRange,
    max_iters: int,
    evaluator: Callable[[str, int, float, float], CandidateResult],
    *,
    hcorr_limits: tuple[float, float] | None = None,
    vcorr_limits: tuple[float, float] | None = None,
) -> tuple[float, float, tuple[AxisScanResult, ...], ScanTermination]:
    if max_iters <= 0:
        raise ValueError("max_iters must be positive.")
    hcorr = float(initial_hcorr)
    vcorr = float(initial_vcorr)
    axis_scans: list[AxisScanResult] = []

    for round_index in range(max_iters):
        iteration_start_h = hcorr
        iteration_start_v = vcorr
        h_candidates = []
        h_values, h_clipped_low, h_clipped_high = _bounded_candidate_info(
            hcorr,
            corrector_scan,
            hcorr_limits,
        )
        if len(h_values) < 2:
            termination = ScanTermination(
                code="insufficient_hcorr_candidates",
                reason=(
                    f"Stopped before iteration {round_index + 1}: HCOR has only "
                    f"{len(h_values)} in-limit candidate(s); at least 2 are required."
                ),
                iterations_completed=round_index,
                early=True,
                blocks_recommendation=True,
            )
            return hcorr, vcorr, tuple(axis_scans), termination
        for h_value in h_values:
            h_candidates.append(evaluator("h", round_index, float(h_value), vcorr))
        h_best = min(h_candidates, key=lambda item: item.score.score)
        hcorr = h_best.hcorr
        axis_scans.append(
            AxisScanResult(
                axis="h",
                round_index=round_index,
                candidates=tuple(h_candidates),
                best=h_best,
            )
        )

        v_candidates = []
        v_values, v_clipped_low, v_clipped_high = _bounded_candidate_info(
            vcorr,
            corrector_scan,
            vcorr_limits,
        )
        if len(v_values) < 2:
            termination = ScanTermination(
                code="insufficient_vcorr_candidates",
                reason=(
                    f"Stopped during iteration {round_index + 1}: VCOR has only "
                    f"{len(v_values)} in-limit candidate(s); at least 2 are required."
                ),
                iterations_completed=round_index,
                early=True,
                blocks_recommendation=True,
            )
            return hcorr, vcorr, tuple(axis_scans), termination
        for v_value in v_values:
            v_candidates.append(evaluator("v", round_index, hcorr, float(v_value)))
        v_best = min(v_candidates, key=lambda item: item.score.score)
        vcorr = v_best.vcorr
        axis_scans.append(
            AxisScanResult(
                axis="v",
                round_index=round_index,
                candidates=tuple(v_candidates),
                best=v_best,
            )
        )

        h_boundary_limited = _at_limit(hcorr, hcorr_limits) or _at_clipped_edge(
            hcorr,
            h_values,
            clipped_low=h_clipped_low,
            clipped_high=h_clipped_high,
        )
        v_boundary_limited = _at_limit(vcorr, vcorr_limits) or _at_clipped_edge(
            vcorr,
            v_values,
            clipped_low=v_clipped_low,
            clipped_high=v_clipped_high,
        )
        if h_boundary_limited or v_boundary_limited:
            axes = []
            if h_boundary_limited:
                axes.append("HCOR")
            if v_boundary_limited:
                axes.append("VCOR")
            termination = ScanTermination(
                code="boundary_limited",
                reason=(
                    f"Stopped after iteration {round_index + 1}: best "
                    f"{' and '.join(axes)} optimum reached the usable boundary "
                    "defined by configured physical limits."
                ),
                iterations_completed=round_index + 1,
                early=True,
                blocks_recommendation=True,
            )
            return hcorr, vcorr, tuple(axis_scans), termination

        if np.isclose(hcorr, iteration_start_h) and np.isclose(vcorr, iteration_start_v):
            termination = ScanTermination(
                code="converged_no_coordinate_change",
                reason=(
                    f"Converged after iteration {round_index + 1}: neither HCOR nor VCOR "
                    "selected a different candidate."
                ),
                iterations_completed=round_index + 1,
                early=round_index + 1 < max_iters,
            )
            return hcorr, vcorr, tuple(axis_scans), termination

    termination = ScanTermination(
        code="max_iters_reached",
        reason=f"Completed configured maximum of {max_iters} iteration(s).",
        iterations_completed=max_iters,
        early=False,
    )
    return hcorr, vcorr, tuple(axis_scans), termination


class EpicsScalarIO:
    def __init__(self, timeout_s: float = 2.0):
        from epics import caget, caput

        self._caget = caget
        self._caput = caput
        self.timeout_s = timeout_s

    def read(self, pv_name: str) -> float:
        value = self._caget(pv_name)
        if value is None:
            raise ValueError(f"Failed to read PV: {pv_name}")
        value = float(value)
        if not np.isfinite(value):
            raise ValueError(f"PV {pv_name} returned non-finite value: {value!r}")
        return value

    def write(self, pv_name: str, value: float) -> None:
        status = self._caput(pv_name, float(value), wait=True, timeout=self.timeout_s)
        if not status:
            raise ValueError(f"Failed to write PV {pv_name} to {value:g}.")


class SolenoidCenteringScanner:
    def __init__(
        self,
        app_context: AppContext,
        preset: SolenoidCenteringPreset,
        *,
        io: ScalarIO | None = None,
        progress: Callable[[str, int, int], None] | None = None,
        candidate_finished: Callable[[CandidateResult], None] | None = None,
        scoring_mode: str = SCORING_MODE_SLOPE,
        stop_requested: Callable[[], bool] | None = None,
    ):
        self.app_context = app_context
        self.preset = preset
        self.io = io or EpicsScalarIO()
        self.progress = progress or (lambda _message, _completed, _total: None)
        self.candidate_finished = candidate_finished or (lambda _candidate: None)
        self.scoring_mode = normalize_scoring_mode(scoring_mode)
        self.stop_requested = stop_requested or (lambda: False)
        self.solenoid_target = self._resolve_solenoid_write_target()
        self.solenoid_pv = (
            self.solenoid_target.pv_name
            if self.solenoid_target is not None
            else self._resolve_legacy_solenoid_setpoint_pv()
        )
        self.solenoid_readback_pv = self._resolve_solenoid_readback_pv()
        self.hcorr_target = resolve_write_target(app_context, preset.hcorr)
        self.vcorr_target = resolve_write_target(app_context, preset.vcorr)
        self.hcorr_pv = self.hcorr_target.pv_name
        self.vcorr_pv = self.vcorr_target.pv_name
        self.hcorr_readback_pv = self._resolve_readback_pv(preset.hcorr)
        self.vcorr_readback_pv = self._resolve_readback_pv(preset.vcorr)
        self.bpm_x_pv = resolve_channel(app_context, preset.bpm, "x")
        self.bpm_y_pv = resolve_channel(app_context, preset.bpm, "y")

    def _resolve_solenoid_write_target(self) -> WriteTarget | None:
        if self.preset.solenoid:
            return resolve_write_target(self.app_context, self.preset.solenoid)
        return None

    def _resolve_legacy_solenoid_setpoint_pv(self) -> str:
        if self.preset.solenoid_setpoint_pv:
            return self.preset.solenoid_setpoint_pv
        raise MachineProfileError(
            f"Solenoid centering preset {self.preset.id!r} does not define a solenoid element "
            "or legacy solenoid_setpoint_pv."
        )

    def _resolve_solenoid_readback_pv(self) -> str | None:
        if self.preset.solenoid:
            try:
                return resolve_channel(self.app_context, self.preset.solenoid, "current_readback")
            except MachineProfileError:
                return self.preset.solenoid_readback_pv
        return self.preset.solenoid_readback_pv

    def _resolve_readback_pv(self, element_id: str) -> str | None:
        try:
            return resolve_channel(self.app_context, element_id, "current_readback")
        except MachineProfileError:
            return None

    def run(self) -> CenteringResult:
        require_workflow_write_allowed(
            self.app_context,
            "solenoid_centering",
            "Solenoid centering scan",
        )
        report = self.preflight()
        if not report.is_ready:
            raise MachineProfileError(report.as_text())
        original_solenoid = report.original_solenoid
        original_hcorr = report.original_hcorr
        original_vcorr = report.original_vcorr
        total = self._estimated_candidate_count() + 1
        completed = 0
        observed_candidates: list[CandidateResult] = []

        def evaluator(axis: str, round_index: int, hcorr: float, vcorr: float) -> CandidateResult:
            nonlocal completed
            self._raise_if_stopped()
            message = f"iteration {round_index + 1} {axis.upper()} candidate"
            self.progress(message, completed, total)
            result = self._evaluate_candidate(axis, round_index, hcorr, vcorr, original_solenoid)
            completed += 1
            observed_candidates.append(result)
            self.candidate_finished(result)
            self.progress(message, completed, total)
            return result

        operation_error: Exception | None = None
        result: CenteringResult | None = None
        try:
            self.progress("baseline candidate", completed, total)
            baseline = self._evaluate_candidate(
                "baseline",
                0,
                original_hcorr,
                original_vcorr,
                original_solenoid,
            )
            completed += 1
            observed_candidates.append(baseline)
            self.candidate_finished(baseline)
            self.progress("baseline candidate", completed, total)
            hcorr_limits = _numeric_limit(self.hcorr_target.machine_limit)
            vcorr_limits = _numeric_limit(self.vcorr_target.machine_limit)
            recommended_h, recommended_v, axis_scans, termination = coordinate_descent(
                original_hcorr,
                original_vcorr,
                self.preset.corrector_scan,
                self.preset.max_rounds,
                evaluator,
                hcorr_limits=hcorr_limits,
                vcorr_limits=vcorr_limits,
            )
            best = min((scan.best for scan in axis_scans), key=lambda candidate: candidate.score.score)
            best_score = best.score.score
            relative_improvement, recommendation_available, recommendation_status = (
                self._recommendation_quality(baseline, best)
            )
            if termination.blocks_recommendation:
                recommendation_available = False
                recommendation_status = termination.reason
            result = CenteringResult(
                preset_id=self.preset.id,
                original_solenoid=original_solenoid,
                original_hcorr=original_hcorr,
                original_vcorr=original_vcorr,
                recommended_hcorr=recommended_h,
                recommended_vcorr=recommended_v,
                best_score=float(best_score),
                axis_scans=axis_scans,
                scoring_mode=self.scoring_mode,
                baseline_candidate=baseline,
                relative_improvement=relative_improvement,
                recommendation_available=recommendation_available,
                recommendation_status=recommendation_status,
                preflight=asdict(report),
                selected_devices=self._selected_devices_payload(),
                scan_config=self._scan_config_payload(),
                termination=termination,
            )
        except Exception as exc:
            operation_error = exc

        restore = self._restore_outcome(original_solenoid, original_hcorr, original_vcorr)
        if result is not None:
            result = replace(
                result,
                restore=restore,
                operation_status="completed" if restore.is_verified else "restore_failed",
            )
            archive_payload = result.as_dict()
        else:
            assert operation_error is not None
            archive_payload = self._failed_attempt_payload(
                report,
                operation_error,
                restore,
                observed_candidates,
            )
        archive_error: Exception | None = None
        try:
            write_scan_result(self.app_context, archive_payload)
        except Exception as exc:
            archive_error = exc

        if not restore.is_verified:
            detail = "; ".join(restore.errors)
            if archive_error is not None:
                detail += f"; result archive failed: {archive_error}"
            if operation_error is not None:
                raise RestoreFailed(
                    f"Scan failed ({operation_error}); restore failed: {detail}",
                    outcome=restore,
                    operation_error=operation_error,
                ) from operation_error
            raise RestoreFailed(
                f"Device restore failed: {detail}",
                outcome=restore,
            )
        if archive_error is not None:
            if operation_error is not None:
                raise RuntimeError(
                    f"{operation_error}; result archive failed: {archive_error}"
                ) from operation_error
            raise archive_error
        if operation_error is not None:
            raise operation_error.with_traceback(operation_error.__traceback__)
        assert result is not None
        return result

    def _selected_devices_payload(self) -> dict[str, str]:
        return {
            "solenoid": self.preset.solenoid or self.solenoid_pv,
            "hcorr": self.preset.hcorr,
            "vcorr": self.preset.vcorr,
            "bpm": self.preset.bpm,
            "solenoid_setpoint_pv": self.solenoid_pv,
            "solenoid_readback_pv": self.solenoid_readback_pv or "",
            "hcorr_setpoint_pv": self.hcorr_pv,
            "hcorr_readback_pv": self.hcorr_readback_pv or "",
            "vcorr_setpoint_pv": self.vcorr_pv,
            "vcorr_readback_pv": self.vcorr_readback_pv or "",
            "bpm_x_pv": self.bpm_x_pv,
            "bpm_y_pv": self.bpm_y_pv,
        }

    def _failed_attempt_payload(
        self,
        report: PreflightReport,
        error: Exception,
        restore: RestoreOutcome,
        candidates: list[CandidateResult],
    ) -> dict[str, Any]:
        if isinstance(error, StopRequested):
            code = "operator_stopped"
            reason = "Operator requested stop."
        elif isinstance(error, MotionVerificationError):
            code = "readback_verification_failed"
            reason = f"Readback verification failed: {error}"
        else:
            code = "scan_failed"
            reason = f"{type(error).__name__}: {error}"
        if restore.is_verified:
            status = "stopped" if isinstance(error, StopRequested) else "failed"
            reason += " Original device state was restored and verified."
        else:
            status = "restore_failed"
            reason += " Original device state could not be fully restored."
        iteration_indexes = [
            candidate.round_index for candidate in candidates if candidate.axis != "baseline"
        ]
        termination = ScanTermination(
            code=code,
            reason=reason,
            iterations_completed=max(iteration_indexes, default=-1) + 1,
            early=True,
            blocks_recommendation=True,
        )
        return {
            "schema_version": 5,
            "record_type": "scan_attempt",
            "operation_status": status,
            "preset_id": self.preset.id,
            "scoring_mode": self.scoring_mode,
            "termination": asdict(termination),
            "restore": asdict(restore),
            "error": {"type": type(error).__name__, "message": str(error)},
            "preflight": asdict(report),
            "selected_devices": self._selected_devices_payload(),
            "scan_config": self._scan_config_payload(),
            "candidates": [asdict(candidate) for candidate in candidates],
        }

    def apply_recommended(self, result: CenteringResult) -> None:
        if not result.recommendation_available:
            raise MachineProfileError(
                f"Cannot apply recommendation: {result.recommendation_status}."
            )
        self._apply_corrector_transaction(
            target_hcorr=result.recommended_hcorr,
            target_vcorr=result.recommended_vcorr,
            expected_solenoid=result.original_solenoid,
            expected_hcorr=result.original_hcorr,
            expected_vcorr=result.original_vcorr,
            action="Apply solenoid centering recommendation",
        )

    def restore_original(self, result: CenteringResult) -> None:
        self._apply_corrector_transaction(
            target_hcorr=result.original_hcorr,
            target_vcorr=result.original_vcorr,
            expected_solenoid=result.original_solenoid,
            expected_hcorr=result.recommended_hcorr,
            expected_vcorr=result.recommended_vcorr,
            action="Restore original solenoid centering settings",
        )

    def _apply_corrector_transaction(
        self,
        *,
        target_hcorr: float,
        target_vcorr: float,
        expected_solenoid: float,
        expected_hcorr: float,
        expected_vcorr: float,
        action: str,
    ) -> None:
        require_workflow_write_allowed(
            self.app_context,
            "solenoid_centering",
            action,
        )
        self._assert_expected_state(expected_solenoid, expected_hcorr, expected_vcorr)
        self._check_single_value_limit(self.hcorr_target, target_hcorr)
        self._check_single_value_limit(self.vcorr_target, target_vcorr)
        try:
            self._write_and_verify(
                "HCOR",
                self.hcorr_pv,
                self.hcorr_readback_pv,
                target_hcorr,
                self._corrector_tolerance(),
                stop_sensitive=False,
            )
            self._write_and_verify(
                "VCOR",
                self.vcorr_pv,
                self.vcorr_readback_pv,
                target_vcorr,
                self._corrector_tolerance(),
                stop_sensitive=False,
            )
        except Exception as exc:
            rollback_errors = self._restore_correctors(expected_hcorr, expected_vcorr)
            detail = "rollback succeeded" if not rollback_errors else (
                "rollback failed: " + "; ".join(rollback_errors)
            )
            raise MotionVerificationError(f"{action} failed: {exc}; {detail}.") from exc

    def preflight(self) -> PreflightReport:
        original_solenoid = self.io.read(self.solenoid_pv)
        original_hcorr = self.io.read(self.hcorr_pv)
        original_vcorr = self.io.read(self.vcorr_pv)
        solenoid_readback = self._read_optional(self.solenoid_readback_pv)
        hcorr_readback = self._read_optional(self.hcorr_readback_pv)
        vcorr_readback = self._read_optional(self.vcorr_readback_pv)
        bpm_x, bpm_y = self._read_bpm()

        range_checks: list[RangeCheck] = []
        if self.solenoid_target is not None:
            self._require_current_within_limit(self.solenoid_target, original_solenoid)
        self._require_current_within_limit(self.hcorr_target, original_hcorr)
        self._require_current_within_limit(self.vcorr_target, original_vcorr)
        range_checks.append(
            self._build_range_check(
                "solenoid",
                (
                    self.solenoid_target.element_id
                    if self.solenoid_target is not None
                    else self.preset.solenoid_setpoint_pv or "legacy-solenoid"
                ),
                relative_scan_points(original_solenoid, self.preset.solenoid_scan),
                self.solenoid_pv,
                (
                    self.solenoid_target.machine_limit
                    if self.solenoid_target is not None
                    else LimitRange()
                ),
            )
        )

        range_checks.append(
            self._build_corrector_range_check(
                "HCOR",
                self.hcorr_target,
                original_hcorr,
            )
        )
        range_checks.append(
            self._build_corrector_range_check(
                "VCOR",
                self.vcorr_target,
                original_vcorr,
            )
        )
        motion = self.preset.motion_verification
        solenoid_tolerance = motion.solenoid_readback_tolerance if motion else None
        corrector_tolerance = motion.corrector_readback_tolerance if motion else None
        readback_checks = (
            ReadbackCheck(
                "solenoid",
                (
                    self.solenoid_target.element_id
                    if self.solenoid_target is not None
                    else self.preset.solenoid_setpoint_pv or "legacy-solenoid"
                ),
                self.solenoid_pv,
                self.solenoid_readback_pv,
                original_solenoid,
                solenoid_readback,
                solenoid_tolerance,
            ),
            ReadbackCheck(
                "HCOR",
                self.preset.hcorr,
                self.hcorr_pv,
                self.hcorr_readback_pv,
                original_hcorr,
                hcorr_readback,
                corrector_tolerance,
            ),
            ReadbackCheck(
                "VCOR",
                self.preset.vcorr,
                self.vcorr_pv,
                self.vcorr_readback_pv,
                original_vcorr,
                vcorr_readback,
                corrector_tolerance,
            ),
        )

        return PreflightReport(
            machine_id=self.app_context.machine.id,
            backend=self.app_context.control_backend.name,
            preset_id=self.preset.id,
            solenoid_pv=self.solenoid_pv,
            solenoid_readback_pv=self.solenoid_readback_pv,
            hcorr_pv=self.hcorr_pv,
            vcorr_pv=self.vcorr_pv,
            bpm_x_pv=self.bpm_x_pv,
            bpm_y_pv=self.bpm_y_pv,
            original_solenoid=original_solenoid,
            solenoid_readback=solenoid_readback,
            original_hcorr=original_hcorr,
            original_vcorr=original_vcorr,
            hcorr_readback=hcorr_readback,
            vcorr_readback=vcorr_readback,
            bpm_x=bpm_x,
            bpm_y=bpm_y,
            solenoid_points=self.preset.solenoid_scan.steps,
            corrector_candidates=self._estimated_candidate_count() + 1,
            bpm_samples=self.preset.samples_per_point,
            estimated_duration_s=self._estimated_duration_s(),
            range_checks=tuple(range_checks),
            readback_checks=readback_checks,
            readback_verification_configured=motion is not None,
        )

    def _check_single_value_limit(self, target: WriteTarget, value: float) -> None:
        check = self._build_range_check(
            "corrector",
            target.element_id,
            (float(value),),
            target.pv_name,
            target.machine_limit,
        )
        if not check.is_ok:
            limit = (
                f"[{check.limit_low:g}, {check.limit_high:g}]"
                if check.has_limit
                else "unconfigured"
            )
            raise MachineProfileError(
                f"Planned value for {check.element_id} ({check.pv_name}) is outside configured "
                f"limits {limit}: planned "
                f"[{check.planned_low:g}, {check.planned_high:g}]."
            )

    @staticmethod
    def _require_current_within_limit(target: WriteTarget, value: float) -> None:
        limit = target.machine_limit
        if limit.low is None and limit.high is None:
            return
        if not limit.contains(value):
            raise MachineProfileError(
                f"Current value for {target.element_id}.{target.logical_channel} is outside "
                "its physical limit: "
                f"{value:g}, expected {limit.describe()}."
            )

    def _build_range_check(
        self,
        label: str,
        element_id: str,
        values: Iterable[float],
        pv_name: str,
        machine_limit: LimitRange,
        *,
        first_values: Iterable[float] | None = None,
        requested_points: int | None = None,
        feasible_points: int | None = None,
        clipping_allowed: bool = False,
    ) -> RangeCheck:
        values_tuple = tuple(float(value) for value in values)
        if not values_tuple:
            raise MachineProfileError(f"No planned values for {element_id}.")
        planned_low = min(values_tuple)
        planned_high = max(values_tuple)
        first_scan_low = None
        first_scan_high = None
        if first_values is not None:
            first_tuple = tuple(float(value) for value in first_values)
            if not first_tuple:
                raise MachineProfileError(f"No initial-iteration values for {element_id}.")
            first_scan_low = min(first_tuple)
            first_scan_high = max(first_tuple)
        limit = _numeric_limit(machine_limit)
        if limit is None:
            limit_low = None
            limit_high = None
        else:
            limit_low, limit_high = limit
        return RangeCheck(
            label=label,
            element_id=element_id,
            pv_name=pv_name,
            planned_low=planned_low,
            planned_high=planned_high,
            limit_low=limit_low,
            limit_high=limit_high,
            first_scan_low=first_scan_low,
            first_scan_high=first_scan_high,
            requested_points=requested_points,
            feasible_points=feasible_points,
            clipping_allowed=clipping_allowed,
        )

    def _build_corrector_range_check(
        self,
        label: str,
        target: WriteTarget,
        center: float,
    ) -> RangeCheck:
        requested = tuple(
            float(value) for value in relative_scan_points(center, self.preset.corrector_scan)
        )
        feasible = _bounded_candidate_values(
            center,
            self.preset.corrector_scan,
            _numeric_limit(target.machine_limit),
        )
        return self._build_range_check(
            label,
            target.element_id,
            feasible or requested,
            target.pv_name,
            target.machine_limit,
            first_values=requested,
            requested_points=len(requested),
            feasible_points=len(feasible),
            clipping_allowed=True,
        )

    def _read_optional(self, pv_name: str | None) -> float | None:
        return self.io.read(pv_name) if pv_name else None

    def _solenoid_tolerance(self) -> float:
        if self.preset.motion_verification is None:
            raise MotionVerificationError(
                f"Preset {self.preset.id!r} has no readback_verification configuration."
            )
        return self.preset.motion_verification.solenoid_readback_tolerance

    def _corrector_tolerance(self) -> float:
        if self.preset.motion_verification is None:
            raise MotionVerificationError(
                f"Preset {self.preset.id!r} has no readback_verification configuration."
            )
        return self.preset.motion_verification.corrector_readback_tolerance

    def _write_and_verify(
        self,
        label: str,
        setpoint_pv: str,
        readback_pv: str | None,
        value: float,
        tolerance: float,
        *,
        stop_sensitive: bool,
    ) -> None:
        if readback_pv is None:
            raise MotionVerificationError(f"{label} has no current_readback PV.")
        self.io.write(setpoint_pv, value)
        motion = self.preset.motion_verification
        if motion is None:
            raise MotionVerificationError(
                f"Preset {self.preset.id!r} has no readback_verification configuration."
            )
        deadline = time.monotonic() + motion.readback_timeout_s
        last_readback: float | None = None
        while True:
            if stop_sensitive:
                self._raise_if_stopped()
            last_readback = self.io.read(readback_pv)
            if abs(last_readback - value) <= tolerance:
                return
            if time.monotonic() >= deadline:
                raise MotionVerificationError(
                    f"{label} did not reach {value:g}; readback {last_readback:g}, "
                    f"tolerance {tolerance:g}, timeout {motion.readback_timeout_s:g}s."
                )
            time.sleep(min(motion.poll_interval_s, max(0.0, deadline - time.monotonic())))

    def _assert_expected_state(
        self,
        expected_solenoid: float,
        expected_hcorr: float,
        expected_vcorr: float,
    ) -> None:
        state = (
            ("solenoid", self.solenoid_pv, self.solenoid_readback_pv, expected_solenoid, self._solenoid_tolerance()),
            ("HCOR", self.hcorr_pv, self.hcorr_readback_pv, expected_hcorr, self._corrector_tolerance()),
            ("VCOR", self.vcorr_pv, self.vcorr_readback_pv, expected_vcorr, self._corrector_tolerance()),
        )
        for label, setpoint_pv, readback_pv, expected, tolerance in state:
            if readback_pv is None:
                raise StateDriftError(f"{label} readback PV is unavailable.")
            setpoint = self.io.read(setpoint_pv)
            readback = self.io.read(readback_pv)
            if abs(setpoint - readback) > tolerance:
                raise StateDriftError(
                    f"{label} is not readback-verified: set={setpoint:g}, readback={readback:g}."
                )
            if abs(setpoint - expected) > tolerance:
                raise StateDriftError(
                    f"{label} changed since scan: expected {expected:g}, current {setpoint:g}."
                )

    def _restore_correctors(self, hcorr: float, vcorr: float) -> list[str]:
        errors = []
        for label, setpoint_pv, readback_pv, value in (
            ("HCOR", self.hcorr_pv, self.hcorr_readback_pv, hcorr),
            ("VCOR", self.vcorr_pv, self.vcorr_readback_pv, vcorr),
        ):
            try:
                self._write_and_verify(
                    label,
                    setpoint_pv,
                    readback_pv,
                    value,
                    self._corrector_tolerance(),
                    stop_sensitive=False,
                )
            except Exception as exc:
                errors.append(f"{label} ({setpoint_pv}): {exc}")
        return errors

    def _evaluate_candidate(
        self,
        axis: str,
        round_index: int,
        hcorr: float,
        vcorr: float,
        original_solenoid: float,
    ) -> CandidateResult:
        self._write_and_verify(
            "HCOR",
            self.hcorr_pv,
            self.hcorr_readback_pv,
            hcorr,
            self._corrector_tolerance(),
            stop_sensitive=True,
        )
        self._write_and_verify(
            "VCOR",
            self.vcorr_pv,
            self.vcorr_readback_pv,
            vcorr,
            self._corrector_tolerance(),
            stop_sensitive=True,
        )
        self._sleep(self.preset.settle_time_s)

        solenoid_values = []
        x_samples = []
        y_samples = []
        for solenoid in relative_scan_points(original_solenoid, self.preset.solenoid_scan):
            self._raise_if_stopped()
            solenoid = float(solenoid)
            self._write_and_verify(
                "solenoid",
                self.solenoid_pv,
                self.solenoid_readback_pv,
                solenoid,
                self._solenoid_tolerance(),
                stop_sensitive=True,
            )
            self._sleep(self.preset.settle_time_s)
            xs, ys = self._sample_bpm()
            solenoid_values.append(solenoid)
            x_samples.append(xs)
            y_samples.append(ys)

        score = evaluate_solenoid_response(
            solenoid_values,
            x_samples,
            y_samples,
            scoring_mode=self.scoring_mode,
            reference_solenoid=original_solenoid,
        )
        return CandidateResult(
            axis=axis,
            round_index=round_index,
            hcorr=hcorr,
            vcorr=vcorr,
            corrector_value=hcorr if axis == "h" else vcorr,
            solenoid_values=tuple(solenoid_values),
            bpm_x_means=tuple(float(np.mean(values)) for values in x_samples),
            bpm_y_means=tuple(float(np.mean(values)) for values in y_samples),
            score=score,
        )

    def _sample_bpm(self) -> tuple[list[float], list[float]]:
        xs: list[float] = []
        ys: list[float] = []
        for index in range(self.preset.samples_per_point):
            self._raise_if_stopped()
            if index:
                self._sleep(self.preset.sample_interval_s)
            bpm_x, bpm_y = self._read_bpm()
            xs.append(bpm_x)
            ys.append(bpm_y)
        return xs, ys

    def _read_bpm(self) -> tuple[float, float]:
        workflow = self.app_context.solenoid_centering_workflow
        scale = (
            workflow.bpm_position_scale_to_mm.get(self.app_context.control_backend.name, 1.0)
            if workflow is not None
            else 1.0
        )
        return self.io.read(self.bpm_x_pv) * scale, self.io.read(self.bpm_y_pv) * scale

    def _restore_outcome(
        self,
        solenoid: float,
        hcorr: float,
        vcorr: float,
    ) -> RestoreOutcome:
        errors = []
        for label, pv_name, readback_pv, value, tolerance in (
            ("solenoid", self.solenoid_pv, self.solenoid_readback_pv, solenoid, self._solenoid_tolerance()),
            ("HCOR", self.hcorr_pv, self.hcorr_readback_pv, hcorr, self._corrector_tolerance()),
            ("VCOR", self.vcorr_pv, self.vcorr_readback_pv, vcorr, self._corrector_tolerance()),
        ):
            try:
                self._write_and_verify(
                    label,
                    pv_name,
                    readback_pv,
                    value,
                    tolerance,
                    stop_sensitive=False,
                )
            except Exception as exc:
                errors.append(f"{label} ({pv_name}): {exc}")
        return RestoreOutcome(
            status="failed" if errors else "verified",
            errors=tuple(errors),
        )

    def _restore(self, solenoid: float, hcorr: float, vcorr: float) -> None:
        outcome = self._restore_outcome(solenoid, hcorr, vcorr)
        if not outcome.is_verified:
            raise RestoreFailed(
                "Restore failed: " + "; ".join(outcome.errors),
                outcome=outcome,
            )

    def _estimated_candidate_count(self) -> int:
        return self.preset.max_rounds * 2 * self.preset.corrector_scan.steps

    def _estimated_duration_s(self) -> float:
        samples_delay = max(0, self.preset.samples_per_point - 1) * self.preset.sample_interval_s
        per_candidate = self.preset.settle_time_s + self.preset.solenoid_scan.steps * (
            self.preset.settle_time_s + samples_delay
        )
        return float((self._estimated_candidate_count() + 1) * per_candidate)

    def _recommendation_quality(
        self,
        baseline: CandidateResult,
        best: CandidateResult,
    ) -> tuple[float, bool, str]:
        baseline_score = float(baseline.score.score)
        best_score = float(best.score.score)
        if not np.isfinite(baseline_score) or baseline_score <= NOISE_FLOOR:
            return 0.0, False, "baseline score is too small for a reliable recommendation"
        improvement = (baseline_score - best_score) / abs(baseline_score)
        if improvement < self.preset.minimum_relative_score_improvement:
            return (
                float(improvement),
                False,
                f"improvement {improvement:.1%} is below required "
                f"{self.preset.minimum_relative_score_improvement:.1%}",
            )
        return float(improvement), True, "quality gate passed"

    def _scan_config_payload(self) -> dict[str, Any]:
        return {
            "preset_id": self.preset.id,
            "scoring_mode": self.scoring_mode,
            "solenoid_scan": asdict(self.preset.solenoid_scan),
            "corrector_scan": asdict(self.preset.corrector_scan),
            "samples_per_point": self.preset.samples_per_point,
            "settle_time_s": self.preset.settle_time_s,
            "sample_interval_s": self.preset.sample_interval_s,
            "max_iters": self.preset.max_rounds,
            "minimum_relative_score_improvement": self.preset.minimum_relative_score_improvement,
            "readback_verification": (
                asdict(self.preset.motion_verification)
                if self.preset.motion_verification is not None
                else None
            ),
        }

    def _sleep(self, seconds: float) -> None:
        deadline = time.time() + max(0.0, float(seconds))
        while time.time() < deadline:
            self._raise_if_stopped()
            time.sleep(min(0.1, deadline - time.time()))

    def _raise_if_stopped(self) -> None:
        if self.stop_requested():
            raise StopRequested("Solenoid centering scan stopped.")


def _as_2d_samples(values, label: str) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    if array.ndim == 1:
        array = array.reshape((-1, 1))
    if array.ndim != 2 or array.shape[0] == 0 or array.shape[1] == 0:
        raise ValueError(f"{label} must be a non-empty 1D or 2D numeric array.")
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{label} contains NaN or Inf.")
    return array


def normalize_scoring_mode(value: str | None) -> str:
    normalized = str(value or SCORING_MODE_SLOPE).strip().lower().replace("-", "_")
    aliases = {
        "slope": SCORING_MODE_SLOPE,
        "slope_score": SCORING_MODE_SLOPE,
        "trajectory": SCORING_MODE_TRAJECTORY_LENGTH,
        "trajectory_length": SCORING_MODE_TRAJECTORY_LENGTH,
        "length": SCORING_MODE_TRAJECTORY_LENGTH,
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported scoring mode {value!r}; expected one of {', '.join(SCORING_MODES)}."
        ) from exc


def _trajectory_length(x_values: np.ndarray, y_values: np.ndarray) -> float:
    if x_values.size < 2:
        return 0.0
    return float(np.sum(np.hypot(np.diff(x_values), np.diff(y_values))))


def _noise_scale(stds: np.ndarray, noise_floor: float) -> float:
    finite = np.asarray(stds, dtype=float)
    value = float(np.mean(finite)) if finite.size else 0.0
    if not np.isfinite(value) or value <= noise_floor:
        return 1.0
    return value


def _rms(values: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(values))))


def _numeric_limit(limit: LimitRange) -> tuple[float, float] | None:
    if limit.low is None or limit.high is None:
        return None
    return limit.low, limit.high


def _select_preset(context: AppContext, preset_id: str | None) -> SolenoidCenteringPreset:
    workflow = context.solenoid_centering_workflow
    if workflow is None:
        raise MachineProfileError("Solenoid-centering workflow is not available.")
    selected_id = preset_id or workflow.default_preset
    try:
        return workflow.presets_by_id[selected_id]
    except KeyError as exc:
        raise MachineProfileError(f"Unknown solenoid-centering preset: {selected_id}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a solenoid-centering scan.")
    parser.add_argument("--machine", default=None)
    parser.add_argument("--control-backend", default=None)
    parser.add_argument("--preset", default=None)
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Read current PVs and validate planned ranges without writing any PV.",
    )
    parser.add_argument(
        "--scoring-mode",
        choices=SCORING_MODES,
        default=SCORING_MODE_SLOPE,
        help="Candidate ranking metric.",
    )
    args = parser.parse_args(argv)

    stop = {"requested": False}

    def handle_stop(_signum, _frame):
        stop["requested"] = True

    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    context = load_app_context(
        "solenoid_centering",
        machine_id=args.machine,
        control_backend=args.control_backend,
    )
    preset = _select_preset(context, args.preset)
    scanner = SolenoidCenteringScanner(
        context,
        preset,
        scoring_mode=args.scoring_mode,
        progress=lambda message, completed, total: print(f"{completed}/{total} {message}", flush=True),
        stop_requested=lambda: stop["requested"],
    )
    if args.preflight_only:
        try:
            report = scanner.preflight()
        except Exception as exc:
            print("NOT READY", file=sys.stderr)
            print(str(exc), file=sys.stderr)
            return 1
        print(report.as_text())
        return 0 if report.is_ready else 1
    scanner.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
