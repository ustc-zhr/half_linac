from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class WaveformAnalysis:
    raw: np.ndarray
    normalized: np.ndarray
    baseline: float
    amplitude: float
    polarity: int
    edge_position: float | None
    edge_error: str
    roi_start: int
    roi_stop: int


def analyze_waveform(
    values: Any,
    *,
    threshold_fraction: float = 0.5,
    baseline_fraction: float = 0.1,
    roi_start: int = 0,
    roi_stop: int | None = None,
) -> WaveformAnalysis:
    """Normalize a pulse and locate its first rising threshold crossing."""
    if not 0.0 < float(threshold_fraction) < 1.0:
        raise ValueError("Threshold fraction must be between 0 and 1.")
    if not 0.0 < float(baseline_fraction) < 1.0:
        raise ValueError("Baseline fraction must be between 0 and 1.")
    try:
        raw = np.asarray(values, dtype=float).reshape(-1).copy()
    except (TypeError, ValueError) as exc:
        raise ValueError("Waveform is not a numeric array.") from exc
    if raw.size < 2:
        raise ValueError("Waveform requires at least two samples.")
    finite = np.isfinite(raw)
    if not np.any(finite):
        raise ValueError("Waveform contains no finite samples.")

    baseline_count = max(1, int(math.ceil(raw.size * float(baseline_fraction))))
    baseline_values = raw[:baseline_count]
    baseline_values = baseline_values[np.isfinite(baseline_values)]
    if baseline_values.size == 0:
        baseline_values = raw[finite]
    baseline = float(np.median(baseline_values))
    corrected = raw - baseline
    finite_corrected = corrected[finite]
    extreme = float(finite_corrected[np.argmax(np.abs(finite_corrected))])
    amplitude = abs(extreme)
    scale = max(1.0, abs(baseline), float(np.max(np.abs(raw[finite]))))
    if amplitude <= np.finfo(float).eps * scale:
        raise ValueError("Waveform has no measurable pulse amplitude.")
    polarity = 1 if extreme >= 0.0 else -1
    normalized = corrected * polarity / amplitude

    start = max(0, int(roi_start))
    stop = raw.size if roi_stop is None else min(raw.size, int(roi_stop))
    if stop - start < 2:
        edge_position = None
        edge_error = "ROI requires at least two samples."
    else:
        edge_position = _first_rising_crossing(
            normalized,
            float(threshold_fraction),
            start,
            stop,
        )
        edge_error = "" if edge_position is not None else "No rising threshold crossing."

    return WaveformAnalysis(
        raw=raw,
        normalized=normalized,
        baseline=baseline,
        amplitude=amplitude,
        polarity=polarity,
        edge_position=edge_position,
        edge_error=edge_error,
        roi_start=start,
        roi_stop=stop,
    )


def _first_rising_crossing(
    normalized: np.ndarray,
    threshold: float,
    start: int,
    stop: int,
) -> float | None:
    for index in range(start + 1, stop):
        before = float(normalized[index - 1])
        after = float(normalized[index])
        if not math.isfinite(before) or not math.isfinite(after):
            continue
        if before < threshold <= after:
            span = after - before
            if span <= 0.0:
                return float(index)
            return float(index - 1) + (threshold - before) / span
    return None
