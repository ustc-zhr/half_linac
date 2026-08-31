from __future__ import annotations

import math

try:
    from PyQt5 import QtCore, QtGui, QtWidgets
except ImportError:  # pragma: no cover - optional runtime dependency
    QtCore = None
    QtGui = None
    QtWidgets = None

try:
    import pyqtgraph as pg
except ImportError:  # pragma: no cover - optional runtime dependency
    pg = None


if QtWidgets is not None:

    class InfluencePlot(QtWidgets.QWidget):
        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            self._rows = []
            self._knob_ids = []
            self._knob_names = {}

            layout = QtWidgets.QVBoxLayout(self)
            self.summary_label = QtWidgets.QLabel(
                "Run Multi-Knob to estimate each control PV's influence on the selected read PVs."
            )
            self.summary_label.setWordWrap(True)
            layout.addWidget(self.summary_label)

            self.overview_table = QtWidgets.QTableWidget(0, 5, self)
            self.overview_table.setHorizontalHeaderLabels(
                ["Read PV", "Strongest Knob", "Influence", "R^2", "Resp Span"]
            )
            self._configure_table(self.overview_table)
            overview_header = self.overview_table.horizontalHeader()
            overview_header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
            overview_header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
            for column in range(2, 5):
                overview_header.setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeToContents)
            self.overview_table.setMaximumHeight(210)
            self.overview_table.setSizePolicy(
                QtWidgets.QSizePolicy.Expanding,
                QtWidgets.QSizePolicy.Fixed,
            )
            layout.addWidget(self.overview_table)

            matrix_label = QtWidgets.QLabel("Standardized Influence · Read PV × Control PV")
            matrix_label.setProperty("role", "formSectionTitle")
            layout.addWidget(matrix_label)
            self.matrix_table = QtWidgets.QTableWidget(0, 0, self)
            self._configure_table(self.matrix_table)
            self.matrix_table.horizontalHeader().setSectionResizeMode(
                QtWidgets.QHeaderView.ResizeToContents
            )
            self.matrix_table.verticalHeader().setSectionResizeMode(
                QtWidgets.QHeaderView.ResizeToContents
            )
            layout.addWidget(self.matrix_table, 1)

            self.detail_label = QtWidgets.QLabel("Select a matrix cell to inspect an influence coefficient.")
            self.detail_label.setWordWrap(True)
            layout.addWidget(self.detail_label)

            if pg is not None:
                self.fit_plot = pg.PlotWidget(title="Model Fit")
                self.fit_plot.setLabel("bottom", "Measured Point Mean")
                self.fit_plot.setLabel("left", "Predicted Point Mean")
                self.fit_plot.showGrid(x=True, y=True, alpha=0.2)
                layout.addWidget(self.fit_plot, 1)
            else:
                self.fit_plot = None
                layout.addWidget(QtWidgets.QLabel("pyqtgraph is not installed"))

            self.overview_table.currentCellChanged.connect(self._on_overview_changed)
            self.matrix_table.currentCellChanged.connect(self._on_matrix_changed)

        @staticmethod
        def _configure_table(table) -> None:
            table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
            table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
            table.setAlternatingRowColors(True)
            table.verticalHeader().setVisible(False)
            table.horizontalHeader().setStretchLastSection(False)

        def clear_data(self, message: str) -> None:
            self._rows = []
            self._knob_ids = []
            self._knob_names = {}
            self.summary_label.setText(message)
            self.overview_table.clearContents()
            self.overview_table.setRowCount(0)
            self.matrix_table.clear()
            self.matrix_table.setRowCount(0)
            self.matrix_table.setColumnCount(0)
            self.detail_label.setText("Select a matrix cell to inspect an influence coefficient.")
            if self.fit_plot is not None:
                self.fit_plot.clear()
                self.fit_plot.setTitle("Model Fit")

        def set_rows(self, rows, knob_ids, knob_names) -> None:
            self._rows = list(rows)
            self._knob_ids = list(knob_ids)
            self._knob_names = dict(knob_names)
            if not self._rows or not self._knob_ids:
                self.clear_data("Not enough valid Multi-Knob points to estimate influence.")
                return

            warning_count = sum(bool(row.get("warnings")) for row in self._rows)
            summary = (
                f"{len(self._rows)} read PV model(s) · {len(self._knob_ids)} control PV(s). "
                "Influence values are standardized coefficients; sign shows response direction."
            )
            if warning_count:
                summary += f" {warning_count} model(s) have data-quality warnings."
            self.summary_label.setText(summary)

            self.overview_table.setRowCount(len(self._rows))
            self.overview_table.setFixedHeight(min(210, 38 + 31 * len(self._rows)))
            self.matrix_table.clear()
            self.matrix_table.setRowCount(len(self._rows))
            self.matrix_table.setColumnCount(len(self._knob_ids))
            self.matrix_table.setHorizontalHeaderLabels(
                [self._knob_names.get(knob_id, knob_id) for knob_id in self._knob_ids]
            )
            self.matrix_table.setVerticalHeaderLabels([str(row["name"]) for row in self._rows])
            self.matrix_table.verticalHeader().setVisible(True)

            for row_index, row in enumerate(self._rows):
                strongest = max(
                    row["coefficients"].values(),
                    key=lambda item: abs(float(item["standardized"])),
                )
                overview_values = [
                    str(row["name"]),
                    self._knob_names.get(strongest["knob_id"], strongest["knob_id"]),
                    f"{float(strongest['standardized']):.4g}",
                    f"{float(row['r_squared']):.4g}",
                    f"{float(row['response_span']):.6g}",
                ]
                for column, value in enumerate(overview_values):
                    self.overview_table.setItem(row_index, column, QtWidgets.QTableWidgetItem(value))

                for column, knob_id in enumerate(self._knob_ids):
                    coefficient = row["coefficients"][knob_id]
                    value = float(coefficient["standardized"])
                    item = QtWidgets.QTableWidgetItem(f"{value:+.3f}")
                    item.setTextAlignment(QtCore.Qt.AlignCenter)
                    cell_color = self._influence_color(value)
                    item.setBackground(cell_color)
                    item.setForeground(self._foreground_for_color(cell_color))
                    item.setToolTip(
                        f"Standardized: {value:.6g}\n"
                        f"Raw coefficient: {float(coefficient['raw']):.6g}\n"
                        f"Knob span: {float(coefficient['knob_span']):.6g}"
                    )
                    self.matrix_table.setItem(row_index, column, item)

            blockers = [
                QtCore.QSignalBlocker(self.overview_table),
                QtCore.QSignalBlocker(self.matrix_table),
            ]
            try:
                self.overview_table.setCurrentCell(0, 0)
                strongest_id = max(
                    self._rows[0]["coefficients"],
                    key=lambda knob_id: abs(
                        float(self._rows[0]["coefficients"][knob_id]["standardized"])
                    ),
                )
                self.matrix_table.setCurrentCell(0, self._knob_ids.index(strongest_id))
            finally:
                del blockers
            self._show_detail(0, self.matrix_table.currentColumn())

        def _on_overview_changed(self, row: int, _column: int, _old_row: int, _old_column: int) -> None:
            if row < 0 or row >= len(self._rows):
                return
            strongest_id = max(
                self._rows[row]["coefficients"],
                key=lambda knob_id: abs(
                    float(self._rows[row]["coefficients"][knob_id]["standardized"])
                ),
            )
            self.matrix_table.setCurrentCell(row, self._knob_ids.index(strongest_id))

        def _on_matrix_changed(self, row: int, column: int, _old_row: int, _old_column: int) -> None:
            if 0 <= row < self.overview_table.rowCount():
                blocker = QtCore.QSignalBlocker(self.overview_table)
                self.overview_table.setCurrentCell(row, 0)
                del blocker
            self._show_detail(row, column)

        def _show_detail(self, row_index: int, column: int) -> None:
            if not (0 <= row_index < len(self._rows) and 0 <= column < len(self._knob_ids)):
                return
            row = self._rows[row_index]
            knob_id = self._knob_ids[column]
            coefficient = row["coefficients"][knob_id]
            warnings = list(row.get("warnings", []))
            warning_text = " " + " ".join(warnings) if warnings else ""
            self.detail_label.setText(
                f"{row['name']} ← {self._knob_names.get(knob_id, knob_id)}: "
                f"standardized influence={float(coefficient['standardized']):.6g}, "
                f"raw coefficient={float(coefficient['raw']):.6g} {coefficient['unit']}, "
                f"R²={float(row['r_squared']):.4g}, points={int(row['point_count'])}."
                f"{warning_text}"
            )
            self._show_fit(row)

        def _show_fit(self, row) -> None:
            if self.fit_plot is None:
                return
            measured = [float(value) for value in row["response_values"]]
            predicted = [float(value) for value in row["predicted_values"]]
            self.fit_plot.clear()
            self.fit_plot.setTitle(f"{row['name']} · Measured vs Predicted")
            self.fit_plot.setLabel("bottom", "Measured Point Mean")
            self.fit_plot.setLabel("left", "Predicted Point Mean")
            self.fit_plot.plot(
                measured,
                predicted,
                pen=None,
                symbol="o",
                symbolSize=7,
                symbolBrush="#45d0bc",
            )
            finite = [value for value in measured + predicted if math.isfinite(value)]
            if finite:
                lower = min(finite)
                upper = max(finite)
                if upper > lower:
                    self.fit_plot.plot([lower, upper], [lower, upper], pen=pg.mkPen("#8a929a", width=1))

        def _influence_color(self, value: float):
            strength = min(abs(float(value)), 1.0)
            base = self.palette().color(QtGui.QPalette.Base)
            target = QtGui.QColor("#45d0bc" if value >= 0 else "#ff6b6b")
            mix = 0.18 + 0.62 * strength
            return QtGui.QColor(
                int(base.red() * (1.0 - mix) + target.red() * mix),
                int(base.green() * (1.0 - mix) + target.green() * mix),
                int(base.blue() * (1.0 - mix) + target.blue() * mix),
            )

        @staticmethod
        def _foreground_for_color(color):
            luminance = 0.2126 * color.redF() + 0.7152 * color.greenF() + 0.0722 * color.blueF()
            return QtGui.QBrush(QtGui.QColor("#102033" if luminance > 0.52 else "#f3f8fb"))

else:

    class InfluencePlot:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create InfluencePlot")
