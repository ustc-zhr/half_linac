from __future__ import annotations

import argparse
import signal
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Protocol

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
    ElementConfig,
    MachineProfileError,
    SolenoidCenteringPreset,
    SolenoidCenteringScanRange,
    load_app_context,
    require_workflow_write_allowed,
    resolve_channel,
    resolve_corrector_write_channel,
)
from half_linac.src.apps.solenoid_centering.profile_runtime import write_scan_result


NOISE_FLOOR = 1e-15


class StopRequested(RuntimeError):
    """Raised when a running scan is asked to stop."""


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
class CenteringResult:
    preset_id: str
    original_solenoid: float
    original_hcorr: float
    original_vcorr: float
    recommended_hcorr: float
    recommended_vcorr: float
    best_score: float
    axis_scans: tuple[AxisScanResult, ...]

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

    @property
    def has_limit(self) -> bool:
        return self.limit_low is not None and self.limit_high is not None

    @property
    def is_ok(self) -> bool:
        if not self.has_limit:
            return True
        assert self.limit_low is not None
        assert self.limit_high is not None
        return self.planned_low >= self.limit_low and self.planned_high <= self.limit_high


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
    bpm_x: float
    bpm_y: float
    solenoid_points: int
    corrector_candidates: int
    bpm_samples: int
    estimated_duration_s: float
    range_checks: tuple[RangeCheck, ...]

    @property
    def is_ready(self) -> bool:
        return all(item.is_ok for item in self.range_checks)

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
                f"corrector candidates={self.corrector_candidates}, "
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
            status = "OK" if item.is_ok else "OUT OF LIMIT"
            lines.append(
                f"{status} {item.label} {item.element_id}: planned "
                f"[{item.planned_low:g}, {item.planned_high:g}], limit {limit}"
            )
        return "\n".join(lines)


def relative_scan_points(center: float, scan_range: SolenoidCenteringScanRange) -> np.ndarray:
    if scan_range.steps <= 0:
        raise ValueError("scan_range.steps must be positive.")
    return center + np.linspace(
        float(scan_range.relative_from),
        float(scan_range.relative_to),
        int(scan_range.steps),
    )


def evaluate_solenoid_response(
    solenoid_values: Iterable[float],
    x_samples,
    y_samples,
    *,
    noise_floor: float = NOISE_FLOOR,
) -> ResponseScore:
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

    slope_x, offset_x = np.polyfit(sol, x_means, 1)
    slope_y, offset_y = np.polyfit(sol, y_means, 1)
    residual_x = x_means - (slope_x * sol + offset_x)
    residual_y = y_means - (slope_y * sol + offset_y)
    centered_x = x_means - x_means.mean()
    centered_y = y_means - y_means.mean()
    score = float(np.hypot(slope_x / scale_x, slope_y / scale_y))

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
    )


def coordinate_descent(
    initial_hcorr: float,
    initial_vcorr: float,
    corrector_scan: SolenoidCenteringScanRange,
    max_rounds: int,
    evaluator: Callable[[str, int, float, float], CandidateResult],
) -> tuple[float, float, tuple[AxisScanResult, ...]]:
    if max_rounds <= 0:
        raise ValueError("max_rounds must be positive.")
    hcorr = float(initial_hcorr)
    vcorr = float(initial_vcorr)
    axis_scans: list[AxisScanResult] = []

    for round_index in range(max_rounds):
        h_candidates = []
        for h_value in relative_scan_points(hcorr, corrector_scan):
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
        for v_value in relative_scan_points(vcorr, corrector_scan):
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

    return hcorr, vcorr, tuple(axis_scans)


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
        stop_requested: Callable[[], bool] | None = None,
    ):
        self.app_context = app_context
        self.preset = preset
        self.io = io or EpicsScalarIO()
        self.progress = progress or (lambda _message, _completed, _total: None)
        self.stop_requested = stop_requested or (lambda: False)
        self.solenoid_pv = preset.solenoid_setpoint_pv
        self.solenoid_readback_pv = preset.solenoid_readback_pv
        self.hcorr_pv = resolve_corrector_write_channel(app_context, preset.hcorr)
        self.vcorr_pv = resolve_corrector_write_channel(app_context, preset.vcorr)
        self.bpm_x_pv = resolve_channel(app_context, preset.bpm, "x")
        self.bpm_y_pv = resolve_channel(app_context, preset.bpm, "y")

    def run(self) -> CenteringResult:
        require_workflow_write_allowed(
            self.app_context,
            "solenoid_centering",
            "Solenoid centering scan",
        )
        report = self.preflight()
        original_solenoid = report.original_solenoid
        original_hcorr = report.original_hcorr
        original_vcorr = report.original_vcorr
        total = self._estimated_candidate_count()
        completed = 0

        def evaluator(axis: str, round_index: int, hcorr: float, vcorr: float) -> CandidateResult:
            nonlocal completed
            self._raise_if_stopped()
            message = f"round {round_index + 1} {axis.upper()} candidate"
            self.progress(message, completed, total)
            result = self._evaluate_candidate(axis, round_index, hcorr, vcorr, original_solenoid)
            completed += 1
            self.progress(message, completed, total)
            return result

        try:
            recommended_h, recommended_v, axis_scans = coordinate_descent(
                original_hcorr,
                original_vcorr,
                self.preset.corrector_scan,
                self.preset.max_rounds,
                evaluator,
            )
            best_score = min(scan.best.score.score for scan in axis_scans)
            result = CenteringResult(
                preset_id=self.preset.id,
                original_solenoid=original_solenoid,
                original_hcorr=original_hcorr,
                original_vcorr=original_vcorr,
                recommended_hcorr=recommended_h,
                recommended_vcorr=recommended_v,
                best_score=float(best_score),
                axis_scans=axis_scans,
            )
            write_scan_result(self.app_context, result.as_dict())
            return result
        finally:
            self._restore(original_solenoid, original_hcorr, original_vcorr)

    def apply_recommended(self, hcorr: float, vcorr: float) -> None:
        require_workflow_write_allowed(
            self.app_context,
            "solenoid_centering",
            "Apply solenoid centering recommendation",
        )
        self._check_single_value_limit(self.preset.hcorr, hcorr, self.hcorr_pv)
        self._check_single_value_limit(self.preset.vcorr, vcorr, self.vcorr_pv)
        self.io.write(self.hcorr_pv, hcorr)
        self.io.write(self.vcorr_pv, vcorr)

    def preflight(self) -> PreflightReport:
        original_solenoid = self.io.read(self.solenoid_pv)
        original_hcorr = self.io.read(self.hcorr_pv)
        original_vcorr = self.io.read(self.vcorr_pv)
        solenoid_readback = None
        if self.solenoid_readback_pv:
            solenoid_readback = self.io.read(self.solenoid_readback_pv)
        bpm_x = self.io.read(self.bpm_x_pv)
        bpm_y = self.io.read(self.bpm_y_pv)

        range_checks: list[RangeCheck] = []
        solenoid_element = self._find_current_set_element(self.solenoid_pv)
        if solenoid_element is not None:
            range_checks.append(
                self._build_range_check(
                    "solenoid",
                    solenoid_element,
                    relative_scan_points(original_solenoid, self.preset.solenoid_scan),
                    self.solenoid_pv,
                )
            )

        range_checks.append(
            self._build_range_check(
                "HCOR",
                self.app_context.profile.get_element(self.preset.hcorr),
                self._corrector_scan_envelope(original_hcorr),
                self.hcorr_pv,
            )
        )
        range_checks.append(
            self._build_range_check(
                "VCOR",
                self.app_context.profile.get_element(self.preset.vcorr),
                self._corrector_scan_envelope(original_vcorr),
                self.vcorr_pv,
            )
        )
        for item in range_checks:
            if not item.is_ok:
                raise MachineProfileError(
                    f"Planned scan for {item.element_id} ({item.pv_name}) is outside configured "
                    f"limits [{item.limit_low:g}, {item.limit_high:g}]: planned "
                    f"[{item.planned_low:g}, {item.planned_high:g}]."
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
            bpm_x=bpm_x,
            bpm_y=bpm_y,
            solenoid_points=self.preset.solenoid_scan.steps,
            corrector_candidates=self._estimated_candidate_count(),
            bpm_samples=self.preset.samples_per_point,
            estimated_duration_s=self._estimated_duration_s(),
            range_checks=tuple(range_checks),
        )

    def _check_single_value_limit(self, element_id: str, value: float, pv_name: str) -> None:
        check = self._build_range_check(
            "corrector",
            self.app_context.profile.get_element(element_id),
            (float(value),),
            pv_name,
        )
        if not check.is_ok:
            raise MachineProfileError(
                f"Planned value for {check.element_id} ({check.pv_name}) is outside configured "
                f"limits [{check.limit_low:g}, {check.limit_high:g}]: planned "
                f"[{check.planned_low:g}, {check.planned_high:g}]."
            )

    def _build_range_check(
        self,
        label: str,
        element: ElementConfig,
        values: Iterable[float],
        pv_name: str,
    ) -> RangeCheck:
        values_tuple = tuple(float(value) for value in values)
        if not values_tuple:
            raise MachineProfileError(f"No planned values for {element.id}.")
        planned_low = min(values_tuple)
        planned_high = max(values_tuple)
        limit = _element_numeric_limit(element)
        if limit is None:
            limit_low = None
            limit_high = None
        else:
            limit_low, limit_high = limit
        return RangeCheck(
            label=label,
            element_id=element.id,
            pv_name=pv_name,
            planned_low=planned_low,
            planned_high=planned_high,
            limit_low=limit_low,
            limit_high=limit_high,
        )

    def _corrector_scan_envelope(self, center: float) -> tuple[float, float]:
        low_delta = min(
            0.0,
            self.preset.max_rounds * min(
                self.preset.corrector_scan.relative_from,
                self.preset.corrector_scan.relative_to,
            ),
        )
        high_delta = max(
            0.0,
            self.preset.max_rounds * max(
                self.preset.corrector_scan.relative_from,
                self.preset.corrector_scan.relative_to,
            ),
        )
        return (float(center + low_delta), float(center + high_delta))

    def _find_current_set_element(self, pv_name: str) -> ElementConfig | None:
        backend = self.app_context.control_backend.name
        for element in self.app_context.profile.elements:
            current_set_by_backend = element.channels.get("current_set") or element.channels.get("setpoint")
            if current_set_by_backend is None:
                continue
            if current_set_by_backend.get(backend) == pv_name:
                return element
        return None

    def _evaluate_candidate(
        self,
        axis: str,
        round_index: int,
        hcorr: float,
        vcorr: float,
        original_solenoid: float,
    ) -> CandidateResult:
        self.io.write(self.hcorr_pv, hcorr)
        self.io.write(self.vcorr_pv, vcorr)
        self._sleep(self.preset.settle_time_s)

        solenoid_values = []
        x_samples = []
        y_samples = []
        for solenoid in relative_scan_points(original_solenoid, self.preset.solenoid_scan):
            self._raise_if_stopped()
            solenoid = float(solenoid)
            self.io.write(self.solenoid_pv, solenoid)
            self._sleep(self.preset.settle_time_s)
            xs, ys = self._sample_bpm()
            solenoid_values.append(solenoid)
            x_samples.append(xs)
            y_samples.append(ys)

        score = evaluate_solenoid_response(solenoid_values, x_samples, y_samples)
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
            xs.append(self.io.read(self.bpm_x_pv))
            ys.append(self.io.read(self.bpm_y_pv))
        return xs, ys

    def _restore(self, solenoid: float, hcorr: float, vcorr: float) -> None:
        errors = []
        for pv_name, value in (
            (self.solenoid_pv, solenoid),
            (self.hcorr_pv, hcorr),
            (self.vcorr_pv, vcorr),
        ):
            try:
                self.io.write(pv_name, value)
            except Exception as exc:
                errors.append(f"{pv_name}: {exc}")
        if errors:
            raise RuntimeError("Restore failed: " + "; ".join(errors))

    def _estimated_candidate_count(self) -> int:
        return self.preset.max_rounds * 2 * self.preset.corrector_scan.steps

    def _estimated_duration_s(self) -> float:
        samples_delay = max(0, self.preset.samples_per_point - 1) * self.preset.sample_interval_s
        per_candidate = self.preset.settle_time_s + self.preset.solenoid_scan.steps * (
            self.preset.settle_time_s + samples_delay
        )
        return float(self._estimated_candidate_count() * per_candidate)

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


def _noise_scale(stds: np.ndarray, noise_floor: float) -> float:
    finite = np.asarray(stds, dtype=float)
    value = float(np.mean(finite)) if finite.size else 0.0
    if not np.isfinite(value) or value <= noise_floor:
        return 1.0
    return value


def _rms(values: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(values))))


def _element_numeric_limit(element: ElementConfig) -> tuple[float, float] | None:
    raw_low = element.limits.get("low")
    raw_high = element.limits.get("high")
    if raw_low is None or raw_high is None:
        return None
    low = float(raw_low)
    high = float(raw_high)
    if not np.isfinite(low) or not np.isfinite(high) or low > high:
        return None
    return low, high


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
        return 0
    scanner.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
