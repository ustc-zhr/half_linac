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
    build_final_fit_windows,
    final_window_point_count,
    quality_recovery_values,
    quality_supplement_values,
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

    def test_quality_plan_filters_each_plane_independently(self):
        observations = [
            AdaptiveObservation(
                k1=k1,
                sigx=np.sqrt((k1 - 2.0) ** 2 + 0.2),
                sigy=np.sqrt((k1 + 1.0) ** 2 + 0.3),
                x_usable=k1 >= 0.0,
            )
            for k1 in (-5.0, -2.5, 0.0, 2.5, 5.0)
        ]

        plan = build_adaptive_plan(observations, self.config)

        self.assertAlmostEqual(plan.x.waist_k1, 2.0, places=6)
        self.assertAlmostEqual(plan.y.waist_k1, -1.0, places=6)

    def test_quality_recovery_moves_rejected_seed_inward(self):
        observations = [
            AdaptiveObservation(-5.0, 4.0, 1.0, x_usable=False),
            AdaptiveObservation(-2.5, 2.0, 1.0, x_usable=False),
            AdaptiveObservation(0.0, 1.0, 1.0),
            AdaptiveObservation(2.5, 0.5, 1.0),
            AdaptiveObservation(5.0, 2.0, 1.0, x_usable=False),
        ]

        values = quality_recovery_values(observations, self.config)

        self.assertEqual(values, (-1.25,))

    def test_final_windows_use_all_quality_filtered_observations(self):
        observations = [
            AdaptiveObservation(
                k1=k1,
                sigx=np.sqrt((k1 - 1.5) ** 2 + 0.2),
                sigy=np.sqrt((k1 + 0.5) ** 2 + 0.3),
                x_usable=k1 > -4.0,
            )
            for k1 in (-5.0, -2.0, 0.0, 1.5, 3.0, 5.0)
        ]

        windows = build_final_fit_windows(observations, self.config)

        self.assertAlmostEqual(windows.x.waist_k1, 1.5, places=6)
        self.assertAlmostEqual(windows.y.waist_k1, -0.5, places=6)

    def test_final_window_expands_to_five_existing_quality_points(self):
        observations = [
            AdaptiveObservation(
                k1=k1,
                sigx=np.sqrt((k1 - 2.3) ** 2 + 0.01),
                sigy=np.sqrt((k1 - 0.5) ** 2 + 0.3),
                x_usable=k1 not in (2.3, 2.5),
            )
            for k1 in (0.0, 1.0, 1.5, 1.9, 2.3, 2.5, 3.1, 3.9)
        ]

        windows = build_final_fit_windows(observations, self.config)

        self.assertGreaterEqual(
            final_window_point_count(observations, windows.x, self.config),
            5,
        )
        self.assertLessEqual(windows.x.k1_from, 1.5)
        self.assertGreaterEqual(windows.x.k1_to, 3.1)

    def test_quality_supplement_can_exceed_normal_point_budget(self):
        observations = [
            AdaptiveObservation(-2.0, 2.0, 2.0),
            AdaptiveObservation(0.0, 1.0, 1.0),
            AdaptiveObservation(1.0, 0.2, 0.5, x_usable=False),
            AdaptiveObservation(2.0, 1.0, 1.0),
        ]
        config = AdaptiveScanConfig(
            k1_min=-5.0,
            k1_max=5.0,
            initial_points=4,
            target_points_per_plane=7,
            max_unique_points=4,
            reuse_tolerance=1e-6,
        )

        values = quality_supplement_values(observations, config, max_new_points=4)

        self.assertTrue(values)
        self.assertLessEqual(len(values), 4)
        self.assertTrue(all(config.k1_min <= value <= config.k1_max for value in values))


if __name__ == "__main__":
    unittest.main()
