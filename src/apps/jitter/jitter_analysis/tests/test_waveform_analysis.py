from pathlib import Path
import sys

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jitter_analysis.analysis.waveform import (
    compute_waveform_features,
    downsample_waveform_minmax,
    estimate_waveform_delay,
    estimate_waveform_delay_samples,
)


def test_compute_waveform_features_extracts_expected_values():
    values = [0.0, 0.0, 1.0, 3.0, 2.0, 0.0]

    features = compute_waveform_features(values, 0.5, roi_start_index=0, roi_stop_index=6)

    assert abs(features.baseline_mean) < 1.0e-12
    assert features.peak_value == 3.0
    assert features.peak_time_sec == 1.5
    assert features.integral > 0.0
    assert features.rms > 0.0
    assert features.peak_to_peak == 3.0


def test_compute_waveform_features_without_interval_keeps_amplitude_features():
    values = [0.0, 0.0, 1.0, 3.0, 2.0, 0.0]

    features = compute_waveform_features(values, 0.0)

    assert features.peak_value == 3.0
    assert features.rms > 0.0
    assert features.peak_to_peak == 3.0
    assert np.isnan(features.peak_time_sec)
    assert np.isnan(features.integral)


def test_estimate_waveform_delay_returns_positive_when_right_lags_left():
    left = np.array([0.0, 0.0, 0.5, 1.0, 0.5, 0.0, 0.0], dtype=float)
    right = np.array([0.0, 0.0, 0.0, 0.5, 1.0, 0.5, 0.0], dtype=float)

    estimate = estimate_waveform_delay(left, right, 1.0e-9)

    assert estimate.delay_sec > 0.0
    assert abs(estimate.delay_sec - 1.0e-9) <= 1.0e-9
    assert estimate.overlap_count == 7


def test_downsample_waveform_minmax_preserves_extrema():
    x_values = list(range(4000))
    y_values = [0.0] * 4000
    y_values[123] = 5.0
    y_values[3123] = -4.0

    downsampled_x, downsampled_y = downsample_waveform_minmax(x_values, y_values, max_points=200)

    assert len(downsampled_x) <= 200
    assert 5.0 in downsampled_y
    assert -4.0 in downsampled_y


def test_compute_waveform_features_preserves_legacy_fields_with_default_arguments():
    values = [0.0, 0.0, 1.0, 3.0, 2.0, 0.0]

    features = compute_waveform_features(values, 0.5)

    assert features.baseline_mean == 0.0
    assert features.peak_value == 3.0
    assert features.peak_time_sec == 1.5
    assert features.peak_to_peak == 3.0
    assert features.roi_start_index == 0
    assert features.roi_stop_index == 6


def test_compute_waveform_features_supports_negative_pulses_and_explicit_baseline():
    values = [1.0, 1.2, 1.0, 0.0, -3.0, 0.0, 1.0]

    features = compute_waveform_features(
        values,
        0.25,
        roi_start_index=2,
        roi_stop_index=7,
        baseline_start_index=0,
        baseline_stop_index=3,
        polarity="auto",
    )

    assert features.polarity == "negative"
    assert features.peak_amplitude > 3.0
    assert features.pulse_peak_index == 4.0
    assert features.pulse_peak_time_sec == 1.0
    assert features.area > 0.0
    assert features.fwhm_samples > 0.0
    assert features.fwhm_sec == features.fwhm_samples * 0.25
    assert features.baseline_noise_rms > 0.0
    assert features.snr > 1.0


def test_compute_waveform_features_without_interval_reports_sample_features():
    values = [0.0, 0.0, 1.0, 3.0, 1.0, 0.0]

    features = compute_waveform_features(values, 0.0)

    assert features.pulse_peak_index == 3.0
    assert features.area_samples > 0.0
    assert np.isnan(features.area)
    assert np.isnan(features.pulse_peak_time_sec)
    assert np.isnan(features.fwhm_sec)


def test_compute_waveform_features_rejects_empty_or_nonfinite_regions():
    for values in ([], [np.nan, np.nan]):
        try:
            compute_waveform_features(values, 1.0)
        except ValueError:
            pass
        else:
            raise AssertionError("Expected ValueError for unusable waveform")


def test_estimate_waveform_delay_samples_works_without_time_axis():
    left = [0.0, 0.0, 1.0, 2.0, 1.0, 0.0]
    right = [0.0, 0.0, 0.0, 1.0, 2.0, 1.0]

    estimate = estimate_waveform_delay_samples(left, right)

    assert estimate.delay_samples > 0.0
    assert np.isnan(estimate.delay_sec)


def test_estimate_waveform_delay_keeps_rejecting_missing_time_axis():
    try:
        estimate_waveform_delay([0.0, 1.0, 0.0], [0.0, 0.0, 1.0], 0.0)
    except ValueError:
        pass
    else:
        raise AssertionError("Existing seconds API must still require a positive interval")


def test_compute_waveform_features_keeps_all_legacy_values_with_nan_samples():
    values = [1.0, np.nan, 3.0, 5.0, 2.0]

    features = compute_waveform_features(
        values,
        0.2,
        roi_start_index=1,
        roi_stop_index=5,
    )

    assert features.baseline_mean == 3.0
    assert features.peak_value == 5.0
    np.testing.assert_allclose(features.peak_time_sec, 0.6)
    np.testing.assert_allclose(features.integral, 0.3, rtol=1.0e-12)
    np.testing.assert_allclose(features.rms, np.sqrt(5.0 / 4.0))
    assert features.peak_to_peak == 3.0


def test_compute_waveform_features_reports_exact_fwhm_and_positive_auto_polarity():
    features = compute_waveform_features([0.0, 0.0, 1.0, 2.0, 1.0, 0.0], 0.25)

    assert features.polarity == "positive"
    assert features.peak_amplitude == 2.0
    assert features.pulse_peak_index == 3.0
    assert features.fwhm_samples == 2.0
    assert features.fwhm_sec == 0.5


def test_compute_waveform_features_honors_explicit_polarity():
    values = [0.0, 0.0, -1.0, -3.0, -1.0, 0.0]

    automatic = compute_waveform_features(values, 1.0, polarity="auto")
    negative = compute_waveform_features(values, 1.0, polarity="negative")
    positive = compute_waveform_features(values, 1.0, polarity="positive")

    assert automatic.polarity == "negative"
    assert negative.peak_amplitude == 3.0
    assert negative.pulse_peak_index == 3.0
    assert positive.polarity == "positive"
    assert positive.peak_amplitude == 0.0


def test_compute_waveform_features_marks_single_sample_quality_without_failing():
    features = compute_waveform_features([2.0], 0.0)

    assert features.sample_count == 1
    assert features.area_samples == 0.0
    assert np.isnan(features.fwhm_samples)
    assert "waveform_too_short" in features.quality_flags
    assert "fwhm_unavailable" in features.quality_flags
    assert "sample_interval_unavailable" in features.quality_flags
