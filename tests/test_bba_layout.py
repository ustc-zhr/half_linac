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

from half_linac.src.apps.bba.main import myWindow, bba1_saved_k1_sign, BBAScanThread, ScanParameters


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
            _bpm1_samples_for_fit = BBAScanThread._bpm1_samples_for_fit
            params = ScanParameters()

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

    def test_initial_k1_reference_survives_save_and_recalculation(self):
        params = ScanParameters(
            bba1_bpm1_mode="initial_k1", corr_steps=2, quad_steps=3,
            samples=2, corrPV="corr", quadPV="quad", bpm1PV="bpm1", bpm2PV="bpm2",
        )
        params.corr_target = params.quad_target = None
        state = {"corr": 0.0, "quad": 2.0}
        baseline_reads = []
        scan = BBAScanThread(params)

        def read(pv, label):
            if pv == "bpm1":
                if state["quad"] == 2.0:
                    baseline_reads.append(state["corr"])
                return state["corr"] + 0.1 * (state["quad"] - 2.0)
            return (state["corr"] - 0.3) * state["quad"]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            params.bba1_data_path = root / "m1S.txt"
            params.bba1_quad_scan_path = root / "bba1_quad_scan.txt"
            params.bba1_metadata_path = root / "metadata.json"
            with patch("half_linac.src.apps.bba.main.epics.PV", side_effect=lambda name: name), \
                 patch("half_linac.src.apps.bba.main.resolve_limited_scan_values",
                       side_effect=[np.array([3., 4., 5.]), np.array([0., 1.])]), \
                 patch.object(scan, "_safe_get", side_effect=lambda pv, label: state[pv]), \
                 patch.object(scan, "_safe_put", side_effect=lambda pv, value: state.update({pv: value})), \
                 patch.object(scan, "_sleep_or_stop", return_value=True), \
                 patch.object(scan, "_read_bpm_m", side_effect=read), \
                 patch.object(scan, "_emit"):
                x, slopes = scan._perform_scan()
            np.testing.assert_allclose(x, [0., 1.])
            np.testing.assert_allclose(slopes, [-0.3, 0.7])
            self.assertEqual(baseline_reads, [0., 0., 1., 1.])
            self.assertEqual(state, {"corr": 0.0, "quad": 2.0})
            metadata = json.loads(params.bba1_metadata_path.read_text())
            self.assertEqual(metadata["bpm1_reference"]["initial_k1"], 2.0)

            # The archive controls recalculation even if the UI has since changed mode.
            params.recal = True
            params.bba1_bpm1_mode = "scan_mean"
            recal = BBAScanThread(params)
            with patch.object(recal, "_emit") as emit:
                recal.run()
            result = emit.call_args.args[0]
            self.assertEqual(result["show"], "m1S")
            np.testing.assert_allclose(result["m1"], x)
            self.assertAlmostEqual(result["offset"], 0.3)

            metadata["bpm1_reference"]["measurements"] = []
            params.bba1_metadata_path.write_text(json.dumps(metadata))
            with patch.object(recal, "_emit") as emit:
                recal.run()
            self.assertIn("Missing or invalid", emit.call_args.args[0]["error"])

    def test_initial_k1_reference_matches_legacy_rounded_scan(self):
        scan = BBAScanThread(ScanParameters(bba1_bpm1_mode="initial_k1"))
        kicks = [0.083961473281, 0.183961473281, 0.283961473281]
        scan.bpm1_reference_samples = {
            kick: [position] for kick, position in zip(kicks, [-0.001, 0., 0.001])
        }
        points = np.array([
            (kick, k1, 0.009, (samples[0] - 0.0003) * k1)
            for kick, samples in scan.bpm1_reference_samples.items()
            for k1 in (1., 2., 3.)
        ])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scan.txt"
            archive = Path(directory) / "archive"
            scan._save_array(path, points, archive_dir=archive)
            np.testing.assert_array_equal(np.loadtxt(path), points)
            np.testing.assert_array_equal(np.loadtxt(archive / path.name), points)
            np.savetxt(path, points, fmt="%.6e")
            with patch.object(scan, "_emit"):
                positions, slopes = scan._recalculate_from_points(np.loadtxt(path))
            np.testing.assert_allclose(positions, [-0.001, 0., 0.001])
            self.assertAlmostEqual(-np.polyfit(positions, slopes, 1)[1], 0.0003)

    def test_initial_k1_reference_rejects_missing_invalid_and_ambiguous(self):
        scan = BBAScanThread(ScanParameters(bba1_bpm1_mode="initial_k1"))
        for references in ({}, {0.083961473281: []},
                           {0.083961473281: [float("nan")]},
                           {0.08396148: [0.001]}):
            scan.bpm1_reference_samples = references
            with self.assertRaisesRegex(RuntimeError, "Missing or invalid"):
                scan._bpm1_samples_for_fit(0.08396147, [0.009])
        scan.bpm1_reference_samples = {
            0.083961473281: [0.001], 0.083961473282: [0.002],
        }
        with self.assertRaisesRegex(RuntimeError, "Ambiguous"):
            scan._bpm1_samples_for_fit(0.08396147, [0.009])
        np.testing.assert_array_equal(
            scan._bpm1_samples_for_fit(0.083961473281, [0.009]), [0.001],
        )

    def test_stop_during_reference_settling_restores_magnets(self):
        params = ScanParameters(
            bba1_bpm1_mode="initial_k1", corrPV="corr", quadPV="quad",
            bpm1PV="bpm1", bpm2PV="bpm2",
        )
        params.corr_target = params.quad_target = None
        state = {"corr": 0.0, "quad": 2.0}
        scan = BBAScanThread(params)
        with patch("half_linac.src.apps.bba.main.epics.PV", side_effect=lambda name: name), \
             patch("half_linac.src.apps.bba.main.resolve_limited_scan_values",
                   side_effect=[np.array([3., 4.]), np.array([1.])]), \
             patch.object(scan, "_safe_get", side_effect=lambda pv, label: state[pv]), \
             patch.object(scan, "_safe_put", side_effect=lambda pv, value: state.update({pv: value})), \
             patch.object(scan, "_sleep_or_stop", return_value=False), \
             patch.object(scan, "_read_bpm_m") as read:
            self.assertIsNone(scan._perform_scan())
            read.assert_not_called()
        self.assertEqual(state, {"corr": 0.0, "quad": 2.0})


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
            self.assertEqual(window.bba1_bpm1_mode_combo.currentData(), "scan_mean")
            window.bba1_bpm1_mode_combo.setCurrentIndex(1)
            self.assertEqual(window.get_setting().bba1_bpm1_mode, "initial_k1")
            window.resize(1600, 960)
            window.show()
            self.app.processEvents()

            self.assertLessEqual(
                abs(window.bba1_batch_button.height() - window.bba1_preset_combo.height()),
                2,
            )

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
