"""Noise split integrity, inherited scaling and fail-closed B6 acceptance."""

import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts import calibrate_emit_infobax_100k_noise as b6
from tests.infobax_research_fixtures import FROZEN_INPUTS, use_frozen_inputs
from unittest.mock import patch

import numpy as np

from scripts.calibrate_emit_infobax_100k_noise import (
    DEFAULT_CONFIG, frames_complete, prior_particle_seeds, run, summarize, unit_noise_rows, validate_config,
)
from scripts.calibrate_emit_infobax_weighted_rms import rescale


class HundredKNoiseTests(unittest.TestCase):
    def setUp(self):
        use_frozen_inputs(self, b6)
        self.config = json.loads(DEFAULT_CONFIG.read_text())

    def small_config(self):
        return dict(self.config, cases=[{"id": "unit"}], workpoints=[0], scan_values=[0],
                    repeat_workpoints=[0], repeat_scan_values=[0])

    @staticmethod
    def row(seed, value=0.1, scale=1.0):
        return dict(case="unit", workpoint=0, scan_value=0, particle_seed=seed, arm="native_100k",
                    status="observed", variance_mm2=value, corrected_variance_mm2=value,
                    noise_std_mm2=0.01*scale, variance_relative_error=0.001,
                    corrected_variance_relative_error=0.001,
                    measurement=dict(noise_scale=scale, noise_std_mm2=0.01*scale))

    def test_new_seeds_and_unchanged_selected_setting_are_required(self):
        previous = prior_particle_seeds()
        validate_config(self.config, previous)
        for group, value in (("calibration_particle_seeds", 12345),
                             ("validation_particle_seeds", self.config["calibration_particle_seeds"][0])):
            broken = copy.deepcopy(self.config)
            broken[group][0] = value
            with self.assertRaises(ValueError):
                validate_config(broken, previous)
        for key, value in (("particles", 20000), ("linear_resolution_factors", [2])):
            with self.assertRaises(ValueError):
                validate_config(dict(self.config, **{key: value}), previous)
        broken = copy.deepcopy(self.config)
        broken["calibration_gates"]["heldout_standardized_mean_square_range"] = [0.1, 10]
        with self.assertRaises(ValueError):
            validate_config(broken, previous)

    def test_old_scale_is_removed_before_applying_new_fit(self):
        source = self.row(12345, scale=0.8063974059901406)
        old = copy.deepcopy(source)
        unit = unit_noise_rows([source])
        self.assertEqual(source, old)
        self.assertAlmostEqual(unit[0]["noise_std_mm2"], 0.01)
        new = rescale(unit, 0.7)[0]
        self.assertAlmostEqual(new["noise_std_mm2"], 0.007)
        self.assertEqual(new["variance_mm2"], source["variance_mm2"])
        self.assertEqual(new["measurement"]["noise_scale"], 0.7)

    def test_duplicate_populations_cannot_replace_missing_ones(self):
        config = self.small_config()
        seeds = config["validation_particle_seeds"]
        rows = [self.row(seed) for seed in seeds]
        self.assertTrue(frames_complete(rows, config, seeds, [0], [0]))
        rows[-1] = copy.deepcopy(rows[0])
        self.assertFalse(frames_complete(rows, config, seeds, [0], [0]))

    def test_noise_score_and_incomplete_grid_are_checked_separately(self):
        config = self.small_config()
        primary = [self.row(12345)]
        training = [self.row(seed) for seed in config["calibration_particle_seeds"]]
        validation = [self.row(seed, value=0.1+(-1 if i % 2 else 1)*0.01)
                      for i, seed in enumerate(config["validation_particle_seeds"])]
        references = [dict(case="unit", workpoint=0, status="valid", relative_error=0.01)]
        with patch("scripts.calibrate_emit_infobax_100k_noise.reference_reproduction", return_value={"passed": True}):
            result = summarize(primary, training, validation, references, config)
            self.assertEqual(result["status"], "passed")
            self.assertAlmostEqual(result["heldout_standardized_mean_square"], 8/9)
            self.assertAlmostEqual(result["heldout_standardized_mean"], 0)
            self.assertEqual(result["heldout_95_interval_coverage"], 1)
            validation[-1] = copy.deepcopy(validation[0])
            result = summarize(primary, training, validation, references, config)
            self.assertTrue(result["gates"]["all_frames_valid"])
            self.assertFalse(result["gates"]["validation_grid_complete"])
            self.assertEqual(result["status"], "CALIBRATION-STOP")

    def test_training_fit_is_written_before_any_validation_population(self):
        config = self.small_config()
        primary = self.row(12345, scale=0.8063974059901406)
        training = [self.row(seed, value=0.1+(-1 if i % 2 else 1)*0.01)
                    for i, seed in enumerate(config["calibration_particle_seeds"])]
        validation = [self.row(seed, value=0.1+(-1 if i % 2 else 1)*0.01)
                      for i, seed in enumerate(config["validation_particle_seeds"])]
        reference = dict(case="unit", workpoint=0, status="valid", relative_error=0.01)
        with TemporaryDirectory() as temporary:
            output = Path(temporary)
            def collect(_config, _output, label, seeds):
                if label == "validation":
                    fit = json.loads((output / "noise_scale_fit.json").read_text())
                    expected = np.sqrt(np.var([r["variance_mm2"] for r in training], ddof=1)/0.01**2)
                    self.assertAlmostEqual(fit["noise_scale"], expected)
                    self.assertEqual(fit["particle_seeds"], config["calibration_particle_seeds"])
                return (training if label == "training" else validation), {"unit": {}}, len(seeds)
            with patch("scripts.calibrate_emit_infobax_100k_noise.collect", side_effect=collect), patch(
                "scripts.calibrate_emit_infobax_100k_noise.read_rows", return_value=[primary]
            ), patch("scripts.calibrate_emit_infobax_100k_noise.weighted_references", return_value=[reference]), patch(
                "scripts.calibrate_emit_infobax_100k_noise.reference_reproduction", return_value={"passed": True}
            ):
                run(config, output)
            self.assertEqual(json.loads((output / "summary.json").read_text())["status"], "NOISE-PASS")


if __name__ == "__main__":
    unittest.main()
