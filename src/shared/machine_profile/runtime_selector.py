from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from PyQt5.QtCore import QTimer, pyqtSignal
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
    env["HALF_MACHINE_ID"] = machine_id
    env["HALF_CONTROL_BACKEND"] = control_backend
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
    answer = QMessageBox.question(
        parent,
        "Switch Runtime",
        message,
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if answer != QMessageBox.Yes:
        return False

    QTimer.singleShot(0, lambda: relaunch_current_process(machine_id, control_backend))
    return True


class RuntimeSelectorWidget(QWidget):
    apply_requested = pyqtSignal(str, str)

    def __init__(
        self,
        *,
        current_machine_id: str,
        current_control_backend: str,
        machine_choices: tuple[MachineChoice, ...] | None = None,
        control_backend_choices: tuple[str, ...] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.machine_choices = machine_choices or list_machine_choices()
        self._fixed_control_backend_choices = control_backend_choices

        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        machine_label = QLabel("Machine", self)
        machine_label.setProperty("role", "field")
        layout.addWidget(machine_label)

        self.machine_combo = QComboBox(self)
        self.machine_combo.setMinimumWidth(150)
        for choice in self.machine_choices:
            self.machine_combo.addItem(choice.display_name, choice.machine_id)
        layout.addWidget(self.machine_combo)

        backend_label = QLabel("Backend", self)
        backend_label.setProperty("role", "field")
        layout.addWidget(backend_label)

        self.backend_combo = QComboBox(self)
        self.backend_combo.setMinimumWidth(140)
        layout.addWidget(self.backend_combo)

        self.apply_button = QPushButton("Apply", self)
        self.apply_button.setProperty("compact", True)
        self.apply_button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
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


def _display_control_backend(control_backend: str) -> str:
    if control_backend == "vm":
        return "Virtual Machine"
    if control_backend == "real":
        return "Real Machine"
    return control_backend
