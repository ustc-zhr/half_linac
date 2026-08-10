import unittest
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from half_linac.src.apps.emit_measure.adaptive_scan import (
    AdaptiveObservation,
    AdaptiveScanConfig,
    build_adaptive_plan,
    seed_values,
    validate_adaptive_scan,
)


class AdaptiveScanPlanTests(unittest.TestCase):
    def setUp(self):
        self.config = AdaptiveScanConfig(
            k1_min=-5.0,
            k1_max=5.0,
            initial_points=4,
            target_points_per_plane=7,
            max_unique_points=16,
            reuse_tolerance=1e-6,
        )

    def test_seed_values_are_bounded(self):
        values = seed_values(-9.0, 9.0, self.config)
        np.testing.assert_allclose(values, (-5.0, -5.0 / 3.0, 5.0 / 3.0, 5.0))

    def test_adapts_each_plane_around_its_own_waist(self):
        k1_values = np.linspace(-3.0, 3.0, 7)
        observations = [
            AdaptiveObservation(
                k1=float(k1),
                sigx=float(np.sqrt(0.4 * (k1 + 1.0) ** 2 + 0.25)),
                sigy=float(np.sqrt(0.3 * (k1 - 1.2) ** 2 + 0.36)),
            )
            for k1 in k1_values
        ]
        plan = build_adaptive_plan(observations, self.config)
        self.assertAlmostEqual(plan.x.waist_k1, -1.0, places=6)
        self.assertAlmostEqual(plan.y.waist_k1, 1.2, places=6)
        self.assertLess(plan.x.k1_to, plan.y.k1_to)
        self.assertTrue(all(self.config.k1_min <= value <= self.config.k1_max for value in plan.new_values))

    def test_reuses_existing_points_and_respects_budget(self):
        observations = [
            AdaptiveObservation(k1=k1, sigx=np.sqrt(k1**2 + 1), sigy=np.sqrt(k1**2 + 1))
            for k1 in (-2.0, 0.0, 2.0)
        ]
        config = AdaptiveScanConfig(
            k1_min=-4,
            k1_max=4,
            initial_points=3,
            target_points_per_plane=9,
            max_unique_points=6,
            reuse_tolerance=1e-9,
        )
        plan = build_adaptive_plan(observations, config)
        self.assertLessEqual(len(plan.new_values), 1)
        self.assertEqual(plan.validation_reserved_points, 2)
        self.assertNotIn(0.0, plan.new_values)

    def test_refine_scan_reserves_two_points_for_final_validation(self):
        observations = [
            AdaptiveObservation(
                k1=k1,
                sigx=np.sqrt((k1 + 0.5) ** 2 + 0.2),
                sigy=np.sqrt((k1 - 0.7) ** 2 + 0.3),
            )
            for k1 in (-2.0, -0.5, 1.0, 2.0)
        ]

        plan = build_adaptive_plan(observations, self.config)

        self.assertEqual(plan.validation_reserved_points, 2)
        self.assertLessEqual(
            len(observations) + len(plan.new_values),
            self.config.max_unique_points - 2,
        )

    def test_fallback_expands_toward_edge_minimum(self):
        observations = [
            AdaptiveObservation(k1=k1, sigx=4.0 - k1, sigy=4.0 - k1)
            for k1 in (0.0, 1.0, 2.0)
        ]
        plan = build_adaptive_plan(observations, self.config)
        self.assertGreater(plan.x.k1_to, 2.0)

    def test_final_validation_is_independent_for_each_plane(self):
        k1_values = np.linspace(-3.0, 3.0, 7)
        observations = [
            AdaptiveObservation(
                k1=float(k1),
                sigx=float(np.sqrt((k1 + 0.8) ** 2 + 0.2)),
                sigy=float(np.sqrt((k1 - 4.0) ** 2 + 0.3)),
            )
            for k1 in k1_values
        ]

        validation = validate_adaptive_scan(observations, self.config)

        self.assertEqual(validation.x.status, "validated")
        self.assertEqual(validation.y.status, "needs_high_k_coverage")
        self.assertIn(self.config.k1_max, validation.new_values)
        self.assertEqual(validation.status, "partial")

    def test_shallow_bracket_is_valid_but_requests_more_leverage(self):
        observations = [
            AdaptiveObservation(
                k1=k1,
                sigx=np.sqrt(0.01 * k1**2 + 1.0),
                sigy=np.sqrt(0.01 * k1**2 + 1.0),
            )
            for k1 in (-1.0, 0.0, 1.0)
        ]

        validation = validate_adaptive_scan(observations, self.config)

        self.assertEqual(validation.status, "validated")
        self.assertIn("limited low-K leverage", validation.x.warnings)
        self.assertIn("limited high-K leverage", validation.x.warnings)
        self.assertEqual(validation.new_values, (-5.0, 5.0))

    def test_final_validation_respects_remaining_point_budget(self):
        observations = [
            AdaptiveObservation(
                k1=k1,
                sigx=np.sqrt(0.01 * k1**2 + 1.0),
                sigy=np.sqrt(0.01 * k1**2 + 1.0),
            )
            for k1 in (-1.0, 0.0, 1.0)
        ]
        config = AdaptiveScanConfig(
            k1_min=-5.0,
            k1_max=5.0,
            initial_points=3,
            target_points_per_plane=3,
            max_unique_points=4,
            reuse_tolerance=1e-6,
        )

        validation = validate_adaptive_scan(observations, config)

        self.assertEqual(len(validation.new_values), 1)


if __name__ == "__main__":
    unittest.main()
