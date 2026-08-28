import json

import numpy as np
import pytest

from gotacc.interfaces.epics import BPMGuardConstraintPolicy, FelEnergyGuardPolicy
from gotacc.interfaces.policies import POLICY_REGISTRY


def test_fel_and_bpm_presets_match_legacy_policy_behavior():
    backend = type(
        "Backend",
        (),
        {
            "objective_names": ["fel_energy"],
            "obj_pvnames": ["FEL:ENERGY"],
            "constraint_names": ["orbit_x"],
            "constraint_pvnames": ["BPM:01:X"],
            "constraint_bounds": [(-1.0, 1.0)],
        },
    )()

    fel_spec = POLICY_REGISTRY.expand_preset("objective", "fel_energy_guard")
    fel_rule = POLICY_REGISTRY.build("objective", fel_spec["name"], fel_spec["kwargs"])
    total = np.asarray([[2e6], [3e6], [4e6]])
    reduced = np.asarray([3e6])
    np.testing.assert_allclose(
        fel_rule.post_reduce(reduced, total, backend),
        FelEnergyGuardPolicy().post_reduce(reduced, total, backend),
    )

    bpm_spec = POLICY_REGISTRY.expand_preset("constraint", "bpm_guard")
    bpm_rule = POLICY_REGISTRY.build("constraint", bpm_spec["name"], bpm_spec["kwargs"])
    zeros = np.zeros((3, 1))
    np.testing.assert_allclose(
        bpm_rule.post_reduce(np.asarray([0.0]), zeros, backend),
        BPMGuardConstraintPolicy().post_reduce(np.asarray([0.0]), zeros, backend),
    )


def test_structured_rule_editor_loads_presets_without_raw_json(monkeypatch):
    pytest.importorskip("PyQt5")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication, QPlainTextEdit
    from gotacc.gui.views.tool_dialogs import SampleGuardRuleEditorDialog

    app = QApplication.instance() or QApplication([])
    spec = POLICY_REGISTRY.expand_preset("objective", "fel_energy_guard")
    dialog = SampleGuardRuleEditorDialog(
        kind="objective",
        target_names=["fel_energy", "beam_current"],
        policy_name=spec["name"],
        kwargs=spec["kwargs"],
        preset_name="fel_energy_guard",
    )
    try:
        assert dialog.findChildren(QPlainTextEdit) == []
        assert dialog.comboBox_preset.currentData() == "fel_energy_guard"
        assert dialog.tableWidget_conditions.rowCount() == 2
        state = dialog.rule_state()
        assert state["name"] == "sample_guard"
        assert state["kwargs"]["target"] == "fel_energy"
        assert state["kwargs"]["match"] == "any"
        assert state["kwargs"]["action"] == {"type": "replace", "value": 0.0}
        json.dumps(state["kwargs"])
    finally:
        dialog.close()
        app.processEvents()


def test_mapping_bound_rule_editor_locks_and_persists_target(monkeypatch):
    pytest.importorskip("PyQt5")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication
    from gotacc.gui.views.tool_dialogs import SampleGuardRuleEditorDialog

    app = QApplication.instance() or QApplication([])
    spec = POLICY_REGISTRY.expand_preset("constraint", "bpm_guard")
    dialog = SampleGuardRuleEditorDialog(
        kind="constraint",
        target_names=["orbit_x", "orbit_y"],
        kwargs=spec["kwargs"],
        preset_name="bpm_guard",
        locked_target="orbit_y",
    )
    try:
        assert not dialog.comboBox_target.isEnabled()
        assert dialog.comboBox_target.currentText() == "orbit_y"
        state = dialog.rule_state()
        assert state["kwargs"]["target"] == "orbit_y"
        assert state["kwargs"]["target_col"] == 1
    finally:
        dialog.close()
        app.processEvents()


def test_policy_template_picker_is_preset_first_and_explains_setup(monkeypatch):
    pytest.importorskip("PyQt5")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication, QDialogButtonBox
    from gotacc.gui.views.tool_dialogs import PolicyTemplatePickerDialog

    app = QApplication.instance() or QApplication([])
    objective = PolicyTemplatePickerDialog(
        kind="objective",
        target="fel_energy",
        pv_name="FEL:ENERGY",
        custom_presets=[
            {
                "id": "custom_stable",
                "name": "Stable Signal",
                "kind": "objective",
                "description": "Keep a locally validated stable-signal rule.",
                "policy": {
                    "name": "sample_guard",
                    "kwargs": {
                        "conditions": [
                            {"metric": "std", "operator": "lt", "value": 0.01}
                        ],
                        "match": "all",
                        "action": {"type": "replace", "value": 0.0},
                    },
                },
            }
        ],
    )
    constraint = PolicyTemplatePickerDialog(
        kind="constraint",
        target="orbit_x",
        pv_name="BPM:01:X",
        constraint_bound_ready=False,
    )
    try:
        assert objective.tableWidget_templates.rowCount() == 4
        assert objective.selected_template() is None
        assert not objective.buttonBox.button(QDialogButtonBox.Ok).isEnabled()
        objective.tableWidget_templates.setCurrentCell(0, 0)
        assert objective.selected_template()["id"] == "fel_energy_guard"
        assert "replace the result with 0" in (
            objective.tableWidget_templates.item(0, 1).text()
        )
        assert objective.tableWidget_templates.item(2, 0).text() == "Stable Signal"
        assert objective.tableWidget_templates.item(3, 0).text() == "Custom Policy"

        assert constraint.tableWidget_templates.rowCount() == 2
        constraint.tableWidget_templates.setCurrentCell(0, 0)
        assert constraint.selected_template()["id"] == "bpm_guard"
        assert not constraint.label_setup.isHidden()
        assert "Lower or Upper bound" in constraint.label_setup.text()
        constraint.tableWidget_templates.setCurrentCell(1, 0)
        assert constraint.selected_template()["id"] == "custom"
        assert constraint.label_setup.isHidden()
    finally:
        objective.close()
        constraint.close()
        app.processEvents()


def test_structured_rule_editor_applies_machine_custom_preset(monkeypatch):
    pytest.importorskip("PyQt5")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication
    from gotacc.gui.views.tool_dialogs import SampleGuardRuleEditorDialog

    app = QApplication.instance() or QApplication([])
    custom_preset = {
        "id": "custom_stable_signal",
        "name": "Stable Signal",
        "kind": "objective",
        "policy": {
            "name": "sample_guard",
            "kwargs": {
                "target": None,
                "target_col": 0,
                "conditions": [{"metric": "std", "operator": "lt", "value": 0.01}],
                "match": "all",
                "action": {"type": "replace", "value": -1.0},
            },
        },
    }
    dialog = SampleGuardRuleEditorDialog(
        kind="objective",
        target_names=["energy", "charge"],
        custom_presets=[custom_preset],
        locked_target="charge",
    )
    try:
        dialog.comboBox_preset.setCurrentIndex(
            dialog.comboBox_preset.findData("custom_stable_signal")
        )
        state = dialog.rule_state()
        assert state["preset"] == "custom_stable_signal"
        assert state["kwargs"]["target"] == "charge"
        assert state["kwargs"]["target_col"] == 1
        assert state["kwargs"]["conditions"] == [
            {"metric": "std", "operator": "lt", "value": 0.01}
        ]
    finally:
        dialog.close()
        app.processEvents()


def test_policy_editor_uses_plain_language_and_inline_validation(monkeypatch):
    pytest.importorskip("PyQt5")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication, QDialogButtonBox
    from gotacc.gui.views.tool_dialogs import SampleGuardRuleEditorDialog

    app = QApplication.instance() or QApplication([])
    dialog = SampleGuardRuleEditorDialog(
        kind="objective",
        target_names=["fel_energy"],
        locked_target="fel_energy",
        pv_name="FEL:ENERGY",
    )
    try:
        metric = dialog.tableWidget_conditions.cellWidget(0, 0)
        operator = dialog.tableWidget_conditions.cellWidget(0, 1)
        assert dialog.windowTitle() == "Edit Objective Policy"
        assert dialog.comboBox_preset.itemText(0) == "Custom Policy"
        assert dialog.comboBox_match.currentText() in {"Any condition", "All conditions"}
        assert metric.currentText() == dialog.METRIC_LABELS[metric.currentData()]
        assert operator.currentText() == dialog.OPERATOR_LABELS[operator.currentData()]
        assert dialog.rule_state()["kwargs"]["conditions"][0]["metric"] in dialog.METRICS
        assert "Policy behavior: fel_energy" in dialog.label_summary.text()
        assert "FEL:ENERGY" in dialog.findChild(
            type(dialog.label_summary), "policyEditorTarget"
        ).text()

        dialog.tableWidget_conditions.setRowCount(0)
        dialog._on_rule_changed()
        assert not dialog.buttonBox.button(QDialogButtonBox.Ok).isEnabled()
        assert "Add at least one condition" in dialog.label_validation.text()
    finally:
        dialog.close()
        app.processEvents()


def test_template_policy_opens_read_only_before_customization(monkeypatch):
    pytest.importorskip("PyQt5")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication, QDialogButtonBox
    from gotacc.gui.views.tool_dialogs import SampleGuardRuleEditorDialog

    app = QApplication.instance() or QApplication([])
    spec = POLICY_REGISTRY.expand_preset("constraint", "bpm_guard")
    dialog = SampleGuardRuleEditorDialog(
        kind="constraint",
        target_names=["orbit_x"],
        kwargs=spec["kwargs"],
        preset_name="bpm_guard",
        locked_target="orbit_x",
        pv_name="BPM:01:X",
        read_only=True,
        template_display_name="BPM Zero Guard",
    )
    try:
        assert dialog.windowTitle() == "View Constraint Policy"
        assert "Based on Policy Template" in dialog.label_mode.text()
        assert not dialog.comboBox_preset.isEnabled()
        assert not dialog.tableWidget_conditions.cellWidget(0, 0).isEnabled()
        assert dialog.pushButton_addCondition.isHidden()
        assert (
            dialog.buttonBox.button(QDialogButtonBox.Ok).text()
            == "Customize Policy"
        )
        assert dialog.buttonBox.button(QDialogButtonBox.Cancel).text() == "Close"
    finally:
        dialog.close()
        app.processEvents()


def test_mapping_policy_manager_exposes_row_management_actions(monkeypatch):
    pytest.importorskip("PyQt5")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication, QDialog
    from gotacc.gui.views.tool_dialogs import MappingPolicyManagerDialog

    app = QApplication.instance() or QApplication([])
    dialog = MappingPolicyManagerDialog(
        target="orbit_x",
        pv_name="BPM:01:X",
        policies=[
            {
                "enabled": True,
                "preset": "BPM Zero Guard",
                "is_template": True,
                "summary": "max_abs ≤ 1e-09 → Mark infeasible",
            }
        ],
    )
    try:
        assert dialog.pushButton_edit.text() == "View Policy"
        assert not dialog.pushButton_savePreset.isEnabled()
        assert dialog.pushButton_moveUp.isHidden()
        assert dialog.pushButton_moveDown.isHidden()
        dialog.pushButton_toggle.click()
        assert dialog.result() == QDialog.Accepted
        assert dialog.requested_action() == ("toggle", 0)
    finally:
        dialog.close()
        app.processEvents()


def test_mapping_policy_manager_shows_order_controls_only_for_multiple_policies(
    monkeypatch,
):
    pytest.importorskip("PyQt5")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication, QDialog
    from gotacc.gui.views.tool_dialogs import MappingPolicyManagerDialog

    app = QApplication.instance() or QApplication([])
    policies = [
        {
            "enabled": True,
            "preset": "First",
            "is_template": True,
            "summary": "First behavior",
        },
        {
            "enabled": True,
            "preset": "Custom Policy",
            "is_template": False,
            "summary": "Second behavior",
        },
    ]
    dialog = MappingPolicyManagerDialog(
        target="fel_energy",
        pv_name="FEL:ENERGY",
        policies=policies,
    )
    try:
        assert dialog.tableWidget_policies.item(0, 0).text() == "1"
        assert dialog.tableWidget_policies.item(1, 0).text() == "2"
        assert not dialog.pushButton_moveUp.isHidden()
        assert not dialog.pushButton_moveDown.isHidden()
        assert not dialog.pushButton_moveUp.isEnabled()
        assert dialog.pushButton_moveDown.isEnabled()
        dialog.pushButton_moveDown.click()
        assert dialog.result() == QDialog.Accepted
        assert dialog.requested_action() == ("move_down", 0)
    finally:
        dialog.close()
        app.processEvents()
