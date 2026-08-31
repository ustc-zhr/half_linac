from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from ..domain.types import MultiKnobStepRecord


@dataclass(slots=True)
class KnobInfluence:
    knob_id: str
    coefficient: float
    standardized_coefficient: float
    knob_span: float


@dataclass(slots=True)
class RandomInfluenceStats:
    pv_id: str
    point_count: int
    response_span: float
    intercept: float
    r_squared: float
    rank: int
    required_rank: int
    coefficients: list[KnobInfluence]
    response_values: np.ndarray
    predicted_values: np.ndarray
    warnings: list[str]


def compute_random_multi_knob_influence(
    step_records: Sequence[MultiKnobStepRecord],
    knob_ids: Sequence[str] | None = None,
) -> list[RandomInfluenceStats]:
    ordered_knob_ids = _ordered_knob_ids(step_records, knob_ids)
    if not ordered_knob_ids:
        return []

    point_rows: list[tuple[int, list[float], dict[str, float]]] = []
    for step in step_records:
        knob_values = []
        valid_features = True
        for knob_id in ordered_knob_ids:
            readback = step.readback_values.get(knob_id)
            target = step.target_values.get(knob_id)
            value = readback if readback is not None and np.isfinite(readback) else target
            if value is None or not np.isfinite(value):
                valid_features = False
                break
            knob_values.append(float(value))
        if not valid_features:
            continue

        samples_by_pv: dict[str, list[float]] = {}
        for sample in step.samples:
            if np.isfinite(sample.value):
                samples_by_pv.setdefault(sample.pv_id, []).append(float(sample.value))
        responses = {
            pv_id: float(np.mean(np.asarray(values, dtype=float)))
            for pv_id, values in samples_by_pv.items()
            if values
        }
        point_rows.append((int(step.step_index), knob_values, responses))

    pv_ids = []
    for _step_index, _knob_values, responses in point_rows:
        for pv_id in responses:
            if pv_id not in pv_ids:
                pv_ids.append(pv_id)

    results = []
    for pv_id in pv_ids:
        rows = [(features, responses[pv_id]) for _, features, responses in point_rows if pv_id in responses]
        if len(rows) < 2:
            continue
        x_values = np.asarray([row[0] for row in rows], dtype=float)
        y_values = np.asarray([row[1] for row in rows], dtype=float)
        x_std = np.std(x_values, axis=0)
        varying_mask = x_std > 1.0e-12
        varying_indices = np.flatnonzero(varying_mask)
        if varying_indices.size <= 0:
            continue

        x_mean = np.mean(x_values, axis=0)
        standardized_x = (
            x_values[:, varying_indices] - x_mean[varying_indices]
        ) / x_std[varying_indices]
        design = np.column_stack(
            [np.ones(x_values.shape[0], dtype=float), standardized_x]
        )
        fitted, _residuals, rank, _singular_values = np.linalg.lstsq(design, y_values, rcond=None)
        predicted = design @ fitted
        residual_sum = float(np.sum(np.square(y_values - predicted)))
        total_sum = float(np.sum(np.square(y_values - np.mean(y_values))))
        if total_sum <= 0.0:
            r_squared = 1.0 if residual_sum <= 1.0e-12 else 0.0
        else:
            r_squared = max(0.0, 1.0 - residual_sum / total_sum)

        raw_coefficients = np.zeros(len(ordered_knob_ids), dtype=float)
        raw_coefficients[varying_indices] = fitted[1:] / x_std[varying_indices]
        intercept = float(fitted[0] - np.dot(raw_coefficients, x_mean))
        y_std = float(np.std(y_values))
        standardized = np.zeros(len(ordered_knob_ids), dtype=float)
        if y_std > 1.0e-12:
            standardized[varying_indices] = fitted[1:] / y_std

        required_rank = int(varying_indices.size + 1)
        warnings = []
        if int(rank) < required_rank:
            warnings.append("Knob inputs are linearly dependent; influence coefficients are not unique.")
        recommended_points = max(required_rank + 1, 3 * required_rank)
        if x_values.shape[0] < recommended_points:
            warnings.append(
                f"Only {x_values.shape[0]} valid points; at least {recommended_points} are recommended "
                "for a stable influence estimate."
            )
        fixed_knobs = [
            ordered_knob_ids[index]
            for index in range(len(ordered_knob_ids))
            if not varying_mask[index]
        ]
        if fixed_knobs:
            warnings.append("No observed variation for: " + ", ".join(fixed_knobs) + ".")

        coefficients = [
            KnobInfluence(
                knob_id=knob_id,
                coefficient=float(raw_coefficients[index]),
                standardized_coefficient=float(standardized[index]),
                knob_span=float(np.ptp(x_values[:, index])),
            )
            for index, knob_id in enumerate(ordered_knob_ids)
        ]
        results.append(
            RandomInfluenceStats(
                pv_id=pv_id,
                point_count=int(x_values.shape[0]),
                response_span=float(np.ptp(y_values)),
                intercept=intercept,
                r_squared=float(r_squared),
                rank=int(rank),
                required_rank=required_rank,
                coefficients=coefficients,
                response_values=y_values,
                predicted_values=np.asarray(predicted, dtype=float),
                warnings=warnings,
            )
        )

    results.sort(
        key=lambda row: max(
            (abs(item.standardized_coefficient) for item in row.coefficients),
            default=0.0,
        ),
        reverse=True,
    )
    return results


def _ordered_knob_ids(
    step_records: Sequence[MultiKnobStepRecord],
    knob_ids: Sequence[str] | None,
) -> list[str]:
    ordered = []
    for knob_id in knob_ids or []:
        token = str(knob_id)
        if token and token not in ordered:
            ordered.append(token)
    for step in step_records:
        for knob_id in list(step.target_values) + list(step.readback_values):
            token = str(knob_id)
            if token and token not in ordered:
                ordered.append(token)
    return ordered
