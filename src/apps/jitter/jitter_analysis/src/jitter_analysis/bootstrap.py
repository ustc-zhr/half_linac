"""Bootstrap helpers for the desktop application."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from .gui.main_window import MainWindow, require_qt
from .gui.theme import apply_app_theme
from .gui.state import AppState
from .services.run_service import RunService
from .services.task_service import TaskService


def create_application(argv: Sequence[str] | None = None):
    qtwidgets = require_qt()
    app = qtwidgets.QApplication(list(argv or []))
    app.setApplicationName("Jitter Analysis")
    app.setOrganizationName("IRFEL")
    apply_app_theme(app)
    return app


def create_main_window(config_path: str | None = None) -> MainWindow:
    state = AppState(config_path=str(Path(config_path).resolve()) if config_path else None)
    run_service = RunService()
    task_service = TaskService()
    return MainWindow(state=state, run_service=run_service, task_service=task_service)
