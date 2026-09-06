from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = next(
    parent for parent in Path(__file__).resolve().parents
    if (parent / "repo_bootstrap.py").is_file()
)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

from PyQt5.QtCore import QSignalBlocker, Qt
from PyQt5.QtGui import QDoubleValidator
from PyQt5.QtWidgets import (
    QApplication,
    QAbstractSpinBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from half_linac.src.apps.symmetric_quad_adjust.epics_client import (
    K1Monitor,
    K1WriteWorker,
)
from half_linac.src.apps.symmetric_quad_adjust.model import (
    CoalescingK1Queue,
    shifted_pair_targets,
    single_target,
)
from half_linac.src.apps.symmetric_quad_adjust.profile_runtime import (
    PairRuntime,
    load_symmetric_quad_runtime,
)
from half_linac.src.shared.app_theme import resolve_initial_theme
from half_linac.src.shared.machine_profile import RuntimeContextWidget
from half_linac.src.shared.window_activation import install_qt_window_raise_handler


PALETTES = {
    "dark": {
        "window": "#0f1519", "panel": "#172027", "input": "#10171c",
        "border": "#2a3943", "text": "#e6edf2", "muted": "#91a2ad",
        "accent": "#45d0bc", "warning": "#e4b86f", "danger": "#e37878",
    },
    "light": {
        "window": "#f2ede5", "panel": "#fffdf9", "input": "#fffdf8",
        "border": "#d7cec1", "text": "#2c3942", "muted": "#746c62",
        "accent": "#2d7f6d", "warning": "#a97118", "danger": "#b44141",
    },
}


def build_stylesheet(theme: str) -> str:
    p = PALETTES[theme]
    return f"""
    QMainWindow, QWidget {{ background: {p['window']}; color: {p['text']}; font-family: "IBM Plex Sans", "Source Han Sans SC", "Segoe UI", sans-serif; font-size: 12px; }}
    QFrame#pairCard, QFrame#toolbar {{ background: {p['panel']}; border: 1px solid {p['border']}; border-radius: 9px; }}
    QLabel {{ background: transparent; border: none; }}
    QLabel#title {{ font-size: 21px; font-weight: 700; }}
    QLabel#subtitle, QLabel#pvLabel {{ color: {p['muted']}; }}
    QLabel#valueLabel {{ font-family: "IBM Plex Mono", monospace; font-size: 15px; font-weight: 700; }}
    QLabel#differenceLabel[matched="false"] {{ color: {p['warning']}; font-weight: 700; }}
    QLabel#status {{ background: {p['input']}; border: 1px solid {p['border']}; border-radius: 6px; padding: 7px 9px; }}
    QDoubleSpinBox, QComboBox {{ background: {p['input']}; color: {p['text']}; border: 1px solid {p['border']}; border-radius: 5px; min-height: 25px; padding: 3px 6px; }}
    QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {p['accent']}; }}
    QPushButton, QToolButton {{ background: {p['panel']}; color: {p['text']}; border: 1px solid {p['border']}; border-radius: 6px; min-height: 27px; padding: 4px 11px; font-weight: 650; }}
    QPushButton:hover, QToolButton:hover {{ border-color: {p['accent']}; }}
    QPushButton#pairButton {{ color: {p['accent']}; }}
    QPushButton:disabled {{ color: {p['muted']}; }}
    QToolButton#themeButton {{ min-width: 32px; max-width: 32px; min-height: 32px; max-height: 32px; font-size: 16px; }}
    """


class RequestSpinBox(QDoubleSpinBox):
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
        self.lineEdit().setText(self.textFromValue(self.value()))


class SymmetricQuadWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.runtime = load_symmetric_quad_runtime()
        self.theme = resolve_initial_theme()
        self.setpoint_values: dict[str, float] = {}
        self.readback_values: dict[str, float] = {}
        self.target_values: dict[str, float] = {}
        self.awaiting_setpoint: dict[str, float] = {}
        self.connected = {
            (target.element_id, field): False
            for target in self.runtime.targets
            for field in ("setpoint", "readback")
        }
        self.setpoint_labels: dict[str, QLabel] = {}
        self.readback_labels: dict[str, QLabel] = {}
        self.editors: dict[str, RequestSpinBox] = {}
        self.individual_buttons: dict[str, list[QPushButton]] = {}
        self.pair_buttons: list[QPushButton] = []
        self.pair_button_pairs: list[PairRuntime] = []
        self.difference_labels: dict[tuple[str, str], QLabel] = {}
        self.queue = CoalescingK1Queue()
        self._worker: K1WriteWorker | None = None
        self._build_ui()
        self._apply_theme()
        self.monitor = K1Monitor(self)
        self.monitor.value_changed.connect(self._value_changed)
        self.monitor.connection_changed.connect(self._connection_changed)
        self.monitor.bind(self.runtime.targets)
        self._refresh_controls()

    def _build_ui(self) -> None:
        self.setWindowTitle("Symmetric Quadrupole Adjust")
        self.resize(1040, 650)
        root = QWidget(self)
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("Symmetric Quadrupole Adjust")
        title.setObjectName("title")
        subtitle = QLabel("Apply the same ΔK1 to each configured mirror pair, or set one quadrupole directly.")
        subtitle.setObjectName("subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box, 1)
        context = self.runtime.context
        header.addWidget(
            RuntimeContextWidget(
                machine_id=context.machine.id,
                machine_display_name=context.machine.display_name,
                control_backend=context.control_backend.name,
            )
        )
        self.theme_button = QToolButton()
        self.theme_button.setObjectName("themeButton")
        self.theme_button.clicked.connect(self._toggle_theme)
        header.addWidget(self.theme_button)
        layout.addLayout(header)

        toolbar = QFrame()
        toolbar.setObjectName("toolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(10, 7, 10, 7)
        toolbar_layout.addWidget(QLabel("Pair step ΔK1"))
        self.step_combo = QComboBox()
        self.step_combo.setEditable(True)
        self.step_combo.setInsertPolicy(QComboBox.NoInsert)
        for step in self.runtime.step_choices:
            self.step_combo.addItem(f"{step:g}", step)
        default_index = min(
            range(self.step_combo.count()),
            key=lambda index: abs(
                float(self.step_combo.itemData(index)) - self.runtime.default_step
            ),
        )
        self.step_combo.setCurrentIndex(default_index)
        self.step_combo.lineEdit().setValidator(
            QDoubleValidator(
                self.runtime.custom_step_minimum,
                self.runtime.custom_step_maximum,
                12,
                self.step_combo,
            )
        )
        self.step_combo.lineEdit().editingFinished.connect(self._normalize_step)
        self.step_combo.currentTextChanged.connect(
            lambda _text: self._sync_editor_steps()
        )
        self.step_combo.setToolTip(
            "Select a preset or enter a positive custom K1 step."
        )
        self.step_combo.setMinimumWidth(100)
        toolbar_layout.addWidget(self.step_combo)
        toolbar_layout.addWidget(QLabel("1/m²"))
        toolbar_layout.addStretch(1)
        note = QLabel("Enter Target or use ◀/▶ · writes K1 immediately")
        note.setObjectName("subtitle")
        toolbar_layout.addWidget(note)
        layout.addWidget(toolbar)

        for index, pair in enumerate(self.runtime.pairs, 1):
            layout.addWidget(self._pair_card(index, pair))

        layout.addStretch(1)
        self.status = QLabel("Connecting to configured K1 channels…")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    def _pair_card(self, index: int, pair: PairRuntime) -> QFrame:
        card = QFrame()
        card.setObjectName("pairCard")
        grid = QGridLayout(card)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        pair_title = QLabel(f"Mirror pair {index}  ·  {pair.left.element_id} ↔ {pair.right.element_id}")
        pair_title.setStyleSheet("font-weight: 700; font-size: 14px;")
        grid.addWidget(pair_title, 0, 0, 1, 4)
        difference = QLabel("Δ = —")
        difference.setObjectName("differenceLabel")
        difference.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        grid.addWidget(difference, 0, 4, 1, 2)
        self.difference_labels[pair.pair.elements] = difference

        for column, text in ((1, "Target"), (4, "Set (K1)"), (5, "Readback (K1 total)")):
            header = QLabel(text)
            header.setObjectName("subtitle")
            header.setAlignment(Qt.AlignCenter)
            grid.addWidget(header, 1, column)

        for row, target in enumerate((pair.left, pair.right), 2):
            name = QLabel(target.display_name)
            name.setMinimumWidth(70)
            grid.addWidget(name, row, 0)
            editor = RequestSpinBox()
            editor.setDecimals(self.runtime.display_decimals)
            editor.setRange(-1_000_000.0, 1_000_000.0)
            editor.setSingleStep(self.runtime.default_step)
            editor.setSuffix("  1/m²")
            editor.setMinimumWidth(190)
            editor.setToolTip(f"K1 write target\n{target.pv_name}")
            editor.setKeyboardTracking(False)
            editor.setButtonSymbols(QAbstractSpinBox.NoButtons)
            editor.clear_request()
            editor.editingFinished.connect(
                lambda element=target.element_id: self._set_one(element)
            )
            grid.addWidget(editor, row, 1)
            minus_one = QPushButton("◀")
            plus_one = QPushButton("▶")
            for column, button, direction in (
                (2, minus_one, -1.0),
                (3, plus_one, 1.0),
            ):
                button.setAutoRepeat(True)
                button.setAutoRepeatDelay(self.runtime.button_repeat_delay_ms)
                button.setAutoRepeatInterval(self.runtime.button_repeat_interval_ms)
                button.setToolTip(
                    f"Adjust {target.element_id} only by the selected ΔK1 step."
                )
                button.clicked.connect(
                    lambda _checked=False, element=target.element_id, sign=direction: self._shift_one(
                        element, sign
                    )
                )
                grid.addWidget(button, row, column)
            setpoint = QLabel("—")
            setpoint.setObjectName("valueLabel")
            setpoint.setMinimumWidth(130)
            setpoint.setAlignment(Qt.AlignCenter)
            setpoint.setToolTip(f"K1 setpoint\n{target.pv_name}")
            grid.addWidget(setpoint, row, 4)
            readback = QLabel("—")
            readback.setObjectName("valueLabel")
            readback.setMinimumWidth(150)
            readback.setAlignment(Qt.AlignCenter)
            readback.setToolTip(f"K1 total readback\n{target.readback_pv}")
            grid.addWidget(readback, row, 5)
            self.setpoint_labels[target.element_id] = setpoint
            self.readback_labels[target.element_id] = readback
            self.editors[target.element_id] = editor
            self.individual_buttons[target.element_id] = [minus_one, plus_one]

        together_label = QLabel("Together")
        together_label.setObjectName("subtitle")
        together_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        grid.addWidget(together_label, 4, 1)
        minus = QPushButton("◀")
        plus = QPushButton("▶")
        for column, button, direction in (
            (2, minus, -1.0),
            (3, plus, 1.0),
        ):
            button.setObjectName("pairButton")
            button.setAutoRepeat(True)
            button.setAutoRepeatDelay(self.runtime.button_repeat_delay_ms)
            button.setAutoRepeatInterval(self.runtime.button_repeat_interval_ms)
            button.clicked.connect(
                lambda _checked=False, p=pair, sign=direction: self._shift_pair(p, sign)
            )
            grid.addWidget(button, 4, column)
            self.pair_buttons.append(button)
            self.pair_button_pairs.append(pair)
        return card

    def _apply_theme(self) -> None:
        self.setStyleSheet(build_stylesheet(self.theme))
        self.theme_button.setText("☼" if self.theme == "dark" else "☾")
        self.theme_button.setToolTip(
            "Switch to light theme." if self.theme == "dark" else "Switch to dark theme."
        )
        self._refresh_differences()

    def _toggle_theme(self) -> None:
        self.theme = "light" if self.theme == "dark" else "dark"
        self._apply_theme()

    def _value_changed(self, element_id: str, field: str, raw_value: object) -> None:
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            return
        if not math.isfinite(value):
            return
        if field == "setpoint":
            self.setpoint_values[element_id] = value
            self._show_setpoint(element_id, value)
            requested = self.awaiting_setpoint.get(element_id)
            if requested is not None and abs(value - requested) <= self.runtime.readback_tolerance:
                self.awaiting_setpoint.pop(element_id, None)
            if requested is None or abs(value - requested) <= self.runtime.readback_tolerance:
                self.target_values[element_id] = value
                self._show_target(element_id, value)
        elif field == "readback":
            self.readback_values[element_id] = value
            self._show_readback(element_id, value)
        self._refresh_controls()

    def _connection_changed(self, element_id: str, field: str, connected: bool) -> None:
        self.connected[(element_id, field)] = connected
        if not connected:
            labels = (
                self.setpoint_labels
                if field == "setpoint"
                else self.readback_labels
            )
            labels[element_id].setText("Disconnected")
            values = (
                self.setpoint_values
                if field == "setpoint"
                else self.readback_values
            )
            values.pop(element_id, None)
            if field == "setpoint":
                self.target_values.pop(element_id, None)
                self.awaiting_setpoint.pop(element_id, None)
                self.editors[element_id].clear_request()
        self._refresh_controls()

    def _set_one(self, element_id: str) -> None:
        editor = self.editors[element_id]
        if not editor._request_initialized or not editor.isEnabled():
            return
        value = editor.value()
        self._write(single_target(element_id, value))

    def _shift_pair(self, pair: PairRuntime, direction: float) -> None:
        try:
            targets = shifted_pair_targets(
                pair.pair,
                self.queue.desired_values(self.target_values),
                direction * self._current_step(),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Symmetric Quadrupole Adjust", str(exc))
            return
        self._write(targets)

    def _shift_one(self, element_id: str, direction: float) -> None:
        try:
            desired = self.queue.desired_values(self.target_values)
            if element_id not in desired:
                raise ValueError(f"K1 is not available for: {element_id}")
            target = desired[element_id] + direction * self._current_step()
        except ValueError as exc:
            QMessageBox.warning(self, "Symmetric Quadrupole Adjust", str(exc))
            return
        self._write(single_target(element_id, target))

    def _current_step(self) -> float:
        text = self.step_combo.currentText().strip()
        try:
            step = float(text)
        except ValueError as exc:
            raise ValueError("Enter a valid positive K1 step.") from exc
        if not self.runtime.custom_step_minimum <= step <= self.runtime.custom_step_maximum:
            raise ValueError(
                "K1 step must be between "
                f"{self.runtime.custom_step_minimum:g} and "
                f"{self.runtime.custom_step_maximum:g} 1/m²."
            )
        return step

    def _normalize_step(self) -> None:
        try:
            step = self._current_step()
        except ValueError:
            self.step_combo.setCurrentText(f"{self.runtime.default_step:g}")
            return
        self.step_combo.setCurrentText(f"{step:g}")
        self._sync_editor_steps()

    def _sync_editor_steps(self) -> None:
        try:
            step = self._current_step()
        except ValueError:
            return
        for editor in self.editors.values():
            editor.setSingleStep(step)

    def _write(self, values: dict[str, float]) -> None:
        disconnected = [
            name
            for name in values
            if not self.connected.get((name, "setpoint"), False)
        ]
        if disconnected:
            QMessageBox.warning(
                self,
                "Symmetric Quadrupole Adjust",
                "K1 channel is disconnected: " + ", ".join(disconnected),
            )
            return
        self.queue.enqueue(values)
        detail = ", ".join(f"{name}={value:.8g}" for name, value in values.items())
        if self._worker is not None:
            self.status.setText(f"Queued latest K1 target: {detail} 1/m²")
            return
        self._start_next_write()

    def _start_next_write(self) -> None:
        values = self.queue.begin_next()
        if not values:
            return
        targets_by_id = {target.element_id: target for target in self.runtime.targets}
        requests = {
            name: (targets_by_id[name].pv_name, float(value))
            for name, value in values.items()
        }
        detail = ", ".join(f"{name}={value:.8g}" for name, value in values.items())
        self.status.setText(f"Writing {detail} 1/m²…")
        self._worker = K1WriteWorker(requests, self)
        self._worker.completed.connect(self._write_completed)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()
        self._refresh_controls()

    def _write_completed(self, request: object, results: object, error: str) -> None:
        requested = dict(zip(request["elements"], request["values"]))
        result_map = dict(results)
        completed = self.queue.finish()
        self._worker = None
        failed = [name for name, success in result_map.items() if not success]
        if failed:
            self.queue.pending.clear()
            detail = error or ", ".join(failed)
            message = f"K1 write failed: {detail}"
            self.status.setText(message)
            QMessageBox.warning(self, "K1 Write Failed", message)
        else:
            for name, value in completed.items():
                self.target_values[name] = value
                self.awaiting_setpoint[name] = value
                self._show_target(name, value)
            detail = ", ".join(
                f"{name}={value:.8g}" for name, value in requested.items()
            )
            self.status.setText(
                f"K1 write completed: {detail} 1/m² · waiting for Set/Readback updates"
            )
        if self.queue.pending:
            self._start_next_write()
        self._refresh_controls()

    def _refresh_controls(self) -> None:
        for name, editor in self.editors.items():
            setpoint_connected = self.connected.get((name, "setpoint"), False)
            editor.setEnabled(setpoint_connected and name in self.target_values)
            for adjust_button in self.individual_buttons[name]:
                adjust_button.setEnabled(
                    setpoint_connected
                    and name in self.setpoint_values
                    and name in self.target_values
                )
        for button, pair in zip(self.pair_buttons, self.pair_button_pairs):
            button.setEnabled(
                all(
                    self.connected.get((name, "setpoint"), False)
                    for name in pair.pair.elements
                )
                and all(name in self.setpoint_values for name in pair.pair.elements)
                and all(name in self.target_values for name in pair.pair.elements)
            )
        busy = self._worker is not None
        if not busy:
            connected_count = sum(self.connected.values())
            if connected_count == len(self.connected):
                if not self.status.text().startswith("K1 write"):
                    self.status.setText(
                        f"Ready · {connected_count}/{len(self.connected)} K1 channels connected"
                    )
            elif not self.status.text().startswith("K1 write"):
                self.status.setText(
                    f"Connecting · {connected_count}/{len(self.connected)} K1 channels connected"
                )
        self._refresh_differences()

    def _show_setpoint(self, element_id: str, value: float) -> None:
        self.setpoint_labels[element_id].setText(
            f"{value:.{self.runtime.display_decimals}f}  1/m²"
        )

    def _show_readback(self, element_id: str, value: float) -> None:
        self.readback_labels[element_id].setText(
            f"{value:.{self.runtime.display_decimals}f}  1/m²"
        )

    def _show_target(self, element_id: str, value: float) -> None:
        editor = self.editors[element_id]
        if not editor.hasFocus():
            with QSignalBlocker(editor):
                editor.set_request_value(value)

    def _refresh_differences(self) -> None:
        if not hasattr(self, "difference_labels"):
            return
        for elements, label in self.difference_labels.items():
            if not all(name in self.readback_values for name in elements):
                label.setText("Δ = —")
                matched = True
            else:
                delta = (
                    self.readback_values[elements[0]]
                    - self.readback_values[elements[1]]
                )
                label.setText(f"Δ = {delta:+.{self.runtime.display_decimals}f} 1/m²")
                matched = abs(delta) <= self.runtime.readback_tolerance
            label.setProperty("matched", matched)
            label.style().unpolish(label)
            label.style().polish(label)

    def closeEvent(self, event) -> None:
        self.monitor.close()
        self.queue.pending.clear()
        if self._worker is not None:
            self._worker.wait(5500)
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Symmetric Quadrupole Adjust")
    try:
        window = SymmetricQuadWindow()
    except Exception as exc:
        QMessageBox.critical(None, "Symmetric Quadrupole Adjust", str(exc))
        return 1
    install_qt_window_raise_handler(window)
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
