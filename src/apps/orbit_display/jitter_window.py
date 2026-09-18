"""Read-only, short-window BPM jitter display."""

from datetime import datetime
from math import nan

from matplotlib.backends.backend_qt5agg import FigureCanvas
from matplotlib.figure import Figure
from matplotlib.ticker import FixedLocator, MaxNLocator
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from jitter_data import JitterSamples


class JitterWindow(QMainWindow):
    sampling_changed = pyqtSignal(bool)
    closed = pyqtSignal()

    def __init__(self, bpm_ids, machine_name, refresh_interval_ms, palette,
                 bpm_range=None, parent=None):
        super().__init__(parent)
        self.bpm_ids = list(bpm_ids)
        self.machine_name = machine_name
        self.refresh_interval_ms = refresh_interval_ms
        self.palette = dict(palette)
        self.data = JitterSamples(len(bpm_ids))
        self.sampling = True
        first, last = bpm_range or (1, len(self.bpm_ids))
        self.range_start = max(1, min(first, max(1, len(self.bpm_ids))))
        self.range_end = max(self.range_start, min(last, max(1, len(self.bpm_ids))))
        self.selected_index = self.range_start - 1
        self.connection = "Waiting for data"

        self.setWindowTitle(f"{machine_name} Orbit Jitter — sampled orbit variation")
        self.resize(1050, 850)
        self.setMinimumSize(760, 620)
        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        controls = QFrame(central)
        controls.setObjectName("jitterControls")
        control_layout = QHBoxLayout(controls)
        control_layout.setContentsMargins(10, 5, 10, 5)
        control_layout.setSpacing(7)
        self.title_label = QLabel("Orbit Jitter", controls)
        self.title_label.setObjectName("jitterTitle")
        control_layout.addWidget(self.title_label)
        control_layout.addStretch(1)
        control_layout.addWidget(QLabel("BPM Range", controls))
        self.range_start_spin = QSpinBox(controls)
        self.range_end_spin = QSpinBox(controls)
        for spin, value in ((self.range_start_spin, self.range_start),
                            (self.range_end_spin, self.range_end)):
            spin.setRange(1, max(1, len(self.bpm_ids)))
            spin.setValue(value)
            spin.setFixedWidth(56)
            spin.setFixedHeight(28)
        self.range_start_spin.valueChanged.connect(self._change_bpm_range)
        self.range_end_spin.valueChanged.connect(self._change_bpm_range)
        control_layout.addWidget(self.range_start_spin)
        control_layout.addWidget(QLabel("to", controls))
        control_layout.addWidget(self.range_end_spin)
        self.toggle_button = QPushButton("Pause", controls)
        self.toggle_button.setFixedHeight(28)
        self.toggle_button.clicked.connect(self._toggle_sampling)
        control_layout.addWidget(self.toggle_button)
        clear_button = QPushButton("Clear", controls)
        clear_button.setFixedHeight(28)
        clear_button.clicked.connect(self._clear)
        control_layout.addWidget(clear_button)
        control_layout.addWidget(QLabel("Window", controls))
        self.window_combo = QComboBox(controls)
        self.window_combo.setFixedHeight(28)
        for count in (30, 60, 120):
            self.window_combo.addItem(f"{count} samples", count)
        self.window_combo.currentIndexChanged.connect(self._change_window)
        control_layout.addWidget(self.window_combo)
        layout.addWidget(controls)

        self.explanation = QLabel(central)
        self.explanation.setObjectName("jitterMeta")
        layout.addWidget(self.explanation)
        self.status_label = QLabel(central)
        self.status_label.setObjectName("jitterMeta")
        layout.addWidget(self.status_label)

        self.rms_canvas = FigureCanvas(Figure(figsize=(8, 4)))
        self.x_ax, self.y_ax = self.rms_canvas.figure.subplots(2, 1, sharex=True)
        self.rms_canvas.mpl_connect("button_press_event", self._select_bpm)
        layout.addWidget(self.rms_canvas, 2)

        self.trend_canvas = FigureCanvas(Figure(figsize=(8, 2.5)))
        self.trend_ax = self.trend_canvas.figure.add_subplot(111)
        layout.addWidget(self.trend_canvas, 1)
        self.apply_theme(palette)
        self._update_meta()
        self._redraw()

    def apply_theme(self, palette):
        self.palette = dict(palette)
        p = self.palette
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ background: {p['window_bg']}; color: {p['window_fg']}; }}
            QLabel {{ background: transparent; border: none; }}
            QFrame#jitterControls {{ background: {p['summary_bg']};
                border: 1px solid {p['summary_border']}; border-radius: 10px; }}
            QLabel#jitterTitle {{ font-size: 19px; font-weight: 700; }}
            QLabel#jitterMeta {{ color: {p['muted_fg']}; }}
            QPushButton, QComboBox, QSpinBox {{ background: {p['button_bg']}; color: {p['button_fg']};
                border: 1px solid {p['button_border']}; border-radius: 6px; padding: 2px 7px; }}
            QPushButton {{ min-height: 0px; max-height: 28px; padding: 0px 8px; }}
        """)
        self._redraw()

    def set_refresh_interval_ms(self, interval):
        self.refresh_interval_ms = interval
        self._update_meta()

    def _update_meta(self):
        count = len(self.data.visible())
        interval = self.refresh_interval_ms / 1000
        self.explanation.setText(
            f"Orbit variation sampled every {interval:g} s · Last {self.data.window_size} samples "
            "· RMS of mean-centered BPM position (mm); not pulse-by-pulse jitter"
        )
        last = self.data.visible()[-1][0] if count else None
        last_text = datetime.fromtimestamp(last).strftime("%H:%M:%S") if last else "--"
        state = "Sampling" if self.sampling else "Paused"
        self.status_label.setText(
            f"{state} · {count}/{self.data.window_size} samples · "
            f"Last: {last_text} · {self.connection}"
        )

    def _toggle_sampling(self):
        self.sampling = not self.sampling
        self.toggle_button.setText("Pause" if self.sampling else "Start")
        self._update_meta()
        self.sampling_changed.emit(self.sampling)

    def _clear(self):
        self.data.clear()
        self.connection = "Waiting for data"
        self._update_meta()
        self._redraw()

    def _change_window(self):
        self.data.set_window_size(self.window_combo.currentData())
        self._update_meta()
        self._redraw()

    def _change_bpm_range(self):
        start = self.range_start_spin.value()
        end = self.range_end_spin.value()
        if start > end:
            if self.sender() is self.range_start_spin:
                end = start
                self.range_end_spin.blockSignals(True)
                self.range_end_spin.setValue(end)
                self.range_end_spin.blockSignals(False)
            else:
                start = end
                self.range_start_spin.blockSignals(True)
                self.range_start_spin.setValue(start)
                self.range_start_spin.blockSignals(False)
        self.range_start, self.range_end = start, end
        if not start <= self.selected_index + 1 <= end:
            self.selected_index = start - 1
        self._redraw()

    def add_sample(self, timestamp, x_values, y_values, scale):
        if not self.sampling:
            return
        self.data.add(timestamp, x_values, y_values, scale)
        latest = self.data.visible()[-1]
        self.connection = (
            "Live" if any(value is not None for plane in latest[1:] for value in plane)
            else "PV unavailable / no valid BPM data"
        )
        self._update_meta()
        self._redraw()

    def _style_axis(self, ax):
        p = self.palette
        ax.set_facecolor(p["plot_bg"])
        ax.tick_params(colors=p["plot_text"], labelsize=9)
        ax.xaxis.label.set_color(p["plot_text"])
        ax.yaxis.label.set_color(p["plot_text"])
        ax.title.set_color(p["plot_text"])
        for spine in ax.spines.values():
            spine.set_edgecolor(p["plot_spine"])
        ax.grid(True, color=p["plot_grid"], linewidth=0.6)

    def _redraw(self):
        if not hasattr(self, "rms_canvas"):
            return
        p = self.palette
        self.rms_canvas.figure.set_facecolor(p["plot_card_bg"])
        self.trend_canvas.figure.set_facecolor(p["plot_card_bg"])
        positions = list(range(self.range_start, self.range_end + 1))
        for plane, ax, color in (("x", self.x_ax, p["orbit_x"]), ("y", self.y_ax, p["orbit_y"])):
            ax.clear()
            self._style_axis(ax)
            rms_values = [self.data.rms(index - 1, plane)[0] for index in positions]
            ax.plot(positions, [value if value is not None else nan for value in rms_values],
                    "-o", color=color, markersize=4, linewidth=1.4)
            if not any(value is not None for value in rms_values):
                ax.text(0.5, 0.5, "Collecting (10 valid samples needed)",
                        transform=ax.transAxes, ha="center", va="center",
                        color=p["muted_fg"])
            if positions:
                ax.axvline(self.selected_index + 1, color=p["muted_fg"], linestyle=":", linewidth=1)
                ax.set_xlim(self.range_start - 0.5, self.range_end + 0.5)
            ax.set_ylabel(f"{plane.upper()} RMS (mm)")
            ax.set_ylim(bottom=0)
            ax.set_title(f"{plane.upper()} jitter RMS", loc="left", fontsize=11,
                         color=p["plot_text"])
        self.y_ax.set_xlabel("BPM # (click to inspect)")
        locator = (FixedLocator([self.range_start]) if self.range_start == self.range_end
                   else MaxNLocator(integer=True))
        self.y_ax.xaxis.set_major_locator(locator)
        self.rms_canvas.figure.tight_layout(pad=1.3)
        self.rms_canvas.draw_idle()

        ax = self.trend_ax
        ax.clear()
        self._style_axis(ax)
        rows = self.data.visible()
        if rows and self.bpm_ids:
            last_time = rows[-1][0]
            times = [row[0] - last_time for row in rows]
            for plane, color in (("x", p["orbit_x"]), ("y", p["orbit_y"])):
                values = [value if value is not None else nan
                          for _, value in self.data.series(self.selected_index, plane)]
                ax.plot(times, values, "-o", color=color, markersize=3, label=plane.upper())
            legend = ax.legend(loc="upper right", facecolor=p["plot_bg"],
                               edgecolor=p["plot_spine"])
            for text in legend.get_texts():
                text.set_color(p["plot_text"])
        name = self.bpm_ids[self.selected_index] if self.bpm_ids else "No BPM"
        x_count = self.data.rms(self.selected_index, "x")[1] if self.bpm_ids else 0
        y_count = self.data.rms(self.selected_index, "y")[1] if self.bpm_ids else 0
        ax.set_title(f"{name} position · valid X {x_count}, Y {y_count}",
                     loc="left", fontsize=11, color=p["plot_text"])
        ax.set_xlabel("Seconds before latest sample")
        ax.set_ylabel("Position (mm)")
        self.trend_canvas.figure.tight_layout(pad=1.3)
        self.trend_canvas.draw_idle()

    def _select_bpm(self, event):
        if event.inaxes not in (self.x_ax, self.y_ax) or event.xdata is None:
            return
        index = round(event.xdata) - 1
        if self.range_start - 1 <= index < self.range_end:
            self.selected_index = index
            self._redraw()

    def closeEvent(self, event):
        self.sampling = False
        self.sampling_changed.emit(False)
        self.closed.emit()
        super().closeEvent(event)
