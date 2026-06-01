from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
import math

from .visibility import resolve_initial_visibility, slice_series_tail

try:
    from PyQt5 import QtWidgets
except ImportError:  # pragma: no cover - optional runtime dependency
    QtWidgets = None

try:
    import pyqtgraph as pg
except ImportError:  # pragma: no cover - optional runtime dependency
    pg = None


if QtWidgets is not None:

    if pg is not None:

        class TrendAxisItem(pg.AxisItem):
            def __init__(self, *args, **kwargs) -> None:
                super().__init__(*args, **kwargs)
                self._axis_mode = "sample_index"

            def set_axis_mode(self, axis_mode: str) -> None:
                token = str(axis_mode or "sample_index")
                if token == self._axis_mode:
                    return
                self._axis_mode = token
                self.picture = None
                self.update()

            def tickStrings(self, values, scale, spacing):
                if self._axis_mode == "elapsed_time":
                    return [self._format_elapsed_time(value, spacing) for value in values]
                if self._axis_mode == "clock_time":
                    return [self._format_clock_time(value, spacing) for value in values]
                return [self._format_sample_index(value) for value in values]

            @staticmethod
            def _format_sample_index(value: float) -> str:
                if not math.isfinite(value):
                    return ""
                rounded = round(value)
                if abs(value - rounded) < 1.0e-6:
                    return str(int(rounded))
                return f"{value:.3f}".rstrip("0").rstrip(".")

            @staticmethod
            def _format_elapsed_time(value: float, spacing: float) -> str:
                if not math.isfinite(value):
                    return ""
                if spacing < 0.1:
                    precision = 3
                elif spacing < 1.0:
                    precision = 2
                elif spacing < 10.0:
                    precision = 1
                else:
                    precision = 0
                return f"{value:.{precision}f}".rstrip("0").rstrip(".")

            @staticmethod
            def _format_clock_time(value: float, spacing: float) -> str:
                if not math.isfinite(value):
                    return ""
                timestamp = datetime.fromtimestamp(value)
                if spacing < 1.0:
                    return timestamp.strftime("%H:%M:%S.%f")[:-3]
                if spacing < 86_400.0:
                    return timestamp.strftime("%H:%M:%S")
                return timestamp.strftime("%m-%d %H:%M")

    else:
        TrendAxisItem = None

    DEFAULT_VISIBLE_TREND_SERIES = 8
    DEFAULT_TREND_RECENT_POINTS = 2000

    class TrendPlot(QtWidgets.QWidget):
        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            layout = QtWidgets.QVBoxLayout(self)
            self._curves = {}
            self._x_data = {}
            self._y_data = {}
            self._labels = {}
            self._pv_order = []
            self._series_visibility = {}
            self._sample_elapsed_seconds = []
            self._time_origin = None
            self._time_origin_epoch_seconds = None
            self._highlight_region = None
            self._highlight_range = None
            self._highlight_label = ""

            controls = QtWidgets.QHBoxLayout()
            controls.addWidget(QtWidgets.QLabel("Show"))
            self.series_button = QtWidgets.QToolButton(self)
            self.series_button.setText("Visible Variables")
            self.series_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
            self.series_menu = QtWidgets.QMenu(self.series_button)
            self.series_button.setMenu(self.series_menu)
            self.series_button.setEnabled(False)
            controls.addWidget(self.series_button, 0)
            controls.addWidget(QtWidgets.QLabel("X Axis"))
            self.axis_combo = QtWidgets.QComboBox(self)
            self.axis_combo.addItem("Sample Index", "sample_index")
            self.axis_combo.addItem("Elapsed Time", "elapsed_time")
            self.axis_combo.addItem("Clock Time", "clock_time")
            controls.addWidget(self.axis_combo, 0)
            controls.addWidget(QtWidgets.QLabel("Range"))
            self.range_combo = QtWidgets.QComboBox(self)
            self.range_combo.addItem("Recent Window", "recent")
            self.range_combo.addItem("Full History", "full")
            controls.addWidget(self.range_combo, 0)
            self.recent_points_spin = QtWidgets.QSpinBox(self)
            self.recent_points_spin.setRange(100, 1_000_000)
            self.recent_points_spin.setSingleStep(100)
            self.recent_points_spin.setValue(DEFAULT_TREND_RECENT_POINTS)
            self.recent_points_spin.setSuffix(" pts")
            self.recent_points_spin.setToolTip("How many recent samples to keep visible in trend view.")
            controls.addWidget(self.recent_points_spin, 0)
            controls.addStretch(1)
            layout.addLayout(controls)

            self.info_label = QtWidgets.QLabel("Trend is ready to display selected read PVs.")
            self.info_label.setWordWrap(True)
            layout.addWidget(self.info_label)

            if pg is not None:
                self._bottom_axis = TrendAxisItem(orientation="bottom") if TrendAxisItem is not None else None
                if self._bottom_axis is not None:
                    self.plot_widget = pg.PlotWidget(title="Trend", axisItems={"bottom": self._bottom_axis})
                else:
                    self.plot_widget = pg.PlotWidget(title="Trend")
                self.plot_widget.addLegend()
                self.plot_widget.setLabel("bottom", "Sample Index")
                self.plot_widget.setLabel("left", "Value")
                self.plot_widget.showGrid(x=True, y=True, alpha=0.2)
                layout.addWidget(self.plot_widget)
            else:
                self._bottom_axis = None
                self.plot_widget = None
                layout.addWidget(QtWidgets.QLabel("pyqtgraph is not installed"))

            self.axis_combo.currentIndexChanged.connect(self._handle_window_mode_changed)
            self.range_combo.currentIndexChanged.connect(self._handle_window_mode_changed)
            self.recent_points_spin.valueChanged.connect(self._handle_window_mode_changed)
            self._update_window_control_state()

        def reset_channels(self, objects) -> None:
            previous_visibility = dict(self._series_visibility)
            self._labels = {obj.id: obj.name for obj in objects}
            self._pv_order = [obj.id for obj in objects]
            self._x_data = {obj.id: [] for obj in objects}
            self._y_data = {obj.id: [] for obj in objects}
            self._sample_elapsed_seconds = []
            self._time_origin = None
            self._time_origin_epoch_seconds = None
            self._series_visibility = resolve_initial_visibility(
                self._pv_order,
                previous_visibility=previous_visibility,
                default_visible_count=DEFAULT_VISIBLE_TREND_SERIES,
            )
            self._highlight_region = None
            self._highlight_range = None
            self._highlight_label = ""
            self._rebuild_series_menu()
            self._rebuild_plot_items()

        def append_batch(self, sample_index: int, samples) -> None:
            for sample in samples:
                if sample.pv_id not in self._x_data:
                    continue
                recorded_index = getattr(sample, "batch_index", None)
                value_index = int(recorded_index) if recorded_index is not None else int(sample_index)
                self._record_sample_time(value_index, getattr(sample, "timestamp", None))
                self._x_data[sample.pv_id].append(value_index)
                self._y_data[sample.pv_id].append(sample.value)
                if sample.pv_id in self._curves:
                    x_values, y_values = self._display_series_data(sample.pv_id)
                    self._curves[sample.pv_id].setData(
                        x_values,
                        y_values,
                    )
            self._update_info_label()

        def set_series_history(
            self,
            series_history: Mapping[str, tuple[Sequence[float], Sequence[float]]],
            sample_timestamps: Sequence[datetime | None] | None = None,
        ) -> None:
            self._sample_elapsed_seconds = []
            self._time_origin = None
            self._time_origin_epoch_seconds = None
            if sample_timestamps is not None:
                for sample_index, timestamp in enumerate(sample_timestamps):
                    self._record_sample_time(sample_index, timestamp)
            for pv_id in self._pv_order:
                x_values, y_values = series_history.get(pv_id, ([], []))
                self._x_data[pv_id] = x_values if isinstance(x_values, list) else list(x_values)
                self._y_data[pv_id] = y_values if isinstance(y_values, list) else list(y_values)
            self._refresh_visible_curve_data()

        def _handle_window_mode_changed(self, *_args) -> None:
            self._update_window_control_state()
            self._refresh_visible_curve_data()

        def _update_window_control_state(self) -> None:
            recent_mode = str(self.range_combo.currentData() or "recent") == "recent"
            self.recent_points_spin.setEnabled(recent_mode)

        def _display_series_data(self, pv_id: str) -> tuple[Sequence[float], Sequence[float]]:
            axis_mode = self._axis_mode()
            x_values = self._x_data.get(pv_id, [])
            y_values = self._y_data.get(pv_id, [])
            if str(self.range_combo.currentData() or "recent") != "full":
                x_values, y_values = slice_series_tail(x_values, y_values, max_points=int(self.recent_points_spin.value()))
            if axis_mode != "sample_index":
                return [self._axis_value_for_index(int(item), axis_mode=axis_mode) for item in x_values], y_values
            return x_values, y_values

        def _refresh_visible_curve_data(self) -> None:
            self._update_axis_label()
            for pv_id, curve in self._curves.items():
                x_values, y_values = self._display_series_data(pv_id)
                curve.setData(x_values, y_values)
            if self._highlight_region is not None and self.plot_widget is not None:
                self.plot_widget.removeItem(self._highlight_region)
                self._highlight_region = None
            self._restore_highlight_region()
            self._update_info_label()

        def _rebuild_series_menu(self) -> None:
            self.series_menu.clear()
            self.series_button.setEnabled(bool(self._pv_order))
            if not self._pv_order:
                self.series_button.setText("Visible Variables")
                return

            show_all_action = self.series_menu.addAction("Show All")
            show_all_action.triggered.connect(lambda: self._set_all_series_visibility(True))
            hide_all_action = self.series_menu.addAction("Hide All")
            hide_all_action.triggered.connect(lambda: self._set_all_series_visibility(False))
            self.series_menu.addSeparator()

            visible_count = 0
            total_count = len(self._pv_order)
            for pv_id in self._pv_order:
                label = str(self._labels.get(pv_id, pv_id))
                visible = bool(self._series_visibility.get(pv_id, False))
                if visible:
                    visible_count += 1
                action = self.series_menu.addAction(label)
                action.setCheckable(True)
                action.setChecked(visible)
                action.toggled.connect(lambda checked, target_pv_id=pv_id: self._set_series_visibility(target_pv_id, checked))

            self.series_button.setText(f"Visible Variables ({visible_count}/{total_count})")
            self._update_info_label()

        def _set_series_visibility(self, pv_id: str, visible: bool) -> None:
            if pv_id not in self._series_visibility:
                return
            self._series_visibility[pv_id] = bool(visible)
            self._rebuild_series_menu()
            self._rebuild_plot_items()

        def _set_all_series_visibility(self, visible: bool) -> None:
            for pv_id in list(self._series_visibility.keys()):
                self._series_visibility[pv_id] = bool(visible)
            self._rebuild_series_menu()
            self._rebuild_plot_items()

        def _update_info_label(self) -> None:
            total_count = len(self._pv_order)
            visible_count = sum(1 for pv_id in self._pv_order if self._series_visibility.get(pv_id, False))
            recent_mode = str(self.range_combo.currentData() or "recent") == "recent"
            if total_count == 0:
                self.info_label.setText("Trend is ready to display selected read PVs.")
                return
            if visible_count == 0:
                self.info_label.setText(f"No variable is selected for trend display. Total available: {total_count}.")
                return
            total_points = max((len(self._x_data.get(pv_id, [])) for pv_id in self._pv_order), default=0)
            if recent_mode:
                visible_point_limit = int(self.recent_points_spin.value())
                displayed_points = min(total_points, visible_point_limit)
                range_text = f"latest {displayed_points}/{total_points} samples"
            else:
                range_text = f"full history ({total_points} samples)"
            self.info_label.setText(
                f"Showing {visible_count}/{total_count} variables in trend. View: {range_text}."
            )

        def _rebuild_plot_items(self) -> None:
            self._curves = {}
            if self.plot_widget is None:
                self._update_info_label()
                return

            self.plot_widget.clear()
            self.plot_widget.addLegend()
            self._update_axis_label()
            self.plot_widget.setLabel("left", "Value")
            self._apply_title()
            self._highlight_region = None

            for index, pv_id in enumerate(self._pv_order):
                if not self._series_visibility.get(pv_id, False):
                    continue
                color = pg.intColor(index, hues=max(len(self._pv_order), 1))
                x_values, y_values = self._display_series_data(pv_id)
                curve = self.plot_widget.plot(
                    x_values,
                    y_values,
                    pen=pg.mkPen(color=color, width=2),
                    name=self._labels.get(pv_id, pv_id),
                )
                self._curves[pv_id] = curve

            self._restore_highlight_region()
            self._update_info_label()

        def _apply_title(self) -> None:
            if self.plot_widget is None:
                return
            if self._highlight_label:
                self.plot_widget.setTitle(f"Trend | {self._highlight_label}")
            else:
                self.plot_widget.setTitle("Trend")

        def _restore_highlight_region(self) -> None:
            if self.plot_widget is None or self._highlight_range is None:
                return
            start_index, end_index = self._highlight_range
            start_value = self._axis_value_for_index(min(start_index, end_index))
            end_value = self._axis_value_for_index(max(start_index, end_index))
            padding = self._highlight_padding(min(start_index, end_index), max(start_index, end_index))
            left = float(min(start_value, end_value)) - padding
            right = float(max(start_value, end_value)) + padding
            region = pg.LinearRegionItem(
                values=(left, right),
                orientation="vertical",
                movable=False,
                brush=pg.mkBrush(251, 191, 36, 60),
                pen=pg.mkPen(color="#f59e0b", width=2),
            )
            region.setZValue(-5)
            self.plot_widget.addItem(region)
            self._highlight_region = region

        def clear_highlight(self) -> None:
            self._highlight_range = None
            self._highlight_label = ""
            if self._highlight_region is not None:
                if self.plot_widget is not None:
                    self.plot_widget.removeItem(self._highlight_region)
                self._highlight_region = None
            self._apply_title()

        def highlight_sample_range(self, start_index: int, end_index: int, label: str = "") -> None:
            self.clear_highlight()
            self._highlight_range = (int(start_index), int(end_index))
            self._highlight_label = str(label)
            self._apply_title()
            self._restore_highlight_region()

        def _update_axis_label(self) -> None:
            if self.plot_widget is None:
                return
            axis_mode = self._axis_mode()
            if self._bottom_axis is not None:
                self._bottom_axis.set_axis_mode(axis_mode)
            if axis_mode == "elapsed_time":
                self.plot_widget.setLabel("bottom", "Elapsed Time [s]")
            elif axis_mode == "clock_time":
                self.plot_widget.setLabel("bottom", "Clock Time")
            else:
                self.plot_widget.setLabel("bottom", "Sample Index")

        def _record_sample_time(self, sample_index: int, timestamp: datetime | None) -> None:
            if timestamp is None or sample_index < 0:
                return
            if self._time_origin is None:
                self._time_origin = timestamp
                self._time_origin_epoch_seconds = float(timestamp.timestamp())
            elapsed_seconds = (timestamp - self._time_origin).total_seconds()
            if sample_index >= len(self._sample_elapsed_seconds):
                self._sample_elapsed_seconds.extend([None] * (sample_index + 1 - len(self._sample_elapsed_seconds)))
            if self._sample_elapsed_seconds[sample_index] is None:
                self._sample_elapsed_seconds[sample_index] = float(elapsed_seconds)

        def _axis_value_for_index(self, sample_index: int, axis_mode: str | None = None) -> float:
            mode = str(axis_mode or self._axis_mode())
            if mode == "sample_index":
                return float(sample_index)
            if 0 <= sample_index < len(self._sample_elapsed_seconds):
                elapsed_seconds = self._sample_elapsed_seconds[sample_index]
                if elapsed_seconds is not None:
                    if mode == "clock_time" and self._time_origin_epoch_seconds is not None:
                        return float(self._time_origin_epoch_seconds + elapsed_seconds)
                    return float(elapsed_seconds)
            return float(sample_index)

        def _highlight_padding(self, start_index: int, end_index: int) -> float:
            if self._axis_mode() == "sample_index":
                return 0.35
            if start_index != end_index:
                return 0.0

            center_value = self._axis_value_for_index(start_index)
            neighbor_deltas = []
            for neighbor_index in (start_index - 1, start_index + 1):
                if neighbor_index < 0:
                    continue
                neighbor_value = self._axis_value_for_index(neighbor_index)
                delta = abs(neighbor_value - center_value)
                if delta > 0.0:
                    neighbor_deltas.append(delta)
            if neighbor_deltas:
                return min(neighbor_deltas) * 0.5
            return 0.001

        def _axis_mode(self) -> str:
            return str(self.axis_combo.currentData() or "sample_index")

else:

    class TrendPlot:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create TrendPlot")
