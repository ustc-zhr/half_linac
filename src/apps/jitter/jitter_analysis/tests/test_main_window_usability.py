import os
from datetime import datetime
from pathlib import Path

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from jitter_analysis.domain.types import MultiKnobStepRecord, RunMode, RunStatus, SampleRecord
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


def test_setup_banner_offers_direct_action_when_config_is_missing(window):
    assert window.mode_status_action_button.text() == "Load PV Library"
    assert not window.mode_status_action_button.isHidden()


def test_loaded_config_keeps_setup_in_manual_control(window):
    config_path = Path(__file__).resolve().parents[1] / "configs" / "irfel_pvlist.json"
    assert window._load_config_path(config_path)
    assert not hasattr(window, "preset_combo")
    assert window.state.selected_object_ids == []
    assert window.scan_panel.task_mode() == "timed_acquisition"
    assert window.state.run_status == RunStatus.IDLE
    assert window.mode_status_title_label.text() == "Setup incomplete"

    window.state.selected_object_ids = [window.loaded_config.objects[0].id]
    window._refresh_selected_pvs()
    assert window.mode_status_banner.isHidden()
    assert window.run_start_button.isEnabled()


def test_run_completion_uses_non_modal_banner(window):
    window.current_run_record_count = 25
    window.status_panel.set_elapsed("00:12")

    window._show_run_completion_banner(RunStatus.COMPLETED, Path("/tmp/run/metadata.json"), None)

    assert not window.run_completion_banner.isHidden()
    assert "Completed · 25 samples · 00:12 · saved successfully" in window.run_completion_label.text()
    assert window.main_tabs.currentIndex() == window.run_tab_index


def test_single_knob_analysis_keeps_only_response_and_sensitivity_visible(window):
    window.scan_panel.set_task_mode("single_knob_scan")
    window._refresh_result_tabs("single_knob_scan")

    assert window.run_plot_stack.currentWidget() is window.run_response_plot
    tab_bar = window.analysis_tabs.tabBar()
    assert tab_bar.isTabVisible(window.response_tab_index)
    assert tab_bar.isTabVisible(window.sensitivity_tab_index)
    assert not tab_bar.isTabVisible(window.waveform_tab_index)
    assert not tab_bar.isTabVisible(window.jitter_tab_index)
    assert not tab_bar.isTabVisible(window.correlation_tab_index)
    assert not tab_bar.isTabVisible(window.spectrum_tab_index)
    assert not window.analysis_more_button.isHidden()
    assert window.analysis_axis_combo.isHidden()

    window.analysis_axis_actions["target"].trigger()
    assert window.analysis_axis_combo.currentData() == "target"
    assert "Target" in window.analysis_more_button.toolTip()

    window.scan_panel.set_task_mode("timed_acquisition")
    window._refresh_result_tabs("timed_acquisition")
    assert window.run_plot_stack.currentWidget() is window.trend_plot

    window.scan_panel.set_task_mode("multi_knob_random")
    window._refresh_result_tabs("multi_knob_random")
    assert window.run_plot_stack.currentWidget() is window.run_response_plot
    assert tab_bar.isTabVisible(window.response_tab_index)
    assert tab_bar.isTabVisible(window.influence_tab_index)
    assert not tab_bar.isTabVisible(window.response_map_tab_index)
    assert not tab_bar.isTabVisible(window.sensitivity_tab_index)
    assert not tab_bar.isTabVisible(window.jitter_tab_index)
    assert not tab_bar.isTabVisible(window.correlation_tab_index)
    assert not tab_bar.isTabVisible(window.spectrum_tab_index)
    assert window.analysis_more_button.isHidden()


def test_grid_response_map_is_only_visible_for_two_changing_control_pvs(window):
    window.current_run_details = {
        "sampling_method": "grid",
        "knob_ranges": [
            {"knob_id": "k1", "low": -1.0, "high": 1.0},
            {"knob_id": "k2", "low": 0.0, "high": 2.0},
            {"knob_id": "fixed", "low": 3.0, "high": 3.0},
        ],
    }
    window._refresh_result_tabs("multi_knob_random")
    assert window.analysis_tabs.tabBar().isTabVisible(window.response_map_tab_index)

    window.current_run_details["knob_ranges"].append(
        {"knob_id": "k3", "low": -2.0, "high": 2.0}
    )
    window._refresh_result_tabs("multi_knob_random")
    assert not window.analysis_tabs.tabBar().isTabVisible(window.response_map_tab_index)


def test_random_influence_view_uses_saved_step_readbacks_and_responses(window):
    config_path = Path(__file__).resolve().parents[1] / "configs" / "irfel_pvlist.json"
    assert window._load_config_path(config_path)
    knobs = window.loaded_config.knobs[:2]
    response = window.loaded_config.objects[0]
    points = [
        (-2.0, -1.0),
        (-2.0, 1.0),
        (-1.0, -2.0),
        (-1.0, 2.0),
        (0.0, -1.0),
        (0.0, 1.0),
        (1.0, -2.0),
        (1.0, 2.0),
        (2.0, -1.0),
        (2.0, 1.0),
    ]
    timestamp = datetime(2026, 1, 1)
    window.current_run_steps = [
        MultiKnobStepRecord(
            step_index=index,
            target_values={knobs[0].id: k1, knobs[1].id: k2},
            readback_values={knobs[0].id: k1, knobs[1].id: k2},
            started_at=timestamp,
            samples=[
                SampleRecord(
                    response.id,
                    3.0 * k1 - 2.0 * k2,
                    timestamp,
                    step_index=index,
                )
            ],
        )
        for index, (k1, k2) in enumerate(points)
    ]
    window.current_run_mode = RunMode.MULTI_KNOB_RANDOM
    window.current_run_details = {
        "knob_ranges": [{"knob_id": knob.id} for knob in knobs],
        "target_object_ids": [response.id],
        "num_points": len(points),
    }

    window._populate_influence_view()

    assert window.influence_plot.overview_table.rowCount() == 1
    assert window.influence_plot.matrix_table.columnCount() == 2
    assert window.influence_plot.overview_table.item(0, 3).text() == "1"

    window._set_random_point_context(window.current_run_steps[0])
    assert "Point 1 / 10" in window.random_point_context_label.text()
    assert knobs[0].name in window.random_point_context_label.text()
    assert window.random_point_details_button.isEnabled()


def test_random_seed_is_generated_internally_and_invalidated_when_plan_changes(window, monkeypatch):
    config_path = Path(__file__).resolve().parents[1] / "configs" / "irfel_pvlist.json"
    assert window._load_config_path(config_path)
    knob = window.loaded_config.knobs[0]
    window.scan_panel.scan_value_mode_combo.setCurrentIndex(0)
    window.state.selected_knob_ids = [knob.id]
    window.state.active_knob_id = knob.id
    window._refresh_selected_pvs()
    monkeypatch.setattr(window, "_new_random_seed", lambda: 123456)

    window.refresh_random_preview()

    assert window._random_preview_seed == 123456
    assert "seed" not in window.scan_panel.random_preview_summary_label.text().lower()
    assert not hasattr(window.scan_panel, "random_seed_edit")

    window.scan_panel.random_point_count_spin.setValue(
        window.scan_panel.random_point_count_spin.value() + 1
    )
    assert window._random_preview_seed is None
