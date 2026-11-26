"""Physicality partitions, transport-span statistics and exact replay checks."""

import json
import unittest

import numpy as np

from scripts.diagnose_emit_infobax_posterior import (
    DEFAULT_CONFIG, PARTITIONS, diagnose_posterior, physicality, reference_audit,
    span_energy_fraction, validate_config, wilson_interval,
)
from half_linac.src.optimization.infobax import FinitePoolSpec, InfoBAXConfig, InfoBAXCore, PointObservation
from half_linac.src.optimization.infobax.algorithm import beta_gamma
from half_linac.src.optimization.infobax.core import stable_seed
from tests.test_infobax_core import small_pool


class PosteriorDiagnosticTests(unittest.TestCase):
    def test_exclusive_partition_distinguishes_curve_and_covariance_failures(self):
        pool = small_pool()
        good = pool.design_matrix @ np.array([1.0, 0, 1.0])
        determinant_bad = pool.design_matrix @ np.array([1.0, 0.4, 0.1])
        diagonal_bad = pool.design_matrix @ np.array([1.0, 0, -0.1])
        negative = np.array([-0.1, 1.0, 2.0])
        nonfinite = np.array([float("nan"), 1.0, 2.0])
        curves = np.array([[good, good], [negative, good], [determinant_bad, determinant_bad],
                           [diagonal_bad, diagonal_bad], [negative, negative], [nonfinite, nonfinite]])
        result = physicality(curves.reshape(6, -1), pool)
        self.assertEqual(result["invalid_output_probability"], 4/6)
        self.assertEqual(result["no_positive_curve_candidate_probability"], 2/6)
        self.assertEqual(result["positive_curves_without_physical_covariance_probability"], 2/6)
        for row in result["candidates"]:
            self.assertEqual(sum(row[key] for key in PARTITIONS), 6)
            self.assertEqual(row["nonpositive_diagonal"], 1)
            self.assertEqual(row["nonpositive_determinant"], 1)

    def test_transport_span_measure_preserves_a_physical_covariance(self):
        x = np.arange(-2, 3, dtype=float)
        design = np.column_stack((np.ones(5), 2*x, x*x))
        mean = design @ np.array([4.0, 0.1, 1.0])
        covariance = design @ np.eye(3) @ design.T
        self.assertLess(span_energy_fraction(mean, covariance, design), 1e-14)
        self.assertAlmostEqual(span_energy_fraction(np.zeros(5), np.eye(5), design), 2/5)

    def test_monte_carlo_intervals_do_not_turn_zero_samples_into_a_zero_bound(self):
        interval = wilson_interval(0, 16384)
        self.assertLessEqual(interval[0], 1e-12)
        self.assertGreater(interval[1], 0)
        lo, hi = wilson_interval(8192, 16384)
        self.assertLess(lo, 0.5)
        self.assertGreater(hi, 0.5)
        with self.assertRaises(ValueError):
            wilson_interval(1, 0)

    def test_shared_unweighted_functional_closes_while_weighted_target_can_differ(self):
        scans = np.arange(-2, 3, dtype=float)
        design = np.column_stack((np.ones(5), 2*scans, scans*scans))
        pool = FinitePoolSpec(candidate_ids=("a", "b"), candidate_coordinates=[[0], [1]],
                             scan_values=scans, scan_coordinates=np.linspace(0, 1, 5), design_matrix=design,
                             energy_mev=225, observation_noise_std_mm2=0.001)
        moments = np.array([4e-8, 1e-9, 1e-8])
        momentum = beta_gamma(pool.energy_mev)
        reference = momentum*np.sqrt(moments[0]*moments[2]-moments[1]**2)*1e6
        primary = []
        for work in (0, 1):
            for scan, variance in zip(scans, design @ moments*1e6):
                primary.append(dict(case="unit", workpoint=work, scan_value=scan,
                    corrected_variance_mm2=variance+(0.01 if scan == 2 else 0), noise_std_mm2=0.1 if scan == 2 else 1e-5,
                    entrance_beta_gamma=momentum, entrance_emittance_mm_mrad=reference, entrance_moments=moments.tolist()))
        references = [dict(case="unit", workpoint=work, emittance_mm_mrad=reference) for work in (0, 1)]
        rows = reference_audit(pool, [(1, x) for x in scans], primary, references,
                               dict(workpoints=[0, 1], scan_values=scans.tolist()), "unit")
        for row in rows:
            self.assertLess(row["m0_shared_uw_relative_error"], 1e-12)
            self.assertLess(row["weighted_reference_reproduction_error"], 1e-5)
            self.assertGreater(row["unweighted_vs_weighted_relative_difference"], 0.01)

    def test_native_draws_reproduce_the_core_recommendation_without_altering_observations(self):
        pool = small_pool()
        core = InfoBAXCore(pool, InfoBAXConfig(seed=4))
        values = np.tile(pool.design_matrix @ np.array([1.0, 0, 1.0]), 2)
        for index in core.initial_design:
            core.observe(PointObservation(index, float(values[index]), 0.1))
        before = core.to_checkpoint()
        diag, candidates, queries = diagnose_posterior(core, pool, values, np.full(6, 0.1),
            dict(case="unit", seed=4, policy="infobax", observations=6, sample_kind="native"),
            core.config.summary_samples, stable_seed("summary", 4, "M0", 0))
        expected = core.recommend()
        self.assertEqual(diag["invalid_output_probability"], expected.invalid_output_probability)
        self.assertEqual([r["physical_probability"] for r in candidates], list(expected.candidate_physical_probabilities))
        self.assertTrue(all(r["observed"] for r in queries))
        self.assertEqual(core.to_checkpoint(), before)

    def test_diagnostic_sampling_budget_is_fixed(self):
        config = json.loads(DEFAULT_CONFIG.read_text())
        validate_config(config)
        with self.assertRaises(ValueError):
            validate_config(dict(config, enhanced_samples=1024))


if __name__ == "__main__":
    unittest.main()
