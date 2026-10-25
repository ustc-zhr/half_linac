"""Pure feedback-control mathematics. No machine I/O is available here."""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from dataclasses import replace

from .cyclic import cyclic_delta, normalize_cyclic
from .models import (
    ActuatorCommand,
    EnergyMeasurement,
    FeedbackDecision,
    PVReading,
    ShadowControlGroup,
)


def unavailable_decision() -> FeedbackDecision:
    return FeedbackDecision(
        "HOLD",
        "Feedback unavailable: actuator group not commissioned",
        None,
    )


def configure_control_group(
    group: ShadowControlGroup,
    *,
    loop_gain: float,
    ratio_actuator_id: str,
    ratio: float,
    ratio_sign: float,
    max_knob_step: float,
    action_window_mev: float,
    max_session_delta: float,
) -> ShadowControlGroup:
    """Apply one immutable operator-settings snapshot to a control group."""

    coefficient_by_id = {
        actuator.id: (
            float(ratio_sign) * float(ratio)
            if actuator.id == ratio_actuator_id
            else float(actuator.coefficient)
        )
        for actuator in group.actuators
    }
    actuators = tuple(
        replace(
            actuator,
            coefficient=coefficient_by_id[actuator.id],
            max_step=abs(coefficient_by_id[actuator.id]) * float(max_knob_step),
            max_session_delta=float(max_session_delta),
        )
        for actuator in group.actuators
    )
    return replace(
        group,
        loop_gain=float(loop_gain),
        max_knob_step=float(max_knob_step),
        action_window_mev=float(action_window_mev),
        actuators=actuators,
    )


def evaluate_shadow(
    measurement: EnergyMeasurement,
    group: ShadowControlGroup,
    actuator_readbacks: Mapping[str, PVReading],
    *,
    session_origins: Mapping[str, float] | None = None,
    stale_timeout_s: float,
    now: float | None = None,
) -> FeedbackDecision:
    """Return a complete suggestion or HOLD; partial commands are never emitted."""

    if not measurement.quality.valid or measurement.error_mev is None:
        return FeedbackDecision(
            "HOLD",
            f"measurement invalid: {measurement.quality.reason}",
            measurement.error_mev,
        )
    error_mev = float(measurement.error_mev)
    if not math.isfinite(error_mev):
        return FeedbackDecision("HOLD", "energy error is not finite", error_mev)
    response = float(group.response_mev_per_knob_unit)
    if not math.isfinite(response) or response == 0:
        return FeedbackDecision("HOLD", "control response is invalid", error_mev)
    if not group.actuators:
        return FeedbackDecision("HOLD", "control group has no actuators", error_mev)
    if (
        not math.isfinite(group.loop_gain)
        or group.loop_gain <= 0
        or not math.isfinite(group.max_knob_step)
        or group.max_knob_step <= 0
        or not math.isfinite(group.deadband_mev)
        or group.deadband_mev < 0
        or not math.isfinite(group.action_window_mev)
        or group.action_window_mev <= group.deadband_mev
    ):
        return FeedbackDecision("HOLD", "control group parameters are invalid", error_mev)
    if abs(error_mev) <= group.deadband_mev:
        return FeedbackDecision("IDLE", "inside energy deadband", error_mev, 0.0)
    if abs(error_mev) >= group.action_window_mev:
        return FeedbackDecision("HOLD", "energy error exceeds action window", error_mev)

    timestamp = time.time() if now is None else float(now)
    values: dict[str, float] = {}
    for actuator in group.actuators:
        reading = actuator_readbacks.get(actuator.id)
        issue = _readback_issue(reading, timestamp, stale_timeout_s)
        if issue:
            return FeedbackDecision(
                "HOLD", f"actuator {actuator.id} {issue}", error_mev
            )
        assert reading is not None and reading.value is not None
        values[actuator.id] = float(reading.value)

    knob_delta = -float(group.loop_gain) * error_mev / response
    knob_limit = float(group.max_knob_step)
    for actuator in group.actuators:
        coefficient = abs(float(actuator.coefficient))
        if (
            not math.isfinite(coefficient)
            or coefficient == 0
            or not math.isfinite(actuator.max_step)
            or actuator.max_step <= 0
        ):
            return FeedbackDecision(
                "HOLD", f"actuator {actuator.id} parameters are invalid", error_mev
            )
        # Limiting the shared scalar preserves the configured N-actuator vector.
        knob_limit = min(knob_limit, float(actuator.max_step) / coefficient)
    knob_delta = max(-knob_limit, min(knob_limit, knob_delta))

    commands: list[ActuatorCommand] = []
    origins = session_origins or {}
    for actuator in group.actuators:
        current = values[actuator.id]
        delta = float(actuator.coefficient) * knob_delta
        low = max(actuator.operating_range[0], actuator.machine_limit[0])
        high = min(actuator.operating_range[1], actuator.machine_limit[1])
        proposed = current + delta
        if actuator.cyclic_period is not None:
            proposed = normalize_cyclic(
                proposed,
                lower=low,
                period=actuator.cyclic_period,
            )
        if not low <= proposed <= high:
            return FeedbackDecision(
                "HOLD",
                f"actuator {actuator.id} proposal exceeds operating or machine limit",
                error_mev,
            )
        if actuator.id in origins:
            origin = float(origins[actuator.id])
            if not math.isfinite(origin):
                return FeedbackDecision(
                    "HOLD", f"actuator {actuator.id} session origin is invalid", error_mev
                )
            session_delta = (
                cyclic_delta(
                    proposed,
                    origin,
                    period=actuator.cyclic_period,
                )
                if actuator.cyclic_period is not None
                else proposed - origin
            )
            if abs(session_delta) > actuator.max_session_delta:
                return FeedbackDecision(
                    "HOLD",
                    f"actuator {actuator.id} proposal exceeds session limit",
                    error_mev,
                )
        commands.append(
            ActuatorCommand(
                actuator.id,
                current,
                delta,
                proposed,
                actuator.unit,
            )
        )
    return FeedbackDecision(
        "SHADOW",
        "valid shadow suggestion",
        error_mev,
        knob_delta,
        tuple(commands),
    )


def _readback_issue(
    reading: PVReading | None,
    now: float,
    stale_timeout_s: float,
) -> str:
    if reading is None:
        return "readback missing"
    if not reading.connected:
        return f"readback disconnected: {reading.error or 'not connected'}"
    if reading.error:
        return f"readback invalid: {reading.error}"
    if reading.value is None or not math.isfinite(reading.value):
        return "readback is not finite"
    if reading.source_timestamp is None:
        return "readback source timestamp missing"
    age = now - reading.source_timestamp
    if age > stale_timeout_s:
        return f"readback stale ({age:.2f} s)"
    if age < -stale_timeout_s:
        return "readback timestamp is in the future"
    return ""
