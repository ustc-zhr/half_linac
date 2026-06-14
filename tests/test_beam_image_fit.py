from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from half_linac.src.shared.beam_diagnostics import fit_beam_image


class BeamImageFitTests(unittest.TestCase):
    def test_fit_beam_image_recovers_gaussian_sigmas(self):
        x = np.linspace(-6.0, 6.0, 121)
        y = np.linspace(-5.0, 5.0, 101)
        xx, yy = np.meshgrid(x, y)
        image = 12.0 * np.exp(-((xx - 0.8) ** 2) / (2 * 1.2**2)) * np.exp(
            -((yy + 0.4) ** 2) / (2 * 1.7**2)
        )

        result = fit_beam_image(image, extent=(-6.0, 6.0, -5.0, 5.0))

        self.assertTrue(result.valid, result.message)
        self.assertAlmostEqual(result.sigx_mm, 1.2, places=2)
        self.assertAlmostEqual(result.sigy_mm, 1.7, places=2)
        self.assertAlmostEqual(result.x_projection.center, 0.8, places=2)
        self.assertAlmostEqual(result.y_projection.center, -0.4, places=2)
        self.assertIsNotNone(result.x_projection.fitted_projection)
        self.assertIsNotNone(result.y_projection.fitted_projection)

    def test_fit_beam_image_reports_low_signal(self):
        image = np.zeros((12, 16))

        result = fit_beam_image(image, extent=(-1.0, 1.0, -1.0, 1.0))

        self.assertFalse(result.valid)
        self.assertEqual(result.status, "low_signal")
        self.assertIsNone(result.sigx_mm)
        self.assertIsNone(result.sigy_mm)

    def test_fit_beam_image_reports_empty_window(self):
        image = np.ones((12, 16))

        result = fit_beam_image(
            image,
            extent=(-1.0, 1.0, -1.0, 1.0),
            xlim=(2.0, 3.0),
            ylim=(2.0, 3.0),
        )

        self.assertFalse(result.valid)
        self.assertEqual(result.status, "empty_window")
        self.assertEqual(result.cropped_image.size, 0)


if __name__ == "__main__":
    unittest.main()
