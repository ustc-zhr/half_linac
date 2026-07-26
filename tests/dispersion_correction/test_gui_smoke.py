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

    from PyQt5.QtWidgets import (
        QApplication,
        QFrame,
        QLabel,
        QListWidgetItem,
        QMessageBox,
        QPushButton,
    )

    from half_linac.src.apps.dispersion_correction.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    assert window.windowTitle() == "Dispersion Correction"
    assert (window.width(), window.height()) == (1600, 1000)
    assert not hasattr(window, "tabs")
    assert not hasattr(window, "workflow_tabs")
    assert not hasattr(window, "detail_sections")
    assert not hasattr(window, "readiness_page")
    assert not hasattr(window, "preflight_text")
    assert not hasattr(window, "plan_text")
    assert window.workspace_splitter.widget(0) is window.dispersion_overview
    assert window.workspace_splitter.widget(1) is window.online_page
    assert window.workspace_splitter.parentWidget().objectName() == "workspacePanel"
    assert window.online_page.objectName() == "workflowActionCard"
    assert window.dispersion_overview.objectName() == "dispersionOverviewCard"
    assert window.workflow_title_label.objectName() == "cardTitle"
    assert window.overview_title_label.objectName() == "cardTitle"
    assert window.measurement_header_label.text() == "Measurement"
    assert not hasattr(window, "overlays_header_label")
    assert (
        window.plot_state_label.parentWidget()
        is window.overview_controls.measurement_group
    )
    assert (
        window.show_design_model_checkbox.parentWidget()
        is window.overview_controls.overlays_group
    )
    assert window.dispersion_curve.parentWidget() is window.dispersion_overview
    assert window.model_details_button.parentWidget() is window.overview_controls
    assert window.model_details_button.text() == "Model Details…"
    assert window.model_dialog.isHidden()
    assert window.response_dialog.isHidden()
    assert window.recommendation_dialog.isHidden()
    assert window.last_run_dialog.isHidden()
    assert window.iteration_history_dialog.isHidden()
    assert not hasattr(window, "iteration_history_button")
    assert window.history_button.text() == "History…"
    assert window.last_run_button is window.history_button
    assert not window.history_button.isEnabled()
    assert window.offline_demo_button.isHidden()
    assert window.response_details_button.isHidden()
    assert window.back_to_correction_methods_button.isHidden()
    assert not hasattr(window, "recommendation_details_button")
    assert window.plot_state_label.text() == "No measured data"
    assert window.plot_state_label.isHidden()
    assert not hasattr(window, "plan_button")
    assert not hasattr(window, "backend_combo")
    assert not hasattr(window, "mode_combo")
    assert window.status_strip.items["MACHINE"].value_label.text() == "STANDALONE"
    assert window.status_strip.items["BACKEND"].value_label.text() == "OFFLINE"
    assert window.status_strip.items["ACCESS"].value_label.text() == "OFFLINE"
    assert window.status_strip.items["READINESS"].value_label.text() == "READY"
    assert window.status_strip.items["ENERGY STEP"].minimumWidth() == 160
    assert not window.status_strip.items["ENERGY STEP"].value_label.wordWrap()
    assert window.status_strip.items["ENERGY STEP"].value_label.text().startswith(
        "SIM "
    )
    assert window.operation_banner.isHidden()
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
    assert window.calibration_status_label.isHidden()
    assert window.measure_button.parentWidget() is window.online_content
    assert window.response_button.parentWidget() is window.online_content
    assert window.review_button.parentWidget() is window.online_content
    assert window.measure_button.isHidden()
    assert window.response_button.isHidden()
    assert window.review_button.isHidden()
    assert not hasattr(window, "connection_controls")
    assert window.preflight_button.isHidden()
    assert window.preflight_button.text() == "Check PVs"
    assert window.preflight_button.parentWidget().objectName() == "controlCard"
    assert window.machine_card_title.text() == "Machine"
    assert window.measurement_card_title.text() == "Measurement"
    assert window.correction_step_card_title.text() == "Correction Step"
    assert window.machine_card.objectName() == "controlSectionCard"
    assert window.measurement_card.objectName() == "controlSectionCard"
    assert window.correction_step_card.objectName() == "controlSectionCard"
    assert window.measurement_action_button.parentWidget() is window.measurement_card
    assert window.gain_spin.parentWidget() is window.correction_step_card
    assert window.run_button.parentWidget() is window.correction_mode_actions
    assert window.run_button.objectName() == "automaticCorrectionButton"
    assert window.apply_recommendation_button.parentWidget() is window.correction_page
    assert not window.apply_recommendation_button.isHidden()
    assert window.apply_recommendation_button.text() == "Apply and Verify"
    assert window.measurement_action_button.text() == "Measure Dispersion"
    assert window.measurement_action_button.isEnabled()
    assert window.measurement_status_label.text() == (
        "No valid dispersion measurement"
    )
    assert window.next_action_button.text() == "Manual Correction"
    assert window.next_action_button.property("workflowAction") == ""
    assert not window.next_action_button.isEnabled()
    assert not window.run_button.isEnabled()
    assert "Energy step" in window.workflow_summary_label.text()
    next_actions = []
    window._start_task = lambda task: next_actions.append(task)
    window.measurement_action_button.click()
    assert next_actions == ["measure"]
    assert window.run_button.text() == "Automatic Correction…"
    automatic_dialog, generations, response_policy = (
        window._build_automatic_correction_dialog()
    )
    assert automatic_dialog.objectName() == "automaticCorrectionDialog"
    assert automatic_dialog.findChild(QFrame, "automaticSettingsCard") is not None
    assert generations.objectName() == "automaticGenerationsSpin"
    assert response_policy.objectName() == "automaticResponsePolicy"
    assert (
        automatic_dialog.findChild(QPushButton, "automaticStartButton").text()
        == "Start Automatic Correction"
    )
    automatic_dialog.close()
    assert window.recommendation_table.columnCount() == 6
    config_labels = {label.text() for label in window.findChildren(QLabel)}
    assert "Scan Samples" in config_labels
    assert "Sample Interval (s)" in config_labels
    assert "Verification Samples" in config_labels
    assert "Gain" in config_labels
    assert "Max Step (%)" in config_labels
    assert "Max Generations" not in config_labels
    assert "Q Response Update" not in config_labels
    assert window.max_iter_spin.isHidden()
    assert window.response_update_combo.isHidden()
    assert "Measure dispersion before" in window.run_button.toolTip()
    legacy_sections = {
        label.text()
        for label in window.findChildren(QLabel)
        if label.property("role") == "configSection"
    }
    assert not legacy_sections
    assert window.operation_plan is not None
    assert not hasattr(window, "knob_table")
    assert window.knob_edit.text() == "Q1L/Q1R; Q2L/Q2R"
    assert "Q1_sym" in window.knob_edit.toolTip()
    from half_linac.src.apps.dispersion_correction.workflow import AchromatWorkflow

    response = AchromatWorkflow(window._config_from_widgets()).build_response_matrix()
    window._task_completed("measure", response.measurement)
    window._set_running(False, "")
    assert window.response_dialog.isHidden()
    assert window.recommendation_dialog.isHidden()
    assert window.measurement_action_button.text() == "Remeasure Dispersion"
    assert "RMS" in window.measurement_status_label.text()
    assert window.next_action_button.text() == "Manual Correction"
    assert window.next_action_button.property("workflowAction") == "select-manual"
    assert window.run_button.isEnabled()
    assert not window.back_to_correction_methods_button.isVisibleTo(window)
    assert f"Maximum {window.max_iter_spin.value()} generations" in (
        window.run_button.toolTip()
    )
    next_actions.clear()
    window.next_action_button.click()
    assert next_actions == []
    assert window.correction_mode == "manual"
    assert window.next_action_button.text() == "Measure Q Response…"
    assert window.run_button.isHidden()
    assert window.next_action_button.height() == window.run_button.height()
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.Yes,
    )
    window.next_action_button.click()
    assert next_actions == ["response"]
    assert "Measured residual RMS" in window.workflow_summary_label.text()
    assert "4/4 correction BPMs valid" in window.workflow_summary_label.text()
    assert window.dispersion_curve.result is None
    assert window.dispersion_curve.measurement.label == "Latest measured"
    assert not hasattr(window, "measurement_source_combo")
    assert "Latest measured" in window.plot_state_label.text()
    assert "4/4 correction BPMs valid" in window.plot_state_label.text()
    assert not window.plot_state_label.isHidden()
    assert not window.model_measure_table.isHidden()
    assert window.model_measure_table.columnCount() == 4
    assert not window.dispersion_curve.grab().isNull()
    window._task_completed("response", response)
    window._set_running(False, "")
    assert window.response_dialog.isHidden()
    assert window.correction_recommendation is not None
    assert window.correction_recommendation.ready
    assert window.recommendation_dialog.isVisible()
    assert not window.response_details_button.isHidden()
    window.recommendation_dialog.close()
    window.response_details_button.click()
    app.processEvents()
    assert window.response_dialog.isVisible()
    window.response_dialog.close()
    assert window.next_action_button.text() == "Review Recommendation…"
    assert window.next_action_button.property("workflowAction") == "review"
    assert window.run_button.isHidden()
    assert window.back_to_correction_methods_button.isVisibleTo(window)
    assert "Predicted residual RMS" in window.workflow_summary_label.text()
    assert window.dispersion_curve.measurement.label == "Response baseline"
    assert "Response baseline" in window.plot_state_label.text()
    window._review_recommendation()
    assert window.recommendation_dialog.isVisible()
    assert window.recommendation_table.rowCount() == 4
    assert window.recommendation_prediction_table.rowCount() == len(
        response.bpm_names
    )
    assert window.apply_recommendation_button.isEnabled()
    assert window.next_action_button.text() == "Review Recommendation…"
    assert window.next_action_button.property("workflowAction") == "review"
    assert "Predicted residual RMS" in window.workflow_summary_label.text()
    assert "no backend" in window.correction_state_label.text().lower()
    window.recommendation_dialog.close()
    old_gain = window.gain_spin.value()
    window.gain_spin.setValue(max(0.001, old_gain - 0.1))
    app.processEvents()
    assert window.correction_recommendation is None
    assert window.latest_measurement is response.measurement
    assert window.latest_response is response
    assert window.next_action_button.property("workflowAction") == "prepare"
    assert window.run_button.isHidden()
    assert window.review_button.isEnabled()
    assert "discarded" in window.correction_state_label.text().lower()
    window.back_to_correction_methods_button.click()
    assert window.correction_mode is None
    assert window.latest_measurement is response.measurement
    assert window.latest_response is None
    assert window.correction_recommendation is None
    assert window.run_button.isEnabled()
    assert not window.back_to_correction_methods_button.isVisibleTo(window)
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
    assert calibration_dialog.settings_card.objectName() == (
        "calibrationSettingsCard"
    )
    assert calibration_dialog.analysis_card.objectName() == (
        "calibrationAnalysisCard"
    )
    assert (
        calibration_dialog.reference_energy_spin.parentWidget()
        is calibration_dialog.reference_energy_row
    )
    assert (
        calibration_dialog.energy_unit_combo.parentWidget()
        is calibration_dialog.reference_energy_row
    )
    assert calibration_dialog.paste_button.text() == "Paste Data"
    assert calibration_dialog.table.verticalHeader().defaultSectionSize() == 36
    assert calibration_dialog.table.verticalHeader().minimumSectionSize() == 36
    assert calibration_dialog.table.minimumHeight() >= 184
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
    assert not window.calibration_status_label.isHidden()
    assert "machine profile was not modified" in (
        window.calibration_status_label.toolTip().lower()
    )
    assert window.restore_calibration_button.isVisibleTo(window)
    assert window.restore_calibration_button.isEnabled()
    window._apply_configured_calibration()
    assert window.session_energy_calibration_source is None
    assert window.calibration_status_label.text() == "Calibration: Not required"
    assert window.calibration_status_label.isHidden()
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
    assert not window.operation_banner.isHidden()
    assert window.knob_edit.text() == "QM13/QM16; QM14/QM15"
    assert " A" in window.knob_edit.toolTip()
    window.resize(1248, 803)
    window.show()
    app.processEvents()
    assert window.overview_controls.compact
    assert window.dispersion_curve.isVisibleTo(window)
    assert window.online_page.isVisibleTo(window)
    window.correction_table.setRowCount(1)
    window.report_text.setPlainText("Last run report")
    window._set_running(False, "")
    assert not window.history_button.isEnabled()
    assert window.dispersion_curve.isVisibleTo(window)
    assert abs(window.load_button.geometry().center().y() - window.config_title_label.geometry().center().y()) <= 1
    assert window.load_button.property("role") is None
    assert not window.abort_button.isVisible()
    assert not window.abort_button.isEnabled()
    assert window.sample_interval_spin.isVisibleTo(window)
    assert window.final_samples_spin.isVisibleTo(window)
    assert window.gain_spin.isVisibleTo(window)
    assert window.max_step_pct_spin.isVisibleTo(window)
    window._set_running(True, "measure")
    assert window.abort_button.isVisible()
    assert window.abort_button.isEnabled()
    assert not window.measure_button.isEnabled()
    assert not window.next_action_button.isEnabled()
    assert window.next_action_button.isHidden()
    assert window.measurement_action_button.text() == "Measuring Dispersion…"
    assert not window.measurement_action_button.isEnabled()
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
    assert window.plot_state_label.isHidden()

    window._set_running(True, "run")
    window._update_progress("Iteration 2/5 · validating", 2, 7)
    assert "Gen 2/5" in window.run_button.text()
    assert "validating" in window.run_button.text()
    assert "29%" in window.run_button.text()
    assert window.next_action_button.isHidden()
    assert "Iteration 2/5" in window.run_button.toolTip()
    window._automatic_measurement_updated(
        0,
        5,
        "initial",
        response.measurement,
    )
    assert window.dispersion_curve.measurement.label == "Automatic initial"
    assert window.dispersion_curve.reference_measurement is None
    window._automatic_measurement_updated(
        1,
        5,
        "accepted",
        response.measurement,
    )
    assert window.dispersion_curve.measurement.label == (
        "Generation 1 · accepted"
    )
    assert window.dispersion_curve.reference_measurement.label == (
        "Before correction"
    )
    assert "generation 1/5 accepted" in window.plot_state_label.text().lower()
    window._set_running(False, "")
    assert window.run_button.text() == "Automatic Correction…"
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
    assert profile_window.status_strip.items["ACCESS"].value_label.text() == "WRITE ENABLED"
    assert profile_window.load_button.isHidden()
    assert profile_window.config_title_label.text() == "Configuration"
    assert profile_window.offline_demo_button.isVisibleTo(profile_window)
    assert profile_window.calibration_status_label.text() == "Calibration: Missing"
    assert not profile_window.calibration_status_label.isHidden()
    assert profile_window.section_combo.currentData() == "MIR-dogleg"
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
    assert profile_window.refresh_snapshot_button.isVisibleTo(profile_window)
    assert profile_window.refresh_snapshot_button.isEnabled()
    profile_window.refresh_snapshot_button.click()
    assert model_requests[-1] == {
        "model_source": "live",
        "focus_comparison": False,
    }
    assert profile_window.model_details_button.isVisibleTo(profile_window)
    assert not profile_window.run_button.isEnabled()
    assert "calibration.actuator_per_delta" in profile_window.operation_banner.text()
    assert not profile_window.operation_banner.isHidden()
    assert (
        f"±{profile_window.config.energy_knob.delta:g} Δp/p"
        == profile_window._energy_step_compact()
    )
    assert not hasattr(profile_window, "connection_controls")
    assert profile_window.preflight_button.isVisibleTo(profile_window)
    assert profile_window.preflight_button.isEnabled()
    assert (
        profile_window.preflight_button.parentWidget().objectName()
        == "controlCard"
    )
    assert (
        abs(
            profile_window.preflight_button.geometry().center().y()
            - profile_window.config_title_label.geometry().center().y()
        )
        <= 1
    )
    assert profile_window.next_action_button.text() == "Manual Correction"
    assert profile_window.next_action_button.property("workflowAction") == ""
    assert not profile_window.next_action_button.isEnabled()
    assert profile_window.next_action_button.isVisibleTo(profile_window)
    assert not profile_window.measurement_action_button.isEnabled()
    assert "calibration" in profile_window.workflow_hint_label.text().lower()
    assert "read-only" in profile_window.preflight_button.toolTip().lower()
    assert "calibration" in profile_window.measure_button.toolTip().lower()
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
    profile_window._set_running(False, "")
    assert connection_dialogs[-1][0] == "Connection Check Failed"
    assert "BPM09 is disconnected." in connection_dialogs[-1][1]
    assert profile_window.operation_banner.isVisibleTo(profile_window)
    assert "calibration.actuator_per_delta" in profile_window.operation_banner.text()
    assert "Live preflight diagnostics" in profile_window.log_view.toPlainText()
    profile_window._activate_session_calibration(
        {
            "kind": "linear",
            "actuator_per_delta": 2500.0,
            "offset": 0.0,
        },
        "onsite-session-calibration.json",
    )
    assert profile_window.calibration_status_label.text() == (
        "Calibration: Session override"
    )
    assert not profile_window.measurement_action_button.isEnabled()
    assert "Click Check PVs" in profile_window.measurement_action_button.toolTip()
    profile_window._live_preflight_completed(
        LivePreflightResult(
            static=PreflightResult(
                level="write-ready",
                blockers=(),
                warnings=(),
                checks={},
            ),
            blockers=(),
            warnings=(),
            checks={},
            readings={},
        )
    )
    profile_window._set_running(False, "")
    assert profile_window.measurement_action_button.isEnabled()
    checked_preflight = profile_window.last_live_preflight
    preflight_seen_by_measure = []
    monkeypatch.setattr(
        profile_window,
        "_operation_block_reason",
        lambda: (
            preflight_seen_by_measure.append(profile_window.last_live_preflight)
            or "Stop before starting the test worker."
        ),
    )
    assert not profile_window._start_task("measure")
    assert preflight_seen_by_measure
    assert all(
        result is checked_preflight
        for result in preflight_seen_by_measure
    )
    assert profile_window.last_live_preflight is checked_preflight
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
    assert knob_table.horizontalHeaderItem(3).text() == "Scan ± (K1 [1/m²])"
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
    assert knob_table.cellWidget(0, 4).maximum() == pytest.approx(
        profile_window.knob_hard_limits[0]
    )
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
    assert irfel_vm_window.operation_banner.isHidden()
    assert irfel_vm_window.section_combo.currentData() == "MIR-dogleg"
    assert irfel_vm_window.model_dialog.isHidden()
    assert irfel_vm_window.next_action_button.text() == "Calculate Design Model"
    assert not hasattr(irfel_vm_window, "connection_controls")
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
    assert "does not use scan" in half_window.knob_edit.toolTip()
    assert "limit ±" not in half_window.knob_edit.toolTip()
    assert half_window.dispersion_curve.result is None
    assert half_window.dispersion_curve.measurement is None
    assert not hasattr(half_window, "import_measurement_button")
    assert not hasattr(half_window, "clear_measurement_button")
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
    assert half_window.online_page.isVisibleTo(half_window)
    assert half_window.model_dialog.isHidden()
    assert half_window.next_action_button.text() == "Calculate Design Model"
    assert half_window.next_action_button.property("workflowAction") == "model-design"
    assert not hasattr(half_window, "connection_controls")
    assert half_window.preflight_button.isHidden()
    from half_linac.src.apps.dispersion_correction.models import (
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
    half_window._set_running(False, "")
    assert half_window.dispersion_curve.measurement is None
    assert half_window.model_measure_table.isHidden()
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


def test_offline_demo_runs_the_reviewed_workflow() -> None:
    pytest.importorskip("PyQt5")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from dataclasses import replace

    from PyQt5.QtWidgets import QApplication

    from half_linac.src.apps.dispersion_correction.gui.main_window import MainWindow
    from half_linac.src.apps.dispersion_correction.workflow import AchromatWorkflow

    app = QApplication.instance() or QApplication([])
    demo = MainWindow(offline_demo=True)
    demo.show()
    app.processEvents()

    assert demo.windowTitle() == "Dispersion Correction · Offline Demo"
    assert demo.app_title_label.text() == "Dispersion Correction"
    assert demo.config_title_label.text() == "Offline Demo"
    assert demo.load_button.isHidden()
    assert demo.offline_demo_button.isHidden()
    assert demo.status_strip.items["MACHINE"].value_label.text() == "STANDALONE"
    assert demo.status_strip.items["BACKEND"].value_label.text() == "OFFLINE"
    assert demo.status_strip.items["ACCESS"].value_label.text() == "OFFLINE DEMO"
    assert demo.measurement_action_button.text() == "Measure Dispersion"
    assert demo.measurement_action_button.isVisibleTo(demo)
    assert demo.next_action_button.text() == "Manual Correction"
    assert not demo.next_action_button.isEnabled()

    response = AchromatWorkflow(
        demo._config_from_widgets()
    ).build_response_matrix()
    demo._task_completed("measure", response.measurement)
    demo._set_running(False, "")
    assert demo.measurement_action_button.text() == "Remeasure Dispersion"
    assert demo.next_action_button.text() == "Manual Correction"
    assert demo.run_button.isEnabled()

    demo._task_completed("response", response)
    demo._set_running(False, "")
    assert demo.next_action_button.text() == "Review Recommendation…"
    assert demo.run_button.isHidden()
    assert demo.back_to_correction_methods_button.isVisibleTo(demo)
    assert demo.correction_recommendation is not None
    assert demo.correction_recommendation.ready
    demo.recommendation_dialog.close()
    result = AchromatWorkflow(
        demo._config_from_widgets()
    ).apply_recommendation(demo.correction_recommendation)
    demo._task_completed("apply", result)
    demo._set_running(False, "")

    assert result.success
    assert demo.correction_table.rowCount() == 1
    assert demo.history_button.isEnabled()
    assert len(demo.correction_session_runs) == 1
    demo.history_button.click()
    app.processEvents()
    assert demo.iteration_history_dialog.isVisible()
    assert demo.iteration_history_run_combo.currentText() == "Manual 1"
    assert demo.iteration_history_generation_combo.count() == 3
    demo.iteration_history_generation_combo.setCurrentIndex(1)
    app.processEvents()
    assert "generation 1 accepted" in (
        demo.iteration_history_status_label.text().lower()
    )
    assert demo.iteration_history_curve.measurement.label == (
        "Generation 1 measured"
    )
    assert demo.iteration_history_knob_table.rowCount() == len(
        result.steps[0].device_values_before
    )
    demo.iteration_history_generation_combo.setCurrentIndex(2)
    demo.iteration_history_overlay_checkbox.setChecked(True)
    app.processEvents()
    assert len(demo.iteration_history_curve.measurement_overlays) == 1
    demo.iteration_history_dialog.close()
    assert demo.next_action_button.text() == "Manual Correction"

    automatic = AchromatWorkflow(demo._config_from_widgets()).run()
    demo._task_completed("run", automatic)
    assert len(demo.correction_session_runs) == 2
    assert demo.iteration_history_run_combo.currentText().startswith(
        "Automatic 1 · "
    )
    assert demo.iteration_history_generation_combo.count() == (
        len(automatic.steps) + 2
    )

    stopped_early = replace(
        automatic,
        steps=automatic.steps[:2],
    )
    demo.config = replace(
        demo.config,
        solver=replace(demo.config.solver, max_iter=3),
    )
    demo._record_correction_run("run", stopped_early)
    assert demo.iteration_history_run_combo.currentText() == (
        "Automatic 2 · 2/3 generations"
    )
    assert "Stopped early · 2/3 generations executed" in [
        demo.iteration_history_generation_combo.itemText(index)
        for index in range(demo.iteration_history_generation_combo.count())
    ]
    early_index = demo.iteration_history_generation_combo.findData(
        "early-stop"
    )
    demo.iteration_history_generation_combo.setCurrentIndex(early_index)
    assert "later generations were not run" in (
        demo.iteration_history_status_label.text()
    )

    aborted = replace(
        automatic,
        success=False,
        reason="Aborted; initial state restored",
    )
    demo._task_completed("run", aborted)
    demo._set_running(False, "")
    assert demo.latest_measurement is None
    assert demo.measurement_action_button.text() == "Measure Dispersion"
    assert not demo.next_action_button.isEnabled()
    assert not demo.run_button.isEnabled()
    assert "Remeasure dispersion" in demo.correction_state_label.text()
    demo.close()


def test_offline_demo_confirms_automatic_correction_settings(monkeypatch) -> None:
    pytest.importorskip("PyQt5")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PyQt5.QtWidgets import QApplication, QDialog

    from half_linac.src.apps.dispersion_correction.gui.main_window import MainWindow
    from half_linac.src.apps.dispersion_correction.workflow import AchromatWorkflow

    app = QApplication.instance() or QApplication([])
    demo = MainWindow(offline_demo=True)
    demo.show()
    app.processEvents()
    measurement = AchromatWorkflow(
        demo._config_from_widgets()
    ).measure_dispersion()
    demo._task_completed("measure", measurement)
    demo._set_running(False, "")

    tasks = []
    demo._start_task = lambda task: tasks.append(task)
    monkeypatch.setattr(QDialog, "exec_", lambda _dialog: QDialog.Accepted)
    demo._confirm_automatic_correction()

    assert tasks == ["run"]
    assert demo.correction_mode == "automatic"
    assert demo.run_button.text() == "Automatic Correction…"
    assert demo.run_button.parentWidget() is demo.correction_mode_actions
    demo.close()


def test_measure_worker_uses_scan_samples(monkeypatch) -> None:
    pytest.importorskip("PyQt5")

    from dataclasses import replace

    from half_linac.src.apps.dispersion_correction.gui.main_window import (
        WorkflowWorker,
    )
    from half_linac.src.apps.dispersion_correction.profile_runtime import (
        default_offline_config,
    )
    from half_linac.src.apps.dispersion_correction.workflow import AchromatWorkflow

    config = default_offline_config()
    config = replace(
        config,
        measurement=replace(
            config.measurement,
            samples_per_step=3,
            final_samples=11,
        ),
    )
    observed = []

    def record_samples(_workflow, samples=None):
        observed.append(samples)
        return object()

    monkeypatch.setattr(AchromatWorkflow, "measure_dispersion", record_samples)
    WorkflowWorker("measure", config).run()

    assert observed == [3]


def test_profile_window_opens_an_independent_offline_demo() -> None:
    pytest.importorskip("PyQt5")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PyQt5.QtWidgets import QApplication

    from half_linac.src.apps.dispersion_correction.gui.main_window import MainWindow
    from half_linac.src.apps.dispersion_correction.profile_runtime import (
        load_profile_run_config,
    )
    from half_linac.src.shared.machine_profile import load_app_context

    app = QApplication.instance() or QApplication([])
    context = load_app_context(
        "dispersion_correction",
        machine_id="irfel",
        control_backend="real",
    )
    _, config = load_profile_run_config(context)
    profile = MainWindow(config, context)
    profile.show()
    app.processEvents()

    assert profile.offline_demo_button.isVisibleTo(profile)
    assert profile.next_action_button.isVisibleTo(profile)
    assert not profile.next_action_button.isEnabled()
    profile.offline_demo_button.click()
    app.processEvents()
    demo = profile._offline_demo_window

    assert demo is not None
    assert demo.isVisible()
    assert demo.app_context is None
    assert demo.config.backend.type == "offline"
    assert profile.config.backend.type == "epics"
    assert profile.app_context is context

    demo.close()
    profile.close()
