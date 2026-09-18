import json
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from half_linac.src.apps.bba.fit_quality import fit_bba1_center


class BbaFitQualityTests(unittest.TestCase):
    def test_exact_center_and_covariance_uncertainty(self):
        positions = np.arange(-2, 3) * 0.001
        noise = np.array([1, -2, 2, -2, 1]) * 0.0001
        result = fit_bba1_center(positions, 3 * (positions - 0.0003) + noise)
        expected_variance = np.sum(noise ** 2) / 3 / 9 * (1 / 5 + 0.0003 ** 2 / np.sum(positions ** 2))
        self.assertAlmostEqual(result["offset_m"], 0.0003)
        self.assertAlmostEqual(result["offset_sigma_m"] ** 2, expected_variance)
        self.assertEqual(result["quality"], "good")
        json.dumps(result, allow_nan=False)

    def test_extrapolation_is_flagged_even_for_perfect_line(self):
        positions = np.linspace(-0.001, 0.001, 5)
        result = fit_bba1_center(positions, 2 * (positions - 0.003))
        self.assertAlmostEqual(result["r_squared"], 1)
        self.assertEqual(result["quality"], "review")
        self.assertIn("extrapolation", " ".join(result["reasons"]))

    def test_degenerate_two_point_and_nonfinite_data(self):
        for positions, responses in (([0, 1], [-1, 1]), ([1, 1, 1], [0, 1, 2]),
                                     ([0, 1, 2], [1, 1, 1]), ([0, 1, 2], [1, float("nan"), 2])):
            result = fit_bba1_center(positions, responses)
            self.assertEqual(result["quality"], "invalid")
            self.assertIsNone(result["offset_sigma_m"])
            json.dumps(result, allow_nan=False)

    def test_quality_is_invariant_to_response_units(self):
        positions = np.linspace(-0.001, 0.001, 7)
        responses = positions + np.array([0, 2, -1, 0, 1, -2, 0]) * 0.0002
        original = fit_bba1_center(positions, responses)
        scaled = fit_bba1_center(positions, responses * 1e-12)
        self.assertEqual(original["quality"], "review")
        self.assertEqual(original["quality"], scaled["quality"])
        self.assertAlmostEqual(original["offset_sigma_m"], scaled["offset_sigma_m"])


if __name__ == "__main__":
    unittest.main()
