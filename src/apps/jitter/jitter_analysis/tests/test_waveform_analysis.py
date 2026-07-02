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
