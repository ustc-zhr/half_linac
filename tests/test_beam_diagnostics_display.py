import unittest

import numpy as np
from matplotlib.colors import LogNorm, Normalize

from half_linac.src.shared.beam_diagnostics import resolve_image_display_scale


class ImageDisplayScaleTests(unittest.TestCase):
    def test_log_image_display_masks_nonpositive_values(self):
        image, norm, warning = resolve_image_display_scale(
            np.array([[-1.0, 0.0, 1.0, 10.0]]),
            logarithmic=True,
        )

        self.assertIsInstance(norm, LogNorm)
        self.assertEqual(image.mask.tolist(), [[True, True, False, False]])
        self.assertIsNone(warning)

    def test_log_image_display_falls_back_when_frame_has_no_positive_values(self):
        _image, norm, warning = resolve_image_display_scale(
            np.array([[-2.0, 0.0]]),
            logarithmic=True,
        )

        self.assertIsInstance(norm, Normalize)
        self.assertIn("no positive values", warning)

    def test_log_image_display_replaces_nonpositive_manual_limit(self):
        _image, norm, warning = resolve_image_display_scale(
            np.array([[1.0, 10.0]]),
            logarithmic=True,
            vmin=0.0,
            vmax=10.0,
        )

        self.assertIsInstance(norm, LogNorm)
        self.assertEqual(norm.vmin, 1.0)
        self.assertEqual(norm.vmax, 10.0)
        self.assertIn("must be positive", warning)

    def test_image_display_handles_frame_without_finite_values(self):
        image, norm, warning = resolve_image_display_scale(
            np.array([[np.nan, np.inf]]),
            logarithmic=True,
        )

        self.assertIsInstance(norm, Normalize)
        self.assertEqual(image.mask.tolist(), [[True, True]])
        self.assertIn("no finite", warning)


if __name__ == "__main__":
    unittest.main()
