from __future__ import annotations

import math

from .theme import style_plot_widget

try:
    from PyQt5 import QtWidgets
except ImportError:  # pragma: no cover - optional runtime dependency
    QtWidgets = None

try:
    import pyqtgraph as pg
except ImportError:  # pragma: no cover - optional runtime dependency
    pg = None


if QtWidgets is not None:

    class ResponsePlot(QtWidgets.QWidget):
        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            layout = QtWidgets.QVBoxLayout(self)
            self._curves = {}
            self._grouped_data = {}
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

        def reset_channels(self, knob_name: str, knob_unit: str, objects) -> None:
            self._curves = {}
            self._grouped_data = {}
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
                self._curves[obj.id] = curve
                self._grouped_data[obj.id] = {}
            style_plot_widget(self.plot_widget)

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
                mean_value = sum(clean) / len(clean)
                key = float(knob_value if group_key is None else group_key)
                groups = self._grouped_data.setdefault(pv_id, {})
                entry = groups.setdefault(key, {"x": [], "y": []})
                entry["x"].append(float(knob_value))
                entry["y"].append(float(mean_value))
                plotted_rows = sorted(
                    (
                        sum(entry["x"]) / len(entry["x"]),
                        sum(entry["y"]) / len(entry["y"]),
                    )
                    for entry in groups.values()
                    if entry["x"] and entry["y"]
                )
                self._curves[pv_id].setData(
                    [row[0] for row in plotted_rows],
                    [row[1] for row in plotted_rows],
                )

else:

    class ResponsePlot:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create ResponsePlot")
