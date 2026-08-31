import os
from types import SimpleNamespace

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from jitter_analysis.gui.widgets import scan_panel as scan_panel_module


pytestmark = pytest.mark.skipif(
    scan_panel_module.QtWidgets is None,
    reason="PyQt5 is required for ScanPanel tests",
)


@pytest.fixture(scope="module")
def qt_app():
    app = scan_panel_module.QtWidgets.QApplication.instance()
    return app or scan_panel_module.QtWidgets.QApplication([])


def _select_stop_mode(panel, mode: str) -> None:
    panel.stop_condition_combo.setCurrentIndex(panel.stop_condition_combo.findData(mode))


def test_monitor_stop_condition_shows_only_relevant_controls(qt_app):
    panel = scan_panel_module.ScanPanel()

    _select_stop_mode(panel, "samples")
    assert not panel.count_spin.isHidden()
    assert panel.duration_spin.isHidden()
    assert not panel.monitor_estimate_label.isHidden()

    _select_stop_mode(panel, "duration")
    assert panel.count_spin.isHidden()
    assert not panel.duration_spin.isHidden()
    assert not panel.monitor_estimate_label.isHidden()

    _select_stop_mode(panel, "continuous")
    assert panel.count_spin.isHidden()
    assert panel.duration_spin.isHidden()
    assert panel.monitor_estimate_label.isHidden()


def test_single_knob_page_uses_compact_form_and_only_refreshes_live_center(qt_app):
    panel = scan_panel_module.ScanPanel()

    assert not hasattr(panel, "single_intro_label")
    assert panel.single_knob_page.findChildren(scan_panel_module.QtWidgets.QGroupBox) == []
    assert panel.active_knob_combo.isEditable()
    assert panel.active_knob_combo.objectName() == "activeKnobCombo"
    assert panel.active_knob_dropdown_button.objectName() == "activeKnobDropdownButton"
    assert panel.active_knob_dropdown_button.text() == "\u25be"
    assert panel.active_knob_dropdown_button.toolTip() == "Show selected control PVs"
    assert panel.active_knob_combo.height() == panel.active_knob_dropdown_button.height()
    assert panel.step_sample_spin.value() == 5
    assert panel.settle_spin.value() == 1.5
    assert panel.random_samples_per_point_spin.value() == 5
    assert panel.random_settle_spin.value() == 1.5
    assert panel.scan_value_mode() == "symmetric_points"
    assert panel.scan_value_stack.currentWidget() is panel.symmetric_page
    assert not panel.preview_refresh_button.isHidden()

    manual_index = panel.scan_value_mode_combo.findData("manual")
    panel.scan_value_mode_combo.setCurrentIndex(manual_index)
    assert panel.preview_refresh_button.isHidden()


def test_single_knob_scope_warning_only_appears_for_extra_selected_knobs(qt_app):
    panel = scan_panel_module.ScanPanel()
    limits = SimpleNamespace(low=-1.0, high=1.0)
    knobs = [
        SimpleNamespace(
            id="k1", name="K1", write_pv="TEST:K1", readback_pv="TEST:K1:RB",
            group="corrector", limits=limits, unit="A", step_hint=0.1,
        ),
        SimpleNamespace(
            id="k2", name="K2", write_pv="TEST:K2", readback_pv="TEST:K2:RB",
            group="corrector", limits=limits, unit="A", step_hint=0.1,
        ),
    ]

    panel.set_knob_choices(knobs[:1], active_knob_id="k1")
    assert panel.single_scope_label.isHidden()

    panel.set_knob_choices(knobs, active_knob_id="k1")
    assert not panel.single_scope_label.isHidden()
    assert "other 1 selected" in panel.single_scope_label.text()


def test_random_page_uses_compact_form_and_hides_advanced_options(qt_app):
    panel = scan_panel_module.ScanPanel()

    assert not hasattr(panel, "random_intro_label")
    assert panel.random_page.findChildren(scan_panel_module.QtWidgets.QGroupBox) == []
    assert panel.random_point_count_spin.value() == 20
    assert panel.random_samples_per_point_spin.value() == 5
    assert panel.random_settle_spin.value() == 1.5
    assert panel.random_preview_button.text() == "Refresh"
    assert panel.random_preview_show_button.text() == "Details"
    assert not hasattr(panel, "random_seed_edit")
    assert not hasattr(panel, "random_more_button")
    assert panel.random_sampling_method_combo.count() == 2
    assert panel.random_sampling_method_combo.itemData(0) == "uniform_random"
    assert panel.random_sampling_method_combo.itemData(1) == "grid"
    assert "Influence" in panel.random_sampling_method_combo.toolTip()
    panel.random_sampling_method_combo.setCurrentIndex(1)
    assert panel.random_count_stack.currentIndex() == 1
    assert panel.random_levels_spin.value() == 3
