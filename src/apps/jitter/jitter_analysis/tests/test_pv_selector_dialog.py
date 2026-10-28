import os
from types import SimpleNamespace

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from jitter_analysis.gui.dialogs import pv_selector_dialog as selector_module


pytestmark = pytest.mark.skipif(
    selector_module.QtWidgets is None,
    reason="PyQt5 is required for PVSelectorDialog tests",
)


@pytest.fixture(scope="module")
def qt_app():
    app = selector_module.QtWidgets.QApplication.instance()
    return app or selector_module.QtWidgets.QApplication([])


def _object(
    object_id,
    name,
    group,
    *,
    capture_mode="scalar",
    waveform_sample_interval_sec=None,
    tags=(),
):
    return SimpleNamespace(
        id=object_id,
        name=name,
        group=group,
        read_pv=f"TEST:{object_id}",
        unit="a.u.",
        capture_mode=capture_mode,
        waveform_sample_interval_sec=waveform_sample_interval_sec,
        tags=list(tags),
    )


def test_select_visible_respects_search_filter_and_type_labels(qt_app):
    dialog = selector_module.PVSelectorDialog(
        knobs=[],
        objects=[
            _object("bpm_x", "BPM X", "bpm"),
            _object(
                "scope",
                "Scope",
                "diag",
                capture_mode="waveform",
                waveform_sample_interval_sec=2.5e-9,
            ),
            _object("hc_rb", "HC Readback", "corrector", tags=("knob_readback",)),
        ],
        group_labels={"bpm": "BPM", "diag": "Diagnostics", "corrector": "Corrector"},
    )

    scalar_table = dialog._tables["scalar"]
    waveform_table = dialog._tables["waveform"]
    assert scalar_table.rowCount() == 2
    assert waveform_table.rowCount() == 1
    assert scalar_table.item(0, 4).text() == "Scalar"
    assert scalar_table.item(1, 4).text() == "Derived"
    assert waveform_table.item(0, 4).text() == "2.5 ns"

    dialog._select_visible("waveform")
    dialog._search_boxes["scalar"].setText("bpm")
    dialog._select_visible("scalar")
    assert dialog.selected_object_ids() == ["bpm_x", "scope"]
    assert dialog._status_labels["scalar"].text() == "Visible: 1/2    Selected: 1"
    assert dialog._status_labels["waveform"].text() == "Visible: 1/1    Selected: 1"

    dialog.close()


def test_clear_visible_preserves_hidden_selection(qt_app):
    dialog = selector_module.PVSelectorDialog(
        knobs=[],
        objects=[
            _object("bpm_x", "BPM X", "bpm"),
            _object(
                "scope",
                "Scope",
                "diag",
                capture_mode="waveform",
                waveform_sample_interval_sec=2.5e-9,
            ),
            _object("hc_rb", "HC Readback", "corrector", tags=("knob_readback",)),
        ],
        current_object_ids={"bpm_x", "scope", "hc_rb"},
    )

    dialog._search_boxes["waveform"].setText("scope")
    dialog._clear_visible_buttons["waveform"].click()

    assert dialog.selected_object_ids() == ["bpm_x", "hc_rb"]
    assert dialog._status_labels["scalar"].text() == "Visible: 2/2    Selected: 2"
    assert dialog._status_labels["waveform"].text() == "Visible: 1/1    Selected: 0"

    dialog.close()
