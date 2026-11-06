from __future__ import annotations

import math

import numpy as np

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
            ("baseline_noise_rms", "Baseline Noise RMS"),
            ("peak_amplitude", "Pulse Amplitude"),
            ("peak_position", "Pulse Peak Position"),
            ("pulse_area", "Pulse Area"),
            ("fwhm", "FWHM"),
            ("snr", "SNR"),
        ]

        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            self._signal_region = None
            self._baseline_region = None
            self._region_scale = 1.0
            self._feature_rows = []
            self._waveform_option_count = 0
            self._roi_initialized = False

            layout = QtWidgets.QVBoxLayout(self)
            primary_controls = QtWidgets.QHBoxLayout()
            primary_controls.addWidget(QtWidgets.QLabel("Primary"))
            self.primary_combo = QtWidgets.QComboBox(self)
            primary_controls.addWidget(self.primary_combo, 1)
            self.reference_label = QtWidgets.QLabel("Reference")
            primary_controls.addWidget(self.reference_label)
            self.secondary_combo = QtWidgets.QComboBox(self)
            primary_controls.addWidget(self.secondary_combo, 1)
            primary_controls.addWidget(QtWidgets.QLabel("Shot"))
            self.shot_spin = QtWidgets.QSpinBox(self)
            self.shot_spin.setRange(1, 1)
            primary_controls.addWidget(self.shot_spin)
            primary_controls.addWidget(QtWidgets.QLabel("Display"))
            self.display_combo = QtWidgets.QComboBox(self)
            self.display_combo.addItem("Selected Shot", "selected_shot")
            self.display_combo.addItem("Mean ± Std", "mean_std")
            primary_controls.addWidget(self.display_combo)
            primary_controls.addWidget(QtWidgets.QLabel("Feature"))
            self.feature_combo = QtWidgets.QComboBox(self)
            for key, label in self.FEATURE_OPTIONS:
                self.feature_combo.addItem(label, key)
            primary_controls.addWidget(self.feature_combo)
            layout.addLayout(primary_controls)

            roi_controls = QtWidgets.QHBoxLayout()
            roi_controls.addWidget(QtWidgets.QLabel("Baseline ROI"))
            self.baseline_start_spin = QtWidgets.QSpinBox(self)
            self.baseline_start_spin.setRange(0, 0)
            self.baseline_start_spin.setPrefix("[")
            roi_controls.addWidget(self.baseline_start_spin)
            self.baseline_stop_spin = QtWidgets.QSpinBox(self)
            self.baseline_stop_spin.setRange(1, 1)
            self.baseline_stop_spin.setPrefix(":")
            roi_controls.addWidget(self.baseline_stop_spin)
            roi_controls.addSpacing(12)
            roi_controls.addWidget(QtWidgets.QLabel("Signal ROI"))
            self.roi_start_spin = QtWidgets.QSpinBox(self)
            self.roi_start_spin.setRange(0, 0)
            self.roi_start_spin.setPrefix("[")
            roi_controls.addWidget(self.roi_start_spin)
            self.roi_stop_spin = QtWidgets.QSpinBox(self)
            self.roi_stop_spin.setRange(1, 1)
            self.roi_stop_spin.setPrefix(":")
            roi_controls.addWidget(self.roi_stop_spin)
            self.roi_reset_button = QtWidgets.QPushButton("Full ROI", self)
            roi_controls.addWidget(self.roi_reset_button)
            roi_controls.addSpacing(12)
            roi_controls.addWidget(QtWidgets.QLabel("Polarity"))
            self.polarity_combo = QtWidgets.QComboBox(self)
            self.polarity_combo.addItem("Auto", "auto")
            self.polarity_combo.addItem("Positive", "positive")
            self.polarity_combo.addItem("Negative", "negative")
            roi_controls.addWidget(self.polarity_combo)
            self.expose_checkbox = QtWidgets.QCheckBox(
                "Expose selected feature to Jitter / Correlation / Spectrum",
                self,
            )
            self.expose_checkbox.setChecked(False)
            roi_controls.addWidget(self.expose_checkbox)
            roi_controls.addStretch(1)
            layout.addLayout(roi_controls)

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

            self.summary_table = QtWidgets.QTableWidget(0, 2, self)
            self.summary_table.setHorizontalHeaderLabels(["Metric", "Value"])
            self.summary_table.horizontalHeader().setStretchLastSection(True)
            self.summary_table.verticalHeader().setVisible(False)
            self.summary_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            self.summary_table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
            self.summary_table.setAlternatingRowColors(True)
            self.summary_table.setMaximumHeight(190)
            layout.addWidget(self.summary_table)

            self.primary_combo.currentIndexChanged.connect(self.viewChanged)
            self.secondary_combo.currentIndexChanged.connect(self.viewChanged)
            self.shot_spin.valueChanged.connect(self.viewChanged)
            self.display_combo.currentIndexChanged.connect(self._on_display_changed)
            self.feature_combo.currentIndexChanged.connect(self.viewChanged)
            self.polarity_combo.currentIndexChanged.connect(self.viewChanged)
            self.expose_checkbox.toggled.connect(self.viewChanged)
            self.baseline_start_spin.valueChanged.connect(self._on_baseline_start_changed)
            self.baseline_stop_spin.valueChanged.connect(self._on_baseline_stop_changed)
            self.roi_start_spin.valueChanged.connect(self._on_roi_start_changed)
            self.roi_stop_spin.valueChanged.connect(self._on_roi_stop_changed)
            self.roi_reset_button.clicked.connect(self._reset_roi)

        def clear_data(self, message: str) -> None:
            self.info_label.setText(message)
            self._feature_rows = []
            self._waveform_option_count = 0
            self._roi_initialized = False
            with self._blocked_controls():
                self.primary_combo.clear()
                self.secondary_combo.clear()
                self.shot_spin.setRange(1, 1)
                self.shot_spin.setValue(1)
                for start_spin in (self.baseline_start_spin, self.roi_start_spin):
                    start_spin.setRange(0, 0)
                    start_spin.setValue(0)
                for stop_spin in (self.baseline_stop_spin, self.roi_stop_spin):
                    stop_spin.setRange(1, 1)
                    stop_spin.setValue(1)
                self.display_combo.setCurrentIndex(0)
                self.polarity_combo.setCurrentIndex(0)
                self.expose_checkbox.setChecked(False)
            self.summary_table.setRowCount(0)
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
            old_max = max(int(self.roi_stop_spin.maximum()), 1)
            signal_was_full = (
                self._roi_initialized
                and self.roi_start_spin.value() == 0
                and self.roi_stop_spin.value() == old_max
            )
            old_default_baseline_stop = max(1, int(math.ceil(old_max * 0.1)))
            baseline_was_default = (
                self._roi_initialized
                and self.baseline_start_spin.value() == 0
                and self.baseline_stop_spin.value() == old_default_baseline_stop
            )
            roi_stop = max(int(max_roi_stop), 1)

            with self._blocked_controls():
                self.primary_combo.clear()
                self.secondary_combo.clear()
                for pv_id, label in options:
                    self.primary_combo.addItem(label, pv_id)
                    self.secondary_combo.addItem(label, pv_id)
                if options:
                    self._select_combo_value(self.primary_combo, current_primary or options[0][0])
                    fallback_secondary = current_secondary or (
                        options[1][0] if len(options) > 1 else options[0][0]
                    )
                    self._select_combo_value(self.secondary_combo, fallback_secondary)
                self.shot_spin.setRange(1, max(int(shot_count), 1))
                self.shot_spin.setValue(min(self.shot_spin.value(), max(int(shot_count), 1)))

                for start_spin in (self.baseline_start_spin, self.roi_start_spin):
                    start_spin.setRange(0, max(roi_stop - 1, 0))
                for stop_spin in (self.baseline_stop_spin, self.roi_stop_spin):
                    stop_spin.setRange(1, roi_stop)

                if not self._roi_initialized:
                    self.roi_start_spin.setValue(0)
                    self.roi_stop_spin.setValue(roi_stop)
                    self.baseline_start_spin.setValue(0)
                    self.baseline_stop_spin.setValue(max(1, int(math.ceil(roi_stop * 0.1))))
                    self._roi_initialized = True
                else:
                    if signal_was_full:
                        self.roi_start_spin.setValue(0)
                        self.roi_stop_spin.setValue(roi_stop)
                    else:
                        self.roi_start_spin.setValue(min(self.roi_start_spin.value(), roi_stop - 1))
                        self.roi_stop_spin.setValue(
                            min(max(self.roi_stop_spin.value(), self.roi_start_spin.value() + 1), roi_stop)
                        )
                    if baseline_was_default:
                        self.baseline_start_spin.setValue(0)
                        self.baseline_stop_spin.setValue(max(1, int(math.ceil(roi_stop * 0.1))))
                    else:
                        self.baseline_start_spin.setValue(
                            min(self.baseline_start_spin.value(), roi_stop - 1)
                        )
                        self.baseline_stop_spin.setValue(
                            min(
                                max(
                                    self.baseline_stop_spin.value(),
                                    self.baseline_start_spin.value() + 1,
                                ),
                                roi_stop,
                            )
                        )

            has_reference = len(options) >= 2
            self.reference_label.setVisible(has_reference)
            self.secondary_combo.setVisible(has_reference)
            if self.delay_plot is not None:
                self.delay_plot.setVisible(has_reference)
            self._update_control_state()

        def selected_primary_pv_id(self) -> str:
            return str(self.primary_combo.currentData() or "").strip()

        def selected_secondary_pv_id(self) -> str:
            return str(self.secondary_combo.currentData() or "").strip()

        def selected_feature_key(self) -> str:
            return str(self.feature_combo.currentData() or "baseline_mean")

        def selected_display_mode(self) -> str:
            return str(self.display_combo.currentData() or "selected_shot")

        def selected_polarity(self) -> str:
            return str(self.polarity_combo.currentData() or "auto")

        def expose_selected_feature(self) -> bool:
            return bool(self.expose_checkbox.isChecked())

        def selected_shot_index(self) -> int:
            return max(int(self.shot_spin.value()) - 1, 0)

        def roi_bounds(self) -> tuple[int, int]:
            return self._spin_bounds(self.roi_start_spin, self.roi_stop_spin)

        def baseline_roi_bounds(self) -> tuple[int, int]:
            return self._spin_bounds(self.baseline_start_spin, self.baseline_stop_spin)

        @staticmethod
        def _spin_bounds(start_spin, stop_spin) -> tuple[int, int]:
            start = int(start_spin.value())
            stop = int(stop_spin.value())
            return start, max(stop, start + 1)

        def set_preview_series(
            self,
            *,
            primary,
            secondary=None,
            signal_roi_bounds: tuple[float, float] | None = None,
            baseline_roi_bounds: tuple[float, float] | None = None,
            info_text: str = "",
            x_axis_label: str = "Time [s]",
            region_scale: float = 1.0,
        ) -> None:
            if info_text:
                self.info_label.setText(info_text)
            if self.preview_plot is None:
                return
            self.preview_plot.clear()
            self._signal_region = None
            self._baseline_region = None
            self.preview_plot.addLegend()
            style_plot_widget(self.preview_plot)
            self.preview_plot.setTitle(
                "Waveform Mean ± Std"
                if self.selected_display_mode() == "mean_std"
                else "Waveform Preview"
            )
            self.preview_plot.setLabel("bottom", x_axis_label)
            self.preview_plot.setLabel("left", "Amplitude")
            if primary:
                self._plot_waveform_series(self.preview_plot, primary, "#78d5e3")
            if secondary:
                self._plot_waveform_series(self.preview_plot, secondary, "#ff6b6b")
            style_plot_widget(self.preview_plot)
            self._set_preview_regions(
                signal_roi_bounds=signal_roi_bounds,
                baseline_roi_bounds=baseline_roi_bounds,
                region_scale=region_scale,
            )

        def set_feature_rows(
            self,
            rows,
            *,
            feature_key: str,
            feature_label: str,
            info_text: str = "",
        ) -> None:
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
                    pen=pg.mkPen(
                        color=pg.intColor(index, hues=max(len(self._feature_rows), 1)),
                        width=2,
                    ),
                    symbol=None,
                    name=str(row.get("label", row.get("pv_id", ""))),
                )
            if not self._feature_rows:
                self.feature_plot.setTitle("Waveform Feature Trend")
            style_plot_widget(self.feature_plot)

        def set_delay_series(
            self,
            sample_indices,
            delay_values,
            *,
            unit: str = "s",
            summary_text: str = "",
        ) -> None:
            if summary_text:
                self.info_label.setText(summary_text)
            if self.delay_plot is None:
                return
            self.delay_plot.clear()
            self.delay_plot.addLegend()
            style_plot_widget(self.delay_plot)
            self.delay_plot.setTitle("Waveform Delay Trend")
            self.delay_plot.setLabel("bottom", "Shot Index")
            self.delay_plot.setLabel("left", f"Delay [{unit}]")
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

        def set_summary_rows(self, rows) -> None:
            rows = list(rows)
            self.summary_table.setRowCount(len(rows))
            for row_index, (metric, value) in enumerate(rows):
                self.summary_table.setItem(
                    row_index,
                    0,
                    QtWidgets.QTableWidgetItem(str(metric)),
                )
                self.summary_table.setItem(
                    row_index,
                    1,
                    QtWidgets.QTableWidgetItem(str(value)),
                )

        def _plot_waveform_series(self, plot_widget, payload, color: str) -> None:
            x_values = np.asarray(payload.get("x_values", []), dtype=float)
            y_values = np.asarray(payload.get("y_values", []), dtype=float)
            if x_values.size <= 0 or y_values.size <= 0:
                return
            std_values = np.asarray(payload.get("std_values", []), dtype=float)
            label = str(payload.get("label", ""))
            if std_values.size == y_values.size:
                if x_values.size > 2000:
                    indices = np.linspace(0, x_values.size - 1, 2000, dtype=int)
                    plot_x = x_values[indices]
                    plot_y = y_values[indices]
                    plot_std = std_values[indices]
                else:
                    plot_x = x_values
                    plot_y = y_values
                    plot_std = std_values
                mean_item = plot_widget.plot(
                    plot_x,
                    plot_y,
                    pen=pg.mkPen(color=color, width=2),
                    name=label,
                )
                upper_item = plot_widget.plot(
                    plot_x,
                    plot_y + plot_std,
                    pen=pg.mkPen(color=color, width=1),
                )
                lower_item = plot_widget.plot(
                    plot_x,
                    plot_y - plot_std,
                    pen=pg.mkPen(color=color, width=1),
                )
                fill = pg.FillBetweenItem(
                    upper_item,
                    lower_item,
                    brush=pg.mkBrush(color + "40"),
                )
                plot_widget.addItem(fill)
                mean_item.setZValue(2)
                return

            plot_x, plot_y = downsample_waveform_minmax(
                x_values.tolist(),
                y_values.tolist(),
                max_points=2000,
            )
            plot_widget.plot(
                plot_x,
                plot_y,
                pen=pg.mkPen(color=color, width=2),
                name=label,
            )

        def _set_preview_regions(
            self,
            *,
            signal_roi_bounds,
            baseline_roi_bounds,
            region_scale: float,
        ) -> None:
            if self.preview_plot is None:
                return
            self._region_scale = float(region_scale) if float(region_scale) > 0.0 else 1.0
            if signal_roi_bounds is not None:
                self._signal_region = pg.LinearRegionItem(
                    values=tuple(float(item) for item in signal_roi_bounds),
                    orientation="vertical",
                    movable=True,
                    brush=pg.mkBrush(120, 213, 227, 35),
                    pen=pg.mkPen(color="#78d5e3", width=1),
                )
                self._signal_region.setZValue(-5)
                self._signal_region.sigRegionChangeFinished.connect(
                    lambda: self._region_changed(False)
                )
                self.preview_plot.addItem(self._signal_region)
            if baseline_roi_bounds is not None:
                self._baseline_region = pg.LinearRegionItem(
                    values=tuple(float(item) for item in baseline_roi_bounds),
                    orientation="vertical",
                    movable=True,
                    brush=pg.mkBrush(255, 193, 94, 35),
                    pen=pg.mkPen(color="#ffc15e", width=1),
                )
                self._baseline_region.setZValue(-4)
                self._baseline_region.sigRegionChangeFinished.connect(
                    lambda: self._region_changed(True)
                )
                self.preview_plot.addItem(self._baseline_region)

        def _region_changed(self, baseline: bool) -> None:
            region = self._baseline_region if baseline else self._signal_region
            if region is None:
                return
            low, high = sorted(float(item) for item in region.getRegion())
            start = max(int(round(low / self._region_scale)), 0)
            stop = max(int(round(high / self._region_scale)), start + 1)
            start_spin = self.baseline_start_spin if baseline else self.roi_start_spin
            stop_spin = self.baseline_stop_spin if baseline else self.roi_stop_spin
            start = min(start, start_spin.maximum())
            stop = min(max(stop, start + 1), stop_spin.maximum())
            with self._blocked_controls():
                start_spin.setValue(start)
                stop_spin.setValue(stop)
            self.viewChanged.emit()

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
                plot_widget.setLabel(
                    "bottom",
                    "Shot Index" if plot_widget is not self.preview_plot else "Time [s]",
                )
                plot_widget.setLabel("left", left_label)

        def _update_control_state(self) -> None:
            has_waveforms = self._waveform_option_count > 0
            self.primary_combo.setEnabled(has_waveforms)
            self.secondary_combo.setEnabled(has_waveforms and self._waveform_option_count >= 2)
            self.shot_spin.setEnabled(
                has_waveforms and self.selected_display_mode() == "selected_shot"
            )
            self.display_combo.setEnabled(has_waveforms)
            self.feature_combo.setEnabled(has_waveforms)
            self.polarity_combo.setEnabled(has_waveforms)
            self.expose_checkbox.setEnabled(has_waveforms)
            self.baseline_start_spin.setEnabled(has_waveforms)
            self.baseline_stop_spin.setEnabled(has_waveforms)
            self.roi_start_spin.setEnabled(has_waveforms)
            self.roi_stop_spin.setEnabled(has_waveforms)
            self.roi_reset_button.setEnabled(has_waveforms)

        def _on_display_changed(self) -> None:
            self._update_control_state()
            self.viewChanged.emit()

        def _on_baseline_start_changed(self, value: int) -> None:
            if self.baseline_stop_spin.value() <= value:
                with self._blocked_controls():
                    self.baseline_stop_spin.setValue(value + 1)
            self.viewChanged.emit()

        def _on_baseline_stop_changed(self, value: int) -> None:
            if value <= self.baseline_start_spin.value():
                with self._blocked_controls():
                    self.baseline_start_spin.setValue(max(value - 1, 0))
            self.viewChanged.emit()

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
            maximum = max(self.roi_stop_spin.maximum(), 1)
            with self._blocked_controls():
                self.roi_start_spin.setValue(0)
                self.roi_stop_spin.setValue(maximum)
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
                self.display_combo,
                self.feature_combo,
                self.polarity_combo,
                self.expose_checkbox,
                self.baseline_start_spin,
                self.baseline_stop_spin,
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
