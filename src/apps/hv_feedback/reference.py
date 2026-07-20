from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .controller import ControllerConfig, ControllerReference, IntegralHVController
from .data_buffer import DataBuffer
from .profile_runtime import amplitude_key, phase_key
from .safety import SafetyChecker, SafetyConfig, SafetyReference


@dataclass
class ReferenceBuildResult:
    reference: Optional[SafetyReference]
    values: Optional[Dict[str, Any]] = None
    reason: str = ""


def reference_row(ref: Optional[SafetyReference]) -> Dict[str, object]:
    if ref is None:
        return {}
    row: Dict[str, object] = {"reference_hv_kv": ref.hv_kv}
    for channel_id in ref.channel_amplitudes:
        row[f"reference.{channel_id}.amplitude"] = ref.channel_amplitudes[channel_id]
        row[f"reference.{channel_id}.phase_deg"] = ref.channel_phases[channel_id]
    return row


def validate_reference_values(
    values: Dict[str, Any],
    config: Dict[str, Any],
) -> Optional[str]:
    try:
        hv_kv = float(values["hv_kv"])
    except (KeyError, TypeError, ValueError):
        return "reference.hv_kv is not a finite number"
    if not math.isfinite(hv_kv):
        return "reference.hv_kv is not a finite number"
    safety = config["safety"]
    if not float(safety["hv_min_kv"]) <= hv_kv <= float(safety["hv_max_kv"]):
        return f"reference.hv_kv out of allowed bounds: {hv_kv:.6g}"

    raw_channels = values.get("channels")
    if not isinstance(raw_channels, dict):
        return "reference.channels must be a mapping"
    channel_ids = [str(channel["id"]) for channel in config["rf_channels"]]
    if set(raw_channels) != set(channel_ids):
        return "reference.channels do not match the feedback unit"
    for channel_id in channel_ids:
        channel_values = raw_channels.get(channel_id)
        if not isinstance(channel_values, dict):
            return f"reference.channels.{channel_id} must be a mapping"
        try:
            amplitude = float(channel_values["amplitude"])
            phase = float(channel_values["phase_deg"])
        except (KeyError, TypeError, ValueError):
            return f"reference values for {channel_id} must be finite numbers"
        if not math.isfinite(amplitude) or amplitude <= 1e-9:
            return f"reference amplitude for {channel_id} too small: {amplitude:.6g}"
        if not math.isfinite(phase):
            return f"reference phase for {channel_id} is not finite"
    return None


def reference_from_values(
    values: Dict[str, Any],
    config: Dict[str, Any],
) -> ReferenceBuildResult:
    reason = validate_reference_values(values, config)
    if reason is not None:
        return ReferenceBuildResult(None, values, reason)
    channels = values["channels"]
    return ReferenceBuildResult(
        SafetyReference(
            hv_kv=float(values["hv_kv"]),
            channel_amplitudes={
                channel_id: float(channel_values["amplitude"])
                for channel_id, channel_values in channels.items()
            },
            channel_phases={
                channel_id: float(channel_values["phase_deg"])
                for channel_id, channel_values in channels.items()
            },
        ),
        values,
    )


def manual_reference(config: Dict[str, Any]) -> ReferenceBuildResult:
    return reference_from_values(config["reference"], config)


def auto_reference(buffer: DataBuffer, config: Dict[str, Any]) -> ReferenceBuildResult:
    aggregate = buffer.aggregate_all()
    if aggregate is None:
        return ReferenceBuildResult(None, reason="No valid samples were collected.")
    channel_ids = [str(channel["id"]) for channel in config["rf_channels"]]
    required = ["hv_readback"]
    for channel_id in channel_ids:
        required.extend((amplitude_key(channel_id), phase_key(channel_id)))
    missing = [key for key in required if key not in aggregate]
    if missing:
        return ReferenceBuildResult(
            None,
            values=aggregate,
            reason=f"Reference samples are missing: {missing}",
        )
    values: Dict[str, Any] = {
        "hv_kv": aggregate["hv_readback"],
        "channels": {
            channel_id: {
                "amplitude": aggregate[amplitude_key(channel_id)],
                "phase_deg": aggregate[phase_key(channel_id)],
            }
            for channel_id in channel_ids
        },
    }
    result = reference_from_values(values, config)
    result.values = aggregate
    return result


def create_feedback_components(
    config: Dict[str, Any],
    ref: SafetyReference,
    feedback_channel_id: str,
) -> tuple[IntegralHVController, SafetyChecker]:
    control = config["control"]
    safety = config["safety"]
    controller = IntegralHVController(
        ControllerConfig(
            gain_kv_per_relerr=float(control["gain_kv_per_relerr"]),
            max_step_kv=float(control["max_step_kv"]),
            total_limit_kv=float(control["total_limit_kv"]),
        ),
        ControllerReference(
            feedback_amplitude_ref=ref.channel_amplitudes[feedback_channel_id],
            hv0=ref.hv_kv,
        ),
        amplitude_key(feedback_channel_id),
    )
    checker = SafetyChecker(
        SafetyConfig(
            hv_min_kv=float(safety["hv_min_kv"]),
            hv_max_kv=float(safety["hv_max_kv"]),
            hv_readback_tolerance_kv=float(safety["hv_readback_tolerance_kv"]),
            phase_limit_deg={
                key: float(value) for key, value in safety["phase_limit_deg"].items()
            },
            amplitude_ratio_limit_rel=float(safety["amplitude_ratio_limit_rel"]),
            feedback_amplitude_min_rel=float(safety["feedback_amplitude_min_rel"]),
            feedback_amplitude_max_rel=float(safety["feedback_amplitude_max_rel"]),
            require_valid_pv=bool(safety.get("require_valid_pv", True)),
            hold_on_fault=bool(safety.get("hold_on_fault", True)),
        ),
        ref,
        feedback_channel_id,
    )
    return controller, checker
