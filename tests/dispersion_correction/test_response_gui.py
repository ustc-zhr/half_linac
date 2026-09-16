import os
from dataclasses import replace

import pytest

from half_linac.src.apps.dispersion_correction.response_store import load_response, save_response


def test_measure_save_reload_and_start_with_saved_response(tmp_path, monkeypatch):
    pytest.importorskip("PyQt5")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication, QComboBox, QDialog, QLabel
    from half_linac.src.apps.dispersion_correction.gui import main_window

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(main_window, "q_response_directory", lambda _context: tmp_path)
    window = main_window.MainWindow(offline_demo=True)
    window.show()
    app.processEvents()
    assert window.measure_q_response_button.isVisibleTo(window)
    assert window.measure_q_response_button.isEnabled()
    assert window.run_button.isEnabled()

    def measure(task):
        assert task == "response"
        worker = main_window.WorkflowWorker(task, window._config_from_widgets(), response_directory=tmp_path)
        worker.response_saved.connect(window._response_saved)
        worker.completed.connect(window._task_completed)
        errors = []
        worker.failed.connect(errors.append)
        worker.run()
        assert errors == []
        return True

    monkeypatch.setattr(window, "_start_task", measure)
    window.measure_q_response_button.click()
    assert "Q response saved" in window.saved_response_status.text()
    assert window.correction_recommendation is None
    assert window.recommendation_dialog.isHidden()
    paths = list(tmp_path.glob("q_response_*.json"))
    assert len(paths) == 1
    record = load_response(paths[0])
    window.close()

    reopened = main_window.MainWindow(offline_demo=True)
    assert reopened.latest_measurement is None
    tasks = []
    monkeypatch.setattr(reopened, "_start_task", lambda task, **kwargs: tasks.append((task, kwargs)))

    def select_saved(dialog):
        source = dialog.findChild(QComboBox, "correctionResponseSource")
        assert source.count() == 2
        source.setCurrentIndex(1)
        assert source.currentData().created_at == record.created_at
        assert "Current settings and dispersion will be read again" in dialog.findChild(QLabel, "correctionResponseDetails").text()
        dialog.accept()
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, "exec_", select_saved)
    reopened._confirm_automatic_correction()
    assert tasks[0][0] == "run"
    assert tasks[0][1]["saved_response"].created_at == record.created_at
    reopened.close()


def test_incompatible_sources_disabled_and_corrupt_files_skipped(tmp_path, monkeypatch):
    pytest.importorskip("PyQt5")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication, QComboBox
    from half_linac.src.apps.dispersion_correction.gui import main_window
    from half_linac.src.apps.dispersion_correction.workflow import AchromatWorkflow

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(main_window, "q_response_directory", lambda _context: tmp_path)
    window = main_window.MainWindow(offline_demo=True)
    records = []
    config = window._config_from_widgets()
    config = replace(config, energy_knob=replace(config.energy_knob, name="other"))
    AchromatWorkflow(config, response_callback=records.append).build_response_matrix()
    save_response(tmp_path, records[0])
    (tmp_path / "q_response_broken.json").write_text("not json")
    dialog, *_ = window._build_automatic_correction_dialog()
    source = dialog.findChild(QComboBox, "correctionResponseSource")
    assert source.count() == 2
    assert source.currentIndex() == 0
    assert not source.model().item(1).isEnabled()
    assert "incompatible" in source.itemText(1)
    dialog.reject()
    window.close()
