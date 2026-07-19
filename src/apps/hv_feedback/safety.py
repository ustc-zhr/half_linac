from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from .utils import phase_diff_deg


@dataclass
class SafetyConfig:
    hv_min_kv: float
    hv_max_kv: float
    hv_readback_tolerance_kv: float
    acc1_phase_limit_deg: float
    buncher_phase_limit_deg: float
    amp_ratio_limit_rel: float
    acc1_amp_min_rel: float
    acc1_amp_max_rel: float
    require_valid_pv: bool = True
    hold_on_fault: bool = True


@dataclass
class SafetyReference:
    acc1_amp_ref: float
    acc1_phase_ref: float
    buncher_phase_ref: float
    amp_ratio_ref: float
    hv0: float


@dataclass
class SafetyResult:
    ok: bool
    reason: str = ""


class SafetyChecker:
    def __init__(self, cfg: SafetyConfig, ref: SafetyReference):
        self.cfg = cfg
        self.ref = ref

    def check_sample_ok(self, sample_ok: bool, sample_errors: Dict[str, str]) -> SafetyResult:
        if self.cfg.require_valid_pv and not sample_ok:
            nonempty = {k: v for k, v in sample_errors.items() if v}
            return SafetyResult(False, f"PV read invalid: {nonempty}")
        return SafetyResult(True, "")

    def check_aggregate(self, agg: Dict[str, float], hv_next: Optional[float] = None) -> SafetyResult:
        required = [
            "hv_setpoint",
            "hv_readback",
            "acc1_amp",
            "acc1_phase",
            "buncher_amp",
            "buncher_phase",
            "amp_ratio",
        ]
        missing = [k for k in required if k not in agg]
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
                f"HV out of bounds: hv={hv_to_check:.6g}, allowed=[{self.cfg.hv_min_kv}, {self.cfg.hv_max_kv}]",
            )

        acc1_phase_err = abs(phase_diff_deg(agg["acc1_phase"], self.ref.acc1_phase_ref))
        if acc1_phase_err > self.cfg.acc1_phase_limit_deg:
            return SafetyResult(False, f"ACC1 phase drift too large: {acc1_phase_err:.4g} deg")

        buncher_phase_err = abs(phase_diff_deg(agg["buncher_phase"], self.ref.buncher_phase_ref))
        if buncher_phase_err > self.cfg.buncher_phase_limit_deg:
            return SafetyResult(False, f"Buncher phase drift too large: {buncher_phase_err:.4g} deg")

        ratio_ref = self.ref.amp_ratio_ref
        if ratio_ref == 0:
            return SafetyResult(False, "Invalid amp_ratio_ref=0")
        ratio_err_rel = abs((agg["amp_ratio"] - ratio_ref) / ratio_ref)
        if ratio_err_rel > self.cfg.amp_ratio_limit_rel:
            return SafetyResult(False, f"buncher_amp/acc1_amp ratio drift too large: {ratio_err_rel:.4%}")

        acc1_rel = agg["acc1_amp"] / self.ref.acc1_amp_ref
        if acc1_rel < self.cfg.acc1_amp_min_rel or acc1_rel > self.cfg.acc1_amp_max_rel:
            return SafetyResult(False, f"acc1_amp relative out of range: {acc1_rel:.4%}")

        return SafetyResult(True, "")
