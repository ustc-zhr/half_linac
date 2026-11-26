"""Numerical and failure-accounting tests for P2-B3; no elegant or IOC starts."""

from __future__ import annotations

import copy
import json
import unittest

import numpy as np

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

from scripts.diagnose_emit_infobax_measurement import (
    DEFAULT_CONFIG, STREAMS, covariance_moments, normalized_emittance, raster_moments,
    reconstruct, relative_error, repeat_statistics, summarize,
)


class MeasurementDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(DEFAULT_CONFIG.read_text())
        self.config.update(cases=[{"id": "unit"}], workpoints=[0.0], scan_values=[-1.0, 0.0, 1.0],
                           repeat_workpoints=[0.0], repeat_scan_values=[0.0],
                           repeat_particle_seeds=[12345, 23456])
        self.projections = {-1.0: (-1.0, 2.0), 0.0: (0.0, 2.0), 1.0: (1.0, 2.0)}
        moments = np.array([4e-8, 1e-9, 1e-8])
        reference = normalized_emittance(moments, 441.3)
        self.rows = []
        for scan, (a, b) in self.projections.items():
            variance = float(np.array([a*a, 2*a*b, b*b]) @ moments * 1e6)
            self.rows.append(dict(
                case="unit", workpoint=0.0, scan_value=scan, particle_seed=12345, status="observed",
                entrance_moments=moments.tolist(), entrance_beta_gamma=441.3,
                entrance_emittance_mm_mrad=reference, prediction_relative_error=0.0,
                **{name: variance for name in STREAMS},
            ))

    def test_particle_covariance_uses_central_moments(self):
        x = np.array([-2, -1, 1, 2]) * 1e-4
        xp = np.array([1, -1, -1, 1]) * 1e-5
        np.testing.assert_allclose(covariance_moments(x, xp), covariance_moments(x+0.3, xp-0.4), rtol=1e-10, atol=1e-18)
        self.assertGreater(normalized_emittance(covariance_moments(x, xp), 100), 0)

    def test_pixel_centers_and_nonzero_centroid_are_used(self):
        image = np.array([[0, 1, 0, 3, 0]], dtype=float)
        # At a 2 mm pitch, nonzero bins lie at -2 and +2 mm; centroid is +1 mm.
        self.assertAlmostEqual(raster_moments(image, 2.0), 3.0)
        with self.assertRaises(ValueError):
            raster_moments(-image, 2.0)

    def test_all_width_streams_round_trip_through_shared_solver(self):
        results = reconstruct(self.rows, self.projections, self.config)
        self.assertEqual(len(results), 4)
        self.assertTrue(all(row["status"] == "valid" for row in results))
        self.assertTrue(all(row["relative_error"] < 1e-12 for row in results))

    def test_nonphysical_fit_reconstruction_is_retained(self):
        rows = copy.deepcopy(self.rows)
        for row, variance in zip(rows, (0.01, 1.0, 0.01)):
            row["gaussian_fit"] = variance
        results = reconstruct(rows, self.projections, self.config)
        gaussian = next(row for row in results if row["stream"] == "gaussian_fit")
        self.assertEqual(gaussian["status"], "non_physical")
        self.assertIsNone(gaussian["emittance_mm_mrad"])
        self.assertEqual(sum(row["status"] == "valid" for row in results), 3)

    def test_missing_fit_does_not_get_filled_or_pass_the_gate(self):
        rows = copy.deepcopy(self.rows)
        rows[0]["gaussian_fit"] = None
        results = reconstruct(rows, self.projections, self.config)
        summary = summarize(rows, results, rows[1:2], [{"matched": True}], self.config)
        self.assertFalse(summary["gates"]["all_primary_queries_and_fits_valid"])
        self.assertFalse(summary["gates"]["gaussian_reference_physical"])
        self.assertFalse(summary["gates"]["all_simulated_repeats_valid"])
        self.assertEqual(summary["status"], "MEASUREMENT-NOISE-STOP")

    def test_deterministic_repeats_have_zero_empirical_standard_deviation(self):
        result = repeat_statistics([0.3] * 8)
        self.assertEqual(result["std_mm2"], 0.0)
        self.assertEqual(result["std_95_ci_mm2"], [0.0, 0.0])
        random = repeat_statistics([0.29, 0.3, 0.31])
        self.assertLess(random["std_95_ci_mm2"][0], random["std_mm2"])
        self.assertGreater(random["std_95_ci_mm2"][1], random["std_mm2"])
        with self.assertRaises(ValueError):
            repeat_statistics([0.1, float("nan")])

    def test_invalid_particle_arrays_fail_explicitly(self):
        for x, xp in (([1], [2]), ([1, 2], [1]), ([1, float("nan")], [1, 2])):
            with self.assertRaises(ValueError):
                covariance_moments(x, xp)
        self.assertIsNone(relative_error(None, 1))


if __name__ == "__main__":
    unittest.main()
