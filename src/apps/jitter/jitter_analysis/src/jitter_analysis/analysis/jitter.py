from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np


@dataclass(slots=True)
class JitterStats:
    count: int
    mean: float
    std: float
    rms: float
    peak_to_peak: float
    minimum: float
    maximum: float


@dataclass(slots=True)
class JitterDisplayTransform:
    values: list[float]
    applied_mode: str


@dataclass(slots=True)
class JitterOutlierFilterResult:
    values: list[float]
    kept_indices: list[int]
    removed_count: int
    applied_mode: str


def compute_jitter_stats(values: Sequence[float]) -> JitterStats:
    data = np.asarray(values, dtype=float)
    if data.size == 0:
        raise ValueError("No data points available for jitter analysis")

    mean = float(np.mean(data))
    std = float(np.std(data, ddof=0))
    centered = data - mean
    rms = float(math.sqrt(np.mean(np.square(centered))))
    peak_to_peak = float(np.ptp(data))
    return JitterStats(
        count=int(data.size),
        mean=mean,
        std=std,
        rms=rms,
        peak_to_peak=peak_to_peak,
        minimum=float(np.min(data)),
        maximum=float(np.max(data)),
    )


def transform_jitter_values(
    values: Sequence[float],
    mode: str,
    *,
    mean: float | None = None,
    std: float | None = None,
) -> JitterDisplayTransform:
    data = np.asarray(values, dtype=float)
    if data.size == 0:
        raise ValueError("No data points available for jitter plotting")

    normalized_mode = str(mode).strip().lower().replace("-", "_")
    if normalized_mode == "raw":
        return JitterDisplayTransform(values=data.tolist(), applied_mode="raw")

    mean_value = float(np.mean(data)) if mean is None else float(mean)
    centered = data - mean_value
    if normalized_mode in {"mean_centered", "centered"}:
        return JitterDisplayTransform(values=centered.tolist(), applied_mode="mean_centered")

    if normalized_mode in {"z_score", "normalized"}:
        std_value = float(np.std(data, ddof=0)) if std is None else float(std)
        if not math.isfinite(std_value) or std_value <= 0.0:
            return JitterDisplayTransform(values=centered.tolist(), applied_mode="mean_centered")
        return JitterDisplayTransform(values=(centered / std_value).tolist(), applied_mode="z_score")

    raise ValueError(f"Unsupported jitter display mode: {mode}")


def filter_jitter_outliers(
    values: Sequence[float],
    *,
    enabled: bool = True,
    threshold: float = 10.0,
    method: str = "robust_z_score",
) -> JitterOutlierFilterResult:
    data = np.asarray(values, dtype=float)
    if data.size == 0:
        raise ValueError("No data points available for jitter outlier filtering")

    normalized_method = str(method).strip().lower().replace("-", "_")
    if not enabled or normalized_method in {"off", "none", "disabled"}:
        kept_indices = list(range(int(data.size)))
        return JitterOutlierFilterResult(
            values=data.tolist(),
            kept_indices=kept_indices,
            removed_count=0,
            applied_mode="off",
        )
    if normalized_method != "robust_z_score":
        raise ValueError(f"Unsupported jitter outlier filter method: {method}")
    if not math.isfinite(threshold) or threshold <= 0.0 or data.size < 3:
        kept_indices = list(range(int(data.size)))
        return JitterOutlierFilterResult(
            values=data.tolist(),
            kept_indices=kept_indices,
            removed_count=0,
            applied_mode="robust_z_score_noop",
        )

    median = float(np.median(data))
    absolute_deviation = np.abs(data - median)
    mad = float(np.median(absolute_deviation))

    if math.isfinite(mad) and mad > 0.0:
        robust_sigma = 1.4826 * mad
        keep_mask = absolute_deviation <= threshold * robust_sigma
        kept_indices = np.flatnonzero(keep_mask).tolist()
        return JitterOutlierFilterResult(
            values=data[keep_mask].tolist(),
            kept_indices=kept_indices,
            removed_count=int(data.size - len(kept_indices)),
            applied_mode="robust_z_score",
        )

    equality_tolerance = np.finfo(float).eps * max(abs(median), 1.0)
    equal_to_median = np.isclose(data, median, atol=equality_tolerance, rtol=0.0)
    if float(np.count_nonzero(equal_to_median)) / float(data.size) >= 0.8:
        kept_indices = np.flatnonzero(equal_to_median).tolist()
        return JitterOutlierFilterResult(
            values=data[equal_to_median].tolist(),
            kept_indices=kept_indices,
            removed_count=int(data.size - len(kept_indices)),
            applied_mode="median_majority_fallback",
        )

    kept_indices = list(range(int(data.size)))
    return JitterOutlierFilterResult(
        values=data.tolist(),
        kept_indices=kept_indices,
        removed_count=0,
        applied_mode="robust_z_score_no_scale",
    )
