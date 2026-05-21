from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class RunState:
    phase: str = "Idle"
    elapsed_seconds: int = 0
    eval_count: int = 0
    best_value: Optional[float] = None
    feasibility_ratio: float = 1.0

    def reset_for_start(self) -> None:
        self.phase = "Running"
        self.elapsed_seconds = 0
        self.eval_count = 0
        self.best_value = None
        self.feasibility_ratio = 1.0


@dataclass
class GuiSessionState:
    run: RunState = field(default_factory=RunState)

    objective_history: list[float] = field(default_factory=list)
    eval_history: list[tuple[dict[str, Any], Any, Any]] = field(default_factory=list)
    eval_x_history: list[list[float]] = field(default_factory=list)
    eval_y_history: list[Any] = field(default_factory=list)
    best_history: list[float] = field(default_factory=list)
    pareto_points: list[tuple[float, float]] = field(default_factory=list)
    pareto_front_points: list[tuple[float, float]] = field(default_factory=list)
    hypervolume_history: list[float] = field(default_factory=list)
    objective_dim: int = 1

    latest_task_snapshot: dict[str, Any] = field(default_factory=dict)
    latest_eval_payload: dict[str, Any] = field(default_factory=dict)
    latest_finish_payload: dict[str, Any] = field(default_factory=dict)
    latest_initial_x: dict[str, Any] = field(default_factory=dict)
    latest_best_x: dict[str, Any] = field(default_factory=dict)
    latest_pareto_solutions: list[dict[str, Any]] = field(default_factory=list)
    selected_pareto_index: Optional[int] = None
    latest_result_output_dir: str = ""
    latest_history_path: str = ""
    latest_plot_path: str = ""
    latest_result_plot_paths: dict[str, str] = field(default_factory=dict)
    recent_activity: list[dict[str, str]] = field(default_factory=list)
    last_test_read_status: str = "Not checked"
    last_test_read_detail: str = ""

    def add_recent_activity(self, entry: dict[str, Any], limit: int = 12) -> None:
        normalized = {
            "event": str(entry.get("event", "")).strip() or "Update",
            "task": str(entry.get("task", "")).strip() or "untitled_task",
            "mode": str(entry.get("mode", "")).strip() or "--",
            "algorithm": str(entry.get("algorithm", "")).strip() or "--",
            "status": str(entry.get("status", "")).strip() or "--",
            "timestamp": str(entry.get("timestamp", "")).strip() or "",
        }
        self.recent_activity.insert(0, normalized)
        if len(self.recent_activity) > limit:
            del self.recent_activity[limit:]

    def reset_plot_data(self, objective_dim: int) -> None:
        self.objective_dim = int(objective_dim)
        self.objective_history.clear()
        self.best_history.clear()
        self.pareto_points.clear()
        self.pareto_front_points.clear()
        self.hypervolume_history.clear()

    def reset_for_run_start(self, objective_dim: int) -> None:
        self.run.reset_for_start()
        self.reset_plot_data(objective_dim)
        self.eval_history.clear()
        self.eval_x_history.clear()
        self.eval_y_history.clear()
        self.latest_initial_x.clear()
        self.latest_result_plot_paths.clear()

    def reset_results_snapshot(self) -> None:
        self.latest_task_snapshot.clear()
        self.latest_eval_payload.clear()
        self.latest_finish_payload.clear()
        self.latest_initial_x.clear()
        self.latest_best_x.clear()
        self.latest_pareto_solutions.clear()
        self.selected_pareto_index = None
        self.latest_result_output_dir = ""
        self.latest_history_path = ""
        self.latest_plot_path = ""
        self.latest_result_plot_paths.clear()
