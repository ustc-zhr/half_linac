#!/usr/bin/env python3
"""Construct operator GUIs offscreen and verify their runtime-context layout."""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
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
    machine_id: str = "half"
    control_backend: str = "vm"
    expected_hv_feedback_enabled: bool | None = None


GUI_SMOKE_SPECS = {
    "launcher": GuiSmokeSpec(
        APP_ROOT / "launcher" / "main.py",
        "myWindow",
        frozenset({"real_access", "running"}),
        uses_selector=True,
        expected_hv_feedback_enabled=False,
    ),
    "launcher_irfel_real": GuiSmokeSpec(
        APP_ROOT / "launcher" / "main.py",
        "myWindow",
        frozenset({"real_access", "running"}),
        uses_selector=True,
        machine_id="irfel",
        control_backend="real",
        expected_hv_feedback_enabled=True,
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
    "hv_feedback": GuiSmokeSpec(
        APP_ROOT / "hv_feedback" / "main.py",
        "HVFeedbackWindow",
        frozenset({"operation", "state", "write", "log"}),
        machine_id="irfel",
        control_backend="real",
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

    from half_linac.src.shared.app_theme import resolve_initial_theme
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

    if spec.expected_hv_feedback_enabled is not None:
        actual = window.hv_feedback_button.isEnabled()
        if actual is not spec.expected_hv_feedback_enabled:
            raise AssertionError(
                f"{app_name} HV feedback enabled={actual}; "
                f"expected {spec.expected_hv_feedback_enabled}."
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
        expected_backend_label = (
            "Backend: Real Machine"
            if spec.control_backend == "real"
            else "Backend: Virtual Machine"
        )
        if context.backend_label.text() != expected_backend_label:
            raise AssertionError(f"Unexpected {app_name} backend label: {context.backend_label.text()!r}.")
        if context.sizeHint().width() <= 0 or context.sizeHint().height() <= 0:
            raise AssertionError(f"{app_name} runtime context has an invalid size hint.")

        expected_theme = resolve_initial_theme()
        if window.current_theme != expected_theme:
            raise AssertionError(
                f"{app_name} started with theme {window.current_theme!r}; "
                f"expected {expected_theme!r}."
            )
        window._toggle_theme()
        if window.current_theme == expected_theme:
            raise AssertionError(f"{app_name} could not switch its inherited theme independently.")

    if app_name == "bba":
        if window.comboBox_11.isVisible():
            raise AssertionError("BBA legacy backend combo must remain hidden.")
        if window._profile_default_control_backend() != "vm":
            raise AssertionError("BBA did not retain the global VM backend.")

    if app_name == "hv_feedback":
        required_buttons = (
            "start_monitor_button",
            "start_feedback_button",
            "measure_reference_button",
            "stop_button",
        )
        missing_buttons = [name for name in required_buttons if not hasattr(window, name)]
        if missing_buttons:
            raise AssertionError(
                "HV feedback is missing required actions: " + ", ".join(missing_buttons)
            )
        for forbidden_name in ("mock_check", "mode_combo", "write_check", "config_edit"):
            if hasattr(window, forbidden_name):
                raise AssertionError(f"HV feedback retained forbidden control {forbidden_name!r}.")
        visible_parameter_count = sum(
            spin.isVisible()
            for fields in window._parameter_spins.values()
            for spin in fields.values()
        )
        if visible_parameter_count != 4:
            raise AssertionError(
                "HV feedback should expose four primary control parameters; "
                f"found {visible_parameter_count}."
            )
        for dialog_name in (
            "advanced_settings_dialog",
            "reference_measurement_dialog",
            "reference_dialog",
            "safety_dialog",
        ):
            dialog = getattr(window, dialog_name, None)
            if dialog is None or dialog.isVisible():
                raise AssertionError(
                    f"HV feedback parameter dialog {dialog_name!r} is missing or opened at startup."
                )
        if window.session_thread is not None or window.reference_thread is not None:
            raise AssertionError("HV feedback started EPICS work while constructing the window.")
        if window.plot_scale_combo.currentText() != "Relative":
            raise AssertionError("HV feedback trends should default to the relative scale.")
        if window.plot_window_combo.currentText() != "Recent 15 min":
            raise AssertionError("HV feedback trends should default to the recent 15 minute view.")
        if window.plot_time_axis_combo.currentText() != "Elapsed":
            raise AssertionError("HV feedback trends should default to elapsed time.")
        expected_metrics = {
            "hv_setpoint",
            "hv_readback",
            "hv_mismatch",
            "acc1_level",
            "amp_ratio_error",
            "phase_error",
        }
        if set(window._value_labels) != expected_metrics:
            raise AssertionError("HV feedback Latest Sample metrics are incomplete.")

        reference = window.config["reference"]
        timestamp = time.time()
        sample = {
            "event": "SAMPLE",
            "timestamp": timestamp,
            "hv_setpoint": reference["hv0"],
            "hv_readback": reference["hv0"],
            "acc1_amp": reference["acc1_amp_ref"],
            "buncher_amp": reference["acc1_amp_ref"] * reference["amp_ratio_ref"],
            "acc1_phase": reference["acc1_phase_ref"],
            "buncher_phase": reference["buncher_phase_ref"],
        }
        window._operation = "monitor"
        window._append_sample(sample)
        window._append_hv_command(
            {"event": "CAPUT_HV", "timestamp": timestamp, "hv_next": reference["hv0"]}
        )
        window._draw_plots()
        if window._value_labels["amp_ratio_error"].text() != "+0.000%":
            raise AssertionError("HV feedback did not derive the live amplitude-ratio error.")
        if "Computed target" not in window.hv_axis.get_legend_handles_labels()[1]:
            raise AssertionError("HV feedback trend omitted the computed HV target.")
        window.plot_scale_combo.setCurrentText("Raw")
        if window.amp_axis.get_ylabel() != "Amplitude (a.u.)":
            raise AssertionError("HV feedback raw trend scale did not apply.")
        window.plot_time_axis_combo.setCurrentText("Clock")
        if window.hv_axis.get_xlabel() != "Clock time":
            raise AssertionError("HV feedback clock-time axis did not apply.")
        window.plot_scale_combo.setCurrentText("Relative")
        window.plot_time_axis_combo.setCurrentText("Elapsed")

        invalid_sample = dict(sample, timestamp=timestamp + 1.0, acc1_phase=None)
        window._append_sample(invalid_sample)
        if window._value_labels["phase_error"].text() != "INVALID":
            raise AssertionError("HV feedback retained a stale phase value after an invalid sample.")
        window._reset_monitor_display()
        if window._signal_history["time"] or window._hv_command_history["time"]:
            raise AssertionError("HV feedback did not clear trend history for a new session.")

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
        spec = GUI_SMOKE_SPECS[app_name]
        app_env = env.copy()
        app_env.update(
            {
                "HALF_LINAC_MACHINE_ID": spec.machine_id,
                "HALF_LINAC_CONTROL_BACKEND": spec.control_backend,
                "HALF_MACHINE_ID": spec.machine_id,
                "HALF_CONTROL_BACKEND": spec.control_backend,
            }
        )
        try:
            result = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--app", app_name],
                cwd=REPO_ROOT,
                env=app_env,
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
