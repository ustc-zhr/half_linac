from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PyQt5.QtCore import QUrl, Qt
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidgetItem,
    QVBoxLayout,
)

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


class ResultsController:
    def __init__(self, window: "MainWindow", canvas_class) -> None:
        self.window = window
        self.view = window.view_adapter
        self.canvas_class = canvas_class

    def init_results_page(self) -> None:
        tree = self.window.ui.treeWidget_runList
        tree.setColumnCount(2)
        tree.setHeaderLabels(["Artifact", "Value"])
        tree.setColumnWidth(0, 180)
        tree.header().setSectionResizeMode(0, QHeaderView.Interactive)
        tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        tree.header().setStretchLastSection(True)
        self._ensure_archive_controls()
        self._ensure_pareto_solution_controls()
        self.populate_results_tree()
        self.update_results_summary_table()
        self.refresh_result_source()

    def _ensure_archive_controls(self) -> None:
        if hasattr(self.window.ui, "pushButton_loadArchivedRun"):
            return
        layout = self.window.ui.verticalLayout_runList
        actions = QHBoxLayout()
        refresh = QPushButton("Refresh", self.window.ui.groupBox_runList)
        load = QPushButton("Load Run", self.window.ui.groupBox_runList)
        open_dir = QPushButton("Open Folder", self.window.ui.groupBox_runList)
        refresh.setToolTip("Refresh archived runs from the working directory.")
        load.setToolTip("Load the selected archived run in read-only mode.")
        open_dir.setToolTip("Open the selected run directory.")
        refresh.clicked.connect(self.populate_results_tree)
        load.clicked.connect(self.load_selected_archive)
        open_dir.clicked.connect(self.open_selected_archive_directory)
        actions.addWidget(refresh)
        actions.addWidget(load)
        actions.addWidget(open_dir)
        layout.insertLayout(0, actions)
        self.window.ui.pushButton_refreshRunArchives = refresh
        self.window.ui.pushButton_loadArchivedRun = load
        self.window.ui.pushButton_openRunArchive = open_dir

    def refresh_result_source(self) -> None:
        if not hasattr(self.window, "label_results_source_task"):
            return
        state = self.window.state
        task = state.latest_task_snapshot or {}
        task_name = str(task.get("task_name", "")).strip() or "No run"
        outcome = (
            str(state.latest_finish_payload.get("state", state.run.phase))
            if state.latest_finish_payload
            else state.run.phase if task else "--"
        )
        if state.viewing_archived_run:
            outcome = f"Archived · {outcome}"
        output_path = state.latest_result_output_dir or ""
        output_text = Path(output_path).name if output_path else "--"

        self.window.label_results_source_task.setText(task_name)
        self.window.label_results_source_task.setToolTip(
            f"Frozen result task: {task_name}" if task else "No run result is available."
        )
        self.window.label_results_source_outcome.setText(outcome)
        self.window.label_results_source_outcome.setToolTip("Outcome of the result-producing run.")
        self.window.label_results_source_output.setText(output_text)
        self.window.label_results_source_output.setToolTip(output_path or "No output directory is available.")

        tone = {
            "Running": "success",
            "Finished": "success",
            "Completed": "success",
            "Stopping": "warning",
            "Aborted": "warning",
            "Restoring": "warning",
            "Abort Requested": "danger",
            "Error": "danger",
            "Failed": "danger",
            "Restore Failed": "danger",
        }.get(outcome, "subtle")
        self.window._set_status_label_tone(self.window.label_results_source_outcome, tone)

    def init_plot_canvases(self) -> None:
        self.window.obj_canvas = self.attach_plot_canvas(self.window.run_ui.frame_obj)
        self.window.pareto_canvas = self.attach_plot_canvas(self.window.run_ui.frame_pareto)
        self.window.run_var_canvas = self.attach_plot_canvas(self.window.run_ui.frame_variables)
        self.window.run_constraints_canvas = self.attach_plot_canvas(self.window.run_ui.frame_constraints)
        self.window.results_conv_canvas = self.attach_plot_canvas(self.window.ui.frame_plotConvergence)
        self.window.results_pareto_canvas = self.attach_plot_canvas(self.window.ui.frame_plotParetoFinal)
        variable_frame = getattr(self.window.ui, "frame_plotVariables", self.window.ui.frame_plotConvergence)
        self.window.var_canvas = self.attach_plot_canvas(variable_frame)
        self.reset_plot_data()
        self.redraw_plots()

    def attach_plot_canvas(self, frame):
        frame.setMinimumSize(180, 140)
        margin = 0 if frame.property("plotHost") else 4
        layout = frame.layout()
        if layout is None:
            layout = QVBoxLayout(frame)
            layout.setContentsMargins(margin, margin, margin, margin)
        else:
            layout.setContentsMargins(margin, margin, margin, margin)
            while layout.count():
                item = layout.takeAt(0)
                child = item.widget()
                if child is not None:
                    child.deleteLater()
        canvas = self.canvas_class(frame)
        layout.addWidget(canvas)
        return canvas

    def _ensure_pareto_solution_controls(self) -> None:
        if hasattr(self.window.ui, "tableWidget_paretoSolutions"):
            return
        layout = getattr(self.window.ui, "verticalLayout_pareto", None)
        if layout is None:
            return

        group = QGroupBox("Pareto Solutions", self.window.ui.tab_pareto)
        group.setObjectName("groupBox_paretoSolutions")
        group_layout = QVBoxLayout(group)
        hint = QLabel("", group)
        hint.setWordWrap(True)
        hint.setVisible(False)
        group_layout.addWidget(hint)

        table = QTableWidget(group)
        table.setObjectName("tableWidget_paretoSolutions")
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels(["Index", "Feasible", "Objectives", "Constraints", "Variables"])
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.itemSelectionChanged.connect(self.on_pareto_solution_selection_changed)
        table.setMinimumHeight(180)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        table.setColumnWidth(0, 64)
        table.setColumnWidth(1, 78)
        for column in range(2, 5):
            header.setSectionResizeMode(column, QHeaderView.Stretch)
        content = QHBoxLayout()
        content.addWidget(table, 3)

        detail = QTableWidget(group)
        detail.setObjectName("tableWidget_paretoSelectionDetail")
        detail.setColumnCount(2)
        detail.setHorizontalHeaderLabels(["Selected Field", "Value"])
        detail.setEditTriggers(QAbstractItemView.NoEditTriggers)
        detail.setSelectionMode(QAbstractItemView.NoSelection)
        detail.setMinimumWidth(240)
        detail.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        detail.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        content.addWidget(detail, 2)
        group_layout.addLayout(content)

        actions = QHBoxLayout()
        button = QPushButton("Write Selected to Machine", group)
        button.setObjectName("pushButton_writeSelectedPareto")
        button.setProperty("machineWrite", True)
        button.setFixedHeight(28)
        button.setEnabled(False)
        button.clicked.connect(self.window.set_selected_pareto_to_machine)
        actions.addStretch(1)
        actions.addWidget(button)
        group_layout.addLayout(actions)

        layout.addWidget(group)
        self.window.ui.groupBox_paretoSolutions = group
        self.window.ui.label_paretoSolutionsHint = hint
        self.window.ui.tableWidget_paretoSolutions = table
        self.window.ui.tableWidget_paretoSelectionDetail = detail
        self.window.ui.pushButton_writeSelectedPareto = button

    def reset_plot_data(self) -> None:
        state = self.window.state
        objective_type = self.window.task_ui.comboBox_objectiveType.currentText().strip().lower()
        algorithm = self.window.task_ui.comboBox_algorithm.currentText().strip().lower()
        if objective_type == "multi objective" or algorithm in {"mobo", "consmobo", "mopso", "nsga2"}:
            objective_dim = 2
        else:
            objective_dim = 1
        state.reset_plot_data(objective_dim)

    def redraw_plots(self) -> None:
        if not all(
            hasattr(self.window, name)
            for name in ("obj_canvas", "pareto_canvas", "results_conv_canvas", "results_pareto_canvas")
        ):
            return
        multi = self.window.state.objective_dim > 1
        self.draw_objective_plot(
            self.window.obj_canvas,
            title="Hypervolume History" if multi else "Objective History",
        )
        self.draw_pareto_plot(self.window.pareto_canvas, title="Live Pareto Front")
        self.draw_objective_plot(
            self.window.results_conv_canvas,
            title="Hypervolume History" if multi else "Convergence",
        )
        self.draw_pareto_plot(self.window.results_pareto_canvas, title="Final Pareto")
        if hasattr(self.window, "run_constraints_canvas"):
            self.draw_constraint_history(self.window.run_constraints_canvas)
        self.draw_variable_trajectories()

    def draw_objective_plot(self, canvas, *, title: str) -> None:
        state = self.window.state
        if state.objective_dim != 1:
            if not state.hypervolume_history:
                canvas.clear_with_message(
                    title,
                    "Hypervolume history is shown here for multi-objective tasks once available.",
                )
                return
            canvas.figure.clear()
            ax = canvas.figure.add_subplot(111)
            xs = list(range(1, len(state.hypervolume_history) + 1))
            ax.plot(xs, state.hypervolume_history, label="hypervolume")
            ax.set_title(title)
            ax.set_xlabel("Generation / Update")
            ax.set_ylabel("Hypervolume")
            ax.grid(True, alpha=0.3)
            ax.legend()
            finite_values = [v for v in state.hypervolume_history if self._is_finite(v)]
            if finite_values and max(finite_values) <= 0.0 and state.pareto_points:
                ax.text(
                    0.5,
                    0.08,
                    "Hypervolume is zero. Check that the reference point is worse than all objectives.",
                    transform=ax.transAxes,
                    ha="center",
                    va="bottom",
                    color="#f0b35a",
                    fontsize=9,
                    wrap=True,
                )
            canvas.apply_theme_to_axes(ax)
            canvas.draw_idle()
            return
        if not state.objective_history:
            canvas.clear_with_message(title, "No evaluations yet.")
            return

        canvas.figure.clear()
        ax = canvas.figure.add_subplot(111)
        xs = list(range(1, len(state.objective_history) + 1))
        ax.plot(xs, state.objective_history, label="objective")
        if state.best_history:
            ax.plot(xs, state.best_history, label="best-so-far")
        ax.set_title(title)
        ax.set_xlabel("Evaluation")
        ax.set_ylabel("Objective")
        ax.grid(True, alpha=0.3)
        ax.legend()
        canvas.apply_theme_to_axes(ax)
        canvas.draw_idle()

    def draw_pareto_plot(self, canvas, *, title: str) -> None:
        state = self.window.state
        if state.objective_dim == 1:
            canvas.clear_with_message(title, "Pareto scatter is available for multi-objective tasks.")
            return
        all_points = state.pareto_points
        front_points = state.pareto_front_points
        points = front_points if "final" in title.lower() and front_points else all_points
        if not points:
            canvas.clear_with_message(title, "No multi-objective evaluations yet.")
            return

        canvas.figure.clear()
        ax = canvas.figure.add_subplot(111)
        if all_points and points is not all_points:
            ax.scatter(
                [p[0] for p in all_points if len(p) >= 2],
                [p[1] for p in all_points if len(p) >= 2],
                s=22,
                alpha=0.28,
                label="evaluated",
            )
        feasible_points, infeasible_points = self._split_pareto_points_by_feasibility(points)
        if feasible_points:
            ax.scatter(
                [p[0] for p in feasible_points],
                [p[1] for p in feasible_points],
                s=34,
                label="Pareto feasible",
            )
        if infeasible_points:
            ax.scatter(
                [p[0] for p in infeasible_points],
                [p[1] for p in infeasible_points],
                s=38,
                marker="x",
                label="infeasible",
            )
        selected = self.selected_pareto_solution()
        selected_y = selected.get("y", []) if selected else []
        if len(selected_y) >= 2:
            ax.scatter(
                [selected_y[0]], [selected_y[1]], s=110, marker="o",
                facecolors="none", edgecolors="#f0b35a", linewidths=2.0,
                label="selected",
            )
        ax.set_title(title)
        objective_labels = self._objective_labels(2)
        ax.set_xlabel(objective_labels[0])
        ax.set_ylabel(objective_labels[1])
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=8)
        canvas.apply_theme_to_axes(ax)
        canvas.draw_idle()

    def draw_constraint_history(self, canvas) -> None:
        series: list[list[float]] = []
        for _x, _y, constraints in self.window.state.eval_history:
            values = self._constraint_values(constraints)
            if values:
                series.append(values)
        if not series:
            canvas.clear_with_message("Constraint History", "No numeric constraint values yet.")
            return
        width = max(len(row) for row in series)
        canvas.figure.clear()
        ax = canvas.figure.add_subplot(111)
        for index in range(width):
            xs, ys = [], []
            for evaluation, row in enumerate(series, start=1):
                if index < len(row):
                    xs.append(evaluation)
                    ys.append(row[index])
            ax.plot(xs, ys, ".-", label=f"c{index}")
        ax.set_title("Constraint History")
        ax.set_xlabel("Evaluation")
        ax.set_ylabel("Constraint Value")
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=8)
        canvas.apply_theme_to_axes(ax)
        canvas.draw_idle()

    def _constraint_values(self, value: Any) -> list[float]:
        if isinstance(value, dict):
            value = list(value.values())
        if not isinstance(value, (list, tuple)):
            return []
        try:
            return [float(item) for item in value]
        except (TypeError, ValueError):
            return []

    def _objective_labels(self, count: int) -> list[str]:
        task = self.window.state.latest_task_snapshot or self.view.current_task()
        labels: list[str] = []
        for row in task.get("objectives", []) or []:
            if not isinstance(row, dict) or not self._row_enabled(row):
                continue
            name = str(row.get("Name", "")).strip() or f"f{len(labels)}"
            direction = str(row.get("Direction", "")).strip().lower()
            suffix = "max" if direction.startswith("max") else "min" if direction.startswith("min") else ""
            labels.append(f"{name} ({suffix})" if suffix else name)
            if len(labels) >= count:
                break
        while len(labels) < count:
            labels.append(f"f{len(labels)}")
        return labels

    def _split_pareto_points_by_feasibility(
        self, points: list[tuple[float, float]]
    ) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
        solutions = self.window.state.latest_pareto_solutions
        if not solutions or len(solutions) != len(points):
            return list(points), []
        feasible, infeasible = [], []
        for point, solution in zip(points, solutions):
            (feasible if solution.get("feasible", True) else infeasible).append(point)
        return feasible, infeasible

    def _is_finite(self, value: Any) -> bool:
        try:
            import math
            return math.isfinite(float(value))
        except (TypeError, ValueError):
            return False

    def draw_variable_trajectories(self) -> None:
        targets = []
        if hasattr(self.window, "var_canvas"):
            targets.append(self.window.var_canvas)
        if hasattr(self.window, "run_var_canvas"):
            targets.append(self.window.run_var_canvas)
        if not targets:
            return
        if not self.window.state.eval_x_history:
            for canvas in targets:
                canvas.clear_with_message("Variable Trajectories", "No evaluation vectors yet.")
            return

        import numpy as np

        X = np.array(self.window.state.eval_x_history)
        if X.ndim == 1:
            X = X.reshape(-1, 1)
        variable_names = self._current_variable_names(X.shape[1])
        for canvas in targets:
            canvas.figure.clear()
            ax = canvas.figure.add_subplot(111)

            x_axis = range(1, X.shape[0] + 1)
            for i, name in enumerate(variable_names):
                ax.plot(x_axis, X[:, i], ".-", linewidth=1.1, markersize=3, label=name)
            ax.set_title("Variable Trajectories")
            ax.set_xlabel("Evaluation")
            ax.set_ylabel("Value")
            ax.grid(True, alpha=0.3)
            if X.shape[1] <= 30:
                ax.legend(fontsize=7, ncol=max(1, min(4, (X.shape[1] + 7) // 8)))
            canvas.apply_theme_to_axes(ax)
            canvas.figure.subplots_adjust(left=0.12, right=0.98, top=0.88, bottom=0.16)
            canvas.draw_idle()

    def _current_variable_names(self, count: int) -> list[str]:
        table = getattr(self.window.task_ui, "tableWidget_variables", None)
        names: list[str] = []
        if table is not None:
            for row in range(table.rowCount()):
                enabled_item = table.item(row, 0)
                enabled = enabled_item is None or enabled_item.text().strip().lower() not in {"n", "no", "false", "0"}
                name_item = table.item(row, 1)
                name = name_item.text().strip() if name_item is not None else ""
                if enabled and name:
                    names.append(name)
                if len(names) >= count:
                    break
        while len(names) < count:
            names.append(f"x{len(names)}")
        return names[:count]

    def populate_history_table(self) -> None:
        table = getattr(self.window.ui, "tableWidget_history", None)
        if table is None:
            return

        table.setRowCount(len(self.window.state.eval_history))

        for i, (x, y, _) in enumerate(self.window.state.eval_history):
            table.setItem(i, 0, QTableWidgetItem(str(i)))
            table.setItem(i, 1, QTableWidgetItem(str(x)))
            table.setItem(i, 2, QTableWidgetItem(str(y)))

    def on_history_row_clicked(self, row) -> None:
        if row >= len(self.window.state.eval_history):
            return

        x, y, c = self.window.state.eval_history[row]

        inspector = self.window.ui.tableWidget_solutionInspector
        if not self.view.qobj_alive(inspector):
            return

        task_name = (self.window.state.latest_task_snapshot or {}).get(
            "task_name",
            self.window.task_ui.lineEdit_taskName.text().strip() or "untitled_task",
        )
        inspector.setRowCount(4)
        self.view.set_table_row(inspector, 0, ["Run", task_name])
        self.view.set_table_row(inspector, 1, ["Point", str(x)])
        self.view.set_table_row(inspector, 2, ["Objective", str(y)])
        self.view.set_table_row(inspector, 3, ["Constraints", str(c)])

    def append_recent_eval(self, payload: dict) -> None:
        eval_id = str(payload.get("eval_id", ""))
        timestamp = str(payload.get("timestamp", ""))
        status = str(payload.get("status", ""))
        x_values = payload.get("x_values", {})
        x_summary = ", ".join(f"{k}={v:.3f}" for k, v in list(x_values.items())[:3])
        if payload.get("objective_summary"):
            y_summary = str(payload.get("objective_summary"))
        elif payload.get("objective_value") is not None:
            y_summary = f"y0={float(payload.get('objective_value', 0.0)):.6f}"
        else:
            y_summary = "--"
        c_summary = str(payload.get("constraint_summary", ""))

        for table in self.view.living_tables(self.window.run_ui.tableWidget_recent):
            row = table.rowCount()
            table.insertRow(row)
            self.view.set_table_row(table, row, [eval_id, timestamp, status, x_summary, y_summary, c_summary])

    def summarize_x_values(self, x_values: dict | None) -> str:
        if not x_values:
            return "--"
        return ", ".join(f"{k}={float(v):.6g}" for k, v in x_values.items())

    def _format_vector(self, prefix: str, values: list[float] | tuple[float, ...] | None) -> str:
        if not values:
            return "--"
        return ", ".join(f"{prefix}{i}={float(v):.6g}" for i, v in enumerate(values))

    def _coerce_float_list(self, value: Any) -> list[float]:
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            return [float(v) for v in value]
        return [float(value)]

    def _coerce_bool(self, value: Any, default: bool = True) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"true", "1", "yes", "y", "on", "feasible"}:
            return True
        if text in {"false", "0", "no", "n", "off", "infeasible"}:
            return False
        return default

    def _row_enabled(self, row: dict[str, Any]) -> bool:
        value = row.get("Enabled", "")
        if value == "":
            return True
        return self._coerce_bool(value, default=True)

    def _pareto_variable_names(self, count: int) -> list[str]:
        task = self.window.state.latest_task_snapshot or self.view.current_task()
        names: list[str] = []
        for row in task.get("variables", []) or []:
            if not isinstance(row, dict) or not self._row_enabled(row):
                continue
            name = str(row.get("Name", "")).strip()
            names.append(name or f"x{len(names)}")
            if len(names) >= count:
                break
        if len(names) < count:
            names = self._current_variable_names(count)
        while len(names) < count:
            names.append(f"x{len(names)}")
        return names[:count]

    def _build_pareto_solutions(self, payload: dict) -> list[dict[str, Any]]:
        pareto_x = payload.get("pareto_x") or []
        pareto_y = payload.get("pareto_y") or []
        if not isinstance(pareto_x, list) or not isinstance(pareto_y, list):
            return []
        count = min(len(pareto_x), len(pareto_y))
        if count == 0:
            return []

        first_x = self._coerce_float_list(pareto_x[0])
        variable_names = self._pareto_variable_names(len(first_x))
        feasible_values = payload.get("pareto_feasible") or []
        constraint_values = payload.get("pareto_constraints") or []

        solutions: list[dict[str, Any]] = []
        for i in range(count):
            x_vec = self._coerce_float_list(pareto_x[i])
            y_vec = self._coerce_float_list(pareto_y[i])
            names = variable_names
            if len(names) < len(x_vec):
                names = [*names, *[f"x{j}" for j in range(len(names), len(x_vec))]]
            x_dict = {names[j]: float(x_vec[j]) for j in range(len(x_vec))}
            if isinstance(feasible_values, list) and i < len(feasible_values):
                feasible = self._coerce_bool(feasible_values[i], default=True)
            else:
                feasible = True
            constraints = []
            if isinstance(constraint_values, list) and i < len(constraint_values):
                constraints = self._coerce_float_list(constraint_values[i])
            solutions.append(
                {
                    "index": i,
                    "x": x_dict,
                    "x_values": x_vec,
                    "y": y_vec,
                    "constraints": constraints,
                    "feasible": feasible,
                }
            )
        return solutions

    def populate_pareto_solution_table(self) -> None:
        table = getattr(self.window.ui, "tableWidget_paretoSolutions", None)
        if table is None or not self.view.qobj_alive(table):
            return

        solutions = self.window.state.latest_pareto_solutions
        was_blocked = table.blockSignals(True)
        try:
            table.clearSelection()
            table.setRowCount(len(solutions))
            for row, solution in enumerate(solutions):
                values = [
                    str(solution.get("index", row)),
                    "yes" if solution.get("feasible", True) else "no",
                    self._format_vector("f", solution.get("y", [])),
                    self._format_vector("c", solution.get("constraints", [])),
                    self.summarize_x_values(solution.get("x", {})),
                ]
                for col, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    if col == 0:
                        item.setTextAlignment(Qt.AlignCenter)
                        item.setData(Qt.UserRole, int(solution.get("index", row)))
                    table.setItem(row, col, item)
        finally:
            table.blockSignals(was_blocked)
        table.resizeRowsToContents()
        self._sync_pareto_write_button()

    def selected_pareto_solution(self) -> dict[str, Any] | None:
        table = getattr(self.window.ui, "tableWidget_paretoSolutions", None)
        selected_index = self.window.state.selected_pareto_index
        if table is not None and self.view.qobj_alive(table):
            selected_rows = table.selectionModel().selectedRows() if table.selectionModel() else []
            if selected_rows:
                row = int(selected_rows[0].row())
                item = table.item(row, 0)
                if item is not None and item.data(Qt.UserRole) is not None:
                    selected_index = int(item.data(Qt.UserRole))
        if selected_index is None:
            return None
        for solution in self.window.state.latest_pareto_solutions:
            if int(solution.get("index", -1)) == int(selected_index):
                return solution
        return None

    def on_pareto_solution_selection_changed(self) -> None:
        solution = self.selected_pareto_solution()
        self.window.state.selected_pareto_index = (
            None if solution is None else int(solution.get("index", 0))
        )
        self._sync_pareto_write_button()
        if solution is not None:
            self.show_pareto_solution_details(solution)
        self.draw_pareto_plot(self.window.results_pareto_canvas, title="Final Pareto")

    def show_pareto_solution_details(self, solution: dict[str, Any]) -> None:
        rows = [
            ("Selected Pareto", str(solution.get("index", "--"))),
            ("Feasible", "yes" if solution.get("feasible", True) else "no"),
            ("Objectives", self._format_vector("f", solution.get("y", []))),
            ("Constraints", self._format_vector("c", solution.get("constraints", []))),
            ("Point", self.summarize_x_values(solution.get("x", {}))),
        ]
        for inspector in (
            getattr(self.window.ui, "tableWidget_solutionInspector", None),
            getattr(self.window.ui, "tableWidget_paretoSelectionDetail", None),
        ):
            if not self.view.qobj_alive(inspector):
                continue
            inspector.setRowCount(0)
            for field, value in rows:
                row = inspector.rowCount()
                inspector.insertRow(row)
                self.view.set_table_row(inspector, row, [field, value])

    def _sync_pareto_write_button(self) -> None:
        button = getattr(self.window.ui, "pushButton_writeSelectedPareto", None)
        if button is None or not self.view.qobj_alive(button):
            return
        solution = self.selected_pareto_solution()
        task = self.window.state.latest_task_snapshot or self.view.current_task()
        is_online = self.view.is_online_task(task)
        feasible = bool(solution and solution.get("feasible", True))
        archived = self.window.state.viewing_archived_run
        button.setVisible(is_online and not archived)
        button.setEnabled(bool(solution and feasible and is_online and not archived))
        if archived:
            button.setToolTip("Archived runs are read-only and cannot write to the machine.")
        elif not solution:
            button.setToolTip("Select a Pareto solution first.")
        elif not is_online:
            button.setToolTip("Writing to machine is available for Online EPICS tasks.")
        elif not feasible:
            button.setToolTip("This Pareto point is marked infeasible and will not be written.")
        else:
            button.setToolTip("Write the selected Pareto point's variables to the machine.")

    def populate_results_tree(self) -> None:
        state = self.window.state
        tree = self.window.ui.treeWidget_runList
        tree.clear()

        if state.latest_task_snapshot:
            run_task = state.latest_task_snapshot.get("task_name", "untitled_task")
            self._append_current_result_tree(tree, run_task)

        archives = QTreeWidgetItem(["Archived Runs", ""])
        archives.setData(0, Qt.UserRole, {"kind": "archives"})
        tree.addTopLevelItem(archives)
        for summary_path in self._archive_summary_paths():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            task = summary.get("task", {}) if isinstance(summary, dict) else {}
            task_name = str(task.get("task_name") or summary_path.parent.parent.name)
            run_id = str(summary.get("run_id") or summary_path.parent.name)
            state_text = str(summary.get("run_state") or "--")
            item = QTreeWidgetItem([f"{task_name} · {run_id}", state_text])
            item.setData(
                0,
                Qt.UserRole,
                {"kind": "archive", "path": str(summary_path.parent), "summary": str(summary_path)},
            )
            archives.addChild(item)
        if archives.childCount() == 0:
            archives.addChild(QTreeWidgetItem(["No archived runs", "--"]))

        tree.expandToDepth(0)
        if tree.topLevelItemCount() > 0 and tree.currentItem() is None:
            tree.setCurrentItem(tree.topLevelItem(0))

    def _append_current_result_tree(self, tree, run_task: str) -> None:
        state = self.window.state
        run_state = (
            state.latest_finish_payload.get("state", state.run.phase)
            if state.latest_finish_payload
            else state.run.phase
        )
        prefix = "Archived Result" if state.viewing_archived_run else "Current Result"
        run_item = QTreeWidgetItem([f"{prefix}: {run_task}", run_state])
        run_item.setData(0, Qt.UserRole, {"kind": "run"})
        tree.addTopLevelItem(run_item)

        summary = QTreeWidgetItem(["Summary", ""])
        summary.setData(0, Qt.UserRole, {"kind": "summary"})
        run_item.addChild(summary)
        summary.addChild(
            QTreeWidgetItem(
                ["Best Value", "--" if state.run.best_value is None else f"{state.run.best_value:.6f}"]
            )
        )
        summary.addChild(QTreeWidgetItem(["Best Point", self.summarize_x_values(state.latest_best_x)]))
        summary.addChild(QTreeWidgetItem(["Objective Dim", str(state.objective_dim)]))
        summary.addChild(QTreeWidgetItem(["Evaluations", str(state.run.eval_count)]))
        if state.objective_dim > 1:
            summary.addChild(
                QTreeWidgetItem(["Pareto Points", str(len(state.pareto_front_points) or len(state.pareto_points))])
            )
            summary.addChild(QTreeWidgetItem(["HV Samples", str(len(state.hypervolume_history))]))

        artifacts = QTreeWidgetItem(["Artifacts", ""])
        artifacts.setData(0, Qt.UserRole, {"kind": "artifacts"})
        run_item.addChild(artifacts)

        if state.latest_history_path:
            item = QTreeWidgetItem(["History File", state.latest_history_path])
            item.setData(0, Qt.UserRole, {"kind": "path", "path": state.latest_history_path})
            artifacts.addChild(item)
        if state.latest_plot_path:
            item = QTreeWidgetItem(["Convergence Plot", state.latest_plot_path])
            item.setData(0, Qt.UserRole, {"kind": "path", "path": state.latest_plot_path})
            artifacts.addChild(item)
        for label, path in state.latest_result_plot_paths.items():
            item = QTreeWidgetItem([label, path])
            item.setData(0, Qt.UserRole, {"kind": "path", "path": path})
            artifacts.addChild(item)
        if state.latest_result_output_dir:
            item = QTreeWidgetItem(["Output Directory", state.latest_result_output_dir])
            item.setData(0, Qt.UserRole, {"kind": "path", "path": state.latest_result_output_dir})
            artifacts.addChild(item)

        latest_eval = QTreeWidgetItem(["Latest Evaluation", ""])
        latest_eval.setData(0, Qt.UserRole, {"kind": "latest_eval"})
        run_item.addChild(latest_eval)
        if state.latest_eval_payload:
            latest_eval.addChild(
                QTreeWidgetItem(
                    ["Point", self.summarize_x_values(state.latest_eval_payload.get("x_values", {}))]
                )
            )
            latest_eval.addChild(
                QTreeWidgetItem(
                    [
                        "Objective",
                        str(
                            state.latest_eval_payload.get("objective_summary")
                            or state.latest_eval_payload.get("objective_value")
                            or "--"
                        ),
                    ]
                )
            )
            latest_eval.addChild(
                QTreeWidgetItem(
                    ["Constraints", str(state.latest_eval_payload.get("constraint_summary", "--"))]
                )
            )


    def _archive_root(self) -> Path:
        task = self.window.state.latest_task_snapshot or {}
        root = task.get("project_workdir")
        if not root:
            try:
                root = self.view.current_task().get("workdir")
            except Exception:
                root = None
        return Path(str(root or Path.cwd())).expanduser().resolve()

    def _archive_summary_paths(self) -> list[Path]:
        root = self._archive_root()
        if not root.exists():
            return []
        paths = list(root.glob("*/*/run_summary.json"))
        return sorted(paths, key=lambda path: path.stat().st_mtime, reverse=True)

    def _selected_archive_directory(self) -> Path | None:
        items = self.window.ui.treeWidget_runList.selectedItems()
        item = items[0] if items else None
        while item is not None:
            data = item.data(0, Qt.UserRole) or {}
            if isinstance(data, dict) and data.get("kind") == "archive":
                return Path(str(data.get("path")))
            item = item.parent()
        return None

    def load_selected_archive(self) -> None:
        directory = self._selected_archive_directory()
        if directory is None:
            selected = QFileDialog.getExistingDirectory(
                self.window, "Open Archived Run", str(self._archive_root())
            )
            if not selected:
                return
            directory = Path(selected)
        try:
            self.load_run_archive(directory)
        except Exception as exc:
            QMessageBox.critical(self.window, "Open Archived Run", str(exc))

    def open_selected_archive_directory(self) -> None:
        directory = self._selected_archive_directory()
        if directory is None or not directory.exists():
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory.resolve())))

    def update_results_summary_table(self, selected_item=None) -> None:
        state = self.window.state
        table = self.window.ui.tableWidget_solutionInspector
        rows = []
        task_name = (state.latest_task_snapshot or {}).get("task_name", "No run")
        rows.append(("Task", task_name))
        rows.append(
            (
                "Status",
                state.latest_finish_payload.get("state", state.run.phase)
                if state.latest_finish_payload
                else state.run.phase if state.latest_task_snapshot else "--",
            )
        )
        rows.append(("Best Value", "--" if state.run.best_value is None else f"{state.run.best_value:.6f}"))
        rows.append(("Best Point", self.summarize_x_values(state.latest_best_x)))
        rows.append(("History Path", state.latest_history_path or "--"))
        rows.append(("Result Images", str(len(state.latest_result_plot_paths))))
        rows.append(("Output Directory", state.latest_result_output_dir or "--"))
        if state.objective_dim > 1:
            rows.append(("Pareto Points", str(len(state.pareto_front_points) or len(state.pareto_points))))
            rows.append(("HV Samples", str(len(state.hypervolume_history))))

        if selected_item is not None:
            data = selected_item.data(0, Qt.UserRole) or {}
            rows.append(("Selected Item", selected_item.text(0)))
            rows.append(("Selected Value", selected_item.text(1)))
            if isinstance(data, dict) and data.get("kind") == "path":
                rows.append(("Action", "Double-click to open"))

        table.setRowCount(0)
        for field, value in rows:
            row = table.rowCount()
            table.insertRow(row)
            self.view.set_table_row(table, row, [field, value])

    def on_results_tree_selection_changed(self) -> None:
        items = self.window.ui.treeWidget_runList.selectedItems()
        self.update_results_summary_table(items[0] if items else None)

    def open_selected_result_item(self, item, _column: int) -> None:
        data = item.data(0, Qt.UserRole) or {}
        if isinstance(data, dict) and data.get("kind") == "archive":
            try:
                self.load_run_archive(str(data.get("path")))
            except Exception as exc:
                QMessageBox.critical(self.window, "Open Archived Run", str(exc))
            return
        path = data.get("path") if isinstance(data, dict) else None
        if not path:
            return
        file_path = Path(path)
        target = file_path if file_path.exists() else file_path.parent
        if not target.exists():
            QMessageBox.information(self.window, "Open Artifact", f"Path does not exist yet:\n{path}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.resolve())))

    def update_results_after_start(self, task: dict) -> None:
        state = self.window.state
        state.viewing_archived_run = False
        state.latest_task_snapshot = copy.deepcopy(task)
        state.latest_task_identity = TaskService.normalized_task_identity(task)
        state.latest_eval_payload.clear()
        state.latest_finish_payload.clear()
        state.latest_initial_x.clear()
        state.latest_best_x.clear()
        state.latest_pareto_solutions.clear()
        state.selected_pareto_index = None
        state.latest_history_path = ""
        state.latest_plot_path = ""
        state.latest_result_plot_paths.clear()
        state.pareto_front_points.clear()
        state.hypervolume_history.clear()
        state.latest_result_output_dir = str(Path(task.get("workdir", Path.cwd())).resolve())
        self.populate_pareto_solution_table()
        self.populate_results_tree()
        self.update_results_summary_table()
        self.refresh_result_source()

    def archive_evaluation(self, payload: dict[str, Any]) -> None:
        task = self.window.state.latest_task_snapshot or {}
        run_dir = str(task.get("run_archive_dir") or "").strip()
        if not run_dir:
            return
        path = Path(run_dir) / "evaluations.jsonl"
        record = {
            "eval_id": payload.get("eval_id"),
            "timestamp": payload.get("timestamp"),
            "status": payload.get("status"),
            "x_values": payload.get("x_values", {}),
            "objective_value": payload.get("objective_value"),
            "objective_values": payload.get("objective_values", []),
            "objective_summary": payload.get("objective_summary", ""),
            "constraint_values": payload.get("constraint_values", []),
            "constraint_summary": payload.get("constraint_summary", ""),
            "feasible": str(payload.get("status", "")).lower() != "infeasible",
            "feasibility_ratio": payload.get("feasibility_ratio"),
            "best_value": payload.get("best_value"),
            "best_changed": payload.get("best_changed", False),
            "hypervolume_updates": payload.get("hypervolume_updates", []),
        }
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    def update_results_after_evaluation(self, payload: dict) -> None:
        state = self.window.state
        state.latest_eval_payload = dict(payload)
        if payload.get("best_changed"):
            state.latest_best_x = dict(payload.get("x_values", {}))
        self.update_results_summary_table()
        self.refresh_result_source()

    def update_results_after_finish(self, payload: dict) -> None:
        state = self.window.state
        state.latest_finish_payload = dict(payload)
        best_x = payload.get("best_x")
        if isinstance(best_x, dict) and best_x:
            state.latest_best_x = dict(best_x)
        state.latest_pareto_solutions = self._build_pareto_solutions(payload)
        state.selected_pareto_index = None
        pareto_y = payload.get("pareto_y")
        if isinstance(pareto_y, list):
            state.pareto_front_points = [
                (float(row[0]), float(row[1]))
                for row in pareto_y
                if isinstance(row, (list, tuple)) and len(row) >= 2
            ]
        hv_history = payload.get("hypervolume_history")
        if isinstance(hv_history, list):
            state.hypervolume_history = [float(v) for v in hv_history]
        state.latest_history_path = str(payload.get("history_path") or "")
        state.latest_plot_path = str(payload.get("plot_path") or "")
        output_dir = ""
        if state.latest_history_path:
            output_dir = str(Path(state.latest_history_path).resolve().parent)
        elif state.latest_plot_path:
            output_dir = str(Path(state.latest_plot_path).resolve().parent)
        elif state.latest_task_snapshot:
            output_dir = str(Path(state.latest_task_snapshot.get("workdir", Path.cwd())).resolve())
        state.latest_result_output_dir = output_dir
        self.populate_pareto_solution_table()
        self.populate_results_tree()
        self.update_results_summary_table()
        self.refresh_result_source()
        if state.objective_dim > 1 and hasattr(self.window.ui, "tab_pareto"):
            index = self.window.ui.tabWidget_resultsViews.indexOf(self.window.ui.tab_pareto)
            if index >= 0:
                self.window.ui.tabWidget_resultsViews.setCurrentIndex(index)

    def save_result_images(self, output_dir: str | Path | None = None) -> dict[str, str]:
        state = self.window.state
        target_dir = Path(output_dir or state.latest_result_output_dir or Path.cwd()).resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        stem = self._result_artifact_stem()
        canvases = {
            "Results Convergence": getattr(self.window, "results_conv_canvas", None),
            "Results Variables": getattr(self.window, "var_canvas", None),
            "Results Pareto": getattr(self.window, "results_pareto_canvas", None),
        }
        suffixes = {
            "Results Convergence": "results_convergence",
            "Results Variables": "results_variables",
            "Results Pareto": "results_pareto",
        }
        archive_names = {
            "Results Convergence": "convergence.png",
            "Results Variables": "variables.png",
            "Results Pareto": "pareto.png",
        }
        archived = bool((state.latest_task_snapshot or {}).get("run_archive_dir"))
        saved: dict[str, str] = {}
        for label, canvas in canvases.items():
            if canvas is None:
                continue
            filename = archive_names[label] if archived else f"{stem}_{suffixes[label]}.png"
            path = target_dir / filename
            canvas.figure.savefig(str(path), dpi=160, bbox_inches="tight")
            saved[label] = str(path)
        state.latest_result_plot_paths = saved
        if saved:
            state.latest_result_output_dir = str(target_dir)
        self.populate_results_tree()
        self.update_results_summary_table()
        self.refresh_result_source()
        return saved

    def save_run_summary(self) -> str:
        state = self.window.state
        target_dir = Path(state.latest_result_output_dir or Path.cwd()).resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / "run_summary.json"
        summary = {
            "format_version": 1,
            "run_id": (state.latest_task_snapshot or {}).get("run_id", ""),
            "task": state.latest_task_snapshot,
            "run_state": state.latest_finish_payload.get("state", state.run.phase),
            "error": state.latest_finish_payload.get("error"),
            "elapsed_seconds": state.run.elapsed_seconds,
            "eval_count": state.run.eval_count,
            "objective_dim": state.objective_dim,
            "best_value": state.run.best_value,
            "best_x": state.latest_best_x,
            "pareto_solutions": state.latest_pareto_solutions,
            "hypervolume_history": state.hypervolume_history,
            "history_path": state.latest_history_path,
            "plot_path": state.latest_plot_path,
            "result_plot_paths": state.latest_result_plot_paths,
            "output_directory": str(target_dir),
            "latest_evaluation": state.latest_eval_payload,
            "evaluations_path": str(target_dir / "evaluations.jsonl"),
        }
        path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def load_run_archive(self, directory: str | Path) -> None:
        if self.window.run_session.is_running():
            raise RuntimeError("Stop the active run before opening an archived result.")
        directory = Path(directory).expanduser().resolve()
        summary_path = directory / "run_summary.json"
        if not summary_path.is_file():
            raise FileNotFoundError(f"No run_summary.json found in {directory}")
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if not isinstance(summary, dict) or not isinstance(summary.get("task"), dict):
            raise ValueError("The run summary does not contain a valid task snapshot.")

        state = self.window.state
        task = copy.deepcopy(summary["task"])
        state.viewing_archived_run = True
        state.latest_task_snapshot = task
        state.latest_task_identity = TaskService.normalized_task_identity(task)
        state.objective_dim = max(1, int(summary.get("objective_dim", 1) or 1))
        state.objective_history.clear()
        state.best_history.clear()
        state.pareto_points.clear()
        state.pareto_front_points.clear()
        state.hypervolume_history.clear()
        state.eval_history.clear()
        state.eval_x_history.clear()
        state.eval_y_history.clear()
        state.latest_eval_payload.clear()
        state.latest_pareto_solutions = list(summary.get("pareto_solutions") or [])
        state.selected_pareto_index = None

        records = self._read_evaluation_archive(directory / "evaluations.jsonl")
        running_best = None
        for record in records:
            x_values = record.get("x_values") or {}
            objective_values = record.get("objective_values") or []
            objective_value = record.get("objective_value")
            constraints = record.get("constraint_values") or []
            y_value: Any = objective_values if objective_values else objective_value
            state.eval_history.append((x_values, y_value, constraints))
            state.eval_x_history.append([float(value) for value in x_values.values()])
            state.eval_y_history.append(y_value)
            if state.objective_dim == 1 and objective_value is not None:
                value = float(objective_value)
                state.objective_history.append(value)
                if record.get("feasible", True):
                    running_best = value if running_best is None else max(running_best, value)
                state.best_history.append(value if running_best is None else running_best)
            elif len(objective_values) >= 2:
                state.pareto_points.append((float(objective_values[0]), float(objective_values[1])))
            updates = record.get("hypervolume_updates") or []
            state.hypervolume_history.extend(float(value) for value in updates)
        if records:
            state.latest_eval_payload = dict(records[-1])

        saved_hv = summary.get("hypervolume_history")
        if isinstance(saved_hv, list):
            state.hypervolume_history = [float(value) for value in saved_hv]
        state.pareto_front_points = [
            (float(solution["y"][0]), float(solution["y"][1]))
            for solution in state.latest_pareto_solutions
            if isinstance(solution, dict) and len(solution.get("y", [])) >= 2
        ]
        state.run.phase = str(summary.get("run_state") or "Finished")
        state.run.elapsed_seconds = int(summary.get("elapsed_seconds", 0) or 0)
        state.run.eval_count = int(summary.get("eval_count", len(records)) or len(records))
        best_value = summary.get("best_value")
        state.run.best_value = None if best_value is None else float(best_value)
        state.latest_best_x = dict(summary.get("best_x") or {})
        state.latest_finish_payload = {"state": state.run.phase}
        state.latest_history_path = str(summary.get("history_path") or "")
        state.latest_plot_path = str(summary.get("plot_path") or "")
        state.latest_result_plot_paths = dict(summary.get("result_plot_paths") or {})
        state.latest_result_output_dir = str(directory)

        self.view.clear_recent_evaluations()
        for record in records:
            self.append_recent_eval(record)
        self.populate_pareto_solution_table()
        self.populate_results_tree()
        self.update_results_summary_table()
        self.refresh_result_source()
        self.window.runtime_status_controller.sync_run_workspace(task)
        self.redraw_plots()
        if state.objective_dim > 1:
            index = self.window.ui.tabWidget_resultsViews.indexOf(self.window.ui.tab_pareto)
            if index >= 0:
                self.window.ui.tabWidget_resultsViews.setCurrentIndex(index)
        self.view.status_message(f"Archived run loaded: {directory.name}", 5000)

    def _read_evaluation_archive(self, path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            return []
        records: list[dict[str, Any]] = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError as exc:
                raise ValueError(f"Invalid evaluations.jsonl line {line_number}: {exc}") from exc
            if isinstance(record, dict):
                records.append(record)
        return records

    def _result_artifact_stem(self) -> str:
        state_task = self.window.state.latest_task_snapshot or {}
        raw_name = str(state_task.get("task_name") or self.window.task_ui.lineEdit_taskName.text() or "task")
        stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw_name.strip()).strip("._")
        return stem or "task"
