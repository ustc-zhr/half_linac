from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from ..domain.types import ScanStepRecord


@dataclass(slots=True)
class SensitivityStats:
    pv_id: str
    point_count: int
    knob_span: float
    response_span: float
    slope: float
    intercept: float
    correlation: float
    r_squared: float
    step_indices: np.ndarray
    knob_values: np.ndarray
    response_values: np.ndarray


def compute_single_knob_sensitivity(
    step_records: Sequence[ScanStepRecord],
    axis_source: str = "readback",
) -> list[SensitivityStats]:
    grouped_points: dict[str, dict[str, list[float]]] = {}

    for step in step_records:
        if axis_source == "target":
            knob_value = step.target_value
        elif axis_source == "readback":
            knob_value = step.readback_value if step.readback_value is not None else step.target_value
        else:
            raise ValueError(f"Unsupported single-knob axis source: {axis_source}")
        if knob_value is None or not np.isfinite(knob_value):
            continue

        samples_by_pv: dict[str, list[float]] = {}
        for sample in step.samples:
            if np.isfinite(sample.value):
                samples_by_pv.setdefault(sample.pv_id, []).append(float(sample.value))

        for pv_id, values in samples_by_pv.items():
            if not values:
                continue
            mean_value = float(np.mean(np.asarray(values, dtype=float)))
            entry = grouped_points.setdefault(pv_id, {"step_indices": [], "x": [], "y": []})
            entry["step_indices"].append(int(step.step_index))
            entry["x"].append(float(knob_value))
            entry["y"].append(mean_value)

    rows: list[SensitivityStats] = []
    for pv_id, series in grouped_points.items():
        x_values = np.asarray(series["x"], dtype=float)
        y_values = np.asarray(series["y"], dtype=float)
        if x_values.size < 2 or np.unique(x_values).size < 2:
            continue

        slope, intercept = np.polyfit(x_values, y_values, deg=1)
        predicted = slope * x_values + intercept
        residual_sum = float(np.sum(np.square(y_values - predicted)))
        total_sum = float(np.sum(np.square(y_values - np.mean(y_values))))
        if total_sum <= 0.0:
            r_squared = 1.0 if residual_sum <= 1.0e-12 else 0.0
        else:
            r_squared = max(0.0, 1.0 - residual_sum / total_sum)

        x_std = float(np.std(x_values))
        y_std = float(np.std(y_values))
        if x_std > 0.0 and y_std > 0.0:
            correlation = float(np.corrcoef(x_values, y_values)[0, 1])
        else:
            correlation = float("nan")

        rows.append(
            SensitivityStats(
                pv_id=pv_id,
                point_count=int(x_values.size),
                knob_span=float(np.ptp(x_values)),
                response_span=float(np.ptp(y_values)),
                slope=float(slope),
                intercept=float(intercept),
                correlation=correlation,
                r_squared=float(r_squared),
                step_indices=np.asarray(series["step_indices"], dtype=int),
                knob_values=x_values,
                response_values=y_values,
            )
        )

    rows.sort(key=lambda row: abs(row.slope), reverse=True)
    return rows
