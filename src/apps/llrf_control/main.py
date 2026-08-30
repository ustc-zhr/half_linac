from __future__ import annotations

import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file()
)
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

from PyQt5.QtCore import QSignalBlocker, QTimer
from PyQt5.QtGui import QDoubleValidator
from PyQt5.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
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
    QStatusBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from half_linac.src.apps.llrf_control.epics_client import LlrfMonitor, WriteWorker
from half_linac.src.apps.llrf_control.model import CoalescingWriteQueue
from half_linac.src.apps.llrf_control.profile_runtime import (
    QUANTITIES,
    LlrfGroup,
    LlrfRuntime,
    QuantityConfig,
    load_llrf_runtime,
)
from half_linac.src.shared.app_theme import resolve_initial_theme
from half_linac.src.shared.machine_profile import RuntimeContextWidget


DARK = {
    "window": "#0f1519", "panel": "#172027", "input": "#10171c",
    "border": "#2a3943", "text": "#e6edf2", "muted": "#91a2ad",
    "accent": "#45d0bc", "warning": "#e4b86f", "danger": "#e37878",
}
LIGHT = {
    "window": "#f2ede5", "panel": "#fffdf9", "input": "#fffdf9",
    "border": "#d7cec1", "text": "#2c3942", "muted": "#746c62",
    "accent": "#2d7f6d", "warning": "#a97118", "danger": "#b44141",
}


def _stylesheet(palette: dict[str, str]) -> str:
    return f"""
QMainWindow, QWidget {{ background: {palette['window']}; color: {palette['text']};
  font-family: "IBM Plex Sans", "Source Han Sans SC", "Segoe UI", sans-serif;
  font-size: 12px; }}
QFrame#panel {{ background: {palette['panel']}; border: 1px solid {palette['border']};
  border-radius: 8px; }}
QLabel {{ background: transparent; border: none; }}
QLabel#title {{ font-size: 22px; font-weight: 700; }}
QLabel[role="field"] {{ color: {palette['muted']}; font-size: 11px; font-weight: 700; }}
QLabel[role="quantity"] {{ font-size: 14px; font-weight: 700; }}
QLabel[role="value"] {{ font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace; }}
QLabel[tone="success"] {{ color: {palette['accent']}; }}
QLabel[tone="warning"] {{ color: {palette['warning']}; }}
QLabel[tone="danger"] {{ color: {palette['danger']}; }}
QPushButton, QToolButton, QComboBox, QDoubleSpinBox {{
  background: {palette['input']}; color: {palette['text']};
  border: 1px solid {palette['border']}; border-radius: 6px;
  min-height: 28px; padding: 1px 7px; }}
QPushButton:hover, QToolButton:hover, QComboBox:hover, QDoubleSpinBox:focus {{
  border-color: {palette['accent']}; }}
QPushButton:checked {{ color: {palette['accent']}; border-color: {palette['accent']};
  font-weight: 700; }}
QPushButton:disabled, QToolButton:disabled, QComboBox:disabled, QDoubleSpinBox:disabled {{
  color: {palette['muted']}; }}
QStatusBar {{ background: {palette['panel']}; color: {palette['muted']}; }}
"""


@dataclass
class QuantityWidgets:
    target: "TargetSpinBox"
    decrease: QToolButton
    increase: QToolButton
    step: "NoWheelComboBox"
    setpoint: QLabel
    readback: QLabel
    delta: QLabel
    status: QLabel


class TargetSpinBox(QDoubleSpinBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        self._initialized = False
        super().__init__(parent)

    def textFromValue(self, value: float) -> str:
        if not getattr(self, "_initialized", False):
            return "--"
        return super().textFromValue(value)

    def clear_target(self) -> None:
        self._initialized = False
        self.setEnabled(False)
        self.lineEdit().setText("--")

    def set_target(self, value: float) -> None:
        self._initialized = True
        self.setValue(value)
        self.lineEdit().setText(self.textFromValue(self.value()))

    def wheelEvent(self, event) -> None:
        event.ignore()


class NoWheelComboBox(QComboBox):
    def wheelEvent(self, event) -> None:
        event.ignore()


class LlrfControlWindow(QMainWindow):
    def __init__(self, runtime: LlrfRuntime) -> None:
        super().__init__()
        self.runtime = runtime
        self.groups = {group.element_id: group for group in runtime.groups}
        self.current_group: LlrfGroup | None = None
        self.values: dict[str, float] = {}
        self.connected: dict[str, bool] = {}
        self.queue = CoalescingWriteQueue()
        self.dirty_targets: set[str] = set()
        self.failed_quantities: set[str] = set()
        self.last_write_completed: dict[str, float] = {}
        self._worker: WriteWorker | None = None
        self._theme = resolve_initial_theme()
        self.group_buttons: dict[str, QPushButton] = {}
        self.quantity_widgets: dict[str, QuantityWidgets] = {}
        self.monitor = LlrfMonitor(self)
        self.monitor.value_changed.connect(self._on_value)
        self.monitor.connection_changed.connect(self._on_connection)
        self.status_timer = QTimer(self)
        self.status_timer.setInterval(250)
        self.status_timer.timeout.connect(self._refresh_all_quantities)
        self.status_timer.start()

        self.setWindowTitle(
            f"{runtime.context.machine.display_name} - LLRF Amplitude & Phase"
        )
        self.resize(1180, 560)
        self.setMinimumSize(940, 500)
        self._build_ui()
        self._apply_theme()
        self._select_group(runtime.default_element)

    def _build_ui(self) -> None:
        root = QWidget(self)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(12, 10, 12, 8)
        outer.setSpacing(9)

        heading = QHBoxLayout()
        title = QLabel("LLRF Amplitude & Phase", root)
        title.setObjectName("title")
        heading.addWidget(title)
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

        selector = QFrame(root)
        selector.setObjectName("panel")
        selector_layout = QGridLayout(selector)
        selector_layout.setContentsMargins(10, 9, 10, 9)
        selector_layout.setHorizontalSpacing(6)
        selector_layout.setVerticalSpacing(6)
        self.group_button_group = QButtonGroup(self)
        self.group_button_group.setExclusive(True)
        columns = 11
        for index, group in enumerate(self.runtime.groups):
            button = QPushButton(group.element_id, selector)
            button.setCheckable(True)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button.setToolTip(group.display_name)
            button.clicked.connect(
                lambda _checked=False, element_id=group.element_id: self._select_group(element_id)
            )
            selector_layout.addWidget(button, index // columns, index % columns)
            self.group_button_group.addButton(button)
            self.group_buttons[group.element_id] = button
        outer.addWidget(selector)

        controls = QFrame(root)
        controls.setObjectName("panel")
        grid = QGridLayout(controls)
        grid.setContentsMargins(12, 10, 12, 12)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(9)
        headers = ("", "Target", "", "", "Step", "", "Set", "Readback", "Delta", "Status")
        for column, text in enumerate(headers):
            label = QLabel(text, controls)
            label.setProperty("role", "field")
            grid.addWidget(label, 0, column)
        for row, name in enumerate(QUANTITIES, start=1):
            spec = self.runtime.groups[0].quantities[name]
            widgets = self._build_quantity_row(spec, controls)
            self.quantity_widgets[name] = widgets
            quantity_label = QLabel(spec.label, controls)
            quantity_label.setProperty("role", "quantity")
            unit_label = QLabel(spec.unit, controls)
            unit_label.setProperty("role", "field")
            row_widgets = (
                quantity_label, widgets.target, widgets.decrease, widgets.increase,
                widgets.step, unit_label, widgets.setpoint, widgets.readback,
                widgets.delta, widgets.status,
            )
            for column, widget in enumerate(row_widgets):
                grid.addWidget(widget, row, column)
        grid.setColumnStretch(9, 1)
        outer.addWidget(controls)
        outer.addStretch(1)

        self.setCentralWidget(root)
        self.setStatusBar(QStatusBar(self))
        self.statusBar().showMessage("Waiting for PV data")

    def _build_quantity_row(self, spec: QuantityConfig, parent: QWidget) -> QuantityWidgets:
        target = TargetSpinBox(parent)
        target.setRange(spec.low, spec.high)
        target.setDecimals(3)
        target.setKeyboardTracking(False)
        target.setButtonSymbols(QAbstractSpinBox.NoButtons)
        target.setMinimumWidth(120)
        target.clear_target()
        target.editingFinished.connect(
            lambda name=spec.name: self._commit_target(name)
        )
        target.valueChanged.connect(
            lambda _value, name=spec.name: self.dirty_targets.add(name)
        )

        decrease = self._adjust_button("-")
        increase = self._adjust_button("+")
        decrease.setToolTip(f"Decrease {spec.name} by the selected step")
        increase.setToolTip(f"Increase {spec.name} by the selected step")
        decrease.clicked.connect(lambda _checked=False, name=spec.name: self._shift(name, -1))
        increase.clicked.connect(lambda _checked=False, name=spec.name: self._shift(name, 1))

        step = NoWheelComboBox(parent)
        step.setEditable(True)
        step.setInsertPolicy(QComboBox.NoInsert)
        for value in spec.step_choices:
            step.addItem(f"{value:g}", value)
        selected = min(
            range(step.count()),
            key=lambda index: abs(float(step.itemData(index)) - spec.default_step),
        )
        step.setCurrentIndex(selected)
        step.lineEdit().setValidator(QDoubleValidator(0.000001, 1.0e9, 6, step))
        step.setMinimumWidth(92)
        step.setToolTip("Select a preset or enter a positive step")

        return QuantityWidgets(
            target=target,
            decrease=decrease,
            increase=increase,
            step=step,
            setpoint=self._value_label(),
            readback=self._value_label(),
            delta=self._value_label(),
            status=self._status_label(),
        )

    def _adjust_button(self, text: str) -> QToolButton:
        button = QToolButton(self)
        button.setText(text)
        button.setFixedWidth(42)
        button.setAutoRepeat(True)
        button.setAutoRepeatDelay(400)
        button.setAutoRepeatInterval(120)
        return button

    @staticmethod
    def _value_label() -> QLabel:
        label = QLabel("--")
        label.setProperty("role", "value")
        label.setMinimumWidth(92)
        return label

    @staticmethod
    def _status_label() -> QLabel:
        label = QLabel("Connecting")
        label.setProperty("tone", "warning")
        label.setMinimumWidth(118)
        return label

    def _toggle_theme(self) -> None:
        self._theme = "light" if self._theme == "dark" else "dark"
        self._apply_theme()

    def _apply_theme(self) -> None:
        palette = DARK if self._theme == "dark" else LIGHT
        self.setStyleSheet(_stylesheet(palette))
        self.theme_button.setText("L" if self._theme == "dark" else "D")
        self.theme_button.setToolTip(
            "Switch to light theme" if self._theme == "dark" else "Switch to dark theme"
        )

    def _select_group(self, element_id: str) -> None:
        if self.queue.busy:
            return
        self.current_group = self.groups[element_id]
        self.values.clear()
        self.connected.clear()
        self.queue.clear()
        self.dirty_targets.clear()
        self.failed_quantities.clear()
        self.last_write_completed.clear()
        for widgets in self.quantity_widgets.values():
            with QSignalBlocker(widgets.target):
                widgets.target.clear_target()
            for label in (widgets.setpoint, widgets.readback, widgets.delta):
                label.setText("--")
            self._set_status(widgets.status, "Connecting", "warning")
            self._set_controls_enabled(widgets, False)
        with QSignalBlocker(self.group_buttons[element_id]):
            self.group_buttons[element_id].setChecked(True)
        self.monitor.bind(self.current_group)
        self.statusBar().showMessage(f"Selected {element_id}; connecting PVs")

    def _on_connection(self, field: str, connected: bool) -> None:
        self.connected[field] = connected
        self._refresh_all_quantities()

    def _on_value(self, field: str, raw_value: object) -> None:
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            return
        if not math.isfinite(value):
            return
        for name in QUANTITIES:
            spec = self.current_group.quantities[name] if self.current_group else None
            if spec is not None and field in {spec.set_channel, spec.readback_channel}:
                widgets = self.quantity_widgets[name]
                if field == spec.set_channel:
                    previous = self.values.get(field)
                    expected = self.queue.expected_values(name)
                    requested = self.queue.requested.get(name)
                    acceptable = expected + (() if requested is None else (requested,))
                    if previous is not None:
                        acceptable += (previous,)
                    if acceptable and not any(
                        math.isclose(value, item, abs_tol=1e-12)
                        for item in acceptable
                    ):
                        self.queue.cancel_pending(name)
                        self.statusBar().showMessage(
                            f"External setpoint change detected for {self.current_group.element_id} {name}"
                        )
                    self.values[field] = value
                    self.queue.acknowledge(name, value)
                    if (
                        name not in self.queue.requested
                        and name not in self.dirty_targets
                        and not widgets.target.hasFocus()
                    ):
                        with QSignalBlocker(widgets.target):
                            widgets.target.set_target(value)
                else:
                    self.values[field] = value
                self._refresh_quantity(name)
                break

    def _refresh_all_quantities(self) -> None:
        for name in QUANTITIES:
            self._refresh_quantity(name)

    def _refresh_quantity(self, name: str) -> None:
        if self.current_group is None:
            return
        spec = self.current_group.quantities[name]
        widgets = self.quantity_widgets[name]
        setpoint = self.values.get(spec.set_channel)
        readback = self.values.get(spec.readback_channel)
        widgets.setpoint.setText(self._format(setpoint, spec.unit))
        widgets.readback.setText(self._format(readback, spec.unit))
        widgets.delta.setText(
            self._format(None if setpoint is None or readback is None else readback - setpoint, spec.unit, signed=True)
        )
        ready = (
            self.connected.get(spec.set_channel, False)
            and self.connected.get(spec.readback_channel, False)
            and setpoint is not None
        )
        self._set_controls_enabled(widgets, ready)
        inflight = self.queue.inflight
        if inflight is not None and inflight[0] == name:
            self._set_status(widgets.status, "Writing", "warning")
        elif name in self.queue.pending:
            self._set_status(widgets.status, "Queued", "warning")
        elif name in self.failed_quantities:
            self._set_status(widgets.status, "Write failed", "danger")
        elif ready and readback is not None:
            difference = abs(readback - setpoint)
            if difference <= spec.readback_tolerance:
                self._set_status(widgets.status, "Matched", "success")
            elif (
                name in self.last_write_completed
                and time.monotonic() - self.last_write_completed[name] < spec.settle_s
            ):
                self._set_status(widgets.status, "Following", "warning")
            else:
                self._set_status(widgets.status, "Mismatch", "danger")
        elif not self.connected.get(spec.set_channel, False) or not self.connected.get(spec.readback_channel, False):
            self._set_status(widgets.status, "Disconnected", "danger")
        else:
            self._set_status(widgets.status, "Waiting", "warning")

    @staticmethod
    def _set_controls_enabled(widgets: QuantityWidgets, enabled: bool) -> None:
        for widget in (widgets.target, widgets.decrease, widgets.increase, widgets.step):
            widget.setEnabled(enabled)

    def _shift(self, name: str, direction: int) -> None:
        if self.current_group is None:
            return
        spec = self.current_group.quantities[name]
        current = self.queue.requested.get(name, self.values.get(spec.set_channel))
        if current is None:
            return
        step = self._step_value(self.quantity_widgets[name].step)
        if step is None:
            return
        value = current + direction * step
        if name == "phase":
            value = self._wrap_phase(value, spec.low, spec.high)
        else:
            value = min(max(value, spec.low), spec.high)
        with QSignalBlocker(self.quantity_widgets[name].target):
            self.quantity_widgets[name].target.set_target(value)
        self.dirty_targets.discard(name)
        self._enqueue_write(name, value)

    def _commit_target(self, name: str) -> None:
        if self.current_group is None:
            return
        spec = self.current_group.quantities[name]
        current = self.queue.requested.get(name, self.values.get(spec.set_channel))
        if name not in self.dirty_targets:
            if current is not None:
                with QSignalBlocker(self.quantity_widgets[name].target):
                    self.quantity_widgets[name].target.set_target(current)
            return
        self.dirty_targets.discard(name)
        requested = self.quantity_widgets[name].target.value()
        if current is None or math.isclose(requested, current, abs_tol=1e-12):
            return
        self._enqueue_write(name, requested)

    def _enqueue_write(self, name: str, value: float) -> None:
        if self.current_group is None:
            return
        spec = self.current_group.quantities[name]
        value = min(max(float(value), spec.low), spec.high)
        self.failed_quantities.discard(name)
        self.queue.enqueue(name, value)
        self._pump_queue()
        self._refresh_all_quantities()

    def _pump_queue(self) -> None:
        if self.current_group is None or self._worker is not None:
            return
        request = self.queue.begin_next()
        if request is None:
            self._update_group_buttons()
            return
        name, value = request
        spec = self.current_group.quantities[name]
        worker = WriteWorker(name, spec.setpoint_pv, value, self)
        worker.completed.connect(self._write_completed)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        self._update_group_buttons()
        self.statusBar().showMessage(f"Writing {self.current_group.element_id} {name} = {value:g} {spec.unit}")
        worker.start()

    def _write_completed(self, name: str, value: float, success: bool, error: str) -> None:
        self.queue.finish()
        self._worker = None
        if success:
            self.last_write_completed[name] = time.monotonic()
            self.failed_quantities.discard(name)
            current = self.values.get(self.current_group.quantities[name].set_channel)
            if current is not None:
                self.queue.acknowledge(name, current)
            self.statusBar().showMessage(f"{name.title()} request {value:g} written")
        else:
            self.queue.fail(name, value)
            self.failed_quantities.add(name)
            QMessageBox.warning(self, "LLRF Write Failed", error or "EPICS write failed.")
        self._pump_queue()
        self._update_group_buttons()
        self._refresh_all_quantities()

    def _update_group_buttons(self) -> None:
        enabled = not self.queue.busy
        for button in self.group_buttons.values():
            button.setEnabled(enabled)

    @staticmethod
    def _step_value(combo: QComboBox) -> float | None:
        try:
            value = float(combo.currentText())
        except ValueError:
            return None
        return value if math.isfinite(value) and value > 0 else None

    @staticmethod
    def _wrap_phase(value: float, low: float, high: float) -> float:
        if low <= value <= high:
            return value
        width = high - low
        return ((value - low) % width) + low

    @staticmethod
    def _format(value: float | None, unit: str, *, signed: bool = False) -> str:
        if value is None:
            return "--"
        return f"{value:+.3f} {unit}" if signed else f"{value:.3f} {unit}"

    @staticmethod
    def _set_status(label: QLabel, text: str, tone: str) -> None:
        label.setText(text)
        label.setProperty("tone", tone)
        label.style().unpolish(label)
        label.style().polish(label)

    def closeEvent(self, event) -> None:
        self.monitor.close()
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(5500)
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    try:
        runtime = load_llrf_runtime()
    except Exception as exc:
        QMessageBox.critical(None, "LLRF Amplitude & Phase", str(exc))
        return 2
    window = LlrfControlWindow(runtime)
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
