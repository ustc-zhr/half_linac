from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PyQt5.QtCore import QUrl, Qt
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
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


class ResultsController:
    def __init__(self, window: "MainWindow", canvas_class) -> None:
        self.window = window
        self.view = window.view_adapter
        self.canvas_class = canvas_class

    def init_results_page(self) -> None:
        tree = self.window.ui.treeWidget_runList
        tree.setColumnCount(2)
        tree.setHeaderLabels(["Run / Artifact", "Path / Value"])
        tree.header().setStretchLastSection(True)
        self._ensure_pareto_solution_controls()
        self.populate_results_tree()
        self.update_results_summary_table()

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
        layout = frame.layout()
        if layout is None:
            layout = QVBoxLayout(frame)
            layout.setContentsMargins(4, 4, 4, 4)
        else:
            layout.setContentsMargins(4, 4, 4, 4)
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
        group_layout = QVBoxLayout(group)
        hint = QLabel(
            "Select one Pareto solution to inspect its objectives, constraints and machine setpoints.",
            group,
        )
        hint.setWordWrap(True)
        group_layout.addWidget(hint)

        table = QTableWidget(group)
        table.setObjectName("tableWidget_paretoSolutions")
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels(["Index", "Feasible", "Objectives", "Constraints", "Variables"])
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.itemSelectionChanged.connect(self.on_pareto_solution_selection_changed)
        table.horizontalHeader().setStretchLastSection(True)
        group_layout.addWidget(table)

        actions = QHBoxLayout()
        button = QPushButton("Write Selected Pareto Point to Machine", group)
        button.setObjectName("pushButton_writeSelectedPareto")
        button.setEnabled(False)
        button.clicked.connect(self.window.set_selected_pareto_to_machine)
        actions.addStretch(1)
        actions.addWidget(button)
        group_layout.addLayout(actions)

        layout.addWidget(group)
        self.window.ui.groupBox_paretoSolutions = group
        self.window.ui.label_paretoSolutionsHint = hint
        self.window.ui.tableWidget_paretoSolutions = table
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
        self.draw_objective_plot(self.window.obj_canvas, title="Objective History")
        self.draw_pareto_plot(self.window.pareto_canvas, title="Pareto / HV")
        self.draw_objective_plot(self.window.results_conv_canvas, title="Convergence")
        self.draw_pareto_plot(self.window.results_pareto_canvas, title="Final Pareto")
        if hasattr(self.window, "run_constraints_canvas"):
            self.window.run_constraints_canvas.clear_with_message(
                "Constraint History",
                "Constraint detail is summarized in the evaluation tables for the current GUI flow.",
            )
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
        canvas.draw_idle()

    def draw_pareto_plot(self, canvas, *, title: str) -> None:
        state = self.window.state
        if state.objective_dim == 1:
            canvas.clear_with_message(title, "Pareto scatter is available for multi-objective tasks.")
            return
        points = state.pareto_points
        if "final" in title.lower() and state.pareto_front_points:
            points = state.pareto_front_points
        if not points:
            canvas.clear_with_message(title, "No multi-objective evaluations yet.")
            return

        canvas.figure.clear()
        ax = canvas.figure.add_subplot(111)
        xs = [p[0] for p in points if len(p) >= 2]
        ys = [p[1] for p in points if len(p) >= 2]
        ax.scatter(xs, ys)
        ax.set_title(title)
        ax.set_xlabel("f0")
        ax.set_ylabel("f1")
        ax.grid(True, alpha=0.3)
        canvas.draw_idle()

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

        self.view.set_table_row(inspector, 0, ["Run", self.window.task_ui.lineEdit_taskName.text()])
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

        for table in self.view.living_tables(
            self.window.run_ui.tableWidget_recent,
            getattr(self.window.ui, "tableWidget_recentEvaluations", None),
        ):
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
        table.resizeColumnsToContents()
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

    def show_pareto_solution_details(self, solution: dict[str, Any]) -> None:
        inspector = getattr(self.window.ui, "tableWidget_solutionInspector", None)
        if not self.view.qobj_alive(inspector):
            return
        rows = [
            ("Selected Pareto", str(solution.get("index", "--"))),
            ("Feasible", "yes" if solution.get("feasible", True) else "no"),
            ("Objectives", self._format_vector("f", solution.get("y", []))),
            ("Constraints", self._format_vector("c", solution.get("constraints", []))),
            ("Point", self.summarize_x_values(solution.get("x", {}))),
        ]
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
        button.setEnabled(bool(solution and feasible and is_online))
        if not solution:
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

        run_task = (state.latest_task_snapshot or {}).get(
            "task_name",
            self.window.task_ui.lineEdit_taskName.text().strip() or "untitled_task",
        )
        run_state = (
            state.latest_finish_payload.get("state", state.run.phase)
            if state.latest_finish_payload
            else state.run.phase
        )
        run_item = QTreeWidgetItem([f"Run: {run_task}", run_state])
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

        tree.expandAll()
        if tree.topLevelItemCount() > 0 and tree.currentItem() is None:
            tree.setCurrentItem(run_item)

    def update_results_summary_table(self, selected_item=None) -> None:
        state = self.window.state
        table = self.window.ui.tableWidget_solutionInspector
        rows = []
        task_name = (state.latest_task_snapshot or {}).get(
            "task_name",
            self.window.task_ui.lineEdit_taskName.text().strip() or "untitled_task",
        )
        rows.append(("Task", task_name))
        rows.append(
            (
                "Status",
                state.latest_finish_payload.get("state", state.run.phase)
                if state.latest_finish_payload
                else state.run.phase,
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
        state.latest_task_snapshot = dict(task)
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

    def update_results_after_evaluation(self, payload: dict) -> None:
        state = self.window.state
        state.latest_eval_payload = dict(payload)
        if payload.get("best_changed"):
            state.latest_best_x = dict(payload.get("x_values", {}))
        self.populate_results_tree()
        self.update_results_summary_table()

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
        saved: dict[str, str] = {}
        for label, canvas in canvases.items():
            if canvas is None:
                continue
            path = target_dir / f"{stem}_{suffixes[label]}.png"
            canvas.figure.savefig(str(path), dpi=160, bbox_inches="tight")
            saved[label] = str(path)
        state.latest_result_plot_paths = saved
        if saved:
            state.latest_result_output_dir = str(target_dir)
        self.populate_results_tree()
        self.update_results_summary_table()
        return saved

    def _result_artifact_stem(self) -> str:
        state_task = self.window.state.latest_task_snapshot or {}
        raw_name = str(state_task.get("task_name") or self.window.task_ui.lineEdit_taskName.text() or "task")
        stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw_name.strip()).strip("._")
        return stem or "task"
