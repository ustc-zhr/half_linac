"""Image comparison provenance, limits and replay; no native PV connections."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from half_linac.src.shared.digital_shadow.example import write_example
from half_linac.src.shared.digital_shadow.image_capture import ImageCapture, archive_images
from half_linac.src.shared.digital_shadow.image_comparison import compare_image_diagnostics
from half_linac.src.shared.digital_shadow.models import ShadowRequest
from half_linac.src.shared.digital_shadow.pipeline import prepare_model
from half_linac.src.shared.digital_shadow.session import digest, run_once
from half_linac.src.shared.machine_profile.loader import load_model_context
from half_linac.src.shared.machine_state import load_snapshot, snapshot_to_dict
from half_linac.src.virtual_machine.workbench_data import input_version


@pytest.fixture
def data(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No PV access during image comparison")
    for name in ("PV", "caget", "caput", "get_pv"):
        monkeypatch.setattr("epics."+name, forbidden)
    directory = write_example(tmp_path/"inputs", vm=True)
    context = load_model_context("half")
    request = ShadowRequest.from_dict(json.loads((directory/"request.json").read_text()))
    snapshot = load_snapshot(directory/"machine_snapshot.json")
    return SimpleNamespace(context=context, request=request, snapshot=snapshot)


def bundle(data, root, *, crop=False, offset=0, background=False, sigma=12):
    images = ImageCapture(data.context, data.request)
    screen, pv, geometry = images.plan[0]
    geometry = deepcopy(geometry)
    w, h = geometry["shape"]
    if crop:
        geometry["default_roi"] = {"x": 0, "y": 0, "width": w//2, "height": h}
    x, y = np.arange(w)[None, :], np.arange(h)[:, None]
    raw = (10000*np.exp(-.5*(((x-w/2)/sigma)**2+((y-h/2)/sigma)**2))).astype(np.uint16) + offset
    original_time = data.snapshot.capture_finished_at
    if background:
        images.backgrounds[screen] = {"array": np.full((h, w), offset, dtype=np.uint16),
                                       "provenance": "Synthetic unit-test background, native orientation"}
    images.plan = [(screen, pv, geometry)]
    images.frames = [{"screen": screen, "pv_name": pv, "geometry": geometry, "array": raw.ravel(),
        "quality": "ok", "reader_kind": "injected_reader", "synthetic": False,
        "source_timestamp": datetime.fromisoformat(original_time).timestamp(), "received_at": original_time,
        "alarm_status": 0, "alarm_severity": 0, "returned_count": w*h, "native_count": w*h}]
    snapshot = replace(data.snapshot, snapshot_id="vm_capture_unit_fixture", consistency="vm_result_matched")
    state, _ = prepare_model(data.context, data.request)
    version = input_version(state)
    evidence = {"schema": "digital_shadow_vm_capture_v1", "snapshot_hash": digest(snapshot_to_dict(snapshot)),
        "input_state": state, "screens": {screen: {"name": screen, "sx": 1.6, "sy": 1.6}},
        "status": {"phase": "Ready", "session": "unit_fixture", "calculation": 1, "input_version": version,
                   "result_version": version, "completed_at": original_time, "elapsed": .1,
                   "publication": {"rms": True, "screens": True}}}
    saved = archive_images(images, snapshot, root, vm_evidence=evidence)
    data.snapshot = snapshot
    return saved, evidence


def predictions(saved):
    values = saved["frames"][0]["derived_rms"]
    return {"PRF06/sigx": values["sigx_mm"]*.001, "PRF06/sigy": values["sigy_mm"]*.001}


def compare(data, saved, evidence=None, model=None):
    return compare_image_diagnostics(data.context, data.snapshot, data.request, model or predictions(saved), saved,
                                     vm_evidence=evidence)


def test_verified_vm_rows_keep_image_identity_and_pixel_metrics(data, tmp_path):
    saved, evidence = bundle(data, tmp_path)
    result = compare(data, saved, evidence)
    assert result["status"] == "complete" and result["counts"]["compared"] == 2
    assert result["vm_working_point"]["image_publication"]
    for row in result["comparisons"]:
        assert "/image_rms_" in row["key"] and row["source"]["pv_name"].endswith(":vm")
        assert row["residual"] == pytest.approx(0) and not row["physics_precision_validated"]
        assert row["metrics"]["roi_signal_fraction"] == 1
        assert row["metrics"]["sigma_pixels"] > 2 and row["analysis_hash"]


@pytest.mark.parametrize("unverified", ["missing_evidence", "no_image_publication", "synthetic_image"])
def test_unverified_images_never_get_numeric_residuals(data, tmp_path, unverified):
    saved, evidence = bundle(data, tmp_path)
    if unverified == "missing_evidence": evidence = None
    elif unverified == "no_image_publication": evidence["status"]["publication"]["screens"] = False
    else: saved["frames"][0]["synthetic"] = True
    rows = compare(data, saved, evidence)["comparisons"]
    assert all(row["status"] == "unverified" and row["measured"] > 0 and row["residual"] is None for row in rows)


def test_roi_clipping_is_quantified_without_accuracy_residual(data, tmp_path):
    saved, evidence = bundle(data, tmp_path, crop=True)
    rows = compare(data, saved, evidence)["comparisons"]
    assert all(row["status"] == "limited" and row["residual"] is None for row in rows)
    assert rows[0]["metrics"]["roi_signal_fraction"] < .6
    assert rows[0]["metrics"]["roi_minus_full_frame_mm"] < 0
    assert "ROI" in rows[0]["detail"]


def test_background_is_recomputed_from_saved_object(data, tmp_path):
    plain, ev1 = bundle(data, tmp_path/"plain", offset=100)
    corrected, ev2 = bundle(data, tmp_path/"corrected", offset=100, background=True)
    first, second = compare(data, plain, ev1), compare(data, corrected, ev2)
    assert first["comparisons"][0]["status"] == "limited"
    row = second["comparisons"][0]
    assert row["status"] == "compared" and row["measured"] < first["comparisons"][0]["measured"]
    assert row["source"]["background_sha256"] and row["processing"]["background_subtracted"]
    assert row["metrics"]["background_removed_signal_fraction"] > 0


def test_underresolved_image_has_no_residual(data, tmp_path):
    saved, evidence = bundle(data, tmp_path, sigma=.5)
    rows = compare(data, saved, evidence)["comparisons"]
    assert all(row["status"] == "limited" and row["residual"] is None for row in rows)
    assert rows[0]["metrics"]["sigma_pixels"] < 2


@pytest.mark.parametrize("fault", ["alarm", "old", "receipt", "roi", "derived_value", "processing", "definition", "method", "background_flag"])
def test_bad_metadata_and_edited_derived_values_are_unavailable(data, tmp_path, fault):
    saved, evidence = bundle(data, tmp_path)
    frame = saved["frames"][0]
    if fault == "alarm": frame["alarm_severity"] = 2
    elif fault == "old": frame["source_timestamp"] -= 100
    elif fault == "receipt": frame["received_at"] = "2000-01-01T00:00:00+00:00"
    elif fault == "roi": frame["geometry"]["default_roi"] = {"x": -1, "y": 0, "width": 10, "height": 10}
    elif fault == "derived_value": frame["derived_rms"]["sigx_mm"] += 1
    elif fault == "processing": saved["processing"] = "unknown pipeline"
    elif fault == "definition": frame["derived_rms"]["width_definition"] = "gaussian_sigma"
    elif fault == "method": frame["derived_rms"]["method"] = "Gaussian fit"
    elif fault == "background_flag": frame["derived_rms"]["background_subtracted"] = True
    rows = compare(data, saved, evidence)["comparisons"]
    assert all(row["status"] == "unavailable" and row["residual"] is None and row["detail"] for row in rows)


def test_image_residual_units_follow_the_declared_diagnostic_units(data, tmp_path):
    saved, evidence = bundle(data, tmp_path)
    # VM scalar evidence requires mm. Its verification is independent of the
    # image display conversion, so exercise this path without claiming a match.
    data.request = replace(data.request, diagnostics=tuple(replace(d, unit="m") for d in data.request.diagnostics))
    rows = compare(data, saved)["comparisons"]
    assert rows[0]["unit"] == "m" and rows[0]["measured"] == pytest.approx(predictions(saved)["PRF06/sigx"])
    assert rows[0]["status"] == "unverified"


def test_session_archive_and_gui_preserve_scalar_rows_and_replay(data, tmp_path, monkeypatch):
    from PyQt5.QtWidgets import QApplication
    from half_linac.src.apps.digital_shadow.jobs import read_archive
    from half_linac.src.apps.digital_shadow.main import DigitalShadowWindow
    saved, evidence = bundle(data, tmp_path)
    monkeypatch.setattr("half_linac.src.shared.digital_shadow.pipeline.build_model_backend",
                        lambda *args, **kwargs: SimpleNamespace(get_map=lambda *a, **kw: np.eye(6)))
    first = run_once(data.context, data.snapshot, data.request, tmp_path/"first", vm_evidence=evidence, image_bundle=saved)
    second = run_once(data.context, data.snapshot, data.request, tmp_path/"replay", vm_evidence=evidence, image_bundle=saved)
    assert first["input_hash"] == second["input_hash"]
    assert first["image_comparisons"] == second["image_comparisons"]
    assert all(row["measured"] == 1.6 for row in first["comparisons"])
    assert not list((tmp_path/"replay").rglob("*.npz"))
    manifest = json.loads((Path(first["run_dir"])/"manifest.json").read_text())
    assert "beam_diagnostics/image_fit.py" in manifest["implementation"]
    assert "image_comparisons.json" in manifest["artifacts"]
    app = QApplication.instance() or QApplication([])
    window = DigitalShadowWindow(output_root=tmp_path/"gui")
    try:
        window.show_archive(first["run_dir"])
        assert window.comparison_source.isEnabled() and window.results.item(0, 2).text() == "1.6"
        window.comparison_source.setCurrentIndex(1)
        assert "image_rms" in window.results.item(0, 0).text()
        assert window.results.item(0, 3).text()
        assert "Image ROI RMS" in window.figure.axes[0].get_legend_handles_labels()[1]
    finally:
        window.close()
        app.processEvents()
    artifact = Path(first["run_dir"])/"image_comparisons.json"
    artifact.write_text('{}')
    with pytest.raises(ValueError, match="integrity"):
        read_archive(first["run_dir"])


def test_image_selector_opens_for_a_result_without_images_and_explains_how_to_proceed(data, tmp_path):
    from PyQt5.QtCore import Qt
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QApplication
    from half_linac.src.apps.digital_shadow.main import DigitalShadowWindow
    directory = tmp_path/"inputs"
    (directory/"manifest.json").write_text(json.dumps({"schema": "digital_shadow_run_v1", "status": "complete", "input_hash": "fixture"}))
    (directory/"result.json").write_text(json.dumps({"status": "complete", "input_hash": "fixture", "comparisons": []}))
    app = QApplication.instance() or QApplication([])
    window = DigitalShadowWindow(output_root=tmp_path/"gui")
    try:
        window.show()
        window.show_archive(directory)
        app.processEvents()
        assert window.comparison_source.isEnabled()
        QTest.mouseClick(window.comparison_source, Qt.LeftButton)
        app.processEvents()
        assert window.comparison_source.view().isVisible()
        window.comparison_source.hidePopup()
        window.comparison_source.setCurrentIndex(1)
        assert window.comparison_hint.isVisible()
        assert "no saved images" in window.comparison_hint.text()
        assert "cannot add missing images" in window.comparison_hint.text()
        assert window.results.rowCount() == 0 and not window.controller.busy
        window.comparison_source.setCurrentIndex(0)
        assert not window.comparison_hint.isVisible()
        window.controller.process = object()
        window._job_state("Running")
        assert not window.comparison_source.isEnabled()
        window.controller.process = None
        window._job_state("Failed")
        assert window.comparison_source.isEnabled()
    finally:
        window.close()
        app.processEvents()
