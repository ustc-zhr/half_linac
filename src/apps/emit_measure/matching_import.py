"""Convert existing emittance archives to SI measurement inputs."""
from copy import deepcopy
import math

from .matching import MeasurementBaseline, Point, Twiss


def measurement_from_dict(data):
    data = deepcopy(data)
    data["point"] = Point(**data["point"])
    data["planes"] = {p: Twiss(**t) for p, t in data["planes"].items()}
    return MeasurementBaseline(**data)


def import_measurement(payload, *, line=None):
    if "planes" in payload and "point" in payload:
        return measurement_from_dict(payload)
    if payload.get("schema") == "emit_matching_v1":
        return measurement_from_dict(payload["result"]["request"]["measurement"])
    multi = payload.get("schema") == "emit_multi_screen_v1"
    if multi:
        fit = payload.get("reconstruction") or {}
        source = payload.get("reference_element")
        edge = "exit"  # MultiScreenOptics uses exit2exit maps.
    else:
        summaries = payload.get("fit_summary", {})
        fit = summaries if "xplane" in summaries else summaries.get("leastSquares", summaries.get("parabolic", {}))
        source = payload.get("source_quad") or payload.get("quad")
        edge = "entrance"
    planes = {}
    for p in ("x", "y"):
        item = fit.get(p if multi else p + "plane", {})
        if item.get("status") != "valid":
            raise ValueError(f"Archive has no valid {p.upper()} fit; recalculate both planes first")
        if multi:
            emit = item["geometric_emittance_m_rad"]
        else:
            # Legacy scan fits operate on sigma^2 in mm^2. determinant retains
            # precision even when the displayed emittance was rounded to zero.
            det = item.get("determinant")
            emit = (math.sqrt(float(det)) if det is not None and float(det) > 0
                    else float(item["emittance"])) * 1e-6
        planes[p] = Twiss(float(item["beta_m" if multi else "beta"]), float(item["alpha"]), float(emit))
    snapshot = payload.get("model_snapshot") or {}
    overrides = {}
    for item in snapshot.get("fields", []):
        overrides.setdefault(item["element_id"], {})[item["field_name"]] = item["value"]
    return MeasurementBaseline(
        Point(str(source or ""), edge), float(payload["energy_mev"]), planes,
        str(payload.get("machine", payload.get("machine_id", ""))), str(payload.get("backend", "")),
        str(line or payload.get("model_line", "")), overrides,
        {"kind": "multi_screen" if multi else "quad_scan", "archive": deepcopy(payload),
         "uncertainty": {p: deepcopy(fit.get(p if multi else p + "plane")) for p in ("x", "y")}},
        False)
