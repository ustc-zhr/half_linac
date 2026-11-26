"""Fixed expanded seed budget and correlated-score uncertainty checks."""

import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts import calibrate_emit_infobax_expanded_noise as b7
from tests.infobax_research_fixtures import FROZEN_INPUTS, use_frozen_inputs
from unittest.mock import patch

import numpy as np

from scripts.calibrate_emit_infobax_expanded_noise import (
    DEFAULT_CONFIG, frame_matrices, prior_particle_seeds, run, seed_score_statistics,
    training_seed_bootstrap, validate_config,
)


class ExpandedNoiseTests(unittest.TestCase):
    def setUp(self):
        use_frozen_inputs(self, b7, b7.b6)
        self.config = json.loads(DEFAULT_CONFIG.read_text())

    @staticmethod
    def row(seed, value=0.1):
        return dict(case="unit", workpoint=0, scan_value=0, particle_seed=seed, arm="native_100k",
                    status="observed", variance_mm2=value, corrected_variance_mm2=value,
                    noise_std_mm2=0.01, variance_relative_error=0.001, corrected_variance_relative_error=0.001,
                    measurement=dict(noise_scale=1.0, noise_std_mm2=0.01))

    def small_config(self):
        return dict(self.config, cases=[{"id": "unit"}], workpoints=[0], scan_values=[0],
                    repeat_workpoints=[0], repeat_scan_values=[0])

    def test_all_64_seeds_are_new_and_budget_and_gates_stay_fixed(self):
        previous = prior_particle_seeds()
        validate_config(self.config, previous)
        broken = copy.deepcopy(self.config)
        broken["validation_particle_seeds"][-1] = 1256137035
        with self.assertRaises(ValueError):
            validate_config(broken, previous)
        broken = copy.deepcopy(self.config)
        broken["calibration_particle_seeds"].pop()
        with self.assertRaises(ValueError):
            validate_config(broken, previous)
        broken = copy.deepcopy(self.config)
        broken["calibration_gates"]["minimum_heldout_95_interval_coverage"] = 0.875
        with self.assertRaises(ValueError):
            validate_config(broken, previous)

    def test_perfectly_correlated_points_do_not_narrow_seed_intervals(self):
        scores = np.array([0.0]*30+[3.0]*2)[:, None]
        options = self.config["seed_bootstrap"]
        single = seed_score_statistics(scores, options)
        repeated = seed_score_statistics(np.repeat(scores, 36, axis=1), options)
        self.assertEqual(single["coverage"], repeated["coverage"])
        self.assertAlmostEqual(repeated["coverage"]["estimate"], 0.9375)
        lower, upper = repeated["coverage"]["seed_bootstrap_interval"]
        self.assertLess(lower, 0.9)
        self.assertGreater(upper, lower)
        naive = seed_score_statistics(np.repeat(scores, 36, axis=1).reshape(-1, 1), options)
        a, b = naive["coverage"]["seed_bootstrap_interval"]
        self.assertGreater(upper-lower, 2*(b-a))

    def test_training_uncertainty_resamples_complete_seed_vectors(self):
        config = self.small_config()
        rows = [self.row(seed, 0.1+i*0.001) for i, seed in enumerate(config["calibration_particle_seeds"])]
        single = training_seed_bootstrap(rows, config)
        doubled = rows+[dict(r, scan_value=1) for r in rows]
        twin = training_seed_bootstrap(doubled, dict(config, repeat_scan_values=[0, 1]))
        np.testing.assert_allclose(single["noise_scale_interval"], twin["noise_scale_interval"], rtol=1e-14)
        self.assertEqual(single["seed_count"], 32)

    def test_incomplete_seed_vectors_do_not_produce_filtered_intervals(self):
        config = self.small_config()
        seeds = config["validation_particle_seeds"]
        rows = [self.row(seed) for seed in seeds]
        with self.assertRaises(ValueError):
            frame_matrices(rows[:-1], config, seeds)
        with self.assertRaises(ValueError):
            seed_score_statistics([[float("nan")], [0]], config["seed_bootstrap"])

    def test_fit_is_frozen_before_expanded_validation_starts(self):
        config = self.small_config()
        train = [self.row(seed, 0.1+(-1 if i % 2 else 1)*0.01)
                 for i, seed in enumerate(config["calibration_particle_seeds"])]
        validation = [self.row(seed, 0.1+(-1 if i % 2 else 1)*0.01)
                      for i, seed in enumerate(config["validation_particle_seeds"])]
        reference = dict(case="unit", workpoint=0, status="valid", relative_error=0.01)
        with TemporaryDirectory() as temporary:
            output = Path(temporary)
            def collect(_config, _output, label, seeds):
                fit = output / "noise_scale_fit.json"
                if label == "training":
                    self.assertFalse(fit.exists())
                else:
                    data = json.loads(fit.read_text())
                    self.assertEqual(data["particle_seeds"], config["calibration_particle_seeds"])
                    expected = np.sqrt(np.var([r["variance_mm2"] for r in train], ddof=1)/0.01**2)
                    self.assertAlmostEqual(data["noise_scale"], expected)
                return (train if label == "training" else validation), {"unit": {}}, len(seeds)
            with patch("scripts.calibrate_emit_infobax_expanded_noise.collect", side_effect=collect), patch(
                "scripts.calibrate_emit_infobax_expanded_noise.read_rows", return_value=[self.row(12345)]
            ), patch("scripts.calibrate_emit_infobax_expanded_noise.weighted_references", return_value=[reference]), patch(
                "scripts.calibrate_emit_infobax_100k_noise.reference_reproduction", return_value={"passed": True}
            ):
                run(config, output)
            self.assertEqual(json.loads((output / "summary.json").read_text())["status"], "NOISE-PASS")
            self.assertEqual(json.loads((output / "seed_uncertainty.json").read_text())["seed_count"], 32)


if __name__ == "__main__":
    unittest.main()
