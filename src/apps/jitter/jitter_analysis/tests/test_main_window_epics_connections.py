import os
from types import SimpleNamespace

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from jitter_analysis.gui import main_window as main_window_module
from jitter_analysis.gui.state import AppState
from jitter_analysis.services.run_service import RunService
from jitter_analysis.services.task_service import TaskService


pytestmark = pytest.mark.skipif(
    main_window_module.QtWidgets is None,
    reason="PyQt5 is required for MainWindow EPICS connection tests",
)


@pytest.fixture(scope="module")
def qt_app():
    app = main_window_module.QtWidgets.QApplication.instance()
    return app or main_window_module.QtWidgets.QApplication([])


@pytest.fixture
def window(qt_app, monkeypatch):
    monkeypatch.setattr(
        main_window_module.MainWindow, "_try_load_default_config", lambda self: None
    )
    result = main_window_module.MainWindow(AppState(), RunService(), TaskService())
    yield result
    result.close()


def test_check_epics_shows_failed_endpoint_details(window, monkeypatch):
    read_object = SimpleNamespace(
        id="bpm_x",
        name="BPM X",
        group="bpm",
        read_pv="TEST:BPM:X",
    )
    knob = SimpleNamespace(
        id="hc01",
        name="HC01",
        group="corrector",
        write_pv="TEST:HC01:SET",
        readback_pv="TEST:HC01:RB",
    )
    window.loaded_config = SimpleNamespace(
        groups=[
            SimpleNamespace(id="bpm", label="BPM"),
            SimpleNamespace(id="corrector", label="Corrector"),
        ],
        objects=[read_object],
        knobs=[knob],
    )
    window.state.selected_object_ids = [read_object.id]
    window.state.selected_knob_ids = [knob.id]
    window.epics_client = SimpleNamespace(
        is_connected=lambda pv_name: pv_name == "TEST:HC01:SET"
    )
    monkeypatch.setattr(window, "_ensure_epics_runtime", lambda: True)

    shown = {}

    class FakeResultsDialog:
        def __init__(self, **kwargs):
            shown.update(kwargs)

        def exec_(self):
            shown["executed"] = True

    monkeypatch.setattr(
        main_window_module, "EpicsConnectionResultsDialog", FakeResultsDialog
    )

    log_messages = []
    monkeypatch.setattr(window, "append_log", log_messages.append)

    window.check_selected_connections()

    assert shown["connected"] == 1
    assert shown["total"] == 3
    assert shown["parent"] is window
    assert shown["executed"] is True
    assert shown["failures"] == [
        main_window_module.ConnectionFailure(
            endpoint_type="Read",
            name="BPM X",
            pv_name="TEST:BPM:X",
            group="BPM",
        ),
        main_window_module.ConnectionFailure(
            endpoint_type="Control Readback",
            name="HC01",
            pv_name="TEST:HC01:RB",
            group="Corrector",
        ),
    ]
    assert "TEST:BPM:X" in log_messages[1]
    assert "TEST:HC01:RB" in log_messages[2]
