#!/usr/bin/env python3
"""Render offline GUI previews without loading a PV library or contacting EPICS."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from PyQt5 import QtWidgets

from jitter_analysis.gui.main_window import MainWindow
from jitter_analysis.gui.state import AppState
from jitter_analysis.gui.theme import apply_app_theme
from jitter_analysis.services.run_service import RunService
from jitter_analysis.services.task_service import TaskService


THEMES = ("control_room", "night_shift")
PAGES = (
    ("config", "config_tab_index"),
    ("run", "run_tab_index"),
    ("analysis", "analysis_page_index"),
)
SIZES = ((1280, 720), (1920, 1080))


def render_previews(output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    MainWindow._try_load_default_config = lambda self: None
    rendered: list[Path] = []

    for theme_id in THEMES:
        apply_app_theme(app, theme_id)
        window = MainWindow(AppState(), RunService(), TaskService())
        window.show()
        app.processEvents()

        for width, height in SIZES:
            window.resize(width, height)
            app.processEvents()
            for page_name, index_attribute in PAGES:
                window.main_tabs.setCurrentIndex(getattr(window, index_attribute))
                app.processEvents()
                target = output_dir / (
                    f"{page_name}_{theme_id}_{width}x{height}.png"
                )
                if not window.grab().save(str(target)):
                    raise RuntimeError(f"Could not write preview: {target}")
                rendered.append(target)

        window.close()
        window.deleteLater()
        app.processEvents()

    return rendered


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render light and dark Jitter Analysis GUI previews offline."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("/tmp/jitter_analysis_ui_previews"),
        help="Directory for PNG previews.",
    )
    args = parser.parse_args()
    for path in render_previews(args.output_dir):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
