from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-bba-layout-tests")

REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
BBA_DIR = REPO_ROOT / "src/apps/bba"
for path in (PARENT, BBA_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from PyQt5.QtWidgets import QApplication

from half_linac.src.apps.bba.main import myWindow


class BbaLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_bba_pages_keep_left_and_right_columns_equal(self):
        with patch.dict(
            os.environ,
            {"HALFLINAC_MACHINE": "half", "HALFLINAC_CONTROL_BACKEND": "vm"},
        ):
            window = myWindow()

        try:
            window.resize(1600, 960)
            window.show()
            self.app.processEvents()

            page_widgets = (
                (window.tab, window.widget, window.widget_2),
                (window.tab_2, window.widget_3, window.widget_4),
            )
            for tab, left_plot, right_plot in page_widgets:
                window.tabWidget.setCurrentWidget(tab)
                self.app.processEvents()
                left_width = window._plot_wrappers[left_plot].width()
                right_width = window._plot_wrappers[right_plot].width()
                self.assertLessEqual(abs(left_width - right_width), 10)

            self.assertGreaterEqual(window.lineEdit.width(), 72)
            self.assertGreaterEqual(window.lineEdit_14.width(), 72)
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
