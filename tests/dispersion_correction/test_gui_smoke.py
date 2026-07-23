import os
from pathlib import Path

import numpy as np
import pytest


def test_main_window_constructs_offscreen(tmp_path, monkeypatch) -> None:
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

    from PyQt5.QtWidgets import QApplication, QListWidgetItem, QMessageBox

    from half_linac.src.apps.dispersion_correction.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    assert window.windowTitle() == "Dispersion Correction"
    assert (window.width(), window.height()) == (1600, 1000)
    assert window.tabs.count() == 2
    assert [window.tabs.tabText(index) for index in range(window.tabs.count())] == [
        "Online Correction",
        "History",
    ]
    assert not hasattr(window, "workflow_tabs")
    assert len(window.detail_sections) == 2
    assert all(not button.isChecked() for button in window.detail_sections.values())
    assert not hasattr(window, "readiness_page")
    assert not hasattr(window, "preflight_text")
    assert not hasattr(window, "plan_text")
    assert window.workspace_splitter.widget(0) is window.dispersion_overview
    assert window.workspace_splitter.widget(1) is window.tabs
    assert window.workspace_splitter.parentWidget().objectName() == "workspacePanel"
    assert window.dispersion_overview.objectName() == "dispersionOverviewCard"
    assert window.dispersion_curve.parentWidget() is window.dispersion_overview
    assert window.model_details_button.parentWidget() is window.dispersion_overview
    assert window.model_details_button.text() == "Model Details…"
    assert window.model_dialog.isHidden()
    assert window.plot_state_label.text() == "No measured data"
    assert not hasattr(window, "plan_button")
    assert not hasattr(window, "backend_combo")
    assert not hasattr(window, "mode_combo")
    assert window.status_strip.items["MACHINE"].value_label.text() == "STANDALONE"
    assert window.status_strip.items["BACKEND"].value_label.text() == "OFFLINE"
    assert window.status_strip.items["ACCESS"].value_label.text() == "OFFLINE"
    assert window.status_strip.items["READINESS"].value_label.text() == "READY"
    assert window.status_strip.items["ENERGY STEP"].value_label.text().startswith(
        "SIM "
    )
    assert "Simulated energy step" in window.energy_step_summary.text()
    assert "MODEL_DELTA" not in window.energy_step_summary.text()
    assert not hasattr(window, "calibration_page")
    assert not hasattr(window, "calibration_text")
    assert (
        window.calibration_button.parentWidget()
        is window.energy_calibration_controls
    )
    assert window.calibration_button.text() == "Edit Energy Knob Calibration…"
    assert window.calibration_status_label.text() == "Calibration: Not required"
    assert window.measure_button.parentWidget() is window.online_content
    assert window.response_button.parentWidget() is window.online_content
    assert window.review_button.parentWidget() is window.online_content
    assert window.measure_button.isHidden()
    assert window.response_button.isHidden()
    assert window.review_button.isHidden()
    assert window.connection_controls.isHidden()
    assert window.preflight_button.isHidden()
    assert window.run_button.parentWidget() is window.correction_page
    assert window.apply_recommendation_button.parentWidget() is window.correction_page
    assert window.apply_recommendation_button.isHidden()
    assert window.next_action_button.text() == "Measure Dispersion"
    assert window.next_action_button.property("workflowAction") == "measure"
    next_actions = []
    window._start_task = lambda task: next_actions.append(task)
    window.next_action_button.click()
    assert next_actions == ["measure"]
    assert window.run_button.text() == "Advanced: Automatic Loop"
    assert window.recommendation_table.columnCount() == 6
    assert not window.advanced_settings.isVisible()
    assert window.advanced_button.text() == "Advanced settings"
    assert window.operation_plan is not None
    assert not hasattr(window, "knob_table")
    assert window.knob_edit.text() == "Q1L/Q1R; Q2L/Q2R"
    assert "Q1_sym" in window.knob_edit.toolTip()
    from half_linac.src.apps.dispersion_correction.workflow import AchromatWorkflow

    response = AchromatWorkflow(window._config_from_widgets()).build_response_matrix()
    window._task_completed("measure", response.measurement)
    window._set_running(False, "")
    assert window.tabs.currentWidget() is window.online_page
    assert all(not button.isChecked() for button in window.detail_sections.values())
    assert window.next_action_button.text() == "Measure Q Response"
    assert window.next_action_button.property("workflowAction") == "response"
    assert window.dispersion_curve.result is None
    assert window.dispersion_curve.measurement.label == "Latest measured"
    assert window.measurement_source_combo.currentData() == "live"
    assert "Latest measured" in window.plot_state_label.text()
    assert "4/4 valid BPMs" in window.plot_state_label.text()
    assert not window.model_measure_table.isHidden()
    assert window.model_measure_table.columnCount() == 4
    assert not window.dispersion_curve.grab().isNull()
    window._task_completed("response", response)
    window._set_running(False, "")
    assert window.detail_sections[window.response_page].isChecked()
    assert window.next_action_button.text() == "Review Recommendation"
    assert window.next_action_button.property("workflowAction") == "review"
    assert window.dispersion_curve.measurement.label == "Response baseline"
    assert "Response baseline" in window.plot_state_label.text()
    window._compute_recommendation()
    assert window.correction_recommendation is not None
    assert window.correction_recommendation.ready
    assert window.tabs.currentWidget() is window.online_page
    assert window.detail_sections[window.correction_page].isChecked()
    assert window.recommendation_table.rowCount() == 4
    assert window.recommendation_prediction_table.rowCount() == len(
        response.bpm_names
    )
    assert window.apply_recommendation_button.isEnabled()
    assert window.next_action_button.text() == "Apply & Remeasure"
    assert window.next_action_button.property("workflowAction") == "apply"
    assert "no backend" in window.correction_state_label.text().lower()
    old_gain = window.gain_spin.value()
    window.gain_spin.setValue(max(0.001, old_gain - 0.1))
    app.processEvents()
    assert window.correction_recommendation is None
    assert not window.review_button.isEnabled()
    assert "discarded" in window.correction_state_label.text().lower()
    from half_linac.src.apps.dispersion_correction.calibration_draft import (
        EnergyCalibrationDraft,
        EnergyCalibrationPoint,
        calibration_fragment,
    )
    from half_linac.src.apps.dispersion_correction.gui.calibration_editor import (
        CalibrationEditorDialog,
    )

    calibration_dialog = CalibrationEditorDialog(
        actuator="rf_phase",
        actuator_unit="deg",
        target_delta=1.0e-4,
        draft_directory=tmp_path,
        machine_id="irfel",
        backend="real",
        parent=window,
    )
    assert calibration_dialog.objectName() == "energyCalibrationDialog"
    assert calibration_dialog.styleSheet()
    assert calibration_dialog.plot.theme_name == window.theme_name
    calibration_draft = EnergyCalibrationDraft(
        actuator="rf_phase",
        actuator_unit="deg",
        input_mode="direct_delta",
        baseline_actuator=0.0,
        reference_energy=None,
        points=tuple(
            EnergyCalibrationPoint(
                actuator_value=actuator,
                delta_p_over_p=delta,
            )
            for actuator, delta in (
                (-0.50, -0.00020),
                (-0.25, -0.00010),
                (0.00, 0.00000),
                (0.25, 0.00010),
                (0.50, 0.00020),
            )
        ),
        machine_id="irfel",
        backend="real",
    )
    calibration_dialog.set_draft(calibration_draft)
    assert calibration_dialog.analysis is not None
    assert calibration_dialog.analysis.valid
    assert calibration_dialog.activate_button.isEnabled()
    assert "Quality: PASS" in calibration_dialog.preview.toPlainText()
    assert calibration_dialog.table.horizontalHeaderItem(2).text() == (
        "Measured energy (MeV)"
    )
    draft_paths = calibration_dialog._save_draft(show_message=False)
    assert draft_paths["latest"].exists()
    calibration = calibration_fragment(
        calibration_draft,
        calibration_dialog.analysis,
        source_path=str(draft_paths["archive"]),
    )
    window._activate_session_calibration(
        calibration,
        str(draft_paths["archive"]),
    )
    assert window.session_energy_calibration_source == str(
        draft_paths["archive"]
    )
    assert window.calibration_status_label.text() == "Calibration: Session override"
    assert "machine profile was not modified" in (
        window.calibration_status_label.toolTip().lower()
    )
    assert window.restore_calibration_button.isVisibleTo(window)
    assert window.restore_calibration_button.isEnabled()
    window._apply_configured_calibration()
    assert window.session_energy_calibration_source is None
    assert window.calibration_status_label.text() == "Calibration: Not required"
    assert window.restore_calibration_button.isHidden()
    assert not window.restore_calibration_button.isEnabled()
    calibration_dialog.close()
    from half_linac.src.apps.dispersion_correction.config import load_config

    window.config = load_config("tests/dispersion_correction/fixtures/irfel_achromat.json")
    window._load_config_to_widgets()
    assert window._config_from_widgets().backend == window.config.backend
    assert window.status_strip.items["BACKEND"].value_label.text() == "EPICS"
    assert window.status_strip.items["ACCESS"].value_label.text() == "READ ONLY"
    assert window.status_strip.items["READINESS"].value_label.text() == "NOT READY"
    assert "actuator_per_delta" in window.operation_banner.text()
    assert window.knob_edit.text() == "QM13/QM16; QM14/QM15"
    assert " A" in window.knob_edit.toolTip()
    window.resize(1248, 803)
    window.show()
    app.processEvents()
    assert window.dispersion_curve.isVisibleTo(window)
    window.tabs.setCurrentWidget(window.history_page)
    app.processEvents()
    assert window.dispersion_curve.isVisibleTo(window)
    window.tabs.setCurrentWidget(window.online_page)
    assert abs(window.load_button.geometry().center().y() - window.config_title_label.geometry().center().y()) <= 1
    assert window.load_button.property("role") is None
    assert not window.abort_button.isVisible()
    assert not window.abort_button.isEnabled()
    window.advanced_button.setChecked(True)
    app.processEvents()
    assert window.advanced_settings.isVisible()
    window.advanced_button.setChecked(False)
    app.processEvents()
    assert not window.advanced_settings.isVisible()
    window._set_running(True, "measure")
    assert window.abort_button.isVisible()
    assert window.abort_button.isEnabled()
    assert not window.measure_button.isEnabled()
    assert not window.next_action_button.isEnabled()
    assert window.next_action_button.text() == "Measuring Dispersion…"
    assert not window.delta_spin.isEnabled()
    assert window.progress_widget.isVisible()
    assert "Measuring dispersion" in window.plot_state_label.text()
    window._update_progress("Sampling +Δp/p", 2, 5)
    assert window.operation_progress.value() == 40
    assert window.progress_percent_label.text() == "40%"
    window._set_running(False, "")
    assert not window.abort_button.isVisible()
    assert window.delta_spin.isEnabled()
    assert not window.progress_widget.isVisible()
    assert window.plot_state_label.text() == "No measured data"
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
    assert profile_window.status_strip.items["ACCESS"].value_label.text() == "READ ONLY"
    assert profile_window.load_button.isHidden()
    assert profile_window.config_title_label.text() == "Machine Profile"
    assert profile_window.calibration_status_label.text() == "Calibration: Machine profile"
    assert profile_window.section_combo.currentData() == "dogleg"
    assert profile_window.model_boundary_label.text() == "Assume D=D'=0 at BPM07"
    assert profile_window.model_source_combo.itemText(0) == "Design lattice"
    assert profile_window.model_source_combo.itemText(1) == "Current snapshot"
    assert "REAL backend" in profile_window.model_source_combo.toolTip()
    assert not profile_window.model_response_button.isHidden()
    assert profile_window.model_response_button.isEnabled()
    assert profile_window.show_design_model_checkbox.isVisibleTo(profile_window)
    assert profile_window.show_snapshot_model_checkbox.isVisibleTo(profile_window)
    assert profile_window.show_design_model_checkbox.isEnabled()
    assert profile_window.show_snapshot_model_checkbox.isEnabled()
    assert profile_window.dispersion_curve.result is None
    assert profile_window.dispersion_curve.measurement is None
    model_requests = []
    profile_window._start_model_response = lambda **kwargs: model_requests.append(kwargs)
    profile_window.show_design_model_checkbox.setChecked(True)
    assert model_requests[-1] == {
        "model_source": "design",
        "focus_comparison": False,
    }
    profile_window.show_design_model_checkbox.setChecked(False)
    profile_window.show_snapshot_model_checkbox.setChecked(True)
    assert model_requests[-1] == {
        "model_source": "live",
        "focus_comparison": False,
    }
    profile_window.show_snapshot_model_checkbox.setChecked(False)
    assert profile_window.model_page not in profile_window.detail_sections
    assert profile_window.model_details_button.isVisibleTo(profile_window)
    assert not profile_window.run_button.isEnabled()
    assert "READ ONLY" in profile_window.operation_banner.text()
    assert "±0.0001 / ±0.25 deg" == profile_window._energy_step_compact()
    assert profile_window.connection_controls.isVisibleTo(profile_window)
    assert profile_window.preflight_button.isVisibleTo(profile_window)
    assert profile_window.preflight_button.isEnabled()
    assert (
        profile_window.preflight_button.parentWidget()
        is profile_window.connection_controls
    )
    assert profile_window.next_action_button.text() == "Measure Dispersion"
    assert profile_window.next_action_button.property("workflowAction") == ""
    assert not profile_window.next_action_button.isEnabled()
    assert "left configuration panel" in profile_window.workflow_hint_label.text()
    assert "read-only" in profile_window.preflight_button.toolTip().lower()
    assert "read-only" in profile_window.measure_button.toolTip().lower()
    from half_linac.src.apps.dispersion_correction.preflight import (
        LivePreflightResult,
        PreflightResult,
    )

    connection_dialogs = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, title, message: connection_dialogs.append((title, message)),
    )
    profile_window._live_preflight_completed(
        LivePreflightResult(
            static=PreflightResult(
                level="read-only-ready",
                blockers=(),
                warnings=("Calibration should be reviewed.",),
                checks={},
            ),
            blockers=(),
            warnings=(),
            checks={},
            readings={},
        )
    )
    assert connection_dialogs[-1][0] == "Connection Check Warnings"
    assert "Calibration should be reviewed." in connection_dialogs[-1][1]
    profile_window._live_preflight_completed(
        LivePreflightResult(
            static=PreflightResult(
                level="blocked",
                blockers=(),
                warnings=(),
                checks={},
            ),
            blockers=("BPM09 is disconnected.",),
            warnings=(),
            checks={},
            readings={},
        )
    )
    assert connection_dialogs[-1][0] == "Connection Check Failed"
    assert "BPM09 is disconnected." in connection_dialogs[-1][1]
    assert "Live preflight diagnostics" in profile_window.log_view.toPlainText()
    assert profile_window.bpm_select_button.isVisibleTo(profile_window)
    assert profile_window.bpm_select_button.height() == profile_window.bpm_edit.height() == 34
    assert profile_window.knob_select_button.height() == profile_window.knob_edit.height() == 34
    assert profile_window.bpm_select_button.geometry().top() == profile_window.bpm_edit.geometry().top()
    assert profile_window.bpm_select_button.geometry().bottom() == profile_window.bpm_edit.geometry().bottom()
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

    irfel_vm_context = load_app_context(
        "dispersion_correction",
        machine_id="irfel",
        control_backend="vm",
    )
    _, irfel_vm_config = load_profile_run_config(irfel_vm_context)
    irfel_vm_window = MainWindow(irfel_vm_config, irfel_vm_context)
    irfel_vm_window.show()
    app.processEvents()
    assert irfel_vm_window.status_strip.items["BACKEND"].value_label.text() == "VM"
    assert irfel_vm_window.status_strip.items["ACCESS"].value_label.text() == "MODEL ONLY"
    assert irfel_vm_window.status_strip.items["READINESS"].value_label.text() == "MODEL ONLY"
    assert irfel_vm_window.section_combo.currentData() == "dogleg"
    assert irfel_vm_window.model_page not in irfel_vm_window.detail_sections
    assert irfel_vm_window.model_dialog.isHidden()
    assert irfel_vm_window.next_action_button.text() == "Calculate Design Model"
    assert irfel_vm_window.connection_controls.isHidden()
    assert irfel_vm_window.preflight_button.isHidden()
    assert irfel_vm_window.show_design_model_checkbox.isEnabled()
    assert irfel_vm_window.show_snapshot_model_checkbox.isEnabled()
    assert not irfel_vm_window.measure_button.isEnabled()
    assert not irfel_vm_window.response_button.isEnabled()
    assert not irfel_vm_window.run_button.isEnabled()
    irfel_vm_window.close()

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
    assert half_window.model_response_button.text() == "Analyze Model"
    assert not half_window.model_response_button.isHidden()
    assert half_window.model_response_button.isEnabled()
    assert half_window.model_page not in half_window.detail_sections
    assert "does not use scan" in half_window.knob_edit.toolTip()
    assert "limit ±" not in half_window.knob_edit.toolTip()
    assert half_window.dispersion_curve.result is None
    assert half_window.dispersion_curve.measurement is None
    assert half_window.import_measurement_button.isEnabled()
    assert not half_window.measure_button.isEnabled()
    assert not half_window.response_button.isEnabled()
    assert not half_window.run_button.isEnabled()
    assert not half_window.bpm_select_button.isVisibleTo(half_window)
    assert not half_window.knob_select_button.isVisibleTo(half_window)
    assert half_window.status_strip.items["READINESS"].value_label.text() == "MODEL ONLY"
    assert half_window.status_strip.items["ENERGY STEP"].value_label.text() == "NOT USED"
    assert not half_window.delta_spin.isVisibleTo(half_window)
    assert not half_window.energy_step_field_label.isVisibleTo(half_window)
    assert half_window.energy_calibration_controls.isHidden()
    assert not half_window.calibration_button.isEnabled()
    assert "calculates dispersion directly" in half_window.energy_step_summary.text()
    assert "No energy scan" in half_window.energy_step_summary.text()
    assert "MODEL_DELTA" not in half_window.energy_step_summary.text()
    assert half_window.tabs.currentWidget() is half_window.online_page
    assert half_window.model_dialog.isHidden()
    assert half_window.next_action_button.text() == "Calculate Design Model"
    assert half_window.next_action_button.property("workflowAction") == "model-design"
    assert half_window.connection_controls.isHidden()
    assert half_window.preflight_button.isHidden()
    from half_linac.src.apps.dispersion_correction.models import (
        ImportedDispersionDataset,
        ModelOpticsCurve,
        ModelResponseResult,
    )

    curve = ModelOpticsCurve(
        element_names=("_BEG_", "BL01A", "QL01", "BPM06"),
        element_types=("MARK", "SBEN", "QUAD", "MONI"),
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
        device_names=("QL01",),
        selected_values=np.asarray([0.1, 0.01]),
        target_values=np.zeros(2),
        design_reference_values=np.zeros(2),
        selected_curve=curve,
        design_reference_curve=curve,
        selected_k1={"QL01": 6.1},
        design_k1={"QL01": 6.0},
        design_reference_deltas={"QL01": -0.1},
        design_curve=curve,
    )
    half_window._show_model_response(model_result)
    half_window._set_running(False, "")
    app.processEvents()
    assert half_window.dispersion_curve.result is model_result
    assert half_window.show_design_model_checkbox.isChecked()
    assert half_window.show_snapshot_model_checkbox.isEnabled()
    assert half_window.dispersion_curve.show_design_model
    assert not half_window.dispersion_curve.show_snapshot_model
    assert half_window.model_table.horizontalHeaderItem(0).text() == "Quadrupole"
    assert half_window.model_table.horizontalHeaderItem(3).text() == "Design-reference ΔK1"
    assert half_window.model_table.item(0, 0).text() == "QL01"
    assert half_window.model_table.item(0, 3).text() == "-0.1"
    half_window.model_details_button.click()
    app.processEvents()
    assert half_window.model_dialog.isVisible()
    assert half_window.model_page.parentWidget() is half_window.model_dialog
    assert half_window.model_empty_label.isHidden()
    assert half_window.response_table.rowCount() == 0
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
    half_window._show_imported_comparison(model_result, imported)
    half_window._set_running(False, "")
    assert half_window.clear_measurement_button.isEnabled()
    assert half_window.measurement_source_combo.currentData() == "imported"
    assert half_window.dispersion_curve.measurement.label == "External measurement"
    assert half_window.model_measure_table.isVisible()
    assert half_window.model_measure_table.horizontalHeaderItem(1).text() == (
        "Measurement ηx (mm)"
    )
    assert half_window.model_measure_table.horizontalHeaderItem(4).text() == (
        "Design model ηx (mm)"
    )
    assert half_window.model_measure_table.item(0, 5).text() == "0.15"
    assert half_window.measure_table.rowCount() == 0
    assert not half_window.dispersion_curve.grab().isNull()
    assert not half_window.dispersion_curve._is_rf("WATCH")
    assert half_window.dispersion_curve._is_rf("RFCW")
    assert half_window.dispersion_curve._is_bend("SBEN")
    assert half_window.dispersion_curve._is_bend("RBEN")
    assert half_window.dispersion_curve._is_bend("CSBEND")
    assert half_window.dispersion_curve._is_visible_optics_element("BPM06", "MONI")
    assert half_window.dispersion_curve._is_visible_optics_element("DM8", "SBEN")
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
