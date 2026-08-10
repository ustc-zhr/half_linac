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

if QtWidgets is not None:
    from ...analysis.spectrum import compute_amplitude_spectrum, compute_welch_psd

    DEFAULT_VISIBLE_SPECTRUM_SERIES = 1


if QtWidgets is not None:

    class SpectrumPlot(QtWidgets.QWidget):
        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            self._series_sources = []
            self._series_visibility = {}
            layout = QtWidgets.QVBoxLayout(self)
            self.info_label = QtWidgets.QLabel(
                "Run a task to populate spectrum analysis. Sample interval is estimated from valid timestamps."
            )
            self.info_label.setWordWrap(True)
            layout.addWidget(self.info_label)

            controls = QtWidgets.QHBoxLayout()
            controls.addWidget(QtWidgets.QLabel("Show"))
            self.series_button = QtWidgets.QToolButton(self)
            self.series_button.setText("Visible PVs")
            self.series_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
            self.series_menu = QtWidgets.QMenu(self.series_button)
            self.series_button.setMenu(self.series_menu)
            self.series_button.setEnabled(False)
            controls.addWidget(self.series_button)
            controls.addWidget(QtWidgets.QLabel("Mode"))
            self.mode_combo = QtWidgets.QComboBox(self)
            self.mode_combo.addItem("Amplitude (FFT)", "amplitude")
            self.mode_combo.addItem("PSD (Welch)", "welch_psd")
            controls.addWidget(self.mode_combo)
            controls.addWidget(QtWidgets.QLabel("Window"))
            self.window_combo = QtWidgets.QComboBox(self)
            self.window_combo.addItem("Mode Default", "default")
            self.window_combo.addItem("Rectangular", "boxcar")
            self.window_combo.addItem("Hann", "hann")
            self.window_combo.addItem("Hamming", "hamming")
            self.window_combo.addItem("Blackman", "blackman")
            controls.addWidget(self.window_combo)
            self.nperseg_label = QtWidgets.QLabel("nperseg")
            controls.addWidget(self.nperseg_label)
            self.nperseg_spin = QtWidgets.QSpinBox(self)
            self.nperseg_spin.setRange(0, 8192)
            self.nperseg_spin.setSpecialValueText("Auto")
            self.nperseg_spin.setValue(0)
            self.nperseg_spin.setToolTip("Welch segment length. Auto uses min(256, samples).")
            controls.addWidget(self.nperseg_spin)
            controls.addStretch(1)
            layout.addLayout(controls)
            if pg is not None:
                self.plot_widget = pg.PlotWidget(title="Spectrum")
                self.plot_widget.addLegend()
                self.plot_widget.setLabel("bottom", "Frequency [Hz]")
                self.plot_widget.setLabel("left", "Amplitude")
                self.plot_widget.showGrid(x=True, y=True, alpha=0.2)
                layout.addWidget(self.plot_widget)
            else:
                self.plot_widget = None
                layout.addWidget(QtWidgets.QLabel("pyqtgraph is not installed"))

            self.summary_table = QtWidgets.QTableWidget(0, 8, self)
            self.summary_table.setHorizontalHeaderLabels(
                ["PV", "Samples", "dt [s]", "Window", "Seg", "Peak f [Hz]", "Peak Amp", "Unit"]
            )
            self.summary_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            self.summary_table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
            self.summary_table.setAlternatingRowColors(True)
            self.summary_table.verticalHeader().setVisible(False)
            self.summary_table.horizontalHeader().setStretchLastSection(True)
            layout.addWidget(self.summary_table)
            self.mode_combo.currentIndexChanged.connect(self._handle_mode_changed)
            self.window_combo.currentIndexChanged.connect(self._render_sources)
            self.nperseg_spin.valueChanged.connect(self._render_sources)
            self._update_control_state()

        def clear_data(self, message: str) -> None:
            self._series_sources = []
            self._series_visibility = {}
            self.info_label.setText(message)
            self.summary_table.setRowCount(0)
            self._rebuild_series_menu()
            if self.plot_widget is None:
                return
            self.plot_widget.clear()
            self.plot_widget.addLegend()
            style_plot_widget(self.plot_widget)
            self.plot_widget.setTitle("Spectrum")
            self.plot_widget.setLabel("left", "Amplitude")

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

        def _update_control_state(self) -> None:
            is_welch = str(self.mode_combo.currentData() or "amplitude") == "welch_psd"
            self.nperseg_label.setEnabled(is_welch)
            self.nperseg_spin.setEnabled(is_welch)

        @staticmethod
        def _window_label(window_name: str) -> str:
            labels = {
                "boxcar": "Rectangular",
                "hann": "Hann",
                "hamming": "Hamming",
                "blackman": "Blackman",
            }
            return labels.get(window_name, window_name.title())

        def _selected_window_name(self, mode: str) -> str:
            requested = str(self.window_combo.currentData() or "default")
            if requested == "default":
                return "hann" if mode == "welch_psd" else "boxcar"
            return requested

        @staticmethod
        def _series_key(source) -> str:
            token = str(source.get("pv_id", "")).strip()
            return token or str(source.get("display_name", source.get("name", ""))).strip()

        @staticmethod
        def _series_label(source) -> str:
            return str(source.get("display_name", source.get("name", ""))).strip()

        def _rebuild_series_menu(self) -> None:
            self.series_menu.clear()
            self.series_button.setEnabled(bool(self._series_sources))
            if not self._series_sources:
                self.series_button.setText("Visible PVs")
                return

            show_all_action = self.series_menu.addAction("Show All")
            show_all_action.triggered.connect(lambda: self._set_all_series_visibility(True))
            hide_all_action = self.series_menu.addAction("Hide All")
            hide_all_action.triggered.connect(lambda: self._set_all_series_visibility(False))
            self.series_menu.addSeparator()

            visible_count = 0
            total_count = len(self._series_sources)
            for source in self._series_sources:
                key = self._series_key(source)
                label = self._series_label(source)
                visible = bool(self._series_visibility.get(key, True))
                if visible:
                    visible_count += 1
                action = self.series_menu.addAction(label)
                action.setCheckable(True)
                action.setChecked(visible)
                action.toggled.connect(lambda checked, series_key=key: self._set_series_visibility(series_key, checked))

            self.series_button.setText(f"Visible PVs ({visible_count}/{total_count})")

        def _set_series_visibility(self, series_key: str, visible: bool) -> None:
            if series_key not in self._series_visibility:
                return
            self._series_visibility[series_key] = bool(visible)
            self._rebuild_series_menu()
            self._render_sources()

        def _set_all_series_visibility(self, visible: bool) -> None:
            for key in list(self._series_visibility.keys()):
                self._series_visibility[key] = bool(visible)
            self._rebuild_series_menu()
            self._render_sources()

        def _render_sources(self, *_args) -> None:
            mode = str(self.mode_combo.currentData() or "amplitude")
            window_name = self._selected_window_name(mode)
            requested_nperseg = int(self.nperseg_spin.value()) or None
            selected_sources = [
                source for source in self._series_sources if self._series_visibility.get(self._series_key(source), True)
            ]
            total_sources = len(self._series_sources)
            if total_sources and not selected_sources:
                self.summary_table.setRowCount(0)
                if self.plot_widget is not None:
                    self.plot_widget.clear()
                    self.plot_widget.addLegend()
                    style_plot_widget(self.plot_widget)
                    self.plot_widget.setTitle("Spectrum")
                    self.plot_widget.setLabel("bottom", "Frequency [Hz]")
                    self.plot_widget.setLabel("left", "Amplitude" if mode != "welch_psd" else "PSD")
                self.info_label.setText("No PV is selected for spectrum display.")
                return

            series_items = []
            failures = []
            for source in selected_sources:
                try:
                    if mode == "welch_psd":
                        result = compute_welch_psd(
                            source["values"],
                            float(source["series_sample_interval_sec"]),
                            window_name=window_name,
                            nperseg=requested_nperseg,
                        )
                    else:
                        result = compute_amplitude_spectrum(
                            source["values"],
                            float(source["series_sample_interval_sec"]),
                            window_name=window_name,
                        )
                except Exception as exc:  # pragma: no cover - UI fallback
                    failures.append(f"{source['name']}: {exc}")
                    continue
                series_items.append({**source, "result": result})

            if not series_items:
                self.summary_table.setRowCount(0)
                if self.plot_widget is not None:
                    self.plot_widget.clear()
                    self.plot_widget.addLegend()
                    style_plot_widget(self.plot_widget)
                    self.plot_widget.setTitle("Spectrum")
                    self.plot_widget.setLabel("bottom", "Frequency [Hz]")
                    self.plot_widget.setLabel("left", "Amplitude" if mode != "welch_psd" else "PSD")
                if failures:
                    self.info_label.setText("Spectrum analysis is unavailable for the current data.")
                else:
                    self.info_label.setText("Run a task to populate spectrum analysis.")
                return

            self.summary_table.setRowCount(len(series_items))
            if self.plot_widget is not None:
                self.plot_widget.clear()
                self.plot_widget.addLegend()
                style_plot_widget(self.plot_widget)
                if mode == "welch_psd":
                    self.plot_widget.setTitle("Power Spectral Density")
                    self.plot_widget.setLabel("left", "PSD")
                else:
                    self.plot_widget.setTitle("Amplitude Spectrum")
                    self.plot_widget.setLabel("left", "Amplitude")

            peak_summaries = []
            for row, item in enumerate(series_items):
                name = self._series_label(item)
                unit = str(item.get("unit", ""))
                sample_count = int(item["sample_count"])
                sample_interval_sec = float(item["series_sample_interval_sec"])
                result = item["result"]
                if mode == "welch_psd":
                    peak_frequency = result.dominant_frequency_hz
                    peak_value = result.dominant_psd
                    y_values = result.psd
                    peak_label = "Peak PSD"
                    segment_text = str(result.nperseg)
                else:
                    peak_frequency = result.dominant_frequency_hz
                    peak_value = result.dominant_amplitude
                    y_values = result.amplitudes
                    peak_label = "Peak Amp"
                    segment_text = "-"

                values = [
                    name,
                    str(sample_count),
                    f"{sample_interval_sec:.6g}",
                    self._window_label(result.window_name),
                    segment_text,
                    f"{peak_frequency:.6g}",
                    f"{peak_value:.6g}",
                    unit,
                ]
                for col, value in enumerate(values):
                    self.summary_table.setItem(row, col, QtWidgets.QTableWidgetItem(value))

                if self.plot_widget is not None:
                    color = pg.intColor(row, hues=max(len(series_items), 1))
                    self.plot_widget.plot(
                        result.frequencies_hz,
                        y_values,
                        pen=pg.mkPen(color=color, width=2),
                        name=name,
                    )

                peak_summaries.append(
                    f"{name}: {peak_frequency:.6g} Hz @ {peak_value:.6g}"
                )

            if self.plot_widget is not None:
                style_plot_widget(self.plot_widget)

            self.summary_table.setHorizontalHeaderLabels(
                ["PV", "Samples", "dt [s]", "Window", "Seg", "Peak f [Hz]", peak_label, "Unit"]
            )
            self.summary_table.resizeColumnsToContents()
            if peak_summaries:
                if mode == "welch_psd":
                    segment_summary = "Auto" if requested_nperseg is None else str(requested_nperseg)
                    mode_label = (
                        f"Welch PSD | window={self._window_label(window_name)} | nperseg={segment_summary}"
                    )
                else:
                    mode_label = f"FFT amplitude | window={self._window_label(window_name)}"
                visibility_label = f"showing {len(series_items)}/{total_sources} PVs"
                self.info_label.setText(f"{mode_label} | {visibility_label}: " + " | ".join(peak_summaries[:4]))
            else:
                self.info_label.setText("No valid spectrum data available.")

else:

    class SpectrumPlot:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create SpectrumPlot")
