import os
from pathlib import Path

import numpy as np
import pytest


def test_main_window_constructs_offscreen() -> None:
    pytest.importorskip("PyQt5")
    from PyQt5.QtCore import QLibraryInfo

    plugin_root = Path(QLibraryInfo.location(QLibraryInfo.PluginsPath))
    platforms = plugin_root / "platforms"
    if not platforms.exists():
        pytest.skip("Qt platform plugins are not installed")
    if (platforms / "libqoffscreen.so").exists():
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    elif (platforms / "libqminimal.so").exists():
        os.environ.setdefault("QT_QPA_PLATFORM", "minimal")
    else:
        pytest.skip("No offscreen/minimal Qt platform plugin is installed")

    from PyQt5.QtWidgets import QApplication, QListWidgetItem

    from half_linac.src.apps.dispersion_correction.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    assert window.windowTitle() == "Dispersion Correction"
    assert window.tabs.count() == 6
    assert window.tabs.tabText(0) == "Plan"
    assert window.tabs.tabText(1) == "Calibration"
    assert not hasattr(window, "plan_button")
    assert not hasattr(window, "backend_combo")
    assert not hasattr(window, "mode_combo")
    assert window.status_strip.items["BACKEND"].value_label.text() == "OFFLINE"
    assert window.status_strip.items["SAFETY"].value_label.text() == "READY"
    assert window.calibration_button.parentWidget() is window.calibration_page
    assert window.measure_button.parentWidget() is window.measure_page
    assert window.response_button.parentWidget() is window.response_page
    assert window.measure_button.text() == "Measure D_eff"
    assert window.response_button.text() == "Measure Response"
    assert "Dispersion Correction Dry Run" in window.plan_text.toPlainText()
    assert not hasattr(window, "knob_table")
    assert window.knob_edit.text() == "Q1L/Q1R; Q2L/Q2R"
    assert "Q1_sym" in window.knob_edit.toolTip()
    from half_linac.src.apps.dispersion_correction.config import load_config

    window.config = load_config("tests/dispersion_correction/fixtures/irfel_achromat.json")
    window._load_config_to_widgets()
    assert window._config_from_widgets().backend == window.config.backend
    assert window.status_strip.items["BACKEND"].value_label.text() == "EPICS"
    assert window.status_strip.items["SAFETY"].value_label.text() == "NOT READY"
    assert window.knob_edit.text() == "QM13/QM16; QM14/QM15"
    assert " A" in window.knob_edit.toolTip()
    window.resize(1248, 803)
    window.show()
    app.processEvents()
    assert window.primary_action_stack.currentWidget() is window.run_button
    assert window.load_button.width() < window.primary_action_stack.width()
    assert abs(window.load_button.geometry().center().y() - window.config_title_label.geometry().center().y()) <= 1
    assert window.load_button.property("role") is None
    assert not window.abort_button.isEnabled()
    window._set_running(True, "measure")
    assert window.primary_action_stack.currentWidget() is window.abort_button
    assert window.abort_button.isEnabled()
    assert window.progress_widget.isVisible()
    window._update_progress("Sampling +Δp/p", 2, 5)
    assert window.operation_progress.value() == 40
    assert window.progress_percent_label.text() == "40%"
    window._set_running(False, "")
    assert window.primary_action_stack.currentWidget() is window.run_button
    assert not window.progress_widget.isVisible()
    tab_bar = window.tabs.tabBar()
    tab_widths = [tab_bar.tabRect(index).width() for index in range(tab_bar.count())]
    assert max(tab_widths) - min(tab_widths) <= 1
    assert tab_bar.tabRect(tab_bar.count() - 1).right() == tab_bar.width() - 1
    window.close()

    from half_linac.src.apps.dispersion_correction.profile_runtime import load_profile_run_config
    from half_linac.src.shared.machine_profile import load_app_context

    context = load_app_context(
        "dispersion_correction",
        machine_id="irfel",
        control_backend="real",
    )
    _, profile_config = load_profile_run_config(context)
    profile_window = MainWindow(profile_config, context)
    profile_window.show()
    app.processEvents()
    assert profile_window.status_strip.items["BACKEND"].value_label.text() == "REAL"
    assert profile_window.load_button.isHidden()
    assert profile_window.config_title_label.text() == "Machine Profile"
    assert profile_window.model_response_button.isHidden()
    assert not profile_window.run_button.isEnabled()
    assert profile_window.bpm_select_button.isVisibleTo(profile_window)
    assert profile_window.bpm_select_button.height() == profile_window.bpm_edit.height() == 34
    assert profile_window.knob_select_button.height() == profile_window.knob_edit.height() == 34
    assert profile_window.preflight_button.height() == profile_window.config_title_label.height() == 34
    assert profile_window.bpm_select_button.geometry().top() == profile_window.bpm_edit.geometry().top()
    assert profile_window.bpm_select_button.geometry().bottom() == profile_window.bpm_edit.geometry().bottom()
    assert profile_window.preflight_button.geometry().top() == profile_window.config_title_label.geometry().top()
    assert profile_window.preflight_button.geometry().bottom() == profile_window.config_title_label.geometry().bottom()
    assert "QListWidget#bpmSelectionList" in profile_window.styleSheet()
    bpm_item = QListWidgetItem()
    profile_window._set_bpm_choice_item(bpm_item, "BPM01", True)
    assert bpm_item.text().startswith("✓")
    profile_window._toggle_bpm_choice_item(bpm_item)
    assert not bpm_item.text().startswith("✓")
    profile_window.bpm_edit.setText("BPM08, BPM09, BPM10")
    knob_dialog, knob_table, _buttons = profile_window._build_knob_selection_dialog()
    assert knob_table.horizontalHeaderItem(3).text() == "Scan ± (A)"
    knob_table.cellWidget(0, 1).setCurrentText("QM11")
    knob_table.cellWidget(0, 2).setCurrentText("QM12")
    knob_table.cellWidget(1, 1).setCurrentText("QM17")
    knob_table.cellWidget(1, 2).setCurrentText("QM18")
    knob_table.cellWidget(0, 3).setValue(0.0004)
    knob_table.cellWidget(0, 4).setValue(0.01)
    profile_window.selected_knobs = profile_window._knobs_from_table(knob_table)
    profile_window._update_knob_summary()
    selected_config = profile_window._config_from_widgets()
    assert selected_config.target_bpms == ("BPM08", "BPM09", "BPM10")
    assert selected_config.knobs[0].name == "QM11_QM12_sym"
    assert selected_config.knobs[0].scan_step == pytest.approx(0.0004)
    assert selected_config.knobs[0].limit == pytest.approx(0.01)
    assert knob_table.cellWidget(0, 4).maximum() == pytest.approx(0.012)
    assert profile_window.knob_edit.text() == "QM11/QM12; QM17/QM18"
    assert set(selected_config.backend.options["pv_map"]["quadrupoles"]) == {
        "QM11",
        "QM12",
        "QM17",
        "QM18",
    }
    knob_dialog.close()
    profile_window.close()

    half_context = load_app_context(
        "dispersion_correction",
        machine_id="half",
        control_backend="vm",
    )
    _, half_config = load_profile_run_config(half_context)
    half_window = MainWindow(half_config, half_context)
    half_window.show()
    app.processEvents()
    assert half_window.section_combo.currentData() == "bl01"
    assert half_window.model_source_combo.itemText(0) == "Design lattice"
    assert half_window.model_source_combo.itemText(1) == "Current snapshot"
    assert half_window.model_source_combo.itemData(1) == "live"
    assert "VM backend" in half_window.model_source_combo.toolTip()
    assert half_window.model_boundary_label.text() == "Assume D=D'=0 at BPM02"
    assert half_window.model_response_button.text() == "Analyze + Predict Correction"
    assert not half_window.model_response_button.isHidden()
    assert half_window.model_response_button.isEnabled()
    assert half_window.dispersion_curve.result is None
    assert not half_window.measure_button.isEnabled()
    assert not half_window.response_button.isEnabled()
    assert not half_window.run_button.isEnabled()
    assert not half_window.bpm_select_button.isVisibleTo(half_window)
    assert not half_window.knob_select_button.isVisibleTo(half_window)
    assert half_window.status_strip.items["SAFETY"].value_label.text() == "MODEL ONLY"
    from half_linac.src.apps.dispersion_correction.models import (
        ImportedDispersionDataset,
        ModelOpticsCurve,
        ModelResponseResult,
    )

    curve = ModelOpticsCurve(
        element_names=("_BEG_", "BL01A", "QL01", "BPM06"),
        element_types=("MARK", "CSBEND", "QUAD", "MONI"),
        element_occurrences=(1, 1, 1, 1),
        element_lengths_m=np.asarray([0.0, 0.35, 0.15, 0.0]),
        element_k1_m2=np.asarray([np.nan, np.nan, 6.0, np.nan]),
        element_angles_rad=np.asarray([np.nan, -0.3, np.nan, np.nan]),
        element_tilts_rad=np.zeros(4),
        s_m=np.asarray([0.0, 1.0, 2.0, 3.0]),
        dx_mm=np.asarray([0.0, 10.0, 3.0, 0.1]),
        dxp_mrad=np.asarray([0.0, 2.0, 1.0, 0.01]),
        dy_mm=np.zeros(4),
        dyp_mrad=np.zeros(4),
        beta_x_m=np.asarray([10.0, 12.0, 11.0, 10.0]),
        beta_y_m=np.asarray([9.0, 10.0, 11.0, 12.0]),
    )
    model_result = ModelResponseResult(
        section_id="bl01",
        observable_names=("BPM06 Dx", "BPM06 Dx'"),
        observable_elements=("BPM06", "BPM06"),
        observable_components=("dx", "dxp"),
        observable_units=("mm", "mrad"),
        knob_names=("QL01_QL06_sym",),
        baseline_values=np.asarray([0.1, 0.01]),
        target_values=np.zeros(2),
        response_matrix=np.asarray([[1.0], [0.1]]),
        singular_values=np.asarray([1.0]),
        condition_number=1.0,
        retained_rank=1,
        derived_knobs=(),
        baseline_curve=curve,
        preview_knob_deltas={"QL01_QL06_sym": -0.1},
        preview_values=np.zeros(2),
        preview_curve=curve,
    )
    half_window._show_model_response(model_result)
    half_window._set_running(False, "")
    app.processEvents()
    assert half_window.dispersion_curve.result is model_result
    assert half_window.import_measurement_button.isEnabled()
    assert not half_window.clear_measurement_button.isEnabled()
    imported = ImportedDispersionDataset(
        section_id="bl01",
        bpm_names=("BPM06",),
        etax_mm=np.asarray([0.25]),
        etax_sigma_mm=np.asarray([0.08]),
        source_path="/tmp/bl01_etax.csv",
    )
    half_window.imported_dispersion = imported
    half_window.dispersion_curve.set_measurement(imported)
    half_window._show_imported_comparison(model_result, imported)
    half_window._set_running(False, "")
    assert half_window.clear_measurement_button.isEnabled()
    assert half_window.measure_table.horizontalHeaderItem(1).text() == "Imported ηx (mm)"
    assert half_window.measure_table.item(0, 3).text() == "0.15"
    assert not half_window.dispersion_curve.grab().isNull()
    assert not half_window.dispersion_curve._is_rf("WATCH")
    assert half_window.dispersion_curve._is_rf("RFCW")
    assert half_window.dispersion_curve._is_visible_optics_element("BPM06", "MONI")
    assert not half_window.dispersion_curve._is_visible_optics_element("PRF02", "WATCH")
    half_window.close()

    half_real_context = load_app_context(
        "dispersion_correction",
        machine_id="half",
        control_backend="real",
    )
    _, half_real_config = load_profile_run_config(half_real_context)
    half_real_window = MainWindow(half_real_config, half_real_context)
    assert half_real_window.model_source_combo.itemText(1) == "Current snapshot"
    assert half_real_window.model_source_combo.itemData(1) == "live"
    assert "REAL backend" in half_real_window.model_source_combo.toolTip()
    assert half_real_window.model_response_button.isEnabled()
    half_real_window.close()
    app.quit()
