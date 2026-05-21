from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from ..main_window import MainWindow


class RunResultsPresenter:
    def __init__(self, window: "MainWindow") -> None:
        self.window = window
        self.view = window.view_adapter

    def apply_evaluation_payload(self, payload: dict[str, Any]) -> None:
        self._append_objective_snapshot(payload)
        self.view.append_recent_eval(payload)
        self._update_solution_inspector(payload)
        self.view.update_results_after_evaluation(payload)
        self._append_evaluation_history(payload)
        self.view.populate_history_table()
        self.view.redraw_plots()

    def _append_objective_snapshot(self, payload: dict[str, Any]) -> None:
        state = self.window.state
        run = state.run
        objective_values = payload.get("objective_values") or []
        if state.objective_dim == 1:
            scalar_value = None
            if payload.get("objective_value") is not None:
                scalar_value = float(payload.get("objective_value"))
            elif objective_values:
                scalar_value = float(objective_values[0])
            if scalar_value is not None:
                state.objective_history.append(scalar_value)
                best_val = run.best_value if run.best_value is not None else scalar_value
                state.best_history.append(float(best_val))
            return

        if len(objective_values) >= 2:
            state.pareto_points.append(
                (float(objective_values[0]), float(objective_values[1]))
            )

    def _update_solution_inspector(self, payload: dict[str, Any]) -> None:
        inspector = getattr(self.window.ui, "tableWidget_solutionInspector", None)
        if not self.view.qobj_alive(inspector):
            return

        x_values = payload.get("x_values", {})
        x_text = ", ".join(f"{k}={v:.4f}" for k, v in x_values.items()) if x_values else "--"
        objective_text = str(payload.get("objective_summary", ""))
        if not objective_text:
            if payload.get("objective_value") is None:
                objective_text = "--"
            else:
                objective_text = f"{float(payload.get('objective_value', 0.0)):.6f}"

        self.view.set_table_row(
            inspector,
            0,
            ["Run", self.window.task_ui.lineEdit_taskName.text().strip() or "untitled_task"],
        )
        self.view.set_table_row(inspector, 1, ["Point", x_text])
        self.view.set_table_row(inspector, 2, ["Objective", objective_text])
        self.view.set_table_row(
            inspector,
            3,
            ["Constraints", str(payload.get("constraint_summary", "--"))],
        )

    def _append_evaluation_history(self, payload: dict[str, Any]) -> None:
        state = self.window.state
        x_dict = payload.get("x_values", {}) or {}
        x_list = list(x_dict.values())

        if payload.get("objective_value") is not None:
            y_val: Any = float(payload["objective_value"])
        elif payload.get("objective_values"):
            y_val = payload["objective_values"]
        else:
            y_val = None

        c_val = payload.get("constraint_summary", None)

        state.eval_history.append((x_dict, y_val, c_val))
        state.eval_x_history.append(x_list)
        state.eval_y_history.append(y_val)
