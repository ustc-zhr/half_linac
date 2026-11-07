from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


def amplitude_key(channel_id: str) -> str:
    return f"rf.{channel_id}.amplitude"

def phase_key(channel_id: str) -> str:
    return f"rf.{channel_id}.phase"

def phase_diff_deg(angle: float, reference: float) -> float:
    return (angle - reference + 180.0) % 360.0 - 180.0


@dataclass
class SafetyConfig:
    hv_min_kv: float | None
    hv_max_kv: float | None
    hv_readback_tolerance_kv: float
    phase_limit_deg: Dict[str, float]
    amplitude_ratio_limit_rel: float | None = None
    feedback_amplitude_min_rel: float | None = None
    feedback_amplitude_max_rel: float | None = None
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
        feedback_channel_id: str | None = None,
    ):
        self.cfg = cfg
        self.ref = ref
        self.feedback_channel_id = feedback_channel_id
        self.channel_ids = tuple(ref.channel_phases)
        if (cfg.hv_min_kv is None) != (cfg.hv_max_kv is None):
            raise ValueError("HV range must provide both limits or neither.")
        amplitude_range_configured = (
            cfg.feedback_amplitude_min_rel is not None
            or cfg.feedback_amplitude_max_rel is not None
        )
        if amplitude_range_configured and (
            cfg.feedback_amplitude_min_rel is None
            or cfg.feedback_amplitude_max_rel is None
        ):
            raise ValueError("Feedback amplitude range must provide both limits.")
        if (
            amplitude_range_configured or cfg.amplitude_ratio_limit_rel is not None
        ) and feedback_channel_id is None:
            raise ValueError("Amplitude checks require a feedback channel.")

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
            required.append(phase_key(channel_id))
        feedback_id = self.feedback_channel_id
        check_feedback_amplitude = (
            self.cfg.feedback_amplitude_min_rel is not None
            and self.cfg.feedback_amplitude_max_rel is not None
        )
        if check_feedback_amplitude:
            assert feedback_id is not None
            required.append(amplitude_key(feedback_id))
        if self.cfg.amplitude_ratio_limit_rel is not None:
            required.extend(amplitude_key(channel_id) for channel_id in self.channel_ids)
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
        if (
            self.cfg.hv_min_kv is not None
            and self.cfg.hv_max_kv is not None
            and not (self.cfg.hv_min_kv <= hv_to_check <= self.cfg.hv_max_kv)
        ):
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

        if check_feedback_amplitude:
            assert feedback_id is not None
            feedback_amp = agg[amplitude_key(feedback_id)]
            feedback_ref = self.ref.channel_amplitudes[feedback_id]
            if feedback_amp == 0 or feedback_ref == 0:
                return SafetyResult(False, "Feedback channel amplitude is zero.")
            feedback_rel = feedback_amp / feedback_ref
            assert self.cfg.feedback_amplitude_min_rel is not None
            assert self.cfg.feedback_amplitude_max_rel is not None
            if (
                feedback_rel < self.cfg.feedback_amplitude_min_rel
                or feedback_rel > self.cfg.feedback_amplitude_max_rel
            ):
                return SafetyResult(
                    False,
                    f"{feedback_id} amplitude relative out of range: {feedback_rel:.4%}",
                )

        if self.cfg.amplitude_ratio_limit_rel is not None:
            assert feedback_id is not None
            feedback_amp = agg[amplitude_key(feedback_id)]
            feedback_ref = self.ref.channel_amplitudes[feedback_id]
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
