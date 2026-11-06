from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import h5py
import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.base import clone

from jitter_analysis.training import (
    FIR_BPM04_FORECAST,
    CandidateMetric,
    RunRole,
    TrainingExperimentSpec,
    TrainingService,
    export_model_package,
)
from jitter_analysis.training.data import (
    AggregatedRun,
    inspect_run,
    load_aggregated_run,
    prepare_dataset,
)
from jitter_analysis.training.types import RunDescriptor, RunQuality
import jitter_analysis.training.service as service_module


CHANNELS = (
    "bpm02_x",
    "bpm03_x",
    "bpm04_x",
    "bpm04_s",
    "bpm02_s",
    "bpm03_s",
    "gun_hv",
    "shb_ref_pha",
)


def _write_timed_run(
    root: Path,
    run_id: str,
    *,
    created_at: datetime,
    points: int = 420,
    interval: float = 1.0,
    status: str = "completed",
    mode: str = "timed_acquisition",
    phase_offset: float = 0.0,
    channels: tuple[str, ...] = CHANNELS,
) -> Path:
    run_dir = root / run_id
    run_dir.mkdir(parents=True)
    t = np.arange(points, dtype=float)
    bpm02 = 0.003 * t + 0.15 * np.sin(t / 18.0 + phase_offset)
    bpm03 = -0.001 * t + 0.08 * np.cos(t / 22.0 + phase_offset)
    bpm04 = 2.2 * bpm02 - 0.6 * bpm03 + 0.03 * np.sin(t / 5.0)
    values_by_channel = {
        "bpm02_x": bpm02,
        "bpm03_x": bpm03,
        "bpm04_x": bpm04,
        "bpm04_s": np.full(points, 2500.0),
        "bpm02_s": np.full(points, 3200.0),
        "bpm03_s": np.full(points, 3000.0),
        "gun_hv": 39.0 + 0.002 * np.sin(t / 15.0),
        "shb_ref_pha": ((179.0 + 0.4 * t + 180.0) % 360.0) - 180.0,
    }
    values = np.column_stack([values_by_channel[channel] for channel in channels])
    string_dtype = h5py.string_dtype(encoding="utf-8")
    dtype = np.dtype(
        [
            ("pv_id", string_dtype),
            ("value", np.float64),
            ("timestamp", string_dtype),
            ("connected", np.bool_),
            ("severity", np.int32),
            ("status", np.int32),
            ("step_index", np.int64),
            ("batch_index", np.int64),
        ]
    )
    rows = np.empty(points * len(channels), dtype=dtype)
    rows["pv_id"] = np.tile(np.asarray(channels, dtype=object), points)
    rows["value"] = values.reshape(-1)
    timestamps = [
        (created_at + timedelta(seconds=index * interval)).isoformat()
        for index in range(points)
    ]
    rows["timestamp"] = np.repeat(np.asarray(timestamps, dtype=object), len(channels))
    rows["connected"] = True
    rows["severity"] = 0
    rows["status"] = 0
    rows["step_index"] = -1
    rows["batch_index"] = np.repeat(np.arange(points), len(channels))
    with h5py.File(run_dir / "raw.h5", "w") as handle:
        handle.create_dataset("samples", data=rows)
    metadata = {
        "run_id": run_id,
        "mode": mode,
        "created_at": created_at.isoformat(),
        "operator": "test",
        "machine": "IRFEL",
        "config_path": "",
        "notes": "synthetic",
    }
    (run_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (run_dir / "result.json").write_text(
        json.dumps(
            {
                "metadata": metadata,
                "status": status,
                "details": {
                    "sample_count": points,
                    "shot_interval_sec": interval,
                    "scalar_object_ids": list(channels),
                },
                "counts": {"samples": points * len(channels), "steps": 0},
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "config_snapshot.json").write_text(
        json.dumps(
            {
                "objects": [
                    {"id": channel, "unit": "mm" if channel.endswith("_x") else "a.u."}
                    for channel in channels
                ]
            }
        ),
        encoding="utf-8",
    )
    return run_dir


def _spec(root: Path, train: Path, test: Path, output: Path) -> TrainingExperimentSpec:
    return TrainingExperimentSpec(
        run_root=str(root),
        output_root=str(output),
        run_roles={str(train): RunRole.TRAIN.value, str(test): RunRole.TEST.value},
        window_seconds=2.0,
        horizon_windows=1,
        beam_threshold=1000.0,
    )


def test_hdf5_loading_applies_window_gate_and_optional_orbit_subtraction(tmp_path: Path):
    run_dir = _write_timed_run(tmp_path, "run", created_at=datetime(2026, 1, 1))
    descriptor = inspect_run(run_dir, FIR_BPM04_FORECAST)
    spec = TrainingExperimentSpec(
        run_root=str(tmp_path),
        output_root=str(tmp_path / "out"),
        run_roles={str(run_dir): RunRole.TRAIN.value},
        subtract_upstream_orbit=True,
        coefficient_a=2.2,
        coefficient_b=-0.6,
        window_seconds=2.0,
    )
    loaded = load_aggregated_run(
        descriptor,
        spec,
        FIR_BPM04_FORECAST,
        list(CHANNELS),
        RunRole.TRAIN.value,
    )

    expected = (
        loaded.values["bpm04_x"]
        - 2.2 * loaded.values["bpm02_x"]
        + 0.6 * loaded.values["bpm03_x"]
    )
    np.testing.assert_allclose(loaded.target, expected)
    assert loaded.window_samples == 2
    assert loaded.quality.beam_valid_fraction == 1.0
    assert loaded.quality.windows == 210


def test_feature_preparation_is_causal_wraps_phase_and_honors_horizon():
    count = 80
    descriptor = RunDescriptor(
        path="/tmp/run",
        run_id="run",
        created_at="2026-01-01T00:00:00",
        mode="timed_acquisition",
        status="completed",
        machine="IRFEL",
        notes="",
        sample_interval_seconds=1.0,
        logical_sample_count=count,
        object_ids=CHANNELS,
        units={"bpm04_x": "mm"},
        compatible=True,
    )
    phase = np.full(count, -179.0)
    phase[1::2] = 179.0
    target = np.arange(count, dtype=float)
    values = {channel: np.zeros(count) for channel in CHANNELS}
    values["shb_ref_pha"] = phase
    run = AggregatedRun(
        descriptor=descriptor,
        values=values,
        target=target,
        gate=np.ones(count, dtype=bool),
        window_samples=1,
        quality=RunQuality("run", "train", count, count, 1.0, 0.0, 1.0, 0.0, 1.0),
    )
    recipe = next(item for item in FIR_BPM04_FORECAST.feature_recipes if item.id == "rf_hv")
    dataset = prepare_dataset(
        run,
        recipe,
        FIR_BPM04_FORECAST,
        {"shb_ref_pha"},
        horizon_windows=2,
    )

    assert dataset.window_indices[0] == 30
    assert np.all(dataset.y_delta == 2.0)
    phase_column = dataset.feature_names.index("shb_ref_pha_rate_lag1")
    assert np.max(np.abs(dataset.x[:, phase_column])) == pytest.approx(2.0)
    assert len(dataset.x) == count - 30 - 2


def test_run_inspection_rejects_scan_and_missing_required_channel(tmp_path: Path):
    run_dir = _write_timed_run(
        tmp_path, "scan", created_at=datetime(2026, 1, 1), mode="knob_scan"
    )
    result_path = run_dir / "result.json"
    payload = json.loads(result_path.read_text())
    payload["details"]["scalar_object_ids"].remove("bpm03_x")
    result_path.write_text(json.dumps(payload))

    descriptor = inspect_run(run_dir, FIR_BPM04_FORECAST)

    assert descriptor.compatible is False
    assert any("only timed acquisitions" in issue for issue in descriptor.issues)
    assert any("missing required channels" in issue for issue in descriptor.issues)


def test_training_service_saves_reproducible_experiment_and_model_package(tmp_path: Path, monkeypatch):
    train = _write_timed_run(
        tmp_path / "runs", "train", created_at=datetime(2026, 1, 1), phase_offset=0.0
    )
    test = _write_timed_run(
        tmp_path / "runs", "test", created_at=datetime(2026, 1, 2), phase_offset=0.3
    )
    original_candidates = service_module._model_candidates()
    monkeypatch.setattr(
        service_module,
        "_model_candidates",
        lambda: {
            "ridge": (
                clone(original_candidates["ridge"][0]),
                dict(original_candidates["ridge"][1]),
                dict(original_candidates["ridge"][2]),
            )
        },
    )
    spec = _spec(tmp_path / "runs", train, test, tmp_path / "experiments")
    progress = []

    result = TrainingService().run(spec, progress=lambda percent, text: progress.append((percent, text)))

    experiment = Path(result.experiment_dir)
    assert experiment.exists()
    assert (experiment / "experiment.json").exists()
    assert (experiment / "run_quality.csv").exists()
    assert (experiment / "metrics.csv").exists()
    assert (experiment / "predictions.csv").exists()
    assert result.recommended_candidate_id is not None
    assert progress[-1][0] == 100

    descriptor = inspect_run(test, FIR_BPM04_FORECAST)
    loaded = load_aggregated_run(
        descriptor,
        spec,
        FIR_BPM04_FORECAST,
        list(CHANNELS),
        RunRole.TEST.value,
    )
    recipe_id, _model_name = result.recommended_candidate_id.split("__", 1)
    recipe = next(item for item in FIR_BPM04_FORECAST.feature_recipes if item.id == recipe_id)
    dataset = prepare_dataset(
        loaded, recipe, FIR_BPM04_FORECAST, set(CHANNELS), spec.horizon_windows
    )
    model = joblib.load(result.candidate_model_paths[result.recommended_candidate_id])
    saved = pd.read_csv(result.predictions_path)
    saved = saved[saved["candidate_id"] == result.recommended_candidate_id]
    np.testing.assert_allclose(model.predict(dataset.x), saved["predicted_delta_mm"].to_numpy())

    package = export_model_package(
        experiment, result.recommended_candidate_id, tmp_path / "candidate"
    )
    assert package.suffix == ".zip"
    with zipfile.ZipFile(package) as archive:
        assert set(archive.namelist()) == {"model.joblib", "manifest.json", "metrics.json"}
        manifest = json.loads(archive.read("manifest.json"))
        model_bytes = archive.read("model.joblib")
    assert manifest["schema_version"] == 1
    assert manifest["candidate_id"] == result.recommended_candidate_id
    assert manifest["model_sha256"] == hashlib.sha256(model_bytes).hexdigest()
    assert manifest["feature_names"]

    assert {"persistence", "trend_12"}.issubset(result.candidate_model_paths)
    persistence_package = export_model_package(
        experiment, "persistence", tmp_path / "persistence"
    )
    with zipfile.ZipFile(persistence_package) as archive:
        baseline = joblib.load(archive.open("model.joblib"))
    np.testing.assert_allclose(baseline.predict(dataset.x), 0.0)


def test_recommendation_requires_positive_skill_on_every_test_run():
    def metric(run_id: str, skill: float, candidate: str = "candidate") -> CandidateMetric:
        return CandidateMetric(
            scope="run",
            run_id=run_id,
            candidate_id=candidate,
            feature_group="group",
            model="ridge",
            exportable=True,
            samples=10,
            rmse_mm=1.0,
            mae_mm=1.0,
            persistence_rmse_mm=1.0,
            skill_vs_persistence=skill,
            delta_correlation=0.0,
            direction_accuracy_large_half=0.5,
        )

    pooled = metric("ALL", 0.2)
    pooled.scope = "pooled"
    loaded = {
        "/a": SimpleNamespace(descriptor=SimpleNamespace(run_id="a")),
        "/b": SimpleNamespace(descriptor=SimpleNamespace(run_id="b")),
    }
    assert TrainingService._recommend_candidate(
        [metric("a", 0.2), metric("b", -0.01), pooled], ["/a", "/b"], loaded
    ) is None
    assert TrainingService._recommend_candidate(
        [metric("a", 0.2), metric("b", 0.01), pooled], ["/a", "/b"], loaded
    ) == "candidate"


def test_irregular_hdf5_batches_and_incompatible_window_interval_are_rejected(
    tmp_path: Path,
):
    irregular = _write_timed_run(
        tmp_path, "irregular", created_at=datetime(2026, 2, 1)
    )
    with h5py.File(irregular / "raw.h5", "r+") as handle:
        dataset = handle["samples"]
        middle_batch = 420 // 2
        row_index = middle_batch * len(CHANNELS)
        row = dataset[row_index]
        row["batch_index"] = -99
        dataset[row_index] = row

    descriptor = inspect_run(irregular, FIR_BPM04_FORECAST)
    assert descriptor.compatible is False
    assert any("batch indexes are irregular" in issue for issue in descriptor.issues)

    odd_interval = _write_timed_run(
        tmp_path,
        "odd_interval",
        created_at=datetime(2026, 2, 2),
        interval=0.3,
    )
    odd_descriptor = inspect_run(odd_interval, FIR_BPM04_FORECAST)
    spec = TrainingExperimentSpec(
        run_root=str(tmp_path),
        output_root=str(tmp_path / "out"),
        run_roles={str(odd_interval): RunRole.TRAIN.value},
        window_seconds=1.0,
    )
    with pytest.raises(ValueError, match="not compatible with sample interval"):
        load_aggregated_run(
            odd_descriptor,
            spec,
            FIR_BPM04_FORECAST,
            list(CHANNELS),
            RunRole.TRAIN.value,
        )


def test_selected_channel_loading_beam_gate_and_missing_values(tmp_path: Path):
    run_dir = _write_timed_run(
        tmp_path, "gate_missing", created_at=datetime(2026, 3, 1)
    )
    gate_column = CHANNELS.index("bpm04_s")
    target_column = CHANNELS.index("bpm04_x")
    with h5py.File(run_dir / "raw.h5", "r+") as handle:
        dataset = handle["samples"]
        for batch in range(4):
            gate_row = batch * len(CHANNELS) + gate_column
            row = dataset[gate_row]
            row["value"] = 100.0
            dataset[gate_row] = row
        for batch in range(10, 12):
            target_row = batch * len(CHANNELS) + target_column
            row = dataset[target_row]
            row["connected"] = False
            dataset[target_row] = row

    descriptor = inspect_run(run_dir, FIR_BPM04_FORECAST)
    spec = TrainingExperimentSpec(
        run_root=str(tmp_path),
        output_root=str(tmp_path / "out"),
        run_roles={str(run_dir): RunRole.TRAIN.value},
        window_seconds=2.0,
    )
    selected_channels = ["bpm02_x", "bpm03_x", "bpm04_x", "bpm04_s"]
    loaded = load_aggregated_run(
        descriptor,
        spec,
        FIR_BPM04_FORECAST,
        selected_channels,
        RunRole.TRAIN.value,
    )

    assert set(loaded.values) == set(selected_channels)
    assert loaded.quality.beam_valid_fraction == pytest.approx(208 / 210)
    assert np.isnan(loaded.target[5])
    assert loaded.quality.missing_fraction > 0.0

    recipe = FIR_BPM04_FORECAST.feature_recipes[0]
    dataset = prepare_dataset(
        loaded,
        recipe,
        FIR_BPM04_FORECAST,
        set(selected_channels),
        horizon_windows=1,
    )
    ridge = service_module._model_candidates()["ridge"][0]
    ridge.fit(dataset.x, dataset.y_delta)
    assert np.all(np.isfinite(ridge.predict(dataset.x)))


def test_optional_channel_intersection_is_recorded(tmp_path: Path, monkeypatch):
    run_root = tmp_path / "runs"
    train_channels = tuple(channel for channel in CHANNELS if channel != "gun_hv")
    train = _write_timed_run(
        run_root,
        "train_without_gun_hv",
        created_at=datetime(2026, 4, 1),
        channels=train_channels,
    )
    test = _write_timed_run(
        run_root,
        "test_with_gun_hv",
        created_at=datetime(2026, 4, 2),
    )
    original_candidates = service_module._model_candidates()
    monkeypatch.setattr(
        service_module,
        "_model_candidates",
        lambda: {
            "ridge": (
                clone(original_candidates["ridge"][0]),
                dict(original_candidates["ridge"][1]),
                dict(original_candidates["ridge"][2]),
            )
        },
    )

    result = TrainingService().run(
        _spec(run_root, train, test, tmp_path / "experiments")
    )
    experiment = json.loads(
        (Path(result.experiment_dir) / "experiment.json").read_text()
    )

    assert "gun_hv" not in experiment["common_channels"]
    assert "gun_hv" not in experiment["selected_channels"]
    assert any("gun_hv" in warning for warning in experiment["warnings"])
    assert "shb_ref_pha" in experiment["selected_channels"]


def test_fixed_mlp_seed_and_train_only_preprocessing():
    rng = np.random.default_rng(7)
    x_train = rng.normal(size=(120, 4))
    x_train[::9, 1] = np.nan
    y_train = (
        0.4 * np.nan_to_num(x_train[:, 0])
        - 0.2 * np.nan_to_num(x_train[:, 2])
    )
    x_test = rng.normal(loc=100.0, size=(20, 4))

    first = service_module._model_candidates()["mlp"][0]
    second = service_module._model_candidates()["mlp"][0]
    first.fit(x_train, y_train)
    second.fit(x_train, y_train)

    np.testing.assert_allclose(first.predict(x_test), second.predict(x_test))
    ridge = service_module._model_candidates()["ridge"][0]
    ridge.fit(x_train, y_train)
    imputed_train = ridge.named_steps["simpleimputer"].transform(x_train)
    np.testing.assert_allclose(
        ridge.named_steps["standardscaler"].mean_,
        np.mean(imputed_train, axis=0),
    )
    assert not np.allclose(
        ridge.named_steps["standardscaler"].mean_,
        np.mean(ridge.named_steps["simpleimputer"].transform(x_test), axis=0),
    )
