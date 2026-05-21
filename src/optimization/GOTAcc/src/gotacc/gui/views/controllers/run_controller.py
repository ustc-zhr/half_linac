from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PyQt5.QtWidgets import QMessageBox

if TYPE_CHECKING:  # pragma: no cover
    from ..main_window import MainWindow

try:
    from ...services.task_service import TaskService
except ImportError:  # pragma: no cover - local script fallback
    import sys

    CURRENT_DIR = Path(__file__).resolve().parent
    GUI_ROOT = CURRENT_DIR.parents[1]
    for path in (GUI_ROOT, GUI_ROOT / "services"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    from task_service import TaskService


class RunController:
    def __init__(self, window: "MainWindow") -> None:
        self.window = window
        self.view = window.view_adapter
        self.completion_presenter = window.run_completion_presenter
        self.preparation_presenter = window.run_preparation_presenter
        self.results_presenter = window.run_results_presenter
        self.presenter = window.run_session_presenter

    def start_run(self) -> None:
        if self.window.state.run.phase == "Running":
            return
        if not self.window.validate_task_silent():
            QMessageBox.warning(
                self.window,
                "Cannot Start",
                "Please fix validation errors before starting the run.",
            )
            return

        self.window.run_session.cleanup_if_idle()
        task = self.view.current_task()
        if not self.view.ensure_machine_ready_for_online(task):
            QMessageBox.warning(
                self.window,
                "Cannot Start",
                "Online EPICS tasks require the machine interface to be connected first.",
            )
            return

        self.presenter.prepare_for_start(objective_dim=self.window.state.objective_dim)
        self.preparation_presenter.prepare_for_start(task)
        self.view.go_to_page(self.window.PAGE_RUN_MONITOR)
        self.view.log_event("Run started.")
        self.window.run_session.start(task, events=self)

    def pause_run(self) -> None:
        if self.window.state.run.phase != "Running" or not self.window.run_session.has_worker():
            return
        self.window.run_session.request_pause()
        self.presenter.mark_paused()
        self.view.log_event("Run paused.")

    def resume_run(self) -> None:
        if self.window.state.run.phase != "Paused" or not self.window.run_session.has_worker():
            return
        self.window.run_session.request_resume()
        self.presenter.mark_running()
        self.view.log_event("Run resumed.")

    def stop_run(self) -> None:
        if self.window.state.run.phase not in {"Running", "Paused"}:
            return
        if self.window.run_session.has_worker():
            self.window.run_session.request_stop()
        self.presenter.mark_stopping()
        self.view.log_event("Run stop requested.")

    def abort_and_restore(self) -> None:
        if self.window.run_session.has_worker():
            self.window.run_session.request_stop()
        self.presenter.mark_aborted()
        self.view.log_warning("Run aborted. Stop signal sent to worker.")
        self.view.log_event("Abort & Restore triggered.")
        if self.window.machine_ui.checkBox_restore.isChecked():
            self.view.log_pv("Worker will restore initial values if runtime restoration is enabled.")
        else:
            self.view.log_pv("Restore-on-abort is disabled for the current task.")

    def restore_initial_to_machine(self) -> None:
        if self.window.state.run.phase in {"Running", "Paused", "Stopping"}:
            QMessageBox.information(
                self.window,
                "Restore Initial",
                "Stop or finish the current run before restoring initial values.",
            )
            return
        if not self.window.state.latest_initial_x:
            QMessageBox.information(
                self.window,
                "Restore Initial",
                "No saved initial knob values are available for the current run.",
            )
            return

        task = self.view.current_task()
        if not self.view.is_online_task(task):
            QMessageBox.information(self.window, "Restore Initial", "Current task is not an online EPICS task.")
            return
        if not self.view.ensure_machine_ready_for_online(task):
            QMessageBox.warning(self.window, "Restore Initial", "Connect the machine before writing setpoints.")
            return
        if self.window.machine_ui.checkBox_confirm.isChecked():
            answer = QMessageBox.question(
                self.window,
                "Restore Initial",
                "Write the saved initial knob values to the machine now?",
            )
            if answer != QMessageBox.Yes:
                return

        try:
            from gotacc.interfaces.factory import build_backend
            from gotacc.runners.optimize import close_backend_if_possible

            task_cfg = TaskService.build_task_config(task)
            backend_task_cfg = TaskService.make_backend_build_ready_config(task_cfg)
            backend = build_backend(backend_task_cfg)
            variable_names = list(task_cfg.backend.kwargs.get("variable_names", []))
            if not variable_names:
                variable_names = list(self.window.state.latest_initial_x.keys())
            missing = [name for name in variable_names if name not in self.window.state.latest_initial_x]
            if missing:
                raise ValueError(f"Saved initial values are missing variable(s): {', '.join(missing)}")
            initial_vector = [float(self.window.state.latest_initial_x[name]) for name in variable_names]
            if not hasattr(backend, "_apply_setpoints"):
                raise TypeError("Current backend does not expose GUI setpoint writing support.")
            backend._apply_setpoints(initial_vector)
        except Exception as exc:
            self.view.log_warning(f"Restore initial failed: {exc}")
            QMessageBox.critical(self.window, "Restore Initial Failed", str(exc))
            return
        finally:
            if "backend" in locals():
                try:
                    close_backend_if_possible(backend)
                except Exception:
                    pass

        self.view.log_pv(f"Initial knob values restored: {self.window.state.latest_initial_x}")
        self.view.log_event("Initial knob values restored to machine.")
        QMessageBox.information(self.window, "Restore Initial", "Saved initial knob values were written to the machine.")

    def set_best_to_machine(self) -> None:
        if not self.window.state.latest_best_x:
            QMessageBox.information(self.window, "Set Best", "No best point is available yet.")
            return

        task = self.view.current_task()
        if not self.view.is_online_task(task):
            QMessageBox.information(self.window, "Set Best", "Current task is not an online EPICS task.")
            return
        if not self.view.ensure_machine_ready_for_online(task):
            QMessageBox.warning(self.window, "Set Best", "Connect the machine before writing setpoints.")
            return
        if self.window.machine_ui.checkBox_confirm.isChecked():
            answer = QMessageBox.question(
                self.window,
                "Set Best",
                "Write the current best point to the machine now?",
            )
            if answer != QMessageBox.Yes:
                return

        try:
            from gotacc.interfaces.factory import build_backend
            from gotacc.runners.optimize import close_backend_if_possible

            task_cfg = TaskService.build_task_config(task)
            backend_task_cfg = TaskService.make_backend_build_ready_config(task_cfg)
            backend = build_backend(backend_task_cfg)
            variable_names = list(task_cfg.backend.kwargs.get("variable_names", []))
            if not variable_names:
                variable_names = list(self.window.state.latest_best_x.keys())
            best_vector = [float(self.window.state.latest_best_x[name]) for name in variable_names]
            if not hasattr(backend, "_apply_setpoints"):
                raise TypeError("Current backend does not expose GUI setpoint writing support.")
            backend._apply_setpoints(best_vector)
        except Exception as exc:
            self.view.log_warning(f"Set best failed: {exc}")
            QMessageBox.critical(self.window, "Set Best Failed", str(exc))
            return
        finally:
            if "backend" in locals():
                try:
                    close_backend_if_possible(backend)
                except Exception:
                    pass

        best_text = self.window.ui.label_statusBestValue.text()
        self.view.log_pv(f"Best point written to machine: best={best_text}")
        QMessageBox.information(self.window, "Set Best", f"Best point written to machine.\nBest={best_text}")

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

        task = self.window.state.latest_task_snapshot or self.view.current_task()
        if not self.view.is_online_task(task):
            QMessageBox.information(
                self.window,
                "Set Pareto Point",
                "Current task is not an online EPICS task.",
            )
            return
        if not self.view.ensure_machine_ready_for_online(task):
            QMessageBox.warning(
                self.window,
                "Set Pareto Point",
                "Connect the machine before writing setpoints.",
            )
            return
        if self.window.machine_ui.checkBox_confirm.isChecked():
            answer = QMessageBox.question(
                self.window,
                "Set Pareto Point",
                "Write the selected Pareto point to the machine now?",
            )
            if answer != QMessageBox.Yes:
                return

        try:
            from gotacc.interfaces.factory import build_backend
            from gotacc.runners.optimize import close_backend_if_possible

            task_cfg = TaskService.build_task_config(task)
            backend_task_cfg = TaskService.make_backend_build_ready_config(task_cfg)
            backend = build_backend(backend_task_cfg)
            variable_names = list(task_cfg.backend.kwargs.get("variable_names", []))

            x_dict = solution.get("x", {}) if isinstance(solution.get("x", {}), dict) else {}
            x_values = solution.get("x_values", [])
            if variable_names and x_dict:
                missing = [name for name in variable_names if name not in x_dict]
                if missing:
                    raise ValueError(
                        "Selected Pareto point is missing variable(s): "
                        + ", ".join(missing)
                    )
                vector = [float(x_dict[name]) for name in variable_names]
            elif x_values:
                vector = [float(v) for v in x_values]
            else:
                raise ValueError("Selected Pareto point has no writable variable vector.")

            if not hasattr(backend, "_apply_setpoints"):
                raise TypeError("Current backend does not expose GUI setpoint writing support.")
            backend._apply_setpoints(vector)
        except Exception as exc:
            self.view.log_warning(f"Set Pareto point failed: {exc}")
            QMessageBox.critical(self.window, "Set Pareto Point Failed", str(exc))
            return
        finally:
            if "backend" in locals():
                try:
                    close_backend_if_possible(backend)
                except Exception:
                    pass

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
        QMessageBox.critical(self.window, "Worker Error", message)
