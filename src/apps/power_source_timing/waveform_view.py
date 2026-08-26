from __future__ import annotations

import math
import time
from dataclasses import dataclass

import numpy as np
from PyQt5.QtCore import QSignalBlocker, QTimer
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .epics_client import WaveformMonitor, WaveformSnapshot
from .model import WAVEFORM_DEVICES
from .profile_runtime import TimingGroup, WaveformAlignmentConfig
from .waveform import WaveformAnalysis, analyze_waveform

try:
    import pyqtgraph as pg
except ImportError:  # pragma: no cover - project environment declares pyqtgraph
    pg = None


DEVICE_LABELS = {
    "hv": "HV",
    "llrf": "LLRF",
    "ssa": "SSA",
    "kly": "KLY",
    "pickup": "Pickup",
}
TRACE_COLORS = {
    "hv": "#e7a64a",
    "llrf": "#49b6ff",
    "ssa": "#58cf8b",
    "kly": "#d987e8",
    "pickup": "#f28c6f",
}


@dataclass
class TraceWidgets:
    visible: QCheckBox
    status: QLabel
    result: QLabel


class WaveformAlignmentWidget(QFrame):
    def __init__(self, config: WaveformAlignmentConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("panel")
        self.config = config
        self.monitor = WaveformMonitor()
        self.current_group: TimingGroup | None = None
        self.trace_widgets: dict[str, TraceWidgets] = {}
        self.curves: dict[str, object] = {}
        self.edge_markers: dict[str, object] = {}
        self._latest_length = 0
        self._roi_initialized = False
        self._palette: dict[str, str] = {}
        self._build_ui()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(config.refresh_interval_ms)
        self.refresh_timer.timeout.connect(self.refresh_now)
        self.refresh_timer.start()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 10)
        layout.setSpacing(7)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(7)
        self.title_label = QLabel(
            "Waveform Alignment"
            if self.config.shared_time_origin
            else "Waveform Inspection",
            self,
        )
        self.title_label.setProperty("role", "sectionTitle")
        toolbar.addWidget(self.title_label)
        toolbar.addStretch(1)
        toolbar.addWidget(self._field("View"))
        self.display_mode = QComboBox(self)
        self.display_mode.addItem("Normalized", "normalized")
        self.display_mode.addItem("Raw", "raw")
        self._select_combo(self.display_mode, self.config.default_display_mode)
        self.display_mode.currentIndexChanged.connect(self.refresh_now)
        toolbar.addWidget(self.display_mode)
        self.reference_label = self._field("Reference")
        self.reference_combo = QComboBox(self)
        self.reference_combo.currentIndexChanged.connect(self.refresh_now)
        if self.config.shared_time_origin:
            toolbar.addWidget(self.reference_label)
            toolbar.addWidget(self.reference_combo)
        else:
            self.reference_label.hide()
            self.reference_combo.hide()
        toolbar.addWidget(self._field("Threshold"))
        self.threshold_spin = QDoubleSpinBox(self)
        self.threshold_spin.setRange(5.0, 95.0)
        self.threshold_spin.setSingleStep(5.0)
        self.threshold_spin.setDecimals(0)
        self.threshold_spin.setSuffix(" %")
        self.threshold_spin.setValue(self.config.default_threshold_fraction * 100.0)
        self.threshold_spin.valueChanged.connect(self.refresh_now)
        toolbar.addWidget(self.threshold_spin)
        self.full_roi_button = QPushButton("Full ROI", self)
        self.full_roi_button.clicked.connect(self.reset_roi)
        toolbar.addWidget(self.full_roi_button)
        self.fit_button = QPushButton("Fit View", self)
        self.fit_button.clicked.connect(self.fit_view)
        toolbar.addWidget(self.fit_button)
        self.freeze_button = QPushButton("Freeze", self)
        self.freeze_button.setCheckable(True)
        self.freeze_button.toggled.connect(self._set_frozen)
        toolbar.addWidget(self.freeze_button)
        layout.addLayout(toolbar)

        trace_grid = QGridLayout()
        trace_grid.setContentsMargins(0, 0, 0, 0)
        trace_grid.setHorizontalSpacing(14)
        trace_grid.setVerticalSpacing(2)
        for column, device in enumerate(WAVEFORM_DEVICES):
            visible = QCheckBox(DEVICE_LABELS[device], self)
            visible.setChecked(True)
            visible.setStyleSheet(f"color: {TRACE_COLORS[device]}; font-weight: 700;")
            visible.toggled.connect(self.refresh_now)
            status = QLabel("Not configured", self)
            status.setProperty("role", "field")
            result = QLabel("Unavailable", self)
            result.setProperty("role", "value")
            summary = QWidget(self)
            summary_layout = QHBoxLayout(summary)
            summary_layout.setContentsMargins(0, 0, 0, 0)
            summary_layout.setSpacing(7)
            summary_layout.addWidget(visible)
            summary_layout.addWidget(status)
            summary_layout.addStretch(1)
            trace_grid.addWidget(summary, 0, column)
            trace_grid.addWidget(result, 1, column)
            trace_grid.setColumnStretch(column, 1)
            self.trace_widgets[device] = TraceWidgets(visible, status, result)
        layout.addLayout(trace_grid)

        self.info_label = QLabel("Waiting for waveform configuration", self)
        self.info_label.setProperty("role", "field")
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        if pg is None:
            self.plot = None
            self.roi = None
            unavailable = QLabel(
                "Waveform plot unavailable: pyqtgraph is not installed. Timing controls remain available.",
                self,
            )
            unavailable.setProperty("tone", "warning")
            layout.addWidget(unavailable, 1)
            self.full_roi_button.setEnabled(False)
            self.fit_button.setEnabled(False)
            return

        self.plot = pg.PlotWidget(self)
        self.plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.plot.setMinimumHeight(230)
        self.plot.setLabel(
            "bottom",
            "Sample Index"
            if self.config.shared_time_origin
            else "Local Sample Index (independent origins)",
        )
        self.plot.setLabel("left", "Normalized Amplitude")
        self.plot.showGrid(x=True, y=True, alpha=0.18)
        self.plot.setDownsampling(auto=True, mode="peak")
        self.plot.setClipToView(True)
        for device in WAVEFORM_DEVICES:
            curve = self.plot.plot([], [], pen=pg.mkPen(TRACE_COLORS[device], width=2))
            marker = pg.InfiniteLine(
                angle=90,
                movable=False,
                pen=pg.mkPen(TRACE_COLORS[device], width=1, style=2),
            )
            marker.hide()
            self.plot.addItem(marker)
            self.curves[device] = curve
            self.edge_markers[device] = marker
        self.roi = pg.LinearRegionItem(values=(0.0, 1.0), movable=True)
        self.roi.setBrush(pg.mkBrush(69, 208, 188, 18))
        self.roi.setHoverBrush(pg.mkBrush(69, 208, 188, 32))
        self.roi.setZValue(10)
        self.roi.sigRegionChangeFinished.connect(self.refresh_now)
        self.plot.addItem(self.roi)
        layout.addWidget(self.plot, 1)

    @staticmethod
    def _field(text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("role", "field")
        return label

    @staticmethod
    def _select_combo(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(max(index, 0))

    def bind(self, group: TimingGroup) -> None:
        self.current_group = group
        self.monitor.bind(group)
        self._latest_length = 0
        self._roi_initialized = False
        self._clear_plot()
        with QSignalBlocker(self.reference_combo):
            self.reference_combo.clear()
            for device in WAVEFORM_DEVICES:
                widgets = self.trace_widgets[device]
                configured = device in group.waveforms
                was_enabled = widgets.visible.isEnabled()
                with QSignalBlocker(widgets.visible):
                    if not configured:
                        widgets.visible.setChecked(False)
                    elif not was_enabled:
                        widgets.visible.setChecked(True)
                widgets.visible.setEnabled(configured)
                if not configured:
                    widgets.status.setText("Not configured")
                    widgets.result.setText("Unavailable")
                    continue
                widgets.status.setText("Connecting")
                self.reference_combo.addItem(DEVICE_LABELS[device], device)
            self._select_combo(self.reference_combo, self.config.reference_device)
        configured_count = len(group.waveforms)
        self.reference_combo.setEnabled(configured_count > 0)
        self.info_label.setText(
            "Waveforms not configured"
            if configured_count == 0
            else self._timebase_summary(configured_count)
        )

    def _timebase_summary(self, configured_count: int) -> str:
        rate = (
            "Unknown sample rate"
            if self.config.sample_rate_mhz is None
            else f"{self.config.sample_rate_mhz:g} MHz"
        )
        origin = (
            "shared time origin"
            if self.config.shared_time_origin
            else "independent channel origins · cross-channel Δ unavailable"
        )
        return f"{configured_count} waveform channel(s) configured · {rate} · {origin}"

    def refresh_now(self, *_args: object) -> None:
        if self.freeze_button.isChecked() or self.current_group is None:
            return
        snapshots = self.monitor.snapshots()
        display_mode = str(self.display_mode.currentData() or "normalized")
        if self.plot is not None:
            self.plot.setLabel(
                "left", "Raw Amplitude" if display_mode == "raw" else "Normalized Amplitude"
            )
        max_length = max(
            (
                int(np.asarray(snapshot.value).size)
                for snapshot in snapshots.values()
                if snapshot.value is not None
            ),
            default=0,
        )
        if max_length > 1:
            self._latest_length = max_length
            if self.roi is not None and not self._roi_initialized:
                self.roi.setRegion((0.0, float(max_length - 1)))
                self._roi_initialized = True
        roi_start, roi_stop = self.roi_bounds()
        now = time.monotonic()
        analyses: dict[str, WaveformAnalysis] = {}
        usable: set[str] = set()

        for device in WAVEFORM_DEVICES:
            widgets = self.trace_widgets[device]
            if device not in self.current_group.waveforms:
                self._hide_trace(device)
                continue
            snapshot = snapshots.get(device, WaveformSnapshot(None, False, None, None))
            analysis = self._analyze_snapshot(snapshot, roi_start, roi_stop)
            if analysis is not None:
                analyses[device] = analysis
            age = (
                math.inf
                if snapshot.received_monotonic is None
                else max(0.0, now - snapshot.received_monotonic)
            )
            stale = age > self.config.stale_after_s
            if not snapshot.connected:
                widgets.status.setText("Disconnected")
            elif snapshot.value is None:
                widgets.status.setText("Waiting")
            elif analysis is None:
                widgets.status.setText("Invalid")
            elif stale:
                widgets.status.setText(f"Stale {age:.1f}s")
            else:
                widgets.status.setText(f"{analysis.raw.size} samples · {age:.1f}s")
                usable.add(device)
            self._render_trace(
                device,
                analysis,
                display_mode,
                active=snapshot.connected and not stale,
            )

        self._render_results(analyses, usable)

    def _analyze_snapshot(
        self,
        snapshot: WaveformSnapshot,
        roi_start: int,
        roi_stop: int | None,
    ) -> WaveformAnalysis | None:
        if snapshot.value is None:
            return None
        try:
            return analyze_waveform(
                snapshot.value,
                threshold_fraction=self.threshold_spin.value() / 100.0,
                baseline_fraction=self.config.baseline_fraction,
                roi_start=roi_start,
                roi_stop=roi_stop,
            )
        except (TypeError, ValueError):
            return None

    def _render_trace(
        self,
        device: str,
        analysis: WaveformAnalysis | None,
        display_mode: str,
        *,
        active: bool,
    ) -> None:
        if self.plot is None:
            return
        visible = self.trace_widgets[device].visible.isChecked()
        curve = self.curves[device]
        marker = self.edge_markers[device]
        if analysis is None or not visible:
            curve.setData([], [])
            marker.hide()
            return
        y_values = analysis.raw if display_mode == "raw" else analysis.normalized
        curve.setData(np.arange(y_values.size, dtype=float), y_values)
        curve.setPen(pg.mkPen(TRACE_COLORS[device], width=2 if active else 1))
        curve.setOpacity(1.0 if active else 0.28)
        if analysis.edge_position is None or not active:
            marker.hide()
        else:
            marker.setValue(analysis.edge_position)
            marker.show()

    def _render_results(
        self,
        analyses: dict[str, WaveformAnalysis],
        usable: set[str],
    ) -> None:
        reference = str(self.reference_combo.currentData() or "")
        reference_analysis = analyses.get(reference)
        reference_usable = (
            self.config.shared_time_origin
            and reference in usable
            and self.trace_widgets.get(reference) is not None
            and self.trace_widgets[reference].visible.isChecked()
            and reference_analysis is not None
            and reference_analysis.edge_position is not None
        )
        for device in WAVEFORM_DEVICES:
            result = self.trace_widgets[device].result
            analysis = analyses.get(device)
            if (
                device not in usable
                or not self.trace_widgets[device].visible.isChecked()
                or analysis is None
                or analysis.edge_position is None
            ):
                result.setText("Unavailable")
                continue
            edge_text = f"Edge {analysis.edge_position:.3f} samples"
            if self.config.sample_rate_mhz is not None:
                local_time_us = analysis.edge_position / self.config.sample_rate_mhz
                edge_text += f" · {local_time_us:.6f} μs local"
            if not self.config.shared_time_origin:
                result.setText(edge_text)
                continue
            if not reference_usable or reference_analysis is None:
                result.setText(f"{edge_text} · Δ unavailable")
                continue
            offset = analysis.edge_position - float(reference_analysis.edge_position)
            if device == reference:
                result.setText(f"{edge_text} · Δ 0.000 samples")
            else:
                direction = "Later" if offset > 0.0 else "Earlier" if offset < 0.0 else "Aligned"
                offset_text = f"Δ {offset:+.3f} samples"
                if self.config.sample_rate_mhz is not None:
                    offset_text += (
                        f" / {offset / self.config.sample_rate_mhz:+.6f} μs"
                    )
                result.setText(f"{edge_text} · {offset_text} · {direction}")

    def roi_bounds(self) -> tuple[int, int | None]:
        if self.roi is None or not self._roi_initialized:
            return 0, None
        low, high = sorted(float(value) for value in self.roi.getRegion())
        return max(0, int(math.floor(low))), max(1, int(math.floor(high)) + 1)

    def reset_roi(self) -> None:
        if self.roi is None or self._latest_length < 2:
            return
        self.roi.setRegion((0.0, float(self._latest_length - 1)))
        self._roi_initialized = True
        self.refresh_now()

    def fit_view(self) -> None:
        if self.plot is not None:
            self.plot.enableAutoRange()

    def _set_frozen(self, frozen: bool) -> None:
        self.freeze_button.setText("Frozen" if frozen else "Freeze")
        if not frozen:
            self.refresh_now()

    def _hide_trace(self, device: str) -> None:
        if self.plot is not None:
            self.curves[device].setData([], [])
            self.edge_markers[device].hide()

    def _clear_plot(self) -> None:
        for device in WAVEFORM_DEVICES:
            self._hide_trace(device)
            self.trace_widgets[device].result.setText("Unavailable")

    def apply_theme(self, palette: dict[str, str]) -> None:
        self._palette = dict(palette)
        if self.plot is None:
            return
        self.plot.setBackground(palette["input"])
        for axis_name in ("left", "bottom"):
            axis = self.plot.getAxis(axis_name)
            axis.setPen(palette["muted"])
            axis.setTextPen(palette["muted"])

    def close(self) -> None:
        self.refresh_timer.stop()
        self.monitor.close()
