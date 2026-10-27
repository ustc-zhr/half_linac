import math

import numpy as np
import pytest

from jitter_analysis.analysis.spectrum import (
    compute_amplitude_spectrum,
    compute_periodic_fit,
    compute_welch_psd,
)


@pytest.mark.parametrize("sample_count,bin_index", [(1000, 100), (999, 100)])
@pytest.mark.parametrize("window_name", ["boxcar", "hann", "hamming", "blackman"])
def test_one_sided_fft_recovers_bin_centered_peak_amplitude(
    sample_count,
    bin_index,
    window_name,
):
    peak_amplitude = 0.3
    times = np.arange(sample_count, dtype=float)
    frequency_hz = bin_index / sample_count
    values = 4.2 + peak_amplitude * np.sin(
        2.0 * np.pi * frequency_hz * times + 0.4
    )

    result = compute_amplitude_spectrum(
        values,
        1.0,
        window_name,
        detrend="mean",
    )

    assert result.dominant_frequency_hz == pytest.approx(frequency_hz)
    assert result.dominant_amplitude == pytest.approx(peak_amplitude, rel=1.0e-5)
    assert result.frequency_resolution_hz == pytest.approx(1.0 / sample_count)


def test_one_sided_fft_does_not_double_dc_or_nyquist():
    dc_result = compute_amplitude_spectrum(
        np.full(1000, 2.5),
        1.0,
        "boxcar",
        detrend="none",
    )
    assert dc_result.amplitudes[0] == pytest.approx(2.5)

    values = 0.7 * (-1.0) ** np.arange(1000)
    nyquist_result = compute_amplitude_spectrum(
        values,
        1.0,
        "boxcar",
        detrend="mean",
    )
    assert nyquist_result.dominant_frequency_hz == pytest.approx(0.5)
    assert nyquist_result.dominant_amplitude == pytest.approx(0.7)


def test_fft_supports_explicit_detrending_and_rejects_unknown_mode():
    positions = np.arange(256, dtype=float)
    values = 3.0 + 0.05 * positions

    mean_result = compute_amplitude_spectrum(values, 1.0, detrend="mean")
    linear_result = compute_amplitude_spectrum(values, 1.0, detrend="linear")

    assert mean_result.detrend_name == "mean"
    assert linear_result.detrend_name == "linear"
    assert np.max(linear_result.amplitudes) < np.max(mean_result.amplitudes)

    with pytest.raises(ValueError, match="Unsupported detrend"):
        compute_amplitude_spectrum(values, 1.0, detrend="quadratic")


def test_welch_auto_uses_512_samples_and_reports_detrend_and_resolution():
    values = np.sin(2.0 * np.pi * 0.1 * np.arange(800, dtype=float))

    result = compute_welch_psd(values, 1.0, detrend="linear")

    assert result.nperseg == 512
    assert result.detrend_name == "linear"
    assert result.frequency_resolution_hz == pytest.approx(1.0 / 512.0)


def _irregular_periodic_series():
    times = np.cumsum(1.0 + 0.003 * np.sin(np.arange(700) * 0.17))
    times -= times[0]
    frequency_hz = 1.0 / 9.0
    peak_amplitude = 0.12
    phase_rad = 0.7
    drift_per_sec = 2.0e-5
    values = (
        2.0
        + drift_per_sec * times
        + peak_amplitude * np.sin(
            2.0 * np.pi * frequency_hz * times + phase_rad
        )
    )
    keep = np.ones(times.size, dtype=bool)
    keep[[50, 51, 303, 500]] = False
    return (
        values[keep],
        times[keep],
        frequency_hz,
        peak_amplitude,
        math.degrees(phase_rad),
        drift_per_sec * 3600.0,
    )


def test_periodic_fit_auto_recovers_irregular_gapped_signal():
    values, times, frequency_hz, amplitude, phase_deg, drift_per_hour = (
        _irregular_periodic_series()
    )

    result = compute_periodic_fit(values, times)

    assert result.frequency_source == "auto"
    assert result.frequency_hz == pytest.approx(frequency_hz, abs=1.0e-7)
    assert result.period_sec == pytest.approx(9.0, abs=1.0e-5)
    assert result.peak_amplitude == pytest.approx(amplitude, abs=1.0e-7)
    assert result.rms_amplitude == pytest.approx(amplitude / math.sqrt(2.0))
    assert result.phase_deg == pytest.approx(phase_deg, abs=1.0e-3)
    assert result.drift_per_hour == pytest.approx(drift_per_hour, abs=1.0e-6)
    assert result.periodic_variance_fraction > 0.999999
    assert result.residual_rms < 1.0e-6
    assert result.window_name == "hann"
    assert result.detrend_name == "linear"


def test_periodic_fit_locked_amplitude_is_stable_across_ranges():
    values, times, frequency_hz, amplitude, _phase_deg, _drift = (
        _irregular_periodic_series()
    )

    full = compute_periodic_fit(
        values,
        times,
        target_frequency_hz=frequency_hz,
    )
    shortened = compute_periodic_fit(
        values[:400],
        times[:400],
        target_frequency_hz=frequency_hz,
    )

    assert full.frequency_source == "locked"
    assert full.frequency_hz == frequency_hz
    assert full.peak_amplitude == pytest.approx(amplitude, abs=1.0e-10)
    assert shortened.peak_amplitude == pytest.approx(amplitude, abs=1.0e-10)


def test_periodic_fit_validates_timestamps_and_target_frequency():
    with pytest.raises(ValueError, match="eight"):
        compute_periodic_fit([1.0, 2.0], [0.0, 1.0])

    values = np.sin(2.0 * np.pi * 0.1 * np.arange(16, dtype=float))
    with pytest.raises(ValueError, match="distinct"):
        compute_periodic_fit(values, np.zeros(16))
    with pytest.raises(ValueError, match="below the Nyquist"):
        compute_periodic_fit(
            values,
            np.arange(16, dtype=float),
            target_frequency_hz=0.5,
        )
