import os

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from jitter_analysis.gui.dialogs import pv_library_editor_dialog as editor_module


pytestmark = pytest.mark.skipif(
    editor_module.QtWidgets is None,
    reason="PyQt5 is required for PV editor dialog tests",
)


@pytest.fixture(scope="module")
def qt_app():
    app = editor_module.QtWidgets.QApplication.instance()
    return app or editor_module.QtWidgets.QApplication([])


def test_scalar_value_type_hides_waveform_options(qt_app):
    dialog = editor_module.PVObjectEditorDialog(group_labels={"user": "User PVs"})

    assert dialog.waveform_handling_box.isHidden()
    assert dialog.waveform_interval_edit.isHidden()
    data = dialog.object_data()
    assert data["kind"] == "scalar"
    assert data["capture_mode"] == "scalar"
    assert data["value_reducer"] == "none"
    assert "waveform_sample_interval_sec" not in data


def test_raw_waveform_maps_to_existing_config_fields(qt_app):
    dialog = editor_module.PVObjectEditorDialog(group_labels={"user": "User PVs"})
    dialog.kind_box.setCurrentIndex(dialog.kind_box.findData("waveform"))
    dialog.waveform_handling_box.setCurrentIndex(dialog.waveform_handling_box.findData("raw"))
    dialog.waveform_interval_edit.setText("2.5e-9")

    data = dialog.object_data()
    assert data["kind"] == "waveform"
    assert data["capture_mode"] == "waveform"
    assert data["value_reducer"] == "none"
    assert data["waveform_sample_interval_sec"] == pytest.approx(2.5e-9)


def test_mean_waveform_maps_to_scalar_capture_without_interval(qt_app):
    dialog = editor_module.PVObjectEditorDialog(group_labels={"user": "User PVs"})
    dialog.kind_box.setCurrentIndex(dialog.kind_box.findData("waveform"))
    dialog.waveform_handling_box.setCurrentIndex(dialog.waveform_handling_box.findData("mean"))

    data = dialog.object_data()
    assert data["kind"] == "waveform"
    assert data["capture_mode"] == "scalar"
    assert data["value_reducer"] == "mean"
    assert "waveform_sample_interval_sec" not in data


def test_add_group_dialog_generates_id_and_order(qt_app):
    existing = [
        {"id": "bpm", "label": "BPM", "kind": "object", "color": "#111111", "order": 20}
    ]
    dialog = editor_module.PVGroupEditorDialog(existing_groups=existing)
    dialog.name_edit.setText("User Diagnostics")

    group = dialog.group_data()
    assert group == {
        "id": "user_diagnostics",
        "label": "User Diagnostics",
        "kind": "object",
        "color": "#607d8b",
        "order": 30,
    }


def test_object_editor_add_group_callback_selects_new_group(qt_app):
    created = {
        "id": "user_diagnostics",
        "label": "User Diagnostics",
        "kind": "object",
        "color": "#607d8b",
        "order": 30,
    }
    dialog = editor_module.PVObjectEditorDialog(
        group_labels={"bpm": "BPM"},
        add_group_callback=lambda _parent: created,
    )

    dialog._add_group()

    assert dialog.group_box.isEditable()
    assert dialog.group_box.currentData() == "user_diagnostics"


def test_group_action_controls_fit_without_clipping(qt_app):
    dialog = editor_module.PVObjectEditorDialog(group_labels={"user": "User PVs"})

    assert dialog.group_dropdown_button.arrowType() == editor_module.QtCore.Qt.DownArrow
    assert dialog.group_dropdown_button.size() == editor_module.QtCore.QSize(32, 32)
    assert dialog.add_group_button.size() == editor_module.QtCore.QSize(32, 32)
    assert dialog.group_dropdown_button.parentWidget().height() == 34
