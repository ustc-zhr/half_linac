from __future__ import annotations

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
    from ...analysis.waveform import downsample_waveform_minmax


if QtWidgets is not None:

    class WaveformPlot(QtWidgets.QWidget):
        viewChanged = QtCore.pyqtSignal()

        FEATURE_OPTIONS = [
            ("baseline_mean", "Baseline Mean"),
            ("peak_value", "Peak Value"),
            ("peak_time_sec", "Peak Time [s]"),
            ("integral", "Integral"),
            ("rms", "RMS"),
            ("peak_to_peak", "Peak-to-Peak"),
        ]

        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            self._preview_region = None
            self._feature_rows = []
            self._waveform_option_count = 0

            layout = QtWidgets.QVBoxLayout(self)
            controls = QtWidgets.QHBoxLayout()
            controls.addWidget(QtWidgets.QLabel("Primary"))
            self.primary_combo = QtWidgets.QComboBox(self)
            controls.addWidget(self.primary_combo, 1)
            controls.addWidget(QtWidgets.QLabel("Reference"))
            self.secondary_combo = QtWidgets.QComboBox(self)
            controls.addWidget(self.secondary_combo, 1)
            controls.addWidget(QtWidgets.QLabel("Shot"))
            self.shot_spin = QtWidgets.QSpinBox(self)
            self.shot_spin.setRange(1, 1)
            controls.addWidget(self.shot_spin, 0)
            controls.addWidget(QtWidgets.QLabel("Feature"))
            self.feature_combo = QtWidgets.QComboBox(self)
            for key, label in self.FEATURE_OPTIONS:
                self.feature_combo.addItem(label, key)
            controls.addWidget(self.feature_combo, 0)
            controls.addWidget(QtWidgets.QLabel("ROI"))
            self.roi_start_spin = QtWidgets.QSpinBox(self)
            self.roi_start_spin.setRange(0, 0)
            self.roi_start_spin.setPrefix("[")
            controls.addWidget(self.roi_start_spin, 0)
            self.roi_stop_spin = QtWidgets.QSpinBox(self)
            self.roi_stop_spin.setRange(1, 1)
            self.roi_stop_spin.setPrefix(":")
            controls.addWidget(self.roi_stop_spin, 0)
            self.roi_reset_button = QtWidgets.QPushButton("Full ROI", self)
            controls.addWidget(self.roi_reset_button, 0)
            controls.addStretch(1)
            layout.addLayout(controls)

            self.info_label = QtWidgets.QLabel(
                "Run a monitor acquisition with waveform objects to inspect waveform preview, features, and delay."
            )
            self.info_label.setWordWrap(True)
            layout.addWidget(self.info_label)

            if pg is not None:
                self.preview_plot = pg.PlotWidget(title="Waveform Preview")
                self.preview_plot.addLegend()
                self.preview_plot.setLabel("bottom", "Time [s]")
                self.preview_plot.setLabel("left", "Amplitude")
                self.preview_plot.showGrid(x=True, y=True, alpha=0.2)
                layout.addWidget(self.preview_plot, 2)

                self.feature_plot = pg.PlotWidget(title="Waveform Feature Trend")
                self.feature_plot.addLegend()
                self.feature_plot.setLabel("bottom", "Shot Index")
                self.feature_plot.setLabel("left", "Feature Value")
                self.feature_plot.showGrid(x=True, y=True, alpha=0.2)
                layout.addWidget(self.feature_plot, 1)

                self.delay_plot = pg.PlotWidget(title="Waveform Delay Trend")
                self.delay_plot.addLegend()
                self.delay_plot.setLabel("bottom", "Shot Index")
                self.delay_plot.setLabel("left", "Delay [s]")
                self.delay_plot.showGrid(x=True, y=True, alpha=0.2)
                layout.addWidget(self.delay_plot, 1)
            else:
                self.preview_plot = None
                self.feature_plot = None
                self.delay_plot = None
                layout.addWidget(QtWidgets.QLabel("pyqtgraph is not installed"))

            self.primary_combo.currentIndexChanged.connect(self.viewChanged)
            self.secondary_combo.currentIndexChanged.connect(self.viewChanged)
            self.shot_spin.valueChanged.connect(self.viewChanged)
            self.feature_combo.currentIndexChanged.connect(self.viewChanged)
            self.roi_start_spin.valueChanged.connect(self._on_roi_start_changed)
            self.roi_stop_spin.valueChanged.connect(self._on_roi_stop_changed)
            self.roi_reset_button.clicked.connect(self._reset_roi)

        def clear_data(self, message: str) -> None:
            self.info_label.setText(message)
            self._feature_rows = []
            self._waveform_option_count = 0
            with self._blocked_controls():
                self.primary_combo.clear()
                self.secondary_combo.clear()
                self.shot_spin.setRange(1, 1)
                self.shot_spin.setValue(1)
                self.roi_start_spin.setRange(0, 0)
                self.roi_start_spin.setValue(0)
                self.roi_stop_spin.setRange(1, 1)
                self.roi_stop_spin.setValue(1)
            self._render_empty_plots()
            self._update_control_state()

        def set_waveform_options(
            self,
            options,
            *,
            shot_count: int,
            max_roi_stop: int,
            current_primary: str | None = None,
            current_secondary: str | None = None,
        ) -> None:
            options = list(options)
            self._waveform_option_count = len(options)
            current_primary = str(current_primary or "").strip()
            current_secondary = str(current_secondary or "").strip()
            with self._blocked_controls():
                self.primary_combo.clear()
                self.secondary_combo.clear()
                for pv_id, label in options:
                    self.primary_combo.addItem(label, pv_id)
                    self.secondary_combo.addItem(label, pv_id)
                if options:
                    self._select_combo_value(self.primary_combo, current_primary or options[0][0])
                    fallback_secondary = current_secondary or (options[1][0] if len(options) > 1 else options[0][0])
                    self._select_combo_value(self.secondary_combo, fallback_secondary)
                self.shot_spin.setRange(1, max(int(shot_count), 1))
                self.shot_spin.setValue(min(self.shot_spin.value(), max(int(shot_count), 1)))
                roi_stop = max(int(max_roi_stop), 1)
                self.roi_start_spin.setRange(0, max(roi_stop - 1, 0))
                self.roi_stop_spin.setRange(1, roi_stop)
                if self.roi_stop_spin.value() > roi_stop:
                    self.roi_stop_spin.setValue(roi_stop)
                if self.roi_stop_spin.value() <= self.roi_start_spin.value():
                    self.roi_stop_spin.setValue(min(roi_stop, self.roi_start_spin.value() + 1))
            self._update_control_state()

        def selected_primary_pv_id(self) -> str:
            return str(self.primary_combo.currentData() or "").strip()

        def selected_secondary_pv_id(self) -> str:
            return str(self.secondary_combo.currentData() or "").strip()

        def selected_feature_key(self) -> str:
            return str(self.feature_combo.currentData() or "peak_value")

        def selected_shot_index(self) -> int:
            return max(int(self.shot_spin.value()) - 1, 0)

        def roi_bounds(self) -> tuple[int, int]:
            start = int(self.roi_start_spin.value())
            stop = int(self.roi_stop_spin.value())
            if stop <= start:
                stop = start + 1
            return start, stop

        def set_preview_series(
            self,
            *,
            primary,
            secondary=None,
            roi_bounds: tuple[int, int] | None = None,
            info_text: str = "",
        ) -> None:
            if info_text:
                self.info_label.setText(info_text)
            if self.preview_plot is None:
                return
            self.preview_plot.clear()
            self.preview_plot.addLegend()
            style_plot_widget(self.preview_plot)
            self.preview_plot.setTitle("Waveform Preview")
            self.preview_plot.setLabel("bottom", "Time [s]")
            self.preview_plot.setLabel("left", "Amplitude")
            if primary:
                self._plot_waveform_series(self.preview_plot, primary, "#78d5e3")
            if secondary:
                self._plot_waveform_series(self.preview_plot, secondary, "#ff6b6b")
            style_plot_widget(self.preview_plot)
            self._set_preview_region(roi_bounds)

        def set_feature_rows(self, rows, *, feature_key: str, feature_label: str, info_text: str = "") -> None:
            self._feature_rows = list(rows)
            if info_text:
                self.info_label.setText(info_text)
            if self.feature_plot is None:
                return
            self.feature_plot.clear()
            self.feature_plot.addLegend()
            style_plot_widget(self.feature_plot)
            self.feature_plot.setTitle(f"Waveform Feature Trend | {feature_label}")
            self.feature_plot.setLabel("bottom", "Shot Index")
            self.feature_plot.setLabel("left", feature_label)
            for index, row in enumerate(self._feature_rows):
                x_values = [float(item) + 1.0 for item in row.get("sample_indices", [])]
                y_values = [float(item) for item in row.get("values", [])]
                plot_x, plot_y = downsample_waveform_minmax(x_values, y_values, max_points=2000)
                self.feature_plot.plot(
                    plot_x,
                    plot_y,
                    pen=pg.mkPen(color=pg.intColor(index, hues=max(len(self._feature_rows), 1)), width=2),
                    symbol=None,
                    name=str(row.get("label", row.get("pv_id", ""))),
                )
            if not self._feature_rows:
                self.feature_plot.setTitle("Waveform Feature Trend")
            style_plot_widget(self.feature_plot)

        def set_delay_series(self, sample_indices, delay_values, *, summary_text: str = "") -> None:
            if summary_text:
                self.info_label.setText(summary_text)
            if self.delay_plot is None:
                return
            self.delay_plot.clear()
            self.delay_plot.addLegend()
            style_plot_widget(self.delay_plot)
            self.delay_plot.setTitle("Waveform Delay Trend")
            self.delay_plot.setLabel("bottom", "Shot Index")
            self.delay_plot.setLabel("left", "Delay [s]")
            if sample_indices and delay_values:
                x_values = [float(item) + 1.0 for item in sample_indices]
                y_values = [float(item) for item in delay_values]
                plot_x, plot_y = downsample_waveform_minmax(x_values, y_values, max_points=2000)
                self.delay_plot.plot(
                    plot_x,
                    plot_y,
                    pen=pg.mkPen(color="#45d0bc", width=2),
                    symbol="o",
                    symbolSize=5,
                    name="Delay",
                )
            style_plot_widget(self.delay_plot)

        def _plot_waveform_series(self, plot_widget, payload, color: str) -> None:
            x_values = list(payload.get("x_values", []))
            y_values = list(payload.get("y_values", []))
            if not x_values or not y_values:
                return
            plot_x, plot_y = downsample_waveform_minmax(x_values, y_values, max_points=2000)
            plot_widget.plot(
                plot_x,
                plot_y,
                pen=pg.mkPen(color=color, width=2),
                name=str(payload.get("label", "")),
            )

        def _set_preview_region(self, roi_bounds: tuple[int, int] | None) -> None:
            if self.preview_plot is None:
                return
            if self._preview_region is not None:
                self.preview_plot.removeItem(self._preview_region)
                self._preview_region = None
            if roi_bounds is None:
                return
            start_sec, stop_sec = roi_bounds
            self._preview_region = pg.LinearRegionItem(
                values=(float(start_sec), float(stop_sec)),
                orientation="vertical",
                movable=False,
                brush=pg.mkBrush(120, 213, 227, 40),
                pen=pg.mkPen(color="#78d5e3", width=1),
            )
            self._preview_region.setZValue(-5)
            self.preview_plot.addItem(self._preview_region)

        def _render_empty_plots(self) -> None:
            for plot_widget, title, left_label in (
                (self.preview_plot, "Waveform Preview", "Amplitude"),
                (self.feature_plot, "Waveform Feature Trend", "Feature Value"),
                (self.delay_plot, "Waveform Delay Trend", "Delay [s]"),
            ):
                if plot_widget is None:
                    continue
                plot_widget.clear()
                plot_widget.addLegend()
                plot_widget.setTitle(title)
                plot_widget.setLabel("bottom", "Shot Index" if plot_widget is not self.preview_plot else "Time [s]")
                plot_widget.setLabel("left", left_label)

        def _update_control_state(self) -> None:
            has_waveforms = self._waveform_option_count > 0
            self.primary_combo.setEnabled(has_waveforms)
            self.secondary_combo.setEnabled(has_waveforms and self._waveform_option_count >= 2)
            self.shot_spin.setEnabled(has_waveforms)
            self.feature_combo.setEnabled(has_waveforms)
            self.roi_start_spin.setEnabled(has_waveforms)
            self.roi_stop_spin.setEnabled(has_waveforms)
            self.roi_reset_button.setEnabled(has_waveforms)

        def _on_roi_start_changed(self, value: int) -> None:
            if self.roi_stop_spin.value() <= value:
                with self._blocked_controls():
                    self.roi_stop_spin.setValue(value + 1)
            self.viewChanged.emit()

        def _on_roi_stop_changed(self, value: int) -> None:
            if value <= self.roi_start_spin.value():
                with self._blocked_controls():
                    self.roi_start_spin.setValue(max(value - 1, 0))
            self.viewChanged.emit()

        def _reset_roi(self) -> None:
            with self._blocked_controls():
                self.roi_start_spin.setValue(0)
                self.roi_stop_spin.setValue(self.roi_stop_spin.maximum())
            self.viewChanged.emit()

        @staticmethod
        def _select_combo_value(combo: QtWidgets.QComboBox, value: str) -> None:
            for index in range(combo.count()):
                if str(combo.itemData(index) or "").strip() == value:
                    combo.setCurrentIndex(index)
                    return
            if combo.count() > 0:
                combo.setCurrentIndex(0)

        def _blocked_controls(self):
            return _WaveformControlBlocker(
                self.primary_combo,
                self.secondary_combo,
                self.shot_spin,
                self.feature_combo,
                self.roi_start_spin,
                self.roi_stop_spin,
            )


    class _WaveformControlBlocker:
        def __init__(self, *widgets) -> None:
            self._widgets = list(widgets)
            self._blockers = []

        def __enter__(self):
            self._blockers = [QtCore.QSignalBlocker(widget) for widget in self._widgets]
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            self._blockers.clear()

else:

    class WaveformPlot:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create WaveformPlot")
