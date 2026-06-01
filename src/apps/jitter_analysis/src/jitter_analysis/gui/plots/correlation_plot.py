from __future__ import annotations

import math

try:
    from PyQt5 import QtCore, QtGui, QtWidgets
except ImportError:  # pragma: no cover - optional runtime dependency
    QtCore = None
    QtGui = None
    QtWidgets = None


if QtWidgets is not None:

    try:
        import numpy as np
    except ImportError:  # pragma: no cover - optional runtime dependency
        np = None

    try:
        import pyqtgraph as pg
    except ImportError:  # pragma: no cover - optional runtime dependency
        pg = None

    class CorrelationPlot(QtWidgets.QWidget):
        highlightRequested = QtCore.pyqtSignal(object)

        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            layout = QtWidgets.QVBoxLayout(self)
            self._names = []
            self._matrix = None
            self._valid_counts = None
            self._series_by_name = {}
            self._best_pair_index = None
            self._scatter_item = None

            self.summary_label = QtWidgets.QLabel(
                "Run a task with at least two read PVs to populate the correlation matrix."
            )
            self.summary_label.setWordWrap(True)
            layout.addWidget(self.summary_label)

            self.table = QtWidgets.QTableWidget(0, 0, self)
            self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            self.table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
            self.table.setAlternatingRowColors(True)
            self.table.verticalHeader().setVisible(True)
            self.table.horizontalHeader().setStretchLastSection(False)
            layout.addWidget(self.table, 1)

            self.scatter_summary_label = QtWidgets.QLabel(
                "Select a matrix cell to inspect the paired scatter."
            )
            self.scatter_summary_label.setWordWrap(True)
            layout.addWidget(self.scatter_summary_label)

            if pg is not None:
                self.scatter_plot = pg.PlotWidget(title="Correlation Scatter")
                self.scatter_plot.setLabel("bottom", "X")
                self.scatter_plot.setLabel("left", "Y")
                self.scatter_plot.showGrid(x=True, y=True, alpha=0.2)
                layout.addWidget(self.scatter_plot, 1)
            else:
                self.scatter_plot = None
                layout.addWidget(QtWidgets.QLabel("pyqtgraph is not installed"))

            self.table.currentCellChanged.connect(self._on_current_cell_changed)

        def clear_data(self, message: str) -> None:
            self._names = []
            self._matrix = None
            self._valid_counts = None
            self._series_by_name = {}
            self._best_pair_index = None
            self.summary_label.setText(message)
            self.scatter_summary_label.setText("Select a matrix cell to inspect the paired scatter.")
            self.table.clear()
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            if self.scatter_plot is not None:
                self.scatter_plot.clear()
                self.scatter_plot.setTitle("Correlation Scatter")
            self._scatter_item = None

        def set_matrix(self, names, matrix, valid_counts=None, series_by_name=None) -> None:
            self._names = list(names)
            self._matrix = matrix
            self._valid_counts = valid_counts
            self._series_by_name = dict(series_by_name or {})
            self.table.clear()
            self.table.setRowCount(len(names))
            self.table.setColumnCount(len(names))
            self.table.setHorizontalHeaderLabels(list(names))
            self.table.setVerticalHeaderLabels(list(names))

            best_pair = None
            best_abs_value = -1.0
            best_pair_index = None
            for row, row_name in enumerate(names):
                for col, col_name in enumerate(names):
                    value = float(matrix[row, col])
                    item = QtWidgets.QTableWidgetItem()
                    item.setTextAlignment(QtCore.Qt.AlignCenter)
                    if math.isnan(value):
                        item.setText("--")
                        item.setBackground(QtGui.QColor("#f0f0f0"))
                    else:
                        item.setText(f"{value:.3f}")
                        item.setBackground(self._cell_color(value, diagonal=row == col))
                        if row < col:
                            abs_value = abs(value)
                            if abs_value > best_abs_value:
                                best_abs_value = abs_value
                                best_pair = (row_name, col_name, value)
                                best_pair_index = (row, col)

                    if valid_counts is not None:
                        count = int(valid_counts[row, col])
                        item.setToolTip(f"Valid paired samples: {count}")
                    self.table.setItem(row, col, item)

            self.table.resizeColumnsToContents()
            self.table.resizeRowsToContents()

            if best_pair is None:
                self.summary_label.setText("Not enough valid paired samples to compute cross-correlation.")
                return

            left_name, right_name, value = best_pair
            sign = "positive" if value >= 0 else "negative"
            self.summary_label.setText(
                f"Strongest correlation: {left_name} vs {right_name} = {value:.3f} ({sign})."
            )
            self._best_pair_index = best_pair_index
            if best_pair_index is not None:
                blockers = [QtCore.QSignalBlocker(self.table)]
                try:
                    self.table.setCurrentCell(best_pair_index[0], best_pair_index[1])
                finally:
                    del blockers
                self._show_scatter_for_pair(best_pair_index[0], best_pair_index[1])

        def _cell_color(self, value: float, diagonal: bool = False):
            if diagonal:
                return QtGui.QColor("#dbeafe")
            strength = min(abs(value), 1.0)
            if value >= 0.0:
                red = int(245 - 80 * strength)
                green = int(250 - 50 * strength)
                blue = 255
                return QtGui.QColor(red, green, blue)
            red = 255
            green = int(245 - 70 * strength)
            blue = int(240 - 90 * strength)
            return QtGui.QColor(red, green, blue)

        def _on_current_cell_changed(self, current_row: int, current_col: int, _old_row: int, _old_col: int) -> None:
            self._show_scatter_for_pair(current_row, current_col)

        def _show_scatter_for_pair(self, row: int, col: int) -> None:
            if row < 0 or col < 0 or row >= len(self._names) or col >= len(self._names):
                return
            left_name = self._names[row]
            right_name = self._names[col]
            left_series = self._series_by_name.get(left_name)
            right_series = self._series_by_name.get(right_name)
            if left_series is None or right_series is None or np is None:
                self.scatter_summary_label.setText("Scatter data is not available for the selected pair.")
                return

            left_array = np.asarray(left_series["values"], dtype=float)
            right_array = np.asarray(right_series["values"], dtype=float)
            valid_mask = np.isfinite(left_array) & np.isfinite(right_array)
            paired_left = left_array[valid_mask]
            paired_right = right_array[valid_mask]
            valid_count = int(np.count_nonzero(valid_mask))
            sample_indices = np.asarray(left_series["sample_indices"], dtype=int)[valid_mask]
            step_indices = np.asarray(left_series["step_indices"], dtype=object)[valid_mask]

            if self.scatter_plot is not None:
                self.scatter_plot.clear()
                self.scatter_plot.setLabel("bottom", left_name)
                self.scatter_plot.setLabel("left", right_name)
                self.scatter_plot.setTitle(f"{left_name} vs {right_name}")
                self._scatter_item = None

            if valid_count < 2:
                self.scatter_summary_label.setText(
                    f"{left_name} vs {right_name}: not enough paired finite samples."
                )
                return

            correlation_value = float(self._matrix[row, col]) if self._matrix is not None else float("nan")
            corr_text = "--" if math.isnan(correlation_value) else f"{correlation_value:.3f}"
            self.scatter_summary_label.setText(
                f"{left_name} vs {right_name}: r={corr_text}, paired samples={valid_count}."
            )

            if self.scatter_plot is None:
                return

            color = "#2563eb" if row != col else "#0f766e"
            self._scatter_item = pg.ScatterPlotItem(
                paired_left,
                paired_right,
                pen=pg.mkPen(color=color, width=1),
                brush=pg.mkBrush(color),
                size=7,
                hoverable=True,
                hoverSymbol="o",
                hoverSize=9,
            )
            points = []
            for index, (x_value, y_value) in enumerate(zip(paired_left, paired_right)):
                payload = {
                    "sample_index": int(sample_indices[index]),
                    "step_index": None if step_indices[index] is None else int(step_indices[index]),
                    "label": f"{left_name} vs {right_name}",
                }
                points.append({"pos": (float(x_value), float(y_value)), "data": payload})
            self._scatter_item.setData(points)
            self._scatter_item.sigClicked.connect(self._on_scatter_clicked)
            self.scatter_plot.addItem(self._scatter_item)
            if row != col and valid_count >= 2:
                x_min = float(np.min(paired_left))
                x_max = float(np.max(paired_left))
                if x_max > x_min:
                    slope, intercept = np.polyfit(paired_left, paired_right, deg=1)
                    self.scatter_plot.plot(
                        [x_min, x_max],
                        [slope * x_min + intercept, slope * x_max + intercept],
                        pen=pg.mkPen(color="#dc2626", width=2),
                    )

        def _on_scatter_clicked(self, _item, points) -> None:
            if not points:
                return
            point = points[0]
            payload = point.data()
            if not isinstance(payload, dict):
                return
            self.highlightRequested.emit(dict(payload))

else:

    class CorrelationPlot:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create CorrelationPlot")
