import os

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from jitter_analysis.gui.dialogs import epics_connection_dialog as dialog_module


pytestmark = pytest.mark.skipif(
    dialog_module.QtWidgets is None,
    reason="PyQt5 is required for EPICS connection dialog tests",
)


@pytest.fixture(scope="module")
def qt_app():
    app = dialog_module.QtWidgets.QApplication.instance()
    return app or dialog_module.QtWidgets.QApplication([])


def test_connection_results_show_endpoints_and_copy_unique_pv_names(qt_app):
    failures = [
        dialog_module.ConnectionFailure("Read", "BPM X", "TEST:BPM:X", "BPM"),
        dialog_module.ConnectionFailure(
            "Control Readback", "HC01", "TEST:HC01:RB", "Corrector"
        ),
        dialog_module.ConnectionFailure(
            "Read", "HC01 mirror", "TEST:HC01:RB", "Corrector"
        ),
    ]
    dialog = dialog_module.EpicsConnectionResultsDialog(
        connected=5,
        total=8,
        failures=failures,
    )

    assert dialog.summary_label.text() == "5/8 endpoint(s) connected. 3 disconnected."
    assert dialog.table.rowCount() == 3
    assert dialog.table.item(0, 0).text() == "Read"
    assert dialog.table.item(0, 1).text() == "BPM X"
    assert dialog.table.item(0, 2).text() == "TEST:BPM:X"
    assert dialog.table.item(0, 3).text() == "BPM"

    dialog.copy_button.click()

    assert qt_app.clipboard().text() == "TEST:BPM:X\nTEST:HC01:RB"
    assert dialog.copy_status_label.text() == "Copied 2 unique failed PV name(s)."

    dialog.close()
