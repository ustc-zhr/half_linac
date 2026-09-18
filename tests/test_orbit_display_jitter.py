"""Short-window orbit jitter arithmetic and sample handling."""

import pytest

from half_linac.src.apps.orbit_display.jitter_data import JitterSamples


def test_mean_centered_rms_and_valid_count():
    data = JitterSamples(2)
    for index in range(10):
        data.add(index, [1 if index % 2 else -1, 8], [3, None], 2)
    assert data.rms(0, "x") == pytest.approx((2, 10))
    assert data.rms(1, "x") == pytest.approx((0, 10))
    assert data.rms(1, "y") == (None, 0)


def test_invalid_values_leave_gaps_and_window_rolls():
    data = JitterSamples(2)
    for index in range(121):
        data.add(index, [index, None] if index != 120 else [float("nan"), "bad"],
                 [float("inf"), 2], 1)
    assert len(data.samples) == 120
    assert len(data.visible()) == 30
    assert data.series(0, "x")[-1] == (120, None)
    assert data.rms(0, "x")[1] == 29
    assert data.rms(0, "y") == (None, 0)
    data.set_window_size(120)
    assert data.visible()[0][0] == 1
    data.clear()
    assert data.visible() == []


def test_short_samples_do_not_produce_jitter_value():
    data = JitterSamples(1)
    for index in range(9):
        data.add(index, [index], [index], 1)
    assert data.rms(0, "x") == (None, 9)
    data.add(9, [9], [9], 1)
    assert data.rms(0, "x")[0] == pytest.approx(2.8722813232690143)
