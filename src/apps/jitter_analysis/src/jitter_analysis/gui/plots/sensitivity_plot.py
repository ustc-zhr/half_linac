from __future__ import annotations

import math

try:
    from PyQt5 import QtCore, QtWidgets
except ImportError:  # pragma: no cover - optional runtime dependency
    QtCore = None
    QtWidgets = None

try:
    import pyqtgraph as pg
except ImportError:  # pragma: no cover - optional runtime dependency
    pg = None


if QtWidgets is not None:

    class SensitivityPlot(QtWidgets.QWidget):
        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            self._rows = []
            self._knob_axis_label = "Knob Value"
            self._axis_summary_text = ""
            self._scatter_item = None
            layout = QtWidgets.QVBoxLayout(self)

            self.summary_label = QtWidgets.QLabel(
                "Single Knob sensitivity fits the mean read PV response versus the selected knob axis for each step."
            )
            self.summary_label.setWordWrap(True)
            layout.addWidget(self.summary_label)

            self.table = QtWidgets.QTableWidget(0, 9, self)
            self.table.setHorizontalHeaderLabels(
                ["PV", "Points", "Knob Span", "Resp Span", "Slope", "Intercept", "r", "R^2", "Unit"]
            )
            self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
            self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
            self.table.setAlternatingRowColors(True)
            self.table.verticalHeader().setVisible(False)
            self.table.horizontalHeader().setStretchLastSection(True)
            layout.addWidget(self.table, 1)

            self.detail_label = QtWidgets.QLabel("Select a PV row and hover a point to inspect the sensitivity fit.")
            self.detail_label.setWordWrap(True)
            layout.addWidget(self.detail_label)

            if pg is not None:
                self.plot_widget = pg.PlotWidget(title="Single-Knob Sensitivity Fit")
                self.plot_widget.setLabel("bottom", "Knob Value")
                self.plot_widget.setLabel("left", "Mean Read PV Value")
                self.plot_widget.showGrid(x=True, y=True, alpha=0.2)
                layout.addWidget(self.plot_widget, 1)
            else:
                self.plot_widget = None
                layout.addWidget(QtWidgets.QLabel("pyqtgraph is not installed"))

            self.table.currentCellChanged.connect(self._on_current_cell_changed)

        def clear_data(self, message: str) -> None:
            self._rows = []
            self._axis_summary_text = ""
            self.summary_label.setText(message)
            self.detail_label.setText("Select a PV row and hover a point to inspect the sensitivity fit.")
            self.table.clearContents()
            self.table.setRowCount(0)
            self._scatter_item = None
            if self.plot_widget is not None:
                self.plot_widget.clear()
                self.plot_widget.setTitle("Single-Knob Sensitivity Fit")
                self.plot_widget.setLabel("bottom", self._knob_axis_label)
                self.plot_widget.setLabel("left", "Mean Read PV Value")

        def set_rows(self, rows, knob_name: str = "", knob_unit: str = "", axis_summary_text: str = "") -> None:
            self._rows = list(rows)
            axis_label = knob_name.strip() or "Knob Value"
            if knob_unit.strip():
                axis_label = f"{axis_label} [{knob_unit}]"
            self._knob_axis_label = axis_label
            self._axis_summary_text = axis_summary_text.strip()

            self.table.clearContents()
            self.table.setRowCount(len(self._rows))
            if not self._rows:
                self.clear_data(
                    "Need at least two valid Single Knob step points per read PV to fit sensitivity."
                )
                return

            strongest_row_index = 0
            strongest_slope = -1.0
            for row_index, row in enumerate(self._rows):
                values = [
                    str(row["name"]),
                    str(row["point_count"]),
                    f"{float(row['knob_span']):.6g}",
                    f"{float(row['response_span']):.6g}",
                    f"{float(row['slope']):.6g}",
                    f"{float(row['intercept']):.6g}",
                    "--" if math.isnan(float(row["correlation"])) else f"{float(row['correlation']):.4g}",
                    f"{float(row['r_squared']):.4g}",
                    str(row["slope_unit"]),
                ]
                slope_abs = abs(float(row["slope"]))
                if slope_abs > strongest_slope:
                    strongest_slope = slope_abs
                    strongest_row_index = row_index
                for col_index, value in enumerate(values):
                    self.table.setItem(row_index, col_index, QtWidgets.QTableWidgetItem(value))

            self.table.resizeColumnsToContents()
            strongest_row = self._rows[strongest_row_index]
            direction = "positive" if float(strongest_row["slope"]) >= 0.0 else "negative"
            summary = (
                f"Strongest fitted slope: {strongest_row['name']} = {float(strongest_row['slope']):.6g}"
                f" ({direction}), R^2={float(strongest_row['r_squared']):.4g}."
            )
            if self._axis_summary_text:
                summary = f"{self._axis_summary_text} {summary}"
            self.summary_label.setText(summary)
            blockers = [QtCore.QSignalBlocker(self.table)]
            try:
                self.table.setCurrentCell(strongest_row_index, 0)
            finally:
                del blockers
            self._show_row(strongest_row_index)

        def _on_current_cell_changed(self, current_row: int, _current_col: int, _old_row: int, _old_col: int) -> None:
            self._show_row(current_row)

        def _show_row(self, row_index: int) -> None:
            if row_index < 0 or row_index >= len(self._rows):
                return
            row = self._rows[row_index]
            step_indices = list(row["step_indices"])
            knob_values = list(row["knob_values"])
            response_values = list(row["response_values"])
            correlation_value = float(row["correlation"])
            correlation_text = "--" if math.isnan(correlation_value) else f"{correlation_value:.4g}"
            slope_unit = str(row["slope_unit"])
            slope_text = f"{float(row['slope']):.6g}"
            if slope_unit:
                slope_text = f"{slope_text} {slope_unit}"
            self.detail_label.setText(
                f"{row['name']}: slope={slope_text}, intercept={float(row['intercept']):.6g}, "
                f"r={correlation_text}, R^2={float(row['r_squared']):.4g}, points={int(row['point_count'])}."
            )

            if self.plot_widget is None:
                return

            self.plot_widget.clear()
            self.plot_widget.setTitle(f"{row['name']} Single-Knob Sensitivity Fit")
            self.plot_widget.setLabel("bottom", self._knob_axis_label)
            left_label = str(row["name"])
            if str(row["unit"]).strip():
                left_label = f"{left_label} [{row['unit']}]"
            self.plot_widget.setLabel("left", left_label)
            point_payloads = []
            for index, (step_index, knob_value, response_value) in enumerate(
                zip(step_indices, knob_values, response_values)
            ):
                point_payloads.append(
                    {
                        "step_index": int(step_index),
                        "knob_value": float(knob_value),
                        "response_value": float(response_value),
                        "pv_name": str(row["name"]),
                        "point_index": index,
                    }
                )
            self._scatter_item = pg.ScatterPlotItem(
                knob_values,
                response_values,
                data=point_payloads,
                pen=pg.mkPen("#2563eb", width=1),
                brush=pg.mkBrush("#2563eb"),
                size=8,
                hoverable=True,
                hoverSymbol="o",
                hoverSize=10,
                hoverPen=pg.mkPen("#1d4ed8", width=2),
                hoverBrush=pg.mkBrush("#60a5fa"),
                tip=self._point_tip,
            )
            self.plot_widget.addItem(self._scatter_item)
            if len(knob_values) >= 2:
                x_min = float(min(knob_values))
                x_max = float(max(knob_values))
                if x_max > x_min:
                    slope = float(row["slope"])
                    intercept = float(row["intercept"])
                    self.plot_widget.plot(
                        [x_min, x_max],
                        [slope * x_min + intercept, slope * x_max + intercept],
                        pen=pg.mkPen(color="#dc2626", width=2),
                    )

        @staticmethod
        def _point_tip(x_value, y_value, data) -> str:
            if not isinstance(data, dict):
                return f"Knob = {float(x_value):.6g}\nMean response = {float(y_value):.6g}"
            return (
                f"Step {int(data['step_index']) + 1}\n"
                f"Knob = {float(data['knob_value']):.6g}\n"
                f"Mean response = {float(data['response_value']):.6g}"
            )

else:

    class SensitivityPlot:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create SensitivityPlot")
