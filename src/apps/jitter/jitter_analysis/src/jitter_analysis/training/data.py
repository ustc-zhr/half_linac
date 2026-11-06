from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import warnings

import h5py
import numpy as np

from .types import (
    FeatureRecipe,
    RunDescriptor,
    RunQuality,
    TrainingExperimentSpec,
    TrainingTaskDefinition,
)


@dataclass(slots=True)
class AggregatedRun:
    descriptor: RunDescriptor
    values: dict[str, np.ndarray]
    target: np.ndarray
    gate: np.ndarray
    window_samples: int
    quality: RunQuality


@dataclass(slots=True)
class PreparedDataset:
    run_id: str
    feature_names: list[str]
    x: np.ndarray
    y_delta: np.ndarray
    current_target: np.ndarray
    trend_delta: np.ndarray
    window_indices: np.ndarray


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _metadata_source(metadata_payload: dict, result_payload: dict) -> dict:
    source = metadata_payload.get("metadata", metadata_payload)
    if source:
        return source
    return result_payload.get("metadata", {})


def _object_units(config_payload: dict) -> dict[str, str]:
    units: dict[str, str] = {}
    for item in config_payload.get("objects", []):
        object_id = str(item.get("id", "")).strip()
        if object_id:
            units[object_id] = str(item.get("unit", "")).strip()
    for item in config_payload.get("knobs", []):
        knob_id = str(item.get("id", "")).strip()
        if knob_id:
            units.setdefault(f"{knob_id}__readback", str(item.get("unit", "")).strip())
    return units


def _decode(value):
    return value.decode("utf-8") if isinstance(value, bytes) else value


def _read_field(dataset, name: str, selection=slice(None)):
    try:
        return dataset.fields(name)[selection]
    except Exception:
        return dataset[selection][name]


def inspect_run(path: str | Path, task: TrainingTaskDefinition) -> RunDescriptor:
    run_dir = Path(path).resolve()
    metadata_payload = _load_json(run_dir / "metadata.json")
    result_payload = _load_json(run_dir / "result.json")
    config_payload = _load_json(run_dir / "config_snapshot.json")
    metadata = _metadata_source(metadata_payload, result_payload)
    details = dict(result_payload.get("details", {}))
    issues: list[str] = []

    run_id = str(metadata.get("run_id") or run_dir.name)
    mode = str(metadata.get("mode", ""))
    status = str(result_payload.get("status", "incomplete"))
    machine = str(metadata.get("machine", ""))
    created_at = str(metadata.get("created_at", ""))
    notes = str(metadata.get("notes", ""))
    object_ids = tuple(
        str(item).strip()
        for item in details.get("scalar_object_ids", []) or details.get("target_object_ids", [])
        if str(item).strip()
    )
    interval_raw = details.get("shot_interval_sec", details.get("sample_interval_sec"))
    try:
        interval = float(interval_raw)
    except (TypeError, ValueError):
        interval = None
    logical_count = int(details.get("sample_count", 0) or 0)
    raw_path = run_dir / "raw.h5"

    if mode != "timed_acquisition":
        issues.append("only timed acquisitions are supported")
    if status not in {"completed", "stopped"}:
        issues.append(f"run status is {status or 'unknown'}")
    if not raw_path.exists():
        issues.append("raw.h5 is missing")
    if machine and machine.casefold() != task.machine.casefold():
        issues.append(f"machine is {machine}, expected {task.machine}")
    missing = [channel for channel in task.required_channels if channel not in object_ids]
    if missing:
        issues.append("missing required channels: " + ", ".join(missing))
    if interval is None or not math.isfinite(interval) or interval <= 0.0:
        issues.append("sample interval is missing or invalid")
    if logical_count <= 0:
        issues.append("logical sample count is missing or zero")

    if raw_path.exists() and object_ids and logical_count > 0:
        try:
            with h5py.File(raw_path, "r") as handle:
                if "samples" not in handle:
                    issues.append("HDF5 samples dataset is missing")
                else:
                    dataset = handle["samples"]
                    fields = set(dataset.dtype.names or ())
                    required_fields = {"pv_id", "value", "connected", "batch_index"}
                    if not required_fields.issubset(fields):
                        issues.append("HDF5 samples schema is not supported")
                    elif int(dataset.shape[0]) != logical_count * len(object_ids):
                        issues.append("HDF5 rows do not form complete regular batches")
                    else:
                        sample_batches = sorted({0, logical_count // 2, logical_count - 1})
                        for batch in sample_batches:
                            start = batch * len(object_ids)
                            stop = start + len(object_ids)
                            batch_ids = tuple(
                                str(_decode(item)).strip()
                                for item in _read_field(dataset, "pv_id", slice(start, stop))
                            )
                            if batch_ids != object_ids:
                                issues.append(
                                    f"HDF5 channel order is irregular at batch {batch}"
                                )
                                break
                            batch_indexes = np.asarray(
                                _read_field(dataset, "batch_index", slice(start, stop)),
                                dtype=np.int64,
                            )
                            if not np.all(batch_indexes == batch):
                                issues.append(
                                    f"HDF5 batch indexes are irregular at batch {batch}"
                                )
                                break
        except Exception as exc:
            issues.append(f"could not inspect raw.h5: {exc}")

    units = _object_units(config_payload)
    target_unit = units.get(task.target_channel, "")
    if target_unit and target_unit != "mm":
        issues.append(f"target unit is {target_unit}, expected mm")

    return RunDescriptor(
        path=str(run_dir),
        run_id=run_id,
        created_at=created_at,
        mode=mode,
        status=status,
        machine=machine,
        notes=notes,
        sample_interval_seconds=interval,
        logical_sample_count=logical_count,
        object_ids=object_ids,
        units=units,
        compatible=not issues,
        issues=tuple(issues),
    )


def discover_runs(root: str | Path, task: TrainingTaskDefinition) -> list[RunDescriptor]:
    root_path = Path(root)
    if not root_path.exists():
        return []
    candidates = {path.parent for path in root_path.rglob("result.json")}
    candidates.update(path.parent for path in root_path.rglob("raw.h5"))
    descriptors = [inspect_run(path, task) for path in candidates]
    descriptors.sort(key=lambda item: (item.created_at, item.run_id), reverse=True)
    return descriptors


def _window_sample_count(window_seconds: float, interval_seconds: float) -> int:
    if not math.isfinite(window_seconds) or window_seconds <= 0.0:
        raise ValueError("Window length must be positive")
    ratio = window_seconds / interval_seconds
    count = int(round(ratio))
    if count <= 0 or abs(count * interval_seconds - window_seconds) > max(1e-6, 0.01 * window_seconds):
        raise ValueError(
            f"Window {window_seconds:g} s is not compatible with sample interval {interval_seconds:g} s"
        )
    return count


def load_aggregated_run(
    descriptor: RunDescriptor,
    spec: TrainingExperimentSpec,
    task: TrainingTaskDefinition,
    selected_channels: list[str],
    role: str,
) -> AggregatedRun:
    if not descriptor.compatible:
        raise ValueError(f"Run {descriptor.run_id} is incompatible: {'; '.join(descriptor.issues)}")
    interval = float(descriptor.sample_interval_seconds)
    window_samples = _window_sample_count(float(spec.window_seconds), interval)
    raw_path = Path(descriptor.path) / "raw.h5"
    object_ids = list(descriptor.object_ids)
    column_by_id = {object_id: index for index, object_id in enumerate(object_ids)}
    missing = [channel for channel in selected_channels if channel not in column_by_id]
    if missing:
        raise ValueError(f"Run {descriptor.run_id} is missing selected channels: {', '.join(missing)}")

    with h5py.File(raw_path, "r") as handle:
        dataset = handle["samples"]
        logical_count = descriptor.logical_sample_count
        channel_count = len(object_ids)
        selected_indexes = np.asarray(
            [column_by_id[channel] for channel in selected_channels], dtype=int
        )
        selected = np.empty((logical_count, len(selected_channels)), dtype=float)
        batch_chunk = 8192
        for start_batch in range(0, logical_count, batch_chunk):
            stop_batch = min(logical_count, start_batch + batch_chunk)
            row_slice = slice(start_batch * channel_count, stop_batch * channel_count)
            chunk_shape = (stop_batch - start_batch, channel_count)
            value_chunk = np.asarray(
                _read_field(dataset, "value", row_slice), dtype=float
            ).reshape(chunk_shape)[:, selected_indexes]
            connected_chunk = np.asarray(
                _read_field(dataset, "connected", row_slice), dtype=bool
            ).reshape(chunk_shape)[:, selected_indexes]
            value_chunk[~connected_chunk] = np.nan
            selected[start_batch:stop_batch] = value_chunk

    complete_windows = selected.shape[0] // window_samples
    if complete_windows <= max(task.lag_windows) + int(spec.horizon_windows):
        raise ValueError(f"Run {descriptor.run_id} has too few complete windows")
    selected = selected[: complete_windows * window_samples]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        medians = np.nanmedian(
            selected.reshape(complete_windows, window_samples, len(selected_channels)), axis=1
        )
    values_by_channel = {
        channel: medians[:, index]
        for index, channel in enumerate(selected_channels)
    }
    x04 = values_by_channel[task.target_channel]
    if spec.subtract_upstream_orbit:
        target = (
            x04
            - float(spec.coefficient_a) * values_by_channel["bpm02_x"]
            - float(spec.coefficient_b) * values_by_channel["bpm03_x"]
        )
    else:
        target = x04.copy()
    gate = np.isfinite(values_by_channel[task.gate_channel]) & (
        values_by_channel[task.gate_channel] > float(spec.beam_threshold)
    )
    valid_target = gate & np.isfinite(target)
    missing_fraction = float(np.mean(~np.isfinite(medians))) if medians.size else 1.0
    quality = RunQuality(
        run_id=descriptor.run_id,
        role=role,
        windows=complete_windows,
        valid_windows=int(np.count_nonzero(valid_target)),
        beam_valid_fraction=float(np.mean(gate)),
        target_mean=float(np.nanmean(target[valid_target])) if np.any(valid_target) else math.nan,
        target_std=float(np.nanstd(target[valid_target])) if np.any(valid_target) else math.nan,
        missing_fraction=missing_fraction,
        sample_interval_seconds=interval,
    )
    return AggregatedRun(
        descriptor=descriptor,
        values=values_by_channel,
        target=target,
        gate=gate,
        window_samples=window_samples,
        quality=quality,
    )


def _rate_value(now: float, past: float, name: str, lag: int) -> float:
    if not (math.isfinite(now) and math.isfinite(past)):
        return math.nan
    if name.endswith("_pha"):
        delta = (now - past + 180.0) % 360.0 - 180.0
    else:
        delta = now - past
    return float(delta / lag)


def prepare_dataset(
    run: AggregatedRun,
    recipe: FeatureRecipe,
    task: TrainingTaskDefinition,
    available_channels: set[str],
    horizon_windows: int,
) -> PreparedDataset:
    channels = [channel for channel in recipe.channels if channel in available_channels]
    feature_sources = (["__target_q__"] if recipe.include_target_history else []) + channels
    if not feature_sources:
        raise ValueError(f"Feature recipe {recipe.id} has no compatible channels")
    feature_names = [
        f"{'q' if source == '__target_q__' else source}_rate_lag{lag}"
        for lag in task.lag_windows
        for source in feature_sources
    ]
    source_values = {
        source: run.target if source == "__target_q__" else run.values[source]
        for source in feature_sources
    }
    rows: list[list[float]] = []
    y_delta: list[float] = []
    current_target: list[float] = []
    trend_delta: list[float] = []
    window_indices: list[int] = []
    start = max(task.lag_windows)
    horizon = int(horizon_windows)
    if horizon <= 0:
        raise ValueError("Prediction horizon must be at least one window")

    for current in range(start, len(run.target) - horizon):
        future = current + horizon
        if not np.all(run.gate[current : future + 1]):
            continue
        if not (np.isfinite(run.target[current]) and np.isfinite(run.target[future])):
            continue
        row: list[float] = []
        for lag in task.lag_windows:
            for source in feature_sources:
                values = source_values[source]
                row.append(_rate_value(values[current], values[current - lag], source, lag))
        recent = run.target[max(0, current - 12) : current + 1]
        recent_diff = np.diff(recent)
        finite_diff = recent_diff[np.isfinite(recent_diff)]
        rows.append(row)
        y_delta.append(float(run.target[future] - run.target[current]))
        current_target.append(float(run.target[current]))
        trend_delta.append(float(np.mean(finite_diff) * horizon) if finite_diff.size else 0.0)
        window_indices.append(current)

    if not rows:
        raise ValueError(f"Run {run.descriptor.run_id} has no valid training examples")
    return PreparedDataset(
        run_id=run.descriptor.run_id,
        feature_names=feature_names,
        x=np.asarray(rows, dtype=float),
        y_delta=np.asarray(y_delta, dtype=float),
        current_target=np.asarray(current_target, dtype=float),
        trend_delta=np.asarray(trend_delta, dtype=float),
        window_indices=np.asarray(window_indices, dtype=int),
    )
