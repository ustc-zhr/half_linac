import math
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from jitter_analysis.domain.types import RunMode
from jitter_analysis.gui import main_window as main_window_module
from jitter_analysis.gui.state import AppState
from jitter_analysis.services.run_service import RunService
from jitter_analysis.services.task_service import TaskService


pytestmark = pytest.mark.skipif(
    main_window_module.QtWidgets is None,
    reason="PyQt5 is required for MainWindow tests",
)


@pytest.fixture(scope="module")
def qt_app():
    app = main_window_module.QtWidgets.QApplication.instance()
    return app or main_window_module.QtWidgets.QApplication([])


@pytest.fixture
def window(qt_app, monkeypatch):
    monkeypatch.setattr(main_window_module.MainWindow, "_try_load_default_config", lambda self: None)
    result = main_window_module.MainWindow(AppState(), RunService(), TaskService())
    yield result
    result.close()
    result.deleteLater()
    qt_app.processEvents()


def _load_monitor_series(window):
    config_path = Path(__file__).resolve().parents[1] / "configs" / "irfel_pvlist.json"
    assert window._load_config_path(config_path)
    objects = window.loaded_config.objects[:2]
    window.scan_panel.set_task_mode("timed_acquisition")
    window.current_run_mode = RunMode.TIMED_ACQUISITION
    window.current_series_values = {
        objects[0].id: [0.0, 10.0, 20.0, 30.0, 40.0],
        objects[1].id: [1.0, 3.0, 5.0, 7.0, 9.0],
    }
    window.current_series_metadata = {
        obj.id: {
            "sample_indices": [0, 1, 2, 3, 4],
            "step_indices": [None] * 5,
        }
        for obj in objects
    }
    origin = datetime(2026, 1, 1, 0, 0, 0)
    window.current_run_sample_timestamps = [
        origin + timedelta(seconds=offset)
        for offset in (0.0, 0.2, 0.6, 1.2, 2.0)
    ]
    return objects


def _jitter_row_for(window, pv_id):
    for row in range(window.jitter_table.rowCount()):
        item = window.jitter_table.item(row, 0)
        if item is not None and str(item.data(main_window_module.QtCore.Qt.UserRole)) == pv_id:
            return row
    raise AssertionError(f"No jitter table row for {pv_id}")


def test_selected_range_controls_jitter_correlation_and_spectrum_inputs(window):
    objects = _load_monitor_series(window)
    original_values = {
        pv_id: list(values) for pv_id, values in window.current_series_values.items()
    }
    window._on_analysis_range_changed((1, 3))

    window._populate_jitter_table()
    first_row = _jitter_row_for(window, objects[0].id)
    assert window.jitter_table.item(first_row, 1).text() == "3"
    assert float(window.jitter_table.item(first_row, 2).text()) == pytest.approx(20.0)
    assert float(window.jitter_table.item(first_row, 3).text()) == pytest.approx(
        math.sqrt(200.0 / 3.0), rel=1e-5
    )
    assert float(window.jitter_table.item(first_row, 5).text()) == pytest.approx(20.0)
    first_plot_row = next(
        row for row in window.jitter_plot._series_rows if row["pv_id"] == objects[0].id
    )
    assert first_plot_row["sample_indices"] == [0, 1, 2, 3, 4]
    assert first_plot_row["values"] == [0.0, 10.0, 20.0, 30.0, 40.0]
    assert first_plot_row["mean"] == pytest.approx(20.0)
    assert first_plot_row["analysis_count"] == 3
    assert window.jitter_plot.analysis_sample_range() == (1, 3)

    window._populate_correlation_view()
    assert int(window.correlation_plot._valid_counts[0, 1]) == 3
    for series in window.correlation_plot._series_by_name.values():
        aligned = series["values"]
        assert math.isnan(aligned[0])
        assert aligned[1:4]
        assert math.isnan(aligned[4])
    assert "Selected samples 2\N{EN DASH}4 | 1 s" in window.correlation_plot.summary_label.text()

    window._populate_spectrum_view()
    first_source = next(
        source for source in window.spectrum_plot._series_sources if source["pv_id"] == objects[0].id
    )
    assert first_source["values"] == [10.0, 20.0, 30.0]
    assert first_source["sample_indices"] == [1, 2, 3]
    assert first_source["series_sample_interval_sec"] == pytest.approx(0.5)
    assert first_source["sample_times_sec"] == pytest.approx([0.0, 0.4, 1.0])
    range_summary = "Selected samples 2\N{EN DASH}4 | 1 s"
    assert range_summary in window.spectrum_plot.info_label.text()
    window.spectrum_plot.window_combo.setCurrentIndex(
        window.spectrum_plot.window_combo.findData("boxcar")
    )
    assert range_summary in window.spectrum_plot.info_label.text()
    assert window.current_series_values == original_values


def test_range_change_invalidates_only_shared_analysis_tabs_and_resets_with_run(window):
    _load_monitor_series(window)
    window.main_tabs.setCurrentIndex(window.config_tab_index)
    window._analysis_tab_loaded = {
        tab_index: True for tab_index in window._analysis_tab_loaders
    }

    window._on_analysis_range_changed((1, 2))

    targets = set(window._shared_analysis_tab_indices())
    for tab_index in window._analysis_tab_loaders:
        assert window._analysis_tab_loaded[tab_index] is (tab_index not in targets)
    assert window._analysis_sample_range == (1, 2)

    window._reset_analysis_views()
    assert window._analysis_sample_range is None
    assert window.jitter_plot.analysis_sample_range() is None

    window._analysis_sample_range = (0, 1)
    window._prepare_analysis_views_for_current_data()
    assert window._analysis_sample_range is None
    assert window.jitter_plot.analysis_sample_range() is None
