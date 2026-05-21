from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ..main_window import MainWindow


class RunSessionPresenter:
    def __init__(self, window: "MainWindow") -> None:
        self.window = window
        self.view = window.view_adapter

    def prepare_for_start(self, *, objective_dim: int) -> None:
        self.window.state.reset_for_run_start(objective_dim)
        self.view.update_runtime_labels()
        self.view.set_run_buttons_enabled(start=False, pause=True, resume=False, stop=True)
        self.view.set_run_phase("Running")
        self.view.set_run_progress(0)
        self.view.sync_status_panels()

    def mark_paused(self) -> None:
        self.window.state.run.phase = "Paused"
        self.view.set_run_buttons_enabled(start=False, pause=False, resume=True, stop=True)
        self.view.set_run_phase("Paused")
        self.view.sync_status_panels()

    def mark_running(self) -> None:
        self.window.state.run.phase = "Running"
        self.view.set_run_buttons_enabled(start=False, pause=True, resume=False, stop=True)
        self.view.set_run_phase("Running")
        self.view.sync_status_panels()

    def mark_stopping(self) -> None:
        self.window.state.run.phase = "Stopping"
        self.view.set_run_phase("Stopping")
        self.view.sync_status_panels()

    def mark_aborted(self) -> None:
        self.window.state.run.phase = "Aborted"
        self.view.set_run_phase("Aborted")
        self.view.set_run_buttons_enabled(start=True, pause=False, resume=False, stop=False)
        self.view.sync_status_panels()

    def mark_error(self) -> None:
        self.window.state.run.phase = "Error"
        self.view.set_run_buttons_enabled(start=True, pause=False, resume=False, stop=False)
        self.view.set_run_phase("Error")
        self.view.sync_status_panels()

    def apply_status_payload(self, payload: dict) -> None:
        run = self.window.state.run
        state = str(payload.get("state", run.phase))
        run.elapsed_seconds = int(payload.get("elapsed_seconds", run.elapsed_seconds) or 0)
        run.eval_count = int(payload.get("eval_count", run.eval_count) or 0)
        best = payload.get("best_value", run.best_value)
        run.best_value = float(best) if best is not None else run.best_value
        feas = payload.get("feasibility_ratio", run.feasibility_ratio)
        if feas is not None:
            run.feasibility_ratio = float(feas)
        initial_x = payload.get("initial_x")
        if isinstance(initial_x, dict) and initial_x:
            self.window.state.latest_initial_x = dict(initial_x)

        if state in {"Running", "Paused"}:
            run.phase = state
        self.view.update_runtime_labels()
        self.view.set_run_phase(run.phase)

    def apply_evaluation_payload(self, payload: dict, *, max_evaluations: int) -> None:
        run = self.window.state.run
        run.eval_count = int(payload.get("eval_id", run.eval_count) or 0)
        best = payload.get("best_value", run.best_value)
        run.best_value = float(best) if best is not None else run.best_value
        feas = payload.get("feasibility_ratio", run.feasibility_ratio)
        run.feasibility_ratio = float(feas) if feas is not None else run.feasibility_ratio

        self.view.update_runtime_labels()
        capped_budget = max(1, int(max_evaluations))
        progress = int(min(100, round(100 * run.eval_count / capped_budget)))
        self.view.set_run_progress(progress)

    def apply_finished_payload(self, payload: dict) -> str:
        run = self.window.state.run
        run.elapsed_seconds = int(payload.get("elapsed_seconds", run.elapsed_seconds) or 0)
        run.eval_count = int(payload.get("eval_count", run.eval_count) or 0)
        best = payload.get("best_value", run.best_value)
        run.best_value = float(best) if best is not None else run.best_value
        run.phase = str(payload.get("state", "Finished"))

        self.view.update_runtime_labels()
        self.view.set_run_phase(run.phase)
        self.view.set_run_buttons_enabled(start=True, pause=False, resume=False, stop=False)
        if run.phase == "Finished":
            self.view.set_run_progress(100)
        self.view.append_run_history(run.phase)
        return run.phase
