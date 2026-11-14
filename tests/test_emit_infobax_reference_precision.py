"""Pairing, field of view, selection and reconstruction tests for P2-B5."""

import copy
import json
import unittest

from scripts import validate_emit_infobax_reference_precision as b5
from tests.infobax_research_fixtures import use_frozen_inputs

import numpy as np

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

from scripts.validate_emit_infobax_reference_precision import (
    DEFAULT_CONFIG, arm_summary, choose_primary, rasterize_particles, reconstruct_arm,
    reproduction_check, resolution_geometry, validate_config,
)
from scripts.diagnose_emit_infobax_measurement import normalized_emittance
from half_linac.src.shared.machine_profile.pixel_geometry import FlagPixelGeometry


class ReferencePrecisionTests(unittest.TestCase):
    def setUp(self):
        use_frozen_inputs(self, b5)

    def test_resolution_preserves_field_and_particle_counts(self):
        geometry = FlagPixelGeometry((120, 80), 0.04)
        fine = resolution_geometry(geometry, 2)
        self.assertEqual(np.array(geometry.shape).tolist(), (np.array(fine.shape)/2).tolist())
        self.assertEqual(geometry.shape[0]*geometry.pixel_width_mm, fine.shape[0]*fine.pixel_width_mm)
        rng = np.random.default_rng(17)
        x, y = rng.uniform(-0.0009, 0.0009, (2, 10000))
        native_image = rasterize_particles(x, y, geometry)
        fine_image = rasterize_particles(x, y, fine)
        np.testing.assert_array_equal(native_image, fine_image.reshape(80, 2, 120, 2).sum(axis=(1, 3)))
        self.assertEqual(native_image.sum(), 10000)
        with self.assertRaises(ValueError):
            resolution_geometry(geometry, 0)

    def test_seed_leakage_or_duplicate_arms_fail_validation(self):
        config = json.loads(DEFAULT_CONFIG.read_text())
        validate_config(config)
        broken = copy.deepcopy(config)
        broken["validation_particle_seeds"][0] = broken["primary_particle_seed"]
        with self.assertRaises(ValueError):
            validate_config(broken)
        broken = copy.deepcopy(config)
        broken["particle_counts"][1] = 20001
        with self.assertRaises(ValueError):
            validate_config(broken)

    def test_selection_uses_predeclared_primary_order(self):
        config = json.loads(DEFAULT_CONFIG.read_text())
        primary = {name: {"passed": True} for name in config["selection_order"]}
        self.assertEqual(choose_primary(primary, config["selection_order"]), "native_100k")
        primary["native_100k"]["passed"] = False
        self.assertEqual(choose_primary(primary, config["selection_order"]), "fine_20k")
        for row in primary.values():
            row["passed"] = False
        self.assertIsNone(choose_primary(primary, config["selection_order"]))

    def test_missing_baseline_cannot_pass_reproduction(self):
        config = json.loads(DEFAULT_CONFIG.read_text())
        self.assertFalse(reproduction_check([], [], config)["passed"])

    def test_weighted_reference_matches_entrance_particles_and_retains_failed_scan(self):
        moments = np.array([4e-8, 1e-9, 1e-8])
        truth = normalized_emittance(moments, 441.3)
        projections = {float(k): (float(k), 1.0) for k in (-1, 0, 1)}
        rows = []
        for scan, (a, b) in projections.items():
            variance = float(np.array([a*a, 2*a*b, b*b]) @ moments * 1e6)
            rows.append(dict(case="unit", arm="native_20k", particle_seed=12345, status="observed",
                             workpoint=0, scan_value=scan, variance_mm2=variance, particle_rms=variance,
                             noise_std_mm2=1e-4, entrance_emittance_mm_mrad=truth, entrance_beta_gamma=441.3,
                             variance_relative_error=0.0, image_retention_fraction=1.0))
        config = json.loads(DEFAULT_CONFIG.read_text())
        config.update(cases=[{"id": "unit"}], workpoints=[0], scan_values=[-1, 0, 1])
        reference = reconstruct_arm(rows, projections, config)
        self.assertEqual(reference[0]["status"], "valid")
        self.assertLess(reference[0]["relative_error"], 1e-12)
        summary = arm_summary(rows, reference, config, [12345])
        self.assertTrue(summary["native_20k"]["passed"])
        self.assertFalse(summary["fine_20k"]["passed"])
        rows[0]["status"] = "failed"
        failed = reconstruct_arm(rows, projections, config)
        self.assertEqual(failed[0]["status"], "incomplete_scan")
        self.assertFalse(arm_summary(rows, failed, config, [12345])["native_20k"]["passed"])


if __name__ == "__main__":
    unittest.main()
