from __future__ import annotations

import math

from .theme import style_plot_widget

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

    class ResponseMapPlot(QtWidgets.QWidget):
        """Two-control-PV grid response map using point means."""

        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            self._rows_by_pv = {}
            self._pv_names = {}

            layout = QtWidgets.QVBoxLayout(self)
            controls = QtWidgets.QHBoxLayout()
            label = QtWidgets.QLabel("Response PV")
            label.setProperty("role", "field")
            self.channel_combo = QtWidgets.QComboBox()
            self.channel_combo.setMinimumWidth(240)
            self.summary_label = QtWidgets.QLabel(
                "Run a two-knob Grid scan to view a response surface."
            )
            self.summary_label.setWordWrap(True)
            controls.addWidget(label)
            controls.addWidget(self.channel_combo)
            controls.addSpacing(8)
            controls.addWidget(self.summary_label, 1)
            layout.addLayout(controls)

            if pg is not None:
                self.plot_widget = pg.PlotWidget(title="Grid Response Map")
                self.plot_widget.showGrid(x=True, y=True, alpha=0.2)
                self.scatter = pg.ScatterPlotItem(pxMode=True)
                self.plot_widget.addItem(self.scatter)
                layout.addWidget(self.plot_widget, 1)
            else:
                self.plot_widget = None
                self.scatter = None
                layout.addWidget(QtWidgets.QLabel("pyqtgraph is not installed"))
            self.channel_combo.currentIndexChanged.connect(self._show_selected_channel)

        def clear_data(self, message: str) -> None:
            self._rows_by_pv = {}
            self._pv_names = {}
            self.summary_label.setText(message)
            self.channel_combo.clear()
            if self.scatter is not None:
                self.scatter.setData([])

        def set_data(
            self,
            step_records,
            *,
            x_knob_id: str,
            y_knob_id: str,
            x_name: str,
            y_name: str,
            x_unit: str = "",
            y_unit: str = "",
            objects=(),
        ) -> None:
            rows_by_pv = {}
            for step in step_records:
                x_value = step.target_values.get(x_knob_id)
                y_value = step.target_values.get(y_knob_id)
                if x_value is None or y_value is None:
                    continue
                grouped = {}
                for sample in step.samples:
                    if math.isfinite(sample.value):
                        grouped.setdefault(sample.pv_id, []).append(float(sample.value))
                for pv_id, values in grouped.items():
                    if values:
                        rows_by_pv.setdefault(pv_id, []).append(
                            (float(x_value), float(y_value), sum(values) / len(values))
                        )

            self._rows_by_pv = rows_by_pv
            self._pv_names = {obj.id: obj.name for obj in objects}
            selected_id = self.channel_combo.currentData()
            blocker = QtCore.QSignalBlocker(self.channel_combo)
            self.channel_combo.clear()
            ordered_ids = [obj.id for obj in objects if obj.id in rows_by_pv]
            ordered_ids.extend(pv_id for pv_id in rows_by_pv if pv_id not in ordered_ids)
            for pv_id in ordered_ids:
                self.channel_combo.addItem(self._pv_names.get(pv_id, pv_id), pv_id)
            selected_index = self.channel_combo.findData(selected_id)
            self.channel_combo.setCurrentIndex(selected_index if selected_index >= 0 else 0)
            del blocker

            if self.plot_widget is not None:
                style_plot_widget(self.plot_widget)
                self.plot_widget.setLabel("bottom", self._axis_label(x_name, x_unit))
                self.plot_widget.setLabel("left", self._axis_label(y_name, y_unit))
            self._show_selected_channel()

        def _show_selected_channel(self, *_args) -> None:
            pv_id = self.channel_combo.currentData()
            rows = list(self._rows_by_pv.get(pv_id, []))
            name = self._pv_names.get(str(pv_id), str(pv_id or "Response"))
            if not rows:
                self.summary_label.setText("No valid point means are available for the selected response PV.")
                if self.scatter is not None:
                    self.scatter.setData([])
                return
            values = [row[2] for row in rows]
            low = min(values)
            high = max(values)
            self.summary_label.setText(
                f"{name} · {len(rows)} grid points · response {low:.6g} to {high:.6g}"
            )
            if self.scatter is None:
                return
            span = high - low
            spots = []
            for x_value, y_value, response in rows:
                fraction = 0.5 if span <= 0 else (response - low) / span
                color = self._response_color(fraction)
                spots.append(
                    {
                        "pos": (x_value, y_value),
                        "size": 18,
                        "symbol": "s",
                        "brush": pg.mkBrush(color),
                        "pen": pg.mkPen(color, width=1),
                        "data": {
                            "response": response,
                            "x": x_value,
                            "y": y_value,
                        },
                    }
                )
            self.scatter.setData(spots=spots, hoverable=True)
            self.plot_widget.setTitle(f"{name} · Grid Response Map")
            self.plot_widget.enableAutoRange()

        @staticmethod
        def _axis_label(name: str, unit: str) -> str:
            return f"{name} [{unit}]" if unit else name

        @staticmethod
        def _response_color(fraction: float):
            fraction = min(max(float(fraction), 0.0), 1.0)
            # Dark blue → cyan → warm yellow, readable on both application themes.
            if fraction <= 0.5:
                local = fraction * 2.0
                start = (49, 72, 152)
                end = (69, 208, 188)
            else:
                local = (fraction - 0.5) * 2.0
                start = (69, 208, 188)
                end = (244, 190, 74)
            return QtGui.QColor(
                *(round(start[index] + (end[index] - start[index]) * local) for index in range(3))
            )


else:

    class ResponseMapPlot:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create ResponseMapPlot")
