from pathlib import Path
import os
import json
from dataclasses import replace

import pytest

from half_linac.src.apps.setpoint_transfer.execution import (
    TransferExecutionError,
    append_execution_log,
    execute_transfer_plan,
    execute_restore,
    find_restore_conflicts,
    preflight_transfer_plan,
)
from half_linac.src.shared.machine_profile import MachineProfileError, load_profile
from half_linac.src.shared.setpoint_transfer import (
    backend_capabilities,
    DesignSetpoint,
    StagedSetpoint,
    build_transfer_plan,
    extract_design_setpoints,
    load_target_workspace,
    save_target_workspace,
)
from half_linac.src.shared.twiss_preview import run_twiss_preview


def _write_lattice(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "lattice_ini.lte"
    path.write_text(text, encoding="utf-8")
    return path


def test_extracts_unique_quad_k1_with_scientific_notation(tmp_path):
    path = _write_lattice(
        tmp_path,
        "QL01: QUAD,L=0.15,K1=-1.25E+01\n"
        "D1: DRIFT,L=1\n"
        "ALL_MAIN: LINE=(QL01,D1,QL01)\n",
    )
    values = extract_design_setpoints(path)
    assert [(item.element_id, item.value) for item in values] == [("QL01", -12.5)]


def test_rejects_nonfinite_design_value(tmp_path):
    path = _write_lattice(
        tmp_path,
        "QL01: QUAD,L=0.15,K1=nan\nALL_MAIN: LINE=(QL01)\n",
    )
    with pytest.raises(ValueError, match="must be finite"):
        extract_design_setpoints(path)


def test_plan_maps_vm_k1_and_reports_unknown_element():
    profile = load_profile("half")
    source = Path("design.lte")
    setpoints = (
        DesignSetpoint("QL01", "quad", "K1", 1.5, source),
        DesignSetpoint("NOT_A_QUAD", "quad", "K1", 2.0, source),
    )
    plan = build_transfer_plan(
        profile, setpoints, current_values={"QL01": 1.0},
        staged_setpoints=(StagedSetpoint("QL01", "K1", 1.5, "design"),),
    )
    assert plan.items[0].pv_name == "HALF:IN:AP:QUAD:QL01:K1:ao"
    assert plan.items[0].status == "ready"
    assert plan.items[1].status == "blocked"
    assert "not present" in plan.diagnostics[0]


def test_plan_rejects_missing_current_value_and_unknown_target():
    profile = load_profile("half")
    setpoint = DesignSetpoint("QL01", "quad", "K1", 1.5, Path("design.lte"))
    plan = build_transfer_plan(profile, (setpoint,))
    assert "Current VM value is unavailable" in plan.blockers[0].message
    assert "HALF:IN:AP:QUAD:QL01:K1:ao" in plan.blockers[0].message
    real_plan = build_transfer_plan(profile, (setpoint,), target_backend="real")
    assert real_plan.target_backend == "real"
    with pytest.raises(MachineProfileError, match="use 'vm' or 'real'"):
        build_transfer_plan(profile, (setpoint,), target_backend="offline")


def test_target_workspace_round_trip_excludes_current_values(tmp_path):
    path = tmp_path / "working_point.json"
    staged = (
        StagedSetpoint("QL01", "K1", 1.25, "manual"),
        StagedSetpoint("QL02", "K1", -2.5, "design"),
    )
    save_target_workspace(path, machine_id="half", staged_setpoints=staged)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert "current_value" not in json.dumps(payload)
    assert payload["created_at"]
    assert load_target_workspace(path, expected_machine_id="half") == staged


def test_backend_capabilities_enable_guarded_real_write():
    assert backend_capabilities("vm").can_write
    assert backend_capabilities("real").can_write
    assert backend_capabilities("real").can_read
    with pytest.raises(ValueError, match="Unsupported control backend"):
        backend_capabilities("offline")


def test_target_workspace_rejects_wrong_machine_and_nonfinite_value(tmp_path):
    path = tmp_path / "working_point.json"
    save_target_workspace(
        path,
        machine_id="half",
        staged_setpoints=(StagedSetpoint("QL01", "K1", 1.0, "manual"),),
    )
    with pytest.raises(ValueError, match="does not match"):
        load_target_workspace(path, expected_machine_id="irfel")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["setpoints"][0]["target_value"] = "nan"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="must be finite"):
        load_target_workspace(path, expected_machine_id="half")


class FakeTwissBackend:
    line_name = "ALL_MAIN"

    def get_line_endpoints(self):
        return "START", "END"

    def get_optics_profile(self, first, last, *, lattice_overrides=None, twiss_only=False):
        assert twiss_only
        offset = 0.0 if not lattice_overrides else sum(
            value for fields in lattice_overrides.values() for value in fields.values()
        )
        return (
            {
                "element_name": "START", "element_occurrence": 0, "s_m": 0.0,
                "beta_x_m": 2.0 + offset, "beta_y_m": 3.0 + offset,
                "dx_m": 0.1 + offset, "dy_m": 0.2 + offset,
            },
            {
                "element_name": "END", "element_occurrence": 0, "s_m": 5.0,
                "beta_x_m": 4.0 + offset, "beta_y_m": 6.0 + offset,
                "dx_m": 0.3 + offset, "dy_m": 0.4 + offset,
            },
        )


def test_twiss_preview_compares_design_and_target_overrides():
    result = run_twiss_preview(
        FakeTwissBackend(),
        overrides={"QL01": {"K1": 0.5}},
        machine_id="half",
    )
    assert result.line_name == "ALL_MAIN"
    assert len(result.rows) == 2
    assert result.rows[-1].target["beta_x_m"] == pytest.approx(4.5)
    assert result.rows[-1].target["beta_x_m"] - result.rows[-1].design["beta_x_m"] == pytest.approx(0.5)
    assert result.max_delta_eta_y == pytest.approx(0.5)


def test_twiss_preview_requires_staged_overrides():
    with pytest.raises(ValueError, match="at least one"):
        run_twiss_preview(FakeTwissBackend(), overrides={})


def test_twiss_preview_dialog_renders_overview_and_data_tabs():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt5")
    pytest.importorskip("matplotlib")
    from PyQt5.QtWidgets import QApplication
    from half_linac.src.apps.setpoint_transfer.main import TwissPreviewDialog
    from half_linac.src.shared.twiss_preview import TwissPreviewResult, TwissPreviewRow

    rows = (
        TwissPreviewRow(
            "START", 0.0,
            {"beta_x_m": 2.0, "beta_y_m": 3.0, "dx_m": 0.1, "dy_m": 0.0},
            {"beta_x_m": 2.1, "beta_y_m": 2.9, "dx_m": 0.11, "dy_m": 0.0},
        ),
        TwissPreviewRow(
            "QL01", 1.0,
            {"beta_x_m": 3.0, "beta_y_m": 4.0, "dx_m": 0.2, "dy_m": 0.01},
            {"beta_x_m": 3.2, "beta_y_m": 3.8, "dx_m": 0.23, "dy_m": 0.02},
        ),
    )
    result = TwissPreviewResult("half", "ALL_MAIN", {"QL01": {"K1": 6.35}}, rows)
    app = QApplication.instance() or QApplication([])
    dialog = TwissPreviewDialog(result)
    dialog.show()
    app.processEvents()

    assert [dialog.tabs.tabText(index) for index in range(dialog.tabs.count())] == ["Overview", "Data"]
    assert len(dialog.figure.axes) == 2
    assert len(dialog.beta_axis.lines) == 5
    assert len(dialog.eta_axis.lines) == 5
    assert dialog.table.rowCount() == 2
    dialog.canvas.draw()
    dialog.close()


def test_plan_clips_target_to_profile_limit():
    profile = load_profile("half")
    quad = profile.get_element("QL01")
    limited = replace(quad, limits={"K1": {"low": -1, "high": 1, "unit": "1/m^2"}})
    profile = replace(
        profile,
        elements=tuple(limited if item.id == "QL01" else item for item in profile.elements),
        _elements_by_id={**profile._elements_by_id, "QL01": limited},
    )
    setpoint = DesignSetpoint("QL01", "quad", "K1", 0.5, Path("design.lte"))
    plan = build_transfer_plan(
        profile,
        (setpoint,),
        current_values={"QL01": 0.0},
        staged_setpoints=(StagedSetpoint("QL01", "K1", 2.0, "manual"),),
    )
    assert plan.items[0].status == "ready"
    assert plan.items[0].target_value == pytest.approx(1.0)
    assert "Clipped from 2 to 1" in plan.items[0].message


class FakeClient:
    def __init__(self, values, *, fail_pv=None, fail_read_pv=None):
        self.values = dict(values)
        self.fail_pv = fail_pv
        self.fail_read_pv = fail_read_pv
        self.writes = []

    def read(self, pv_name):
        if pv_name == self.fail_read_pv:
            raise RuntimeError("read failed")
        return self.values[pv_name]

    def write(self, pv_name, value):
        self.writes.append((pv_name, value))
        if pv_name == self.fail_pv:
            raise RuntimeError("write failed")
        self.values[pv_name] = value


def _ready_plan():
    profile = load_profile("half")
    setpoints = (
        DesignSetpoint("QL01", "quad", "K1", 1.5, Path("design.lte")),
        DesignSetpoint("QL02", "quad", "K1", -2.5, Path("design.lte")),
    )
    return build_transfer_plan(
        profile, setpoints, current_values={"QL01": 1.0, "QL02": -2.0},
        staged_setpoints=(
            StagedSetpoint("QL01", "K1", 1.5, "manual"),
            StagedSetpoint("QL02", "K1", -2.5, "manual"),
        ),
    )


def test_execute_writes_and_verifies_values():
    plan = _ready_plan()
    client = FakeClient({item.pv_name: item.current_value for item in plan.items})
    result = execute_transfer_plan(plan, client)
    assert [item.old_value for item in result] == [1.0, -2.0]
    assert [item.actual_value for item in result] == [1.5, -2.5]


def test_execute_supports_real_plan_with_same_preflight_guards():
    plan = replace(_ready_plan(), target_backend="real")
    client = FakeClient({item.pv_name: item.current_value for item in plan.items})
    result = execute_transfer_plan(plan, client)
    assert [item.actual_value for item in result] == [1.5, -2.5]


def test_restore_reverses_successful_apply_and_verifies_old_values():
    plan = _ready_plan()
    client = FakeClient({item.pv_name: item.current_value for item in plan.items})
    applied = execute_transfer_plan(plan, client)
    restored = execute_restore(applied, client)
    assert [item.element_id for item in restored] == ["QL02", "QL01"]
    assert [item.actual_value for item in restored] == [-2.0, 1.0]


def test_restore_detects_external_change_before_confirmation():
    plan = _ready_plan()
    client = FakeClient({item.pv_name: item.current_value for item in plan.items})
    applied = execute_transfer_plan(plan, client)
    client.values[applied[0].pv_name] = 9.0
    conflicts = find_restore_conflicts(applied, client)
    assert conflicts[0][0] == "QL01"


def test_preflight_reads_all_targets_before_write():
    plan = _ready_plan()
    client = FakeClient({item.pv_name: item.current_value for item in plan.items})
    preflight_transfer_plan(plan, client)
    assert client.writes == []


def test_preflight_failure_prevents_every_write():
    plan = _ready_plan()
    client = FakeClient(
        {item.pv_name: item.current_value for item in plan.items},
        fail_read_pv=plan.items[1].pv_name,
    )
    with pytest.raises(TransferExecutionError, match="preflight failed"):
        execute_transfer_plan(plan, client)
    assert client.writes == []


def test_execution_log_is_jsonl(tmp_path):
    plan = _ready_plan()
    append_execution_log(tmp_path / "setpoints.jsonl", plan, (), error="test")
    record = json.loads((tmp_path / "setpoints.jsonl").read_text(encoding="utf-8"))
    assert record["target_backend"] == "vm"
    assert record["items"][0]["target_value"] == 1.5
    assert record["error"] == "test"
    assert record["applied"] == []
    assert record["not_executed"] == ["QL01", "QL02"]


def test_execute_stops_after_first_failure_and_preserves_completed_results():
    plan = _ready_plan()
    client = FakeClient(
        {item.pv_name: item.current_value for item in plan.items},
        fail_pv=plan.items[1].pv_name,
    )
    with pytest.raises(TransferExecutionError) as exc_info:
        execute_transfer_plan(plan, client)
    assert [item.element_id for item in exc_info.value.completed] == ["QL01"]
    assert exc_info.value.failed_element_id == "QL02"
    assert len(client.writes) == 2


def test_gui_allows_custom_ready_selection(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt5")
    from PyQt5.QtWidgets import QApplication
    from half_linac.src.apps.setpoint_transfer import main

    monkeypatch.setenv("HALF_LINAC_MACHINE", "half")
    monkeypatch.setenv("HALF_LINAC_CONTROL_BACKEND", "vm")
    monkeypatch.setattr(
        main.EpicsPvClient,
        "read_many",
        lambda self, names: [None] + [0.0] * (len(names) - 1),
    )
    monkeypatch.setattr(main.EpicsPvClient, "read", lambda self, name: 0.0)
    app = QApplication.instance() or QApplication([])
    window = main.MachineSetpointsWindow()
    while window.preview_worker.isRunning():
        app.processEvents()
    app.processEvents()

    assert window.theme_toggle_button.text() in {"☀", "☾"}
    assert not window.plan.blockers
    assert window.selection_label.text() == "0 selected / 0 staged"
    assert not window.twiss_button.isEnabled()
    window._clear_selection()
    assert window.selection_label.text() == "0 selected / 0 staged"
    assert not window.apply_button.isEnabled()
    first_element = window.plan.items[0].element_id
    checkbox = window.selection_checkboxes[first_element]
    assert checkbox.isEnabled()
    assert checkbox.property("can_stage")
    checkbox.setChecked(True)
    assert window.selection_label.text() == "1 selected / 0 staged"
    assert not window.apply_button.isEnabled()
    window._load_design()
    assert window.selection_label.text() == "1 selected / 1 staged"
    assert window.apply_button.isEnabled()
    assert window.twiss_button.isEnabled()
    assert len(window._selected_plan().items) == 1
    window.search_input.setText(first_element)
    assert not window.table.isRowHidden(0)
    assert sum(not window.table.isRowHidden(row) for row in range(window.table.rowCount())) == 1
    window._clear_selection()
    window._select_visible(True)
    assert window._checked_ids() == {first_element}
    window.status_filter.setCurrentText("Selected")
    checkbox = window.selection_checkboxes[first_element]
    checkbox.setChecked(False)
    assert window.table.isRowHidden(0)
    window.status_filter.setCurrentText("All")
    checkbox = window.selection_checkboxes[first_element]
    checkbox.setChecked(True)
    window._load_design()
    window._nudge_selected(0.1)
    assert window.plan.items[0].target_value == pytest.approx(window.design_setpoints[0].value + 0.1)
    selected_plan = window._selected_plan()
    assert "QL" in window._write_details(selected_plan)
    assert window._plan_validation_error(selected_plan) == ""
    window.close()


def test_gui_workspace_load_replaces_targets_without_selecting(monkeypatch, tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt5")
    from PyQt5.QtWidgets import QApplication
    from half_linac.src.apps.setpoint_transfer import main

    monkeypatch.setenv("HALF_LINAC_MACHINE", "half")
    monkeypatch.setattr(
        main.EpicsPvClient,
        "read_many",
        lambda self, names: [0.0] * len(names),
    )
    monkeypatch.setattr(main.EpicsPvClient, "read", lambda self, name: 0.0)
    workspace_path = tmp_path / "workspace.json"
    save_target_workspace(
        workspace_path,
        machine_id="half",
        staged_setpoints=(StagedSetpoint("QL01", "K1", 1.25, "manual"),),
    )
    monkeypatch.setattr(
        main.QFileDialog,
        "getOpenFileName",
        lambda *args, **kwargs: (str(workspace_path), ""),
    )

    app = QApplication.instance() or QApplication([])
    window = main.MachineSetpointsWindow()
    while window.preview_worker.isRunning():
        app.processEvents()
    app.processEvents()
    window.selection_checkboxes["QL02"].setChecked(True)
    window._load_design()

    window._load_workspace()
    while window.preview_worker.isRunning():
        app.processEvents()
    app.processEvents()

    assert set(window.staged_values) == {("QL01", "K1")}
    assert window.staged_values[("QL01", "K1")].target_value == 1.25
    assert window._checked_ids() == set()
    assert not window.apply_button.isEnabled()
    window.close()
