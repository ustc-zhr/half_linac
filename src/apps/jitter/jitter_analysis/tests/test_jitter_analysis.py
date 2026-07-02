from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jitter_analysis.analysis.jitter import (
    compute_jitter_stats,
    filter_jitter_outliers,
    transform_jitter_values,
)


def test_compute_jitter_stats():
    stats = compute_jitter_stats([1.0, 2.0, 3.0, 4.0])
    assert stats.count == 4
    assert stats.mean == 2.5
    assert round(stats.std, 6) == 1.118034
    assert round(stats.rms, 6) == 1.118034
    assert round(stats.peak_to_peak, 6) == 3.0


def test_transform_jitter_values_mean_centered():
    transformed = transform_jitter_values([1.0, 2.0, 3.0], "mean_centered")

    assert transformed.applied_mode == "mean_centered"
    assert transformed.values == [-1.0, 0.0, 1.0]


def test_transform_jitter_values_z_score():
    transformed = transform_jitter_values([1.0, 2.0, 3.0], "z_score")

    assert transformed.applied_mode == "z_score"
    assert [round(value, 6) for value in transformed.values] == [-1.224745, 0.0, 1.224745]


def test_transform_jitter_values_constant_series_falls_back_to_mean_centered():
    transformed = transform_jitter_values([5.0, 5.0, 5.0], "z_score")

    assert transformed.applied_mode == "mean_centered"
    assert transformed.values == [0.0, 0.0, 0.0]


def test_filter_jitter_outliers_disabled_returns_all_values():
    result = filter_jitter_outliers([1.0, 2.0, 3.0], enabled=False)

    assert result.applied_mode == "off"
    assert result.values == [1.0, 2.0, 3.0]
    assert result.kept_indices == [0, 1, 2]
    assert result.removed_count == 0


def test_filter_jitter_outliers_removes_extreme_value_with_robust_z_score():
    result = filter_jitter_outliers([1.0, 1.1, 0.9, 1.05, 25.0], threshold=4.0)

    assert result.applied_mode == "robust_z_score"
    assert result.values == [1.0, 1.1, 0.9, 1.05]
    assert result.kept_indices == [0, 1, 2, 3]
    assert result.removed_count == 1


def test_filter_jitter_outliers_handles_flat_series_spike_with_fallback():
    result = filter_jitter_outliers([5.0, 5.0, 5.0, 5.0, 100.0], threshold=10.0)

    assert result.applied_mode == "median_majority_fallback"
    assert result.values == [5.0, 5.0, 5.0, 5.0]
    assert result.kept_indices == [0, 1, 2, 3]
    assert result.removed_count == 1
