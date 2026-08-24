from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file()
)
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

from PyQt5.QtCore import Qt, QSignalBlocker, QTimer
from PyQt5.QtGui import QDoubleValidator
from PyQt5.QtWidgets import (
    QApplication,
    QAbstractSpinBox,
    QButtonGroup,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from half_linac.src.apps.power_source_timing.epics_client import (
    BatchWriteWorker,
    GroupMonitor,
)
from half_linac.src.apps.power_source_timing.model import (
    DEVICES,
    CoalescingWriteQueue,
    TimingValues,
    ValueKey,
)
from half_linac.src.apps.power_source_timing.profile_runtime import (
    TimingGroup,
    TimingRuntime,
    load_timing_runtime,
)
from half_linac.src.apps.power_source_timing.waveform_view import WaveformAlignmentWidget
from half_linac.src.shared.app_theme import resolve_initial_theme
from half_linac.src.shared.machine_profile import RuntimeContextWidget


DEVICE_LABELS = {"hv": "HV", "llrf": "LLRF", "ssa": "SSA", "kly": "KLY"}


DARK = {
    "window": "#0f1519",
    "panel": "#172027",
    "input": "#10171c",
    "border": "#2a3943",
    "text": "#e6edf2",
    "muted": "#91a2ad",
    "accent": "#45d0bc",
    "warning": "#e4b86f",
    "danger": "#e37878",
}
LIGHT = {
    "window": "#f2ede5",
    "panel": "#fffdf9",
    "input": "#fffdf9",
    "border": "#d7cec1",
    "text": "#2c3942",
    "muted": "#746c62",
    "accent": "#2d7f6d",
    "warning": "#a97118",
    "danger": "#b44141",
}


def _stylesheet(palette: dict[str, str]) -> str:
    return f"""
QMainWindow, QWidget {{ background: {palette['window']}; color: {palette['text']};
  font-family: "IBM Plex Sans", "Source Han Sans SC", "Segoe UI", sans-serif;
  font-size: 12px; }}
QFrame#panel {{ background: {palette['panel']}; border: 1px solid {palette['border']};
  border-radius: 12px; }}
QLabel {{ background: transparent; border: none; }}
QLabel#title {{ font-size: 22px; font-weight: 700; }}
QLabel[role="sectionTitle"] {{ font-size: 13px; font-weight: 700; }}
QLabel[role="field"] {{ color: {palette['muted']}; font-size: 11px; font-weight: 700; }}
QLabel[role="value"] {{ font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace; }}
QLabel[tone="success"] {{ color: {palette['accent']}; }}
QLabel[tone="warning"] {{ color: {palette['warning']}; }}
QLabel[tone="danger"] {{ color: {palette['danger']}; }}
QPushButton, QToolButton, QComboBox, QDoubleSpinBox {{
  background: {palette['input']}; color: {palette['text']};
  border: 1px solid {palette['border']}; border-radius: 8px;
  min-height: 26px; padding: 1px 7px; }}
QPushButton:hover, QToolButton:hover, QComboBox:hover, QDoubleSpinBox:focus {{
  border-color: {palette['accent']}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {palette['panel']}; }}
QPushButton:checked, QToolButton:checked {{ color: {palette['accent']};
  border-color: {palette['accent']}; font-weight: 700; }}
QPushButton:disabled, QToolButton:disabled {{ color: {palette['muted']}; }}
QPushButton[triggerState="enabled"] {{ color: {palette['accent']};
  border-color: {palette['accent']}; font-weight: 700; }}
QPushButton[triggerState="disabled"] {{ color: {palette['muted']}; }}
QPushButton[triggerState="pending"] {{ color: {palette['warning']};
  border-color: {palette['warning']}; font-weight: 700; }}
QPushButton[triggerState="unavailable"] {{ color: {palette['muted']}; }}
QCheckBox {{ background: transparent; spacing: 6px; }}
QHeaderView::section {{ background: {palette['panel']}; }}
QStatusBar {{ background: {palette['panel']}; color: {palette['muted']}; }}
QSplitter::handle {{ background: {palette['border']}; height: 3px; }}
"""


@dataclass
class ChannelWidgets:
    channel_label: QLabel
    enable: QPushButton
    delay_target: "RequestSpinBox"
    delay_setpoint: QLabel
    delay_readback: QLabel
    delay_advance: QToolButton
    delay_delay: QToolButton
    width_target: "RequestSpinBox"
    width_setpoint: QLabel
    width_readback: QLabel
    width_decrease: QToolButton
    width_increase: QToolButton
    status: QLabel


class RequestSpinBox(QDoubleSpinBox):
    """Numeric request editor that distinguishes no AO value from a real zero."""

    def __init__(self, parent: QWidget | None = None) -> None:
        self._request_initialized = False
        super().__init__(parent)

    def textFromValue(self, value: float) -> str:
        if not getattr(self, "_request_initialized", False):
            return "—"
        return super().textFromValue(value)

    def clear_request(self) -> None:
        self._request_initialized = False
        self.setEnabled(False)
        self.lineEdit().setText("—")

    def set_request_value(self, value: float) -> None:
        self._request_initialized = True
        self.setValue(value)
        # setValue() does not repaint when the first AO is a real zero.
        self.lineEdit().setText(self.textFromValue(self.value()))


class TimingWindow(QMainWindow):
    def __init__(self, runtime: TimingRuntime) -> None:
        super().__init__()
        self.runtime = runtime
        self.groups = {group.element_id: group for group in runtime.groups}
        self.current_group: TimingGroup | None = None
        self.values = TimingValues(minimum_us=runtime.minimum_us)
        self.queue = CoalescingWriteQueue()
        self.connected: dict[tuple[str, str], bool] = {}
        self.failed_devices: set[str] = set()
        self.failed_trigger_devices: set[str] = set()
        self.enable_requests: dict[str, bool] = {}
        self.external_resync_keys: set[ValueKey] = set()
        self._adjustments_suspended = False
        self._worker: BatchWriteWorker | None = None
        self._batch_results: dict[ValueKey, bool] = {}
        self._batch_error = ""
        self.monitor = GroupMonitor(self)
        self.monitor.value_changed.connect(self._on_pv_value)
        self.monitor.connection_changed.connect(self._on_connection)
        self.channel_widgets: dict[str, ChannelWidgets] = {}
        self.group_buttons: dict[str, QPushButton] = {}
        self.adjust_buttons: list[QToolButton] = []
        self._theme = resolve_initial_theme()

        self.setWindowTitle(
            f"{self.runtime.context.machine.display_name} · RF Power Source Timing"
        )
        self.resize(1450, 900)
        self.setMinimumSize(1120, 720)
        self._build_ui()
        self._apply_theme()
        self._select_group(runtime.default_element)

    def _build_ui(self) -> None:
        root = QWidget(self)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(12, 10, 12, 8)
        outer.setSpacing(8)

        heading = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("RF Power Source Timing", root)
        title.setObjectName("title")
        title_box.addWidget(title)
        heading.addLayout(title_box)
        heading.addStretch(1)
        heading.addWidget(
            RuntimeContextWidget(
                machine_id=self.runtime.context.machine.id,
                machine_display_name=self.runtime.context.machine.display_name,
                control_backend=self.runtime.context.control_backend.name,
                parent=root,
            )
        )
        self.theme_button = QToolButton(root)
        self.theme_button.setFixedSize(32, 32)
        self.theme_button.clicked.connect(self._toggle_theme)
        heading.addWidget(self.theme_button)
        outer.addLayout(heading)

        selector_panel = QFrame(root)
        selector_panel.setObjectName("panel")
        selector_layout = QVBoxLayout(selector_panel)
        selector_layout.setContentsMargins(10, 7, 10, 8)
        selector_layout.setSpacing(5)
        selector_title = QLabel("Power Source Group", selector_panel)
        selector_title.setProperty("role", "field")
        selector_layout.addWidget(selector_title)
        selector_grid = QGridLayout()
        selector_grid.setHorizontalSpacing(6)
        selector_grid.setVerticalSpacing(6)
        self.group_button_group = QButtonGroup(self)
        self.group_button_group.setExclusive(True)
        selector_columns = min(11, max(1, math.ceil(len(self.runtime.groups) / 2)))
        for index, group in enumerate(self.runtime.groups):
            button = QPushButton(group.element_id, selector_panel)
            button.setCheckable(True)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button.setToolTip(group.display_name)
            button.clicked.connect(
                lambda _checked=False, element_id=group.element_id: self._select_group(element_id)
            )
            selector_grid.addWidget(
                button,
                index // selector_columns,
                index % selector_columns,
            )
            self.group_button_group.addButton(button)
            self.group_buttons[group.element_id] = button
        selector_layout.addLayout(selector_grid)
        outer.addWidget(selector_panel)

        self.splitter = QSplitter(Qt.Vertical, root)
        self.splitter.setChildrenCollapsible(False)
        self.waveform_view = WaveformAlignmentWidget(
            self.runtime.waveform_alignment, self.splitter
        )
        self.splitter.addWidget(self.waveform_view)

        controls = QFrame(root)
        controls.setObjectName("panel")
        controls.setToolTip(
            "Linked adjustment changes delay only for the selected RF chain. "
            "Disabled channels still accept "
            f"delay/width presets. Set/readback tolerance: "
            f"{self.runtime.readback_tolerance_us:g} μs."
        )
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(10, 8, 10, 9)
        controls_layout.setSpacing(6)
        controls_title = QLabel("Timing Controls", controls)
        controls_title.setProperty("role", "sectionTitle")
        controls_layout.addWidget(controls_title)

        table = QGridLayout()
        table.setHorizontalSpacing(6)
        table.setVerticalSpacing(4)
        table.addWidget(self._field_label("Channel"), 0, 0, 2, 1)
        table.addWidget(self._field_label("Trigger"), 0, 1, 2, 1)
        delay_header = self._field_label("Delay")
        delay_header.setAlignment(Qt.AlignCenter)
        table.addWidget(delay_header, 0, 2, 1, 5)
        width_header = self._field_label("Width")
        width_header.setAlignment(Qt.AlignCenter)
        table.addWidget(width_header, 0, 7, 1, 5)
        table.addWidget(self._field_label("Status"), 0, 12, 2, 1)
        for column, text in enumerate(
            ("Request", "", "", "Set", "Readback", "Request", "", "", "Set", "Readback"),
            start=2,
        ):
            table.addWidget(self._field_label(text), 1, column)
        for row, device in enumerate(DEVICES, start=2):
            widgets = self._build_channel_row(device, controls)
            self.channel_widgets[device] = widgets
            row_widgets = (
                widgets.channel_label,
                widgets.enable,
                widgets.delay_target,
                widgets.delay_advance,
                widgets.delay_delay,
                widgets.delay_setpoint,
                widgets.delay_readback,
                widgets.width_target,
                widgets.width_decrease,
                widgets.width_increase,
                widgets.width_setpoint,
                widgets.width_readback,
                widgets.status,
            )
            for column, widget in enumerate(row_widgets):
                table.addWidget(widget, row, column)
        group_row = 2 + len(DEVICES)
        self.linked_delay_label = self._field_label("Linked Delay")
        self.linked_delay_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        table.addWidget(self.linked_delay_label, group_row, 0, 1, 3)
        self.group_advance = self._repeat_button("◀")
        self.group_delay = self._repeat_button("▶")
        self.group_advance.setToolTip("Move all linked delays earlier")
        self.group_delay.setToolTip("Move all linked delays later")
        self.group_advance.clicked.connect(lambda: self._shift_group_by_step(-1.0))
        self.group_delay.clicked.connect(lambda: self._shift_group_by_step(1.0))
        table.addWidget(self.group_advance, group_row, 3)
        table.addWidget(self.group_delay, group_row, 4)

        step_row = group_row + 1
        delay_step_label = self._field_label("Delay Step")
        delay_step_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        table.addWidget(delay_step_label, step_row, 2)
        self.delay_step = self._step_combo(self.runtime.delay_step_us)
        table.addWidget(self.delay_step, step_row, 3, 1, 2)
        table.addWidget(self._field_label("μs"), step_row, 5)

        width_step_label = self._field_label("Width Step")
        width_step_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        table.addWidget(width_step_label, step_row, 7)
        self.width_step = self._step_combo(self.runtime.width_step_us)
        table.addWidget(self.width_step, step_row, 8, 1, 2)
        table.addWidget(self._field_label("μs"), step_row, 10)

        table.setColumnStretch(12, 1)
        controls_layout.addLayout(table)

        self.splitter.addWidget(controls)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setSizes([500, 310])
        outer.addWidget(self.splitter, 1)
        self.setCentralWidget(root)
        status = QStatusBar(self)
        self.setStatusBar(status)
        self.statusBar().showMessage("Waiting for PV data")

    def _toggle_theme(self) -> None:
        self._theme = "light" if self._theme == "dark" else "dark"
        self._apply_theme()

    def _apply_theme(self) -> None:
        palette = DARK if self._theme == "dark" else LIGHT
        self.setStyleSheet(_stylesheet(palette))
        self.theme_button.setText("☀" if self._theme == "dark" else "☾")
        self.theme_button.setToolTip(
            "Switch to light theme" if self._theme == "dark" else "Switch to dark theme"
        )
        self.waveform_view.apply_theme(palette)

    def _build_channel_row(self, device: str, parent: QWidget) -> ChannelWidgets:
        channel_label = QLabel(DEVICE_LABELS[device], parent)
        enable = QPushButton("Unavailable", parent)
        enable.setCheckable(True)
        enable.setMinimumWidth(96)
        enable.setToolTip(
            "Enable or disable the timing trigger. This does not switch off "
            "the power source."
        )
        enable.clicked.connect(
            lambda checked, name=device: self._set_enable(name, checked)
        )

        delay_target = self._value_spin(parent)
        delay_target.editingFinished.connect(
            lambda name=device: self._set_absolute(name, "delay")
        )
        delay_advance = self._repeat_button("◀")
        delay_delay = self._repeat_button("▶")
        delay_advance.clicked.connect(
            lambda _checked=False, name=device: self._shift_one_by_step(name, "delay", -1.0)
        )
        delay_delay.clicked.connect(
            lambda _checked=False, name=device: self._shift_one_by_step(name, "delay", 1.0)
        )

        width_target = self._value_spin(parent)
        width_target.editingFinished.connect(
            lambda name=device: self._set_absolute(name, "width")
        )
        width_decrease = self._repeat_button("−")
        width_increase = self._repeat_button("+")
        width_decrease.clicked.connect(
            lambda _checked=False, name=device: self._shift_one_by_step(name, "width", -1.0)
        )
        width_increase.clicked.connect(
            lambda _checked=False, name=device: self._shift_one_by_step(name, "width", 1.0)
        )
        return ChannelWidgets(
            channel_label=channel_label,
            enable=enable,
            delay_target=delay_target,
            delay_setpoint=self._value_label(),
            delay_readback=self._value_label(),
            delay_advance=delay_advance,
            delay_delay=delay_delay,
            width_target=width_target,
            width_setpoint=self._value_label(),
            width_readback=self._value_label(),
            width_decrease=width_decrease,
            width_increase=width_increase,
            status=self._status_label(),
        )

    def _repeat_button(self, text: str) -> QToolButton:
        button = QToolButton(self)
        button.setText(text)
        button.setAutoRepeat(True)
        button.setAutoRepeatDelay(self.runtime.button_repeat_delay_ms)
        button.setAutoRepeatInterval(self.runtime.button_repeat_interval_ms)
        button.setMinimumWidth(42)
        self.adjust_buttons.append(button)
        return button

    def _step_combo(self, default: float) -> QComboBox:
        combo = QComboBox(self)
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.NoInsert)
        for value in self.runtime.step_choices_us:
            combo.addItem(f"{value:g}", value)
        selected = min(
            range(combo.count()),
            key=lambda index: abs(float(combo.itemData(index)) - default),
        )
        combo.setCurrentIndex(selected)
        combo.lineEdit().setValidator(QDoubleValidator(0.000001, 1.0e9, 6, combo))
        combo.setMinimumWidth(84)
        combo.setToolTip("Select a preset or enter a positive step value")
        return combo

    def _value_spin(self, parent: QWidget) -> RequestSpinBox:
        spin = RequestSpinBox(parent)
        spin.setRange(self.runtime.minimum_us, 1.0e9)
        spin.setDecimals(6)
        spin.setKeyboardTracking(False)
        spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        spin.setMinimumWidth(115)
        spin.setEnabled(False)
        return spin

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("role", "field")
        return label

    @staticmethod
    def _value_label() -> QLabel:
        label = QLabel("—")
        label.setProperty("role", "value")
        label.setMinimumWidth(72)
        return label

    @staticmethod
    def _status_label() -> QLabel:
        label = QLabel("Waiting for connection")
        label.setProperty("tone", "warning")
        label.setMinimumWidth(150)
        return label

    def _select_group(self, element_id: str) -> None:
        if self.queue.busy or self.queue.pending:
            return
        group = self.groups[element_id]
        self.current_group = group
        self.values = TimingValues(
            minimum_us=self.runtime.minimum_us,
            devices=group.devices,
        )
        self.connected.clear()
        self.failed_devices.clear()
        self.failed_trigger_devices.clear()
        self.enable_requests.clear()
        self.external_resync_keys.clear()
        for device in DEVICES:
            self._clear_row(device)
            self._set_channel_row_visible(device, device in group.devices)
        self.linked_delay_label.setText(
            f"Linked Delay ({len(group.devices)} channels)"
        )
        self._refresh_adjustment_controls()
        with QSignalBlocker(self.group_buttons[element_id]):
            self.group_buttons[element_id].setChecked(True)
        self.monitor.bind(group)
        self.waveform_view.bind(group)
        self.statusBar().showMessage(f"Selected {element_id}; connecting PVs")

    def _clear_row(self, device: str) -> None:
        row = self.channel_widgets[device]
        self._set_trigger_button(
            row.enable,
            text="Unavailable",
            checked=False,
            enabled=False,
            state="unavailable",
            tooltip="Trigger state is unavailable because the enable PV is disconnected.",
        )
        for spin in (row.delay_target, row.width_target):
            with QSignalBlocker(spin):
                spin.clear_request()
        for label in (
            row.delay_setpoint,
            row.delay_readback,
            row.width_setpoint,
            row.width_readback,
        ):
            label.setText("—")
        self._set_status(row.status, "Waiting for connection", "warning")

    def _set_channel_row_visible(self, device: str, visible: bool) -> None:
        row = self.channel_widgets[device]
        for widget in (
            row.channel_label,
            row.enable,
            row.delay_target,
            row.delay_setpoint,
            row.delay_readback,
            row.delay_advance,
            row.delay_delay,
            row.width_target,
            row.width_setpoint,
            row.width_readback,
            row.width_decrease,
            row.width_increase,
            row.status,
        ):
            widget.setVisible(visible)

    def _on_connection(self, device: str, field: str, connected: bool) -> None:
        self.connected[(device, field)] = connected
        if not connected:
            self._cancel_unavailable_pending()
        self._refresh_row(device)

    def _on_pv_value(self, device: str, field: str, raw_value: object) -> None:
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            return
        if not math.isfinite(value):
            return
        if field == "enable":
            self.values.set_enabled(device, bool(round(value)))
        elif field.endswith("_set"):
            quantity = field.removesuffix("_set")
            key = (device, quantity)
            active = key in self.queue.active_keys
            if active:
                expected = [
                    mapping[key]
                    for mapping in (self.queue.inflight, self.queue.pending)
                    if key in mapping
                ]
                previous = self.values.setpoint.get(key)
                if previous is not None:
                    expected.append(previous)
                if expected and all(
                    abs(value - target) > self.runtime.readback_tolerance_us
                    for target in expected
                ):
                    self.queue.pending.pop(key, None)
                    if key in self.queue.inflight:
                        self.external_resync_keys.add(key)
                    self.statusBar().showMessage(
                        f"External setpoint change detected for {device.upper()} {quantity}; "
                        "pending target cancelled"
                    )
            self.values.sync_setpoint(
                device,
                quantity,
                value,
                follow_target=key not in self.queue.active_keys,
            )
        elif field.endswith("_readback"):
            quantity = field.removesuffix("_readback")
            self.values.sync_readback(device, quantity, value)
        self._refresh_row(device)

    def _refresh_row(self, device: str) -> None:
        row = self.channel_widgets[device]
        for quantity, spin, set_label, read_label in (
            ("delay", row.delay_target, row.delay_setpoint, row.delay_readback),
            ("width", row.width_target, row.width_setpoint, row.width_readback),
        ):
            key = (device, quantity)
            if key in self.values.target and not spin.hasFocus():
                with QSignalBlocker(spin):
                    spin.set_request_value(self.values.target[key])
            spin.setEnabled(
                key in self.values.target
                and self.connected.get((device, f"{quantity}_set"), False)
            )
            set_label.setText(self._format_value(self.values.setpoint.get(key)))
            read_label.setText(self._format_value(self.values.readback.get(key)))
        self._refresh_adjustment_controls()
        self._refresh_trigger_button(device)
        if device in self.failed_trigger_devices:
            self._set_status(row.status, "Trigger write failed", "danger")
            return
        if device in self.failed_devices:
            self._set_status(row.status, "Write failed", "danger")
            return
        if device in self.enable_requests:
            self._set_status(row.status, "Updating trigger", "warning")
            return
        required = (
            "delay_set",
            "delay_readback",
            "width_set",
            "width_readback",
            "enable",
        )
        if not all(self.connected.get((device, field), False) for field in required):
            self._set_status(row.status, "Channels not fully connected", "danger")
            return
        delay_match = self.values.matches(
            device, "delay", self.runtime.readback_tolerance_us
        )
        width_match = self.values.matches(
            device, "width", self.runtime.readback_tolerance_us
        )
        if delay_match is None or width_match is None:
            self._set_status(row.status, "Waiting for readback", "warning")
        elif not delay_match or not width_match:
            self._set_status(row.status, "Readback following", "warning")
        elif not self.values.enabled.get(device, False):
            self._set_status(
                row.status, "Trigger disabled · presets allowed", "warning"
            )
        else:
            self._set_status(row.status, "Set/readback matched", "success")

    def _quantity_ready(self, device: str, quantity: str) -> bool:
        return (
            (device, quantity) in self.values.target
            and self.connected.get((device, f"{quantity}_set"), False)
        )

    def _group_delay_ready(self) -> bool:
        return self.current_group is not None and all(
            self._quantity_ready(device, "delay")
            for device in self.current_group.devices
        )

    def _refresh_adjustment_controls(self) -> None:
        if self._adjustments_suspended:
            for button in self.adjust_buttons:
                button.setEnabled(False)
            return
        for device in DEVICES:
            row = self.channel_widgets[device]
            delay_ready = self._quantity_ready(device, "delay")
            width_ready = self._quantity_ready(device, "width")
            row.delay_advance.setEnabled(delay_ready)
            row.delay_delay.setEnabled(delay_ready)
            row.width_decrease.setEnabled(width_ready)
            row.width_increase.setEnabled(width_ready)
        group_ready = self._group_delay_ready()
        self.group_advance.setEnabled(group_ready)
        self.group_delay.setEnabled(group_ready)

    def _resync_external_setpoints(self) -> None:
        keys = set(self.external_resync_keys)
        self.external_resync_keys.clear()
        devices: set[str] = set()
        for key in keys:
            if key not in self.values.setpoint:
                continue
            self.values.target[key] = self.values.setpoint[key]
            devices.add(key[0])
        for device in devices:
            self._refresh_row(device)

    def _write_key_connected(self, key: ValueKey) -> bool:
        device, quantity = key
        field = "enable" if quantity == "enable" else f"{quantity}_set"
        return self.connected.get((device, field), False)

    def _cancel_unavailable_pending(self) -> None:
        unavailable = {
            key for key in self.queue.pending if not self._write_key_connected(key)
        }
        if any(quantity == "delay" for _device, quantity in unavailable):
            # A queued linked move must not degrade into a partial move.
            unavailable.update(
                key for key in self.queue.pending if key[1] == "delay"
            )
        if not unavailable:
            return
        devices: set[str] = set()
        for key in unavailable:
            self.queue.pending.pop(key, None)
            device, quantity = key
            if quantity == "enable":
                self.enable_requests.pop(device, None)
            elif key in self.values.setpoint:
                self.values.target[key] = self.values.setpoint[key]
            devices.add(device)
        for device in devices:
            self._refresh_row(device)
        self.statusBar().showMessage(
            "Pending write cancelled because a Set channel disconnected"
        )

    def _refresh_trigger_button(self, device: str) -> None:
        button = self.channel_widgets[device].enable
        requested = self.enable_requests.get(device)
        if requested is not None:
            self._set_trigger_button(
                button,
                text="Enabling…" if requested else "Disabling…",
                checked=requested,
                enabled=False,
                state="pending",
                tooltip="Waiting for the trigger enable write to complete.",
            )
            return
        if not self.connected.get((device, "enable"), False):
            self._set_trigger_button(
                button,
                text="Unavailable",
                checked=False,
                enabled=False,
                state="unavailable",
                tooltip="Trigger state is unavailable because the enable PV is disconnected.",
            )
            return
        if device not in self.values.enabled:
            self._set_trigger_button(
                button,
                text="Waiting…",
                checked=False,
                enabled=False,
                state="unavailable",
                tooltip="Connected; waiting for the initial trigger state.",
            )
            return
        actual = self.values.enabled[device]
        self._set_trigger_button(
            button,
            text="Enabled" if actual else "Disabled",
            checked=actual,
            enabled=True,
            state="enabled" if actual else "disabled",
            tooltip=(
                "Click to disable the timing trigger. This does not switch off the "
                "power source."
                if actual
                else "Click to enable the timing trigger. Delay and width presets "
                "remain writable while disabled."
            ),
        )

    @staticmethod
    def _set_trigger_button(
        button: QPushButton,
        *,
        text: str,
        checked: bool,
        enabled: bool,
        state: str,
        tooltip: str,
    ) -> None:
        with QSignalBlocker(button):
            button.setChecked(checked)
            button.setText(text)
        button.setEnabled(enabled)
        button.setToolTip(tooltip)
        button.setProperty("triggerState", state)
        button.style().unpolish(button)
        button.style().polish(button)

    @staticmethod
    def _format_value(value: float | None) -> str:
        return "—" if value is None else f"{value:.6f}"

    @staticmethod
    def _set_status(label: QLabel, text: str, tone: str) -> None:
        label.setText(text)
        label.setProperty("tone", tone)
        label.style().unpolish(label)
        label.style().polish(label)

    def _delay_step(self) -> float:
        return self._step_value(self.delay_step)

    def _width_step(self) -> float:
        return self._step_value(self.width_step)

    @staticmethod
    def _step_value(combo: QComboBox) -> float:
        try:
            value = float(combo.currentText().strip())
        except ValueError as exc:
            raise ValueError("Step must be a positive number") from exc
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError("Step must be a positive number")
        return value

    def _shift_group_by_step(self, direction: float) -> None:
        try:
            step = self._delay_step()
        except ValueError as exc:
            self.statusBar().showMessage(str(exc))
            return
        self._shift_group(direction * step)

    def _shift_one_by_step(self, device: str, quantity: str, direction: float) -> None:
        try:
            step = self._delay_step() if quantity == "delay" else self._width_step()
        except ValueError as exc:
            self.statusBar().showMessage(str(exc))
            return
        self._shift_one(device, quantity, direction * step)

    def _shift_group(self, delta_us: float) -> None:
        if not self._group_delay_ready():
            self.statusBar().showMessage(
                "All linked delay Set channels must be connected and initialized"
            )
            return
        try:
            values = self.values.shift_group_delay(delta_us)
        except ValueError as exc:
            self.statusBar().showMessage(str(exc))
            return
        self._enqueue(values)

    def _shift_one(self, device: str, quantity: str, delta_us: float) -> None:
        if not self._quantity_ready(device, quantity):
            self.statusBar().showMessage(
                f"{device.upper()} {quantity} Set channel is not available"
            )
            return
        try:
            values = self.values.shift_one(device, quantity, delta_us)
        except ValueError as exc:
            self.statusBar().showMessage(str(exc))
            return
        self._enqueue(values)

    def _set_absolute(self, device: str, quantity: str) -> None:
        row = self.channel_widgets[device]
        spin = row.delay_target if quantity == "delay" else row.width_target
        if not self._quantity_ready(device, quantity):
            self.statusBar().showMessage(
                f"{device.upper()} {quantity} Set channel is not available"
            )
            self._refresh_row(device)
            return
        try:
            values = self.values.request_value(device, quantity, spin.value())
        except ValueError as exc:
            self.statusBar().showMessage(str(exc))
            self._refresh_row(device)
            return
        self._enqueue(values)

    def _set_enable(self, device: str, enabled: bool) -> None:
        if (
            self.current_group is None
            or not self.connected.get((device, "enable"), False)
            or device not in self.values.enabled
            or device in self.enable_requests
        ):
            self._refresh_trigger_button(device)
            return
        self.enable_requests[device] = enabled
        self._enqueue({(device, "enable"): 1.0 if enabled else 0.0})

    def _enqueue(self, values: dict[ValueKey, float]) -> None:
        self.failed_devices.difference_update(device for device, _field in values)
        self.failed_trigger_devices.difference_update(
            device for device, field in values if field == "enable"
        )
        self.queue.enqueue(values)
        for device, _quantity in values:
            self._refresh_row(device)
        self._start_next_write()

    def _start_next_write(self) -> None:
        if self._worker is not None or self.current_group is None:
            return
        self._cancel_unavailable_pending()
        batch = self.queue.begin_next()
        if not batch:
            self._set_group_selection_enabled(True)
            return
        pv_values: dict[ValueKey, tuple[str, float]] = {}
        for key, value in batch.items():
            device, quantity = key
            field = "enable" if quantity == "enable" else f"{quantity}_set"
            pv_values[key] = (self.current_group.pv(device, field), value)
        self._set_group_selection_enabled(False)
        self._batch_results = {}
        self._batch_error = ""
        worker = BatchWriteWorker(pv_values, parent=self)
        self._worker = worker
        worker.completed.connect(self._on_batch_completed)
        worker.finished.connect(self._on_worker_finished)
        worker.start()
        self.statusBar().showMessage(f"Writing {len(batch)} timing parameter(s)")

    def _on_batch_completed(self, results: object, error: str) -> None:
        self._batch_results = dict(results) if isinstance(results, dict) else {}
        self._batch_error = str(error)

    def _on_worker_finished(self) -> None:
        completed = self.queue.finish()
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        failed = {
            key for key in completed if not self._batch_results.get(key, False)
        }
        if failed:
            abandoned = set(self.queue.pending)
            self.queue.pending.clear()
            for key in failed | abandoned:
                if key[1] == "enable":
                    self.enable_requests.pop(key[0], None)
                    if key in failed:
                        self.failed_trigger_devices.add(key[0])
                if key[1] in ("delay", "width") and key in self.values.setpoint:
                    self.values.target[key] = self.values.setpoint[key]
                self.failed_devices.add(key[0])
                self._refresh_row(key[0])
            self._resync_external_setpoints()
            self._stop_auto_repeat()
            message = self._batch_error or "EPICS write failed"
            self.statusBar().showMessage(message)
            self._set_group_selection_enabled(True)
            return
        completed_devices = {device for device, _field in completed}
        for device, field in completed:
            if field == "enable":
                self.enable_requests.pop(device, None)
        for device in completed_devices:
            self._refresh_row(device)
        self._resync_external_setpoints()
        if self.queue.pending:
            self._start_next_write()
        else:
            self._set_group_selection_enabled(True)
            self.statusBar().showMessage("Write complete; waiting for readback")

    def _set_group_selection_enabled(self, enabled: bool) -> None:
        for button in self.group_buttons.values():
            button.setEnabled(enabled)

    def _stop_auto_repeat(self) -> None:
        self._adjustments_suspended = True
        for button in self.adjust_buttons:
            button.setEnabled(False)
        QTimer.singleShot(200, self._resume_adjust_buttons)

    def _resume_adjust_buttons(self) -> None:
        self._adjustments_suspended = False
        self._refresh_adjustment_controls()

    def closeEvent(self, event) -> None:
        self.monitor.close()
        self.waveform_view.close()
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(6500)
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    try:
        runtime = load_timing_runtime()
    except Exception as exc:
        QMessageBox.critical(None, "RF Power Source Timing", str(exc))
        return 2
    window = TimingWindow(runtime)
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
