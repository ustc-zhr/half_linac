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


def _object(object_id, name, group, *, capture_mode="scalar", tags=()):
    return SimpleNamespace(
        id=object_id,
        name=name,
        group=group,
        read_pv=f"TEST:{object_id}",
        unit="a.u.",
        capture_mode=capture_mode,
        tags=list(tags),
    )


def test_select_visible_respects_search_filter_and_type_labels(qt_app):
    dialog = selector_module.PVSelectorDialog(
        knobs=[],
        objects=[
            _object("bpm_x", "BPM X", "bpm"),
            _object("scope", "Scope", "diag", capture_mode="waveform"),
            _object("hc_rb", "HC Readback", "corrector", tags=("knob_readback",)),
        ],
        group_labels={"bpm": "BPM", "diag": "Diagnostics", "corrector": "Corrector"},
    )

    table = dialog._tables["object"]
    assert table.item(0, 4).text() == "Scalar"
    assert table.item(1, 4).text() == "Waveform · Monitor only"
    assert table.item(2, 4).text() == "Derived"

    dialog._search_boxes["object"].setText("scope")
    dialog._select_visible("object")
    assert dialog.selected_object_ids() == ["scope"]
    assert dialog._status_labels["object"].text() == "Visible: 1/3    Selected: 1"

    dialog.close()
