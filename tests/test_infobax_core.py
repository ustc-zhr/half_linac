"""Offline production-kernel tests. No IOC, GUI, or machine is contacted."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from repo_bootstrap import ensure_repo_import_path

REPO_ROOT = ensure_repo_import_path(__file__)

from half_linac.src.optimization.infobax import (  # noqa: E402
    CheckpointError,
    DuplicateObservationError,
    FinitePoolSpec,
    InfoBAXConfig,
    InfoBAXCore,
    InsufficientDataError,
    PointObservation,
    PoolExhaustedError,
)
from half_linac.src.optimization.infobax.acquisition import (  # noqa: E402
    InfoBAXEstimator,
    binary_sign_information_quadrature,
)
from half_linac.src.optimization.infobax.algorithm import (  # noqa: E402
    BeamEmittanceAlgorithm,
)
from half_linac.src.optimization.infobax.posterior import (  # noqa: E402
    GaussianStatePosterior,
)
from half_linac.src.optimization.infobax.types import EIGDiagnostic  # noqa: E402


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "infobax_m0_golden.json"


def golden_pool(payload):
    return FinitePoolSpec(**payload["pool"])


def small_pool(candidate_count=2, scan_count=3):
    if scan_count != 3:
        raise ValueError("test helper currently uses a three-row design")
    return FinitePoolSpec(
        candidate_ids=tuple(f"candidate-{index}" for index in range(candidate_count)),
        candidate_coordinates=np.linspace(0.0, 1.0, candidate_count)[:, None],
        scan_values=np.asarray((-1.0, 0.0, 1.0)),
        scan_coordinates=np.asarray((0.0, 0.5, 1.0)),
        design_matrix=np.asarray(
            ((1.0, -2.0, 1.0), (1.0, 0.0, 0.0), (1.0, 2.0, 1.0))
        ),
        energy_mev=225.0,
        observation_noise_std_mm2=0.1,
    )


def diagnostic(query_index, value):
    return EIGDiagnostic(
        query_index=query_index,
        raw_eig_nats=value,
        eig_standard_error_nats=0.02,
        prior_entropy_nats=0.2,
        posterior_entropy_mean_nats=0.2 - value,
        direct_prior_entropy_nats=0.2,
        prior_probability_l1=0.0,
        negative_estimate=value < 0.0,
    )


class GoldenBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        cls.pool = golden_pool(cls.payload)
        cls.config = InfoBAXConfig(seed=0)

    def populated_core(self):
        core = InfoBAXCore(self.pool, self.config)
        for row in self.payload["observations"]:
            core.observe(PointObservation(**row))
        return core

    def test_frozen_research_baseline_matches_production_kernel(self):
        self.assertEqual(
            self.payload["source_commit"],
            "7c67b0a8a14e5fba167a679d56ed6ab8447edc70",
        )
        expected = self.payload["expected"]
        core = InfoBAXCore(self.pool, self.config)
        self.assertEqual(core.initial_design, tuple(expected["initial_design"]))
        for row in self.payload["observations"]:
            suggestion = core.propose()
            self.assertEqual(suggestion.phase, "initial")
            self.assertEqual(suggestion.query_index, row["query_index"])
            core.observe(PointObservation(**row))

        recommendation = core.recommend()
        self.assertEqual(
            recommendation.recommended_candidate_index,
            expected["recommended_candidate_index"],
        )
        np.testing.assert_allclose(
            recommendation.best_probabilities,
            expected["best_probabilities"],
            rtol=1e-10,
            atol=1e-12,
        )
        np.testing.assert_allclose(
            recommendation.candidate_physical_probabilities,
            expected["candidate_physical_probabilities"],
            rtol=1e-10,
            atol=1e-12,
        )
        self.assertAlmostEqual(
            recommendation.invalid_output_probability,
            expected["invalid_output_probability"],
            places=12,
        )
        self.assertAlmostEqual(
            recommendation.all_candidates_physical_probability,
            expected["all_candidates_physical_probability"],
            places=12,
        )
        self.assertAlmostEqual(
            recommendation.entropy_nats, expected["entropy_nats"], places=12
        )

        suggestion = core.propose()
        self.assertEqual(suggestion.phase, "infobax")
        self.assertEqual(
            suggestion.query_index, expected["selected_query_index"]
        )
        self.assertAlmostEqual(
            suggestion.raw_eig_nats, expected["raw_eig_nats"], places=12
        )
        self.assertAlmostEqual(
            suggestion.eig_standard_error_nats,
            expected["eig_standard_error_nats"],
            places=12,
        )
        self.assertAlmostEqual(
            suggestion.negative_eig_fraction,
            expected["negative_eig_fraction"],
            places=12,
        )

    def test_fixed_state_is_exactly_reproducible(self):
        first = self.populated_core()
        second = self.populated_core()
        self.assertEqual(first.recommend(), second.recommend())
        self.assertEqual(first.propose(), second.propose())


class LifecycleTests(unittest.TestCase):
    def test_initial_design_is_unique_and_observed_queries_are_never_repeated(self):
        pool = small_pool(candidate_count=3)
        core = InfoBAXCore(pool, InfoBAXConfig(initial_actions=6))
        self.assertEqual(len(set(core.initial_design)), 6)
        issued = []
        for step in range(6):
            suggestion = core.propose()
            self.assertNotIn(suggestion.query_index, issued)
            issued.append(suggestion.query_index)
            core.observe(
                PointObservation(suggestion.query_index, 2.0 + 0.1 * step, 0.1)
            )
        self.assertEqual(issued, list(core.initial_design))

    def test_recommendation_requires_complete_initial_design(self):
        core = InfoBAXCore(small_pool(), InfoBAXConfig(initial_actions=3))
        with self.assertRaisesRegex(InsufficientDataError, "initial design"):
            core.recommend()

    def test_duplicate_out_of_range_and_nonfinite_observations_fail(self):
        core = InfoBAXCore(small_pool(), InfoBAXConfig(initial_actions=1))
        core.observe(PointObservation(0, 1.0, 0.1))
        with self.assertRaises(DuplicateObservationError):
            core.observe(PointObservation(0, 1.1, 0.1))
        with self.assertRaises(IndexError):
            core.observe(PointObservation(core.pool.query_count, 1.0, 0.1))
        with self.assertRaises(ValueError):
            PointObservation(1, float("nan"), 0.1)
        with self.assertRaises(ValueError):
            PointObservation(1, 1.0, 0.0)

    def test_pool_exhaustion_is_explicit(self):
        pool = small_pool()
        core = InfoBAXCore(pool, InfoBAXConfig(initial_actions=pool.query_count))
        while core.available_indices:
            suggestion = core.propose()
            core.observe(PointObservation(suggestion.query_index, 2.0, 0.1))
        with self.assertRaises(PoolExhaustedError):
            core.propose()

    def test_negative_selected_eig_is_preserved_and_degraded(self):
        pool = small_pool()
        core = InfoBAXCore(pool, InfoBAXConfig(initial_actions=1))
        first = core.propose()
        core.observe(PointObservation(first.query_index, 2.0, 0.1))
        rows = (diagnostic(0, -0.1), diagnostic(1, -0.2))
        selected_index = next(
            index for index in core.available_indices if index != first.query_index
        )
        selected = diagnostic(selected_index, -0.1)
        rows = (selected, diagnostic(core.available_indices[-1], -0.2))
        with patch(
            "half_linac.src.optimization.infobax.core.InfoBAXEstimator"
        ) as estimator_class:
            estimator_class.return_value.select.return_value = (selected, rows)
            suggestion = core.propose()
        self.assertEqual(suggestion.status, "degraded_negative_eig")
        self.assertEqual(suggestion.raw_eig_nats, -0.1)
        self.assertEqual(suggestion.negative_eig_fraction, 1.0)

    def test_nonphysical_samples_remain_explicit_invalid_mass(self):
        pool = small_pool(candidate_count=1)
        algorithm = BeamEmittanceAlgorithm(pool)
        curves = np.asarray(((2.0, 1.0, 2.0), (0.0, -1.0, 0.0)))
        evaluated = algorithm.evaluate(curves)
        np.testing.assert_array_equal(evaluated.categories, (0, 1))
        summary = algorithm.summarize(
            curves, recommendation_invalid_threshold=0.5
        )
        self.assertEqual(summary.invalid_output_probability, 0.5)
        self.assertIsNone(summary.recommended_candidate)


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.pool = small_pool()
        self.core = InfoBAXCore(self.pool, InfoBAXConfig(initial_actions=2, seed=7))
        for step in range(2):
            suggestion = self.core.propose()
            self.core.observe(
                PointObservation(suggestion.query_index, 1.5 + step, 0.1)
            )

    def test_json_round_trip_restores_only_source_state(self):
        payload = json.loads(json.dumps(self.core.to_checkpoint(), allow_nan=False))
        self.assertNotIn("posterior", payload)
        restored = InfoBAXCore.from_checkpoint(self.pool, payload)
        self.assertEqual(restored.observations, self.core.observations)
        self.assertEqual(restored.initial_design, self.core.initial_design)
        self.assertEqual(restored.recommend(), self.core.recommend())

    def test_schema_and_pool_mismatch_are_rejected(self):
        payload = self.core.to_checkpoint()
        changed_schema = dict(payload, schema="future")
        with self.assertRaisesRegex(CheckpointError, "schema"):
            InfoBAXCore.from_checkpoint(self.pool, changed_schema)
        changed_pool = FinitePoolSpec(
            candidate_ids=self.pool.candidate_ids,
            candidate_coordinates=self.pool.candidate_coordinates,
            scan_values=self.pool.scan_values,
            scan_coordinates=self.pool.scan_coordinates,
            design_matrix=self.pool.design_matrix,
            energy_mev=226.0,
            observation_noise_std_mm2=0.1,
        )
        with self.assertRaisesRegex(CheckpointError, "fingerprint"):
            InfoBAXCore.from_checkpoint(changed_pool, payload)


class NumericalEstimatorTests(unittest.TestCase):
    class BinarySignAlgorithm:
        category_count = 2

        def __init__(self, state_index=0):
            self.state_index = state_index

        def evaluate(self, samples):
            return SimpleNamespace(
                categories=(samples[:, self.state_index] >= 0.0).astype(int)
            )

    class ConstantAlgorithm:
        category_count = 1

        def evaluate(self, samples):
            return SimpleNamespace(categories=np.zeros(len(samples), dtype=int))

    @staticmethod
    def posterior(noise, dimension=2):
        return GaussianStatePosterior(
            mean=np.zeros(dimension),
            covariance=np.eye(dimension),
            observation_matrix=np.eye(dimension),
            observation_noise_std=np.full(dimension, noise),
        )

    @staticmethod
    def numerical_config(inner_samples=1024):
        return InfoBAXConfig(
            initial_actions=1,
            outer_fantasies=24,
            inner_samples=inner_samples,
            batch_count=4,
            direct_prior_samples=2048,
            summary_samples=16,
        )

    def test_constant_algorithm_information_is_zero(self):
        result = InfoBAXEstimator(
            self.posterior(0.1),
            self.ConstantAlgorithm(),
            self.numerical_config(64),
            seed=7,
        ).estimate(0)
        self.assertAlmostEqual(result.raw_eig_nats, 0.0, places=12)

    def test_binary_reference_error_is_below_frozen_gate(self):
        reference = binary_sign_information_quadrature(0.1)
        estimate = InfoBAXEstimator(
            self.posterior(0.1),
            self.BinarySignAlgorithm(0),
            self.numerical_config(),
            seed=37,
        ).estimate(0)
        self.assertLess(abs(estimate.raw_eig_nats - reference), 0.015)

    def test_relevant_query_beats_variance_matched_nuisance(self):
        estimator = InfoBAXEstimator(
            self.posterior(0.08),
            self.BinarySignAlgorithm(0),
            self.numerical_config(512),
            seed=13,
        )
        relevant = estimator.estimate(0)
        nuisance = estimator.estimate(1)
        self.assertGreater(relevant.raw_eig_nats - nuisance.raw_eig_nats, 0.05)

    def test_information_decreases_with_observation_noise(self):
        estimates = []
        for noise in (0.1, 0.5, 1.0):
            estimates.append(
                InfoBAXEstimator(
                    self.posterior(noise, dimension=1),
                    self.BinarySignAlgorithm(0),
                    self.numerical_config(),
                    seed=41,
                ).estimate(0).raw_eig_nats
            )
        self.assertGreater(estimates[0], estimates[1])
        self.assertGreater(estimates[1], estimates[2])


class IsolationTests(unittest.TestCase):
    def test_import_has_no_epics_gui_gotacc_or_research_side_effects(self):
        script = """
import json, sys
import half_linac.src.optimization.infobax
blocked = [name for name in sys.modules if (
    name == 'epics' or name.startswith('epics.') or
    name == 'PyQt5' or name.startswith('PyQt5.') or
    name == 'GOTAcc' or name.startswith('GOTAcc.') or
    name == 'research' or name.startswith('research.')
)]
print(json.dumps(blocked))
"""
        environment = os.environ.copy()
        import_root = str(REPO_ROOT / "scripts" / "python_imports")
        environment["PYTHONPATH"] = os.pathsep.join(
            [import_root, environment.get("PYTHONPATH", "")]
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(json.loads(result.stdout), [])


if __name__ == "__main__":
    unittest.main()

