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
    baseline_noise_rms: float = math.nan
    peak_amplitude: float = math.nan
    pulse_peak_index: float = math.nan
    pulse_peak_time_sec: float = math.nan
    area_samples: float = math.nan
    area: float = math.nan
    fwhm_samples: float = math.nan
    fwhm_sec: float = math.nan
    snr: float = math.nan
    polarity: str = "positive"
    baseline_start_index: int = 0
    baseline_stop_index: int = 0
    quality_status: str = "ok"
    quality_flags: tuple[str, ...] = ()


@dataclass(slots=True)
class WaveformDelayEstimate:
    delay_samples: float
    delay_sec: float
    peak_correlation: float
    overlap_count: int


def _clamp_roi_indices(
    length: int,
    roi_start_index: int = 0,
    roi_stop_index: int | None = None,
) -> tuple[int, int]:
    if length <= 0:
        return 0, 0
    start = min(max(int(roi_start_index), 0), length - 1)
    stop = length if roi_stop_index is None else min(max(int(roi_stop_index), start + 1), length)
    return start, stop


def _baseline_sample_count(length: int) -> int:
    if length <= 0:
        return 0
    return max(1, int(math.ceil(length * 0.1)))


def _prepare_roi(
    values: Sequence[float],
    roi_start_index: int = 0,
    roi_stop_index: int | None = None,
) -> tuple[np.ndarray, int, int]:
    data = np.asarray(values, dtype=float).reshape(-1)
    start, stop = _clamp_roi_indices(
        int(data.size),
        roi_start_index=roi_start_index,
        roi_stop_index=roi_stop_index,
    )
    return data[start:stop], start, stop


def _normalize_polarity(polarity: str) -> str:
    normalized = str(polarity or "auto").strip().lower().replace("-", "_")
    if normalized not in {"auto", "positive", "negative"}:
        raise ValueError(f"Unsupported waveform polarity: {polarity}")
    return normalized


def _resolve_baseline(
    data: np.ndarray,
    roi: np.ndarray,
    start: int,
    stop: int,
    *,
    baseline_start_index: int | None,
    baseline_stop_index: int | None,
) -> tuple[np.ndarray, int, int]:
    if baseline_start_index is None and baseline_stop_index is None:
        finite_roi = roi[np.isfinite(roi)]
        count = min(_baseline_sample_count(int(finite_roi.size)), int(finite_roi.size))
        return finite_roi[:count], start, min(start + count, stop)

    baseline_start, baseline_stop = _clamp_roi_indices(
        int(data.size),
        roi_start_index=0 if baseline_start_index is None else baseline_start_index,
        roi_stop_index=baseline_stop_index,
    )
    baseline = data[baseline_start:baseline_stop]
    return baseline[np.isfinite(baseline)], baseline_start, baseline_stop


def _resolve_pulse_polarity(corrected_roi: np.ndarray, polarity: str) -> tuple[str, float]:
    normalized = _normalize_polarity(polarity)
    if normalized == "positive":
        return normalized, 1.0
    if normalized == "negative":
        return normalized, -1.0
    positive_excursion = float(np.max(corrected_roi))
    negative_excursion = float(-np.min(corrected_roi))
    if negative_excursion > positive_excursion:
        return "negative", -1.0
    return "positive", 1.0


def _interpolated_crossing(
    values: np.ndarray,
    left_index: int,
    right_index: int,
    threshold: float,
) -> float:
    left_value = float(values[left_index])
    right_value = float(values[right_index])
    delta = right_value - left_value
    if abs(delta) <= 1.0e-15:
        return float(left_index)
    fraction = (threshold - left_value) / delta
    return float(left_index + min(max(fraction, 0.0), 1.0))


def _compute_fwhm(oriented_roi: np.ndarray, peak_offset: int, peak_amplitude: float) -> float:
    if oriented_roi.size < 3 or peak_amplitude <= 0.0:
        return math.nan
    threshold = 0.5 * peak_amplitude

    left_crossing = math.nan
    for index in range(peak_offset - 1, -1, -1):
        if float(oriented_roi[index]) < threshold <= float(oriented_roi[index + 1]):
            left_crossing = _interpolated_crossing(oriented_roi, index, index + 1, threshold)
            break

    right_crossing = math.nan
    for index in range(peak_offset, oriented_roi.size - 1):
        if float(oriented_roi[index]) >= threshold > float(oriented_roi[index + 1]):
            right_crossing = _interpolated_crossing(oriented_roi, index, index + 1, threshold)
            break

    if not math.isfinite(left_crossing) or not math.isfinite(right_crossing):
        return math.nan
    return float(max(right_crossing - left_crossing, 0.0))


def compute_waveform_features(
    values: Sequence[float],
    waveform_sample_interval_sec: float,
    *,
    roi_start_index: int = 0,
    roi_stop_index: int | None = None,
    baseline_start_index: int | None = None,
    baseline_stop_index: int | None = None,
    polarity: str = "auto",
) -> WaveformFeatureSet:
    data = np.asarray(values, dtype=float).reshape(-1)
    roi, start, stop = _prepare_roi(
        data,
        roi_start_index=roi_start_index,
        roi_stop_index=roi_stop_index,
    )
    if roi.size <= 0:
        raise ValueError("Waveform ROI is empty.")

    finite_mask = np.isfinite(roi)
    if not np.any(finite_mask):
        raise ValueError("Waveform ROI contains no finite samples.")

    baseline_values, baseline_start, baseline_stop = _resolve_baseline(
        data,
        roi,
        start,
        stop,
        baseline_start_index=baseline_start_index,
        baseline_stop_index=baseline_stop_index,
    )
    if baseline_values.size <= 0:
        raise ValueError("Waveform baseline ROI contains no finite samples.")

    baseline_mean = float(np.mean(baseline_values))
    baseline_noise_rms = float(
        math.sqrt(np.mean(np.square(baseline_values - baseline_mean)))
    )
    clean_roi = np.where(finite_mask, roi, baseline_mean)
    corrected_roi = clean_roi - baseline_mean

    # Preserve the existing peak_value and peak_time_sec semantics: they
    # describe the raw maximum, independent of the pulse-polarity setting.
    legacy_peak_offset = int(np.argmax(clean_roi))
    sample_interval = float(waveform_sample_interval_sec or 0.0)
    has_time_axis = sample_interval > 0.0

    if not has_time_axis:
        integral = math.nan
    elif clean_roi.size >= 2:
        integral = float(np.trapezoid(corrected_roi, dx=sample_interval))
    else:
        integral = 0.0

    resolved_polarity, polarity_scale = _resolve_pulse_polarity(corrected_roi, polarity)
    oriented_roi = corrected_roi * polarity_scale
    pulse_peak_offset = int(np.argmax(oriented_roi))
    peak_amplitude = float(oriented_roi[pulse_peak_offset])
    pulse_peak_index = float(start + pulse_peak_offset)
    if clean_roi.size >= 2:
        area_samples = float(np.trapezoid(oriented_roi, dx=1.0))
    else:
        area_samples = 0.0
    area = area_samples * sample_interval if has_time_axis else math.nan
    fwhm_samples = _compute_fwhm(oriented_roi, pulse_peak_offset, peak_amplitude)
    quality_flags = []
    if clean_roi.size < 3:
        quality_flags.append("waveform_too_short")
    if not math.isfinite(fwhm_samples):
        quality_flags.append("fwhm_unavailable")
    if not has_time_axis:
        quality_flags.append("sample_interval_unavailable")

    return WaveformFeatureSet(
        baseline_mean=baseline_mean,
        peak_value=float(np.max(clean_roi)),
        peak_time_sec=(
            float((start + legacy_peak_offset) * sample_interval)
            if has_time_axis
            else math.nan
        ),
        integral=integral,
        rms=float(math.sqrt(np.mean(np.square(corrected_roi)))),
        peak_to_peak=float(np.ptp(clean_roi)),
        roi_start_index=int(start),
        roi_stop_index=int(stop),
        sample_count=int(clean_roi.size),
        baseline_noise_rms=baseline_noise_rms,
        peak_amplitude=peak_amplitude,
        pulse_peak_index=pulse_peak_index,
        pulse_peak_time_sec=(
            pulse_peak_index * sample_interval if has_time_axis else math.nan
        ),
        area_samples=area_samples,
        area=area,
        fwhm_samples=fwhm_samples,
        fwhm_sec=(
            fwhm_samples * sample_interval
            if has_time_axis and math.isfinite(fwhm_samples)
            else math.nan
        ),
        snr=(
            peak_amplitude / baseline_noise_rms
            if baseline_noise_rms > 0.0
            else math.nan
        ),
        polarity=resolved_polarity,
        baseline_start_index=int(baseline_start),
        baseline_stop_index=int(baseline_stop),
        quality_status="ok" if not quality_flags else "|".join(quality_flags),
        quality_flags=tuple(quality_flags),
    )


def _estimate_waveform_delay(
    left_values: Sequence[float],
    right_values: Sequence[float],
    waveform_sample_interval_sec: float,
    *,
    roi_start_index: int = 0,
    roi_stop_index: int | None = None,
    baseline_start_index: int | None = None,
    baseline_stop_index: int | None = None,
) -> WaveformDelayEstimate:
    left_data = np.asarray(left_values, dtype=float).reshape(-1)
    right_data = np.asarray(right_values, dtype=float).reshape(-1)
    overlap_length = min(int(left_data.size), int(right_data.size))
    left_roi, start, stop = _prepare_roi(
        left_data[:overlap_length],
        roi_start_index=roi_start_index,
        roi_stop_index=roi_stop_index,
    )
    right_roi = right_data[start:stop]
    overlap_count = min(int(left_roi.size), int(right_roi.size))
    if overlap_count < 3:
        raise ValueError("Need at least three overlapping waveform samples for delay estimation.")

    left_features = compute_waveform_features(
        left_data,
        waveform_sample_interval_sec,
        roi_start_index=start,
        roi_stop_index=stop,
        baseline_start_index=baseline_start_index,
        baseline_stop_index=baseline_stop_index,
    )
    right_features = compute_waveform_features(
        right_data,
        waveform_sample_interval_sec,
        roi_start_index=start,
        roi_stop_index=stop,
        baseline_start_index=baseline_start_index,
        baseline_stop_index=baseline_stop_index,
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

    sample_interval = float(waveform_sample_interval_sec or 0.0)
    return WaveformDelayEstimate(
        delay_samples=delay_samples,
        delay_sec=(
            float(delay_samples * sample_interval)
            if sample_interval > 0.0
            else math.nan
        ),
        peak_correlation=peak_value,
        overlap_count=overlap_count,
    )


def estimate_waveform_delay(
    left_values: Sequence[float],
    right_values: Sequence[float],
    waveform_sample_interval_sec: float,
    *,
    roi_start_index: int = 0,
    roi_stop_index: int | None = None,
    baseline_start_index: int | None = None,
    baseline_stop_index: int | None = None,
) -> WaveformDelayEstimate:
    if waveform_sample_interval_sec <= 0.0:
        raise ValueError("waveform_sample_interval_sec must be positive.")
    return _estimate_waveform_delay(
        left_values,
        right_values,
        waveform_sample_interval_sec,
        roi_start_index=roi_start_index,
        roi_stop_index=roi_stop_index,
        baseline_start_index=baseline_start_index,
        baseline_stop_index=baseline_stop_index,
    )


def estimate_waveform_delay_samples(
    left_values: Sequence[float],
    right_values: Sequence[float],
    *,
    roi_start_index: int = 0,
    roi_stop_index: int | None = None,
    baseline_start_index: int | None = None,
    baseline_stop_index: int | None = None,
) -> WaveformDelayEstimate:
    return _estimate_waveform_delay(
        left_values,
        right_values,
        0.0,
        roi_start_index=roi_start_index,
        roi_stop_index=roi_stop_index,
        baseline_start_index=baseline_start_index,
        baseline_stop_index=baseline_stop_index,
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
