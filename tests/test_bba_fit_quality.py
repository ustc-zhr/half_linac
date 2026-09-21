import json
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from half_linac.src.apps.bba.fit_quality import (
    analyze_bba1_inner_fit,
    build_bba1_scan_guidance,
    fit_bba1_center,
)


class BbaFitQualityTests(unittest.TestCase):
    def test_inner_linear_repeated_samples_are_good(self):
        k1 = np.linspace(-2, 2, 5)
        samples = [np.array([0.4 * value + 0.001, 0.4 * value, 0.4 * value - 0.001]) for value in k1]
        result = analyze_bba1_inner_fit(k1, samples)
        self.assertEqual(result["quality"], "good")
        self.assertGreater(result["signal_to_noise"], 1)
        self.assertIsNotNone(result["slope_sigma"])

    def test_inner_weak_slope_is_not_invalid(self):
        k1 = np.linspace(-2, 2, 5)
        samples = [np.array([0.0001 * value + noise for noise in (-0.01, 0.01, 0.0)]) for value in k1]
        result = analyze_bba1_inner_fit(k1, samples)
        self.assertEqual(result["quality"], "weak")

    def test_inner_excess_residuals_are_review(self):
        k1 = np.linspace(-2, 2, 5)
        samples = [np.array([0.4 * value, 0.4 * value + 0.001, 0.4 * value - 0.001]) for value in k1]
        samples[2] += 0.2
        result = analyze_bba1_inner_fit(k1, samples)
        self.assertEqual(result["quality"], "review")

    def test_inner_invalid_and_single_sample_cases(self):
        invalid = analyze_bba1_inner_fit([0, 1], [[0], [1]])
        self.assertEqual(invalid["quality"], "invalid")
        single = analyze_bba1_inner_fit([0, 1, 2], [[0], [1], [2]])
        self.assertEqual(single["quality"], "good")
        self.assertIsNone(single["noise_mean_m"])

    def test_scan_guidance_distinguishes_weak_review_and_cor_range(self):
        base = {
            "positions_m": [-0.001, 0.0, 0.001],
            "offset_m": 0.0,
            "inner_fits": [
                {"corrector": -1.0, "slope": -1.0, "quality": "good"},
                {"corrector": 0.0, "slope": 0.0, "quality": "weak"},
                {"corrector": 1.0, "slope": 1.0, "quality": "good"},
            ],
        }
        guidance = build_bba1_scan_guidance(base)
        self.assertIn("may be expected", " ".join(guidance))

        weak = dict(base, inner_fits=[
            {"corrector": -1.0, "slope": -0.1, "quality": "weak"},
            {"corrector": 0.0, "slope": 0.0, "quality": "weak"},
            {"corrector": 1.0, "slope": 0.1, "quality": "good"},
        ])
        self.assertIn("widening the K1 range", " ".join(build_bba1_scan_guidance(weak)))

        review = dict(base, inner_fits=[
            {"corrector": -1.0, "slope": -1.0, "quality": "review"},
            {"corrector": 0.0, "slope": 0.0, "quality": "good"},
            {"corrector": 1.0, "slope": 1.0, "quality": "good"},
        ])
        self.assertIn("K1 nonlinearity", " ".join(build_bba1_scan_guidance(review)))

        outside = dict(base, offset_m=-0.002, inner_fits=[
            {"corrector": 0.0, "slope": 1.0, "quality": "good"},
            {"corrector": 1.0, "slope": 2.0, "quality": "good"},
            {"corrector": 2.0, "slope": 3.0, "quality": "good"},
        ])
        self.assertIn("lower COR setpoints", " ".join(build_bba1_scan_guidance(outside)))

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
