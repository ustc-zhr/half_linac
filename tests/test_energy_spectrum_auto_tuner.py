from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from half_linac.src.apps.energy_spectrum.esa_auto_tuner import ESA_AutoTuner


class _DummyPV:
    def get(self):
        return np.zeros((10, 10)).ravel().tolist()


class _FakeAutoTuner(ESA_AutoTuner):
    def __init__(self, progress_callback=None):
        super().__init__(
            flag_pv_obj=_DummyPV(),
            flag_pixel=(10, 10),
            bend_pv="FAKE:BEND",
            progress_callback=progress_callback,
        )
        self.current = None

    def _set_bend(self, current):
        self.current = float(current)

    def _get_flag_image(self):
        return np.zeros((10, 10))

    def _detect_beam(self, img):
        if self.current is None or self.current < 3 or self.current > 5:
            return False, 0.0, None
        score = 10.0 - abs(self.current - 4.0)
        return True, score, self.current


class ESAAutoTunerTests(unittest.TestCase):
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
        best = tuner.run(0, 10, coarse_steps=11, fine_steps=9)
        self.assertAlmostEqual(best, 4.0)
        self.assertAlmostEqual(tuner.get_best_current(), 4.0)
        self.assertEqual(tuner.get_last_status(), "DONE")

    def test_progress_callback_receives_scan_updates(self):
        updates = []
        tuner = _FakeAutoTuner(progress_callback=updates.append)
        best = tuner.run(0, 10, coarse_steps=11, fine_steps=9)

        self.assertAlmostEqual(best, 4.0)
        self.assertGreaterEqual(len(updates), 2)
        self.assertEqual(updates[0]["stage"], "coarse")
        self.assertIn(updates[-1]["stage"], {"final", "fine"})


if __name__ == "__main__":
    unittest.main()
