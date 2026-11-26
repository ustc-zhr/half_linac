"""Runtime prerequisites, actual-frame pairing and benchmark accounting."""

import copy
import json
import unittest

from scripts import benchmark_emit_infobax_calibrated as b8
from tests.infobax_research_fixtures import FROZEN_INPUTS, use_frozen_inputs
from scripts import calibrate_emit_infobax_expanded_noise as b7

from scripts.benchmark_emit_infobax_calibrated import (
    DEFAULT_CONFIG, PairedFrames, comparison_summary, frame_seed,
    initial_pair_equal, seed_schedule, validate_config,
)
from scripts.calibrate_emit_infobax_rms import PositiveFrameEvaluator
from tests.test_infobax_core import small_pool


class CalibratedBenchmarkTests(unittest.TestCase):
    def setUp(self):
        use_frozen_inputs(self, b8, b7, b7.b6)
        self.config = json.loads(DEFAULT_CONFIG.read_text())
        scale = json.loads((FROZEN_INPUTS / "p2_b7/noise_scale_fit.json").read_text())["noise_scale"]
        self.ioc = dict(status="passed", measurement_method="rms_count_variance", particles=100000,
                        noise_scale=scale, observations=7, stale_observations=0, paused_and_resumed=True,
                        restored=True, stale_frame_rejected=True, incompatible_scale_checkpoint_rejected=True,
                        processes_cleaned_up=True, prefix="INFOBAX:TEST:unit:", ca_address="127.0.0.1:12345",
                        live_frame_audits=[dict(query_index=i, count=100000, noise_scale=scale,
                                               unscaled_noise_std_mm2=0.001, scaled_noise_std_mm2=0.001*scale)
                                           for i in range(7)])

    def test_runtime_settings_budget_gates_and_seed_schedule_are_fixed(self):
        validate_config(self.config, self.ioc)
        planned = seed_schedule(self.config)
        self.assertEqual(len(planned), 2*16*63)
        self.assertEqual(len({r["particle_seed"] for r in planned}), len(planned))
        self.assertTrue(all(0 < r["particle_seed"] < 2000000000 and r["particle_seed"] % 2 for r in planned))
        with self.assertRaises(ValueError):
            validate_config(dict(self.config, total_observations=15), self.ioc)
        with self.assertRaises(ValueError):
            validate_config(self.config, dict(self.ioc, incompatible_scale_checkpoint_rejected=False))
        with self.assertRaises(ValueError):
            validate_config(self.config, dict(self.ioc, particles=10000))
        broken = copy.deepcopy(self.config)
        broken["gates"]["minimum_recommendation_rate_per_policy"] = 0.5
        with self.assertRaises(ValueError):
            validate_config(broken, self.ioc)

    def test_policies_reuse_identical_actual_frames_for_a_common_query(self):
        class Beam:
            def __init__(self): self.calls = []
            def measure(self, work, scan, seed):
                self.calls.append((work, scan, seed))
                return dict(status="observed", corrected_variance_mm2=0.1, noise_std_mm2=0.001)
        beam = Beam()
        frames = PairedFrames(beam, dict(workpoints=[-1, 0, 1], scan_values=[-1, 0, 1]), "unit")
        first = frames.reveal(3, 4)
        self.assertIs(first, frames.reveal(3, 4))
        frames.reveal(4, 4)
        self.assertEqual(beam.calls, [(0, 0, frame_seed("unit", 3, 4)), (0, 0, frame_seed("unit", 4, 4))])

    def test_shared_quality_failure_is_retained_without_a_second_tracking_attempt(self):
        class Beam:
            calls = 0
            def measure(self, *_args):
                self.calls += 1
                return dict(status="failed", error="underresolved")
        beam = Beam()
        frames = PairedFrames(beam, dict(workpoints=[-1, 0], scan_values=[-1, 0, 1]), "unit")
        for _policy in range(2):
            evaluator = PositiveFrameEvaluator(small_pool(), [1, 2], lambda q: frames.reveal(0, q))
            with self.assertRaisesRegex(ValueError, "underresolved"):
                evaluator.reveal(2)
        self.assertEqual(beam.calls, 1)

    def test_matching_short_prefix_does_not_count_as_six_paired_initial_observations(self):
        queries = [dict(query_index=i, variance_mm2=0.1+i, noise_std_mm2=0.001) for i in range(6)]
        self.assertTrue(initial_pair_equal([dict(queries=queries), dict(queries=copy.deepcopy(queries))], 6))
        self.assertFalse(initial_pair_equal([dict(queries=queries[:3]), dict(queries=queries[:3])], 6))
        changed = copy.deepcopy(queries)
        changed[2]["noise_std_mm2"] = 0.002
        self.assertFalse(initial_pair_equal([dict(queries=queries), dict(queries=changed)], 6))

    def test_duplicate_trajectory_cannot_replace_a_missing_seed_policy(self):
        config = dict(self.config, cases=[{"id": "unit"}], seeds=[0, 1])
        rows = [dict(case="unit", seed=seed, policy=policy, status="completed", observations=14,
                     queries=[dict(query_index=q, selection_seconds=0.1) for q in range(14)],
                     initial_summary={"entropy_nats": 1}, recommendation={"entropy_nats": 0.5,
                     "all_candidates_physical_probability": 1}, relative_regret=float(policy == "random"), best_hit=True)
                for seed in (0, 1) for policy in ("infobax", "random")]
        pairs = [dict(case="unit", seed=seed, paired_initial_equal=True) for seed in (0, 1)]
        self.assertTrue(comparison_summary(rows, pairs, config)["gates"]["trajectory_grid_complete"])
        rows[-1] = copy.deepcopy(rows[1])
        self.assertFalse(comparison_summary(rows, pairs, config)["gates"]["trajectory_grid_complete"])


if __name__ == "__main__":
    unittest.main()
