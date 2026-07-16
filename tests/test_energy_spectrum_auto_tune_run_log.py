from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from half_linac.src.apps.energy_spectrum.auto_tune_run_log import ESAAutoTuneRunLog


class ESAAutoTuneRunLogTests(unittest.TestCase):
    def test_log_flushes_start_progress_restore_and_result_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            logger = ESAAutoTuneRunLog.create(
                Path(temp_dir),
                {
                    "machine_id": "irfel",
                    "backend": "real",
                    "objective": "brightness_then_profile_lock",
                    "scan_min_mev": 0,
                    "scan_max_mev": 65,
                },
            )
            logger.record_progress(
                {
                    "stage": "center_step",
                    "current": 29.45,
                    "has_beam": True,
                    "score": 1234.0,
                    "center_mm": -0.036,
                    "center_offset_mm": -0.036,
                    "valid_frames": 3,
                    "total_frames": 3,
                    "fit_method": "direct",
                }
            )
            logger.record_progress(
                {"stage": "restore", "current": 29.4, "has_beam": False}
            )
            logger.record_result(
                {
                    "ok": True,
                    "status": "DONE",
                    "initial_value": 29.4,
                    "best_current": 29.45,
                    "center_lock_result": {
                        "seed_energy": 28.95,
                        "final_offset_mm": -0.036,
                        "fit_method": "direct",
                        "center_step": 0.05,
                    },
                }
            )

            with logger.path.open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))

            self.assertEqual([row["event"] for row in rows], [
                "start",
                "progress",
                "progress",
                "result",
            ])
            self.assertEqual(rows[1]["stage"], "center_step")
            self.assertEqual(rows[1]["dx_mm"], "-0.036")
            self.assertEqual(rows[2]["restored_energy_mev"], "29.4")
            self.assertEqual(rows[3]["status"], "DONE")
            self.assertEqual(rows[3]["seed_energy_mev"], "28.95")
            logger.close()

    def test_same_second_runs_receive_unique_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            now = datetime(2026, 7, 16, 15, 30, 12)
            first = ESAAutoTuneRunLog.create(Path(temp_dir), {}, now=now)
            second = ESAAutoTuneRunLog.create(Path(temp_dir), {}, now=now)

            self.assertNotEqual(first.path, second.path)
            self.assertTrue(second.path.name.endswith("_2.csv"))
            first.close()
            second.close()

    def test_log_retention_only_keeps_requested_number(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runs_dir = Path(temp_dir)
            loggers = [
                ESAAutoTuneRunLog.create(runs_dir, {}, max_logs=2)
                for _index in range(3)
            ]

            paths = list(runs_dir.glob("esa_auto_tune_*.csv"))

            self.assertEqual(len(paths), 2)
            self.assertIn(loggers[-1].path, paths)
            for logger in loggers:
                logger.close()


if __name__ == "__main__":
    unittest.main()
