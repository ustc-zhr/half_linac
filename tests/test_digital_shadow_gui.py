"""Offscreen operator checks; no PV connections or Elegant processes."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import replace
import importlib.util
import json
import subprocess
from pathlib import Path
import sys

import pytest
from PyQt5.QtWidgets import QApplication

from half_linac.src.apps.digital_shadow.forms import moments_to_twiss, twiss_to_moments
from half_linac.src.apps.digital_shadow.main import DigitalShadowWindow
from half_linac.src.shared.digital_shadow.example import write_example
from half_linac.src.shared.digital_shadow.models import Diagnostic, ShadowRequest
from half_linac.src.shared.runtime_state import read_runtime_state, write_runtime_state


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(tmp_path, monkeypatch, app):
    def forbidden(*args, **kwargs):
        raise AssertionError("Form edits and viewing must not access PVs")
    for name in ("PV", "caget", "caput", "caget_many", "caput_many"):
        monkeypatch.setattr("epics." + name, forbidden)
    monkeypatch.setenv("HALF_LINAC_CONTROL_BACKEND", "real")
    window = DigitalShadowWindow(output_root=tmp_path / "runs")
    yield window
    window.controller.shutdown_now()
    window.close()
    app.processEvents()


def test_startup_is_offline_even_when_launcher_is_real(window, tmp_path):
    assert window.mode.currentData() == "replay"
    assert window.context.control_backend.name == "vm"
    assert not window.controller.busy
    assert not window.stop_button.isEnabled()
    assert not (tmp_path / "runs").exists()
    assert window.results.rowCount() == 0


def test_geometric_units_and_twiss_round_trip():
    moments = twiss_to_moments(39, 0.73, 10)
    assert moments[0] == pytest.approx(39e-5)
    assert moments[1] == pytest.approx(-0.73e-5)
    assert moments[2] == pytest.approx(1e-5 * (1 + 0.73**2) / 39)
    assert moments_to_twiss(moments) == pytest.approx((39, 0.73, 10))


@pytest.mark.parametrize("values", [(0, 1, 1), (1, 1, 0), (1, float("inf"), 1), (1, 0, -1)])
def test_invalid_beam_rejected(values):
    with pytest.raises(ValueError):
        twiss_to_moments(*values)


def test_request_round_trip_preserves_precision_units_and_advanced_fields(window, tmp_path):
    directory = write_example(tmp_path / "example", vm=True)
    request = ShadowRequest.from_dict(read_runtime_state(directory / "request.json"))
    request = replace(request, x_moments=(4.123456789123457e-6, 1.654321987654321e-8, 2.456789123456789e-8),
        diagnostics=(Diagnostic("PRF06", "sigx", "m", "rms"), Diagnostic("PRF06", "sigy", "mm", "rms")),
        fitted_diagnostics=("PRF07/sigx", "PRF07/sigy"), max_capture_duration_s=3.125,
        diagnostic_max_age_s=1.75, model_timeout_s=9.5)
    window.form.load_request(request)
    assert window.form.request().as_metadata() == request.as_metadata()
    window.form.beam_fields["x"][0].setText("50")
    changed = window.form.request()
    assert changed.x_moments != request.x_moments
    assert changed.y_moments == request.y_moments
    assert changed.fitted_diagnostics == request.fitted_diagnostics


def test_example_is_synthetic_and_no_task_starts(window):
    window.load_example()
    request = window.form.request()
    assert request.beam_basis == "assumed"
    assert "SYNTHETIC" in request.beam_provenance
    assert "SYNTHETIC" in window.status_label.text()
    assert not window.controller.busy
    assert read_runtime_state(window.snapshot_edit.text())["machine"]["control_backend"] == "vm"


def test_real_snapshot_is_view_only_in_window_and_controller(window, tmp_path):
    directory = write_example(tmp_path / "real")
    window.load_example()
    window.snapshot_edit.setText(str(directory / "machine_snapshot.json"))
    window.run_task()
    assert "view-only" in window.status_label.text()
    assert not window.controller.busy
    with pytest.raises(ValueError, match="view-only"):
        window.controller.start(window.form.request(), tmp_path / "rejected", mode="replay",
                                snapshot_path=directory / "machine_snapshot.json")
    assert not (tmp_path / "rejected").exists()
    archive = window.show_archive(directory)
    assert archive["backend"] == "real"
    assert "view only" in window.result_banner.text()


def test_fitted_diagnostic_cannot_be_validation(window):
    window.load_example()
    window.form.fitted.setText("PRF06/sigx")
    window.run_task()
    assert "used to fit" in window.status_label.text()
    assert not window.controller.busy


def test_reference_and_diagnostics_come_from_model(window):
    window.load_example()
    assert [window.form.reference.itemText(i) for i in range(window.form.reference.count())] == ["QT02", "DMTC1", "PRF06"]
    assert [window.form.diagnostics.item(i, 1).text() for i in range(window.form.diagnostics.rowCount())] == ["PRF06/sigx", "PRF06/sigy"]
    window.form.reference.setCurrentText("PRF06")
    assert window.form.diagnostics.rowCount() == 2


def test_input_changes_do_not_start_task(window):
    window.load_example()
    window.mode.setCurrentIndex(1)
    assert window.run_button.text() == "Capture VM & predict"
    assert not window.controller.busy


def test_partial_comparison_leaves_residual_empty(window, tmp_path):
    directory = write_example(tmp_path / "example", vm=True)
    row = {"key": "PRF06/sigx", "predicted": 1.2, "measured": None, "residual": None,
           "unit": "mm", "status": "unavailable", "detail": "Diagnostic timestamp lies outside the capture age window"}
    write_runtime_state(directory / "manifest.json", {"schema": "digital_shadow_run_v1", "status": "partial", "input_hash": "fixture"})
    write_runtime_state(directory / "result.json", {"input_hash": "fixture", "status": "partial", "comparisons": [row]})
    window.show_archive(directory)
    assert window.results.item(0, 3).text() == ""
    assert "timestamp" in window.results.item(0, 6).text()
    assert "accuracy certificate" in window.result_banner.text()


def test_bad_archive_is_reported(window, tmp_path):
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "manifest.json").write_text("{")
    window._open_saved(bad)
    assert window.current_archive is None
    assert "Error" in window.status_label.text()


def test_failed_job_without_snapshot_is_visible(window):
    root = Path(window.output_edit.text()) / "job_test"
    root.mkdir(parents=True)
    write_runtime_state(root / "job.json", {"schema": "digital_shadow_gui_job_v1", "status": "failed", "backend": "vm", "error": "VM is not Ready", "run_dir": None})
    window.refresh_history()
    assert window.history.count() == 1
    window.history.setCurrentRow(0)
    assert "VM is not Ready" in window.history_details.toPlainText()


@pytest.mark.parametrize("name,payload", [("job.json", []), ("manifest.json", []),
    ("manifest.json", {"schema": "digital_shadow_run_v1", "artifacts": []})])
def test_malformed_archive_objects_are_reported(window, tmp_path, name, payload):
    directory = tmp_path / "malformed"
    directory.mkdir()
    (directory / name).write_text(json.dumps(payload))
    window._open_saved(directory)
    assert "Error" in window.status_label.text()
    assert window.current_archive is None


def test_worker_refuses_real_before_cli_import(tmp_path):
    root = Path(__file__).resolve().parents[1]
    source = write_example(tmp_path / "real")
    job = tmp_path / "job"
    job.mkdir()
    completed = subprocess.run([sys.executable, "-B", str(root / "src/apps/digital_shadow/worker.py"), str(job),
        "replay", "--snapshot", str(source / "machine_snapshot.json")], capture_output=True, text=True, timeout=5)
    assert completed.returncode == 1 and "view-only" in completed.stderr
    assert not list(job.glob("shadow_*"))


def test_launcher_half_readonly_entry_and_environment(app, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / "src/apps/launcher"))
    monkeypatch.delitem(sys.modules, "gui", raising=False)
    monkeypatch.setenv("HALF_LINAC_MACHINE_ID", "half")
    monkeypatch.setenv("HALF_LINAC_CONTROL_BACKEND", "real")
    monkeypatch.setenv("HALF_MACHINE_ID", "half")
    monkeypatch.setenv("HALF_CONTROL_BACKEND", "real")
    monkeypatch.setattr("half_linac.src.shared.process_runtime.ManagedProcessGroup.install_signal_handlers", lambda self: None)
    spec = importlib.util.spec_from_file_location("shadow_test_launcher", root / "src/apps/launcher/main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    window = module.myWindow()
    try:
        entry = module.APP_DEFINITIONS["digital_shadow"]
        assert entry["category"] == "simulation" and entry["access"] == "read"
        assert window.managed_buttons["digital_shadow"].isEnabled()
        calls = []
        monkeypatch.setattr(window.process_manager, "start_process", lambda **kwargs: calls.append(kwargs))
        window._launch_app("digital_shadow")
        assert calls[0]["cwd"] == str(root / "src/apps/digital_shadow")
        assert calls[0]["env"]["HALF_LINAC_CONTROL_BACKEND"] == "real"
        window._apply_runtime_selection("irfel", "vm")
        assert not window.managed_buttons["digital_shadow"].isEnabled()
    finally:
        window.close()
        app.processEvents()
