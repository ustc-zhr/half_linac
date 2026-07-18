#!/usr/bin/env python3
"""Construct operator GUIs offscreen and verify their runtime-context layout."""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_PARENT = REPO_ROOT.parent
APP_ROOT = REPO_ROOT / "src" / "apps"


@dataclass(frozen=True)
class GuiSmokeSpec:
    entrypoint: Path
    window_class: str
    status_keys: frozenset[str]
    uses_selector: bool = False


GUI_SMOKE_SPECS = {
    "launcher": GuiSmokeSpec(
        APP_ROOT / "launcher" / "main.py",
        "myWindow",
        frozenset({"real_access", "running"}),
        uses_selector=True,
    ),
    "beam_monitor": GuiSmokeSpec(
        APP_ROOT / "beam_monitor" / "main.py",
        "myWindow",
        frozenset({"flag", "acq", "profile"}),
    ),
    "energy_spectrum": GuiSmokeSpec(
        APP_ROOT / "energy_spectrum" / "main.py",
        "EnergySpectrumApp",
        frozenset({"station", "connection", "fit", "model", "tune", "readout"}),
    ),
    "orbit_display": GuiSmokeSpec(
        APP_ROOT / "orbit_display" / "main.py",
        "myWindow",
        frozenset({"x", "y", "hold", "refresh", "view"}),
    ),
    "orbit_correct": GuiSmokeSpec(
        APP_ROOT / "orbit_correct" / "mainOrbCor.py",
        "myWindow",
        frozenset({"tab", "method", "targets", "process"}),
    ),
    "bba": GuiSmokeSpec(
        APP_ROOT / "bba" / "main.py",
        "myWindow",
        frozenset({"tab", "plane", "scan", "model"}),
    ),
    "emit_measure": GuiSmokeSpec(
        APP_ROOT / "emit_measure" / "main.py",
        "myWindow",
        frozenset({"model", "scan", "twiss", "fit", "emit", "data"}),
    ),
}


def _require_offscreen_plugin() -> None:
    try:
        from PyQt5.QtCore import QLibraryInfo
    except ImportError as exc:
        raise SystemExit(
            f"PyQt5 is unavailable in {sys.executable}. Activate the environment created from environment.yml."
        ) from exc

    plugin_root = Path(QLibraryInfo.location(QLibraryInfo.PluginsPath))
    candidates = (
        plugin_root / "platforms" / "libqoffscreen.so",
        plugin_root / "libqoffscreen.so",
    )
    if not any(path.is_file() for path in candidates):
        raise SystemExit(
            "The active Python does not provide a Qt offscreen platform plugin.\n"
            f"Python: {sys.executable}\n"
            f"Qt plugin root: {plugin_root}\n"
            "Activate the Conda environment created from environment.yml before running this smoke."
        )


def _load_entrypoint(app_name: str, spec: GuiSmokeSpec):
    app_dir = str(spec.entrypoint.parent)
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)
    if str(REPO_PARENT) not in sys.path:
        sys.path.insert(0, str(REPO_PARENT))

    module_spec = importlib.util.spec_from_file_location(
        f"half_linac_gui_smoke_{app_name}",
        spec.entrypoint,
    )
    if module_spec is None or module_spec.loader is None:
        raise RuntimeError(f"Could not load {spec.entrypoint}.")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


def _run_child(app_name: str) -> None:
    _require_offscreen_plugin()

    from PyQt5.QtWidgets import QApplication

    from half_linac.src.shared.machine_profile import (
        RuntimeContextWidget,
        RuntimeSelectorWidget,
    )

    spec = GUI_SMOKE_SPECS[app_name]
    qt_app = QApplication.instance() or QApplication([f"gui-smoke-{app_name}"])
    module = _load_entrypoint(app_name, spec)
    window_type = getattr(module, spec.window_class)
    window = window_type()
    window.show()
    qt_app.processEvents()

    status_items = set(getattr(window.status_panel, "_items", {}))
    if status_items != set(spec.status_keys):
        raise AssertionError(
            f"{app_name} status keys are {sorted(status_items)}, expected {sorted(spec.status_keys)}."
        )

    if spec.uses_selector:
        selectors = window.findChildren(RuntimeSelectorWidget)
        if len(selectors) != 1:
            raise AssertionError(f"{app_name} has {len(selectors)} runtime selectors; expected 1.")
    else:
        contexts = window.findChildren(RuntimeContextWidget)
        if len(contexts) != 1:
            raise AssertionError(f"{app_name} has {len(contexts)} runtime contexts; expected 1.")
        context = contexts[0]
        if context.backend_label.text() != "Backend: Virtual Machine":
            raise AssertionError(f"Unexpected {app_name} backend label: {context.backend_label.text()!r}.")
        if context.sizeHint().width() <= 0 or context.sizeHint().height() <= 0:
            raise AssertionError(f"{app_name} runtime context has an invalid size hint.")

    if app_name == "bba":
        if window.comboBox_11.isVisible():
            raise AssertionError("BBA legacy backend combo must remain hidden.")
        if window._profile_default_control_backend() != "vm":
            raise AssertionError("BBA did not retain the global VM backend.")

    window.close()
    qt_app.processEvents()
    print(f"PASS {app_name}")


def _run_parent(selected_apps: list[str]) -> None:
    _require_offscreen_plugin()
    mpl_config_dir = Path(tempfile.gettempdir()) / "half_linac_gui_smoke_mpl"
    mpl_config_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update(
        {
            "QT_QPA_PLATFORM": "offscreen",
            "MPLCONFIGDIR": str(mpl_config_dir),
            "PYTHONPATH": os.pathsep.join(
                path for path in (str(REPO_PARENT), env.get("PYTHONPATH", "")) if path
            ),
            "HALF_LINAC_MACHINE_ID": "half",
            "HALF_LINAC_CONTROL_BACKEND": "vm",
            "HALF_MACHINE_ID": "half",
            "HALF_CONTROL_BACKEND": "vm",
            "EPICS_CA_AUTO_ADDR_LIST": "NO",
            "EPICS_CA_ADDR_LIST": "127.0.0.1",
        }
    )

    failures: list[str] = []
    for app_name in selected_apps:
        try:
            result = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--app", app_name],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            failures.append(app_name)
            print(f"FAIL {app_name}: timed out after 45 seconds", file=sys.stderr)
            if exc.stdout:
                print(exc.stdout.rstrip(), file=sys.stderr)
            if exc.stderr:
                print(exc.stderr.rstrip(), file=sys.stderr)
            continue
        if result.returncode == 0:
            print(result.stdout.strip())
            continue
        failures.append(app_name)
        print(f"FAIL {app_name}", file=sys.stderr)
        if result.stdout.strip():
            print(result.stdout.rstrip(), file=sys.stderr)
        if result.stderr.strip():
            print(result.stderr.rstrip(), file=sys.stderr)

    if failures:
        raise SystemExit(f"GUI smoke failed: {', '.join(failures)}")
    print(f"GUI layout smoke passed for {len(selected_apps)} app(s).")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", choices=tuple(GUI_SMOKE_SPECS), help=argparse.SUPPRESS)
    parser.add_argument(
        "apps",
        nargs="*",
        help="Optional subset of apps; the default checks every supported app.",
    )
    args = parser.parse_args()

    if args.app:
        _run_child(args.app)
        return 0

    unknown_apps = sorted(set(args.apps) - set(GUI_SMOKE_SPECS))
    if unknown_apps:
        parser.error(
            "unknown app(s): "
            + ", ".join(unknown_apps)
            + "; choose from "
            + ", ".join(GUI_SMOKE_SPECS)
        )
    selected_apps = args.apps or list(GUI_SMOKE_SPECS)
    _run_parent(selected_apps)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
