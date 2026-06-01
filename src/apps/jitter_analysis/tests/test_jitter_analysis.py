from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jitter_analysis.analysis.jitter import compute_jitter_stats, transform_jitter_values


def test_compute_jitter_stats():
    stats = compute_jitter_stats([1.0, 2.0, 3.0, 4.0])
    assert stats.count == 4
    assert stats.mean == 2.5
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
