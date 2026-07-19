from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .controller import ControllerConfig, ControllerReference, IntegralHVController
from .data_buffer import DataBuffer
from .safety import SafetyChecker, SafetyConfig, SafetyReference


REFERENCE_KEYS = [
    "acc1_amp_ref",
    "acc1_phase_ref",
    "buncher_phase_ref",
    "amp_ratio_ref",
    "hv0",
]


@dataclass
class ReferenceBuildResult:
    reference: Optional[SafetyReference]
    values: Optional[Dict[str, float]] = None
    reason: str = ""


def reference_mode(cfg: Dict[str, Any]) -> str:
    mode = str(cfg.get("reference", {}).get("mode", "auto")).strip().lower()
    if mode not in {"auto", "manual"}:
        raise ValueError("reference.mode must be auto or manual")
    return mode


def reference_row(ref: Optional[SafetyReference]) -> Dict[str, object]:
    if ref is None:
        return {}
    return {
        "acc1_amp_ref": ref.acc1_amp_ref,
        "acc1_phase_ref": ref.acc1_phase_ref,
        "buncher_phase_ref": ref.buncher_phase_ref,
        "amp_ratio_ref": ref.amp_ratio_ref,
        "hv0": ref.hv0,
    }


def _finite_float(value: Any, key: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"reference.{key} must be a finite number") from exc
    if not math.isfinite(out):
        raise ValueError(f"reference.{key} must be a finite number")
    return out


def validate_reference_values(values: Dict[str, float], scfg: Dict[str, Any]) -> Optional[str]:
    acc1_amp_ref = values.get("acc1_amp_ref")
    if acc1_amp_ref is None or not math.isfinite(acc1_amp_ref):
        return "acc1_amp_ref is not a finite number"
    if acc1_amp_ref <= 1e-9:
        return f"acc1_amp_ref too small to use as reference: {acc1_amp_ref:.6g}"

    amp_ratio_ref = values.get("amp_ratio_ref")
    if amp_ratio_ref is None or not math.isfinite(amp_ratio_ref) or amp_ratio_ref == 0:
        return f"amp_ratio_ref invalid: {amp_ratio_ref}"

    hv0 = values.get("hv0")
    if hv0 is None or not math.isfinite(hv0):
        return "hv0 is not a finite number"
    hv_min = float(scfg["hv_min_kv"])
    hv_max = float(scfg["hv_max_kv"])
    if not (hv_min <= hv0 <= hv_max):
        return f"hv0 out of allowed bounds: hv0={hv0:.6g}, allowed=[{hv_min}, {hv_max}]"

    for key in ("acc1_phase_ref", "buncher_phase_ref"):
        value = values.get(key)
        if value is None or not math.isfinite(value):
            return f"{key} is not a finite number"
    return None


def reference_from_values(values: Dict[str, float], scfg: Dict[str, Any]) -> ReferenceBuildResult:
    reason = validate_reference_values(values, scfg)
    if reason is not None:
        return ReferenceBuildResult(None, values, reason)
    return ReferenceBuildResult(
        SafetyReference(
            acc1_amp_ref=values["acc1_amp_ref"],
            acc1_phase_ref=values["acc1_phase_ref"],
            buncher_phase_ref=values["buncher_phase_ref"],
            amp_ratio_ref=values["amp_ratio_ref"],
            hv0=values["hv0"],
        ),
        values,
    )


def manual_reference(cfg: Dict[str, Any]) -> ReferenceBuildResult:
    ref_cfg = cfg.get("reference", {})
    values = {key: _finite_float(ref_cfg.get(key), key) for key in REFERENCE_KEYS}
    return reference_from_values(values, cfg["safety"])


def auto_reference(buffer: DataBuffer, scfg: Dict[str, Any]) -> ReferenceBuildResult:
    agg = buffer.aggregate_all()
    if agg is None:
        return ReferenceBuildResult(None)
    required = ["acc1_amp", "acc1_phase", "buncher_phase", "amp_ratio", "hv_readback"]
    if any(key not in agg for key in required):
        return ReferenceBuildResult(None)
    values = {
        "acc1_amp_ref": agg["acc1_amp"],
        "acc1_phase_ref": agg["acc1_phase"],
        "buncher_phase_ref": agg["buncher_phase"],
        "amp_ratio_ref": agg["amp_ratio"],
        "hv0": agg["hv_readback"],
    }
    result = reference_from_values(values, scfg)
    result.values = agg
    return result


def create_feedback_components(
    cfg: Dict[str, Any],
    ref: SafetyReference,
) -> tuple[IntegralHVController, SafetyChecker]:
    ccfg = cfg["control"]
    scfg = cfg["safety"]
    controller = IntegralHVController(
        ControllerConfig(
            gain_kv_per_relerr=float(ccfg["gain_kv_per_relerr"]),
            max_step_kv=float(ccfg["max_step_kv"]),
            total_limit_kv=float(ccfg["total_limit_kv"]),
        ),
        ControllerReference(acc1_amp_ref=ref.acc1_amp_ref, hv0=ref.hv0),
    )
    safety = SafetyChecker(
        SafetyConfig(
            hv_min_kv=float(scfg["hv_min_kv"]),
            hv_max_kv=float(scfg["hv_max_kv"]),
            hv_readback_tolerance_kv=float(scfg["hv_readback_tolerance_kv"]),
            acc1_phase_limit_deg=float(scfg["acc1_phase_limit_deg"]),
            buncher_phase_limit_deg=float(scfg["buncher_phase_limit_deg"]),
            amp_ratio_limit_rel=float(scfg["amp_ratio_limit_rel"]),
            acc1_amp_min_rel=float(scfg["acc1_amp_min_rel"]),
            acc1_amp_max_rel=float(scfg["acc1_amp_max_rel"]),
            require_valid_pv=bool(scfg.get("require_valid_pv", True)),
            hold_on_fault=bool(scfg.get("hold_on_fault", True)),
        ),
        ref,
    )
    return controller, safety
