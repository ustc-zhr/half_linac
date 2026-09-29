from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-beam-monitor-layout-tests")

REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
BEAM_MONITOR_DIR = REPO_ROOT / "src/apps/beam_monitor"
for path in (PARENT, BEAM_MONITOR_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QLabel, QWidget

# The legacy app uses local absolute imports. Keep those helper module names from
# resolving to another app's cached modules or leaking into later GUI tests.
for _module_name in ("gui", "mplwidget", "profile_runtime"):
    sys.modules.pop(_module_name, None)

from half_linac.src.apps.beam_monitor import main as beam_monitor_main

for _module_name in ("gui", "mplwidget", "profile_runtime"):
    sys.modules.pop(_module_name, None)


class _FakePV:
    def __init__(self, name, **_kwargs):
        self.pvname = name

    def add_callback(self, _callback):
        pass

    def get(self, **_kwargs):
        return None


class BeamMonitorLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_method_result_and_collapsible_settings_are_separated(self):
        with patch.dict(
            os.environ,
            {"HALFLINAC_MACHINE": "half", "HALFLINAC_CONTROL_BACKEND": "vm"},
        ), patch.object(beam_monitor_main.myWindow, "plot_beamprofile"), patch.object(
            beam_monitor_main, "PV", _FakePV
        ), patch.object(beam_monitor_main, "caget", return_value=None):
            window = beam_monitor_main.myWindow()

        try:
            window.timer.stop()
            window.resize(1120, 920)
            window.show()
            self.app.processEvents()

            self.assertTrue(window.settings_content.isAncestorOf(window.profile_method_combo))
            self.assertEqual(window.fit_result_card.objectName(), "resultCard")
            self.assertTrue(window.fit_result_card.isVisible())
            self.assertEqual(
                len(window.fit_result_card.findChildren(QWidget, "resultSection")),
                4,
            )
            self.assertFalse(
                any(
                    label.text() == "Fit Result"
                    for label in window.fit_result_card.findChildren(QLabel)
                )
            )
            self.assertTrue(
                window.fit_result_card.isAncestorOf(window.settings_toggle_button)
            )
            self.assertEqual(window.settings_toggle_button.text(), "Settings")
            self.assertTrue(window.settings_content.isHidden())
            self.assertTrue(window.controls_card.isHidden())

            window._show_display_settings_dialog()
            self.assertFalse(window.show_colorbar_checkbox.isChecked())
            window.show_colorbar_checkbox.setChecked(True)
            self.assertTrue(window.show_colorbar_enabled)
            window.display_settings_dialog.hide()

            window.settings_toggle_button.click()
            self.app.processEvents()
            self.assertTrue(window.settings_content.isVisible())
            self.assertTrue(window.controls_card.isVisible())
            self.assertEqual(window.settings_toggle_button.arrowType(), Qt.DownArrow)

            window.settings_toggle_button.click()
            self.app.processEvents()
            self.assertTrue(window.settings_content.isHidden())
            self.assertTrue(window.controls_card.isHidden())
            self.assertEqual(window.settings_toggle_button.arrowType(), Qt.RightArrow)
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
