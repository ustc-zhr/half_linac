from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from half_linac.src.apps.energy_spectrum.esa_auto_tuner import (
    ESA_AutoTuner,
    reference_x_pixel,
)


class _DummyPV:
    def get(self):
        return np.zeros((10, 10)).ravel().tolist()


class _FakeAutoTuner(ESA_AutoTuner):
    def __init__(self, progress_callback=None, cancel_requested=None):
        super().__init__(
            flag_pv_obj=_DummyPV(),
            flag_pixel=(10, 10),
            bend_pv="FAKE:BEND",
            progress_callback=progress_callback,
            cancel_requested=cancel_requested,
        )
        self.current = None

    def _set_bend(self, current, *, allow_cancel=True):
        self.current = float(current)

    def _read_bend(self):
        return self.current

    def _get_flag_image(self):
        return np.zeros((10, 10))

    def _detect_beam(self, img):
        if self.current is None or self.current < 3 or self.current > 5:
            return False, 0.0, None
        score = 10.0 - abs(self.current - 4.0)
        return True, score, self.current


class _CenterObjectiveAutoTuner(ESA_AutoTuner):
    def __init__(self):
        super().__init__(
            flag_pv_obj=_DummyPV(),
            flag_pixel=(100, 100),
            bend_pv="FAKE:ENERGY",
            mode="center_x_reference",
            target_x_pixel=59.5,
        )
        self.current = 5.0
        self.visited = []

    def _set_bend(self, current, *, allow_cancel=True):
        self.current = float(current)
        self.visited.append(self.current)

    def _read_bend(self):
        return self.current

    def _get_flag_image(self):
        image = np.zeros((100, 100))
        center_x = int(round(self.current * 10.0))
        if 5 <= center_x <= 94:
            # Brightness peaks away from the calibrated x target on purpose.
            amplitude = 200.0 - 5.0 * abs(self.current - 3.0)
            image[45:55, center_x - 5:center_x + 5] = amplitude
        return image


class ESAAutoTunerTests(unittest.TestCase):
    def test_reference_x_mm_maps_to_calibrated_pixel_coordinate(self):
        self.assertAlmostEqual(reference_x_pixel(0.0, 1440, 0.02), 719.5)
        self.assertAlmostEqual(
            reference_x_pixel(1.0, 1440, 0.02),
            (1.0 + 14.4) / 28.8 * 1439,
        )

    def test_detect_beam_returns_consistent_triplet_when_no_beam(self):
        tuner = ESA_AutoTuner(
            flag_pv_obj=_DummyPV(),
            flag_pixel=(10, 10),
            bend_pv="FAKE:BEND",
        )
        result = tuner._detect_beam(np.zeros((10, 10)))
        self.assertEqual(len(result), 3)
        self.assertEqual(result, (False, 0.0, None))

    def test_coarse_and_fine_scan_use_triplet_detection_api(self):
        tuner = _FakeAutoTuner()
        interval = tuner.coarse_scan(0, 10, n_steps=11)
        self.assertEqual(interval, (3.0, 5.0))

        best = tuner.fine_scan(3.0, 5.0, n_steps=9)
        self.assertAlmostEqual(best, 4.0)

    def test_run_returns_best_current_and_marks_done(self):
        tuner = _FakeAutoTuner()
        tuner.current = 2.0
        best = tuner.run(0, 10, coarse_steps=11, fine_steps=9)
        self.assertAlmostEqual(best, 4.0)
        self.assertAlmostEqual(tuner.get_best_current(), 4.0)
        self.assertEqual(tuner.get_last_status(), "DONE")

    def test_progress_callback_receives_scan_updates(self):
        updates = []
        tuner = _FakeAutoTuner(progress_callback=updates.append)
        tuner.current = 2.0
        best = tuner.run(0, 10, coarse_steps=11, fine_steps=9)

        self.assertAlmostEqual(best, 4.0)
        self.assertGreaterEqual(len(updates), 2)
        self.assertEqual(updates[0]["stage"], "coarse")
        self.assertIn(updates[-1]["stage"], {"final", "fine"})

    def test_operator_cancel_restores_initial_value(self):
        updates = []
        tuner = _FakeAutoTuner(
            progress_callback=updates.append,
            cancel_requested=lambda: True,
        )
        tuner.current = 4.25

        best = tuner.run(0, 10, coarse_steps=11, fine_steps=9)

        self.assertIsNone(best)
        self.assertEqual(tuner.get_last_status(), "CANCELLED")
        self.assertAlmostEqual(tuner.current, 4.25)
        self.assertEqual(updates[-1]["stage"], "restore")

    def test_scan_does_not_start_without_restorable_initial_value(self):
        tuner = _FakeAutoTuner()

        best = tuner.run(0, 10, coarse_steps=11, fine_steps=9)

        self.assertIsNone(best)
        self.assertEqual(tuner.get_last_status(), "FAILED")
        self.assertIsNone(tuner.current)

    def test_scan_error_restores_initial_value(self):
        updates = []
        tuner = _FakeAutoTuner(progress_callback=updates.append)
        tuner.current = 2.5

        def fail_image_read():
            raise RuntimeError("camera unavailable")

        tuner._get_flag_image = fail_image_read

        with self.assertRaisesRegex(RuntimeError, "camera unavailable"):
            tuner.run(0, 10, coarse_steps=11, fine_steps=9)

        self.assertEqual(tuner.get_last_status(), "FAILED")
        self.assertAlmostEqual(tuner.current, 2.5)
        self.assertEqual(updates[-1]["stage"], "restore")

    def test_center_objective_uses_x_reference_instead_of_peak_brightness(self):
        tuner = _CenterObjectiveAutoTuner()

        interval = tuner.coarse_scan(0, 10, n_steps=11)

        self.assertEqual(interval, (5.0, 7.0))
        self.assertEqual(tuner.visited[:11], list(np.linspace(0, 10, 11)))

        best = tuner.run(0, 10, coarse_steps=11, fine_steps=21)

        self.assertAlmostEqual(best, 6.0)
        self.assertAlmostEqual(tuner.best_center_offset_px, 0.0)


if __name__ == "__main__":
    unittest.main()
