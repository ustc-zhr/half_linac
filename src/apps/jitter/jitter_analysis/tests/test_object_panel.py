import os
from types import SimpleNamespace

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from jitter_analysis.gui.widgets import object_panel as object_panel_module


pytestmark = pytest.mark.skipif(
    object_panel_module.QtWidgets is None,
    reason="PyQt5 is required for ObjectPanel tests",
)


@pytest.fixture(scope="module")
def qt_app():
    app = object_panel_module.QtWidgets.QApplication.instance()
    return app or object_panel_module.QtWidgets.QApplication([])


def test_object_panel_uses_compact_empty_state(qt_app):
    panel = object_panel_module.ObjectPanel()

    assert panel.loaded_summary_label.text() == "Library: Not loaded"
    assert panel.selection_summary_label.text() == "Selected: None"
    assert panel.selection_detail_label.isHidden()


def test_object_panel_shows_counts_and_selected_names(qt_app):
    panel = object_panel_module.ObjectPanel()
    objects = [
        SimpleNamespace(name="BPM X", group="bpm"),
        SimpleNamespace(name="BPM Y", group="bpm"),
    ]
    knobs = [SimpleNamespace(name="Corrector H")]

    panel.set_library_objects(objects, {"bpm": "BPM"})
    panel.set_selected_objects(objects)
    panel.set_selected_knobs(knobs)

    assert panel.loaded_summary_label.text() == "Library: 2 read PVs | 1 group"
    assert panel.selection_summary_label.text() == "Selected: 2 read | 1 control"
    assert panel.selection_detail_label.text() == "Read: BPM X, BPM Y\nControl: Corrector H"
    assert not panel.selection_detail_label.isHidden()
