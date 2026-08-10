from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from .profile_runtime import amplitude_key, phase_key
from .utils import phase_diff_deg


@dataclass
class SafetyConfig:
    hv_min_kv: float
    hv_max_kv: float
    hv_readback_tolerance_kv: float
    phase_limit_deg: Dict[str, float]
    amplitude_ratio_limit_rel: float
    feedback_amplitude_min_rel: float
    feedback_amplitude_max_rel: float
    require_valid_pv: bool = True
    hold_on_fault: bool = True


@dataclass
class SafetyReference:
    hv_kv: float
    channel_amplitudes: Dict[str, float]
    channel_phases: Dict[str, float]


@dataclass
class SafetyResult:
    ok: bool
    reason: str = ""


class SafetyChecker:
    def __init__(
        self,
        cfg: SafetyConfig,
        ref: SafetyReference,
        feedback_channel_id: str,
    ):
        self.cfg = cfg
        self.ref = ref
        self.feedback_channel_id = feedback_channel_id
        self.channel_ids = tuple(ref.channel_amplitudes)

    def check_sample_ok(self, sample_ok: bool, sample_errors: Dict[str, str]) -> SafetyResult:
        if self.cfg.require_valid_pv and not sample_ok:
            nonempty = {k: v for k, v in sample_errors.items() if v}
            return SafetyResult(False, f"PV read invalid: {nonempty}")
        return SafetyResult(True, "")

    def check_aggregate(
        self,
        agg: Dict[str, float],
        hv_next: Optional[float] = None,
    ) -> SafetyResult:
        required = ["hv_setpoint", "hv_readback"]
        for channel_id in self.channel_ids:
            required.extend((amplitude_key(channel_id), phase_key(channel_id)))
        missing = [key for key in required if key not in agg]
        if missing:
            return SafetyResult(False, f"Missing aggregate fields: {missing}")

        hv_rb = agg["hv_readback"]
        hv_sp = agg["hv_setpoint"]
        if abs(hv_rb - hv_sp) > self.cfg.hv_readback_tolerance_kv:
            return SafetyResult(
                False,
                f"HV readback-setpoint mismatch: rb={hv_rb:.6g}, sp={hv_sp:.6g}",
            )

        hv_to_check = hv_sp if hv_next is None else hv_next
        if not (self.cfg.hv_min_kv <= hv_to_check <= self.cfg.hv_max_kv):
            return SafetyResult(
                False,
                f"HV out of bounds: hv={hv_to_check:.6g}, "
                f"allowed=[{self.cfg.hv_min_kv}, {self.cfg.hv_max_kv}]",
            )

        for channel_id in self.channel_ids:
            error = abs(
                phase_diff_deg(
                    agg[phase_key(channel_id)],
                    self.ref.channel_phases[channel_id],
                )
            )
            if error > self.cfg.phase_limit_deg[channel_id]:
                return SafetyResult(
                    False,
                    f"{channel_id} phase drift too large: {error:.4g} deg",
                )

        feedback_id = self.feedback_channel_id
        feedback_amp = agg[amplitude_key(feedback_id)]
        feedback_ref = self.ref.channel_amplitudes[feedback_id]
        feedback_rel = feedback_amp / feedback_ref
        if (
            feedback_rel < self.cfg.feedback_amplitude_min_rel
            or feedback_rel > self.cfg.feedback_amplitude_max_rel
        ):
            return SafetyResult(
                False,
                f"{feedback_id} amplitude relative out of range: {feedback_rel:.4%}",
            )

        if feedback_amp == 0 or feedback_ref == 0:
            return SafetyResult(False, "Feedback channel amplitude is zero.")
        for channel_id in self.channel_ids:
            if channel_id == feedback_id:
                continue
            current_ratio = agg[amplitude_key(channel_id)] / feedback_amp
            reference_ratio = self.ref.channel_amplitudes[channel_id] / feedback_ref
            if reference_ratio == 0:
                return SafetyResult(False, f"Invalid reference ratio for {channel_id}.")
            ratio_error = abs(current_ratio / reference_ratio - 1.0)
            if ratio_error > self.cfg.amplitude_ratio_limit_rel:
                return SafetyResult(
                    False,
                    f"{channel_id}/{feedback_id} amplitude ratio drift too large: "
                    f"{ratio_error:.4%}",
                )

        return SafetyResult(True, "")
