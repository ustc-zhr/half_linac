import sys

import pytest

pytest.importorskip("PyQt5")

from PyQt5.QtWidgets import QApplication, QDialog, QMessageBox

from gotacc.gui.services.task_service import TaskService
from gotacc.gui.services.machine_profile import MachineProfile, save_machine_profile
from gotacc.gui.views.main_window import MainWindow
from gotacc.gui.views.tool_dialogs import BoundsToolsDialog
from gotacc.interfaces.policies import POLICY_REGISTRY


def _online_task(tmp_path):
    return {
        "task_name": "machine_sync_test",
        "description": "",
        "mode": "Online EPICS",
        "objective_type": "Single Objective",
        "algorithm": "BO",
        "max_evaluations": 5,
        "seed": 1,
        "workdir": str(tmp_path),
        "test_function": "",
        "variables": [
            {
                "Enable": "Y",
                "Name": "Q1",
                "Lower": "-1",
                "Upper": "1",
                "Initial": "0.1",
                "Group": "main",
            },
            {
                "Enable": "Y",
                "Name": "Q2",
                "Lower": "-2",
                "Upper": "2",
                "Initial": "0.2",
                "Group": "main",
            },
        ],
        "objectives": [
            {
                "Enable": "Y",
                "Name": "Transmission",
                "Direction": "maximize",
                "Weight": "1",
                "Samples": "1",
                "Math": "mean",
            }
        ],
        "constraints": [],
        "algorithm_params": [],
        "machine": {
            "ca_address": "",
            "restore_on_abort": True,
            "readback_check": True,
            "readback_tol": 1e-6,
            "set_interval": 0.5,
            "sample_interval": 0.1,
            "write_timeout": 2.0,
            "write_policy": "none",
            "objective_policies": [],
            "constraint_policies": [],
            "write_links": [],
            "mapping": [
                {"Role": "knob", "Name": "Q2", "PV Name": "TEST:Q2:SET", "Readback": "TEST:Q2:RB"},
                {"Role": "knob", "Name": "Q1", "PV Name": "TEST:Q1:SET", "Readback": "TEST:Q1:RB"},
                {"Role": "objective", "Name": "Transmission", "PV Name": "TEST:TRANS", "Readback": ""},
            ],
        },
    }


@pytest.fixture
def window(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication(sys.argv)
    instance = MainWindow()
    yield instance
    instance.close()
    app.processEvents()


def test_mapping_master_detail_displays_library_signal_as_read_only(tmp_path, window):
    task = _online_task(tmp_path)
    window._apply_task_payload(task, goto_builder=False)
    table = window.machine_ui.tableWidget_mapping
    table.setCurrentCell(2, 1)

    assert window.machine_ui.label_mappingDetailTitle.text() == "Objective · Transmission"
    assert window.machine_ui.lineEdit_mappingDetailPv.text() == "TEST:TRANS"
    assert window.machine_ui.pushButton_manageMappingPolicies.text() == "Add Policy"
    assert not window.machine_ui.comboBox_mappingDetailRole.isEnabled()
    assert all(
        editor.isReadOnly()
        for editor in (
            window.machine_ui.lineEdit_mappingDetailName,
            window.machine_ui.lineEdit_mappingDetailPv,
            window.machine_ui.lineEdit_mappingDetailReadback,
            window.machine_ui.lineEdit_mappingDetailGroup,
            window.machine_ui.lineEdit_mappingDetailNote,
        )
    )
    assert "PV library" in window.machine_ui.label_mappingDetailSubtitle.text()
    assert "Synced To Task" in window.machine_ui.label_pvLibrarySummary.text()
    assert not window.machine_ui.pushButton_applySelectedPvLibrary.isEnabled()


def test_new_tasks_use_mode_specific_defaults_without_mode_switch_data_loss(window):
    controller = window.task_builder_controller

    controller.create_new_online_task()

    assert window.task_ui.tableWidget_variables.rowCount() == 0
    assert window.task_ui.tableWidget_objectives.rowCount() == 0
    assert window.task_ui.tableWidget_constraints.rowCount() == 0
    assert window.machine_ui.tableWidget_mapping.rowCount() == 0
    assert window.machine_ui.tableWidget_writeLinks.rowCount() == 0
    assert not window.task_ui.label_variablesEmptyState.isHidden()
    assert "Machine Profile" in window.task_ui.label_variablesEmptyState.text()

    controller.create_new_offline_task()

    variables = TaskService.table_to_records(window.task_ui.tableWidget_variables)
    objectives = TaskService.table_to_records(window.task_ui.tableWidget_objectives)
    assert [row["Name"] for row in variables] == ["x0", "x1"]
    assert all(row["Lower"] == "-2.0" and row["Upper"] == "2.0" for row in variables)
    assert [row["Name"] for row in objectives] == ["rosenbrock"]
    assert window.task_ui.tableWidget_constraints.rowCount() == 0
    assert window.machine_ui.tableWidget_mapping.rowCount() == 0
    assert "constrained benchmarks" in window.task_ui.label_constraintsEmptyState.text()
    config = TaskService.build_task_config(window._current_task())
    assert config.backend.type == "offline"

    window.task_ui.comboBox_mode.setCurrentText("Online EPICS")
    QApplication.processEvents()
    assert [
        row["Name"]
        for row in TaskService.table_to_records(window.task_ui.tableWidget_variables)
    ] == ["x0", "x1"]
    assert [
        row["Name"]
        for row in TaskService.table_to_records(window.task_ui.tableWidget_objectives)
    ] == ["rosenbrock"]


def test_empty_task_tables_can_add_and_remove_real_rows(window):
    controller = window.task_builder_controller
    controller.create_new_online_task()

    for field in ("Variable", "Objective", "Constraint"):
        assert getattr(window.task_ui, f"pushButton_add{field}Row").isHidden()
        assert not getattr(window.task_ui, f"pushButton_remove{field}Rows").isEnabled()
    assert "manually" not in window.task_ui.label_variablesEmptyState.text()

    window.task_ui.comboBox_mode.setCurrentText("Offline")
    QApplication.processEvents()
    for field in ("Variable", "Objective", "Constraint"):
        assert not getattr(window.task_ui, f"pushButton_add{field}Row").isHidden()

    controller.add_task_table_row("constraints")
    rows = TaskService.table_to_records(window.task_ui.tableWidget_constraints)
    assert rows[0] == {
        "Enable": "Y",
        "Name": "constraint_1",
        "Lower": "",
        "Upper": "",
        "Math": "mean",
    }
    assert window.task_ui.label_constraintsEmptyState.isHidden()

    controller.remove_selected_task_rows("constraints")
    assert window.task_ui.tableWidget_constraints.rowCount() == 0
    assert not window.task_ui.label_constraintsEmptyState.isHidden()
    assert not window.task_ui.pushButton_removeConstraintRows.isEnabled()


def test_mapping_sync_preserves_parameters_by_name_and_can_undo(
    tmp_path, window, monkeypatch
):
    task = _online_task(tmp_path)
    task["variables"].append(
        {
            "Enable": "N",
            "Name": "Legacy",
            "Lower": "-5",
            "Upper": "5",
            "Initial": "0",
            "Group": "main",
        }
    )
    task["machine"]["mapping"].insert(
        2,
        {"Role": "knob", "Name": "Q3", "PV Name": "TEST:Q3:SET", "Readback": "TEST:Q3:RB"},
    )
    window._apply_task_payload(task, goto_builder=False)
    window.go_to_page(window.PAGE_MACHINE)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.Yes)

    assert "Selection Changed · Sync Needed" in (
        window.machine_ui.label_pvLibrarySummary.text()
    )
    assert window.machine_ui.pushButton_applySelectedPvLibrary.isEnabled()

    window.machine_controller.apply_selected_pv_library_entries()

    rows = TaskService.table_to_records(window.task_ui.tableWidget_variables)
    assert [row["Name"] for row in rows] == ["Q2", "Q1", "Q3"]
    assert rows[0]["Lower"] == "-2"
    assert rows[0]["Initial"] == "0.2"
    assert rows[1]["Lower"] == "-1"
    assert rows[1]["Initial"] == "0.1"
    assert rows[2]["Enable"] == "Y"
    assert rows[2]["Lower"] == ""
    assert rows[2]["Upper"] == ""
    assert rows[2]["Initial"] == ""
    assert "Synced · 1 Knob Needs Setup" in (
        window.machine_ui.label_pvLibrarySummary.text()
    )
    assert not window.machine_ui.pushButton_applySelectedPvLibrary.isEnabled()
    assert window.machine_ui.pushButton_undoMappingSync.isEnabled()
    assert window.ui.tabWidget_configure.currentIndex() == window.CONFIGURE_TAB_TASK_BUILDER
    assert window.task_ui.tabWidget_tables.currentIndex() == 0
    assert window.task_ui.tableWidget_variables.currentRow() == 2

    window.machine_controller.undo_last_mapping_sync()

    restored = TaskService.table_to_records(window.task_ui.tableWidget_variables)
    assert [row["Name"] for row in restored] == ["Q1", "Q2", "Legacy"]
    assert not window.machine_ui.pushButton_undoMappingSync.isEnabled()


def test_machine_profile_load_is_independent_until_confirmed_task_sync(
    tmp_path, window, monkeypatch
):
    window._apply_task_payload(_online_task(tmp_path), goto_builder=False)
    profile = MachineProfile.create(
        "Alternate Beamline",
        {
            "mapping": [
                {
                    "Role": "knob",
                    "Name": "Q3",
                    "PV Name": "ALT:Q3:SET",
                    "Readback": "ALT:Q3:RB",
                },
                {
                    "Role": "objective",
                    "Name": "Energy",
                    "PV Name": "ALT:ENERGY",
                },
            ],
            "write_links": [],
            "policy_bindings": [],
            "policy_presets": [],
        },
        profile_id="alternate-beamline",
    )
    path = save_machine_profile(profile, tmp_path / "alternate.json")
    monkeypatch.setattr(
        window.machine_controller,
        "_selected_machine_profile_path",
        lambda: str(path),
    )

    window.machine_controller.open_machine_profile()

    assert [
        row["Name"]
        for row in TaskService.table_to_records(window.machine_ui.tableWidget_mapping)
    ] == ["Q3", "Energy"]
    assert [
        row["Name"]
        for row in TaskService.table_to_records(window.task_ui.tableWidget_variables)
    ] == ["Q1", "Q2"]
    assert window.machine_ui.machine_profile["profile_id"] == "alternate-beamline"
    assert window._current_task()["machine"]["profile"]["name"] == "Alternate Beamline"

    previews = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda _parent, _title, message, *_args, **_kwargs: (
            previews.append(message) or QMessageBox.No
        ),
    )
    window.machine_controller.apply_selected_pv_library_entries()
    assert "Knobs: add Q3; remove Q1, Q2" in previews[0]
    assert "Objectives: add Energy; remove Transmission" in previews[0]
    assert [
        row["Name"]
        for row in TaskService.table_to_records(window.task_ui.tableWidget_variables)
    ] == ["Q1", "Q2"]

    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.Yes)
    window.machine_controller.apply_selected_pv_library_entries()
    assert [
        row["Name"]
        for row in TaskService.table_to_records(window.task_ui.tableWidget_variables)
    ] == ["Q3"]
    assert [
        row["Name"]
        for row in TaskService.table_to_records(window.task_ui.tableWidget_objectives)
    ] == ["Energy"]


def test_pv_check_covers_current_contract_and_becomes_stale(tmp_path, window, monkeypatch):
    task = _online_task(tmp_path)
    window._apply_task_payload(task, goto_builder=False)
    window.go_to_page(window.PAGE_MACHINE)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.Yes)
    window.machine_controller.apply_selected_pv_library_entries()
    assert window.ui.tabWidget_configure.currentIndex() == window.CONFIGURE_TAB_MACHINE
    reads = []

    def fake_caget(pvname, *, timeout):
        reads.append((pvname, timeout))
        return 1.0

    monkeypatch.setattr(window.machine_controller, "_prepare_epics_caget", lambda: fake_caget)

    assert window.machine_controller.check_machine_pv(show_dialog=False)
    assert {pv for pv, _timeout in reads} == {
        "TEST:Q1:SET",
        "TEST:Q1:RB",
        "TEST:Q2:SET",
        "TEST:Q2:RB",
        "TEST:TRANS",
    }
    current = window._current_task()
    assert window.machine_controller.ensure_machine_ready_for_online(current)
    assert window.machine_ui.label_statusValue.text() == "PV Check Passed"

    window.machine_ui.tableWidget_mapping.item(0, 2).setText("TEST:Q2:NEW")
    QApplication.processEvents()

    assert window.machine_ui.label_statusValue.text() == "Stale"
    assert window.state.last_test_read_status == "Stale"
    assert not window.state.machine_check_identity
    assert not window.machine_controller.ensure_machine_ready_for_online(window._current_task())


def test_online_validation_rejects_mapping_ambiguity(tmp_path):
    task = _online_task(tmp_path)
    task["machine"]["mapping"][1]["PV Name"] = "TEST:Q2:SET"
    task["variables"].append(dict(task["variables"][0]))

    ok, errors = TaskService.validate_task_data(task)

    assert not ok
    assert any("Duplicate enabled variable name" in error for error in errors)
    assert any("share Setpoint PV" in error for error in errors)


def test_mapping_policy_status_stays_compact_and_reports_task_setup(window):
    window.task_ui.comboBox_mode.setCurrentText("Online EPICS")
    window.task_builder_controller.fill_table_from_records(
        window.task_ui.tableWidget_constraints,
        [
            {
                "Enable": "Y",
                "Name": "orbit_x",
                "Lower": "",
                "Upper": "",
                "Math": "mean",
            }
        ],
    )
    window.task_builder_controller.fill_table_from_records(
        window.machine_ui.tableWidget_mapping,
        [
            {
                "Role": "constraint",
                "Name": "orbit_x",
                "PV Name": "TEST:BPM:X",
            }
        ],
    )
    spec = POLICY_REGISTRY.expand_preset("constraint", "bpm_guard")
    kwargs = spec["kwargs"]
    kwargs["target"] = "orbit_x"
    window.machine_ui.policy_bindings = [
        {
            "kind": "constraint",
            "target": "orbit_x",
            "enabled": True,
            "preset": "bpm_guard",
            "policy": {"name": spec["name"], "kwargs": kwargs},
        }
    ]

    window._refresh_mapping_policy_widgets()
    window.machine_controller.update_pv_library_summary()
    window.machine_ui.tableWidget_mapping.setCurrentCell(0, 1)
    QApplication.processEvents()

    policy_cell = window.machine_ui.tableWidget_mapping.item(0, 6)
    assert policy_cell.text() == "BPM Zero Guard · Issue"
    assert "requires Lower or Upper" in policy_cell.toolTip()
    assert "BPM Zero Guard · Issue" in window.machine_ui.label_mappingPolicySummary.text()
    assert "1 Issue" in window.machine_ui.label_pvLibrarySummary.text()
    assert not window.machine_ui.pushButton_applySelectedPvLibrary.isEnabled()

    window.task_ui.tableWidget_constraints.item(0, 3).setText("1.0")
    window._refresh_mapping_policy_widgets()
    window.machine_controller.update_pv_library_summary()

    assert window.machine_ui.tableWidget_mapping.item(0, 6).text() == (
        "BPM Zero Guard · Ready"
    )
    assert "Synced To Task" in window.machine_ui.label_pvLibrarySummary.text()
    assert not window.machine_ui.pushButton_applySelectedPvLibrary.isEnabled()


def test_quick_add_opens_advanced_editor_only_for_custom_rule(window, monkeypatch):
    import gotacc.gui.views.main_window as main_window_module

    window.task_builder_controller.fill_table_from_records(
        window.machine_ui.tableWidget_mapping,
        [
            {
                "Role": "objective",
                "Name": "beam_signal",
                "PV Name": "TEST:BEAM",
            }
        ],
    )
    window._refresh_mapping_policy_widgets()
    editor_calls = []
    manager_calls = []
    monkeypatch.setattr(
        main_window_module.PolicyTemplatePickerDialog,
        "exec_",
        lambda _dialog: QDialog.Accepted,
    )
    monkeypatch.setattr(
        main_window_module.PolicyTemplatePickerDialog,
        "selected_template",
        lambda _dialog: {
            "id": "custom",
            "name": "Custom Policy",
            "policy": None,
            "custom_rule": True,
        },
    )
    monkeypatch.setattr(
        main_window_module.SampleGuardRuleEditorDialog,
        "exec_",
        lambda _dialog: editor_calls.append(True) or QDialog.Accepted,
    )
    monkeypatch.setattr(
        main_window_module.MappingPolicyManagerDialog,
        "exec_",
        lambda _dialog: manager_calls.append(True) or QDialog.Rejected,
    )

    window._manage_mapping_policies(0)

    assert editor_calls == [True]
    assert manager_calls == []
    assert len(window.machine_ui.policy_bindings) == 1
    assert window.machine_ui.policy_bindings[0]["preset"] == "custom"
    assert window.machine_ui.policy_bindings[0]["target"] == "beam_signal"


def test_template_binding_requires_explicit_customize_before_edit(
    tmp_path, window, monkeypatch
):
    import gotacc.gui.views.main_window as main_window_module

    task = _online_task(tmp_path)
    spec = POLICY_REGISTRY.expand_preset("objective", "fel_energy_guard")
    spec["kwargs"]["target"] = "Transmission"
    task["machine"]["policy_bindings"] = [
        {
            "kind": "objective",
            "target": "Transmission",
            "enabled": True,
            "preset": "fel_energy_guard",
            "policy": spec,
        }
    ]
    window._apply_task_payload(task, goto_builder=False)

    calls = []
    responses = iter([QDialog.Accepted, QDialog.Rejected])

    def inspect_then_cancel(dialog):
        calls.append(dialog.read_only)
        return next(responses)

    monkeypatch.setattr(
        main_window_module.SampleGuardRuleEditorDialog,
        "exec_",
        inspect_then_cancel,
    )
    assert not window._edit_policy_rule_row("objective", 0)
    assert calls == [True, False]
    assert window.machine_ui.policy_bindings[0]["preset"] == "fel_energy_guard"

    calls.clear()
    monkeypatch.setattr(
        main_window_module.SampleGuardRuleEditorDialog,
        "exec_",
        lambda dialog: calls.append(dialog.read_only) or QDialog.Accepted,
    )
    assert window._edit_policy_rule_row("objective", 0)
    assert calls == [True, False]
    assert window.machine_ui.policy_bindings[0]["preset"] == "custom"
    assert window.machine_ui.policy_bindings[0]["policy"]["kwargs"]["target"] == (
        "Transmission"
    )


def test_policy_manager_reorders_existing_bindings_without_new_config_fields(
    tmp_path, window, monkeypatch
):
    import gotacc.gui.views.main_window as main_window_module

    task = _online_task(tmp_path)
    first = POLICY_REGISTRY.expand_preset("objective", "fel_energy_guard")
    second = POLICY_REGISTRY.expand_preset("objective", "zero_guard")
    for spec in (first, second):
        spec["kwargs"]["target"] = "Transmission"
    task["machine"]["policy_bindings"] = [
        {
            "kind": "objective",
            "target": "Transmission",
            "enabled": True,
            "preset": "fel_energy_guard",
            "policy": first,
        },
        {
            "kind": "objective",
            "target": "Transmission",
            "enabled": True,
            "preset": "zero_guard",
            "policy": second,
        },
    ]
    window._apply_task_payload(task, goto_builder=False)

    calls = []

    def move_first_down_then_close(dialog):
        calls.append(True)
        if len(calls) == 1:
            dialog._request = ("move_down", 0)
            return QDialog.Accepted
        return QDialog.Rejected

    monkeypatch.setattr(
        main_window_module.MappingPolicyManagerDialog,
        "exec_",
        move_first_down_then_close,
    )
    window._manage_mapping_policies(2)

    assert [
        binding["preset"] for binding in window.machine_ui.policy_bindings
    ] == ["zero_guard", "fel_energy_guard"]
    assert all("order" not in binding for binding in window.machine_ui.policy_bindings)


def test_legacy_policy_rows_migrate_to_canonical_machine_bindings(tmp_path, window):
    task = _online_task(tmp_path)
    task["machine"]["objective_policies"] = [
        {
            "Enabled": "True",
            "Policy Name": "fel_energy_guard",
            "Kwargs JSON": (
                '{"target_col": 0, "large_threshold": 2500.0, '
                '"change_threshold": 0.0002}'
            ),
        }
    ]

    window._apply_task_payload(task, goto_builder=False)

    assert len(window.machine_ui.policy_bindings) == 1
    binding = window.machine_ui.policy_bindings[0]
    assert binding["kind"] == "objective"
    assert binding["target"] == "Transmission"
    assert binding["preset"] == "fel_energy_guard"
    assert binding["policy"]["name"] == "sample_guard"
    assert binding["policy"]["kwargs"]["conditions"][0]["value"] == 2500.0
    assert binding["policy"]["kwargs"]["conditions"][1]["value"] == 0.0002

    serialized_machine = window._current_task()["machine"]
    assert serialized_machine["policy_bindings"] == window.machine_ui.policy_bindings
    assert "objective_policies" not in serialized_machine
    assert "constraint_policies" not in serialized_machine


def test_machine_custom_policy_preset_can_be_saved_renamed_and_deleted(
    tmp_path, window, monkeypatch
):
    import gotacc.gui.views.main_window as main_window_module

    task = _online_task(tmp_path)
    spec = POLICY_REGISTRY.expand_preset("objective", "fel_energy_guard")
    spec["kwargs"]["target"] = "Transmission"
    task["machine"]["policy_bindings"] = [
        {
            "kind": "objective",
            "target": "Transmission",
            "enabled": True,
            "preset": "custom",
            "policy": spec,
        }
    ]
    window._apply_task_payload(task, goto_builder=False)
    monkeypatch.setattr(
        main_window_module.QInputDialog,
        "getText",
        lambda *_args, **_kwargs: ("Transmission Quality", True),
    )

    window._save_policy_binding_as_preset("objective", 0)

    assert len(window.machine_ui.policy_presets) == 1
    preset = window.machine_ui.policy_presets[0]
    assert preset["id"] == "custom_transmission_quality"
    assert preset["policy"]["kwargs"]["target"] is None
    assert window.machine_ui.policy_bindings[0]["preset"] == preset["id"]
    assert window.machine_ui.tableWidget_policyPresets.rowCount() == 4
    assert window.machine_ui.tableWidget_policyPresets.item(3, 2).text() == "Machine"

    serialized_machine = window._current_task()["machine"]
    assert serialized_machine["policy_presets"] == [preset]
    assert serialized_machine["policy_bindings"][0]["preset"] == preset["id"]
    saved_task = window._current_task()
    window._apply_task_payload(saved_task, goto_builder=False)
    assert window.machine_ui.policy_presets[0]["id"] == preset["id"]
    assert window.machine_ui.policy_bindings[0]["preset"] == preset["id"]
    preset = window.machine_ui.policy_presets[0]

    monkeypatch.setattr(
        main_window_module.QInputDialog,
        "getText",
        lambda *_args, **_kwargs: ("Transmission Stable", True),
    )
    window._rename_custom_policy_preset(preset["id"])
    assert preset["name"] == "Transmission Stable"
    assert window.machine_ui.policy_bindings[0]["preset"] == preset["id"]

    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: main_window_module.QMessageBox.Yes,
    )
    window._delete_custom_policy_preset(preset["id"])
    assert window.machine_ui.policy_presets == []
    assert window.machine_ui.policy_bindings[0]["preset"] == "custom"


def test_bounds_tool_previews_exact_plan_before_apply(tmp_path, window, monkeypatch):
    window._apply_task_payload(_online_task(tmp_path), goto_builder=False)
    controller = window.task_builder_controller
    dialog = BoundsToolsDialog(window)
    controller._bounds_dialog = dialog
    source_reads = []
    try:
        ui = dialog.ui
        ui.comboBox_boundsSource.setCurrentText("Initial values")
        ui.comboBox_boundsMode.setCurrentText("± absolute delta")
        ui.doubleSpinBox_boundsPrimary.setValue(0.5)
        ui.checkBox_boundsUpdateInitial.setChecked(True)
        controller.update_bounds_tool_controls()
        monkeypatch.setattr(
            controller,
            "_resolve_bounds_source_values",
            lambda _task, rows: source_reads.append(len(rows)) or [1.0, 2.0],
        )

        controller.preview_bounds_tool()

        assert source_reads == [2]
        assert ui.tableWidget_boundsPreview.rowCount() == 2
        assert ui.tableWidget_boundsPreview.item(0, 0).text() == "Q1"
        assert ui.tableWidget_boundsPreview.item(0, 1).text() == "1"
        assert ui.tableWidget_boundsPreview.item(0, 2).text() == "0.5"
        assert ui.tableWidget_boundsPreview.item(0, 3).text() == "1.5"
        assert ui.tableWidget_boundsPreview.item(0, 4).text() == "1"
        assert ui.pushButton_applyBounds.isEnabled()
        assert ui.pushButton_applyBounds.property("primary") is True

        controller.apply_bounds_tool()

        assert source_reads == [2]
        variables = TaskService.table_to_records(window.task_ui.tableWidget_variables)
        assert variables[0]["Lower"] == "0.5"
        assert variables[0]["Upper"] == "1.5"
        assert variables[0]["Initial"] == "1"
        assert variables[1]["Lower"] == "1.5"
        assert variables[1]["Upper"] == "2.5"
        assert variables[1]["Initial"] == "2"
        assert not ui.pushButton_applyBounds.isEnabled()

        ui.doubleSpinBox_boundsPrimary.setValue(0.75)
        controller._on_bounds_tool_settings_changed()
        assert ui.tableWidget_boundsPreview.rowCount() == 0
        assert not controller._bounds_preview_plan

        ui.comboBox_boundsMode.setCurrentText("Fixed lower / upper")
        ui.checkBox_boundsUpdateInitial.setChecked(False)
        controller.update_bounds_tool_controls()
        ui.doubleSpinBox_boundsPrimary.setValue(-3.0)
        ui.doubleSpinBox_boundsSecondary.setValue(3.0)
        assert not ui.comboBox_boundsSource.isEnabled()
        monkeypatch.setattr(
            controller,
            "_resolve_bounds_source_values",
            lambda *_args: pytest.fail("Fixed bounds should not read a source"),
        )
        controller.preview_bounds_tool()
        assert ui.tableWidget_boundsPreview.item(0, 1).text() == "Not used"
        assert ui.tableWidget_boundsPreview.item(0, 2).text() == "-3"
        assert ui.tableWidget_boundsPreview.item(0, 3).text() == "3"
    finally:
        controller._bounds_dialog = None
        dialog.close()
