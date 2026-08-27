from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Mapping

from .machine_profile.limits import LimitRange
from .machine_profile.models import MachineProfile, MachineProfileError
from .machine_profile.resolver import resolve_write_target
from .machine_state import (
    MachineStateSnapshot,
    SampleQuality,
    StateClass,
    classify_channel,
)


_READBACK_CHANNELS = {
    "current_set": "current_readback",
    "setpoint": "readback",
    "phase_set": "phase_readback",
    "delay_set": "delay_readback",
    "width_set": "width_readback",
    "voltage_set": "voltage_readback",
}


@dataclass(frozen=True)
class ControlPoint:
    element_id: str
    element_kind: str
    element_order: int
    logical_channel: str
    setpoint_pv: str
    readback_channel: str | None
    readback_pv: str | None
    unit: str | None
    limit: LimitRange
    tolerance: float | None
    settle_s: float = 0.0
    timeout_s: float = 2.0

    @property
    def key(self) -> str:
        return f"{self.element_id}/{self.logical_channel}"

    @property
    def configuration_issues(self) -> tuple[str, ...]:
        issues = []
        if self.limit.low is None and self.limit.high is None:
            issues.append("physical limit is not configured")
        if self.readback_pv is None:
            issues.append("readback PV is not configured")
        if self.tolerance is None:
            issues.append("readback tolerance is not configured")
        return tuple(issues)


@dataclass(frozen=True)
class ControlDefaults:
    """Layered defaults; exact point overrides are intentionally exceptional."""

    tolerance: float | None = None
    tolerance_by_kind: Mapping[str, float] | None = None
    tolerance_by_channel: Mapping[str, float] | None = None
    tolerance_by_kind_channel: Mapping[str, float] | None = None
    tolerance_by_point: Mapping[str, float] | None = None
    settle_s: float = 0.0
    timeout_s: float = 2.0

    def tolerance_for(self, element_kind: str, logical_channel: str, key: str) -> float | None:
        value = self.tolerance
        for mapping, selector in (
            (self.tolerance_by_kind, element_kind),
            (self.tolerance_by_channel, logical_channel),
            (self.tolerance_by_kind_channel, f"{element_kind}/{logical_channel}"),
            (self.tolerance_by_point, key),
        ):
            if mapping is not None and selector in mapping:
                value = mapping[selector]
        if value is None:
            return None
        selected = float(value)
        if not math.isfinite(selected) or selected <= 0:
            raise ValueError(f"Tolerance for {key} must be finite and positive")
        return selected


class WatchdogStatus(str, Enum):
    MATCH = "match"
    MISMATCH = "mismatch"
    UNAVAILABLE = "unavailable"
    NOT_CONFIGURED = "not_configured"


@dataclass(frozen=True)
class WatchdogResult:
    point: ControlPoint
    setpoint_value: float | None
    readback_value: float | None
    difference: float | None
    status: WatchdogStatus
    detail: str = ""


def sample_watchdog(
    points: tuple[ControlPoint, ...] | list[ControlPoint],
    read: Callable[[str], object],
    *,
    stop_requested: Callable[[], bool] | None = None,
) -> tuple[WatchdogResult, ...]:
    """Read configured SP/RB pairs once; this function never writes PVs."""
    results = []
    should_stop = stop_requested or (lambda: False)
    for point in points:
        if should_stop():
            break
        if point.readback_pv is None or point.tolerance is None:
            results.append(evaluate_watchdog(point, None, None))
            continue
        try:
            setpoint = read(point.setpoint_pv)
        except Exception as exc:
            results.append(
                WatchdogResult(
                    point, None, None, None, WatchdogStatus.UNAVAILABLE,
                    f"setpoint read failed: {type(exc).__name__}: {exc}",
                )
            )
            continue
        try:
            readback = read(point.readback_pv)
        except Exception as exc:
            results.append(
                WatchdogResult(
                    point, _finite_number(setpoint), None, None,
                    WatchdogStatus.UNAVAILABLE,
                    f"readback read failed: {type(exc).__name__}: {exc}",
                )
            )
            continue
        results.append(evaluate_watchdog(point, setpoint, readback))
    return tuple(results)


def collect_control_points(
    profile: MachineProfile,
    backend: str,
    *,
    defaults: ControlDefaults | None = None,
) -> tuple[ControlPoint, ...]:
    """Derive writable SP/RB descriptions without guessing safety parameters."""
    if backend not in profile.control_backends:
        raise MachineProfileError(f"Unknown control backend {backend!r}.")
    workflow = profile.workflows.get("control_points", {})
    configured_backends = workflow.get("backends", {}) if isinstance(workflow, Mapping) else {}
    if backend not in configured_backends:
        return ()
    configured = defaults or control_defaults_from_profile(profile, backend)
    if configured.settle_s < 0 or configured.timeout_s <= 0:
        raise ValueError("settle_s must be non-negative and timeout_s must be positive")
    points = []
    for element in profile.elements:
        for logical_channel in element.channels:
            if classify_channel(element.kind, logical_channel) != StateClass.SETTING:
                continue
            try:
                target = resolve_write_target(
                    profile,
                    element.id,
                    logical_channel=logical_channel,
                    mode=backend,
                )
            except MachineProfileError:
                continue
            if target.logical_channel != logical_channel:
                continue
            readback_channel = _READBACK_CHANNELS.get(logical_channel)
            readback_pv = None
            if readback_channel is not None:
                readback_pv = element.channels.get(readback_channel, {}).get(backend)
            key = f"{element.id}/{logical_channel}"
            tolerance = configured.tolerance_for(element.kind, logical_channel, key)
            points.append(
                ControlPoint(
                    element_id=element.id,
                    element_kind=element.kind,
                    element_order=element.order,
                    logical_channel=logical_channel,
                    setpoint_pv=target.pv_name,
                    readback_channel=readback_channel,
                    readback_pv=readback_pv,
                    unit=target.unit,
                    limit=target.machine_limit,
                    tolerance=tolerance,
                    settle_s=configured.settle_s,
                    timeout_s=configured.timeout_s,
                )
            )
    return tuple(sorted(points, key=lambda point: (point.element_order, point.key)))


def control_defaults_from_profile(
    profile: MachineProfile,
    backend: str,
) -> ControlDefaults:
    workflow = profile.workflows.get("control_points", {})
    if not isinstance(workflow, Mapping):
        raise MachineProfileError("workflows.control_points must be a mapping")
    backends = workflow.get("backends", {})
    if not isinstance(backends, Mapping):
        raise MachineProfileError("workflows.control_points.backends must be a mapping")
    raw = backends.get(backend, {})
    if not isinstance(raw, Mapping):
        raise MachineProfileError(
            f"workflows.control_points.backends.{backend} must be a mapping"
        )
    known = {
        "tolerance",
        "tolerance_by_kind",
        "tolerance_by_channel",
        "tolerance_by_kind_channel",
        "tolerance_by_point",
        "settle_s",
        "timeout_s",
    }
    unknown = sorted(set(raw) - known)
    if unknown:
        raise MachineProfileError(
            f"workflows.control_points.backends.{backend} has unknown keys: "
            f"{', '.join(unknown)}"
        )
    return ControlDefaults(
        tolerance=_optional_number(raw.get("tolerance"), "tolerance"),
        tolerance_by_kind=_number_mapping(raw.get("tolerance_by_kind"), "tolerance_by_kind"),
        tolerance_by_channel=_number_mapping(raw.get("tolerance_by_channel"), "tolerance_by_channel"),
        tolerance_by_kind_channel=_number_mapping(
            raw.get("tolerance_by_kind_channel"), "tolerance_by_kind_channel"
        ),
        tolerance_by_point=_number_mapping(raw.get("tolerance_by_point"), "tolerance_by_point"),
        settle_s=_optional_number(raw.get("settle_s"), "settle_s", default=0.0),
        timeout_s=_optional_number(raw.get("timeout_s"), "timeout_s", default=2.0),
    )


def evaluate_watchdog(
    point: ControlPoint,
    setpoint_value: object,
    readback_value: object,
) -> WatchdogResult:
    if point.readback_pv is None or point.tolerance is None:
        return WatchdogResult(
            point, None, None, None, WatchdogStatus.NOT_CONFIGURED,
            "; ".join(point.configuration_issues),
        )
    setpoint = _finite_number(setpoint_value)
    readback = _finite_number(readback_value)
    if setpoint is None or readback is None:
        return WatchdogResult(
            point, setpoint, readback, None, WatchdogStatus.UNAVAILABLE,
            "setpoint or readback is unavailable",
        )
    difference = readback - setpoint
    numeric_margin = math.ulp(max(abs(setpoint), abs(readback), point.tolerance, 1.0))
    status = (
        WatchdogStatus.MATCH
        if abs(difference) <= point.tolerance + numeric_margin
        else WatchdogStatus.MISMATCH
    )
    return WatchdogResult(point, setpoint, readback, difference, status)


def snapshot_target_values(
    snapshot: MachineStateSnapshot,
) -> Mapping[str, float]:
    values = {}
    for entry in snapshot.entries:
        if entry.state_class != StateClass.SETTING or entry.quality != SampleQuality.OK:
            continue
        value = _finite_number(entry.value)
        if value is not None:
            values[entry.key] = value
    return values


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _optional_number(value: object, label: str, *, default=None):
    if value is None:
        return default
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise MachineProfileError(f"control_points {label} must be numeric") from exc
    if not math.isfinite(result):
        raise MachineProfileError(f"control_points {label} must be finite")
    return result


def _number_mapping(value: object, label: str) -> Mapping[str, float]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise MachineProfileError(f"control_points {label} must be a mapping")
    return {
        str(key): _optional_number(item, f"{label}.{key}")
        for key, item in value.items()
    }
