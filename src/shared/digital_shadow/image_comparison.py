"""Engineering comparison of saved image moments at a verified VM point."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from .image_capture import _image_rms, verify_image_bundle
from .image_store import ImageStore, file_hash
from .models import timestamp


POLICY = {"min_roi_signal_fraction": .999, "max_edge_signal_fraction": .001,
          "min_sigma_pixels": 2.0}
PROCESSING = "Native pixels retained; profile flip/ROI, optional background subtraction, RMS moments v1"


def _analyze(frame, store, cache):
    def load(reference):
        key = reference["sha256"]
        if key not in cache:
            cache[key] = store.get(reference)
        return cache[key]
    geometry = frame["geometry"]
    w, h = geometry["shape"]
    pixel = float(geometry["pixel_width_mm"])
    if (not all(isinstance(n, int) and not isinstance(n, bool) and n > 1 for n in (w, h))
            or not math.isfinite(pixel) or pixel <= 0):
        raise ValueError("Invalid frozen image geometry/calibration")
    roi = geometry.get("default_roi", {"x": 0, "y": 0, "width": w, "height": h})
    x, y, nx, ny = (roi[key] for key in ("x", "y", "width", "height"))
    if (not all(isinstance(n, int) and not isinstance(n, bool) for n in (x, y, nx, ny))
            or min(x, y) < 0 or min(nx, ny) < 2 or x+nx > w or y+ny > h):
        raise ValueError("Frozen ROI lies outside the saved image or is too small")
    raw = load(frame["object"])
    if raw.size != w*h:
        raise ValueError("Saved pixels differ from frozen image geometry")
    background_ref = frame.get("background", {}).get("object")
    background = load(background_ref) if background_ref else None
    if background is not None and background.shape != (h, w):
        raise ValueError("Saved background shape differs from image geometry")
    measured = _image_rms(raw, geometry, background)
    recorded = frame.get("derived_rms", {})
    if (recorded.get("method") != "RMS moments" or recorded.get("width_definition") != "image_roi_projection_rms"
            or recorded.get("background_subtracted") != (background is not None)):
        raise ValueError("Recorded image processing definition differs from saved inputs")
    if measured["status"] != "valid" or recorded.get("status") != "valid":
        raise ValueError("Image RMS is not valid")
    for key in ("sigx_mm", "sigy_mm"):
        value = float(recorded[key])
        if not math.isfinite(value) or not math.isclose(value, measured[key], rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError("Recorded image RMS differs from recomputation of saved pixels")
    image = np.asarray(raw, dtype=float).reshape(h, w)
    raw_signal = float(np.clip(image, 0, None).sum())
    negative = bool(np.any(image < 0))
    if background is not None:
        # Same rule as subtract_background(), used by _image_rms().
        image = np.maximum(image - np.asarray(background, dtype=float), 0)
    if geometry.get("flip_y"):
        image = np.flipud(image)
    weights = np.clip(image, 0, None)
    cut = weights[y:y+ny, x:x+nx]
    total, selected = float(weights.sum()), float(cut.sum())
    if not math.isfinite(total) or total <= 0 or selected <= 0:
        raise ValueError("Image has no finite positive signal")
    def edge_fraction(array):
        edges = float(array[0].sum()+array[-1].sum()+array[1:-1, 0].sum()+array[1:-1, -1].sum())
        return edges / float(array.sum())
    full_geometry = {key: value for key, value in geometry.items() if key != "default_roi"}
    full = _image_rms(raw, full_geometry, background)
    if full["status"] != "valid":
        raise ValueError("Full-frame image RMS is not valid")
    return measured, {"roi_signal_fraction": selected/total, "frame_edge_signal_fraction": edge_fraction(weights),
                      "roi_edge_signal_fraction": edge_fraction(cut), "pixel_width_mm": pixel,
                      "full_frame_sigx_mm": full["sigx_mm"], "full_frame_sigy_mm": full["sigy_mm"],
                      "background_removed_signal_fraction": (raw_signal-total)/raw_signal if raw_signal else None,
                      "negative_pixels_without_background": negative and background is None}


def compare_image_diagnostics(context, snapshot, request, predictions_m, bundle, *, vm_evidence=None):
    """No PV access; SIZE observations and image moments retain separate identities."""
    verify_image_bundle(context, snapshot, bundle)
    diagnostic_window = None
    vm_point = None
    if vm_evidence is not None:
        from .vm_capture import verify_vm_evidence
        diagnostic_window = verify_vm_evidence(context, snapshot, request, vm_evidence)
        status = vm_evidence["status"]
        vm_point = {key: status.get(key) for key in ("session", "calculation", "input_version", "result_version")}
        vm_point["image_publication"] = status.get("publication", {}).get("screens") is True
    store = ImageStore(bundle["store_root"], bundle["summary"]["limit_bytes"])
    frames = {frame["screen"]: frame for frame in bundle["frames"]}
    started, finished = timestamp(snapshot.capture_started_at), timestamp(snapshot.capture_finished_at)
    window = diagnostic_window or (started-request.diagnostic_max_age_s, finished)
    verified_vm = (snapshot.backend == "vm" and snapshot.consistency == "vm_result_matched"
                   and diagnostic_window is not None and vm_point["image_publication"]
                   and not snapshot.snapshot_id.startswith("synthetic_"))
    shared = Path(__file__).resolve().parents[1]
    algorithms = {name: file_hash(shared/name) for name in ("beam_diagnostics/image_fit.py", "beam_diagnostics/background_store.py")}
    cache, analyses, rows = {}, {}, []
    for diagnostic in request.diagnostics:
        channel = "x" if diagnostic.channel == "sigx" else "y"
        frame = frames.get(diagnostic.element)
        row = {"key": f"{diagnostic.element}/image_rms_{channel}", "prediction_key": diagnostic.key,
               "unit": diagnostic.unit, "predicted": predictions_m[diagnostic.key]*(1000 if diagnostic.unit == "mm" else 1),
               "measured": None, "residual": None, "relative_residual_percent": None,
               "status": "unavailable", "detail": "", "measured_width_definition": "image_roi_projection_rms",
               "model_width_definition": "transverse_rms", "physics_precision_validated": False}
        try:
            if frame is None:
                raise ValueError("No saved image for this diagnostic")
            row["source"] = {"pv_name": frame["pv_name"], "image_sha256": frame.get("object", {}).get("sha256"),
                             "background_sha256": frame.get("background", {}).get("object", {}).get("sha256"),
                             "source_timestamp": frame.get("source_timestamp"), "received_at": frame.get("received_at"),
                             "reader_kind": frame.get("reader_kind"), "synthetic": frame.get("synthetic", False)}
            if bundle.get("processing") != PROCESSING:
                raise ValueError("Unknown archived image processing rules")
            if frame.get("quality") != "ok" or frame.get("alarm_severity") != 0 or frame.get("alarm_status") is None:
                raise ValueError(frame.get("detail") or "Image quality/alarm metadata is unusable")
            source = float(frame["source_timestamp"])
            if not math.isfinite(source) or not window[0] <= source <= window[1]:
                raise ValueError("Image source time is outside the bound working-point window")
            if not started <= timestamp(frame["received_at"]) <= finished:
                raise ValueError("Image receipt time is outside the capture window")
            if diagnostic.element not in analyses:
                analyses[diagnostic.element] = _analyze(frame, store, cache)
            measured, metrics = analyses[diagnostic.element]
            value_mm = measured[f"sig{channel}_mm"]
            row["measured"] = value_mm*(1 if diagnostic.unit == "mm" else .001)
            row["processing"] = {"geometry": deepcopy(frame["geometry"]), "method": measured["method"],
                                 "background_subtracted": measured["background_subtracted"],
                                 "numeric_string_conversion": measured["numeric_string_conversion"], "algorithms": algorithms}
            row["metrics"] = {**metrics, "sigma_pixels": value_mm/metrics["pixel_width_mm"],
                              "roi_minus_full_frame_mm": value_mm-metrics[f"full_frame_sig{channel}_mm"]}
            identity = {"source": row["source"], "processing": row["processing"]}
            row["analysis_hash"] = hashlib.sha256(json.dumps(identity, sort_keys=True, allow_nan=False).encode()).hexdigest()
            limits = []
            if metrics["roi_signal_fraction"] < POLICY["min_roi_signal_fraction"]:
                limits.append("ROI excludes significant recorded signal")
            if max(metrics["frame_edge_signal_fraction"], metrics["roi_edge_signal_fraction"]) > POLICY["max_edge_signal_fraction"]:
                limits.append("Signal touches the frame/ROI edge")
            if row["metrics"]["sigma_pixels"] < POLICY["min_sigma_pixels"]:
                limits.append("Image RMS spans fewer than two pixels")
            if metrics["negative_pixels_without_background"]:
                limits.append("Negative pixels without a saved background")
            row["limitations"] = limits
            row["caveat"] = "Finite field of view and pixel binning remain; this is an engineering difference, not a physical accuracy certificate."
            if not verified_vm or frame.get("synthetic", False):
                row.update(status="unverified", detail="A verified VM working point with successful image publication is required for residuals in this phase")
            elif limits:
                row.update(status="limited", detail="; ".join(limits))
            else:
                predicted = float(row["predicted"])
                if not math.isfinite(predicted) or predicted <= 0:
                    raise ValueError("Model RMS must be positive and finite")
                difference = row["measured"]-predicted
                row.update(status="compared", residual=difference, relative_residual_percent=100*difference/predicted,
                           detail="Verified VM point; image ROI RMS minus model transverse RMS")
        except (ValueError, TypeError, KeyError, OSError) as exc:
            row.update(status="unavailable", residual=None, relative_residual_percent=None, detail=str(exc))
        rows.append(row)
    return {"schema": "digital_shadow_image_comparison_v1", "policy": dict(POLICY), "comparisons": rows,
            "vm_working_point": vm_point,
            "status": "complete" if all(row["status"] == "compared" for row in rows) else "partial",
            "counts": {status: sum(row["status"] == status for row in rows)
                       for status in ("compared", "limited", "unverified", "unavailable")}}
