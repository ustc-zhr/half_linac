"""Accounting, pairing and failure tests for the bounded engineering benchmark."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import numpy as np

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

from scripts.benchmark_emit_infobax import FinitePoolEvaluator, paired_summary, run_policy
from scripts.check_emit_infobax_ioc import isolated_profile, private_environment, validate_measurement_options
from half_linac.src.optimization.infobax import FinitePoolSpec, InfoBAXConfig
from half_linac.src.shared.machine_profile import load_profile


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.pool = FinitePoolSpec(
            candidate_ids=("a", "b", "c"), candidate_coordinates=np.array([[0.0], [0.5], [1.0]]),
            scan_values=np.array([-1, 0, 1]), scan_coordinates=np.array([0.0, 0.5, 1.0]),
            design_matrix=np.array([[1, -2, 1], [1, 0, 0], [1, 2, 1]]),
            energy_mev=225, observation_noise_std_mm2=0.01,
        )
        self.truth = np.array([[2, 1, 2], [3, 2, 3], [4, 3, 4]])
        self.config = InfoBAXConfig(seed=0, outer_fantasies=4, inner_samples=16,
                                   batch_count=2, direct_prior_samples=16, summary_samples=16)

    def evaluator(self, truth=None):
        return FinitePoolEvaluator(self.pool, self.truth if truth is None else truth,
                                   [1, 2, 3], case_id="case", seed=0)

    def test_queries_are_unique_and_policies_share_identical_initial_data(self):
        rows = [run_policy(self.pool, self.config, 7, policy, self.evaluator(), case_id="case")
                for policy in ("infobax", "random")]
        for row in rows:
            self.assertEqual(row["status"], "completed", row["error"])
            self.assertEqual(row["observations"], 7)
            self.assertEqual(len(set(row["attempted_query_indices"])), 7)
        self.assertEqual(
            [(q["query_index"], q["variance_mm2"]) for q in rows[0]["queries"][:6]],
            [(q["query_index"], q["variance_mm2"]) for q in rows[1]["queries"][:6]],
        )

    def test_random_query_trace_does_not_depend_on_hidden_reference_values(self):
        traces = []
        for truth in (self.truth, self.truth * 2):
            row = run_policy(self.pool, self.config, 8, "random", self.evaluator(truth), case_id="case")
            traces.append(row["attempted_query_indices"])
        self.assertEqual(traces[0], traces[1])

    def test_query_indexed_measurements_do_not_depend_on_reveal_order(self):
        first, second = self.evaluator(), self.evaluator()
        first.reveal(0)
        self.assertEqual(first.reveal(3), second.reveal(3))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            first.reveal(3)
        with self.assertRaises((ValueError, IndexError)):
            second.reveal(-1)

    def test_nonpositive_measurement_is_a_retained_failure_without_retry(self):
        evaluator = self.evaluator(-np.ones_like(self.truth))
        row = run_policy(self.pool, self.config, 7, "random", evaluator, case_id="case")
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["observations"], 0)
        self.assertEqual(len(row["attempted_query_indices"]), 1)
        self.assertIsNone(row["relative_regret"])

    def test_failed_pairs_cannot_be_hidden_by_conditional_regret(self):
        row = dict(case="case", seed=0, status="completed", observations=7,
                   queries=[dict(query_index=i, selection_seconds=0.1) for i in range(7)],
                   initial_summary={"entropy_nats": 1.0}, recommendation={
                       "entropy_nats": 0.5, "all_candidates_physical_probability": 1.0,
                   }, best_hit=False)
        rows = [{**row, "policy": "infobax", "relative_regret": 0.0},
                {**row, "policy": "random", "relative_regret": None, "recommendation": None}]
        config = dict(seeds=[0], cases=[{"id": "case"}], policies=["infobax", "random"],
                      bootstrap_seed=0, bootstrap_samples=20, total_observations=7, initial_observations=6,
                      gates={"minimum_recommendation_rate_per_policy": 0.95,
                             "paired_regret_difference_upper_95_ci_strictly_below": 0.0})
        result = paired_summary(rows, config)
        self.assertEqual(result["complete_paired_seeds"], 0)
        self.assertFalse(result["gates"]["regret_superiority"])
        self.assertFalse(result["gates"]["recommendation_availability"])
        self.assertEqual(result["policies"]["random"]["recommendation_rate"], 0.0)


class IsolatedIocTests(unittest.TestCase):
    def test_fitted_scale_is_only_allowed_for_count_measurements(self):
        validate_measurement_options(100000, 0.6902834680118383, "rms_count_variance")
        validate_measurement_options(10000, 1.0, "gaussian_fit")
        for particles, scale, method in ((True, 1.0, "rms_count_variance"),
                                        (0, 1.0, "rms_count_variance"),
                                        (100000, float("nan"), "rms_count_variance"),
                                        (100000, 0.0, "rms_count_variance"),
                                        (100000, 0.69, "gaussian_fit")):
            with self.assertRaises(ValueError):
                validate_measurement_options(particles, scale, method)

    def test_environment_removes_ambient_discovery_and_name_servers(self):
        with patch.dict(os.environ, {"EPICS_CA_NAME_SERVERS": "remote", "EPICS_CA_ADDR_LIST": "remote"}):
            env = private_environment(5100, 5101)
        self.assertNotIn("EPICS_CA_NAME_SERVERS", env)
        self.assertEqual(env["EPICS_CA_ADDR_LIST"], "127.0.0.1:5100")
        self.assertEqual(env["EPICS_CAS_INTF_ADDR_LIST"], "127.0.0.1")
        self.assertEqual(env["EPICS_CA_AUTO_ADDR_LIST"], "NO")

    def test_private_mapping_preserves_missing_vm_channels(self):
        original = load_profile("half")
        private = isolated_profile(original, "INFOBAX:TEST:unit:")
        for before, after in zip(original.elements, private.elements):
            for channel, modes in before.channels.items():
                self.assertEqual("vm" in modes, "vm" in after.channels[channel])
                if "vm" in modes:
                    self.assertTrue(after.channels[channel]["vm"].startswith("INFOBAX:TEST:unit:"))
                self.assertNotIn("real", after.channels[channel])


if __name__ == "__main__":
    unittest.main()
