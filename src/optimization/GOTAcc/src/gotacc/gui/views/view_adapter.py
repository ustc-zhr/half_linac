from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from .main_window import MainWindow


class GuiViewAdapter:
    def __init__(self, window: "MainWindow") -> None:
        self.window = window

    def current_task(self) -> dict[str, Any]:
        return self.window._current_task()

    def add_table_row(self, table, values=None) -> int:
        return self.window._add_table_row(table, values)

    def remove_selected_table_row(self, table) -> None:
        self.window._remove_selected_table_row(table)

    def set_table_row(self, table, row: int, values) -> None:
        self.window._set_table_row(table, row, values)

    def qobj_alive(self, obj) -> bool:
        return self.window._qobj_alive(obj)

    def living_tables(self, *tables):
        return self.window._living_tables(*tables)

    def log_console(self, message: str) -> None:
        self.window._log_console(message)

    def log_warning(self, message: str) -> None:
        self.window._log_warning(message)

    def log_pv(self, message: str) -> None:
        self.window._log_pv(message)

    def log_event(self, message: str) -> None:
        self.window._log_event(message)

    def go_to_page(self, page_index: int) -> None:
        self.window.go_to_page(page_index)

    def refresh_task_preview(self) -> None:
        self.window._refresh_task_preview()

    def apply_task_payload(self, task: dict, *, source_label: str | None = None, goto_builder: bool = True) -> None:
        self.window._apply_task_payload(task, source_label=source_label, goto_builder=goto_builder)

    def status_message(self, text: str, timeout_ms: int = 0) -> None:
        self.window.statusBar().showMessage(text, timeout_ms)

    def append_overview_activity(
        self,
        event: str,
        *,
        status: str,
        task: str | None = None,
        mode: str | None = None,
        algorithm: str | None = None,
    ) -> None:
        self.window._append_overview_activity(
            event,
            status=status,
            task=task,
            mode=mode,
            algorithm=algorithm,
        )

    def refresh_overview_readiness(self) -> None:
        self.window._refresh_overview_readiness()

    def reset_plot_data(self) -> None:
        self.window._reset_plot_data()

    def redraw_plots(self) -> None:
        self.window._redraw_plots()

    def draw_variable_trajectories(self) -> None:
        self.window._draw_variable_trajectories()

    def populate_history_table(self) -> None:
        self.window._populate_history_table()

    def append_recent_eval(self, payload: dict) -> None:
        self.window._append_recent_eval(payload)

    def clear_recent_evaluations(self) -> None:
        recent_table = self.window.run_ui.tableWidget_recent
        if self.qobj_alive(recent_table):
            recent_table.setRowCount(0)
        results_table = getattr(self.window.ui, "tableWidget_recentEvaluations", None)
        if self.qobj_alive(results_table):
            results_table.setRowCount(0)

    def clear_run_events(self) -> None:
        self.window.run_ui.plainTextEdit_events.clear()

    def update_runtime_labels(self) -> None:
        self.window._update_runtime_labels()

    def set_run_buttons_enabled(self, *, start: bool, pause: bool, resume: bool, stop: bool) -> None:
        self.window._set_run_buttons_enabled(start=start, pause=pause, resume=resume, stop=stop)

    def set_run_phase(self, text: str) -> None:
        self.window._set_run_phase(text)

    def set_run_progress(self, value: int) -> None:
        self.window.ui.progressBar_run.setValue(int(value))

    def sync_status_panels(self) -> None:
        self.window._sync_status_panels()

    def append_run_history(self, status: str) -> None:
        self.window._append_run_history(status)

    def update_results_after_start(self, task: dict) -> None:
        self.window._update_results_after_start(task)

    def update_results_after_evaluation(self, payload: dict) -> None:
        self.window._update_results_after_evaluation(payload)

    def update_results_after_finish(self, payload: dict) -> None:
        self.window._update_results_after_finish(payload)

    def ensure_machine_ready_for_online(self, task: dict) -> bool:
        return self.window._ensure_machine_ready_for_online(task)

    def is_online_task(self, task: dict | None = None) -> bool:
        return self.window._is_online_task(task)
