"""Weighted reference and training-only global noise-scale checks."""

import copy
import unittest

import numpy as np

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

from scripts.calibrate_emit_infobax_weighted_rms import training_noise_scale, weighted_references
from scripts.diagnose_emit_infobax_measurement import normalized_emittance
from half_linac.src.apps.emit_measure.infobax_measurement import RmsVarianceConfig, measure_count_variance
from tests.test_emit_infobax_rms_variance import count_image


class WeightedRmsTests(unittest.TestCase):
    def test_scale_uses_training_variation_and_no_reference_emittance(self):
        config = dict(cases=[{"id": "unit"}], repeat_workpoints=[0], repeat_scan_values=[0],
                      calibration_particle_seeds=[1, 3, 5, 7], validation_particle_seeds=[9, 11])
        rows = [dict(case="unit", workpoint=0, scan_value=0, status="observed",
                     corrected_variance_mm2=value, noise_std_mm2=2.0) for value in (7, 9, 11, 13)]
        result = training_noise_scale(rows, config)
        self.assertAlmostEqual(result["noise_scale"], np.sqrt(np.var([7, 9, 11, 13], ddof=1)/4))
        config["validation_particle_seeds"] = [999, 1001]
        self.assertEqual(result, training_noise_scale(rows, config))
        with self.assertRaises(ValueError):
            training_noise_scale(rows[:-1], config)

    def test_empirical_scale_changes_uncertainty_only(self):
        image, _x = count_image()
        kwargs = dict(pixel_shape=(120, 80), pixel_width_mm=0.04)
        raw = measure_count_variance(image, **kwargs)
        scaled = measure_count_variance(image, **kwargs, config=RmsVarianceConfig(noise_scale=0.7))
        self.assertEqual(raw.variance_mm2, scaled.variance_mm2)
        self.assertAlmostEqual(scaled.noise_std_mm2/raw.noise_std_mm2, 0.7)
        self.assertEqual(scaled.noise_scale, 0.7)

    def test_weighted_shared_solver_handles_a_high_uncertainty_scan_point(self):
        moments = np.array([4e-8, 1e-9, 1e-8])
        reference = normalized_emittance(moments, 441.3)
        projections = {float(k): (float(k), 1.0) for k in (-2, -1, 0, 1, 2)}
        rows = []
        for k, (a, b) in projections.items():
            value = float(np.array([a*a, 2*a*b, b*b]) @ moments * 1e6)
            rows.append(dict(case="unit", workpoint=0, scan_value=k, status="observed",
                             corrected_variance_mm2=value + (0.01 if k == 2 else 0),
                             noise_std_mm2=0.1 if k == 2 else 1e-5,
                             entrance_emittance_mm_mrad=reference, entrance_beta_gamma=441.3))
        config = dict(workpoints=[0], scan_values=list(projections))
        result = weighted_references(rows, projections, config)[0]
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["solver"], "weighted_svd_lstsq")
        self.assertLess(result["relative_error"], 1e-5)
        broken = copy.deepcopy(rows)
        broken[0]["status"] = "failed"
        self.assertEqual(weighted_references(broken, projections, config)[0]["status"], "incomplete_scan")


if __name__ == "__main__":
    unittest.main()
