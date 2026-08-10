from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jitter_analysis.gui.plots.visibility import (
    downsample_series_min_max,
    padded_finite_range,
    resolve_initial_visibility,
    slice_series_tail,
)


class _SliceOnlySequence:
    def __init__(self, values) -> None:
        self._values = list(values)
        self.slice_requests = []

    def __len__(self) -> int:
        return len(self._values)

    def __getitem__(self, item):
        if isinstance(item, slice):
            self.slice_requests.append(item)
            return self._values[item]
        return self._values[item]

    def __iter__(self):
        raise AssertionError("slice_series_tail should not iterate the full sequence for recent windows")


def test_resolve_initial_visibility_uses_default_limit_for_new_series():
    visibility = resolve_initial_visibility(
        ["pv1", "pv2", "pv3", "pv4"],
        default_visible_count=2,
    )

    assert visibility == {
        "pv1": True,
        "pv2": True,
        "pv3": False,
        "pv4": False,
    }


def test_resolve_initial_visibility_preserves_previous_user_selection():
    visibility = resolve_initial_visibility(
        ["pv1", "pv2", "pv3"],
        previous_visibility={"pv1": False, "pv2": True, "pv3": False},
        default_visible_count=1,
    )

    assert visibility == {
        "pv1": False,
        "pv2": True,
        "pv3": False,
    }


def test_resolve_initial_visibility_respects_explicit_visible_keys():
    visibility = resolve_initial_visibility(
        ["pv1", "pv2", "pv3"],
        previous_visibility={"pv1": True, "pv2": False, "pv3": True},
        explicit_visible_keys=["pv2"],
        default_visible_count=3,
    )

    assert visibility == {
        "pv1": False,
        "pv2": True,
        "pv3": False,
    }


def test_slice_series_tail_returns_recent_window():
    x_values, y_values = slice_series_tail(
        [1, 2, 3, 4, 5],
        [10, 20, 30, 40, 50],
        max_points=3,
    )

    assert x_values == [3, 4, 5]
    assert y_values == [30, 40, 50]


def test_slice_series_tail_returns_full_history_when_limit_is_large():
    x_values, y_values = slice_series_tail(
        [1, 2, 3],
        [10, 20, 30],
        max_points=10,
    )

    assert x_values == [1, 2, 3]
    assert y_values == [10, 20, 30]


def test_slice_series_tail_uses_direct_tail_slices_for_recent_window():
    x_source = _SliceOnlySequence(range(10))
    y_source = _SliceOnlySequence(range(100, 110))

    x_values, y_values = slice_series_tail(
        x_source,
        y_source,
        max_points=3,
    )

    assert x_values == [7, 8, 9]
    assert y_values == [107, 108, 109]
    assert x_source.slice_requests == [slice(-3, None, None)]
    assert y_source.slice_requests == [slice(-3, None, None)]


def test_downsample_series_min_max_preserves_bucket_extrema():
    x_values, y_values, downsampled = downsample_series_min_max(
        list(range(8)),
        [0.0, 10.0, 1.0, 2.0, -5.0, 3.0, 4.0, 20.0],
        max_points=4,
    )

    assert downsampled is True
    assert x_values == [0, 1, 4, 7]
    assert y_values == [0.0, 10.0, -5.0, 20.0]


def test_downsample_series_min_max_returns_original_when_small_enough():
    x_values, y_values, downsampled = downsample_series_min_max(
        [0, 1, 2],
        [1.0, 2.0, 3.0],
        max_points=4,
    )

    assert downsampled is False
    assert x_values == [0, 1, 2]
    assert y_values == [1.0, 2.0, 3.0]


def test_padded_finite_range_adds_padding_to_data_range():
    lower, upper = padded_finite_range([1.0, 3.0], padding_fraction=0.1)

    assert round(lower, 6) == 0.8
    assert round(upper, 6) == 3.2


def test_padded_finite_range_handles_constant_series():
    lower, upper = padded_finite_range([5.0, 5.0, 5.0], padding_fraction=0.1)

    assert lower == 4.5
    assert upper == 5.5


def test_padded_finite_range_ignores_non_finite_values():
    assert padded_finite_range([float("nan"), float("inf")]) is None

    lower, upper = padded_finite_range([float("nan"), -1.0, 1.0, float("-inf")])

    assert lower < -1.0
    assert upper > 1.0
