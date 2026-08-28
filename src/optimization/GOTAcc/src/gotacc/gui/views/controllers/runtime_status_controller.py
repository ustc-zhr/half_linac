from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from ..main_window import MainWindow


class RuntimeStatusController:
    ACTIVE_PHASES = {"Running", "Stopping", "Abort Requested", "Restoring"}

    def __init__(self, window: "MainWindow") -> None:
        self.window = window
        self.view = window.view_adapter

    def update_runtime_labels(self) -> None:
        run = self.window.state.run
        hh = run.elapsed_seconds // 3600
        mm = (run.elapsed_seconds % 3600) // 60
        ss = run.elapsed_seconds % 60
        self.window.run_ui.label_elapsedValue.setText(f"{hh:02d}:{mm:02d}:{ss:02d}")
        self.update_evaluation_label()
        task = self._task_for_run_workspace()
        multi = self._is_multi_objective_task(task)
        self.window.run_ui.label_bestTitle.setText("Hypervolume" if multi else "Best Objective")
        if multi:
            hv = self.window.state.hypervolume_history
            self.window.run_ui.label_bestValue.setText("--" if not hv else f"{hv[-1]:.6g}")
        else:
            self.window.run_ui.label_bestValue.setText(
                "--" if run.best_value is None else f"{run.best_value:.6f}"
            )
        self.window.run_ui.label_feasibilityValue.setText(f"{run.feasibility_ratio:.2f}")
        self.window.ui.label_statusBestValue.setText(
            "--" if run.best_value is None else f"{run.best_value:.6f}"
        )
        if hasattr(self.window.run_ui, "label_runSummary"):
            best_text = "--" if run.best_value is None else f"{run.best_value:.6f}"
            self.window.run_ui.label_runSummary.setText(
                f"{run.phase} · {run.eval_count} evaluation(s) · best {best_text} · feasibility {run.feasibility_ratio:.2f}"
            )
        self.sync_status_panels()

    def update_evaluation_label(self) -> None:
        state = self.window.state
        task = state.latest_task_snapshot
        if not task:
            try:
                task = self.view.current_task()
            except Exception:
                task = {}
        try:
            budget = int(task.get("max_evaluations", 0) or 0)
        except (TypeError, ValueError):
            budget = 0
        budget_text = str(budget) if budget > 0 else "--"
        self.window.run_ui.label_evalValue.setText(f"{state.run.eval_count}/{budget_text}")

    def set_run_buttons_enabled(self, *, start: bool, stop: bool) -> None:
        self.window.ui.pushButton_startRun.setEnabled(start)
        self.window.ui.pushButton_stopRun.setEnabled(stop)
        self.window.run_ui.pushButton_start.setEnabled(start)
        self.window.run_ui.pushButton_stop.setEnabled(stop)
        self.window.run_ui.pushButton_abortRestore.setEnabled(not start)
        if hasattr(self.window.run_ui, "pushButton_restoreInitial"):
            self.window.run_ui.pushButton_restoreInitial.setEnabled(
                bool(start and self.window.state.latest_initial_x)
            )
        self.window.run_ui.pushButton_setBest.setEnabled(self.window.state.run.best_value is not None)
        self._sync_run_action_visibility()

    def set_run_phase(self, text: str) -> None:
        self.window.run_ui.label_phaseValue.setText(text)
        self._sync_phase_tone(text)
        self._sync_run_action_visibility()
        self.window._sync_workspace_status()
        self.window._refresh_overview_cards()
        self.window.results_controller.refresh_result_source()

    def append_run_history(self, status: str) -> None:
        task_name = self.window.task_ui.lineEdit_taskName.text().strip() or "untitled_task"
        mode = self.window.task_ui.comboBox_mode.currentText()
        algorithm = self.window.task_ui.comboBox_algorithm.currentText()
        row = self.window.ui.tableWidget_runHistory.rowCount()
        self.window.ui.tableWidget_runHistory.insertRow(row)
        values = [
            str(row + 1),
            task_name,
            mode,
            algorithm,
            status,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ]
        self.view.set_table_row(self.window.ui.tableWidget_runHistory, row, values)
        self.view.append_overview_activity(
            "Run",
            task=task_name,
            mode=mode,
            algorithm=algorithm,
            status=status,
        )

    def sync_status_panels(self) -> None:
        run = self.window.state.run
        self._sync_phase_tone(run.phase)
        self.window.ui.label_statusBestValue.setText(
            "--" if run.best_value is None else f"{run.best_value:.6f}"
        )
        if hasattr(self.window.run_ui, "label_runSummary"):
            best_text = "--" if run.best_value is None else f"{run.best_value:.6f}"
            self.window.run_ui.label_runSummary.setText(
                f"{run.phase} · {run.eval_count} evaluation(s) · best {best_text} · feasibility {run.feasibility_ratio:.2f}"
            )
        self.window.run_ui.pushButton_setBest.setEnabled(run.best_value is not None)
        if hasattr(self.window.run_ui, "pushButton_restoreInitial"):
            self.window.run_ui.pushButton_restoreInitial.setEnabled(
                bool(run.phase not in {"Running", "Stopping"} and self.window.state.latest_initial_x)
            )
        self.sync_run_workspace()
        self.window._sync_workspace_status()
        self.window._refresh_overview_cards()
        self.window.results_controller.refresh_result_source()

    def _sync_phase_tone(self, phase: str) -> None:
        tone = {
            "Running": "success",
            "Completed": "success",
            "Stopping": "warning",
            "Restoring": "warning",
            "Abort Requested": "danger",
            "Failed": "danger",
        }.get(str(phase), "subtle")
        frame = self.window.run_ui.frame_phase
        if frame.property("tone") == tone:
            return
        frame.setProperty("tone", tone)
        frame.style().unpolish(frame)
        frame.style().polish(frame)

    def sync_run_workspace(self, task: dict[str, Any] | None = None) -> None:
        """Keep Run page controls and plot tabs aligned with the active task."""
        task = self._task_for_run_workspace(task)
        self._sync_run_action_visibility(task)
        self._sync_plot_tab_visibility(task)

    def _task_for_run_workspace(self, task: dict[str, Any] | None = None) -> dict[str, Any]:
        if task is not None:
            return task
        state = self.window.state
        if state.run.phase in self.ACTIVE_PHASES and state.latest_task_snapshot:
            return state.latest_task_snapshot
        try:
            return self.view.current_task()
        except Exception:
            return state.latest_task_snapshot or {}

    def _sync_run_action_visibility(self, task: dict[str, Any] | None = None) -> None:
        task = self._task_for_run_workspace(task)
        run = self.window.state.run
        phase = run.phase
        phase_active = phase in self.ACTIVE_PHASES
        thread_active = self.window.run_session.is_running()
        active = phase_active or thread_active
        running = phase == "Running"
        stopping = phase == "Stopping"

        start_visible = not active
        stop_visible = running
        online_task = self._is_online_task(task)
        multi_objective = self._is_multi_objective_task(task)
        archived = self.window.state.viewing_archived_run
        abort_visible = phase_active and online_task
        abort_enabled = online_task and (running or stopping)
        restore_visible = (
            not active
            and online_task
            and not archived
            and bool(self.window.state.latest_initial_x)
        )
        set_best_visible = (
            not active
            and online_task
            and not archived
            and not multi_objective
            and bool(self.window.state.latest_best_x)
        )

        primary_actions_in_sidebar = bool(getattr(self.window, "_run_primary_actions_in_sidebar", False))
        action_states = (
            (self.window.run_ui.pushButton_start, start_visible and not primary_actions_in_sidebar, start_visible),
            (self.window.run_ui.pushButton_stop, stop_visible, stop_visible),
            (self.window.run_ui.pushButton_abortRestore, abort_visible, abort_enabled),
            (
                self.window.run_ui.pushButton_restoreInitial,
                restore_visible,
                restore_visible,
            ),
            (
                self.window.run_ui.pushButton_setBest,
                set_best_visible,
                set_best_visible,
            ),
        )
        for button, visible, enabled in action_states:
            button.setVisible(visible)
            button.setEnabled(enabled)
        if phase == "Restoring":
            abort_text = "Restoring..."
        elif phase == "Abort Requested":
            abort_text = "Abort Requested"
        else:
            abort_text = "Abort && Restore"
        self.window.run_ui.pushButton_abortRestore.setText(abort_text)
        self.window.run_ui.groupBox_actions.setVisible(False)

    def _sync_plot_tab_visibility(self, task: dict[str, Any]) -> None:
        has_constraints = bool(self._enabled_rows(task.get("constraints", [])))
        is_multi_objective = self._is_multi_objective_task(task)
        objective_index = self.window.run_ui.tabWidget_plots.indexOf(self.window.run_ui.tab_obj)
        if objective_index >= 0:
            self.window.run_ui.tabWidget_plots.setTabText(
                objective_index, "Hypervolume" if is_multi_objective else "Objective"
            )
        self.window.run_ui.label_bestTitle.setText(
            "Hypervolume" if is_multi_objective else "Best Objective"
        )

        self._set_tab_visible(
            self.window.run_ui.tabWidget_plots,
            self.window.run_ui.tab_constraints,
            has_constraints,
        )
        self._set_tab_visible(
            self.window.run_ui.tabWidget_plots,
            self.window.run_ui.tab_pareto,
            is_multi_objective,
        )
        if hasattr(self.window.ui, "tabWidget_resultsViews") and hasattr(self.window.ui, "tab_pareto"):
            self._set_tab_visible(
                self.window.ui.tabWidget_resultsViews,
                self.window.ui.tab_pareto,
                is_multi_objective,
            )

    def _set_tab_visible(self, tab_widget, page, visible: bool) -> None:
        index = tab_widget.indexOf(page)
        if index < 0:
            return
        if hasattr(tab_widget, "setTabVisible"):
            tab_widget.setTabVisible(index, visible)
        else:  # pragma: no cover - compatibility with older Qt bindings
            tab_widget.setTabEnabled(index, visible)
        if visible or tab_widget.currentIndex() != index:
            return
        for candidate in range(tab_widget.count()):
            if candidate == index:
                continue
            if self._tab_is_visible(tab_widget, candidate):
                tab_widget.setCurrentIndex(candidate)
                return

    def _tab_is_visible(self, tab_widget, index: int) -> bool:
        if hasattr(tab_widget, "isTabVisible"):
            return bool(tab_widget.isTabVisible(index))
        return bool(tab_widget.isTabEnabled(index))

    def _is_online_task(self, task: dict[str, Any] | None = None) -> bool:
        current = self._task_for_run_workspace(task)
        return str(current.get("mode", "")).strip().lower() == "online epics"

    def _is_multi_objective_task(self, task: dict[str, Any]) -> bool:
        objective_type = str(task.get("objective_type", "")).strip().lower()
        algorithm = self._normalize_algorithm(task.get("algorithm", ""))
        return (
            objective_type == "multi objective"
            or self.window.state.objective_dim > 1
            or algorithm in {"mobo", "consmobo", "mggpo", "consmggpo", "mopso", "nsgaii"}
        )

    @staticmethod
    def _normalize_algorithm(value: Any) -> str:
        return "".join(ch for ch in str(value).strip().lower() if ch.isalnum())

    @staticmethod
    def _enabled_rows(rows: Any) -> list[dict[str, Any]]:
        enabled: list[dict[str, Any]] = []
        if not isinstance(rows, list):
            return enabled
        for row in rows:
            if not isinstance(row, dict):
                continue
            text = str(row.get("Enable", "")).strip().lower()
            if text in {"y", "yes", "true", "1", "on", "enabled"}:
                enabled.append(row)
        return enabled
