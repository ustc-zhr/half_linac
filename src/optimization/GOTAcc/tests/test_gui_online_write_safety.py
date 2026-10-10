import copy
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt5")

from PyQt5.QtWidgets import QAbstractItemView, QApplication, QDialog
from PyQt5.QtGui import QCloseEvent

from gotacc.gui.services.task_service import TaskService
from gotacc.gui.state import GuiSessionState
from gotacc.gui.views.controllers.run_controller import RunController
from gotacc.gui.views.controllers.results_controller import ResultsController
from gotacc.gui.views.tool_dialogs import MachineWriteConfirmationDialog


def _online_task(tmp_path, *, setpoint_pv="TEST:Q1:SET"):
    return {
        "task_name": "online_safety_test",
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
                "Enable": "Yes",
                "Name": "Q1",
                "Lower": "-2",
                "Upper": "2",
                "Initial": "0.25",
            }
        ],
        "objectives": [
            {
                "Enable": "Yes",
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
            "ca_address": "TEST-NET",
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
                {
                    "Role": "knob",
                    "Name": "Q1",
                    "PV Name": setpoint_pv,
                    "Readback": "TEST:Q1:RB",
                },
                {
                    "Role": "objective",
                    "Name": "Transmission",
                    "PV Name": "TEST:TRANS",
                    "Readback": "",
                },
            ],
        },
    }


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    return app


def test_normalized_identity_is_stable_and_detects_pv_changes(tmp_path):
    task = _online_task(tmp_path)
    legacy_task = copy.deepcopy(task)
    legacy_task["machine"]["confirm_before_write"] = False

    identity = TaskService.normalized_task_identity(task)

    assert identity == TaskService.normalized_task_identity(copy.deepcopy(task))
    assert identity == TaskService.normalized_task_identity(legacy_task)
    changed = _online_task(tmp_path, setpoint_pv="TEST:Q9:SET")
    assert identity != TaskService.normalized_task_identity(changed)
    changed_initial = copy.deepcopy(task)
    changed_initial["variables"][0]["Initial"] = "0.5"
    assert identity != TaskService.normalized_task_identity(changed_initial)
    changed_restore = copy.deepcopy(task)
    changed_restore["machine"]["restore_on_abort"] = False
    assert identity == TaskService.normalized_task_identity(changed_restore)
    assert TaskService.build_task_config(changed_restore).runtime.restore_initial_on_keyboard_interrupt is True


def test_machine_write_dialog_shows_online_write_contract(tmp_path, qapp):
    task = _online_task(tmp_path)
    task["machine"]["restore_on_abort"] = False
    dialog = MachineWriteConfirmationDialog(
        task,
        mode=MachineWriteConfirmationDialog.ONLINE_START,
        action_title="Authorize Online Run",
    )
    try:
        dialog.show()
        qapp.processEvents()
        assert dialog.table.rowCount() == 1
        assert dialog.table.item(0, 0).text() == "Q1"
        assert dialog.table.item(0, 1).text() == "TEST:Q1:SET"
        assert dialog.table.item(0, 2).text() == "TEST:Q1:RB"
        assert dialog.table.item(0, 5).text() == "0.25"
        assert dialog.accept_button.text() == "Start Online Run"
        assert dialog.accept_button.property("primary") is True
        assert not dialog.accept_button.isDefault()
        assert "up to 5" in dialog.label_notice.text()
        assert "Always enabled" in {
            label.text() for label in dialog.findChildren(type(dialog.label_notice))
        }
    finally:
        dialog.close()


def test_results_start_stores_a_deep_task_snapshot(tmp_path):
    task = _online_task(tmp_path)
    window = SimpleNamespace(state=GuiSessionState(), view_adapter=SimpleNamespace())
    controller = ResultsController(window, canvas_class=None)
    controller.populate_pareto_solution_table = lambda: None
    controller.populate_results_tree = lambda: None
    controller.update_results_summary_table = lambda: None

    controller.update_results_after_start(task)
    task["machine"]["mapping"][0]["PV Name"] = "TEST:MUTATED:SET"

    assert window.state.latest_task_snapshot["machine"]["mapping"][0]["PV Name"] == "TEST:Q1:SET"
    assert window.state.latest_task_identity == TaskService.normalized_task_identity(
        window.state.latest_task_snapshot
    )


class _FakeRunSession:
    def __init__(self):
        self.started = []

    def is_running(self):
        return False

    def cleanup_if_idle(self):
        return None

    def start(self, task, *, events):
        self.started.append((task, events))


class _FakeDialog:
    ONLINE_START = MachineWriteConfirmationDialog.ONLINE_START
    EXACT_VALUES = MachineWriteConfirmationDialog.EXACT_VALUES
    result = QDialog.Rejected
    tasks = []
    kwargs = []

    def __init__(self, task, **kwargs):
        self.tasks.append(task)
        self.kwargs.append(kwargs)

    def exec_(self):
        return self.result


def _controller_window(task):
    logs = []
    run_session = _FakeRunSession()
    presenter = SimpleNamespace(prepare_for_start=lambda **_kwargs: logs.append("presenter"))
    preparation = SimpleNamespace(prepare_for_start=lambda _task: logs.append("preparation"))
    view = SimpleNamespace(
        current_task=lambda: task,
        ensure_machine_ready_for_online=lambda _task: True,
        is_online_task=lambda value: value.get("mode") == "Online EPICS",
        go_to_page=lambda _page: None,
        log_event=logs.append,
        log_warning=logs.append,
        log_pv=logs.append,
    )
    window = SimpleNamespace(
        state=GuiSessionState(),
        view_adapter=view,
        run_session=run_session,
        run_session_presenter=presenter,
        run_preparation_presenter=preparation,
        run_completion_presenter=SimpleNamespace(),
        run_results_presenter=SimpleNamespace(),
        validate_task_silent=lambda _task=None: True,
        PAGE_RUN_MONITOR=2,
    )
    return window, logs


def test_online_start_cancel_has_no_run_side_effects(tmp_path, monkeypatch):
    import gotacc.gui.views.controllers.run_controller as run_controller_module

    task = _online_task(tmp_path)
    window, logs = _controller_window(task)
    _FakeDialog.result = QDialog.Rejected
    _FakeDialog.tasks = []
    _FakeDialog.kwargs = []
    monkeypatch.setattr(run_controller_module, "MachineWriteConfirmationDialog", _FakeDialog)

    RunController(window).start_run()

    assert window.state.run.phase == "Idle"
    assert window.state.latest_task_snapshot == {}
    assert window.run_session.started == []
    assert "presenter" not in logs
    assert _FakeDialog.tasks[0] is not task


def test_online_start_accepts_and_passes_the_confirmed_snapshot(tmp_path, monkeypatch):
    import gotacc.gui.views.controllers.run_controller as run_controller_module

    task = _online_task(tmp_path)
    window, _logs = _controller_window(task)
    _FakeDialog.result = QDialog.Accepted
    _FakeDialog.tasks = []
    _FakeDialog.kwargs = []
    monkeypatch.setattr(run_controller_module, "MachineWriteConfirmationDialog", _FakeDialog)

    RunController(window).start_run()

    assert len(window.run_session.started) == 1
    started_task = window.run_session.started[0][0]
    assert started_task == _FakeDialog.tasks[0]
    assert started_task is not _FakeDialog.tasks[0]


def test_offline_start_does_not_request_machine_write_authorization(tmp_path, monkeypatch):
    import gotacc.gui.views.controllers.run_controller as run_controller_module

    task = _online_task(tmp_path)
    task["mode"] = "Offline"
    window, _logs = _controller_window(task)

    class UnexpectedDialog:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("Offline runs must not request machine write authorization.")

    monkeypatch.setattr(run_controller_module, "MachineWriteConfirmationDialog", UnexpectedDialog)

    RunController(window).start_run()

    assert len(window.run_session.started) == 1


@pytest.mark.parametrize("action", ["restore", "best", "evaluation", "pareto"])
def test_changed_pv_blocks_all_result_writes_before_backend_creation(tmp_path, monkeypatch, action):
    import gotacc.gui.views.controllers.run_controller as run_controller_module

    run_task = _online_task(tmp_path)
    current_task = _online_task(tmp_path, setpoint_pv="TEST:Q9:SET")
    window, logs = _controller_window(current_task)
    window.state.latest_task_snapshot = copy.deepcopy(run_task)
    window.state.latest_task_identity = TaskService.normalized_task_identity(run_task)
    window.state.latest_initial_x = {"Q1": 0.25}
    window.state.latest_best_x = {"Q1": 1.5}
    window.results_controller = SimpleNamespace(
        selected_pareto_solution=lambda: {
            "index": 0,
            "feasible": True,
            "x": {"Q1": 1.0},
            "y": [2.0],
        }
    )
    window.results_controller.selected_evaluation_record = lambda: {
        "eval_id": 3,
        "feasible": True,
        "x_values": {"Q1": 1.25},
    }
    window.results_controller.evaluation_is_feasible = lambda record: bool(
        record.get("feasible", True)
    )
    warnings = []
    monkeypatch.setattr(run_controller_module.QMessageBox, "warning", lambda *_args: warnings.append(_args[-1]))

    backend_calls = []
    monkeypatch.setattr("gotacc.interfaces.factory.build_backend", lambda _cfg: backend_calls.append(_cfg))

    controller = RunController(window)
    {
        "restore": controller.restore_initial_to_machine,
        "best": controller.set_best_to_machine,
        "evaluation": controller.set_selected_evaluation_to_machine,
        "pareto": controller.set_selected_pareto_to_machine,
    }[action]()

    assert backend_calls == []
    assert warnings and "configuration has changed" in warnings[0]
    assert any("does not match the run snapshot" in message for message in logs)


class _FakeBackend:
    def __init__(self):
        self.vectors = []
        self.closed = False

    def _apply_setpoints(self, vector):
        self.vectors.append(list(vector))

    def close(self):
        self.closed = True


def test_set_best_requires_confirmation_and_uses_snapshot_mapping(tmp_path, monkeypatch):
    import gotacc.gui.views.controllers.run_controller as run_controller_module

    task = _online_task(tmp_path)
    window, _logs = _controller_window(copy.deepcopy(task))
    window.state.latest_task_snapshot = copy.deepcopy(task)
    window.state.latest_task_identity = TaskService.normalized_task_identity(task)
    window.state.latest_best_x = {"Q1": 1.5}
    window.ui = SimpleNamespace(label_statusBestValue=SimpleNamespace(text=lambda: "2.0"))
    _FakeDialog.result = QDialog.Accepted
    _FakeDialog.tasks = []
    _FakeDialog.kwargs = []
    monkeypatch.setattr(run_controller_module, "MachineWriteConfirmationDialog", _FakeDialog)
    monkeypatch.setattr(run_controller_module.QMessageBox, "information", lambda *_args: None)
    backend = _FakeBackend()
    built_configs = []

    def build_backend(task_cfg):
        built_configs.append(task_cfg)
        return backend

    monkeypatch.setattr("gotacc.interfaces.factory.build_backend", build_backend)

    RunController(window).set_best_to_machine()

    assert backend.vectors == [[1.5]]
    assert backend.closed
    assert built_configs[0].backend.kwargs["knobs_pvnames"] == ["TEST:Q1:SET"]
    assert _FakeDialog.kwargs[0]["values"] == {"Q1": 1.5}
    assert _FakeDialog.kwargs[0]["mode"] == MachineWriteConfirmationDialog.EXACT_VALUES



def test_set_selected_evaluation_requires_confirmation_and_uses_full_history_values(
    tmp_path,
    monkeypatch,
):
    import gotacc.gui.views.controllers.run_controller as run_controller_module

    task = _online_task(tmp_path)
    for index in range(2, 5):
        variable = dict(task["variables"][0], Name=f"Q{index}")
        task["variables"].append(variable)
        task["machine"]["mapping"].append({
            "Role": "knob", "Name": f"Q{index}",
            "PV Name": f"TEST:Q{index}:SET", "Readback": f"TEST:Q{index}:RB",
        })
    task["objective_type"] = "Multi Objective"
    task["algorithm"] = "MOBO"
    task["objectives"].append(dict(task["objectives"][0], Name="Energy"))
    task["machine"]["mapping"].append({
        "Role": "objective", "Name": "Energy", "PV Name": "TEST:ENERGY", "Readback": "",
    })
    window, logs = _controller_window(copy.deepcopy(task))
    window.state.latest_task_snapshot = copy.deepcopy(task)
    window.state.latest_task_identity = TaskService.normalized_task_identity(task)
    values = {"Q4": 0.123456789012345, "Q3": 0.5, "Q2": -1.25, "Q1": -0.75}
    record = {"eval_id": 4, "feasible": True, "x_values": values, "objective_values": [1.0, 2.0]}
    window.results_controller = SimpleNamespace(
        selected_evaluation_record=lambda: record,
        evaluation_is_feasible=lambda value: bool(value.get("feasible", True)),
    )
    _FakeDialog.result = QDialog.Accepted
    _FakeDialog.tasks = []
    _FakeDialog.kwargs = []
    monkeypatch.setattr(run_controller_module, "MachineWriteConfirmationDialog", _FakeDialog)
    monkeypatch.setattr(run_controller_module.QMessageBox, "information", lambda *_args: None)
    backend = _FakeBackend()
    monkeypatch.setattr("gotacc.interfaces.factory.build_backend", lambda _cfg: backend)

    controller = RunController(window)
    controller.set_selected_evaluation_to_machine()

    assert backend.vectors == [[-0.75, -1.25, 0.5, 0.123456789012345]]
    assert backend.closed
    assert _FakeDialog.kwargs[0]["values"] == values
    assert _FakeDialog.kwargs[0]["mode"] == MachineWriteConfirmationDialog.EXACT_VALUES
    assert any("eval_id=4" in message for message in logs)

    record["feasible"] = False
    warnings = []
    monkeypatch.setattr(
        run_controller_module.QMessageBox,
        "warning",
        lambda *_args: warnings.append(_args[-1]),
    )
    controller.set_selected_evaluation_to_machine()
    assert len(backend.vectors) == 1
    assert "infeasible" in warnings[-1]



def test_set_best_cancel_and_nonfinite_values_never_build_backend(tmp_path, monkeypatch):
    import gotacc.gui.views.controllers.run_controller as run_controller_module

    task = _online_task(tmp_path)
    window, _logs = _controller_window(copy.deepcopy(task))
    window.state.latest_task_snapshot = copy.deepcopy(task)
    window.state.latest_task_identity = TaskService.normalized_task_identity(task)
    window.state.latest_best_x = {"Q1": 1.5}
    _FakeDialog.result = QDialog.Rejected
    _FakeDialog.tasks = []
    _FakeDialog.kwargs = []
    monkeypatch.setattr(run_controller_module, "MachineWriteConfirmationDialog", _FakeDialog)
    backend_calls = []
    monkeypatch.setattr("gotacc.interfaces.factory.build_backend", lambda cfg: backend_calls.append(cfg))

    RunController(window).set_best_to_machine()
    assert backend_calls == []
    assert len(_FakeDialog.tasks) == 1

    window.state.latest_best_x = {"Q1": float("nan")}
    critical_messages = []
    monkeypatch.setattr(
        run_controller_module.QMessageBox,
        "critical",
        lambda *_args: critical_messages.append(_args[-1]),
    )
    RunController(window).set_best_to_machine()
    assert backend_calls == []
    assert "finite" in critical_messages[-1]


def test_evaluation_history_selection_controls_machine_write(tmp_path, qapp):
    from gotacc.gui.views.main_window import MainWindow

    task = _online_task(tmp_path)
    window = MainWindow()
    try:
        window.view_adapter.current_task = lambda: task
        window.state.latest_task_snapshot = copy.deepcopy(task)
        window.state.latest_task_identity = TaskService.normalized_task_identity(task)
        window.state.run.phase = "Finished"

        window.results_controller.append_recent_eval(
            {
                "eval_id": 7,
                "timestamp": "12:00:00",
                "status": "ok",
                "x_values": {"Q1": 0.625},
                "objective_value": 1.5,
                "constraint_values": [],
                "feasible": True,
            }
        )
        table = window.run_ui.tableWidget_recent
        table.selectRow(0)
        qapp.processEvents()

        assert table.selectionMode() == QAbstractItemView.SingleSelection
        assert table.selectionBehavior() == QAbstractItemView.SelectRows
        assert window.results_controller.selected_evaluation_record()["x_values"] == {"Q1": 0.625}
        assert window.run_ui.pushButton_setSelectedEvaluation.isEnabled()
        assert window.run_ui.pushButton_setSelectedEvaluation.property("machineWrite") is True
        assert window.ui.tableWidget_solutionInspector.item(0, 0).text() == "Evaluation"
        assert window.ui.tableWidget_solutionInspector.item(1, 1).text() == "Q1=0.625"

        window.state.run.phase = "Running"
        window.runtime_status_controller.sync_run_workspace(task)
        assert not window.run_ui.pushButton_setSelectedEvaluation.isEnabled()

        window.state.run.phase = "Finished"
        window.results_controller.append_recent_eval(
            {
                "eval_id": 8,
                "status": "infeasible",
                "x_values": {"Q1": -0.5},
                "objective_value": 2.0,
                "feasible": False,
            }
        )
        table.selectRow(1)
        qapp.processEvents()
        assert not window.run_ui.pushButton_setSelectedEvaluation.isEnabled()
        assert "infeasible" in window.run_ui.pushButton_setSelectedEvaluation.toolTip()

        window.state.viewing_archived_run = True
        table.selectRow(0)
        qapp.processEvents()
        assert not window.run_ui.pushButton_setSelectedEvaluation.isEnabled()
        assert "read-only" in window.run_ui.pushButton_setSelectedEvaluation.toolTip()
    finally:
        window.close()


@pytest.mark.parametrize("blocked_by", [
    "cancel", "no_selection", "infeasible", "false_string", "failed", "unknown_status",
    "missing", "extra", "nan", "inf", "not_numeric", "archived", "offline",
    "no_snapshot", "Running", "Stopping", "Abort Requested", "Restoring", "thread_running",
])
def test_selected_evaluation_blockers_never_build_backend(tmp_path, monkeypatch, blocked_by):
    import gotacc.gui.views.controllers.run_controller as run_controller_module

    task = _online_task(tmp_path)
    if blocked_by == "offline":
        task["mode"] = "Offline"
        task["test_function"] = "sphere"
    window, logs = _controller_window(task)
    window.state.latest_task_snapshot = copy.deepcopy(task)
    window.state.latest_task_identity = TaskService.normalized_task_identity(task)
    record = {"eval_id": 5, "status": "ok", "feasible": True, "x_values": {"Q1": 0.5}}
    if blocked_by == "no_selection":
        record = None
    elif blocked_by in {"infeasible", "false_string"}:
        record["feasible"] = False if blocked_by == "infeasible" else "false"
    elif blocked_by in {"failed", "unknown_status"}:
        record["status"] = blocked_by
    elif blocked_by == "missing":
        record["x_values"] = {}
    elif blocked_by == "extra":
        record["x_values"]["Q9"] = 1.0
    elif blocked_by in {"nan", "inf", "not_numeric"}:
        record["x_values"]["Q1"] = "bad" if blocked_by == "not_numeric" else float(blocked_by)
    elif blocked_by == "archived":
        window.state.viewing_archived_run = True
    elif blocked_by == "no_snapshot":
        window.state.latest_task_snapshot = {}
    elif blocked_by in {"Running", "Stopping", "Abort Requested", "Restoring"}:
        window.state.run.phase = blocked_by
    elif blocked_by == "thread_running":
        window.run_session.is_running = lambda: True
    window.results_controller = SimpleNamespace(
        selected_evaluation_record=lambda: record,
        evaluation_is_feasible=ResultsController.evaluation_is_feasible,
    )
    _FakeDialog.result = QDialog.Rejected if blocked_by == "cancel" else QDialog.Accepted
    _FakeDialog.tasks = []
    _FakeDialog.kwargs = []
    monkeypatch.setattr(run_controller_module, "MachineWriteConfirmationDialog", _FakeDialog)
    messages = []
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(run_controller_module.QMessageBox, name, lambda *_args: messages.append(_args[-1]))
    backend_calls = []
    monkeypatch.setattr("gotacc.interfaces.factory.build_backend", lambda cfg: backend_calls.append(cfg))

    RunController(window).set_selected_evaluation_to_machine()

    assert backend_calls == []
    assert len(_FakeDialog.tasks) == (1 if blocked_by == "cancel" else 0)
    assert messages or any("cancelled" in message for message in logs)


def test_history_record_keeps_complete_precision_and_selection_after_append(tmp_path, qapp):
    from PyQt5.QtCore import Qt
    from gotacc.gui.views.main_window import MainWindow

    window = MainWindow()
    task = _online_task(tmp_path)
    for index in range(2, 5):
        task["variables"].append(dict(task["variables"][0], Name=f"Q{index}"))
        task["machine"]["mapping"].append({
            "Role": "knob", "Name": f"Q{index}",
            "PV Name": f"TEST:Q{index}:SET", "Readback": f"TEST:Q{index}:RB",
        })
    window.view_adapter.current_task = lambda: task
    window.state.latest_task_snapshot = copy.deepcopy(task)
    window.state.latest_task_identity = TaskService.normalized_task_identity(task)
    values = {"Q1": 0.0, "Q2": 0.5, "Q3": -0.5, "Q4": 0.123456789012345}
    record = {
        "eval_id": 7, "status": "ok", "feasible": True, "x_values": copy.deepcopy(values),
        "objective_value": 0.0, "objective_summary": "rounded",
        "constraint_values": [0.123456789012345], "constraint_summary": "rounded",
    }
    try:
        window.results_controller.append_recent_eval(record)
        record["x_values"]["Q4"] = 9.0
        table = window.run_ui.tableWidget_recent
        table.selectRow(0)
        assert window.results_controller.selected_evaluation_record()["x_values"] == values
        details = window.run_ui.plainTextEdit_selectedEvaluation
        assert details.isReadOnly()
        assert "Q4=0.123456789012345" in details.toPlainText()
        assert "Objective: Transmission [mean] = 0" in details.toPlainText()
        assert "Constraints: [0.123456789012345]" in details.toPlainText()
        assert "Q4" not in table.item(0, 3).text()
        assert window.run_ui.pushButton_setSelectedEvaluation.isEnabled()

        window.results_controller.append_recent_eval(dict(record, eval_id=8))
        assert window.results_controller.selected_evaluation_record()["eval_id"] == 7
        assert "Q4=0.123456789012345" in details.toPlainText()
        table.sortItems(0, Qt.DescendingOrder)
        assert window.results_controller.selected_evaluation_record()["eval_id"] == 7

        for invalid in ({"Q1": 0.5}, dict(values, Q9=1.0), dict(values, Q4=float("nan"))):
            window.results_controller.append_recent_eval(dict(record, x_values=invalid))
            table.selectRow(table.rowCount() - 1)
            assert not window.run_ui.pushButton_setSelectedEvaluation.isEnabled()
        window.view_adapter.clear_recent_evaluations()
        assert details.toPlainText() == ""
        assert window.results_controller.selected_evaluation_record() is None
        assert not window.run_ui.pushButton_setSelectedEvaluation.isEnabled()
    finally:
        window.close()


def test_active_run_close_is_deferred_and_restore_failure_keeps_window_open(
    tmp_path,
    qapp,
    monkeypatch,
):
    from gotacc.gui.views.main_window import MainWindow
    import gotacc.gui.views.main_window as main_window_module

    window = MainWindow()
    task = _online_task(tmp_path)
    task["machine"]["restore_on_abort"] = False
    window.state.latest_task_snapshot = copy.deepcopy(task)
    window.state.run.phase = "Running"
    window.runtime_status_controller.sync_run_workspace(task)
    assert window.run_ui.pushButton_abortRestore.text() == "Abort && Restore"

    window.state.run.phase = "Abort Requested"
    window.runtime_status_controller.sync_run_workspace(task)
    assert window.run_ui.pushButton_abortRestore.text() == "Abort Requested"
    assert not window.run_ui.pushButton_abortRestore.isEnabled()

    window.state.run.phase = "Restoring"
    window.runtime_status_controller.sync_run_workspace(task)
    assert window.run_ui.pushButton_abortRestore.text() == "Restoring..."

    class FakeActiveSession:
        running = True

        def is_running(self):
            return self.running

    session = FakeActiveSession()
    window.run_session = session
    abort_calls = []
    window.run_controller = SimpleNamespace(abort_and_restore=lambda: abort_calls.append(True))
    window.state.run.phase = "Running"

    window._confirm_close_active_run = lambda: False
    cancelled_event = QCloseEvent()
    window.closeEvent(cancelled_event)
    assert not cancelled_event.isAccepted()
    assert not window._close_when_run_finishes
    assert abort_calls == []

    window._confirm_close_active_run = lambda: True
    approved_event = QCloseEvent()
    window.closeEvent(approved_event)
    assert not approved_event.isAccepted()
    assert window._close_when_run_finishes
    assert abort_calls == [True]

    critical_messages = []
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "critical",
        lambda *_args: critical_messages.append(_args[-1]),
    )
    session.running = False
    window.state.run.phase = "Restore Failed"
    window._complete_deferred_close()
    assert not window._close_when_run_finishes
    assert critical_messages and "Review the machine state" in critical_messages[-1]
    window.close()
