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


def compute_jitter_stats(values: Sequence[float]) -> JitterStats:
    data = np.asarray(values, dtype=float)
    if data.size == 0:
        raise ValueError("No data points available for jitter analysis")

    mean = float(np.mean(data))
    std = float(np.std(data, ddof=0))
    rms = float(math.sqrt(np.mean(np.square(data))))
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
