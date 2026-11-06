"""Verified actuator writes with all-command rollback on partial failure."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from dataclasses import replace

from .client import WritablePVClient
from .cyclic import cyclic_delta, normalize_cyclic
from .models import (
    ActuatorCommand,
    FeedbackDecision,
    PVReading,
    ShadowActuator,
    ShadowControlGroup,
)


_QUANTIZED_NOOP_REASON = "feedback command unchanged after rounding"


def apply_feedback_decision(
    client: WritablePVClient,
    group: ShadowControlGroup,
    decision: FeedbackDecision,
    *,
    timeout_s: float,
    poll_interval_s: float,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> FeedbackDecision:
    """Apply one complete suggestion and verify every independent readback."""

    if not decision.has_valid_suggestion:
        return decision
    actuators = {actuator.id: actuator for actuator in group.actuators}
    if set(actuators) != {command.actuator_id for command in decision.commands}:
        return replace(
            decision,
            state="HOLD",
            reason="feedback command set does not match the control group",
            write_detail="no write attempted",
        )

    rounded_commands: list[ActuatorCommand] = []
    for command in decision.commands:
        actuator = actuators[command.actuator_id]
        proposed = round(float(command.proposed), actuator.command_precision)
        if actuator.cyclic_period is not None:
            low = max(actuator.operating_range[0], actuator.machine_limit[0])
            proposed = normalize_cyclic(
                proposed,
                lower=low,
                period=actuator.cyclic_period,
            )
            delta = cyclic_delta(
                proposed,
                command.current,
                period=actuator.cyclic_period,
            )
        else:
            delta = proposed - float(command.current)
        rounded_commands.append(replace(command, proposed=proposed, delta=delta))
    rounded = tuple(rounded_commands)
    if all(command.delta == 0.0 for command in rounded):
        return replace(
            decision,
            state="SHADOW",
            reason=_QUANTIZED_NOOP_REASON,
            commands=rounded,
            write_verified=False,
            rollback_applied=False,
            write_detail="no write attempted",
        )
    ok, detail, rollback_applied = _write_commands(
        client,
        actuators,
        rounded,
        timeout_s=timeout_s,
        poll_interval_s=poll_interval_s,
        sleep=sleep,
        monotonic=monotonic,
    )
    if ok:
        return replace(
            decision,
            state="APPLIED",
            reason="feedback applied",
            commands=rounded,
            write_verified=True,
            rollback_applied=False,
            write_detail=detail,
        )
    return replace(
        decision,
        state="HOLD",
        reason="control-knob write failed",
        commands=rounded,
        write_verified=False,
        rollback_applied=rollback_applied,
        write_detail=detail,
    )


def apply_manual_targets(
    client: WritablePVClient,
    group: ShadowControlGroup,
    actuator_readbacks: Mapping[str, PVReading],
    targets: Mapping[str, float],
    *,
    timeout_s: float,
    poll_interval_s: float,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> FeedbackDecision:
    """Validate and apply one or more operator-entered actuator targets."""

    if not targets:
        return FeedbackDecision("HOLD", "manual target set is empty", None)
    actuators = {actuator.id: actuator for actuator in group.actuators}
    unknown = sorted(set(targets) - set(actuators))
    if unknown:
        return FeedbackDecision(
            "HOLD", f"manual target references unknown actuators: {unknown}", None
        )

    commands: list[ActuatorCommand] = []
    for actuator_id, target_value in targets.items():
        actuator = actuators[actuator_id]
        reading = actuator_readbacks.get(actuator_id)
        if (
            reading is None
            or not reading.connected
            or reading.value is None
            or not math.isfinite(reading.value)
        ):
            return FeedbackDecision(
                "HOLD", f"manual write requires valid {actuator_id} readback", None
            )
        target = float(target_value)
        if not math.isfinite(target):
            return FeedbackDecision(
                "HOLD", f"manual target for {actuator_id} is not finite", None
            )
        low = max(actuator.operating_range[0], actuator.machine_limit[0])
        high = min(actuator.operating_range[1], actuator.machine_limit[1])
        if actuator.cyclic_period is not None:
            target = normalize_cyclic(
                target,
                lower=low,
                period=actuator.cyclic_period,
            )
        if not low <= target <= high:
            return FeedbackDecision(
                "HOLD",
                f"manual target for {actuator_id} is outside [{low:g}, {high:g}]",
                None,
            )
        current = float(reading.value)
        delta = (
            cyclic_delta(target, current, period=actuator.cyclic_period)
            if actuator.cyclic_period is not None
            else target - current
        )
        commands.append(
            ActuatorCommand(
                actuator_id,
                current,
                delta,
                target,
                actuator.unit,
            )
        )

    proposal = FeedbackDecision(
        "SHADOW",
        "manual control-knob request",
        None,
        commands=tuple(commands),
    )
    selected_group = replace(
        group,
        actuators=tuple(actuators[command.actuator_id] for command in commands),
    )
    result = apply_feedback_decision(
        client,
        selected_group,
        proposal,
        timeout_s=timeout_s,
        poll_interval_s=poll_interval_s,
        sleep=sleep,
        monotonic=monotonic,
    )
    if result.write_verified:
        return replace(result, reason="manual control-knob write applied")
    if result.reason == _QUANTIZED_NOOP_REASON:
        return replace(
            result,
            reason="manual control-knob target unchanged after rounding",
        )
    return replace(result, reason="manual control-knob write failed")


def _write_commands(
    client: WritablePVClient,
    actuators: Mapping[str, ShadowActuator],
    commands: tuple[ActuatorCommand, ...],
    *,
    timeout_s: float,
    poll_interval_s: float,
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
) -> tuple[bool, str, bool]:
    details: list[str] = []
    for command in commands:
        actuator = actuators[command.actuator_id]
        if command.delta == 0.0:
            details.append(f"{actuator.id} unchanged after rounding")
            continue
        if not actuator.setpoint_key or not actuator.readback_key:
            details.append(f"{actuator.id} write keys are not configured")
            return _failed_with_rollback(
                client,
                actuators,
                commands,
                details,
                timeout_s=timeout_s,
                poll_interval_s=poll_interval_s,
                sleep=sleep,
                monotonic=monotonic,
            )
        baseline = client.read_many((actuator.readback_key,))[actuator.readback_key]
        try:
            put_ok = client.write(
                actuator.setpoint_key,
                command.proposed,
                wait=True,
                timeout_s=timeout_s,
            )
        except Exception as exc:  # noqa: BLE001 - converted to failed/rollback result
            put_ok = False
            details.append(f"{actuator.id} write raised: {exc}")
        if not put_ok:
            if not details or not details[-1].startswith(f"{actuator.id} write raised"):
                details.append(f"{actuator.id} write failed")
            return _failed_with_rollback(
                client,
                actuators,
                commands,
                details,
                timeout_s=timeout_s,
                poll_interval_s=poll_interval_s,
                sleep=sleep,
                monotonic=monotonic,
            )
        verified, readback, fresh_seen = _wait_for_readback(
            client,
            actuator,
            command.proposed,
            baseline=baseline,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
            sleep=sleep,
            monotonic=monotonic,
        )
        if not verified:
            if fresh_seen:
                details.append(
                    f"{actuator.id} verify mismatch cmd={command.proposed:.3f}, "
                    f"rb={_format_readback(readback)}"
                )
            else:
                details.append(
                    f"{actuator.id} verify timeout waiting for fresh readback "
                    f"after cmd={command.proposed:.3f}, "
                    f"last rb={_format_readback(readback)}"
                )
            return _failed_with_rollback(
                client,
                actuators,
                commands,
                details,
                timeout_s=timeout_s,
                poll_interval_s=poll_interval_s,
                sleep=sleep,
                monotonic=monotonic,
            )
        details.append(f"{actuator.id} verified at {readback:.3f}")
    return True, "; ".join(details), False


def _failed_with_rollback(
    client: WritablePVClient,
    actuators: Mapping[str, ShadowActuator],
    commands: tuple[ActuatorCommand, ...],
    details: list[str],
    **verification,
) -> tuple[bool, str, bool]:
    rollback_ok = _rollback(client, actuators, commands, **verification)
    details.append(
        "rollback restored previous readbacks" if rollback_ok else "rollback failed"
    )
    return False, "; ".join(details), rollback_ok


def _rollback(
    client: WritablePVClient,
    actuators: Mapping[str, ShadowActuator],
    commands: tuple[ActuatorCommand, ...],
    *,
    timeout_s: float,
    poll_interval_s: float,
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
) -> bool:
    ok = True
    for command in commands:
        actuator = actuators[command.actuator_id]
        if command.delta == 0.0:
            continue
        baseline = client.read_many((actuator.readback_key,))[actuator.readback_key]
        try:
            put_ok = client.write(
                actuator.setpoint_key,
                command.current,
                wait=True,
                timeout_s=timeout_s,
            )
        except Exception:  # noqa: BLE001 - rollback result carries the failure
            put_ok = False
        verified = False
        if put_ok:
            verified, _, _ = _wait_for_readback(
                client,
                actuator,
                command.current,
                baseline=baseline,
                timeout_s=timeout_s,
                poll_interval_s=poll_interval_s,
                sleep=sleep,
                monotonic=monotonic,
            )
        ok = ok and put_ok and verified
    return ok


def _wait_for_readback(
    client: WritablePVClient,
    actuator: ShadowActuator,
    target: float,
    *,
    baseline: PVReading,
    timeout_s: float,
    poll_interval_s: float,
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
) -> tuple[bool, float | None, bool]:
    deadline = monotonic() + timeout_s
    last_value = None
    fresh_seen = False
    while True:
        reading = client.read_many((actuator.readback_key,))[actuator.readback_key]
        if reading.connected and reading.value is not None and math.isfinite(reading.value):
            last_value = float(reading.value)
            reading_is_fresh = _is_fresh_readback(reading, baseline)
            fresh_seen = fresh_seen or reading_is_fresh
            error = (
                cyclic_delta(
                    last_value,
                    target,
                    period=actuator.cyclic_period,
                )
                if actuator.cyclic_period is not None
                else last_value - target
            )
            if reading_is_fresh and abs(error) <= actuator.verify_tolerance:
                return True, last_value, True
        if monotonic() >= deadline:
            return False, last_value, fresh_seen
        sleep(min(poll_interval_s, max(0.0, deadline - monotonic())))


def _is_fresh_readback(reading: PVReading, baseline: PVReading) -> bool:
    return _timestamp_advanced(
        reading.monitor_received_at,
        baseline.monitor_received_at,
    ) or _timestamp_advanced(reading.source_timestamp, baseline.source_timestamp)


def _timestamp_advanced(current: float | None, previous: float | None) -> bool:
    if current is None:
        return False
    return previous is None or current > previous


def _format_readback(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:.3f}"
