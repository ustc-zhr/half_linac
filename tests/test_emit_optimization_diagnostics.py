"""Offline tests for read-only emittance optimization diagnostics."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

from half_linac.src.apps.emit_measure.optimization_diagnostics import (
    load_measurement_diagnostics,
    load_optimization_run,
    plane_metric,
)


class OptimizationDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run_dir = Path(self.temp.name) / "optimization_test"
        latest = self.run_dir / "measurement_001" / "latest"
        latest.mkdir(parents=True)
        (latest / "scanResults.txt").write_text(
            "-1 2.0 3.0\n0 1.0 2.0\n1 2.1 3.1\n", encoding="utf-8"
        )
        plane = {
            "status": "valid",
            "validation_status": "validated",
            "normalized_emittance": 0.4,
            "rank": 3,
            "condition_number": 100.0,
            "residual_rms": 0.01,
            "left_points": 2,
            "right_points": 2,
            "low_growth_ratio": 2.2,
            "high_growth_ratio": 2.3,
            "fit_selection": {
                "status": "window",
                "points_used": 3,
                "points_total": 3,
                "selected_k1": [-1, 0, 1],
            },
        }
        self.metadata = {
            "scan_strategy": "adaptive_quality",
            "point_quality": [
                {"k1": value, "x": {"usable": True}, "y": {"usable": True}}
                for value in (-1, 0, 1)
            ],
            "fit_summary": {
                "leastSquares": {"xplane": dict(plane), "yplane": dict(plane)}
            },
        }
        (latest / "metadata.json").write_text(json.dumps(self.metadata), encoding="utf-8")
        self.record = {
            "index": 1,
            "stage": "Baseline",
            "archive": "/stale/location/measurement_001",
            "finished_at": 1,
            "valid": True,
            "feasible": True,
            "values": {"x": 0.4, "y": 0.5},
            "result": {},
        }

    def test_valid_archive_is_good_and_relocated_run_is_found(self):
        diagnostic = load_measurement_diagnostics(self.record, run_dir=self.run_dir)
        self.assertEqual(diagnostic["rating"], "Good")
        self.assertEqual(diagnostic["points"].shape, (3, 3))
        self.assertEqual(diagnostic["archive"], self.run_dir / "measurement_001")
        self.assertEqual(plane_metric(diagnostic["planes"]["x"], "exn"), 0.4)

    def test_rejected_image_sample_is_review_only(self):
        self.metadata["point_quality"][1]["x"] = {
            "usable": False, "status": "poor_fit"
        }
        metadata_path = self.run_dir / "measurement_001" / "latest" / "metadata.json"
        metadata_path.write_text(json.dumps(self.metadata), encoding="utf-8")
        diagnostic = load_measurement_diagnostics(self.record, run_dir=self.run_dir)
        self.assertEqual(diagnostic["rating"], "Review")
        self.assertTrue(any("rejected" in reason for reason in diagnostic["reasons"]))

    def test_controller_rejection_is_invalid(self):
        self.record.update(valid=False, error="X reconstruction is not valid")
        diagnostic = load_measurement_diagnostics(self.record, run_dir=self.run_dir)
        self.assertEqual(diagnostic["rating"], "Invalid")

    def test_run_loader_validates_schema(self):
        archive = self.run_dir / "optimization.json"
        archive.write_text(json.dumps({
            "schema_version": "emit_optimization_v2",
            "records": [self.record],
        }), encoding="utf-8")
        payload, directory = load_optimization_run(archive)
        self.assertEqual(directory, self.run_dir)
        self.assertEqual(len(payload["records"]), 1)
        archive.write_text(json.dumps({"schema_version": "unknown", "records": []}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not a supported"):
            load_optimization_run(archive)


if __name__ == "__main__":
    unittest.main()
