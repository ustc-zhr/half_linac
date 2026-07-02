from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np

try:
    from scipy import signal
except ImportError:  # pragma: no cover - optional runtime dependency
    signal = None


@dataclass(slots=True)
class WaveformFeatureSet:
    baseline_mean: float
    peak_value: float
    peak_time_sec: float
    integral: float
    rms: float
    peak_to_peak: float
    roi_start_index: int
    roi_stop_index: int
    sample_count: int


@dataclass(slots=True)
class WaveformDelayEstimate:
    delay_samples: float
    delay_sec: float
    peak_correlation: float
    overlap_count: int


def _clamp_roi_indices(length: int, roi_start_index: int = 0, roi_stop_index: int | None = None) -> tuple[int, int]:
    if length <= 0:
        return 0, 0
    start = max(int(roi_start_index), 0)
    stop = length if roi_stop_index is None else min(max(int(roi_stop_index), start + 1), length)
    return start, max(stop, start)


def _baseline_sample_count(length: int) -> int:
    if length <= 0:
        return 0
    return max(1, int(math.ceil(length * 0.1)))


def _prepare_roi(values: Sequence[float], roi_start_index: int = 0, roi_stop_index: int | None = None) -> tuple[np.ndarray, int, int]:
    data = np.asarray(values, dtype=float).reshape(-1)
    start, stop = _clamp_roi_indices(int(data.size), roi_start_index=roi_start_index, roi_stop_index=roi_stop_index)
    return data[start:stop], start, stop


def compute_waveform_features(
    values: Sequence[float],
    waveform_sample_interval_sec: float,
    *,
    roi_start_index: int = 0,
    roi_stop_index: int | None = None,
) -> WaveformFeatureSet:
    if waveform_sample_interval_sec <= 0.0:
        raise ValueError("waveform_sample_interval_sec must be positive.")
    roi, start, stop = _prepare_roi(values, roi_start_index=roi_start_index, roi_stop_index=roi_stop_index)
    if roi.size <= 0:
        raise ValueError("Waveform ROI is empty.")

    finite_mask = np.isfinite(roi)
    if not np.any(finite_mask):
        raise ValueError("Waveform ROI contains no finite samples.")

    finite_roi = roi[finite_mask]
    baseline_count = min(_baseline_sample_count(int(finite_roi.size)), int(finite_roi.size))
    baseline_mean = float(np.mean(finite_roi[:baseline_count])) if baseline_count > 0 else 0.0
    clean_roi = np.where(finite_mask, roi, baseline_mean)
    corrected_roi = clean_roi - baseline_mean
    peak_offset = int(np.argmax(clean_roi))

    if clean_roi.size >= 2:
        integral = float(np.trapezoid(corrected_roi, dx=waveform_sample_interval_sec))
    else:
        integral = 0.0

    return WaveformFeatureSet(
        baseline_mean=baseline_mean,
        peak_value=float(np.max(clean_roi)),
        peak_time_sec=float((start + peak_offset) * waveform_sample_interval_sec),
        integral=integral,
        rms=float(math.sqrt(np.mean(np.square(corrected_roi)))),
        peak_to_peak=float(np.ptp(clean_roi)),
        roi_start_index=int(start),
        roi_stop_index=int(stop),
        sample_count=int(clean_roi.size),
    )


def estimate_waveform_delay(
    left_values: Sequence[float],
    right_values: Sequence[float],
    waveform_sample_interval_sec: float,
    *,
    roi_start_index: int = 0,
    roi_stop_index: int | None = None,
) -> WaveformDelayEstimate:
    if waveform_sample_interval_sec <= 0.0:
        raise ValueError("waveform_sample_interval_sec must be positive.")

    left_roi, _, _ = _prepare_roi(left_values, roi_start_index=roi_start_index, roi_stop_index=roi_stop_index)
    right_roi, _, _ = _prepare_roi(right_values, roi_start_index=roi_start_index, roi_stop_index=roi_stop_index)
    overlap_count = min(int(left_roi.size), int(right_roi.size))
    if overlap_count < 3:
        raise ValueError("Need at least three overlapping waveform samples for delay estimation.")

    left_roi = left_roi[:overlap_count]
    right_roi = right_roi[:overlap_count]

    left_features = compute_waveform_features(
        left_roi,
        waveform_sample_interval_sec,
        roi_start_index=0,
        roi_stop_index=overlap_count,
    )
    right_features = compute_waveform_features(
        right_roi,
        waveform_sample_interval_sec,
        roi_start_index=0,
        roi_stop_index=overlap_count,
    )

    left_clean = np.where(np.isfinite(left_roi), left_roi, left_features.baseline_mean) - left_features.baseline_mean
    right_clean = np.where(np.isfinite(right_roi), right_roi, right_features.baseline_mean) - right_features.baseline_mean

    left_norm = float(np.linalg.norm(left_clean))
    right_norm = float(np.linalg.norm(right_clean))
    if left_norm <= 0.0 or right_norm <= 0.0:
        raise ValueError("Waveform delay requires non-constant finite signals.")

    left_clean = left_clean / left_norm
    right_clean = right_clean / right_norm

    if signal is not None:
        correlation = signal.correlate(right_clean, left_clean, mode="full", method="auto")
        lags = signal.correlation_lags(right_clean.size, left_clean.size, mode="full")
    else:  # pragma: no cover - scipy is a project dependency
        correlation = np.correlate(right_clean, left_clean, mode="full")
        lags = np.arange(-(left_clean.size - 1), right_clean.size)

    peak_index = int(np.argmax(correlation))
    peak_value = float(correlation[peak_index])
    delay_samples = float(lags[peak_index])

    if 0 < peak_index < len(correlation) - 1:
        left_value = float(correlation[peak_index - 1])
        center_value = float(correlation[peak_index])
        right_value = float(correlation[peak_index + 1])
        denominator = left_value - 2.0 * center_value + right_value
        if abs(denominator) > 1.0e-12:
            delay_samples += 0.5 * (left_value - right_value) / denominator

    return WaveformDelayEstimate(
        delay_samples=delay_samples,
        delay_sec=float(delay_samples * waveform_sample_interval_sec),
        peak_correlation=peak_value,
        overlap_count=overlap_count,
    )


def downsample_waveform_minmax(
    x_values: Sequence[float],
    y_values: Sequence[float],
    *,
    max_points: int = 2000,
) -> tuple[list[float], list[float]]:
    x_array = np.asarray(x_values, dtype=float).reshape(-1)
    y_array = np.asarray(y_values, dtype=float).reshape(-1)
    if x_array.size != y_array.size:
        raise ValueError("x_values and y_values must have the same number of samples.")
    if x_array.size <= max_points or max_points < 4:
        return x_array.tolist(), y_array.tolist()

    bucket_count = max(1, max_points // 2)
    edges = np.linspace(0, x_array.size, bucket_count + 1, dtype=int)
    downsampled_x: list[float] = []
    downsampled_y: list[float] = []
    for start, stop in zip(edges[:-1], edges[1:]):
        if stop <= start:
            continue
        bucket_x = x_array[start:stop]
        bucket_y = y_array[start:stop]
        if bucket_y.size == 1:
            downsampled_x.append(float(bucket_x[0]))
            downsampled_y.append(float(bucket_y[0]))
            continue
        finite_positions = np.flatnonzero(np.isfinite(bucket_y))
        if finite_positions.size <= 0:
            downsampled_x.append(float(bucket_x[0]))
            downsampled_y.append(float(bucket_y[0]))
            continue
        finite_bucket = bucket_y[finite_positions]
        min_pos = int(finite_positions[int(np.argmin(finite_bucket))])
        max_pos = int(finite_positions[int(np.argmax(finite_bucket))])
        ordered_positions = [min_pos, max_pos] if min_pos <= max_pos else [max_pos, min_pos]
        for position in ordered_positions:
            downsampled_x.append(float(bucket_x[position]))
            downsampled_y.append(float(bucket_y[position]))
    return downsampled_x, downsampled_y
