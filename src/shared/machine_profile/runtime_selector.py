from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from PyQt5.QtCore import QEvent, QTimer, pyqtSignal
from PyQt5.QtGui import QPalette
from PyQt5.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from .loader import (
    CONTROL_BACKEND_ENV,
    LEGACY_CONTROL_BACKEND_ENV,
    LEGACY_MACHINE_ID_ENV,
    MACHINE_ID_ENV,
    list_control_backend_choices,
    list_machine_profile_ids,
    load_profile,
)


@dataclass(frozen=True)
class MachineChoice:
    machine_id: str
    display_name: str


def list_machine_choices() -> tuple[MachineChoice, ...]:
    choices: list[MachineChoice] = []
    for machine_id in list_machine_profile_ids():
        profile = load_profile(machine_id)
        choices.append(
            MachineChoice(
                machine_id=profile.machine.id,
                display_name=profile.machine.display_name,
            )
        )
    return tuple(choices)


def default_control_backend_choices(machine_id: str | None = None) -> tuple[str, ...]:
    return list_control_backend_choices(machine_id)


def relaunch_current_process(machine_id: str, control_backend: str) -> None:
    env = os.environ.copy()
    env[MACHINE_ID_ENV] = machine_id
    env[CONTROL_BACKEND_ENV] = control_backend
    env[LEGACY_MACHINE_ID_ENV] = machine_id
    env[LEGACY_CONTROL_BACKEND_ENV] = control_backend
    argv = [sys.executable, *sys.argv]
    os.execvpe(sys.executable, argv, env)


def request_runtime_restart(
    parent: QWidget,
    *,
    app_label: str,
    current_machine_id: str,
    current_control_backend: str,
    machine_id: str,
    control_backend: str,
) -> bool:
    if machine_id == current_machine_id and control_backend == current_control_backend:
        return False

    message = (
        f"Reload {app_label} with machine '{machine_id}' and backend '{control_backend}'?\n\n"
        "Current unsaved GUI state will be lost."
    )
    prompt = QMessageBox(parent)
    prompt.setIcon(QMessageBox.Question)
    prompt.setWindowTitle("Switch Runtime")
    prompt.setText(message)
    prompt.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    prompt.setDefaultButton(QMessageBox.No)
    _apply_runtime_prompt_style(prompt, parent)
    answer = prompt.exec_()
    if answer != QMessageBox.Yes:
        return False

    QTimer.singleShot(0, lambda: relaunch_current_process(machine_id, control_backend))
    return True


def _apply_runtime_prompt_style(prompt: QMessageBox, parent: QWidget) -> None:
    dark_parent = _is_dark_widget(parent)
    if dark_parent:
        prompt.setStyleSheet(
            """
QMessageBox {
    background-color: #172027;
    color: #e6edf2;
}
QMessageBox QLabel {
    color: #e6edf2;
    background: transparent;
    font-size: 12px;
    font-weight: 600;
}
QMessageBox QPushButton {
    background-color: #11191f;
    border: 1px solid #2b3d48;
    border-radius: 8px;
    color: #edf3f7;
    min-width: 72px;
    min-height: 28px;
    padding: 4px 12px;
    font-weight: 700;
}
QMessageBox QPushButton:hover {
    background-color: #18242c;
}
"""
        )
        return

    prompt.setStyleSheet(
        """
QMessageBox {
    background-color: #fffdf9;
    color: #2c3942;
}
QMessageBox QLabel {
    color: #2c3942;
    background: transparent;
    font-size: 12px;
    font-weight: 600;
}
QMessageBox QPushButton {
    background-color: #f8f3eb;
    border: 1px solid #d9d0c3;
    border-radius: 8px;
    color: #2c3942;
    min-width: 72px;
    min-height: 28px;
    padding: 4px 12px;
    font-weight: 700;
}
QMessageBox QPushButton:hover {
    background-color: #efe6d9;
}
"""
    )


def _is_dark_widget(widget: QWidget) -> bool:
    if getattr(widget, "current_theme", None) == "dark":
        return True
    if getattr(widget, "current_theme", None) == "light":
        return False
    return widget.palette().color(QPalette.Window).lightness() < 128


class RuntimeSelectorWidget(QWidget):
    apply_requested = pyqtSignal(str, str)

    def __init__(
        self,
        *,
        current_machine_id: str,
        current_control_backend: str,
        machine_choices: tuple[MachineChoice, ...] | None = None,
        control_backend_choices: tuple[str, ...] | None = None,
        control_height: int | None = None,
        machine_width: int | None = None,
        backend_width: int | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.machine_choices = machine_choices or list_machine_choices()
        self._fixed_control_backend_choices = control_backend_choices
        self._control_height = control_height
        self._machine_width = machine_width
        self._backend_width = backend_width

        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        machine_label = QLabel("Machine", self)
        machine_label.setProperty("role", "field")
        layout.addWidget(machine_label)

        self.machine_combo = QComboBox(self)
        self.machine_combo.setMinimumWidth(self._machine_width or 150)
        self._apply_control_height(self.machine_combo)
        for choice in self.machine_choices:
            self.machine_combo.addItem(choice.display_name, choice.machine_id)
        layout.addWidget(self.machine_combo)

        backend_label = QLabel("Backend", self)
        backend_label.setProperty("role", "field")
        layout.addWidget(backend_label)

        self.backend_combo = QComboBox(self)
        self.backend_combo.setMinimumWidth(self._backend_width or 140)
        self._apply_control_height(self.backend_combo)
        layout.addWidget(self.backend_combo)

        self.apply_button = QPushButton("Apply", self)
        self.apply_button.setProperty("compact", True)
        self.apply_button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.apply_button.setMinimumWidth(82)
        self._apply_control_height(self.apply_button)
        self.apply_button.clicked.connect(self._emit_apply_requested)
        layout.addWidget(self.apply_button)

        self.machine_combo.currentIndexChanged.connect(self._sync_control_backend_choices)
        self._set_current_machine(current_machine_id)
        self._sync_control_backend_choices(current_control_backend)

    def current_machine_id(self) -> str:
        return str(self.machine_combo.currentData())

    def current_control_backend(self) -> str:
        return str(self.backend_combo.currentData())

    def _set_current_machine(self, machine_id: str) -> None:
        for index in range(self.machine_combo.count()):
            if self.machine_combo.itemData(index) == machine_id:
                self.machine_combo.setCurrentIndex(index)
                return

    def _set_current_control_backend(self, control_backend: str) -> None:
        for index in range(self.backend_combo.count()):
            if self.backend_combo.itemData(index) == control_backend:
                self.backend_combo.setCurrentIndex(index)
                return

    def _sync_control_backend_choices(self, preferred_backend: str | int | None = None) -> None:
        current_machine_id = self.current_machine_id()
        current_backend = self.current_control_backend()
        if isinstance(preferred_backend, str):
            target_backend = preferred_backend
        else:
            target_backend = current_backend

        if self._fixed_control_backend_choices is None:
            backend_choices = default_control_backend_choices(current_machine_id)
        else:
            backend_choices = self._fixed_control_backend_choices

        self.backend_combo.blockSignals(True)
        self.backend_combo.clear()
        for backend in backend_choices:
            self.backend_combo.addItem(_display_control_backend(backend), backend)
        self.backend_combo.blockSignals(False)

        if backend_choices:
            selected_backend = target_backend if target_backend in backend_choices else backend_choices[0]
            self._set_current_control_backend(selected_backend)

    def _emit_apply_requested(self) -> None:
        self.apply_requested.emit(self.current_machine_id(), self.current_control_backend())

    def _apply_control_height(self, widget: QWidget) -> None:
        if self._control_height is None:
            return
        widget.setFixedHeight(self._control_height)


class RuntimeContextWidget(QWidget):
    """Compact, read-only display of the active machine and control backend."""

    def __init__(
        self,
        *,
        machine_id: str,
        machine_display_name: str,
        control_backend: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("runtimeContext")
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.machine_label = QLabel(f"Machine: {machine_display_name}", self)
        self.machine_label.setProperty("role", "field")
        self.machine_label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.machine_label.setToolTip(f"Machine profile: {machine_id}")
        layout.addWidget(self.machine_label)

        self._normalized_backend = str(control_backend).strip().lower()
        backend_display = _display_control_backend(self._normalized_backend)
        self.backend_label = QLabel(f"Backend: {backend_display}", self)
        self.backend_label.setObjectName("runtimeBackendLabel")
        self.backend_label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.backend_label.setToolTip(
            "Real Machine: commands may access live PVs."
            if self._normalized_backend == "real"
            else "Virtual Machine backend"
        )
        layout.addWidget(self.backend_label)
        self._apply_backend_style()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.PaletteChange, QEvent.StyleChange):
            QTimer.singleShot(0, self._apply_backend_style)

    def _apply_backend_style(self) -> None:
        dark = _is_dark_widget(self)
        if self._normalized_backend == "real":
            foreground = "#58c7b7" if dark else "#26796f"
            border = "#3b9185" if dark else "#4f978d"
        else:
            foreground = "#d1a052" if dark else "#966519"
            border = "#a67a35" if dark else "#b4863c"
        self.backend_label.setStyleSheet(
            "QLabel#runtimeBackendLabel {"
            f" color: {foreground}; background: transparent; border: 1px solid {border};"
            " border-radius: 6px; padding: 3px 8px; font-weight: 700; }"
        )


def _display_control_backend(control_backend: str) -> str:
    if control_backend == "vm":
        return "Virtual Machine"
    if control_backend == "real":
        return "Real Machine"
    return control_backend
