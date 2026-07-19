from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from .utils import clamp


@dataclass
class ControllerConfig:
    gain_kv_per_relerr: float
    max_step_kv: float
    total_limit_kv: float


@dataclass
class ControllerReference:
    acc1_amp_ref: float
    hv0: float


@dataclass
class ControllerOutput:
    error_rel: float
    delta_hv_raw: float
    delta_hv: float
    hv_next: float
    saturated_step: bool
    saturated_total: bool


class IntegralHVController:
    def __init__(self, cfg: ControllerConfig, ref: ControllerReference):
        self.cfg = cfg
        self.ref = ref

    def compute(self, agg: Dict[str, float], hv_setpoint_now: float) -> ControllerOutput:
        acc1_amp = agg["acc1_amp"]
        error_rel = (self.ref.acc1_amp_ref - acc1_amp) / self.ref.acc1_amp_ref
        delta_hv_raw = self.cfg.gain_kv_per_relerr * error_rel
        delta_hv = clamp(delta_hv_raw, -self.cfg.max_step_kv, self.cfg.max_step_kv)
        hv_unclamped = hv_setpoint_now + delta_hv
        low = self.ref.hv0 - self.cfg.total_limit_kv
        high = self.ref.hv0 + self.cfg.total_limit_kv
        hv_next = clamp(hv_unclamped, low, high)
        return ControllerOutput(
            error_rel=error_rel,
            delta_hv_raw=delta_hv_raw,
            delta_hv=hv_next - hv_setpoint_now,
            hv_next=hv_next,
            saturated_step=abs(delta_hv_raw) > self.cfg.max_step_kv,
            saturated_total=hv_next != hv_unclamped,
        )
