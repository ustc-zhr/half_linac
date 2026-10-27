from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

import numpy as np

try:
    from scipy import optimize, signal
except ImportError:  # pragma: no cover - optional runtime dependency
    optimize = None
    signal = None


@dataclass(slots=True)
class SpectrumResult:
    frequencies_hz: np.ndarray
    amplitudes: np.ndarray
    dominant_frequency_hz: float
    dominant_amplitude: float
    window_name: str
    detrend_name: str
    frequency_resolution_hz: float


@dataclass(slots=True)
class WelchPsdResult:
    frequencies_hz: np.ndarray
    psd: np.ndarray
    dominant_frequency_hz: float
    dominant_psd: float
    window_name: str
    nperseg: int
    detrend_name: str
    frequency_resolution_hz: float


@dataclass(slots=True)
class PeriodicFitResult:
    frequency_hz: float
    period_sec: float
    peak_amplitude: float
    rms_amplitude: float
    phase_deg: float
    drift_per_hour: float
    periodic_variance_fraction: float
    residual_rms: float
    frequency_source: str
    sample_count: int
    frequencies_hz: np.ndarray
    amplitudes: np.ndarray
    frequency_resolution_hz: float
    window_name: str
    detrend_name: str


def _resolve_window_name(window_name: str | None, *, default: str) -> str:
    token = str(window_name or default).strip().lower()
    aliases = {
        "": default,
        "default": default,
        "rectangular": "boxcar",
        "boxcar": "boxcar",
        "hann": "hann",
        "hanning": "hann",
        "hamming": "hamming",
        "blackman": "blackman",
    }
    resolved = aliases.get(token)
    if resolved is None:
        raise ValueError(f"Unsupported window '{window_name}'.")
    return resolved


def _build_window(size: int, window_name: str) -> np.ndarray:
    if window_name == "boxcar":
        return np.ones(size, dtype=float)
    if window_name == "hann":
        return np.hanning(size)
    if window_name == "hamming":
        return np.hamming(size)
    if window_name == "blackman":
        return np.blackman(size)
    raise ValueError(f"Unsupported window '{window_name}'.")


def _resolve_detrend_name(detrend: str | None, *, default: str = "mean") -> str:
    token = str(detrend or default).strip().lower()
    aliases = {
        "": default,
        "none": "none",
        "off": "none",
        "mean": "mean",
        "constant": "mean",
        "linear": "linear",
    }
    resolved = aliases.get(token)
    if resolved is None:
        raise ValueError(f"Unsupported detrend mode '{detrend}'.")
    return resolved


def _detrend_values(data: np.ndarray, detrend_name: str) -> np.ndarray:
    if detrend_name == "none":
        return np.asarray(data, dtype=float).copy()
    if detrend_name == "mean":
        return np.asarray(data, dtype=float) - float(np.mean(data))
    if detrend_name == "linear":
        positions = np.arange(data.size, dtype=float)
        design = np.column_stack((np.ones(data.size, dtype=float), positions))
        coefficients, *_ = np.linalg.lstsq(design, data, rcond=None)
        return np.asarray(data, dtype=float) - design @ coefficients
    raise ValueError(f"Unsupported detrend mode '{detrend_name}'.")


def _frequency_resolution(frequencies_hz: np.ndarray) -> float:
    if frequencies_hz.size < 2:
        return math.nan
    return float(frequencies_hz[1] - frequencies_hz[0])


def compute_amplitude_spectrum(
    values: Sequence[float],
    sample_interval_sec: float,
    window_name: str | None = None,
    *,
    detrend: str | None = "mean",
) -> SpectrumResult:
    if sample_interval_sec <= 0:
        raise ValueError("sample_interval_sec must be positive")

    data = np.asarray(values, dtype=float)
    data = data[np.isfinite(data)]
    if data.size < 2:
        raise ValueError("At least two samples are required for spectrum analysis")

    resolved_window_name = _resolve_window_name(window_name, default="hann")
    resolved_detrend_name = _resolve_detrend_name(detrend)
    detrended = _detrend_values(data, resolved_detrend_name)
    window = _build_window(detrended.size, resolved_window_name)
    coherent_gain = float(np.mean(window))
    if coherent_gain <= 0.0:
        raise ValueError("Window coherent gain must be positive")
    weighted = detrended * window
    freqs = np.fft.rfftfreq(detrended.size, d=sample_interval_sec)
    spectrum = np.abs(np.fft.rfft(weighted)) / float(np.sum(window))
    if spectrum.size > 1:
        if detrended.size % 2 == 0:
            spectrum[1:-1] *= 2.0
        else:
            spectrum[1:] *= 2.0
    if freqs.size <= 1:
        dominant_frequency_hz = 0.0
        dominant_amplitude = float(spectrum[0]) if spectrum.size else 0.0
    else:
        peak_index = int(np.argmax(spectrum[1:])) + 1
        dominant_frequency_hz = float(freqs[peak_index])
        dominant_amplitude = float(spectrum[peak_index])
    return SpectrumResult(
        frequencies_hz=freqs,
        amplitudes=spectrum,
        dominant_frequency_hz=dominant_frequency_hz,
        dominant_amplitude=dominant_amplitude,
        window_name=resolved_window_name,
        detrend_name=resolved_detrend_name,
        frequency_resolution_hz=_frequency_resolution(freqs),
    )


def compute_welch_psd(
    values: Sequence[float],
    sample_interval_sec: float,
    *,
    window_name: str | None = None,
    nperseg: int | None = None,
    detrend: str | None = "mean",
) -> WelchPsdResult:
    if signal is None:
        raise RuntimeError("scipy is required for Welch PSD analysis")
    if sample_interval_sec <= 0:
        raise ValueError("sample_interval_sec must be positive")

    data = np.asarray(values, dtype=float)
    data = data[np.isfinite(data)]
    if data.size < 2:
        raise ValueError("At least two samples are required for Welch PSD analysis")

    fs = 1.0 / sample_interval_sec
    resolved_window_name = _resolve_window_name(window_name, default="hann")
    resolved_detrend_name = _resolve_detrend_name(detrend)
    requested_nperseg = None if nperseg is None or int(nperseg) <= 0 else int(nperseg)
    actual_nperseg = min(
        512 if requested_nperseg is None else requested_nperseg,
        int(data.size),
    )
    if actual_nperseg < 2:
        raise ValueError("At least two finite samples are required for Welch PSD analysis")

    scipy_detrend: str | bool
    if resolved_detrend_name == "none":
        scipy_detrend = False
    elif resolved_detrend_name == "mean":
        scipy_detrend = "constant"
    else:
        scipy_detrend = "linear"
    freqs, psd = signal.welch(
        data,
        fs=fs,
        window=resolved_window_name,
        nperseg=actual_nperseg,
        noverlap=actual_nperseg // 2,
        detrend=scipy_detrend,
        scaling="density",
    )
    if freqs.size <= 1:
        dominant_frequency_hz = 0.0
        dominant_psd = float(psd[0]) if psd.size else 0.0
    else:
        peak_index = int(np.argmax(psd[1:])) + 1
        dominant_frequency_hz = float(freqs[peak_index])
        dominant_psd = float(psd[peak_index])
    return WelchPsdResult(
        frequencies_hz=freqs,
        psd=psd,
        dominant_frequency_hz=dominant_frequency_hz,
        dominant_psd=dominant_psd,
        window_name=resolved_window_name,
        nperseg=actual_nperseg,
        detrend_name=resolved_detrend_name,
        frequency_resolution_hz=_frequency_resolution(freqs),
    )


def _fit_periodic_frequency(
    values: np.ndarray,
    times_sec: np.ndarray,
    frequency_hz: float,
) -> tuple[np.ndarray, np.ndarray, float]:
    angular_frequency = 2.0 * np.pi * float(frequency_hz)
    design = np.column_stack(
        (
            np.ones(values.size, dtype=float),
            times_sec,
            np.sin(angular_frequency * times_sec),
            np.cos(angular_frequency * times_sec),
        )
    )
    coefficients, _residuals, rank, _singular_values = np.linalg.lstsq(
        design,
        values,
        rcond=None,
    )
    if int(rank) < 4:
        raise ValueError("Periodic fit design matrix is rank deficient.")
    fitted = design @ coefficients
    residual_sum_squares = float(np.sum(np.square(values - fitted)))
    return coefficients, fitted, residual_sum_squares


def compute_periodic_fit(
    values: Sequence[float],
    sample_times_sec: Sequence[float],
    *,
    target_frequency_hz: float | None = None,
) -> PeriodicFitResult:
    if optimize is None:
        raise RuntimeError("scipy is required for periodic fitting")

    data = np.asarray(values, dtype=float)
    times = np.asarray(sample_times_sec, dtype=float)
    if data.shape != times.shape:
        raise ValueError("values and sample_times_sec must have the same shape")
    finite = np.isfinite(data) & np.isfinite(times)
    data = data[finite]
    times = times[finite]
    if data.size < 8:
        raise ValueError(
            "At least eight finite timestamped samples are required for periodic fitting"
        )

    order = np.argsort(times)
    data = data[order]
    times = times[order]
    if np.any(np.diff(times) <= 0.0):
        raise ValueError("sample_times_sec must contain distinct timestamps")
    times = times - times[0]
    duration_sec = float(times[-1])
    deltas = np.diff(times)
    sample_interval_sec = float(np.median(deltas))
    if duration_sec <= 0.0 or sample_interval_sec <= 0.0:
        raise ValueError("sample_times_sec must span a positive duration")

    nyquist_hz = 0.5 / sample_interval_sec
    uniform_times = np.arange(
        0.0,
        duration_sec + 0.5 * sample_interval_sec,
        sample_interval_sec,
    )
    if uniform_times.size < 8:
        raise ValueError("At least eight uniformly spaced context samples are required")
    uniform_values = np.interp(uniform_times, times, data)
    context = compute_amplitude_spectrum(
        uniform_values,
        sample_interval_sec,
        window_name="hann",
        detrend="linear",
    )

    if target_frequency_hz is None:
        candidate_hz = float(context.dominant_frequency_hz)
        resolution_hz = float(context.frequency_resolution_hz)
        lower_hz = max(candidate_hz - resolution_hz, 0.5 / duration_sec)
        upper_hz = min(
            candidate_hz + resolution_hz,
            np.nextafter(nyquist_hz, 0.0),
        )
        if not lower_hz < upper_hz:
            raise ValueError(
                "Unable to construct a valid automatic frequency search interval"
            )

        def objective(frequency_hz: float) -> float:
            return _fit_periodic_frequency(data, times, frequency_hz)[2]

        optimized = optimize.minimize_scalar(
            objective,
            bounds=(lower_hz, upper_hz),
            method="bounded",
            options={"xatol": max(1.0e-12, resolution_hz * 1.0e-9)},
        )
        if not optimized.success or not np.isfinite(optimized.fun):
            raise ValueError("Automatic periodic frequency optimization failed")
        frequency_hz = float(optimized.x)
        frequency_source = "auto"
    else:
        frequency_hz = float(target_frequency_hz)
        if not np.isfinite(frequency_hz) or frequency_hz <= 0.0:
            raise ValueError("target_frequency_hz must be positive")
        if frequency_hz >= nyquist_hz:
            raise ValueError(
                "target_frequency_hz must be below the Nyquist frequency"
            )
        frequency_source = "locked"

    coefficients, fitted, residual_sum_squares = _fit_periodic_frequency(
        data,
        times,
        frequency_hz,
    )
    trend_design = np.column_stack((np.ones(data.size, dtype=float), times))
    trend_coefficients, *_ = np.linalg.lstsq(trend_design, data, rcond=None)
    trend_residuals = data - trend_design @ trend_coefficients
    trend_residual_sum_squares = float(np.sum(np.square(trend_residuals)))
    if trend_residual_sum_squares > np.finfo(float).eps:
        periodic_variance_fraction = (
            1.0 - residual_sum_squares / trend_residual_sum_squares
        )
        periodic_variance_fraction = float(
            np.clip(periodic_variance_fraction, 0.0, 1.0)
        )
    else:
        periodic_variance_fraction = 0.0

    sine_coefficient = float(coefficients[2])
    cosine_coefficient = float(coefficients[3])
    peak_amplitude = float(np.hypot(sine_coefficient, cosine_coefficient))
    phase_deg = float(
        np.degrees(np.arctan2(cosine_coefficient, sine_coefficient))
    )
    residual_rms = float(np.sqrt(np.mean(np.square(data - fitted))))
    return PeriodicFitResult(
        frequency_hz=frequency_hz,
        period_sec=1.0 / frequency_hz,
        peak_amplitude=peak_amplitude,
        rms_amplitude=peak_amplitude / np.sqrt(2.0),
        phase_deg=phase_deg,
        drift_per_hour=float(coefficients[1] * 3600.0),
        periodic_variance_fraction=periodic_variance_fraction,
        residual_rms=residual_rms,
        frequency_source=frequency_source,
        sample_count=int(data.size),
        frequencies_hz=context.frequencies_hz,
        amplitudes=context.amplitudes,
        frequency_resolution_hz=context.frequency_resolution_hz,
        window_name=context.window_name,
        detrend_name=context.detrend_name,
    )
