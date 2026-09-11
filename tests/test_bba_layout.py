from __future__ import annotations

import os
import json
import tempfile
import numpy as np
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

from half_linac.src.apps.bba.main import myWindow, bba1_saved_k1_sign, BBAScanThread


class BbaK1ConventionTests(unittest.TestCase):
    def test_saved_conventions_and_missing_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bba1_quad_scan.txt"
            path.write_text("# corrector_setpoint quad_k1 bpm1_m bpm2_m\n")
            with self.assertRaises(RuntimeError):
                bba1_saved_k1_sign(path)
            metadata = path.parent / "metadata.json"
            for plane, expected in (("X", 1), ("Y", -1)):
                metadata.write_text(json.dumps({"family": "bba1", "plane": plane}))
                self.assertEqual(bba1_saved_k1_sign(path), expected)
            metadata.write_text(json.dumps({"family": "bba1", "plane": "Y", "k1_convention": "magnet"}))
            self.assertEqual(bba1_saved_k1_sign(path), 1)
            metadata.unlink()
            path.write_text("# corrector_setpoint quad_k1 bpm1_m bpm2_m k1_convention=magnet\n")
            self.assertEqual(bba1_saved_k1_sign(path), 1)

    def test_legacy_y_conversion_preserves_offset_and_reverses_slope(self):
        class Collector:
            _ordered_unique = staticmethod(BBAScanThread._ordered_unique)

            def _emit(self, data):
                pass

        # Synthetic response vanishes at BPM1 = +0.3 mm.
        points = np.array([
            (corr, k1, bpm1, 2 * (bpm1 - 0.0003) * k1 + 0.001)
            for corr, bpm1 in enumerate((-0.001, 0.0, 0.001))
            for k1 in (0.4, 0.6, 0.8)
        ])
        x, slopes = BBAScanThread._recalculate_from_points(Collector(), points)
        legacy = points.copy()
        legacy[:, 1] *= -1
        old_x, old_slopes = BBAScanThread._recalculate_from_points(Collector(), legacy)
        np.testing.assert_allclose(slopes, -old_slopes)
        for positions, response in ((x, slopes), (old_x, old_slopes)):
            a, b = np.polyfit(positions, response, 1)
            self.assertAlmostEqual(-b / a, 0.0003)


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
