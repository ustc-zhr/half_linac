import os
from types import SimpleNamespace

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from jitter_analysis.gui.dialogs import random_knob_config_dialog as dialog_module


pytestmark = pytest.mark.skipif(
    dialog_module.QtWidgets is None,
    reason="PyQt5 is required for RandomKnobConfigDialog tests",
)


@pytest.fixture(scope="module")
def qt_app():
    app = dialog_module.QtWidgets.QApplication.instance()
    return app or dialog_module.QtWidgets.QApplication([])


def test_random_range_dialog_keeps_only_editable_range_columns(qt_app):
    knob = SimpleNamespace(
        id="k1",
        name="K1",
        group="corrector",
        readback_pv="TEST:K1:RB",
        write_pv="TEST:K1",
        step_hint=0.2,
        limits=SimpleNamespace(low=-2.0, high=2.0),
    )
    dialog = dialog_module.RandomKnobConfigDialog(
        knobs=[knob],
        group_labels={"corrector": "Correctors"},
        current_state={
            "k1": {"enabled": True, "current_text": "0.5", "low_text": "-0.1", "high_text": "0.9"}
        },
    )

    assert dialog.table.columnCount() == 5
    assert [dialog.table.horizontalHeaderItem(index).text() for index in range(5)] == [
        "Use",
        "Control PV",
        "Current",
        "Low",
        "High",
    ]
    assert "Group: Correctors" in dialog.table.item(0, 1).toolTip()
    assert "Use Full Limits" in dialog.use_limits_action.text()
    assert dialog.selected_state()["k1"] == {
        "enabled": True,
        "current_text": "0.5",
        "low_text": "-0.1",
        "high_text": "0.9",
    }
