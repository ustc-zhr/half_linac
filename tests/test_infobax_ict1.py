import json
import math
import unittest

import numpy as np

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

from half_linac.src.optimization.infobax import (
    DuplicateObservationError, FinitePoolSpec, ICT1Config, ICT1Observation,
    InfoBAXConfig, InfoBAXCore, PointObservation,
)
from half_linac.src.optimization.infobax.acquisition import InfoBAXEstimator
from half_linac.src.optimization.infobax.algorithm import BeamEmittanceAlgorithm
from half_linac.src.optimization.infobax.charge import fit_ict1_posterior
from half_linac.src.optimization.infobax.posterior import GaussianStatePosterior


class ICT1CoreTests(unittest.TestCase):
    def setUp(self):
        scan = np.array([-1.0, 0.0, 1.0])
        design = np.column_stack((np.ones(3), 2 * scan, scan**2))
        self.pool = FinitePoolSpec(("A", "B"), [[0.0], [1.0]], scan, (1 + scan) / 2, design, 100, .001)
        self.curves = np.concatenate((design @ [1, 0, 1], design @ [2, 0, 2]))
        self.config = InfoBAXConfig(initial_actions=4, inner_samples=1024)

    def test_charge_changes_information_ranking_with_identical_beam_posterior(self):
        beam = GaussianStatePosterior(self.curves, np.zeros((6, 6)), np.eye(6), np.full(6, .001))
        algorithm = BeamEmittanceAlgorithm(self.pool, constrain_ict1=True)
        for margins, variance, expected in (([0., 1.], [1., 1e-12], 0), ([-1., 0.], [1e-12, 1.], 1)):
            with self.subTest(expected=expected):
                ict1 = GaussianStatePosterior(np.array(margins), np.diag(variance), np.eye(2), np.full(2, .001))
                estimator = InfoBAXEstimator(beam, algorithm, self.config, seed=37,
                                             ict1_posterior=ict1, scans_per_candidate=3)
                selected, scores = estimator.select((0, 3))
                self.assertEqual(selected.query_index, expected * 3)
                self.assertAlmostEqual(scores[expected].raw_eig_nats, math.log(2), delta=.015)
                self.assertAlmostEqual(scores[1 - expected].raw_eig_nats, 0.0, places=10)

    def test_invalid_and_no_feasible_are_distinct(self):
        algorithm = BeamEmittanceAlgorithm(self.pool, constrain_ict1=True)
        curves = np.vstack((self.curves, -self.curves, self.curves))
        charge = np.array([[-1., -1.], [1., 1.], [-1., 0.]])
        samples = algorithm.evaluate(curves, charge)
        np.testing.assert_array_equal(samples.categories, [algorithm.no_feasible_category, algorithm.invalid_category, 1])
        summary = algorithm.summarize(curves, charge, recommendation_invalid_threshold=.5)
        self.assertIsNone(summary.recommended_candidate)
        self.assertAlmostEqual(summary.invalid_output_probability, 1/3)
        self.assertAlmostEqual(summary.no_feasible_probability, 1/3)

    def test_deterministically_feasible_charge_recovers_original_eig(self):
        beam = GaussianStatePosterior(self.curves, np.eye(6) * .2, np.eye(6), np.full(6, .01))
        charge = GaussianStatePosterior(np.ones(2), np.eye(2) * 1e-12, np.eye(2), np.full(2, .001))
        original = InfoBAXEstimator(beam, BeamEmittanceAlgorithm(self.pool), self.config, seed=17)
        constrained = InfoBAXEstimator(beam, BeamEmittanceAlgorithm(self.pool, constrain_ict1=True), self.config,
                                       seed=17, ict1_posterior=charge, scans_per_candidate=3)
        selected, scores = original.select(range(6))
        self.assertEqual((selected, scores), constrained.select(range(6)))

    def test_repeated_workpoint_windows_and_checkpoint_for_both_models(self):
        for model in ("M0", "transport_moments"):
            with self.subTest(model=model):
                core = InfoBAXCore(self.pool, self.config, model=model, initial_queries=(0, 1, 2, 3),
                                   ict1=ICT1Config(1.0, .95, .003))
                for index in core.initial_design:
                    core.observe(PointObservation(index, self.curves[index], .001))
                first = ICT1Observation("a", 0, .88, 0)
                core.observe_ict1(first)
                with self.assertRaises(DuplicateObservationError):
                    core.observe_ict1(first)
                core.observe_ict1(ICT1Observation("b", 0, .90, .001))
                core.observe_ict1(ICT1Observation("c", 1, 1., .001))
                posterior = fit_ict1_posterior(self.pool, core.ict1, core.ict1_observations)
                self.assertLess(posterior.mean[0], 0.)
                self.assertGreater(posterior.mean[1], 0.)
                saved = json.loads(json.dumps(core.to_checkpoint()))
                self.assertEqual(saved["schema"], "half_infobax_checkpoint_v3")
                restored = InfoBAXCore.from_checkpoint(self.pool, saved)
                self.assertEqual(restored.to_checkpoint(), saved)
                self.assertEqual(restored.recommend(), core.recommend())
                self.assertEqual(restored.propose(), core.propose())

    def test_absolute_minimum_changes_feasible_margin_and_survives_checkpoint(self):
        observation = ICT1Observation("a", 0, .419, .0001)
        relative = ICT1Config(.44, .95, .003)
        absolute = ICT1Config(.44, None, .003, absolute_minimum_nc=.42)
        self.assertNotIn("absolute_minimum_nc", relative.to_dict())
        self.assertGreater(fit_ict1_posterior(self.pool, relative, (observation,)).mean[0], 0)
        self.assertLess(fit_ict1_posterior(self.pool, absolute, (observation,)).mean[0], 0)
        core = InfoBAXCore(self.pool, self.config, ict1=absolute)
        core.observe_ict1(observation)
        saved = json.loads(json.dumps(core.to_checkpoint()))
        restored = InfoBAXCore.from_checkpoint(self.pool, saved)
        self.assertEqual(restored.ict1.minimum_nc, .42)
        self.assertEqual(restored.to_checkpoint(), saved)
