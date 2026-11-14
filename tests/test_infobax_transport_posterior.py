"""Transport-GP conditioning and representation tests independent of research artifacts."""

from dataclasses import replace
import unittest
from unittest.mock import patch

import numpy as np

from repo_bootstrap import ensure_repo_import_path
ensure_repo_import_path(__file__)

from half_linac.src.optimization.infobax import FinitePoolSpec, InfoBAXConfig, InfoBAXCore, PointObservation
from half_linac.src.optimization.infobax.algorithm import BeamEmittanceAlgorithm
from half_linac.src.optimization.infobax.posterior import matern52
from half_linac.src.optimization.infobax.transport_posterior import TransportMomentGP
from tests.test_infobax_core import small_pool


def pool():
    scan = np.arange(-2, 3, dtype=float)
    return FinitePoolSpec(candidate_ids=("a", "b"), candidate_coordinates=[[0], [1]],
        scan_values=scan, scan_coordinates=np.linspace(0, 1, 5),
        design_matrix=np.column_stack((np.ones(5), 2*scan, scan*scan)),
        energy_mev=225, observation_noise_std_mm2=0.01)


class TransportMomentTests(unittest.TestCase):
    def test_latent_conditioning_matches_independent_response_space_formula(self):
        spec, config = pool(), InfoBAXConfig()
        observations = [PointObservation(0, 1.0, 0.2), PointObservation(7, 2.0, 0.1)]
        actual = TransportMomentGP(spec, config).fit(observations)
        design = spec.design_matrix
        projector = design @ np.linalg.pinv(design)
        kernel = matern52(spec.candidate_coordinates, spec.candidate_coordinates, np.array([0.32]))
        scale = 0.5
        prior = scale**2*(5/3)*np.kron(kernel, projector)
        mean = np.tile(projector @ np.full(5, 1.5), 2)
        indices = [0, 7]
        likelihood = prior[np.ix_(indices, indices)]+np.diag([0.2**2+1e-10, 0.1**2+1e-10])
        cross = prior[:, indices]
        expected_mean = mean+cross @ np.linalg.solve(likelihood, np.array([1, 2])-mean[indices])
        expected_covariance = prior-cross @ np.linalg.solve(likelihood, cross.T)
        np.testing.assert_allclose(actual.mean, expected_mean, atol=1e-12)
        np.testing.assert_allclose(actual.covariance, expected_covariance, atol=1e-12)
        np.testing.assert_allclose(actual.coefficient_mean @ design.T, actual.mean.reshape(2, 5), atol=1e-12)
        self.assertEqual(actual.latent_dimension, 6)
        self.assertFalse(actual.mean.flags.writeable)

    def test_every_draw_obeys_transport_and_invalid_samples_are_retained(self):
        spec = pool()
        posterior = TransportMomentGP(spec, InfoBAXConfig()).fit([PointObservation(2, 1.0, 1.0)])
        samples = posterior.sample(512, 17)
        curves = samples.reshape(512, 2, 5)
        residual = np.eye(5)-spec.design_matrix @ np.linalg.pinv(spec.design_matrix)
        self.assertLess(np.linalg.norm(curves @ residual.T)/np.linalg.norm(curves), 1e-12)
        evaluated = BeamEmittanceAlgorithm(spec).evaluate(samples)
        self.assertEqual(len(evaluated.categories), 512)
        self.assertTrue(np.any(~evaluated.candidate_physical))
        self.assertTrue(np.all(np.isfinite(samples)))

    def test_response_posterior_is_invariant_to_design_column_units(self):
        spec = pool()
        reparameterized = replace(spec, design_matrix=spec.design_matrix @ np.diag([1000, 0.01, -2]))
        observations = [PointObservation(0, 1, 0.2), PointObservation(7, 2, 0.1)]
        first = TransportMomentGP(spec, InfoBAXConfig()).fit(observations)
        second = TransportMomentGP(reparameterized, InfoBAXConfig()).fit(observations)
        np.testing.assert_allclose(first.mean, second.mean, atol=1e-12)
        np.testing.assert_allclose(first.covariance, second.covariance, atol=1e-12)

    def test_duplicates_and_invalid_sampling_requests_fail_explicitly(self):
        model = TransportMomentGP(pool(), InfoBAXConfig())
        observation = PointObservation(2, 1, 0.1)
        with self.assertRaises(ValueError): model.fit([])
        with self.assertRaises(ValueError): model.fit([observation, observation])
        with self.assertRaises(IndexError): model.fit([PointObservation(10, 1, 0.1)])
        posterior = model.fit([observation])
        with self.assertRaises(ValueError): posterior.sample(0, 1)
        with self.assertRaises(ValueError): posterior.sample(True, 1)

    def test_per_frame_uncertainty_limits_a_noisy_scan_point(self):
        spec = pool()
        truth = np.tile(spec.design_matrix @ np.array([1.0, 0.1, 0.3]), 2)
        observations = [PointObservation(i, float(v+(0.5 if i == 4 else 0)), 1.0 if i == 4 else 0.001)
                        for i, v in enumerate(truth)]
        weighted = TransportMomentGP(spec, InfoBAXConfig()).fit(observations)
        equal = TransportMomentGP(spec, InfoBAXConfig()).fit([PointObservation(o.query_index, o.variance_mm2, 0.001) for o in observations])
        self.assertLess(np.linalg.norm(weighted.mean-truth), np.linalg.norm(equal.mean-truth)/10)

    def test_default_core_does_not_select_the_experimental_model(self):
        spec = small_pool()
        core = InfoBAXCore(spec)
        values = np.tile([2.0, 1.0, 2.0], 2)
        for index in core.initial_design: core.observe(PointObservation(index, values[index], 0.1))
        expected = core.recommend()
        with patch.object(TransportMomentGP, "fit", side_effect=AssertionError("must remain opt-in")):
            self.assertEqual(core.recommend(), expected)

    def test_explicit_model_initialization_and_checkpoint_round_trip(self):
        spec = pool()
        config = InfoBAXConfig(initial_actions=3, summary_samples=64)
        core = InfoBAXCore(spec, config, model="transport_moments", initial_queries=(0, 4, 7))
        truth = np.tile(spec.design_matrix @ np.array([1., .1, .3]), 2)
        for index in (0, 4, 7):
            self.assertEqual(core.propose().query_index, index)
            core.observe(PointObservation(index, float(truth[index]), .01))
        restored = InfoBAXCore.from_checkpoint(spec, core.to_checkpoint())
        self.assertEqual(restored.model, "transport_moments")
        self.assertEqual(restored.initial_design, (0, 4, 7))
        self.assertEqual(restored.recommend(), core.recommend())


if __name__ == "__main__":
    unittest.main()
