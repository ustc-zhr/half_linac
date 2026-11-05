"""Pure feedback-control mathematics. No machine I/O is available here."""

from __future__ import annotations

import math
import statistics
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, replace

from .cyclic import cyclic_delta, normalize_cyclic
from .models import (
    ActuatorCommand,
    EnergyMeasurement,
    FeedbackDecision,
    PVReading,
    ShadowControlGroup,
)


@dataclass(frozen=True)
class ControlTiming:
    """Resolved non-overlapping control-window timing."""

    sample_period_s: float
    requested_control_period_s: float
    window_samples: int
    effective_control_period_s: float


def derive_control_timing(
    sample_period_s: float,
    control_period_s: float,
) -> ControlTiming:
    """Resolve a requested control period to a whole number of samples.

    A non-integral ratio is rounded up so the effective feedback period is
    never shorter than the operator request.
    """

    sample_period = float(sample_period_s)
    control_period = float(control_period_s)
    if not math.isfinite(sample_period) or sample_period <= 0:
        raise ValueError("sample period must be a finite positive number")
    if not math.isfinite(control_period) or control_period <= 0:
        raise ValueError("control period must be a finite positive number")
    if control_period < sample_period:
        raise ValueError("control period must not be shorter than sample period")

    ratio = control_period / sample_period
    nearest = round(ratio)
    if math.isclose(ratio, nearest, rel_tol=1e-9, abs_tol=1e-9):
        window_samples = max(1, int(nearest))
    else:
        window_samples = max(1, int(math.ceil(ratio)))
    return ControlTiming(
        sample_period_s=sample_period,
        requested_control_period_s=control_period,
        window_samples=window_samples,
        effective_control_period_s=sample_period * window_samples,
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
    requested_knob_delta: float | None = None,
    control_parameters: Mapping[str, float] | None = None,
) -> FeedbackDecision:
    """Return a complete suggestion or HOLD; partial commands are never emitted."""

    error_value = measurement.control_error
    if not measurement.quality.valid or error_value is None:
        return FeedbackDecision(
            "HOLD",
            f"measurement invalid: {measurement.quality.reason}",
            error_value,
        )
    error_mev = float(error_value)
    if not math.isfinite(error_mev):
        return FeedbackDecision("HOLD", "observable error is not finite", error_mev)
    response = float(group.response_mev_per_knob_unit)
    if not math.isfinite(response) or response == 0:
        return FeedbackDecision("HOLD", "control response is invalid", error_mev)
    if not group.actuators:
        return FeedbackDecision("HOLD", "control group has no actuators", error_mev)
    if (
        not math.isfinite(group.loop_gain)
        or group.loop_gain < 0
        or (requested_knob_delta is None and group.loop_gain == 0)
        or not math.isfinite(group.max_knob_step)
        or group.max_knob_step <= 0
        or not math.isfinite(group.deadband_mev)
        or group.deadband_mev < 0
        or not math.isfinite(group.action_window_mev)
        or group.action_window_mev <= group.deadband_mev
    ):
        return FeedbackDecision("HOLD", "control group parameters are invalid", error_mev)
    if abs(error_mev) <= group.deadband_mev:
        return FeedbackDecision("IDLE", "inside feedback deadband", error_mev, 0.0)
    if abs(error_mev) >= group.action_window_mev:
        return FeedbackDecision("HOLD", "observable error exceeds action window", error_mev)

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

    knob_delta = (
        -float(group.loop_gain) * error_mev / response
        if requested_knob_delta is None
        else float(requested_knob_delta)
    )
    if not math.isfinite(knob_delta):
        return FeedbackDecision("HOLD", "controller proposal is not finite", error_mev)
    raw_knob_delta = knob_delta
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
        control_parameters={
            **dict(control_parameters or {}),
            "raw_knob_delta": raw_knob_delta,
            "limited_knob_delta": knob_delta,
        },
    )


@dataclass(frozen=True)
class PIProposal:
    error: float
    p_component: float
    i_component: float
    knob_delta: float


class IncrementalPIController:
    """Incremental I/PI controller with explicit, bumpless state commits."""

    def __init__(self, *, kp: float = 0.0, ki: float = 0.1, period_s: float = 1.0):
        self.configure(kp=kp, ki=ki, period_s=period_s)
        self._previous_error: float | None = None

    def configure(self, *, kp: float, ki: float, period_s: float) -> None:
        values = (float(kp), float(ki), float(period_s))
        if not all(math.isfinite(value) for value in values):
            raise ValueError("PI parameters must be finite")
        if values[0] < 0 or values[1] < 0 or values[2] <= 0:
            raise ValueError("kp and ki must be nonnegative and period must be positive")
        self.kp, self.ki, self.period_s = values

    def reset(self, *, current_error: float | None = None) -> None:
        self._previous_error = (
            float(current_error)
            if current_error is not None and math.isfinite(current_error)
            else None
        )

    def propose(self, error: float, response: float) -> PIProposal:
        error = float(error)
        response = float(response)
        if not math.isfinite(error) or not math.isfinite(response) or response == 0:
            raise ValueError("error and non-zero response must be finite")
        previous = error if self._previous_error is None else self._previous_error
        p_component = self.kp * (error - previous) / response
        i_component = self.ki * self.period_s * error / response
        return PIProposal(error, p_component, i_component, p_component + i_component)

    def commit(self, proposal: PIProposal) -> None:
        self._previous_error = float(proposal.error)


class NonOverlappingMedianWindow:
    """Collect exactly N valid samples; invalid input discards the partial window."""

    def __init__(self, size: int):
        if int(size) <= 0:
            raise ValueError("window size must be positive")
        self.size = int(size)
        self._values: deque[tuple[float, Mapping[str, float]]] = deque()
        self.sequence = 0

    def clear(self) -> None:
        self._values.clear()

    @property
    def pending(self) -> int:
        return len(self._values)

    def add(
        self,
        value: float | None,
        diagnostics: Mapping[str, float | None],
        *,
        valid: bool,
    ) -> tuple[int, float, dict[str, float]] | None:
        if not valid or value is None or not math.isfinite(float(value)):
            self.clear()
            return None
        finite_diagnostics = {
            key: float(item)
            for key, item in diagnostics.items()
            if item is not None and math.isfinite(float(item))
        }
        self._values.append((float(value), finite_diagnostics))
        if len(self._values) < self.size:
            return None
        rows = tuple(self._values)
        self.clear()
        self.sequence += 1
        keys = set.intersection(*(set(row[1]) for row in rows)) if rows else set()
        medians = {
            key: float(statistics.median(row[1][key] for row in rows))
            for key in keys
        }
        return (
            self.sequence,
            float(statistics.median(row[0] for row in rows)),
            medians,
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
