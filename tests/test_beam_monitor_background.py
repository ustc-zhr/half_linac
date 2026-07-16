from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from half_linac.src.apps.beam_monitor.profile_runtime import (
    resolve_beam_monitor_background_paths,
)
from half_linac.src.shared.beam_diagnostics import subtract_background
from half_linac.src.shared.machine_profile import load_app_context


class BeamMonitorBackgroundTests(unittest.TestCase):
    def test_background_paths_are_isolated_by_flag(self):
        context = load_app_context("beam_monitor", machine_id="irfel", control_backend="vm")

        prfesa = resolve_beam_monitor_background_paths(context, "PRFESA")
        prf04 = resolve_beam_monitor_background_paths(context, "PRF04")

        self.assertNotEqual(prfesa["background_dir"], prf04["background_dir"])
        self.assertEqual(prfesa["background_image_path"].name, "background.npy")
        self.assertIn("runtime/irfel/vm/latest/backgrounds/prfesa", str(prfesa["background_dir"]))

    def test_background_subtraction_clips_negative_pixels(self):
        image = np.array([[5.0, 2.0], [1.0, 9.0]])
        background = np.array([[3.0, 4.0], [1.0, 2.0]])

        actual = subtract_background(image, background)

        np.testing.assert_array_equal(actual, np.array([[2.0, 0.0], [0.0, 7.0]]))


if __name__ == "__main__":
    unittest.main()
