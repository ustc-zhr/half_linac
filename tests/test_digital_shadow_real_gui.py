import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

from PyQt5.QtWidgets import QApplication

from half_linac.src.apps.digital_shadow.main import DigitalShadowWindow


def test_real_demo_is_offline_and_synthetic_beam_cannot_trigger_live_capture(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No PV access during form operations")
    for name in ("PV", "caget", "caput", "caget_many", "caput_many"):
        monkeypatch.setattr("epics." + name, forbidden)
    app = QApplication.instance() or QApplication([])
    window = DigitalShadowWindow(output_root=tmp_path / "runs")
    try:
        assert window.mode.currentData() == "replay"
        window.mode.setCurrentIndex(window.mode.findData("demo-real"))
        window.example_button.click()
        assert "SYNTHETIC" in window.form.request().beam_provenance
        assert "NO PV" in window.context_label.text()
        assert len(window.form.request().diagnostics) == 8
        assert not window.controller.busy and not (tmp_path / "runs").exists()
        window.mode.setCurrentIndex(window.mode.findData("run-real"))
        window.run_button.click()
        assert "Replace the synthetic demo" in window.status_label.text()
        assert not window.controller.busy and not (tmp_path / "runs").exists()
    finally:
        window.close()
        app.processEvents()


def test_real_screen_choices_do_not_depend_on_vm_scalar_mappings(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Selecting a real model segment must remain offline")
    monkeypatch.setattr("epics.PV", forbidden)
    app = QApplication.instance() or QApplication([])
    window = DigitalShadowWindow(output_root=tmp_path / "runs")
    try:
        window.form.line.setCurrentText("EMIT_QL09_PRF03")
        assert window.form.diagnostics.rowCount() == 0  # PRF03 has no VM size mapping.
        window.mode.setCurrentIndex(window.mode.findData("demo-real"))
        keys = {window.form.diagnostics.item(i, 1).text() for i in range(window.form.diagnostics.rowCount())}
        assert keys == {"PRF03/sigx", "PRF03/sigy"}
        assert not window.controller.busy
    finally:
        window.close()
        app.processEvents()
