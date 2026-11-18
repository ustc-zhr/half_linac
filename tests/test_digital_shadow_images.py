"""Original pixels, bounded capture and reference-only replay; never real PVs."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import time

import numpy as np
import pytest

from half_linac.src.shared.digital_shadow.image_capture import ImageCapture, archive_images, load_backgrounds, verify_image_bundle
from half_linac.src.shared.digital_shadow.image_store import ImageStore, load_array, store_usage
from half_linac.src.shared.digital_shadow.real_capture import capture_real_once
from half_linac.src.shared.digital_shadow.real_demo import demo_request, SimulatedRealReader
from half_linac.src.shared.digital_shadow.session import run_once
from half_linac.src.shared.machine_profile.loader import load_model_context
from half_linac.src.shared.machine_profile.models import ControlBackendConfig
from half_linac.src.shared.machine_profile.resolver import resolve_channel
from half_linac.src.shared.machine_state import snapshot_to_dict


@pytest.fixture
def data(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No native PV access during image archive verification")
    for name in ("PV", "caget", "caput", "get_pv"):
        monkeypatch.setattr("epics."+name, forbidden)
    monkeypatch.setattr("epics.ca.poll", forbidden)
    context = replace(load_model_context("half"), control_backend=ControlBackendConfig("real"))
    request = demo_request()
    request = replace(request, diagnostics=request.diagnostics[:2])
    calls = []
    def backend(*args, **kwargs):
        def get_map(*args, **kwargs):
            calls.append(1)
            return np.eye(6)
        return SimpleNamespace(get_map=get_map)
    monkeypatch.setattr("half_linac.src.shared.digital_shadow.pipeline.build_model_backend", backend)
    return SimpleNamespace(context=context, request=request, calls=calls)


def capture(data, factory=None, backgrounds=None):
    images = ImageCapture(data.context, data.request, backgrounds=backgrounds, synthetic=True)
    reader = factory or SimulatedRealReader(data.context, data.request)
    snapshot, evidence = capture_real_once(data.context, data.request, pv_factory=reader,
        poll_function=lambda _: None, synthetic=True, image_capture=images)
    return images, snapshot, evidence, reader


@pytest.mark.parametrize("dtype", [np.uint16, np.float32, np.float64, "S8"])
def test_lossless_objects_preserve_dtype_and_deduplicate(tmp_path, dtype):
    array = np.arange(4096).astype(dtype).reshape(64, 64)
    store = ImageStore(tmp_path, 1024**2)
    first, second = store.put(array), store.put(array.copy())
    assert first == second and len(list(tmp_path.glob("*.npz"))) == 1
    restored = store.get(first)
    assert restored.dtype == array.dtype and restored.tobytes() == array.tobytes()
    assert first["stored_bytes"] < first["raw_bytes"]
    assert store_usage(tmp_path)["used_bytes"] == first["stored_bytes"]


def test_one_frame_for_two_planes_bound_to_snapshot_and_raw_source(data, tmp_path):
    images, snapshot, evidence, reader = capture(data)
    image_pv = resolve_channel(data.context, "PRF06", "image", "real")
    assert reader.reads.count(image_pv) == 1
    assert len(reader.reads) == len(set(reader.reads))
    assert all(entry.native_count == 1 for entry in snapshot.entries)
    raw_before = snapshot_to_dict(snapshot)
    bundle = archive_images(images, snapshot, tmp_path)
    row = bundle["frames"][0]
    assert row["synthetic"] and row["object"]["dtype"] == np.dtype(np.uint16).str
    assert row["source_timestamp"] is not None and row["native_count"] == 1440*1080
    assert row["derived_rms"]["status"] == "valid"
    assert row["derived_rms"]["width_definition"] == "image_roi_projection_rms"
    assert not row["derived_rms"]["physics_precision_validated"]
    assert bundle["summary"]["saved"] == bundle["summary"]["requested"] == 1
    verify_image_bundle(data.context, snapshot, bundle)
    assert snapshot_to_dict(snapshot) == raw_before
    with pytest.raises(ValueError, match="does not belong"):
        verify_image_bundle(data.context, replace(snapshot, snapshot_id="different"), bundle)
    with pytest.raises(ValueError, match="single-use"):
        images.sample(time.monotonic()+1, pv_factory=reader, poll_function=lambda _: None)


def test_background_reused_and_not_implicitly_applied_to_other_screens(data, tmp_path):
    data.request = demo_request()
    background = np.zeros((1080, 1440), dtype=np.uint16)
    np.save(tmp_path / "background.npy", background)
    manifest = tmp_path / "backgrounds.json"
    manifest.write_text(json.dumps({"PRF06": {"path": "background.npy", "provenance": "SYNTHETIC dark frame"},
                                    "PRF08": {"path": "background.npy", "provenance": "SYNTHETIC shared test fixture"}}))
    images, snapshot, _, _ = capture(data, backgrounds=load_backgrounds(manifest))
    bundle = archive_images(images, snapshot, tmp_path)
    by_screen = {row["screen"]: row for row in bundle["frames"]}
    assert by_screen["PRF06"]["background"]["object"] == by_screen["PRF08"]["background"]["object"]
    assert "background" not in by_screen["PRF07"] and "background" not in by_screen["PRF09"]
    assert by_screen["PRF06"]["derived_rms"]["background_subtracted"]
    assert not by_screen["PRF07"]["derived_rms"]["background_subtracted"]
    assert len(list((tmp_path/"image_store").glob("*.npz"))) == 2  # identical synthetic frames plus one background


def test_expired_budget_does_not_connect_images(data, tmp_path):
    images = ImageCapture(data.context, data.request, synthetic=True)
    def forbidden(*args):
        raise AssertionError("Expired budget must not create a PV")
    images.sample(time.monotonic()-1, pv_factory=forbidden, poll_function=lambda _: None)
    assert all(row["quality"] == "unavailable" and "not read" in row["detail"] for row in images.frames)


@pytest.mark.parametrize("fault", ["alarm", "stale", "count", "nonfinite"])
def test_bad_frames_keep_reasons_and_do_not_become_precision_comparisons(data, tmp_path, fault):
    reader = SimulatedRealReader(data.context, data.request)
    target = resolve_channel(data.context, "PRF06", "image", "real")
    def factory(name):
        pv = reader(name)
        if name != target:
            return pv
        if fault == "count": pv.count = 1
        original = pv.get_with_metadata
        def get(**kwargs):
            item = original(**kwargs)
            if fault == "alarm": item["severity"] = 2
            elif fault == "stale": item["timestamp"] -= 100
            elif fault == "nonfinite": item["value"] = np.full(item["count"], float("nan"))
            return item
        pv.get_with_metadata = get
        return pv
    images, snapshot, evidence, _ = capture(data, factory)
    bundle = archive_images(images, snapshot, tmp_path)
    assert bundle["summary"]["status"] == "partial" and bundle["summary"]["warnings"]
    assert "derived_rms" not in bundle["frames"][0]
    assert bundle["summary"]["saved"] == (0 if fault == "count" else 1)
    result = run_once(data.context, snapshot, data.request, tmp_path, real_evidence=evidence, image_bundle=bundle)
    assert result["prediction_status"] == "complete"
    assert all(row["residual"] is None for row in result["comparisons"])


def test_quota_does_not_delete_existing_objects_or_prevent_scalar_prediction(data, tmp_path):
    pool = tmp_path / "image_store"
    existing = ImageStore(pool, 10**6).put(np.arange(200, dtype=np.uint16))
    before = (pool / (existing["sha256"]+".npz")).read_bytes()
    abandoned = pool / ".image_interrupted.tmp"
    abandoned.write_bytes(b"0" * 600000)
    assert store_usage(pool)["used_bytes"] == existing["stored_bytes"] + 600000
    images, snapshot, evidence, _ = capture(data)
    bundle = archive_images(images, snapshot, tmp_path, limit_gib=10**6/1024**3)
    assert bundle["summary"]["saved"] == 0 and "limit" in " ".join(bundle["summary"]["warnings"])
    assert (pool / (existing["sha256"]+".npz")).read_bytes() == before
    assert list(pool.glob(".image_*.tmp")) == [abandoned]
    result = run_once(data.context, snapshot, data.request, tmp_path, real_evidence=evidence, image_bundle=bundle)
    assert result["prediction_status"] == "complete" and result["status"] == "partial"


def test_replay_reuses_objects_and_corruption_stops_before_model(data, tmp_path):
    from half_linac.src.apps.digital_shadow.jobs import read_archive
    images, snapshot, evidence, _ = capture(data)
    bundle = archive_images(images, snapshot, tmp_path)
    first = run_once(data.context, snapshot, data.request, tmp_path/"first", real_evidence=evidence, image_bundle=bundle)
    archived = read_archive(first["run_dir"])
    second = run_once(data.context, snapshot, data.request, tmp_path/"second", real_evidence=evidence,
                      image_bundle=archived["image_index"])
    assert first["input_hash"] == second["input_hash"]
    assert not list((tmp_path/"second").rglob("*.npz"))
    assert len(list((tmp_path/"image_store").glob("*.npz"))) == 1
    path = next((tmp_path/"image_store").glob("*.npz"))
    path.write_bytes(b"corrupt")
    before = len(data.calls)
    with pytest.raises(RuntimeError, match="checksum"):
        run_once(data.context, snapshot, data.request, tmp_path/"bad", real_evidence=evidence, image_bundle=bundle)
    assert len(data.calls) == before
    with pytest.raises(ValueError, match="checksum"):
        read_archive(first["run_dir"])


def test_gui_image_options_stay_offline_and_optional(tmp_path, monkeypatch):
    from PyQt5.QtWidgets import QApplication
    from half_linac.src.apps.digital_shadow.main import DigitalShadowWindow
    monkeypatch.setattr("epics.PV", lambda *args, **kwargs: pytest.fail("Unexpected PV access"))
    app = QApplication.instance() or QApplication([])
    window = DigitalShadowWindow(output_root=tmp_path/"runs")
    try:
        assert not window.save_images.isChecked() and not window.save_images.isEnabled()
        window.mode.setCurrentIndex(window.mode.findData("demo-real"))
        window.save_images.setChecked(True)
        window.example_button.click()
        calls = []
        monkeypatch.setattr(window.controller, "start", lambda *args, **kwargs: calls.append(kwargs))
        window.run_task()
        assert calls[0]["save_images"] and calls[0]["image_limit_gib"] == 10
        assert not (tmp_path/"runs").exists()
        window.mode.setCurrentIndex(0)
        assert not window.save_images.isEnabled()
    finally:
        window.close()
        app.processEvents()


def test_numeric_roi_rms_uses_calibration_and_background_without_changing_raw_pixels():
    from half_linac.src.shared.digital_shadow.image_capture import _image_rms
    background = np.full((3, 5), 5, dtype=np.uint16)
    raw = background.copy()
    raw[np.ix_([0, 2], [1, 3])] += 10
    before = raw.tobytes()
    result = _image_rms(raw, {"shape": [5, 3], "pixel_width_mm": .2, "flip_y": True}, background)
    assert result["sigx_mm"] == pytest.approx(.2) and result["sigy_mm"] == pytest.approx(.2)
    assert raw.tobytes() == before and raw.dtype == np.uint16


def test_capture_rejects_cross_backend_image_plan_before_any_io(data):
    from half_linac.src.shared.digital_shadow.vm_capture import capture_vm_once
    images = ImageCapture(data.context, data.request, synthetic=True)
    vm_context = replace(data.context, control_backend=ControlBackendConfig("vm"))
    with pytest.raises(ValueError, match="context/request"):
        capture_vm_once(vm_context, data.request, image_capture=images)


def test_bad_background_shape_is_rejected_before_any_capture(data, tmp_path):
    np.save(tmp_path / "wrong.npy", np.zeros((3, 5), dtype=np.uint16))
    source = tmp_path / "backgrounds.json"
    source.write_text(json.dumps({"PRF06": {"path": "wrong.npy", "provenance": "SYNTHETIC wrong shape"}}))
    with pytest.raises(ValueError, match="Background shape"):
        ImageCapture(data.context, data.request, backgrounds=load_backgrounds(source))
