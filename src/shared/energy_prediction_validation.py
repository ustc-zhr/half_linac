"""Offline, read-only validation of short-horizon energy prediction models.

The module consumes timed-acquisition ``raw.h5`` files written by the jitter
application.  It deliberately has no EPICS imports and never writes machine
PVs.  Its purpose is to decide whether a predictive model beats causal,
simple baselines before any model is integrated into energy feedback.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import math
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np


DEFAULT_TARGET_ID = "eny_energy_actual"
DEFAULT_HORIZONS_SECONDS = (1.0, 2.0, 5.0, 10.0)
DEFAULT_LAGS_SECONDS = (0.0, 1.0, 2.0, 5.0, 10.0, 30.0)
DEFAULT_SEASONAL_PERIOD_SECONDS = 9.0

RF18_19_FEATURES = (
    "llrf18_phase",
    "llrf19_phase",
    "llrf18_ch2_amp",
    "llrf18_ch3_amp",
    "llrf18_ch4_amp",
    "llrf18_ch5_amp",
    "llrf18_ch6_amp",
    "llrf19_ch2_amp",
    "llrf19_ch3_amp",
    "llrf19_ch4_amp",
    "llrf19_ch5_amp",
    "llrf19_ch6_amp",
    "llrf18_ch2_phase",
    "llrf18_ch3_phase",
    "llrf18_ch4_phase",
    "llrf18_ch5_phase",
    "llrf18_ch6_phase",
    "llrf19_ch2_phase",
    "llrf19_ch3_phase",
    "llrf19_ch4_phase",
    "llrf19_ch5_phase",
    "llrf19_ch6_phase",
)
BEAM_FEATURES = (
    "fct1_peak_current",
    "ict01_charge",
    "ict02_charge",
    "ict03_charge",
    "ict04_charge",
)


@dataclass(frozen=True)
class RunSeries:
    path: Path
    run_id: str
    timestamps: np.ndarray
    object_ids: tuple[str, ...]
    values: np.ndarray
    valid: np.ndarray

    @property
    def index(self) -> dict[str, int]:
        return {object_id: index for index, object_id in enumerate(self.object_ids)}

    @property
    def cadence_seconds(self) -> float:
        intervals = np.diff(self.timestamps)
        intervals = intervals[np.isfinite(intervals) & (intervals > 0)]
        return float(np.median(intervals)) if intervals.size else math.nan


@dataclass(frozen=True)
class ExampleSet:
    run_id: str
    group: str
    horizon_seconds: float
    feature_names: tuple[str, ...]
    timestamps: np.ndarray
    inputs: np.ndarray
    targets: np.ndarray
    current_energy: np.ndarray
    history_mean: np.ndarray
    seasonal_energy: np.ndarray

    def __len__(self) -> int:
        return int(self.targets.size)


def _decode(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def discover_run_directories(inputs: Iterable[str | Path]) -> list[Path]:
    """Resolve raw files, run directories, or roots containing run directories."""

    found: set[Path] = set()
    for raw_input in inputs:
        path = Path(raw_input).expanduser().resolve()
        if path.is_file() and path.name == "raw.h5":
            found.add(path.parent)
        elif path.is_dir() and (path / "raw.h5").is_file():
            found.add(path)
        elif path.is_dir():
            found.update(item.parent for item in path.rglob("raw.h5"))
        else:
            raise FileNotFoundError(f"Unsupported run path: {path}")
    if not found:
        raise ValueError("No jitter raw.h5 files were found")
    return sorted(found)


def _object_ids_from_details(run_dir: Path, handle) -> list[str]:
    details: dict = {}
    raw_details = _decode(handle.attrs.get("details_json", ""))
    if raw_details:
        details = dict(json.loads(raw_details))
    if not details:
        details = dict(_load_json(run_dir / "result.json").get("details", {}))
    return [
        str(item).strip()
        for item in (
            details.get("scalar_object_ids", [])
            or details.get("target_object_ids", [])
        )
        if str(item).strip()
    ]


def _infer_object_ids(samples) -> list[str]:
    names = set(samples.dtype.names or ())
    record_count = int(samples.shape[0])
    if record_count <= 0 or "pv_id" not in names:
        return []
    scan_count = min(record_count, 8192)
    pv_ids = [_decode(item) for item in samples["pv_id"][:scan_count]]
    if "batch_index" in names:
        batch_indices = np.asarray(samples["batch_index"][:scan_count], dtype=int)
        different = np.flatnonzero(batch_indices != batch_indices[0])
        if different.size:
            return pv_ids[: int(different[0])]
    first_id = pv_ids[0]
    for index, pv_id in enumerate(pv_ids[1:], start=1):
        if pv_id == first_id:
            return pv_ids[:index]
    return []


def load_timed_run(path: str | Path) -> RunSeries:
    """Load one timed jitter run without importing the external GUI package."""

    try:
        import h5py
    except ImportError as exc:  # pragma: no cover - runtime dependency
        raise RuntimeError("h5py is required to read jitter raw.h5 files") from exc

    run_dir = Path(path).expanduser().resolve()
    if run_dir.is_file() and run_dir.name == "raw.h5":
        run_dir = run_dir.parent
    raw_path = run_dir / "raw.h5"
    if not raw_path.is_file():
        raise FileNotFoundError(f"Missing raw.h5 in {run_dir}")

    with h5py.File(raw_path, "r") as handle:
        mode = _decode(handle.attrs.get("mode", "timed_acquisition"))
        if mode != "timed_acquisition":
            raise ValueError(f"{raw_path} is mode {mode!r}, expected timed_acquisition")
        if "samples" not in handle:
            raise ValueError(f"{raw_path} has no /samples dataset")
        samples = handle["samples"]
        field_names = set(samples.dtype.names or ())
        required = {"pv_id", "value", "timestamp", "connected"}
        missing_fields = required - field_names
        if missing_fields:
            raise ValueError(
                f"{raw_path} samples are missing fields: {sorted(missing_fields)}"
            )
        object_ids = _object_ids_from_details(run_dir, handle) or _infer_object_ids(samples)
        object_count = len(object_ids)
        record_count = int(samples.shape[0])
        if object_count <= 0:
            raise ValueError(f"Cannot infer scalar object order in {raw_path}")
        if record_count <= 0 or record_count % object_count:
            raise ValueError(
                f"{raw_path}: {record_count} records are not divisible by "
                f"{object_count} objects"
            )
        first_ids = [_decode(item) for item in samples["pv_id"][:object_count]]
        last_ids = [_decode(item) for item in samples["pv_id"][-object_count:]]
        if first_ids != object_ids or last_ids != object_ids:
            raise ValueError(f"{raw_path}: scalar object order changes within the run")

        logical_count = record_count // object_count
        values = np.asarray(samples["value"][:], dtype=float).reshape(
            logical_count, object_count
        )
        connected = np.asarray(samples["connected"][:], dtype=bool).reshape(
            logical_count, object_count
        )
        valid = connected & np.isfinite(values)
        if "severity" in field_names:
            severity = np.asarray(samples["severity"][:], dtype=int).reshape(
                logical_count, object_count
            )
            valid &= severity == 0
        timestamp_tokens = samples["timestamp"][::object_count]
        timestamps = np.asarray(
            [datetime.fromisoformat(_decode(item)).timestamp() for item in timestamp_tokens],
            dtype=float,
        )
        run_id = _decode(handle.attrs.get("run_id", run_dir.name)) or run_dir.name

    if np.any(np.diff(timestamps) <= 0):
        raise ValueError(f"{raw_path}: logical sample timestamps are not strictly increasing")
    return RunSeries(
        path=run_dir,
        run_id=run_id,
        timestamps=timestamps,
        object_ids=tuple(object_ids),
        values=values,
        valid=valid,
    )


def audit_run(run: RunSeries, target_id: str = DEFAULT_TARGET_ID) -> dict[str, object]:
    intervals = np.diff(run.timestamps)
    finite_intervals = intervals[np.isfinite(intervals) & (intervals > 0)]
    target_index = run.index.get(target_id)
    if target_index is None:
        target_valid_fraction = 0.0
        unchanged_fraction = math.nan
    else:
        target_valid = run.valid[:, target_index]
        target_valid_fraction = float(np.mean(target_valid))
        target_values = run.values[:, target_index]
        pairs = target_valid[:-1] & target_valid[1:]
        unchanged_fraction = (
            float(np.mean(np.diff(target_values)[pairs] == 0.0))
            if np.any(pairs)
            else math.nan
        )
    return {
        "run_id": run.run_id,
        "path": str(run.path),
        "samples": int(run.timestamps.size),
        "duration_seconds": float(run.timestamps[-1] - run.timestamps[0]),
        "cadence_median_seconds": (
            float(np.median(finite_intervals)) if finite_intervals.size else math.nan
        ),
        "cadence_p95_seconds": (
            float(np.percentile(finite_intervals, 95))
            if finite_intervals.size
            else math.nan
        ),
        "maximum_gap_seconds": (
            float(np.max(finite_intervals)) if finite_intervals.size else math.nan
        ),
        "target_valid_fraction": target_valid_fraction,
        "target_unchanged_pair_fraction": unchanged_fraction,
    }


def default_feature_groups(
    runs: Sequence[RunSeries], target_id: str = DEFAULT_TARGET_ID
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    if not runs:
        return {}, {}
    common = set.intersection(*(set(run.object_ids) for run in runs))
    requested = {
        "energy_history": (target_id,),
        "rf18_19": (target_id, *RF18_19_FEATURES),
        "rf18_19_beam": (target_id, *RF18_19_FEATURES, *BEAM_FEATURES),
    }
    available = {
        name: tuple(item for item in object_ids if item in common)
        for name, object_ids in requested.items()
    }
    missing = {
        name: tuple(item for item in object_ids if item not in common)
        for name, object_ids in requested.items()
    }
    return available, missing


def _nearest_index(timestamps: np.ndarray, desired: float) -> int:
    right = int(np.searchsorted(timestamps, desired, side="left"))
    if right <= 0:
        return 0
    if right >= timestamps.size:
        return int(timestamps.size - 1)
    left = right - 1
    if desired - timestamps[left] <= timestamps[right] - desired:
        return left
    return right


def _is_phase_feature(object_id: str) -> bool:
    return "phase" in object_id.lower()


def beam_on_mask(
    run: RunSeries,
    *,
    fct_threshold: float = 0.2,
    ict_threshold: float = 0.1,
) -> np.ndarray:
    mask = np.ones(run.timestamps.size, dtype=bool)
    for object_id, threshold in (
        ("fct1_peak_current", fct_threshold),
        ("ict01_charge", ict_threshold),
    ):
        column = run.index.get(object_id)
        if column is not None:
            mask &= run.valid[:, column] & (run.values[:, column] > float(threshold))
    return mask


def build_examples(
    run: RunSeries,
    *,
    group: str,
    feature_ids: Sequence[str],
    target_id: str = DEFAULT_TARGET_ID,
    lags_seconds: Sequence[float] = DEFAULT_LAGS_SECONDS,
    horizon_seconds: float,
    seasonal_period_seconds: float = DEFAULT_SEASONAL_PERIOD_SECONDS,
    fct_threshold: float = 0.2,
    ict_threshold: float = 0.1,
) -> ExampleSet:
    """Build strictly causal examples using timestamp-aligned lagged values."""

    if target_id not in run.index:
        raise ValueError(f"Run {run.run_id} does not contain target {target_id!r}")
    feature_ids = tuple(dict.fromkeys(str(item) for item in feature_ids))
    missing = [item for item in feature_ids if item not in run.index]
    if missing:
        raise ValueError(f"Run {run.run_id} is missing features: {missing}")
    lags = tuple(sorted({float(item) for item in lags_seconds}))
    if not lags or lags[0] < 0:
        raise ValueError("Lag seconds must be a non-empty sequence of non-negative values")
    if float(horizon_seconds) <= 0:
        raise ValueError("Prediction horizon must be positive")
    cadence = run.cadence_seconds
    if not math.isfinite(cadence) or cadence <= 0:
        raise ValueError(f"Run {run.run_id} has no usable sample cadence")
    tolerance = max(0.05, 0.6 * cadence)
    index = run.index
    target_column = index[target_id]
    gate = beam_on_mask(
        run, fct_threshold=fct_threshold, ict_threshold=ict_threshold
    )

    feature_names: list[str] = []
    for lag in lags:
        for object_id in feature_ids:
            suffix = f"lag_{lag:g}s"
            if _is_phase_feature(object_id):
                feature_names.extend(
                    (f"{object_id}.sin.{suffix}", f"{object_id}.cos.{suffix}")
                )
            else:
                feature_names.append(f"{object_id}.{suffix}")

    rows: list[list[float]] = []
    targets: list[float] = []
    current_energy: list[float] = []
    history_mean: list[float] = []
    seasonal_energy: list[float] = []
    example_times: list[float] = []
    first_time = run.timestamps[0] + max(lags)
    last_time = run.timestamps[-1] - float(horizon_seconds)
    for anchor in range(run.timestamps.size):
        anchor_time = float(run.timestamps[anchor])
        if anchor_time < first_time or anchor_time > last_time:
            continue
        lag_indices = [
            _nearest_index(run.timestamps, anchor_time - lag) for lag in lags
        ]
        future_index = _nearest_index(
            run.timestamps, anchor_time + float(horizon_seconds)
        )
        if seasonal_period_seconds > 0:
            seasonal_cycles = max(
                1, math.ceil(float(horizon_seconds) / seasonal_period_seconds)
            )
            seasonal_lag = (
                seasonal_cycles * seasonal_period_seconds - float(horizon_seconds)
            )
            seasonal_index = _nearest_index(
                run.timestamps, anchor_time - seasonal_lag
            )
            seasonal_desired_time = anchor_time - seasonal_lag
        else:
            seasonal_index = lag_indices[0]
            seasonal_desired_time = anchor_time
        if future_index <= anchor:
            continue
        desired_times = [anchor_time - lag for lag in lags]
        if any(
            abs(float(run.timestamps[item]) - desired) > tolerance
            for item, desired in zip(lag_indices, desired_times)
        ):
            continue
        if (
            abs(
                float(run.timestamps[future_index])
                - (anchor_time + float(horizon_seconds))
            )
            > tolerance
        ):
            continue
        if abs(float(run.timestamps[seasonal_index]) - seasonal_desired_time) > tolerance:
            continue
        if (
            not gate[future_index]
            or not gate[seasonal_index]
            or not np.all(gate[lag_indices])
        ):
            continue
        if (
            not run.valid[future_index, target_column]
            or not run.valid[seasonal_index, target_column]
        ):
            continue
        columns = [index[item] for item in feature_ids]
        if not np.all(run.valid[np.ix_(lag_indices, columns)]):
            continue

        row: list[float] = []
        for lag_index in lag_indices:
            for object_id, column in zip(feature_ids, columns):
                value = float(run.values[lag_index, column])
                if _is_phase_feature(object_id):
                    radians = math.radians(value)
                    row.extend((math.sin(radians), math.cos(radians)))
                else:
                    row.append(value)
        target_history = run.values[lag_indices, target_column]
        if not np.all(np.isfinite(target_history)):
            continue
        rows.append(row)
        targets.append(float(run.values[future_index, target_column]))
        current_energy.append(float(run.values[lag_indices[0], target_column]))
        history_mean.append(float(np.mean(target_history)))
        seasonal_energy.append(float(run.values[seasonal_index, target_column]))
        example_times.append(anchor_time)

    width = len(feature_names)
    inputs = np.asarray(rows, dtype=float).reshape(-1, width)
    return ExampleSet(
        run_id=run.run_id,
        group=group,
        horizon_seconds=float(horizon_seconds),
        feature_names=tuple(feature_names),
        timestamps=np.asarray(example_times, dtype=float),
        inputs=inputs,
        targets=np.asarray(targets, dtype=float),
        current_energy=np.asarray(current_energy, dtype=float),
        history_mean=np.asarray(history_mean, dtype=float),
        seasonal_energy=np.asarray(seasonal_energy, dtype=float),
    )


def combine_examples(examples: Sequence[ExampleSet], run_id: str) -> ExampleSet:
    if not examples:
        raise ValueError("No example sets to combine")
    first = examples[0]
    if any(item.feature_names != first.feature_names for item in examples):
        raise ValueError("Cannot combine example sets with different features")
    return ExampleSet(
        run_id=run_id,
        group=first.group,
        horizon_seconds=first.horizon_seconds,
        feature_names=first.feature_names,
        timestamps=np.concatenate([item.timestamps for item in examples]),
        inputs=np.concatenate([item.inputs for item in examples], axis=0),
        targets=np.concatenate([item.targets for item in examples]),
        current_energy=np.concatenate([item.current_energy for item in examples]),
        history_mean=np.concatenate([item.history_mean for item in examples]),
        seasonal_energy=np.concatenate([item.seasonal_energy for item in examples]),
    )


def _subset(examples: ExampleSet, selection: np.ndarray, run_id: str) -> ExampleSet:
    return ExampleSet(
        run_id=run_id,
        group=examples.group,
        horizon_seconds=examples.horizon_seconds,
        feature_names=examples.feature_names,
        timestamps=examples.timestamps[selection],
        inputs=examples.inputs[selection],
        targets=examples.targets[selection],
        current_energy=examples.current_energy[selection],
        history_mean=examples.history_mean[selection],
        seasonal_energy=examples.seasonal_energy[selection],
    )


def chronological_split(
    examples: ExampleSet,
    *,
    train_fraction: float = 0.7,
    purge_seconds: float,
) -> tuple[ExampleSet, ExampleSet]:
    """Fallback split for one pilot run with a temporal purge gap."""

    if not 0.2 <= train_fraction <= 0.8:
        raise ValueError("train_fraction must be between 0.2 and 0.8")
    if len(examples) < 20:
        raise ValueError("At least 20 aligned examples are required")
    split_index = int(len(examples) * train_fraction)
    split_time = float(examples.timestamps[split_index])
    half_gap = max(0.0, float(purge_seconds)) / 2.0
    train_selection = examples.timestamps < split_time - half_gap
    test_selection = examples.timestamps > split_time + half_gap
    if np.count_nonzero(train_selection) < 10 or np.count_nonzero(test_selection) < 10:
        raise ValueError("Temporal purge leaves fewer than 10 train or test examples")
    return (
        _subset(examples, train_selection, f"{examples.run_id}:early"),
        _subset(examples, test_selection, f"{examples.run_id}:late"),
    )


def prediction_metrics(
    actual: np.ndarray,
    predicted: np.ndarray,
    current_energy: np.ndarray,
    persistence_rmse: float,
    best_baseline_rmse: float,
) -> dict[str, float]:
    error = np.asarray(predicted, dtype=float) - np.asarray(actual, dtype=float)
    rmse = float(np.sqrt(np.mean(error**2)))
    actual_change = np.asarray(actual, dtype=float) - np.asarray(
        current_energy, dtype=float
    )
    predicted_change = np.asarray(predicted, dtype=float) - np.asarray(
        current_energy, dtype=float
    )
    nonzero = np.abs(actual_change) > 1e-12
    direction_accuracy = (
        float(np.mean(np.sign(actual_change[nonzero]) == np.sign(predicted_change[nonzero])))
        if np.any(nonzero)
        else math.nan
    )
    return {
        "mae_mev": float(np.mean(np.abs(error))),
        "rmse_mev": rmse,
        "bias_mev": float(np.mean(error)),
        "skill_vs_persistence": (
            1.0 - (rmse**2 / persistence_rmse**2)
            if persistence_rmse > 0
            else math.nan
        ),
        "skill_vs_best_baseline": (
            1.0 - (rmse**2 / best_baseline_rmse**2)
            if best_baseline_rmse > 0
            else math.nan
        ),
        "direction_accuracy": direction_accuracy,
    }


def fit_and_score_models(
    train: ExampleSet,
    test: ExampleSet,
    *,
    include_mlp: bool = True,
    random_state: int = 17,
) -> tuple[list[dict[str, object]], dict[str, np.ndarray]]:
    """Fit baselines and compact tabular models, returning metrics and predictions."""

    try:
        from sklearn.compose import TransformedTargetRegressor
        from sklearn.ensemble import HistGradientBoostingRegressor
        from sklearn.linear_model import Ridge
        from sklearn.neural_network import MLPRegressor
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
    except ImportError as exc:  # pragma: no cover - runtime dependency
        raise RuntimeError("scikit-learn is required for prediction validation") from exc

    if len(train) < 10 or len(test) < 10:
        raise ValueError("At least 10 train and 10 test examples are required")
    predictions: dict[str, np.ndarray] = {
        "persistence": np.asarray(test.current_energy, dtype=float),
        "history_mean": np.asarray(test.history_mean, dtype=float),
        "seasonal_persistence": np.asarray(test.seasonal_energy, dtype=float),
    }
    models = {
        "ridge": make_pipeline(StandardScaler(), Ridge(alpha=1.0)),
        "gradient_boosting": HistGradientBoostingRegressor(
            max_iter=200,
            max_leaf_nodes=15,
            learning_rate=0.05,
            l2_regularization=1.0,
            random_state=random_state,
        ),
    }
    if include_mlp and len(train) >= 100:
        models["mlp"] = TransformedTargetRegressor(
            regressor=make_pipeline(
                StandardScaler(),
                MLPRegressor(
                    hidden_layer_sizes=(32, 16),
                    activation="relu",
                    early_stopping=True,
                    validation_fraction=0.15,
                    max_iter=400,
                    random_state=random_state,
                ),
            ),
            transformer=StandardScaler(),
        )
    for name, model in models.items():
        model.fit(train.inputs, train.targets)
        predictions[name] = np.asarray(model.predict(test.inputs), dtype=float)

    persistence_error = predictions["persistence"] - test.targets
    persistence_rmse = float(np.sqrt(np.mean(persistence_error**2)))
    baseline_rmses = [
        float(np.sqrt(np.mean((predictions[name] - test.targets) ** 2)))
        for name in ("persistence", "history_mean", "seasonal_persistence")
    ]
    best_baseline_rmse = min(baseline_rmses)
    rows: list[dict[str, object]] = []
    for name, predicted in predictions.items():
        rows.append(
            {
                "group": test.group,
                "horizon_seconds": test.horizon_seconds,
                "model": name,
                "train_examples": len(train),
                "test_examples": len(test),
                "train_runs": train.run_id,
                "test_runs": test.run_id,
                **prediction_metrics(
                    test.targets,
                    predicted,
                    test.current_energy,
                    persistence_rmse,
                    best_baseline_rmse,
                ),
            }
        )
    return rows, predictions


def finite_json(value):
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): finite_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [finite_json(item) for item in value]
    return value
