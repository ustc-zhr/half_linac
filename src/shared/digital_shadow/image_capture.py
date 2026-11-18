"""Optional one-frame sidecar, separate from scalar MachineStateSnapshot."""
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time

import numpy as np

from half_linac.src.shared.beam_diagnostics.background_store import subtract_background
from half_linac.src.shared.beam_diagnostics.image_fit import fit_beam_image
from half_linac.src.shared.machine_profile.resolver import resolve_channel
from half_linac.src.shared.machine_state import snapshot_to_dict

from .image_store import ImageStore, array_digest, file_hash, load_array, store_usage
from .models import timestamp


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def load_backgrounds(path):
    """Explicit per-screen files in native (unflipped) camera orientation."""
    if path is None:
        return {}
    path = Path(path).resolve()
    mapping = json.loads(path.read_text())
    if not isinstance(mapping, dict):
        raise ValueError("Background JSON must map screen IDs to path/provenance objects")
    result = {}
    for screen, item in mapping.items():
        if not isinstance(item, dict) or not str(item.get("provenance", "")).strip():
            raise ValueError(f"Background {screen} requires an explicit provenance")
        source = (path.parent / item["path"]).resolve()
        result[screen] = {"array": load_array(source), "source_path": str(source),
                          "source_file_sha256": file_hash(source), "provenance": item["provenance"]}
    return result


class ImageCapture:
    def __init__(self, context, request, *, backgrounds=None, synthetic=False):
        self.backend = context.control_backend.name
        if self.backend not in {"vm", "real"}:
            raise ValueError("Images require an explicit VM or real backend")
        self.synthetic = synthetic
        self.machine_id = context.machine.id
        self.request = request
        self.backgrounds = backgrounds or {}
        self.frames = []
        self.plan = []
        others = {mapping[other] for element in context.profile.elements
                  for mapping in element.channels.values() for other in mapping if other != self.backend}
        for screen in dict.fromkeys(d.element for d in request.diagnostics):
            element = context.profile.get_element(screen)
            pv = resolve_channel(context, screen, "image", self.backend)
            geometry = json.loads(json.dumps(element.image_geometry[self.backend]))
            if pv in others or pv in {p[1] for p in self.plan}:
                raise ValueError("Ambiguous image PV mapping; no image connection made")
            if screen in self.backgrounds:
                w, h = geometry["shape"]
                background = self.backgrounds[screen]["array"]
                if background.shape != (h, w) or not np.all(np.isfinite(np.asarray(background, dtype=float))):
                    raise ValueError(f"Background shape/values invalid for {screen}; use native 2D orientation")
            self.plan.append((screen, pv, geometry))
        if set(self.backgrounds) - {p[0] for p in self.plan}:
            raise ValueError("Background supplied for an unselected screen")

    def sample(self, deadline, *, pv_factory=None, poll_function=None, allow_live=False):
        if self.frames:
            raise ValueError("ImageCapture is single-use; no repeated image reads")
        if pv_factory is None and self.backend == "real" and not allow_live:
            raise ValueError("Real image reads require the explicit real capture authorization")
        if pv_factory is not None and poll_function is None:
            raise ValueError("An injected image reader requires an injected poll function")
        injected = pv_factory is not None
        if pv_factory is None:
            import epics
            pv_factory = lambda name: epics.PV(name, auto_monitor=False, form="time")
            poll_function = lambda interval: epics.ca.poll(evt=interval, iot=max(interval, 0.001))
        for index, (screen, name, geometry) in enumerate(self.plan):
            frame = {"screen": screen, "pv_name": name, "geometry": geometry, "quality": "not_read",
                     "reader_kind": "synthetic" if self.synthetic else "injected_reader" if injected else "live",
                     "synthetic": self.synthetic, "source_timestamp": None, "received_at": None}
            pv = None
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Shared capture budget exhausted; image PV was not read")
                # A disconnected screen cannot consume every other screen's turn.
                turn_end = time.monotonic() + remaining / (len(self.plan) - index)
                pv = pv_factory(name)
                while not pv.connected and time.monotonic() < turn_end:
                    poll_function(min(0.01, max(0.0, turn_end - time.monotonic())))
                if not pv.connected or time.monotonic() >= turn_end:
                    raise TimeoutError("Image connection exceeded its share of the capture window")
                expected_count = math.prod(geometry["shape"])
                capacity = getattr(pv, "count", None)
                if capacity is None or not expected_count <= capacity <= 16*1024**2:
                    raise ValueError("Image native count differs from configured geometry; not read")
                metadata = pv.get_with_metadata(with_ctrlvars=True, use_monitor=False,
                                                 timeout=max(0.001, turn_end - time.monotonic()))
                frame["received_at"] = datetime.now(timezone.utc).isoformat()
                if not isinstance(metadata, dict) or metadata.get("value") is None:
                    raise ValueError("Image read returned no value/metadata")
                array = np.asarray(metadata["value"])
                array_digest(array)  # Reject objects, unsupported dtypes and unbounded data.
                if array.size != expected_count:
                    raise ValueError("Image array size/count differs from configured geometry")
                frame.update(array=np.array(array, copy=True), source_timestamp=metadata.get("timestamp"),
                             alarm_status=metadata.get("status"), alarm_severity=metadata.get("severity"),
                             native_count=int(metadata.get("count", array.size)), pv_capacity=int(capacity),
                             returned_count=int(array.size), quality="ok")
                if metadata.get("severity") is None or metadata.get("status") is None:
                    frame.update(quality="metadata_missing", detail="Image alarm metadata missing")
                elif int(metadata["severity"]) > 0:
                    frame.update(quality="alarm", detail="Image PV alarm; original pixels retained")
            except Exception as exc:
                frame.update(quality="unavailable", detail=str(exc))
            finally:
                if pv is not None:
                    for action in ("clear_callbacks", "disconnect"):
                        try:
                            getattr(pv, action)()
                        except Exception:
                            pass
            self.frames.append(frame)


def _image_rms(raw, geometry, background):
    w, h = geometry["shape"]
    image = np.asarray(raw, dtype=float).reshape(h, w)
    if not np.all(np.isfinite(image)):
        raise ValueError("Nonfinite image pixels; RMS unavailable")
    if background is not None:
        image = subtract_background(image, background)
    if geometry.get("flip_y"):
        image = np.flipud(image)
    pixel = float(geometry["pixel_width_mm"])
    roi = geometry.get("default_roi", {"x": 0, "y": 0, "width": w, "height": h})
    result = fit_beam_image(image, extent=(0, (w-1)*pixel, 0, (h-1)*pixel),
        xlim=((roi["x"]-.5)*pixel, (roi["x"]+roi["width"]-.5)*pixel),
        ylim=((roi["y"]-.5)*pixel, (roi["y"]+roi["height"]-.5)*pixel), method="RMS moments")
    return {"status": result.status, "sigx_mm": result.sigx_mm, "sigy_mm": result.sigy_mm,
            "method": "RMS moments", "width_definition": "image_roi_projection_rms", "roi": roi,
            "source_dtype": raw.dtype.str, "processing_dtype": image.dtype.str,
            "numeric_string_conversion": raw.dtype.kind in "SU",
            "background_subtracted": background is not None, "negative_weights_clipped": True,
            "physics_precision_validated": False}


def archive_images(capture, snapshot, output_root, *, limit_gib=10.0, vm_evidence=None, store_root=None):
    from .models import positive
    store = ImageStore(store_root or Path(output_root) / "image_store", positive(limit_gib, "image limit GiB") * 1024**3)
    frames, warnings = [], []
    started, finished = timestamp(snapshot.capture_started_at), timestamp(snapshot.capture_finished_at)
    source_window = (started - capture.request.diagnostic_max_age_s, finished)
    if vm_evidence:
        status = vm_evidence["status"]
        completed = timestamp(status["completed_at"])
        source_window = (completed - float(status["elapsed"]) - 1, min(completed + 1, finished))
    for original in capture.frames:
        row = {key: value for key, value in original.items() if key != "array"}
        raw = original.get("array")
        if raw is not None:
            try:
                row["object"] = store.put(raw)
                source = float(row["source_timestamp"])
                if (not math.isfinite(source) or not source_window[0] <= source <= source_window[1]
                        or not started <= timestamp(row["received_at"]) <= finished):
                    raise ValueError("Image source/receipt time is outside its bound working-point window")
                background = capture.backgrounds.get(row["screen"])
                if background:
                    row["background"] = {key: value for key, value in background.items() if key != "array"}
                    row["background"]["object"] = store.put(background["array"])
                if row["quality"] == "ok":
                    row["derived_rms"] = _image_rms(raw, row["geometry"], background["array"] if background else None)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                row.update(quality="unavailable", detail=str(exc))
        if row["quality"] != "ok" or row.get("derived_rms", {}).get("status") != "valid":
            warnings.append(row["screen"] + ": " + row.get("detail", "Image RMS unavailable"))
        # Nonfinite metadata must never leak into JSON; original pixel bytes remain preserved.
        if not isinstance(row.get("source_timestamp"), (int, float)) or not math.isfinite(row["source_timestamp"]):
            row["source_timestamp"] = None
        frames.append(row)
    try:
        usage = store_usage(store.root)
    except OSError as exc:
        usage = {"used_bytes": 0, "free_bytes": None}
        warnings.append(f"Image storage usage unavailable: {exc}")
    if usage["used_bytes"] >= store.limit_bytes * .8:
        warnings.append("Image store is at or above 80% of its limit; no automatic deletion")
    return {"schema": "digital_shadow_images_v1", "snapshot_id": snapshot.snapshot_id,
            "snapshot_hash": _digest(snapshot_to_dict(snapshot)), "machine": snapshot.machine_id,
            "backend": snapshot.backend, "store_root": str(store.root), "frames": frames,
            "processing": "Native pixels retained; profile flip/ROI, optional background subtraction, RMS moments v1",
            "same_bunch_confirmed": False, "summary": {"requested": len(capture.plan),
            "saved": sum("object" in row for row in frames), "warnings": warnings,
            "status": "partial" if warnings else "complete", "limit_bytes": store.limit_bytes, **usage}}


def verify_image_bundle(context, snapshot, bundle):
    if (bundle.get("schema") != "digital_shadow_images_v1" or bundle.get("snapshot_id") != snapshot.snapshot_id
            or bundle.get("snapshot_hash") != _digest(snapshot_to_dict(snapshot))
            or bundle.get("backend") != snapshot.backend or bundle.get("machine") != snapshot.machine_id):
        raise ValueError("Image index does not belong to this machine snapshot")
    store = ImageStore(bundle["store_root"], bundle["summary"]["limit_bytes"])
    if (not isinstance(bundle.get("frames"), list) or bundle["summary"]["requested"] != len(bundle["frames"])
            or bundle["summary"]["saved"] != sum("object" in frame for frame in bundle["frames"])):
        raise ValueError("Image inventory differs from its summary")
    seen, loaded = set(), set()
    for frame in bundle["frames"]:
        screen = frame["screen"]
        if screen in seen or frame["pv_name"] != resolve_channel(context, screen, "image", snapshot.backend):
            raise ValueError("Duplicate or mismatched image source")
        seen.add(screen)
        for reference in (frame.get("object"), frame.get("background", {}).get("object")):
            if reference and reference["sha256"] not in loaded:
                store.get(reference)
                loaded.add(reference["sha256"])
    return bundle["summary"]
