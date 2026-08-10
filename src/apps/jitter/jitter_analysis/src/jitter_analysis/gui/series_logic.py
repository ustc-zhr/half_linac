from __future__ import annotations

import math

from ..analysis.jitter import filter_jitter_outliers


def series_sample_indices(metadata, values, expected_length: int | None = None) -> list[int]:
    if isinstance(metadata, dict):
        sample_indices = metadata.get("sample_indices")
        if isinstance(sample_indices, list):
            return sample_indices
        if sample_indices is not None:
            return [int(item) for item in sample_indices]
        length = len(values) if expected_length is None else int(expected_length)
        return list(range(length))

    indices = []
    for row_index, item in enumerate(metadata):
        try:
            indices.append(int(item["sample_index"]))
        except Exception:
            indices.append(row_index)
    return indices


def series_step_indices(metadata, values, expected_length: int | None = None) -> list[int | None]:
    if isinstance(metadata, dict):
        step_indices = metadata.get("step_indices")
        if isinstance(step_indices, list):
            return step_indices
        if step_indices is not None:
            return list(step_indices)
        length = len(values) if expected_length is None else int(expected_length)
        return [None] * length

    return [item.get("step_index") for item in metadata]


def filtered_series_payload(
    values,
    sample_indices,
    step_indices,
    *,
    outlier_filter_enabled: bool,
    outlier_filter_threshold: float,
) -> dict[str, object]:
    finite_rows = []
    aligned_values = []
    for index, value in enumerate(values):
        numeric_value = float(value)
        if math.isnan(numeric_value):
            aligned_values.append(float("nan"))
            continue
        aligned_values.append(numeric_value)
        sample_index = int(sample_indices[index]) if index < len(sample_indices) else index
        step_index = step_indices[index] if index < len(step_indices) else None
        finite_rows.append((index, numeric_value, sample_index, step_index))

    if not finite_rows:
        return {
            "raw_values": [],
            "filtered_values": [],
            "filtered_sample_indices": [],
            "filtered_step_indices": [],
            "filtered_raw_indices": [],
            "aligned_values": aligned_values,
            "aligned_sample_indices": list(sample_indices),
            "aligned_step_indices": list(step_indices),
            "raw_count": 0,
            "removed_count": 0,
            "filter_mode": "off" if not outlier_filter_enabled else "robust_z_score_noop",
        }

    raw_values = [row[1] for row in finite_rows]
    filter_result = filter_jitter_outliers(
        raw_values,
        enabled=outlier_filter_enabled,
        threshold=outlier_filter_threshold,
    )
    kept_positions = {
        int(position)
        for position in filter_result.kept_indices
        if 0 <= int(position) < len(finite_rows)
    }

    filtered_values = []
    filtered_sample_indices = []
    filtered_step_indices = []
    filtered_raw_indices = []
    for position, (raw_index, numeric_value, sample_index, step_index) in enumerate(finite_rows):
        if position in kept_positions:
            filtered_values.append(numeric_value)
            filtered_sample_indices.append(sample_index)
            filtered_step_indices.append(step_index)
            filtered_raw_indices.append(raw_index)
        else:
            aligned_values[raw_index] = float("nan")

    return {
        "raw_values": raw_values,
        "filtered_values": filtered_values,
        "filtered_sample_indices": filtered_sample_indices,
        "filtered_step_indices": filtered_step_indices,
        "filtered_raw_indices": filtered_raw_indices,
        "aligned_values": aligned_values,
        "aligned_sample_indices": list(sample_indices),
        "aligned_step_indices": list(step_indices),
        "raw_count": len(raw_values),
        "removed_count": int(filter_result.removed_count),
        "filter_mode": filter_result.applied_mode,
    }
