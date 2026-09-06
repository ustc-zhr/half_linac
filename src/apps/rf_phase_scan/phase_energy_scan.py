from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Mapping

import numpy as np


class PhaseEnergyScanCancelled(RuntimeError):
    """Raised when an operator cooperatively stops a phase scan."""


@dataclass(frozen=True)
class PhaseScanSettings:
    low_offset_deg: float
    high_offset_deg: float
    points: int
    phase_settle_time_s: float
    tracking_half_window_mev: float
    fallback_half_window_mev: float
    max_consecutive_failures: int
    energy_low_mev: float
    energy_high_mev: float
    phase_mode: str = "relative"
    retry_first_point_on_failure: bool = False

    def __post_init__(self) -> None:
        finite_values = (
            self.low_offset_deg,
            self.high_offset_deg,
            self.phase_settle_time_s,
            self.tracking_half_window_mev,
            self.fallback_half_window_mev,
            self.energy_low_mev,
            self.energy_high_mev,
        )
        if not all(math.isfinite(float(value)) for value in finite_values):
            raise ValueError("Phase scan settings must be finite.")
        if self.low_offset_deg >= self.high_offset_deg:
            raise ValueError("Phase scan low offset must be less than high offset.")
        if self.high_offset_deg - self.low_offset_deg > 360:
            raise ValueError("Phase scan range must not exceed 360 degrees.")
        if int(self.points) < 3:
            raise ValueError("Phase scan requires at least three points.")
        if self.phase_settle_time_s < 0:
            raise ValueError("Phase settle time must not be negative.")
        if self.tracking_half_window_mev <= 0:
            raise ValueError("Tracking energy window must be positive.")
        if self.fallback_half_window_mev < self.tracking_half_window_mev:
            raise ValueError("Fallback energy window must cover the tracking window.")
        if int(self.max_consecutive_failures) < 1:
            raise ValueError("Maximum consecutive failures must be at least one.")
        if self.energy_low_mev >= self.energy_high_mev:
            raise ValueError("Energy limits must contain low < high.")
        if self.phase_mode not in {"relative", "absolute"}:
            raise ValueError("Phase scan mode must be 'relative' or 'absolute'.")


@dataclass(frozen=True)
class EnergyMatchResult:
    ok: bool
    status: str
    energy_mev: float | None = None
    message: str | None = None
    center_offset_mm: float | None = None
    brightness: float | None = None
    valid_frames: int | None = None
    fit_method: str | None = None
    fit_r_squared: float | None = None
    center_spread_mm: float | None = None
    beam_threshold: float | None = None
    beam_area_px: int | None = None
    beam_major_axis_px: float | None = None
    beam_minor_axis_px: float | None = None
    beam_aspect_ratio: float | None = None
    beam_orientation_rad: float | None = None


@dataclass(frozen=True)
class PhaseScanPoint:
    index: int
    offset_deg: float
    requested_phase_unwrapped_deg: float
    command_phase_deg: float
    status: str
    attempts: int
    search_low_mev: float
    search_high_mev: float
    matched_energy_mev: float | None = None
    center_offset_mm: float | None = None
    brightness: float | None = None
    valid_frames: int | None = None
    fit_method: str | None = None
    fit_r_squared: float | None = None
    center_spread_mm: float | None = None
    beam_threshold: float | None = None
    beam_area_px: int | None = None
    beam_major_axis_px: float | None = None
    beam_minor_axis_px: float | None = None
    beam_aspect_ratio: float | None = None
    beam_orientation_rad: float | None = None
    message: str | None = None
    acquisition_index: int = -1


@dataclass(frozen=True)
class PhaseEnergyFit:
    baseline_energy_mev: float
    amplitude_mev: float
    crest_phase_unwrapped_deg: float
    crest_phase_command_deg: float
    rmse_mev: float
    r_squared: float
    points_used: int


@dataclass(frozen=True)
class PhaseScanResult:
    status: str
    initial_phase_deg: float | None
    initial_energy_mev: float | None
    points: tuple[PhaseScanPoint, ...] = ()
    fit: PhaseEnergyFit | None = None
    message: str | None = None
    phase_restored: bool = False
    energy_restored: bool = False
    restore_errors: Mapping[str, str] = field(default_factory=dict)

    def to_mapping(self) -> dict[str, Any]:
        return asdict(self)


def wrap_phase_deg(value: float) -> float:
    """Wrap a phase to the machine command interval [-180, 180)."""
    wrapped = (float(value) + 180.0) % 360.0 - 180.0
    return 0.0 if wrapped == 0.0 else wrapped


def phase_difference_deg(value: float, reference: float) -> float:
    return wrap_phase_deg(float(value) - float(reference))


def wait_for_phase_readback(
    read_phase: Callable[[], float | None],
    target_deg: float,
    *,
    tolerance_deg: float,
    timeout_s: float,
    poll_interval_s: float,
) -> float:
    """Wait for a wrapped phase readback to reach a commanded target."""
    target = float(target_deg)
    tolerance = float(tolerance_deg)
    timeout = float(timeout_s)
    poll_interval = float(poll_interval_s)
    if not math.isfinite(target):
        raise ValueError("Phase target must be finite.")
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("Phase readback tolerance must be finite and non-negative.")
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError("Phase readback timeout must be finite and non-negative.")
    if not math.isfinite(poll_interval) or poll_interval <= 0:
        raise ValueError("Phase readback poll interval must be finite and positive.")

    deadline = time.monotonic() + timeout
    actual = None
    while True:
        raw = read_phase()
        try:
            candidate = float(raw) if raw is not None else math.nan
        except (TypeError, ValueError):
            candidate = math.nan
        if math.isfinite(candidate):
            actual = candidate
            if abs(phase_difference_deg(actual, target)) <= tolerance:
                return actual
        if time.monotonic() >= deadline:
            actual_text = "unavailable" if actual is None else f"{actual:g} deg"
            raise RuntimeError(
                "LLRF phase readback verification failed: "
                f"target={target:g} deg, readback={actual_text}, "
                f"tolerance={tolerance:g} deg, timeout={timeout:g} s."
            )
        time.sleep(min(poll_interval, max(deadline - time.monotonic(), 0.0)))


def fit_phase_energy_curve(
    points: tuple[PhaseScanPoint, ...] | list[PhaseScanPoint],
) -> PhaseEnergyFit | None:
    valid = [
        point
        for point in points
        if point.status == "ok"
        and point.matched_energy_mev is not None
        and math.isfinite(float(point.matched_energy_mev))
    ]
    if len(valid) < 5:
        return None

    phases_deg = np.asarray(
        [point.requested_phase_unwrapped_deg for point in valid], dtype=float
    )
    energies = np.asarray([point.matched_energy_mev for point in valid], dtype=float)
    phases_rad = np.deg2rad(phases_deg)
    design = np.column_stack(
        (np.ones(len(valid)), np.cos(phases_rad), np.sin(phases_rad))
    )
    if np.linalg.matrix_rank(design) < 3 or np.linalg.cond(design) > 1e10:
        return None
    coefficients, _residuals, _rank, _singular = np.linalg.lstsq(
        design, energies, rcond=None
    )
    predicted = design @ coefficients
    residual = energies - predicted
    rmse = float(np.sqrt(np.mean(residual**2)))
    total = float(np.sum((energies - np.mean(energies)) ** 2))
    r_squared = 1.0 if total <= np.finfo(float).eps else 1.0 - float(
        np.sum(residual**2)
    ) / total
    baseline, cosine_coefficient, sine_coefficient = coefficients
    crest_command = wrap_phase_deg(
        math.degrees(math.atan2(sine_coefficient, cosine_coefficient))
    )
    phase_center = float(np.mean(phases_deg))
    crest_unwrapped = crest_command + 360.0 * round(
        (phase_center - crest_command) / 360.0
    )
    return PhaseEnergyFit(
        baseline_energy_mev=float(baseline),
        amplitude_mev=float(math.hypot(cosine_coefficient, sine_coefficient)),
        crest_phase_unwrapped_deg=float(crest_unwrapped),
        crest_phase_command_deg=float(crest_command),
        rmse_mev=rmse,
        r_squared=float(r_squared),
        points_used=len(valid),
    )


class PhaseEnergyScanner:
    """Scan one wrapped phase setpoint and match beam energy at every point."""

    def __init__(
        self,
        *,
        settings: PhaseScanSettings,
        read_phase: Callable[[], float | None],
        set_phase: Callable[[float], None],
        read_energy: Callable[[], float | None],
        set_energy: Callable[[float], None],
        match_energy: Callable[[float, float, float, int], EnergyMatchResult],
        cancel_requested: Callable[[], bool] | None = None,
        progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> None:
        self.settings = settings
        self.read_phase = read_phase
        self.set_phase = set_phase
        self.read_energy = read_energy
        self.set_energy = set_energy
        self.match_energy = match_energy
        self.cancel_requested = cancel_requested or (lambda: False)
        self.progress_callback = progress_callback

    @staticmethod
    def _finite_snapshot(value: float | None, name: str) -> float:
        if value is None:
            raise RuntimeError(f"Could not read initial {name} setpoint.")
        result = float(value)
        if not math.isfinite(result):
            raise RuntimeError(f"Initial {name} setpoint is not finite.")
        return result

    def _raise_if_cancelled(self) -> None:
        if self.cancel_requested():
            raise PhaseEnergyScanCancelled("Phase-energy scan stopped by operator.")

    def _wait(self, duration_s: float, *, cancellable: bool = True) -> None:
        deadline = time.monotonic() + max(float(duration_s), 0.0)
        while True:
            if cancellable:
                self._raise_if_cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 0.05))

    def _emit(self, event: str, **payload: Any) -> None:
        if self.progress_callback is not None:
            self.progress_callback({"event": event, **payload})

    def _bounds(self, center: float, half_window: float) -> tuple[float, float]:
        low = max(self.settings.energy_low_mev, float(center) - float(half_window))
        high = min(self.settings.energy_high_mev, float(center) + float(half_window))
        if low >= high:
            raise RuntimeError("Energy search window is empty after limit clipping.")
        return low, high

    def _scan_targets(self, initial_phase: float) -> list[tuple[int, float, float]]:
        values = np.linspace(
            self.settings.low_offset_deg,
            self.settings.high_offset_deg,
            int(self.settings.points),
        )
        targets = []
        for curve_index, scan_value in enumerate(values):
            if self.settings.phase_mode == "relative":
                offset = float(scan_value)
                requested = initial_phase + offset
            else:
                requested = float(scan_value)
                offset = requested - initial_phase
            targets.append((curve_index, offset, requested))
        return sorted(
            targets,
            key=lambda item: (abs(item[2] - initial_phase), item[2]),
        )

    def run(self) -> PhaseScanResult:
        initial_phase = None
        initial_energy = None
        points: list[PhaseScanPoint] = []
        status = "FAILED"
        message = None
        phase_restored = False
        energy_restored = False
        restore_errors: dict[str, str] = {}

        try:
            initial_phase = self._finite_snapshot(self.read_phase(), "phase")
            initial_energy = self._finite_snapshot(self.read_energy(), "energy")
            self._emit(
                "start",
                initial_phase_deg=initial_phase,
                initial_energy_mev=initial_energy,
            )
            scan_targets = self._scan_targets(initial_phase)
            valid_matches: list[tuple[float, float]] = []
            consecutive_failures = 0

            for acquisition_index, (index, offset, requested_phase) in enumerate(scan_targets):
                self._raise_if_cancelled()
                command_phase = wrap_phase_deg(requested_phase)
                self.set_phase(command_phase)
                self._wait(self.settings.phase_settle_time_s)
                self._emit(
                    "phase_set",
                    index=index,
                    acquisition_index=acquisition_index,
                    offset_deg=float(offset),
                    requested_phase_unwrapped_deg=requested_phase,
                    command_phase_deg=command_phase,
                )

                if acquisition_index == 0:
                    if self.settings.retry_first_point_on_failure:
                        windows = (
                            self.settings.tracking_half_window_mev,
                            self.settings.fallback_half_window_mev,
                        )
                    else:
                        windows = (self.settings.fallback_half_window_mev,)
                else:
                    windows = (
                        self.settings.tracking_half_window_mev,
                        self.settings.fallback_half_window_mev,
                    )
                if valid_matches:
                    search_center = min(
                        valid_matches,
                        key=lambda item: abs(item[0] - requested_phase),
                    )[1]
                else:
                    search_center = initial_energy
                match = None
                final_low = final_high = search_center
                for attempt, half_window in enumerate(windows, start=1):
                    self._raise_if_cancelled()
                    final_low, final_high = self._bounds(search_center, half_window)
                    self._emit(
                        "match_start",
                        index=index,
                        acquisition_index=acquisition_index,
                        attempt=attempt,
                        search_center_mev=search_center,
                        search_low_mev=final_low,
                        search_high_mev=final_high,
                    )
                    match = self.match_energy(search_center, final_low, final_high, attempt)
                    if str(match.status).upper() == "CANCELLED":
                        raise PhaseEnergyScanCancelled(
                            match.message or "Phase-energy scan stopped by operator."
                        )
                    if str(match.status).upper() == "MEASUREMENT_FAILED":
                        break
                    if match.ok and match.energy_mev is not None:
                        break

                assert match is not None
                if match.ok and match.energy_mev is not None:
                    valid_matches.append((requested_phase, float(match.energy_mev)))
                    consecutive_failures = 0
                    point_status = "ok"
                else:
                    consecutive_failures += 1
                    point_status = "failed"

                point = PhaseScanPoint(
                    index=index,
                    acquisition_index=acquisition_index,
                    offset_deg=float(offset),
                    requested_phase_unwrapped_deg=requested_phase,
                    command_phase_deg=command_phase,
                    status=point_status,
                    attempts=attempt,
                    search_low_mev=final_low,
                    search_high_mev=final_high,
                    matched_energy_mev=(
                        float(match.energy_mev)
                        if match.ok and match.energy_mev is not None
                        else None
                    ),
                    center_offset_mm=match.center_offset_mm,
                    brightness=match.brightness,
                    valid_frames=match.valid_frames,
                    fit_method=match.fit_method,
                    fit_r_squared=match.fit_r_squared,
                    center_spread_mm=match.center_spread_mm,
                    beam_threshold=match.beam_threshold,
                    beam_area_px=match.beam_area_px,
                    beam_major_axis_px=match.beam_major_axis_px,
                    beam_minor_axis_px=match.beam_minor_axis_px,
                    beam_aspect_ratio=match.beam_aspect_ratio,
                    beam_orientation_rad=match.beam_orientation_rad,
                    message=match.message,
                )
                points.append(point)
                self._emit("point", point=asdict(point))

                if consecutive_failures >= self.settings.max_consecutive_failures:
                    raise RuntimeError(
                        f"Energy matching failed at {consecutive_failures} consecutive phase points."
                    )

            status = "DONE"
        except PhaseEnergyScanCancelled as exc:
            status = "CANCELLED"
            message = str(exc)
        except Exception as exc:
            status = "FAILED"
            message = str(exc)
        finally:
            if initial_phase is not None:
                try:
                    self.set_phase(wrap_phase_deg(initial_phase))
                    self._wait(self.settings.phase_settle_time_s, cancellable=False)
                    phase_restored = True
                except Exception as exc:
                    restore_errors["phase"] = str(exc)
            if initial_energy is not None:
                try:
                    self.set_energy(initial_energy)
                    energy_restored = True
                except Exception as exc:
                    restore_errors["energy"] = str(exc)
            self._emit(
                "restore",
                phase_restored=phase_restored,
                energy_restored=energy_restored,
                restore_errors=dict(restore_errors),
            )

        if restore_errors:
            status = "RESTORE_FAILED"
            restore_message = "; ".join(
                f"{key}: {value}" for key, value in restore_errors.items()
            )
            message = f"{message}; restore failed: {restore_message}" if message else (
                f"Restore failed: {restore_message}"
            )
        fit = fit_phase_energy_curve(points)
        result = PhaseScanResult(
            status=status,
            initial_phase_deg=initial_phase,
            initial_energy_mev=initial_energy,
            points=tuple(points),
            fit=fit,
            message=message,
            phase_restored=phase_restored,
            energy_restored=energy_restored,
            restore_errors=restore_errors,
        )
        self._emit("result", result=result.to_mapping())
        return result
