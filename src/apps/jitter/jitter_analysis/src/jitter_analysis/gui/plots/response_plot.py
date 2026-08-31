from __future__ import annotations

import math

from .theme import style_plot_widget

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

    class ResponsePlot(QtWidgets.QWidget):
        def __init__(self, parent=None, *, show_channel_selector: bool = False) -> None:
            super().__init__(parent)
            layout = QtWidgets.QVBoxLayout(self)
            self._curves = {}
            self._error_items = {}
            self._grouped_data = {}
            self._show_channel_selector = bool(show_channel_selector)

            self.channel_controls = QtWidgets.QWidget()
            controls = QtWidgets.QHBoxLayout(self.channel_controls)
            controls.setContentsMargins(0, 0, 0, 0)
            controls.setSpacing(8)
            channel_label = QtWidgets.QLabel("Response PV")
            channel_label.setProperty("role", "field")
            self.channel_combo = QtWidgets.QComboBox()
            self.channel_combo.setMinimumWidth(240)
            controls.addWidget(channel_label)
            controls.addWidget(self.channel_combo)
            controls.addStretch(1)
            self.channel_controls.setVisible(self._show_channel_selector)
            layout.addWidget(self.channel_controls)
            if pg is not None:
                self.plot_widget = pg.PlotWidget(title="Response")
                self.plot_widget.addLegend()
                self.plot_widget.setLabel("bottom", "Scan Axis")
                self.plot_widget.setLabel("left", "Mean Read PV Value")
                self.plot_widget.showGrid(x=True, y=True, alpha=0.2)
                layout.addWidget(self.plot_widget)
            else:
                self.plot_widget = None
                layout.addWidget(QtWidgets.QLabel("pyqtgraph is not installed"))
            self.channel_combo.currentIndexChanged.connect(self._update_channel_visibility)

        def reset_channels(self, knob_name: str, knob_unit: str, objects) -> None:
            objects = list(objects)
            self._curves = {}
            self._error_items = {}
            self._grouped_data = {}
            selected_id = self.channel_combo.currentData()
            blocker = QtCore.QSignalBlocker(self.channel_combo)
            self.channel_combo.clear()
            for obj in objects:
                self.channel_combo.addItem(obj.name, obj.id)
            selected_index = self.channel_combo.findData(selected_id)
            self.channel_combo.setCurrentIndex(selected_index if selected_index >= 0 else 0)
            del blocker
            if self.plot_widget is None:
                return

            self.plot_widget.clear()
            self.plot_widget.addLegend()
            style_plot_widget(self.plot_widget)
            axis_label = knob_name.strip()
            if knob_unit.strip():
                axis_label = f"{axis_label} [{knob_unit}]".strip()
            self.plot_widget.setLabel("bottom", axis_label or "Scan Axis")
            self.plot_widget.setTitle(f"Response vs {axis_label or 'Scan Axis'}")
            for index, obj in enumerate(objects):
                color = pg.intColor(index, hues=max(len(objects), 1))
                curve = self.plot_widget.plot(
                    [],
                    [],
                    pen=pg.mkPen(color=color, width=2),
                    symbol="o",
                    symbolSize=6,
                    name=obj.name,
                )
                error_item = pg.ErrorBarItem(
                    x=[],
                    y=[],
                    height=[],
                    beam=0.0,
                    pen=pg.mkPen(color=color, width=1),
                )
                self.plot_widget.addItem(error_item)
                self._curves[obj.id] = curve
                self._error_items[obj.id] = error_item
                self._grouped_data[obj.id] = {}
            style_plot_widget(self.plot_widget)
            self._update_channel_visibility()

        def append_step(self, knob_value: float, samples, group_key: float | None = None) -> None:
            if self.plot_widget is None:
                return

            grouped = {}
            for sample in samples:
                grouped.setdefault(sample.pv_id, []).append(sample.value)

            for pv_id, values in grouped.items():
                clean = [value for value in values if not math.isnan(value)]
                if not clean or pv_id not in self._curves:
                    continue
                key = float(knob_value if group_key is None else group_key)
                groups = self._grouped_data.setdefault(pv_id, {})
                entry = groups.setdefault(key, {"x": [], "values": []})
                entry["x"].append(float(knob_value))
                entry["values"].extend(float(value) for value in clean)
                plotted_rows = sorted(
                    (
                        sum(entry["x"]) / len(entry["x"]),
                        sum(entry["values"]) / len(entry["values"]),
                        self._sample_std(entry["values"]),
                    )
                    for entry in groups.values()
                    if entry["x"] and entry["values"]
                )
                self._curves[pv_id].setData(
                    [row[0] for row in plotted_rows],
                    [row[1] for row in plotted_rows],
                )
                self._error_items[pv_id].setData(
                    x=[row[0] for row in plotted_rows],
                    y=[row[1] for row in plotted_rows],
                    height=[2.0 * row[2] for row in plotted_rows],
                    beam=0.0,
                )

        def _update_channel_visibility(self) -> None:
            selected_id = self.channel_combo.currentData()
            for pv_id, curve in self._curves.items():
                visible = not self._show_channel_selector or pv_id == selected_id
                curve.setVisible(visible)
                error_item = self._error_items.get(pv_id)
                if error_item is not None:
                    error_item.setVisible(visible)

        @staticmethod
        def _sample_std(values) -> float:
            if len(values) < 2:
                return 0.0
            mean_value = sum(values) / len(values)
            variance = sum((value - mean_value) ** 2 for value in values) / (len(values) - 1)
            return math.sqrt(max(variance, 0.0))

else:

    class ResponsePlot:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create ResponsePlot")
