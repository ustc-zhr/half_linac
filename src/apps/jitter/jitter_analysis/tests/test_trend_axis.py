import os
from types import SimpleNamespace

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from jitter_analysis.gui.plots import jitter_plot as jitter_plot_module
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


def test_run_trend_show_uses_analysis_variable_selector(qt_app, monkeypatch):
    captured = {}

    class FakeVisibilityDialog:
        def __init__(self, rows, visible_pv_ids, parent=None):
            captured["rows"] = rows
            captured["visible_pv_ids"] = list(visible_pv_ids)
            captured["parent"] = parent

        def exec_(self):
            return trend_plot_module.QtWidgets.QDialog.Accepted

        def selected_pv_ids(self):
            return ["llrf_phase"]

    monkeypatch.setattr(
        jitter_plot_module,
        "SeriesVisibilityDialog",
        FakeVisibilityDialog,
    )
    plot = trend_plot_module.TrendPlot()
    plot.set_group_labels(
        {
            "bpm": "BPM",
            "llrf": "LLRF",
        }
    )
    plot.reset_channels(
        [
            SimpleNamespace(
                id="bpm_x",
                name="BPM X",
                group="bpm",
                read_pv="TEST:BPM:X",
                tags=["orbit"],
            ),
            SimpleNamespace(
                id="llrf_phase",
                name="LLRF Phase",
                group="llrf",
                read_pv="TEST:LLRF:PHASE",
                tags=["phase"],
            ),
        ]
    )

    assert plot.series_button.menu() is None
    assert plot.series_button.text() == "Visible Variables (1/2)"
    assert plot.current_pv_ids() == ["bpm_x"]

    plot.series_button.click()

    assert captured["visible_pv_ids"] == ["bpm_x"]
    assert captured["parent"] is plot
    assert captured["rows"][1] == {
        "pv_id": "llrf_phase",
        "label": "LLRF Phase",
        "group_id": "llrf",
        "group_label": "LLRF",
        "read_pv": "TEST:LLRF:PHASE",
        "tags": ["phase"],
    }
    assert plot.current_pv_ids() == ["llrf_phase"]
    assert plot.series_button.text() == "Visible Variables (1/2)"

    plot.close()
    plot.deleteLater()
    qt_app.processEvents()
