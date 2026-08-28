from __future__ import annotations

import copy
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from PyQt5.QtWidgets import QDialog, QMessageBox

if TYPE_CHECKING:  # pragma: no cover
    from ..main_window import MainWindow

try:
    from ...services.task_service import TaskService
    from ..tool_dialogs import MachineWriteConfirmationDialog
except ImportError:  # pragma: no cover - local script fallback
    import sys

    CURRENT_DIR = Path(__file__).resolve().parent
    GUI_ROOT = CURRENT_DIR.parents[1]
    for path in (GUI_ROOT, GUI_ROOT / "services"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    from task_service import TaskService
    from tool_dialogs import MachineWriteConfirmationDialog


class RunController:
    def __init__(self, window: "MainWindow") -> None:
        self.window = window
        self.view = window.view_adapter
        self.completion_presenter = window.run_completion_presenter
        self.preparation_presenter = window.run_preparation_presenter
        self.results_presenter = window.run_results_presenter
        self.presenter = window.run_session_presenter

    def start_run(self) -> None:
        if self.window.state.run.phase in {
            "Running",
            "Stopping",
            "Abort Requested",
            "Restoring",
        }:
            return
        if self.window.run_session.is_running():
            return

        task = copy.deepcopy(self.view.current_task())
        if not self.window.validate_task_silent(task):
            QMessageBox.warning(
                self.window,
                "Cannot Start",
                "Please fix validation errors before starting the run.",
            )
            return

        self.window.run_session.cleanup_if_idle()
        if not self.view.ensure_machine_ready_for_online(task):
            QMessageBox.warning(
                self.window,
                "Cannot Start",
                "Run PV Check for the current Online EPICS task before starting.",
            )
            return

        task = TaskService.prepare_run_archive(task)

        if self.view.is_online_task(task):
            try:
                dialog = MachineWriteConfirmationDialog(
                    task,
                    mode=MachineWriteConfirmationDialog.ONLINE_START,
                    action_title="Authorize Online Run",
                    parent=self.window,
                )
            except Exception as exc:
                self.view.log_warning(f"Online run authorization could not be prepared: {exc}")
                QMessageBox.critical(self.window, "Cannot Start", str(exc))
                return
            if dialog.exec_() != QDialog.Accepted:
                self.view.log_event("Online run authorization cancelled.")
                return
            self.view.log_event("Online run authorized by operator.")

        try:
            TaskService.materialize_run_archive(task)
        except Exception as exc:
            self.view.log_warning(f"Run archive could not be created: {exc}")
            QMessageBox.critical(self.window, "Cannot Start", f"Run archive could not be created:\n{exc}")
            return

        self.presenter.prepare_for_start(objective_dim=self.window.state.objective_dim)
        self.preparation_presenter.prepare_for_start(task)
        self.view.go_to_page(self.window.PAGE_RUN_MONITOR)
        self.view.log_event("Run started.")
        self.window.run_session.start(copy.deepcopy(task), events=self)

    def stop_run(self) -> None:
        if self.window.state.run.phase != "Running":
            return
        if self.window.run_session.has_worker():
            self.window.run_session.request_stop()
        self.presenter.mark_stopping()
        self.view.log_event("Run stop requested.")

    def abort_and_restore(self) -> None:
        if self.window.state.run.phase not in {"Running", "Stopping"}:
            return
        task = self.window.state.latest_task_snapshot or self.view.current_task()
        if not self.view.is_online_task(task):
            self.view.log_event("Offline run uses Stop; no machine state is available to restore.")
            self.stop_run()
            return
        if self.window.run_session.has_worker():
            self.window.run_session.request_abort_restore()
        self.presenter.mark_abort_requested()
        self.view.log_warning("Abort requested. Waiting for the active evaluation to stop.")
        self.view.log_event("Abort & Restore requested.")
        self.view.log_pv("Worker will restore the initial machine state before completing abort.")

    def restore_initial_to_machine(self) -> None:
        if not self.window.state.latest_initial_x:
            QMessageBox.information(
                self.window,
                "Restore Initial",
                "No saved initial knob values are available for the current run.",
            )
            return
        if not self._write_snapshot_values(
            action_title="Restore Initial",
            values=self.window.state.latest_initial_x,
        ):
            return

        self.view.log_pv(f"Initial knob values restored: {self.window.state.latest_initial_x}")
        self.view.log_event("Initial knob values restored to machine.")
        QMessageBox.information(
            self.window,
            "Restore Initial",
            "Saved initial knob values were written to the machine.",
        )

    def set_best_to_machine(self) -> None:
        if not self.window.state.latest_best_x:
            QMessageBox.information(self.window, "Set Best", "No best point is available yet.")
            return
        if not self._write_snapshot_values(
            action_title="Set Best",
            values=self.window.state.latest_best_x,
        ):
            return

        best_text = self.window.ui.label_statusBestValue.text()
        self.view.log_pv(f"Best point written to machine: best={best_text}")
        QMessageBox.information(
            self.window,
            "Set Best",
            f"Best point written to machine.\nBest={best_text}",
        )

    def set_selected_pareto_to_machine(self) -> None:
        solution = self.window.results_controller.selected_pareto_solution()
        if not solution:
            QMessageBox.information(
                self.window,
                "Set Pareto Point",
                "Select a Pareto point first.",
            )
            return
        if not bool(solution.get("feasible", True)):
            QMessageBox.warning(
                self.window,
                "Set Pareto Point",
                "The selected Pareto point is marked infeasible and will not be written.",
            )
            return

        x_dict = solution.get("x")
        values: Any = x_dict if isinstance(x_dict, Mapping) and x_dict else solution.get("x_values")
        if values is None or (isinstance(values, Sequence) and not values):
            QMessageBox.critical(
                self.window,
                "Set Pareto Point Failed",
                "Selected Pareto point has no writable variable vector.",
            )
            return
        if not self._write_snapshot_values(action_title="Set Pareto Point", values=values):
            return

        objective_text = ", ".join(
            f"f{i}={float(v):.6g}" for i, v in enumerate(solution.get("y", []))
        ) or "--"
        self.view.log_pv(
            f"Selected Pareto point written to machine: index={solution.get('index')}, objectives={objective_text}"
        )
        QMessageBox.information(
            self.window,
            "Set Pareto Point",
            f"Selected Pareto point written to machine.\nObjectives={objective_text}",
        )

    def _prepare_snapshot_write(
        self,
        *,
        action_title: str,
        values: Mapping[str, Any] | Sequence[Any],
    ):
        active_phase = self.window.state.run.phase in {
            "Running",
            "Stopping",
            "Abort Requested",
            "Restoring",
        }
        if active_phase or self.window.run_session.is_running():
            QMessageBox.information(
                self.window,
                action_title,
                "Stop or finish the current run before writing setpoints.",
            )
            return None

        state = self.window.state
        if state.viewing_archived_run:
            QMessageBox.warning(
                self.window,
                action_title,
                "Archived runs are read-only. Start the task again before writing to the machine.",
            )
            return None
        if not state.latest_task_snapshot or not state.latest_task_identity:
            QMessageBox.warning(
                self.window,
                action_title,
                "No complete run task snapshot is available. Run the task again before writing setpoints.",
            )
            return None

        try:
            current_identity = TaskService.normalized_task_identity(self.view.current_task())
        except Exception as exc:
            self.view.log_warning(
                f"{action_title} blocked because the current task could not be compared: {exc}"
            )
            current_identity = None
        if current_identity != state.latest_task_identity:
            QMessageBox.warning(
                self.window,
                action_title,
                "The current task configuration has changed since this result was produced. "
                "Restore the original configuration or run the task again before writing setpoints.",
            )
            self.view.log_warning(
                f"{action_title} blocked: current task does not match the run snapshot."
            )
            return None

        task = copy.deepcopy(state.latest_task_snapshot)
        try:
            task_cfg = TaskService.build_task_config(task)
            if task_cfg.backend.type != "epics":
                raise ValueError("The saved run task is not an Online EPICS task.")
            variable_names = list(task_cfg.backend.kwargs.get("variable_names", []))
            if not variable_names:
                raise ValueError("The saved run task has no writable variables.")

            if isinstance(values, Mapping):
                missing = [name for name in variable_names if name not in values]
                extras = [str(name) for name in values if name not in variable_names]
                if missing or extras:
                    details = []
                    if missing:
                        details.append(f"missing: {', '.join(missing)}")
                    if extras:
                        details.append(f"unexpected: {', '.join(extras)}")
                    raise ValueError(
                        "Writable values do not match the run variables ("
                        + "; ".join(details)
                        + ")."
                    )
                vector = [float(values[name]) for name in variable_names]
            else:
                vector = [float(value) for value in values]
                if len(vector) != len(variable_names):
                    raise ValueError(
                        f"Writable value count {len(vector)} does not match variable count {len(variable_names)}."
                    )
            if not all(math.isfinite(value) for value in vector):
                raise ValueError("Writable values must all be finite numbers.")
            exact_values = dict(zip(variable_names, vector))
        except Exception as exc:
            self.view.log_warning(f"{action_title} preparation failed: {exc}")
            QMessageBox.critical(self.window, f"{action_title} Failed", str(exc))
            return None

        if not self.view.ensure_machine_ready_for_online(task):
            QMessageBox.warning(
                self.window,
                action_title,
                "Connect the machine before writing setpoints.",
            )
            return None

        try:
            dialog = MachineWriteConfirmationDialog(
                task,
                mode=MachineWriteConfirmationDialog.EXACT_VALUES,
                action_title=action_title,
                values=exact_values,
                parent=self.window,
            )
        except Exception as exc:
            self.view.log_warning(f"{action_title} confirmation could not be prepared: {exc}")
            QMessageBox.critical(self.window, f"{action_title} Failed", str(exc))
            return None
        if dialog.exec_() != QDialog.Accepted:
            self.view.log_event(f"{action_title} cancelled by operator.")
            return None
        return task_cfg, vector

    def _write_snapshot_values(
        self,
        *,
        action_title: str,
        values: Mapping[str, Any] | Sequence[Any],
    ) -> bool:
        prepared = self._prepare_snapshot_write(action_title=action_title, values=values)
        if prepared is None:
            return False
        task_cfg, vector = prepared

        backend = None
        try:
            from gotacc.interfaces.factory import build_backend
            from gotacc.runners.task_runner import close_backend_if_possible

            backend_task_cfg = TaskService.make_backend_build_ready_config(task_cfg)
            backend = build_backend(backend_task_cfg)
            if not hasattr(backend, "_apply_setpoints"):
                raise TypeError("Current backend does not expose GUI setpoint writing support.")
            backend._apply_setpoints(vector)
        except Exception as exc:
            self.view.log_warning(f"{action_title} failed: {exc}")
            QMessageBox.critical(self.window, f"{action_title} Failed", str(exc))
            return False
        finally:
            if backend is not None:
                try:
                    close_backend_if_possible(backend)
                except Exception:
                    pass
        return True

    def on_session_log(self, message: str) -> None:
        self.view.log_event(message)

    def on_session_warning(self, message: str) -> None:
        self.view.log_warning(message)
        self.view.log_event(message)

    def on_session_status(self, payload: dict) -> None:
        self.presenter.apply_status_payload(payload)

    def on_session_evaluation(self, payload: dict) -> None:
        self.presenter.apply_evaluation_payload(
            payload,
            max_evaluations=self.window.task_ui.spinBox_maxEval.value(),
        )
        self.results_presenter.apply_evaluation_payload(payload)

    def on_session_finished(self, payload: dict) -> None:
        self.completion_presenter.apply_finished_payload(payload)

    def on_session_error(self, message: str) -> None:
        self.presenter.mark_error()
        self.view.log_warning(f"Worker error: {message}")
        self.view.log_event(f"Worker error: {message}")
        payload = {
            "state": "Error",
            "elapsed_seconds": self.window.state.run.elapsed_seconds,
            "eval_count": self.window.state.run.eval_count,
            "best_value": self.window.state.run.best_value,
            "best_x": self.window.state.latest_best_x,
            "error": message,
        }
        try:
            self.view.update_results_after_finish(payload)
            self.view.redraw_plots()
            self.window.results_controller.save_result_images()
            self.window.results_controller.save_run_summary()
        except Exception as exc:
            self.view.log_warning(f"Failed run archive finalization failed: {exc}")
        QMessageBox.critical(self.window, "Worker Error", message)
