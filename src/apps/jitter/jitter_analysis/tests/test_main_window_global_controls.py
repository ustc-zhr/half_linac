import os
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from jitter_analysis.gui import main_window as main_window_module
from jitter_analysis.gui.state import AppState
from jitter_analysis.domain.types import RunStatus
from jitter_analysis.services.run_service import RunService
from jitter_analysis.services.task_service import TaskService


pytestmark = pytest.mark.skipif(
    main_window_module.QtWidgets is None,
    reason="PyQt5 is required for MainWindow tests",
)


@pytest.fixture(scope="module")
def qt_app():
    app = main_window_module.QtWidgets.QApplication.instance()
    return app or main_window_module.QtWidgets.QApplication([])


def test_run_controls_are_persistent_children_of_the_app_header(qt_app, monkeypatch):
    monkeypatch.setattr(main_window_module.MainWindow, "_try_load_default_config", lambda self: None)
    window = main_window_module.MainWindow(
        state=AppState(),
        run_service=RunService(),
        task_service=TaskService(),
    )

    header = window.findChild(main_window_module.QtWidgets.QFrame, "globalStatusBar")
    controls = window.findChild(main_window_module.QtWidgets.QWidget, "globalRunControls")

    assert header is not None
    assert controls is window.global_run_controls
    assert header.isAncestorOf(controls)
    assert controls.isAncestorOf(window.run_check_button)
    assert controls.isAncestorOf(window.run_start_button)
    assert controls.isAncestorOf(window.run_stop_button)
    assert window.status_panel.isAncestorOf(controls) is False
    assert header.height() == 48
    assert window.global_epics_status_label.text() == "●  EPICS  Load PV library"
    assert window.global_mode_status_label.text() == "MODE  Monitor"
    assert window.global_run_status_label.text() == "RUN  Idle"
    assert window.run_check_button.text() == "Check EPICS"
    assert window.run_start_button.text() == "Start Monitor"
    assert window.run_stop_button.text() == "Stop"

    window.scan_panel.set_task_mode("single_knob_scan")
    window._refresh_ui_affordances()
    assert window.global_mode_status_label.text() == "MODE  Single Knob"
    assert window.run_start_button.text() == "Start Single-Knob Scan"

    window.scan_panel.set_task_mode("multi_knob_random")
    window._refresh_ui_affordances()
    assert window.global_mode_status_label.text() == "MODE  Multi-Knob"
    assert window.run_start_button.text() == "Start Multi-Knob Scan"

    window._set_connection_status("Connected (3/3 connected)", tone="success")
    assert window.global_epics_status_label.text() == "●  EPICS  Connected (3/3 connected)"
    assert set(window.status_panel._items) == {"run_id", "sample", "step", "elapsed"}
    assert window.status_panel.run_id_value.text() == "--"
    assert window.status_panel.elapsed_value.text() == "--"

    started_at = datetime(2026, 8, 31, 10, 0, 0)
    window.current_run_metadata = SimpleNamespace(run_id="run-42", created_at=started_at)
    window.state.run_status = RunStatus.RUNNING
    window._initialize_run_status_panel()
    window._set_elapsed_from_timestamp(started_at + timedelta(seconds=125))
    assert window.status_panel.run_id_value.text() == "run-42"
    assert window.status_panel.elapsed_value.text() == "02:05"

    window.close()
