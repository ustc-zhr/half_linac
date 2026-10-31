from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Mapping

from .model import DEVICES


class TimingEnergyScanCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class TimingEnergyScanSettings:
    low_offset_us: float = -1.0
    high_offset_us: float = 1.0
    step_us: float = 0.1
    hard_limit_us: float = 1.0
    timing_settle_time_s: float = 1.0
    energy_settle_time_s: float = 3.0
    tracking_half_window_mev: float = 50.0
    energy_low_mev: float = 0.0
    energy_high_mev: float = 2450.0
    minimum_delay_us: float = 0.0
    min_relative_brightness: float = 0.05

    def __post_init__(self) -> None:
        numeric = {
            name: float(getattr(self, name))
            for name in (
                "low_offset_us",
                "high_offset_us",
                "step_us",
                "hard_limit_us",
                "timing_settle_time_s",
                "energy_settle_time_s",
                "tracking_half_window_mev",
                "energy_low_mev",
                "energy_high_mev",
                "minimum_delay_us",
                "min_relative_brightness",
            )
        }
        if not all(math.isfinite(value) for value in numeric.values()):
            raise ValueError("Scan settings must be finite.")
        if self.step_us <= 0 or self.hard_limit_us <= 0:
            raise ValueError("Timing step and hard limit must be positive.")
        if not (
            -self.hard_limit_us
            <= self.low_offset_us
            <= 0
            <= self.high_offset_us
            <= self.hard_limit_us
        ) or self.low_offset_us >= self.high_offset_us:
            raise ValueError(
                "Timing range must contain zero and remain inside the hard limit."
            )
        for endpoint in (abs(self.low_offset_us), abs(self.high_offset_us)):
            quotient = endpoint / self.step_us
            if not math.isclose(quotient, round(quotient), abs_tol=1e-9):
                raise ValueError("Timing range endpoints must lie on the step grid.")
        if self.timing_settle_time_s < 0 or self.energy_settle_time_s < 0:
            raise ValueError("Settle times must not be negative.")
        if self.tracking_half_window_mev <= 0:
            raise ValueError("Energy tracking window must be positive.")
        if self.energy_low_mev >= self.energy_high_mev:
            raise ValueError("Energy bounds must contain low < high.")
        if self.minimum_delay_us < 0:
            raise ValueError("Minimum delay must not be negative.")
        if not 0 <= self.min_relative_brightness <= 1:
            raise ValueError("Minimum relative brightness must be in [0, 1].")

    def offsets(self) -> tuple[float, ...]:
        positive_count = int(round(self.high_offset_us / self.step_us))
        negative_count = int(round(abs(self.low_offset_us) / self.step_us))
        positive = tuple(
            float(index * self.step_us)
            for index in range(1, positive_count + 1)
        )
        negative = tuple(
            float(-index * self.step_us)
            for index in range(1, negative_count + 1)
        )
        return (0.0, *positive, *negative)


@dataclass(frozen=True)
class TimingEnergyMatchResult:
    ok: bool
    status: str
    energy_mev: float | None = None
    energy_setpoint_mev: float | None = None
    energy_standard_deviation_mev: float | None = None
    energy_standard_error_mev: float | None = None
    brightness: float | None = None
    valid_frames: int | None = None
    total_frames: int | None = None
    message: str | None = None
    diagnostics: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TimingEnergyScanPoint:
    index: int
    acquisition_index: int
    offset_us: float
    status: str
    targets_us: Mapping[str, float]
    readbacks_us: Mapping[str, float]
    search_low_mev: float
    search_high_mev: float
    matched_energy_mev: float | None = None
    energy_setpoint_mev: float | None = None
    energy_standard_deviation_mev: float | None = None
    energy_standard_error_mev: float | None = None
    brightness: float | None = None
    valid_frames: int | None = None
    total_frames: int | None = None
    message: str | None = None
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def to_mapping(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TimingEnergyScanResult:
    status: str
    initial_delays_us: Mapping[str, float]
    initial_energy_mev: float | None
    points: tuple[TimingEnergyScanPoint, ...]
    best_point: TimingEnergyScanPoint | None
    timing_restored: bool
    energy_restored: bool
    restore_errors: Mapping[str, str]
    restore_readbacks_us: Mapping[str, float] = field(default_factory=dict)
    message: str | None = None
    csv_path: str | None = None
    json_path: str | None = None

    @property
    def best_at_boundary(self) -> bool:
        if self.best_point is None:
            return False
        offsets = [point.offset_us for point in self.points]
        return bool(offsets) and self.best_point.offset_us in {
            min(offsets),
            max(offsets),
        }

    def to_mapping(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "initial_delays_us": dict(self.initial_delays_us),
            "initial_energy_mev": self.initial_energy_mev,
            "points": [point.to_mapping() for point in self.points],
            "best_point": (
                self.best_point.to_mapping() if self.best_point is not None else None
            ),
            "best_at_boundary": self.best_at_boundary,
            "timing_restored": self.timing_restored,
            "energy_restored": self.energy_restored,
            "restore_errors": dict(self.restore_errors),
            "restore_readbacks_us": dict(self.restore_readbacks_us),
            "message": self.message,
            "csv_path": self.csv_path,
            "json_path": self.json_path,
        }


def delay_targets(
    initial_delays_us: Mapping[str, float],
    offset_us: float,
    *,
    minimum_us: float,
) -> dict[str, float]:
    missing = [device for device in DEVICES if device not in initial_delays_us]
    if missing:
        raise ValueError(
            "Initial delay is unavailable for " + ", ".join(item.upper() for item in missing)
        )
    targets = {
        device: float(initial_delays_us[device]) + float(offset_us)
        for device in DEVICES
    }
    invalid = {
        device: value for device, value in targets.items() if value < float(minimum_us)
    }
    if invalid:
        details = ", ".join(
            f"{device.upper()}={value:g}" for device, value in invalid.items()
        )
        raise ValueError(f"Delay target below {float(minimum_us):g} us: {details}.")
    if not all(math.isfinite(value) for value in targets.values()):
        raise ValueError("Delay targets must be finite.")
    return targets


def select_best_point(
    points: tuple[TimingEnergyScanPoint, ...] | list[TimingEnergyScanPoint],
) -> TimingEnergyScanPoint | None:
    valid = [
        point
        for point in points
        if point.status == "ok"
        and point.matched_energy_mev is not None
        and math.isfinite(float(point.matched_energy_mev))
    ]
    if not valid:
        return None
    return max(
        valid,
        key=lambda point: (
            float(point.matched_energy_mev),
            -abs(float(point.offset_us)),
            -int(point.acquisition_index),
        ),
    )


class TimingEnergyScanner:
    """UI-independent four-channel linked timing scan."""

    def __init__(
        self,
        settings: TimingEnergyScanSettings,
        *,
        read_delays: Callable[[], Mapping[str, float]],
        set_delays: Callable[[Mapping[str, float]], Mapping[str, float]],
        read_energy: Callable[[], float],
        set_energy: Callable[[float], None],
        write_center_energy: Callable[[float], None],
        match_energy: Callable[
            [float, float, float, float, int], TimingEnergyMatchResult
        ],
        cancel_requested: Callable[[], bool] | None = None,
        progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
        wait_callback: Callable[[float, bool], None] | None = None,
    ) -> None:
        self.settings = settings
        self.read_delays = read_delays
        self.set_delays = set_delays
        self.read_energy = read_energy
        self.set_energy = set_energy
        self.write_center_energy = write_center_energy
        self.match_energy = match_energy
        self.cancel_requested = cancel_requested or (lambda: False)
        self.progress_callback = progress_callback or (lambda _payload: None)
        self.wait_callback = wait_callback

    def _emit(self, event: str, **payload: Any) -> None:
        self.progress_callback({"event": event, **payload})

    def _raise_if_cancelled(self) -> None:
        if self.cancel_requested():
            raise TimingEnergyScanCancelled(
                "Linked timing-energy scan stopped by operator."
            )

    def _wait(self, duration_s: float, *, cancellable: bool = True) -> None:
        duration = max(float(duration_s), 0.0)
        if self.wait_callback is not None:
            self.wait_callback(duration, cancellable)
            if cancellable:
                self._raise_if_cancelled()
            return
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            if cancellable:
                self._raise_if_cancelled()
            time.sleep(min(0.05, max(deadline - time.monotonic(), 0.0)))

    @staticmethod
    def _finite(value: Any, name: str) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"{name} is not readable.") from exc
        if not math.isfinite(number):
            raise RuntimeError(f"{name} is not finite.")
        return number

    def _energy_bounds(self, center: float) -> tuple[float, float]:
        half = self.settings.tracking_half_window_mev
        low = max(self.settings.energy_low_mev, float(center) - half)
        high = min(self.settings.energy_high_mev, float(center) + half)
        if low >= high:
            return self.settings.energy_low_mev, self.settings.energy_high_mev
        return low, high

    def run(self) -> TimingEnergyScanResult:
        initial_delays: dict[str, float] = {}
        initial_energy: float | None = None
        points: list[TimingEnergyScanPoint] = []
        timing_restored = False
        energy_restored = False
        restore_errors: dict[str, str] = {}
        restore_readbacks: dict[str, float] = {}
        status = "FAILED"
        message = None

        try:
            initial_delays = {
                device: self._finite(value, f"{device.upper()} initial delay")
                for device, value in self.read_delays().items()
            }
            delay_targets(
                initial_delays,
                self.settings.low_offset_us,
                minimum_us=self.settings.minimum_delay_us,
            )
            delay_targets(
                initial_delays,
                self.settings.high_offset_us,
                minimum_us=self.settings.minimum_delay_us,
            )
            initial_energy = self._finite(self.read_energy(), "Initial energy")
            self._emit(
                "start",
                initial_delays_us=dict(initial_delays),
                initial_energy_mev=initial_energy,
            )
            valid_matches: list[tuple[float, float]] = []
            reference_brightness: float | None = None
            center_energy = initial_energy
            positive_started = False
            center_returned = False

            for acquisition_index, offset in enumerate(self.settings.offsets()):
                self._raise_if_cancelled()
                if offset > 0:
                    positive_started = True
                elif offset < 0 and positive_started and not center_returned:
                    center_targets = delay_targets(
                        initial_delays,
                        0.0,
                        minimum_us=self.settings.minimum_delay_us,
                    )
                    self._emit(
                        "center_return",
                        targets_us=dict(center_targets),
                        energy_setpoint_mev=center_energy,
                        settle_time_s=2.0 * self.settings.energy_settle_time_s,
                    )
                    center_readbacks = dict(self.set_delays(center_targets))
                    self._wait(self.settings.timing_settle_time_s)
                    self._emit(
                        "center_return_verified",
                        targets_us=dict(center_targets),
                        readbacks_us=center_readbacks,
                        energy_setpoint_mev=center_energy,
                    )
                    self.write_center_energy(center_energy)
                    self._wait(2.0 * self.settings.energy_settle_time_s)
                    center_returned = True

                targets = delay_targets(
                    initial_delays,
                    offset,
                    minimum_us=self.settings.minimum_delay_us,
                )
                readbacks = dict(self.set_delays(targets))
                self._wait(self.settings.timing_settle_time_s)
                self._emit(
                    "timing_set",
                    acquisition_index=acquisition_index,
                    offset_us=offset,
                    targets_us=dict(targets),
                    readbacks_us=dict(readbacks),
                )

                search_center = (
                    min(valid_matches, key=lambda item: abs(item[0] - offset))[1]
                    if valid_matches
                    else initial_energy
                )
                search_low, search_high = self._energy_bounds(search_center)
                self._emit(
                    "match_start",
                    acquisition_index=acquisition_index,
                    offset_us=offset,
                    search_center_mev=search_center,
                    search_low_mev=search_low,
                    search_high_mev=search_high,
                )
                match = self.match_energy(
                    offset,
                    search_center,
                    search_low,
                    search_high,
                    1,
                )
                if str(match.status).upper() == "CANCELLED":
                    raise TimingEnergyScanCancelled(
                        match.message or "Energy Match stopped by operator."
                    )

                point_status = "failed"
                point_message = match.message
                if match.ok and match.energy_mev is not None:
                    brightness = match.brightness
                    finite_brightness = (
                        brightness is not None
                        and math.isfinite(float(brightness))
                        and float(brightness) > 0
                    )
                    if reference_brightness is None and finite_brightness:
                        reference_brightness = float(brightness)
                    low_signal = bool(
                        finite_brightness
                        and reference_brightness is not None
                        and float(brightness)
                        < reference_brightness
                        * self.settings.min_relative_brightness
                    )
                    if low_signal:
                        point_status = "low_signal"
                        warning = (
                            "Signal brightness is below the configured fraction "
                            "of the reference point."
                        )
                        point_message = (
                            f"{point_message} {warning}" if point_message else warning
                        )
                    else:
                        point_status = "ok"
                        search_energy = (
                            match.energy_setpoint_mev
                            if match.energy_setpoint_mev is not None
                            else match.energy_mev
                        )
                        if not math.isfinite(float(search_energy)):
                            search_energy = match.energy_mev
                        valid_matches.append((offset, float(search_energy)))
                        if abs(offset) <= 1e-12:
                            center_energy = float(search_energy)

                point = TimingEnergyScanPoint(
                    index=acquisition_index,
                    acquisition_index=acquisition_index,
                    offset_us=float(offset),
                    status=point_status,
                    targets_us=dict(targets),
                    readbacks_us=dict(readbacks),
                    search_low_mev=search_low,
                    search_high_mev=search_high,
                    matched_energy_mev=(
                        float(match.energy_mev)
                        if match.ok and match.energy_mev is not None
                        else None
                    ),
                    energy_setpoint_mev=match.energy_setpoint_mev,
                    energy_standard_deviation_mev=match.energy_standard_deviation_mev,
                    energy_standard_error_mev=match.energy_standard_error_mev,
                    brightness=match.brightness,
                    valid_frames=match.valid_frames,
                    total_frames=match.total_frames,
                    message=point_message,
                    diagnostics=dict(match.diagnostics),
                )
                points.append(point)
                self._emit("point", point=point.to_mapping())
            status = "DONE"
        except TimingEnergyScanCancelled as exc:
            status = "CANCELLED"
            message = str(exc)
        except Exception as exc:
            status = "FAILED"
            message = str(exc)
        finally:
            if initial_delays:
                try:
                    restored = dict(self.set_delays(initial_delays))
                    restore_readbacks = dict(restored)
                    self._wait(
                        self.settings.timing_settle_time_s,
                        cancellable=False,
                    )
                    timing_restored = all(device in restored for device in DEVICES)
                except Exception as exc:
                    restore_errors["timing"] = str(exc)
            if initial_energy is not None:
                try:
                    self.set_energy(initial_energy)
                    energy_restored = True
                except Exception as exc:
                    restore_errors["energy"] = str(exc)
            self._emit(
                "restore",
                targets_us=dict(initial_delays),
                readbacks_us=dict(restore_readbacks),
                timing_restored=timing_restored,
                energy_restored=energy_restored,
                restore_errors=dict(restore_errors),
            )

        if restore_errors:
            status = "RESTORE_FAILED"
            details = "; ".join(
                f"{name}: {error}" for name, error in restore_errors.items()
            )
            message = f"{message}; restore failed: {details}" if message else (
                f"Restore failed: {details}"
            )
        best = select_best_point(points)
        result = TimingEnergyScanResult(
            status=status,
            initial_delays_us=dict(initial_delays),
            initial_energy_mev=initial_energy,
            points=tuple(points),
            best_point=best,
            timing_restored=timing_restored,
            energy_restored=energy_restored,
            restore_errors=restore_errors,
            message=message,
            restore_readbacks_us=restore_readbacks,
        )
        self._emit("result", result=result.to_mapping())
        return result
