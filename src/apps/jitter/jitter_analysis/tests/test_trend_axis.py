import os

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from jitter_analysis.gui.plots import trend_plot as trend_plot_module


pytestmark = pytest.mark.skipif(
    trend_plot_module.TrendAxisItem is None,
    reason="PyQt5 and pyqtgraph are required for TrendAxisItem tests",
)


@pytest.fixture(scope="module")
def qt_app():
    app = trend_plot_module.QtWidgets.QApplication.instance()
    return app or trend_plot_module.QtWidgets.QApplication([])


def test_elapsed_time_ticks_preserve_integer_trailing_zeroes(qt_app):
    axis = trend_plot_module.TrendAxisItem(orientation="bottom")
    axis.set_axis_mode("elapsed_time")

    labels = axis.tickStrings([1000.0, 10000.0, 11000.0, 22000.0], 1.0, 1000.0)

    assert labels == ["1000", "10000", "11000", "22000"]


def test_elapsed_time_ticks_trim_only_fractional_zeroes(qt_app):
    axis = trend_plot_module.TrendAxisItem(orientation="bottom")
    axis.set_axis_mode("elapsed_time")

    labels = axis.tickStrings([1.0, 1.25, 1000.5], 1.0, 0.5)

    assert labels == ["1", "1.25", "1000.5"]


def test_custom_time_axis_disables_pyqtgraph_exponent_scale(qt_app):
    axis = trend_plot_module.TrendAxisItem(orientation="bottom")
    axis.setLabel("Clock Time")
    axis.setRange(1_800_000_000.0, 1_800_020_000.0)

    assert axis.autoSIPrefix is False
    assert axis.autoSIPrefixScale == 1.0
    assert "(x" not in axis.labelString()
