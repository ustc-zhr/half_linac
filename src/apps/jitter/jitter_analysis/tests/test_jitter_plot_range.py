import os
from datetime import datetime, timedelta

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from jitter_analysis.gui.plots import jitter_plot as jitter_plot_module


pytestmark = pytest.mark.skipif(
    jitter_plot_module.QtWidgets is None or jitter_plot_module.pg is None,
    reason="PyQt5 and pyqtgraph are required for JitterPlot range tests",
)


@pytest.fixture(scope="module")
def qt_app():
    app = jitter_plot_module.QtWidgets.QApplication.instance()
    return app or jitter_plot_module.QtWidgets.QApplication([])


def _row():
    return {
        "pv_id": "pv_1",
        "label": "PV 1",
        "unit": "V",
        "sample_indices": [0, 2, 4, 6, 8],
        "values": [10.0, 11.0, 12.0, 13.0, 14.0],
        "mean": 12.0,
        "std": 2.0 ** 0.5,
        "relative_jitter_percent": 10.0,
        "p2p": 4.0,
        "raw_count": 5,
        "analysis_count": 5,
        "removed_count": 0,
    }


def test_jitter_range_survives_axis_switches_downsampling_and_redraw(qt_app):
    plot = jitter_plot_module.JitterPlot()
    emitted = []
    plot.analysisRangeChanged.connect(emitted.append)
    origin = datetime(2026, 1, 1, 12, 0, 0)
    timestamps = [origin + timedelta(seconds=index * 0.5) for index in range(9)]
    try:
        plot.set_series_rows([_row()], sample_timestamps=timestamps)
        assert plot.select_range_button.isEnabled()

        plot.select_range_button.click()
        assert plot.analysis_sample_range() == (0, 8)
        assert emitted[-1] == (0, 8)
        assert "Selected samples 1\N{EN DASH}9" in plot.range_summary_label.text()
        assert "4 s" in plot.range_summary_label.text()

        plot.set_analysis_sample_range((2, 6))
        assert tuple(plot._analysis_region.getRegion()) == pytest.approx((2.0, 6.0))

        plot.axis_combo.setCurrentIndex(plot.axis_combo.findData("elapsed_time"))
        assert plot.analysis_sample_range() == (2, 6)
        assert tuple(plot._analysis_region.getRegion()) == pytest.approx((1.0, 3.0))

        plot.axis_combo.setCurrentIndex(plot.axis_combo.findData("clock_time"))
        start, end = plot._analysis_region.getRegion()
        assert start == pytest.approx(timestamps[2].timestamp())
        assert end == pytest.approx(timestamps[6].timestamp())

        plot.downsample_check.setChecked(False)
        plot.display_mode_combo.setCurrentIndex(1)
        assert plot.analysis_sample_range() == (2, 6)
        assert plot._analysis_region is not None

        plot.axis_combo.setCurrentIndex(plot.axis_combo.findData("sample_index"))
        emitted_before_drag = len(emitted)
        blocker = jitter_plot_module.QtCore.QSignalBlocker(plot._analysis_region)
        plot._analysis_region.setRegion((3.6, 7.9))
        del blocker
        plot._on_analysis_region_changed()
        assert len(emitted) == emitted_before_drag
        plot._on_analysis_region_finished()
        assert plot.analysis_sample_range() == (4, 8)
        assert emitted[-1] == (4, 8)

        plot.use_all_data_button.click()
        assert plot.analysis_sample_range() is None
        assert emitted[-1] is None
        assert plot.range_summary_label.text() == "All data"
    finally:
        plot.close()
        plot.deleteLater()
        qt_app.processEvents()
