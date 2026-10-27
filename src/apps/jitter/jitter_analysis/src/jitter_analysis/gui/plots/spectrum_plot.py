from __future__ import annotations

from .theme import style_plot_widget
from .visibility import resolve_initial_visibility

try:
    from PyQt5 import QtWidgets
except ImportError:  # pragma: no cover - optional runtime dependency
    QtWidgets = None

try:
    import pyqtgraph as pg
except ImportError:  # pragma: no cover - optional runtime dependency
    pg = None

if pg is not None:

    class SpectrumAxisItem(pg.AxisItem):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.enableAutoSIPrefix(False)

        def logTickStrings(self, values, scale, spacing):
            labels = super().logTickStrings(values, scale, spacing)
            if spacing is not None:
                return labels

            decade_indices = {
                index
                for index, value in enumerate(values)
                if abs(float(value) - round(float(value))) < 1.0e-9
            }
            visible_indices = set(decade_indices)
            if len(decade_indices) < 2:
                for index, value in enumerate(values):
                    mantissa = 10.0 ** (float(value) % 1.0)
                    if min(abs(mantissa - 2.0), abs(mantissa - 5.0)) < 1.0e-8:
                        visible_indices.add(index)
            return [
                label if index in visible_indices else ""
                for index, label in enumerate(labels)
            ]

else:
    SpectrumAxisItem = None

if QtWidgets is not None:
    from ...analysis.spectrum import (
        compute_amplitude_spectrum,
        compute_periodic_fit,
        compute_welch_psd,
    )
    from .jitter_plot import SeriesVisibilityDialog

    DEFAULT_VISIBLE_SPECTRUM_SERIES = 1


if QtWidgets is not None:

    class SpectrumPlot(QtWidgets.QWidget):
        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            self._series_sources = []
            self._series_visibility = {}
            self._context_summary = ""
            layout = QtWidgets.QVBoxLayout(self)
            self.info_label = QtWidgets.QLabel(
                "Run a task to populate spectrum analysis. "
                "Sample interval is estimated from valid timestamps."
            )
            self.info_label.setWordWrap(True)
            layout.addWidget(self.info_label)

            controls = QtWidgets.QHBoxLayout()
            controls.addWidget(QtWidgets.QLabel("Show"))
            self.series_button = QtWidgets.QToolButton(self)
            self.series_button.setText("Visible Variables")
            self.series_button.setProperty("plotToolbarControl", "true")
            self.series_button.setFixedHeight(28)
            self.series_button.setEnabled(False)
            controls.addWidget(self.series_button, 0)

            controls.addWidget(QtWidgets.QLabel("Mode"))
            self.mode_combo = QtWidgets.QComboBox(self)
            self.mode_combo.addItem("Amplitude (FFT)", "amplitude")
            self.mode_combo.addItem("PSD (Welch)", "welch_psd")
            self.mode_combo.addItem("Periodic Fit", "periodic_fit")
            controls.addWidget(self.mode_combo)

            controls.addWidget(QtWidgets.QLabel("X Scale"))
            self.x_scale_combo = QtWidgets.QComboBox(self)
            self.x_scale_combo.addItem("Linear", "linear")
            self.x_scale_combo.addItem("Log10", "log10")
            self.x_scale_combo.setToolTip(
                "Use a logarithmic frequency axis. "
                "The 0 Hz component is hidden in this mode."
            )
            controls.addWidget(self.x_scale_combo)

            controls.addWidget(QtWidgets.QLabel("Y Scale"))
            self.y_scale_combo = QtWidgets.QComboBox(self)
            self.y_scale_combo.addItem("Linear", "linear")
            self.y_scale_combo.addItem("Log10", "log10")
            self.y_scale_combo.setToolTip(
                "Use a logarithmic Y axis to reveal spectrum components "
                "across multiple orders of magnitude."
            )
            controls.addWidget(self.y_scale_combo)

            controls.addWidget(QtWidgets.QLabel("Window"))
            self.window_combo = QtWidgets.QComboBox(self)
            self.window_combo.addItem("Mode Default", "default")
            self.window_combo.addItem("Rectangular", "boxcar")
            self.window_combo.addItem("Hann", "hann")
            self.window_combo.addItem("Hamming", "hamming")
            self.window_combo.addItem("Blackman", "blackman")
            controls.addWidget(self.window_combo)

            self.detrend_label = QtWidgets.QLabel("Detrend")
            controls.addWidget(self.detrend_label)
            self.detrend_combo = QtWidgets.QComboBox(self)
            self.detrend_combo.addItem("Mean", "mean")
            self.detrend_combo.addItem("Linear", "linear")
            self.detrend_combo.addItem("None", "none")
            controls.addWidget(self.detrend_combo)

            self.nperseg_label = QtWidgets.QLabel("nperseg")
            controls.addWidget(self.nperseg_label)
            self.nperseg_spin = QtWidgets.QSpinBox(self)
            self.nperseg_spin.setRange(0, 8192)
            self.nperseg_spin.setSpecialValueText("Auto")
            self.nperseg_spin.setValue(0)
            self.nperseg_spin.setToolTip(
                "Welch segment length. Auto uses min(512, samples)."
            )
            controls.addWidget(self.nperseg_spin)

            self.fit_frequency_label = QtWidgets.QLabel("Fit f [Hz]")
            controls.addWidget(self.fit_frequency_label)
            self.fit_frequency_spin = QtWidgets.QDoubleSpinBox(self)
            self.fit_frequency_spin.setRange(0.0, 1.0e6)
            self.fit_frequency_spin.setDecimals(9)
            self.fit_frequency_spin.setSingleStep(0.001)
            self.fit_frequency_spin.setSpecialValueText("Auto")
            self.fit_frequency_spin.setValue(0.0)
            self.fit_frequency_spin.setToolTip(
                "Auto finds and continuously refines the dominant frequency. "
                "Enter a positive frequency to lock the fit."
            )
            controls.addWidget(self.fit_frequency_spin)
            controls.addStretch(1)
            layout.addLayout(controls)

            if pg is not None:
                self._frequency_axis = SpectrumAxisItem(orientation="bottom")
                self._amplitude_axis = SpectrumAxisItem(orientation="left")
                self.plot_widget = pg.PlotWidget(
                    title="Spectrum",
                    axisItems={
                        "bottom": self._frequency_axis,
                        "left": self._amplitude_axis,
                    },
                )
                self.plot_widget.addLegend()
                self.plot_widget.setLabel("bottom", "Frequency [Hz]")
                self.plot_widget.setLabel("left", "One-sided peak amplitude")
                self.plot_widget.showGrid(x=True, y=True, alpha=0.2)
                layout.addWidget(self.plot_widget)
            else:
                self.plot_widget = None
                layout.addWidget(QtWidgets.QLabel("pyqtgraph is not installed"))

            self.summary_table = QtWidgets.QTableWidget(0, 0, self)
            self.summary_table.setEditTriggers(
                QtWidgets.QAbstractItemView.NoEditTriggers
            )
            self.summary_table.setSelectionMode(
                QtWidgets.QAbstractItemView.NoSelection
            )
            self.summary_table.setAlternatingRowColors(True)
            self.summary_table.verticalHeader().setVisible(False)
            layout.addWidget(self.summary_table)

            self.mode_combo.currentIndexChanged.connect(self._handle_mode_changed)
            self.x_scale_combo.currentIndexChanged.connect(
                self._handle_axis_scale_changed
            )
            self.y_scale_combo.currentIndexChanged.connect(
                self._handle_axis_scale_changed
            )
            self.window_combo.currentIndexChanged.connect(self._render_sources)
            self.detrend_combo.currentIndexChanged.connect(self._render_sources)
            self.nperseg_spin.valueChanged.connect(self._render_sources)
            self.fit_frequency_spin.valueChanged.connect(self._render_sources)
            self.series_button.clicked.connect(self._open_series_selector)
            self._update_control_state()
            self._set_summary_headers(self._headers_for_mode("amplitude"))

        def set_context_summary(self, summary: str) -> None:
            self._context_summary = str(summary or "").strip()

        def _set_info_text(self, text: str) -> None:
            message = str(text)
            if self._context_summary:
                message += f" | {self._context_summary}"
            self.info_label.setText(message)

        def clear_data(self, message: str) -> None:
            self._series_sources = []
            self._series_visibility = {}
            self._context_summary = ""
            self.info_label.setText(message)
            self.summary_table.setRowCount(0)
            self._rebuild_series_menu()
            if self.plot_widget is None:
                return
            self.plot_widget.clear()
            self.plot_widget.addLegend()
            style_plot_widget(self.plot_widget)
            self.plot_widget.setTitle("Spectrum")
            self._apply_axis_scales(auto_range=False)

        def set_series_sources(self, series_sources) -> None:
            self._series_sources = list(series_sources)
            previous_visibility = dict(self._series_visibility)
            self._series_visibility = resolve_initial_visibility(
                [self._series_key(source) for source in self._series_sources],
                previous_visibility=previous_visibility,
                default_visible_count=DEFAULT_VISIBLE_SPECTRUM_SERIES,
            )
            self._rebuild_series_menu()
            self._render_sources()

        def _handle_mode_changed(self, *_args) -> None:
            self._update_control_state()
            self._render_sources()

        def _handle_axis_scale_changed(self, *_args) -> None:
            self._apply_axis_scales(auto_range=True)

        def _is_log_x(self) -> bool:
            return str(self.x_scale_combo.currentData() or "linear") == "log10"

        def _is_log_y(self) -> bool:
            return str(self.y_scale_combo.currentData() or "linear") == "log10"

        def _x_axis_label(self) -> str:
            if self._is_log_x():
                return "Frequency [Hz] (log scale)"
            return "Frequency [Hz]"

        def _y_axis_label(self, mode: str) -> str:
            label = (
                "PSD"
                if mode == "welch_psd"
                else "One-sided peak amplitude"
            )
            return f"{label} (log scale)" if self._is_log_y() else label

        def _apply_axis_scales(self, *, auto_range: bool) -> None:
            if self.plot_widget is None:
                return
            mode = str(self.mode_combo.currentData() or "amplitude")
            self.plot_widget.setLogMode(
                x=self._is_log_x(),
                y=self._is_log_y(),
            )
            self.plot_widget.setLabel("bottom", self._x_axis_label())
            self.plot_widget.setLabel("left", self._y_axis_label(mode))
            if auto_range:
                self.plot_widget.autoRange()

        def _update_control_state(self) -> None:
            mode = str(self.mode_combo.currentData() or "amplitude")
            is_welch = mode == "welch_psd"
            is_periodic_fit = mode == "periodic_fit"
            self.window_combo.setEnabled(not is_periodic_fit)
            self.detrend_label.setEnabled(not is_periodic_fit)
            self.detrend_combo.setEnabled(not is_periodic_fit)
            self.nperseg_label.setEnabled(is_welch)
            self.nperseg_spin.setEnabled(is_welch)
            self.fit_frequency_label.setEnabled(is_periodic_fit)
            self.fit_frequency_spin.setEnabled(is_periodic_fit)

        @staticmethod
        def _window_label(window_name: str) -> str:
            labels = {
                "boxcar": "Rectangular",
                "hann": "Hann",
                "hamming": "Hamming",
                "blackman": "Blackman",
            }
            return labels.get(window_name, window_name.title())

        @staticmethod
        def _detrend_label_text(detrend_name: str) -> str:
            labels = {
                "none": "None",
                "mean": "Mean",
                "linear": "Linear",
            }
            return labels.get(detrend_name, detrend_name.title())

        def _selected_window_name(self, mode: str) -> str:
            if mode == "periodic_fit":
                return "hann"
            requested = str(self.window_combo.currentData() or "default")
            return "hann" if requested == "default" else requested

        def _selected_detrend_name(self, mode: str) -> str:
            if mode == "periodic_fit":
                return "linear"
            return str(self.detrend_combo.currentData() or "mean")

        @staticmethod
        def _series_key(source) -> str:
            token = str(source.get("pv_id", "")).strip()
            return token or str(
                source.get("display_name", source.get("name", ""))
            ).strip()

        @staticmethod
        def _series_label(source) -> str:
            pv_id = str(source.get("pv_id", "")).strip()
            name = str(
                source.get("name", source.get("display_name", pv_id))
            ).strip()
            if pv_id and name:
                return f"{name} [{pv_id}]"
            return name or pv_id

        @staticmethod
        def _sample_indices_are_contiguous(source) -> bool:
            indices = [int(item) for item in source.get("sample_indices", [])]
            if len(indices) != len(source.get("values", [])):
                return False
            return all(
                right - left == 1
                for left, right in zip(indices, indices[1:])
            )

        @staticmethod
        def _psd_unit(unit: str) -> str:
            token = str(unit).strip()
            return f"{token}²/Hz" if token else "1/Hz"

        def _rebuild_series_menu(self) -> None:
            self.series_button.setEnabled(bool(self._series_sources))
            if not self._series_sources:
                self.series_button.setText("Visible Variables")
                return

            self.series_button.setText(
                f"Visible Variables ({len(self.current_pv_ids())}/{len(self._series_sources)})"
            )

        def current_pv_ids(self) -> list[str]:
            return [
                self._series_key(source)
                for source in self._series_sources
                if self._series_visibility.get(self._series_key(source), True)
            ]

        def _selector_rows(self) -> list[dict]:
            return [
                {
                    "pv_id": self._series_key(source),
                    "label": self._series_label(source),
                    "group_id": source.get("group_id", ""),
                    "group_label": source.get("group_label", ""),
                    "read_pv": source.get("read_pv", ""),
                    "tags": source.get("tags", []),
                }
                for source in self._series_sources
            ]

        def _open_series_selector(self) -> None:
            if not self._series_sources:
                return
            dialog = SeriesVisibilityDialog(
                self._selector_rows(),
                self.current_pv_ids(),
                parent=self,
            )
            if dialog.exec_() == QtWidgets.QDialog.Accepted:
                self.set_visible_pv_ids(dialog.selected_pv_ids())

        def set_visible_pv_ids(self, pv_ids) -> None:
            visible_ids = {str(pv_id) for pv_id in pv_ids}
            available_ids = {
                self._series_key(source) for source in self._series_sources
            }
            visible_ids &= available_ids
            for series_key in self._series_visibility:
                self._series_visibility[series_key] = series_key in visible_ids
            self._rebuild_series_menu()
            self._render_sources()

        def _set_series_visibility(
            self,
            series_key: str,
            visible: bool,
        ) -> None:
            if series_key not in self._series_visibility:
                return
            visible_ids = set(self.current_pv_ids())
            if visible:
                visible_ids.add(series_key)
            else:
                visible_ids.discard(series_key)
            self.set_visible_pv_ids(visible_ids)

        def _set_all_series_visibility(self, visible: bool) -> None:
            self.set_visible_pv_ids(
                [self._series_key(source) for source in self._series_sources]
                if visible
                else []
            )

        @staticmethod
        def _headers_for_mode(mode: str) -> list[str]:
            if mode == "welch_psd":
                return [
                    "PV",
                    "Samples",
                    "dt [s]",
                    "Δf [Hz]",
                    "Detrend",
                    "Window",
                    "Seg",
                    "Peak f [Hz]",
                    "Period [s]",
                    "Peak PSD",
                    "Unit",
                ]
            if mode == "periodic_fit":
                return [
                    "PV",
                    "Samples",
                    "Fit",
                    "f [Hz]",
                    "Period [s]",
                    "Peak Amp",
                    "RMS Amp",
                    "Phase [deg]",
                    "Drift/h",
                    "Periodic [%]",
                    "Residual RMS",
                    "Unit",
                ]
            return [
                "PV",
                "Samples",
                "dt [s]",
                "Δf [Hz]",
                "Detrend",
                "Window",
                "Peak f [Hz]",
                "Period [s]",
                "Peak Amp",
                "Unit",
            ]

        def _set_summary_headers(self, headers: list[str]) -> None:
            self.summary_table.setColumnCount(len(headers))
            self.summary_table.setHorizontalHeaderLabels(headers)
            self.summary_table.horizontalHeader().setStretchLastSection(True)

        def _clear_plot_for_render(self, title: str) -> None:
            if self.plot_widget is None:
                return
            self.plot_widget.clear()
            self.plot_widget.addLegend()
            style_plot_widget(self.plot_widget)
            self.plot_widget.setTitle(title)

        def _render_sources(self, *_args) -> None:
            mode = str(self.mode_combo.currentData() or "amplitude")
            window_name = self._selected_window_name(mode)
            detrend_name = self._selected_detrend_name(mode)
            requested_nperseg = int(self.nperseg_spin.value()) or None
            target_frequency_hz = (
                float(self.fit_frequency_spin.value())
                if self.fit_frequency_spin.value() > 0.0
                else None
            )
            selected_sources = [
                source
                for source in self._series_sources
                if self._series_visibility.get(
                    self._series_key(source),
                    True,
                )
            ]
            total_sources = len(self._series_sources)
            self._set_summary_headers(self._headers_for_mode(mode))
            if total_sources and not selected_sources:
                self.summary_table.setRowCount(0)
                self._clear_plot_for_render("Spectrum")
                self._apply_axis_scales(auto_range=False)
                self._set_info_text("No PV is selected for spectrum display.")
                return

            series_items = []
            failures = []
            for source in selected_sources:
                label = self._series_label(source)
                try:
                    if mode != "periodic_fit" and not self._sample_indices_are_contiguous(
                        source
                    ):
                        raise ValueError(
                            "sample indices are not contiguous; use Periodic Fit"
                        )
                    if mode == "welch_psd":
                        result = compute_welch_psd(
                            source["values"],
                            float(source["series_sample_interval_sec"]),
                            window_name=window_name,
                            nperseg=requested_nperseg,
                            detrend=detrend_name,
                        )
                    elif mode == "periodic_fit":
                        result = compute_periodic_fit(
                            source["values"],
                            source.get("sample_times_sec", []),
                            target_frequency_hz=target_frequency_hz,
                        )
                    else:
                        result = compute_amplitude_spectrum(
                            source["values"],
                            float(source["series_sample_interval_sec"]),
                            window_name=window_name,
                            detrend=detrend_name,
                        )
                except Exception as exc:  # pragma: no cover - UI fallback
                    failures.append(f"{label}: {exc}")
                    continue
                series_items.append({**source, "result": result})

            if not series_items:
                self.summary_table.setRowCount(0)
                self._clear_plot_for_render("Spectrum")
                self._apply_axis_scales(auto_range=False)
                if failures:
                    self._set_info_text(
                        "Spectrum analysis is unavailable: "
                        + " | ".join(failures[:3])
                    )
                else:
                    self._set_info_text(
                        "Run a task to populate spectrum analysis."
                    )
                return

            self.summary_table.setRowCount(len(series_items))
            titles = {
                "amplitude": "One-sided Peak Amplitude Spectrum",
                "welch_psd": "Power Spectral Density",
                "periodic_fit": "Periodic Fit with Spectrum Context",
            }
            self._clear_plot_for_render(titles.get(mode, "Spectrum"))

            peak_summaries = []
            for row, item in enumerate(series_items):
                name = self._series_label(item)
                unit = str(item.get("unit", ""))
                sample_count = int(item["sample_count"])
                sample_interval_sec = float(
                    item["series_sample_interval_sec"]
                )
                result = item["result"]

                if mode == "welch_psd":
                    peak_frequency = result.dominant_frequency_hz
                    peak_value = result.dominant_psd
                    period_sec = (
                        1.0 / peak_frequency if peak_frequency > 0.0 else 0.0
                    )
                    y_values = result.psd
                    values = [
                        name,
                        str(sample_count),
                        f"{sample_interval_sec:.6g}",
                        f"{result.frequency_resolution_hz:.6g}",
                        self._detrend_label_text(result.detrend_name),
                        self._window_label(result.window_name),
                        str(result.nperseg),
                        f"{peak_frequency:.6g}",
                        f"{period_sec:.6g}",
                        f"{peak_value:.6g}",
                        self._psd_unit(unit),
                    ]
                    summary = (
                        f"{name}: {peak_frequency:.6g} Hz @ "
                        f"{peak_value:.6g} {self._psd_unit(unit)}"
                    )
                    frequencies_hz = result.frequencies_hz
                elif mode == "periodic_fit":
                    peak_frequency = result.frequency_hz
                    peak_value = result.peak_amplitude
                    y_values = result.amplitudes
                    fit_label = (
                        "Auto"
                        if result.frequency_source == "auto"
                        else "Locked"
                    )
                    values = [
                        name,
                        str(result.sample_count),
                        fit_label,
                        f"{result.frequency_hz:.9g}",
                        f"{result.period_sec:.9g}",
                        f"{result.peak_amplitude:.6g}",
                        f"{result.rms_amplitude:.6g}",
                        f"{result.phase_deg:.6g}",
                        f"{result.drift_per_hour:.6g}",
                        f"{100.0 * result.periodic_variance_fraction:.6g}",
                        f"{result.residual_rms:.6g}",
                        unit,
                    ]
                    summary = (
                        f"{name}: {fit_label} {result.frequency_hz:.6g} Hz, "
                        f"T={result.period_sec:.6g} s, "
                        f"A={result.peak_amplitude:.6g} {unit}"
                    )
                    frequencies_hz = result.frequencies_hz
                else:
                    peak_frequency = result.dominant_frequency_hz
                    peak_value = result.dominant_amplitude
                    period_sec = (
                        1.0 / peak_frequency if peak_frequency > 0.0 else 0.0
                    )
                    y_values = result.amplitudes
                    values = [
                        name,
                        str(sample_count),
                        f"{sample_interval_sec:.6g}",
                        f"{result.frequency_resolution_hz:.6g}",
                        self._detrend_label_text(result.detrend_name),
                        self._window_label(result.window_name),
                        f"{peak_frequency:.6g}",
                        f"{period_sec:.6g}",
                        f"{peak_value:.6g}",
                        unit,
                    ]
                    summary = (
                        f"{name}: {peak_frequency:.6g} Hz @ "
                        f"{peak_value:.6g} {unit} peak"
                    )
                    frequencies_hz = result.frequencies_hz

                for column, value in enumerate(values):
                    self.summary_table.setItem(
                        row,
                        column,
                        QtWidgets.QTableWidgetItem(value),
                    )

                if self.plot_widget is not None:
                    color = pg.intColor(
                        row,
                        hues=max(len(series_items), 1),
                    )
                    self.plot_widget.plot(
                        frequencies_hz,
                        y_values,
                        pen=pg.mkPen(color=color, width=2),
                        name=name,
                    )
                    if mode == "periodic_fit":
                        marker_pen = pg.mkPen(
                            color=color,
                            width=1,
                            style=2,
                        )
                        self.plot_widget.addItem(
                            pg.InfiniteLine(
                                pos=peak_frequency,
                                angle=90,
                                movable=False,
                                pen=marker_pen,
                            )
                        )
                        self.plot_widget.plot(
                            [peak_frequency],
                            [peak_value],
                            pen=None,
                            symbol="o",
                            symbolSize=10,
                            symbolPen=pg.mkPen(color=color, width=2),
                            symbolBrush=pg.mkBrush(color),
                        )

                peak_summaries.append(summary)

            if self.plot_widget is not None:
                style_plot_widget(self.plot_widget)
                self._apply_axis_scales(auto_range=False)

            self.summary_table.resizeColumnsToContents()
            if mode == "welch_psd":
                segment_summary = (
                    "Auto≤512"
                    if requested_nperseg is None
                    else str(requested_nperseg)
                )
                mode_label = (
                    "Welch PSD"
                    f" | detrend={self._detrend_label_text(detrend_name)}"
                    f" | window={self._window_label(window_name)}"
                    f" | nperseg={segment_summary}"
                )
            elif mode == "periodic_fit":
                frequency_summary = (
                    "Auto"
                    if target_frequency_hz is None
                    else f"{target_frequency_hz:.9g} Hz locked"
                )
                mode_label = (
                    "Periodic Fit"
                    f" | frequency={frequency_summary}"
                    " | linear trend in model"
                    " | context=Hann one-sided peak FFT"
                )
            else:
                mode_label = (
                    "FFT one-sided peak amplitude"
                    f" | detrend={self._detrend_label_text(detrend_name)}"
                    f" | window={self._window_label(window_name)}"
                )
            visibility_label = (
                f"showing {len(series_items)}/{total_sources} PVs"
            )
            message = (
                f"{mode_label} | {visibility_label}: "
                + " | ".join(peak_summaries[:4])
            )
            if failures:
                message += f" | Skipped {len(failures)} PV(s): {failures[0]}"
            self._set_info_text(message)

else:

    class SpectrumPlot:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create SpectrumPlot")
