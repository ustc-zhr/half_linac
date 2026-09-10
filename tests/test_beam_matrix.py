import math
import sys
import unittest
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from half_linac.src.shared.beam_matrix import (
    build_measurement_matrix,
    diagnose_measurement_matrix,
    estimate_beam_matrix_uncertainty,
    extract_uncoupled_transverse_projections,
    normalized_emittance,
    reconstruct_beam_matrix,
    reconstruct_transverse_beam_matrices,
    simulate_beam_matrix_uncertainty,
    transport_beam_moments,
)


class BeamMatrixReconstructionTests(unittest.TestCase):
    @staticmethod
    def _truth():
        emittance = 2.0e-6
        beta = 8.0
        alpha = 1.2
        gamma = (1.0 + alpha**2) / beta
        return np.array(
            [emittance * beta, -emittance * alpha, emittance * gamma]
        )

    @staticmethod
    def _sizes(projections, moments):
        design = build_measurement_matrix(projections)
        return np.sqrt(design @ moments)

    def test_three_drift_screens_recover_exact_beam_matrix(self):
        projections = ((1.0, 0.0), (1.0, 5.5), (1.0, 11.0))
        truth = self._truth()
        sizes = self._sizes(projections, truth)

        result = reconstruct_beam_matrix(projections, sizes)

        self.assertTrue(result.valid, result.message)
        np.testing.assert_allclose(result.moments, truth, rtol=1e-11, atol=1e-18)
        self.assertAlmostEqual(result.geometric_emittance_m_rad, 2.0e-6, places=16)
        self.assertAlmostEqual(result.beta_m, 8.0, places=11)
        self.assertAlmostEqual(result.alpha, 1.2, places=11)
        self.assertEqual(result.rank, 3)
        self.assertEqual(result.degrees_of_freedom, 0)
        self.assertIsNone(result.parameter_covariance)
        self.assertTrue(result.warnings)

    def test_four_conditions_use_weighted_least_squares(self):
        projections = (
            (1.0, 0.0),
            (1.0, 5.5),
            (1.0, 11.0),
            (1.3503327631, 18.5753196978),
        )
        truth = self._truth()
        sizes = self._sizes(projections, truth)
        measured = sizes + np.array((1.0e-6, -2.0e-6, 1.5e-6, -0.5e-6))
        errors = np.array((2.0e-6, 3.0e-6, 2.5e-6, 4.0e-6))

        result = reconstruct_beam_matrix(projections, measured, errors)

        design = build_measurement_matrix(projections)
        variance_errors = 2.0 * measured * errors
        expected, *_ = np.linalg.lstsq(
            design / variance_errors[:, np.newaxis],
            measured**2 / variance_errors,
            rcond=None,
        )
        self.assertTrue(result.valid, result.message)
        np.testing.assert_allclose(result.moments, expected, rtol=1e-10)
        self.assertEqual(result.solver, "weighted_svd_lstsq")
        self.assertEqual(result.degrees_of_freedom, 1)
        self.assertIsNotNone(result.parameter_covariance)
        self.assertIsNotNone(result.reduced_chi_squared)
        self.assertIsNotNone(result.geometric_emittance_standard_deviation_m_rad)

    def test_fourth_independent_condition_reduces_parameter_uncertainty(self):
        three = ((1.0, 0.0), (1.0, 5.5), (1.0, 11.0))
        four = three + ((0.5149653909, 9.6176949433),)
        truth = self._truth()
        errors_three = np.full(3, 2.0e-6)
        errors_four = np.full(4, 2.0e-6)

        result_three = reconstruct_beam_matrix(
            three,
            self._sizes(three, truth),
            errors_three,
        )
        result_four = reconstruct_beam_matrix(
            four,
            self._sizes(four, truth),
            errors_four,
        )

        covariance_reduction = np.asarray(result_three.parameter_covariance) - np.asarray(
            result_four.parameter_covariance
        )
        self.assertGreaterEqual(float(np.min(np.linalg.eigvalsh(covariance_reduction))), -1e-27)

    def test_monte_carlo_is_reproducible_and_reports_invalid_trials(self):
        projections = ((1.0, 0.0), (1.0, 5.5), (1.0, 11.0))

        first = simulate_beam_matrix_uncertainty(
            projections,
            self._truth(),
            0.05,
            trial_count=250,
            seed=123,
        )
        second = simulate_beam_matrix_uncertainty(
            projections,
            self._truth(),
            0.05,
            trial_count=250,
            seed=123,
        )

        self.assertEqual(first, second)
        self.assertEqual(first.valid_trial_count + first.invalid_trial_count, 250)
        self.assertGreater(first.valid_fraction, 0.0)
        self.assertIsNotNone(first.relative_rmse)

    def test_monte_carlo_fourth_condition_reduces_random_emittance_spread(self):
        three = ((1.0, 0.0), (1.0, 5.5), (1.0, 11.0))
        four = three + ((1.3503327631, 18.5753196978),)

        three_result = simulate_beam_matrix_uncertainty(
            three,
            self._truth(),
            0.02,
            trial_count=3_000,
            seed=42,
        )
        four_result = simulate_beam_matrix_uncertainty(
            four,
            self._truth(),
            0.02,
            trial_count=3_000,
            seed=42,
        )

        self.assertLess(
            four_result.relative_standard_deviation,
            three_result.relative_standard_deviation,
        )

    def test_linear_uncertainty_estimate_tracks_low_noise_monte_carlo(self):
        projections = (
            (1.0, 0.0),
            (1.0, 5.5),
            (1.0, 11.0),
            (0.5149653909, 9.6176949433),
        )
        analytic = estimate_beam_matrix_uncertainty(
            projections,
            self._truth(),
            0.002,
        )
        simulated = simulate_beam_matrix_uncertainty(
            projections,
            self._truth(),
            0.002,
            trial_count=5_000,
            seed=17,
        )

        self.assertAlmostEqual(simulated.valid_fraction, 1.0)
        self.assertAlmostEqual(
            simulated.relative_standard_deviation,
            analytic.relative_emittance_standard_deviation,
            delta=0.001,
        )

    def test_zero_noise_monte_carlo_recovers_truth(self):
        result = simulate_beam_matrix_uncertainty(
            ((1.0, 0.0), (1.0, 5.5), (1.0, 11.0)),
            self._truth(),
            0.0,
            trial_count=5,
        )

        self.assertEqual(result.valid_trial_count, 5)
        self.assertLess(result.relative_rmse, 1e-12)

    def test_monte_carlo_rejects_nonphysical_truth(self):
        with self.assertRaisesRegex(ValueError, "positive-definite"):
            simulate_beam_matrix_uncertainty(
                ((1.0, 0.0), (1.0, 5.5), (1.0, 11.0)),
                (1.0, 2.0, 1.0),
                0.02,
            )

    def test_repeated_projection_is_rank_deficient(self):
        result = reconstruct_beam_matrix(
            ((1.0, 2.0), (1.0, 2.0), (1.0, 2.0)),
            (1.0e-3, 1.1e-3, 0.9e-3),
        )

        self.assertFalse(result.valid)
        self.assertEqual(result.status, "rank_deficient")
        self.assertLess(result.rank, 3)
        self.assertTrue(math.isinf(result.condition_number))

    def test_non_positive_covariance_is_reported(self):
        result = reconstruct_beam_matrix(
            ((1.0, 0.0), (1.0, 1.0), (1.0, 2.0)),
            (1.0, 2.0, 1.0),
        )

        self.assertFalse(result.valid)
        self.assertEqual(result.status, "non_physical")
        self.assertIsNotNone(result.moments)
        self.assertIsNone(result.geometric_emittance_m_rad)

    def test_column_normalized_condition_is_invariant_to_r12_units(self):
        projections_m = ((1.0, 0.0), (1.0, 5.5), (1.0, 11.0))
        projections_mm = tuple((r11, 1000.0 * r12) for r11, r12 in projections_m)
        truth_m = self._truth()
        truth_mm = np.array((truth_m[0], truth_m[1] / 1000.0, truth_m[2] / 1.0e6))

        result_m = reconstruct_beam_matrix(
            projections_m,
            self._sizes(projections_m, truth_m),
        )
        result_mm = reconstruct_beam_matrix(
            projections_mm,
            self._sizes(projections_mm, truth_mm),
        )

        self.assertAlmostEqual(result_m.condition_number, result_mm.condition_number, places=12)

        diagnostics_m = diagnose_measurement_matrix(projections_m)
        diagnostics_mm = diagnose_measurement_matrix(projections_mm)
        self.assertEqual(diagnostics_m.rank, 3)
        self.assertAlmostEqual(
            diagnostics_m.condition_number,
            diagnostics_mm.condition_number,
            places=12,
        )

    def test_rejects_invalid_input(self):
        with self.assertRaisesRegex(ValueError, "at least 3"):
            reconstruct_beam_matrix(((1.0, 0.0), (1.0, 1.0)), (1.0, 1.0))
        with self.assertRaisesRegex(ValueError, "positive"):
            reconstruct_beam_matrix(
                ((1.0, 0.0), (1.0, 1.0), (1.0, 2.0)),
                (1.0, 0.0, 1.0),
            )

    def test_normalized_emittance_uses_exact_beta_gamma(self):
        geometric = 2.0e-6
        kinetic_energy = 36.0
        rest_energy = 0.51099895
        gamma = 1.0 + kinetic_energy / rest_energy

        result = normalized_emittance(
            geometric,
            kinetic_energy,
            rest_energy_mev=rest_energy,
        )

        self.assertAlmostEqual(result, geometric * math.sqrt(gamma**2 - 1.0), places=18)

    def test_transport_beam_moments_preserves_emittance_for_symplectic_map(self):
        truth = self._truth()
        transported = transport_beam_moments(
            ((1.0, 5.5), (0.0, 1.0)),
            truth,
        )

        initial_emittance = math.sqrt(truth[0] * truth[2] - truth[1] ** 2)
        final_emittance = math.sqrt(
            transported[0] * transported[2] - transported[1] ** 2
        )
        self.assertAlmostEqual(final_emittance, initial_emittance, places=16)

    def test_reconstructs_both_uncoupled_planes_from_transfer_matrices(self):
        matrices = []
        for x_projection, y_projection in (
            ((1.0, 0.0), (1.0, 0.0)),
            ((1.0, 5.5), (1.0, 5.5)),
            ((1.0, 11.0), (1.0, 11.0)),
            ((1.3503327631, 18.5753196978), (0.5149653909, 9.6176949433)),
        ):
            matrix = np.eye(6)
            matrix[0, :2] = x_projection
            matrix[2, 2:4] = y_projection
            matrices.append(matrix)
        x_projections, y_projections = extract_uncoupled_transverse_projections(matrices)
        x_truth = self._truth()
        y_truth = np.array((10.0e-6, 0.5e-6, 0.3e-6))

        result = reconstruct_transverse_beam_matrices(
            matrices,
            self._sizes(x_projections, x_truth),
            self._sizes(y_projections, y_truth),
        )

        self.assertTrue(result.valid)
        np.testing.assert_allclose(result.x.moments, x_truth, rtol=1e-10)
        np.testing.assert_allclose(result.y.moments, y_truth, rtol=1e-10)

    def test_transverse_reconstruction_rejects_coupled_maps(self):
        matrices = np.repeat(np.eye(6)[np.newaxis, :, :], 3, axis=0)
        matrices[1, 0, 2] = 1.0e-3

        with self.assertRaisesRegex(ValueError, "x-y coupling"):
            extract_uncoupled_transverse_projections(matrices)


if __name__ == "__main__":
    unittest.main()
