from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from half_linac.src.shared.energy_prediction_validation import (
    ExampleSet,
    RunSeries,
    build_examples,
    chronological_split,
    default_feature_groups,
    fit_and_score_models,
    load_timed_run,
)


def synthetic_run(sample_count: int = 80) -> RunSeries:
    timestamps = 1_700_000_000.0 + np.arange(sample_count, dtype=float)
    object_ids = (
        "eny_energy_actual",
        "llrf18_phase",
        "fct1_peak_current",
        "ict01_charge",
    )
    values = np.column_stack(
        (
            np.arange(sample_count, dtype=float),
            np.linspace(170.0, 190.0, sample_count),
            np.ones(sample_count),
            np.ones(sample_count),
        )
    )
    return RunSeries(
        path=Path("/synthetic"),
        run_id="synthetic",
        timestamps=timestamps,
        object_ids=object_ids,
        values=values,
        valid=np.ones_like(values, dtype=bool),
    )


class EnergyPredictionValidationTests(unittest.TestCase):
    def test_build_examples_uses_causal_lags_and_future_target(self):
        examples = build_examples(
            synthetic_run(),
            group="test",
            feature_ids=("eny_energy_actual", "llrf18_phase"),
            lags_seconds=(0.0, 2.0),
            horizon_seconds=3.0,
        )
        self.assertEqual(examples.feature_names[0], "eny_energy_actual.lag_0s")
        self.assertIn("llrf18_phase.sin.lag_0s", examples.feature_names)
        self.assertEqual(examples.inputs.shape[1], 6)
        self.assertEqual(examples.current_energy[0], 6.0)
        self.assertEqual(examples.history_mean[0], 5.0)
        self.assertEqual(examples.targets[0], 9.0)
        self.assertEqual(examples.seasonal_energy[0], 0.0)

    def test_chronological_split_has_purge_gap(self):
        examples = build_examples(
            synthetic_run(160),
            group="test",
            feature_ids=("eny_energy_actual",),
            lags_seconds=(0.0, 2.0),
            horizon_seconds=3.0,
        )
        train, test = chronological_split(examples, purge_seconds=10.0)
        self.assertLess(float(train.timestamps[-1]), float(test.timestamps[0]))
        self.assertGreater(float(test.timestamps[0] - train.timestamps[-1]), 9.0)

    def test_default_groups_report_missing_channels(self):
        groups, missing = default_feature_groups([synthetic_run()])
        self.assertEqual(groups["energy_history"], ("eny_energy_actual",))
        self.assertIn("llrf18_phase", groups["rf18_19"])
        self.assertIn("llrf19_phase", missing["rf18_19"])
        self.assertIn("fct1_peak_current", groups["rf18_19_beam"])

    def test_models_are_scored_against_persistence(self):
        train_x = np.linspace(-2.0, 2.0, 160).reshape(-1, 1)
        test_x = np.linspace(-1.8, 1.8, 60).reshape(-1, 1)

        def examples(run_id: str, inputs: np.ndarray) -> ExampleSet:
            targets = 3.0 * inputs[:, 0] + 0.5
            return ExampleSet(
                run_id=run_id,
                group="synthetic",
                horizon_seconds=1.0,
                feature_names=("x",),
                timestamps=np.arange(inputs.shape[0], dtype=float),
                inputs=inputs,
                targets=targets,
                current_energy=np.zeros(inputs.shape[0]),
                history_mean=np.zeros(inputs.shape[0]),
                seasonal_energy=np.zeros(inputs.shape[0]),
            )

        rows, _ = fit_and_score_models(
            examples("train", train_x),
            examples("test", test_x),
            include_mlp=False,
        )
        by_model = {row["model"]: row for row in rows}
        self.assertAlmostEqual(by_model["persistence"]["skill_vs_persistence"], 0.0)
        self.assertGreater(by_model["ridge"]["skill_vs_persistence"], 0.99)
        self.assertGreater(by_model["ridge"]["skill_vs_best_baseline"], 0.99)

    def test_hdf5_loader_reconstructs_logical_batches(self):
        try:
            import h5py
        except ImportError as exc:  # pragma: no cover - selected test environment
            self.skipTest(str(exc))
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run_a"
            run_dir.mkdir()
            string = h5py.string_dtype(encoding="utf-8")
            dtype = np.dtype(
                [
                    ("pv_id", string),
                    ("value", np.float64),
                    ("timestamp", string),
                    ("connected", np.bool_),
                    ("severity", np.int32),
                    ("status", np.int32),
                    ("step_index", np.int64),
                    ("batch_index", np.int64),
                ]
            )
            object_ids = ["eny_energy_actual", "llrf18_phase"]
            rows = np.empty(6, dtype=dtype)
            start = datetime(2026, 1, 1)
            for batch in range(3):
                timestamp = (start + timedelta(seconds=batch)).isoformat()
                for column, object_id in enumerate(object_ids):
                    rows[batch * 2 + column] = (
                        object_id,
                        100.0 + batch + column,
                        timestamp,
                        True,
                        0,
                        0,
                        -1,
                        batch,
                    )
            with h5py.File(run_dir / "raw.h5", "w") as handle:
                handle.attrs["mode"] = "timed_acquisition"
                handle.attrs["run_id"] = "run_a"
                handle.attrs["details_json"] = json.dumps(
                    {"scalar_object_ids": object_ids}
                )
                handle.create_dataset("samples", data=rows)

            run = load_timed_run(run_dir)
            self.assertEqual(run.run_id, "run_a")
            self.assertEqual(run.object_ids, tuple(object_ids))
            self.assertEqual(run.values.shape, (3, 2))
            np.testing.assert_allclose(run.values[:, 0], [100.0, 101.0, 102.0])


if __name__ == "__main__":
    unittest.main()
