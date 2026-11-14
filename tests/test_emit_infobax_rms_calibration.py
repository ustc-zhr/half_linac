"""Split integrity, held-out checks and observation pairing for P2-B4."""

import copy
import json
import unittest

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

from scripts.calibrate_emit_infobax_rms import (
    DEFAULT_CONFIG, PositiveFrameEvaluator, calibration_summary, frame_seed, validate_config,
)
from tests.test_infobax_core import small_pool


class RmsCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(DEFAULT_CONFIG.read_text())

    def test_heldout_seeds_cannot_overlap_calibration_or_primary(self):
        validate_config(self.config)
        for seed in (self.config["calibration_particle_seeds"][1], self.config["primary_particle_seed"]):
            broken = copy.deepcopy(self.config)
            broken["validation_particle_seeds"][0] = seed
            with self.assertRaisesRegex(ValueError, "leakage"):
                validate_config(broken)

    def test_duplicate_particle_populations_are_not_independent_repeats(self):
        broken = copy.deepcopy(self.config)
        broken["validation_particle_seeds"][1] = broken["validation_particle_seeds"][0]
        with self.assertRaises(ValueError):
            validate_config(broken)

    def test_frame_seeds_are_positive_odd_and_query_indexed(self):
        seeds = [frame_seed("nonlinear", policy_seed, query) for policy_seed in range(16) for query in range(63)]
        self.assertEqual(len(set(seeds)), len(seeds))
        self.assertTrue(all(0 < seed < 2000000000 and seed % 2 for seed in seeds))
        self.assertEqual(frame_seed("nonlinear", 3, 26), frame_seed("nonlinear", 3, 26))

    def test_positive_frames_are_revealed_without_adding_gaussian_noise(self):
        draws = []
        def draw(index):
            draws.append(index)
            return dict(status="observed", corrected_variance_mm2=0.0123, noise_std_mm2=0.0001)
        evaluator = PositiveFrameEvaluator(small_pool(), [1.0, 2.0], draw)
        observation = evaluator.reveal(3)
        self.assertEqual(observation.variance_mm2, 0.0123)
        self.assertEqual(observation.noise_std_mm2, 0.0001)
        self.assertEqual(draws, [3])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            evaluator.reveal(3)

    def test_quality_failure_is_not_retried_or_replaced(self):
        evaluator = PositiveFrameEvaluator(small_pool(), [1.0, 2.0],
                                           lambda index: dict(status="failed", error="underresolved"))
        with self.assertRaisesRegex(ValueError, "underresolved"):
            evaluator.reveal(0)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            evaluator.reveal(0)

    def test_missing_holdout_frames_cannot_pass_a_calibration(self):
        config = copy.deepcopy(self.config)
        config.update(cases=[{"id": "unit"}], workpoints=[0], scan_values=[0],
                      repeat_workpoints=[0], repeat_scan_values=[0])
        row = dict(case="unit", workpoint=0, scan_value=0, status="observed",
                   corrected_variance_mm2=0.1, noise_std_mm2=0.01, corrected_variance_relative_error=0.001)
        train = [dict(row) for _seed in config["calibration_particle_seeds"]]
        heldout = [dict(row, corrected_variance_mm2=0.1 + (0.01 if index % 2 else -0.01))
                   for index, _seed in enumerate(config["validation_particle_seeds"])]
        references = [dict(status="valid", relative_error=0.001)]
        passed = calibration_summary([row], train, heldout, references, config)
        self.assertEqual(passed["status"], "passed")
        failed = calibration_summary([row], train, heldout[:-1], references, config)
        self.assertFalse(failed["gates"]["all_frames_valid"])
        self.assertEqual(failed["status"], "CALIBRATION-STOP")


if __name__ == "__main__":
    unittest.main()
