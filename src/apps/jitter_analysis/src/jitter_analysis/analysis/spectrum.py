from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

try:
    from scipy import signal
except ImportError:  # pragma: no cover - optional runtime dependency
    signal = None


@dataclass(slots=True)
class SpectrumResult:
    frequencies_hz: np.ndarray
    amplitudes: np.ndarray
    dominant_frequency_hz: float
    dominant_amplitude: float
    window_name: str


@dataclass(slots=True)
class WelchPsdResult:
    frequencies_hz: np.ndarray
    psd: np.ndarray
    dominant_frequency_hz: float
    dominant_psd: float
    window_name: str
    nperseg: int


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


def compute_amplitude_spectrum(
    values: Sequence[float],
    sample_interval_sec: float,
    window_name: str | None = None,
) -> SpectrumResult:
    if sample_interval_sec <= 0:
        raise ValueError("sample_interval_sec must be positive")

    data = np.asarray(values, dtype=float)
    data = data[np.isfinite(data)]
    if data.size < 2:
        raise ValueError("At least two samples are required for spectrum analysis")

    resolved_window_name = _resolve_window_name(window_name, default="boxcar")
    centered = data - np.mean(data)
    window = _build_window(centered.size, resolved_window_name)
    coherent_gain = float(np.mean(window))
    if coherent_gain <= 0.0:
        raise ValueError("Window coherent gain must be positive")
    weighted = centered * window
    freqs = np.fft.rfftfreq(centered.size, d=sample_interval_sec)
    spectrum = np.abs(np.fft.rfft(weighted)) / (centered.size * coherent_gain)
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
    )


def compute_welch_psd(
    values: Sequence[float],
    sample_interval_sec: float,
    *,
    window_name: str | None = None,
    nperseg: int | None = None,
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
    requested_nperseg = None if nperseg is None or int(nperseg) <= 0 else int(nperseg)
    actual_nperseg = min(256 if requested_nperseg is None else requested_nperseg, int(data.size))
    if actual_nperseg < 2:
        raise ValueError("At least two finite samples are required for Welch PSD analysis")

    freqs, psd = signal.welch(
        data,
        fs=fs,
        window=resolved_window_name,
        nperseg=actual_nperseg,
        noverlap=actual_nperseg // 2,
        detrend="constant",
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
    )
