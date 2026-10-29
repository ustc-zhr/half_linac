import os

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from jitter_analysis.gui.dialogs import setup_browser_dialog as dialog_module


pytestmark = pytest.mark.skipif(
    dialog_module.QtWidgets is None,
    reason="PyQt5 is required for SetupBrowserDialog tests",
)


@pytest.fixture(scope="module")
def qt_app():
    app = dialog_module.QtWidgets.QApplication.instance()
    return app or dialog_module.QtWidgets.QApplication([])


def test_setup_browser_displays_saved_filename_as_name(qt_app):
    dialog = dialog_module.SetupBrowserDialog(root_dir="/tmp/setups")
    dialog.set_setups(
        [
            {
                "path": "/tmp/setups/orbit_tuning.json",
                "name": "orbit_tuning",
                "saved_at_text": "2026-10-29 09:30:00",
                "task_mode": "Monitor",
                "operator": "operator",
                "object_count": 4,
                "knob_count": 2,
                "save_dir": "runs/orbit",
                "config_path": "configs/half_pvlist.json",
                "notes": "Orbit tuning setup",
            }
        ]
    )

    assert dialog.table.horizontalHeaderItem(0).text() == "Name"
    assert dialog.table.horizontalHeaderItem(1).text() == "Saved"
    assert dialog.table.item(0, 0).text() == "orbit_tuning"
    assert dialog.table.item(0, 1).text() == "2026-10-29 09:30:00"
    assert "Name: orbit_tuning" in dialog.detail_label.text()

    dialog.close()
