#!/usr/bin/env python3
"""Construct operator GUIs offscreen and verify their runtime-context layout."""

from __future__ import annotations

import argparse
import copy
import importlib.util
import math
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
    "launcher_irfel_vm": GuiSmokeSpec(
        APP_ROOT / "launcher" / "main.py",
        "myWindow",
        frozenset({"real_access", "running"}),
        uses_selector=True,
        machine_id="irfel",
        control_backend="vm",
        expected_hv_feedback_enabled=False,
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
    "ct_monitor": GuiSmokeSpec(
        APP_ROOT / "ct_monitor" / "main.py",
        "CTMonitorWindow",
        frozenset({"connection", "pairing"}),
    ),
    "ct_monitor_real": GuiSmokeSpec(
        APP_ROOT / "ct_monitor" / "main.py",
        "CTMonitorWindow",
        frozenset({"connection", "pairing"}),
        control_backend="real",
    ),
    "ct_monitor_irfel_real": GuiSmokeSpec(
        APP_ROOT / "ct_monitor" / "main.py",
        "CTMonitorWindow",
        frozenset({"connection", "pairing"}),
        machine_id="irfel",
        control_backend="real",
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
    ct_pv_calls = []
    if app_name.startswith("ct_monitor"):
        class SmokePV:
            def __init__(self, pvname, **kwargs):
                self.pvname = pvname
                self.init_kwargs = kwargs
                self.callback_kwargs = None
                ct_pv_calls.append(self)

            def add_callback(self, _callback, **kwargs):
                self.callback_kwargs = kwargs

            def clear_callbacks(self):
                return None

        module.PV = SmokePV
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

    if app_name == "launcher_irfel_real" and not window.ct_monitor_button.isEnabled():
        raise AssertionError("IRFEL real launcher did not enable CT Monitor.")
    if app_name == "launcher_irfel_vm" and window.ct_monitor_button.isEnabled():
        raise AssertionError("IRFEL VM launcher incorrectly enabled the real-only CT Monitor.")

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
        if app_name.startswith("ct_monitor"):
            expected_size = module.THEME_BUTTON_SIZE
            if window.theme_button.width() != expected_size or window.theme_button.height() != expected_size:
                raise AssertionError("CT theme toggle does not match the compact BBA button size.")
        window._toggle_theme()
        if window.current_theme == expected_theme:
            raise AssertionError(f"{app_name} could not switch its inherited theme independently.")

    if app_name == "bba":
        if window.comboBox_11.isVisible():
            raise AssertionError("BBA legacy backend combo must remain hidden.")
        if window._profile_default_control_backend() != "vm":
            raise AssertionError("BBA did not retain the global VM backend.")

    if app_name.startswith("ct_monitor"):
        import time

        if window.pause_button.parent() is not window.status_panel.parent():
            raise AssertionError("CT Pause button was not moved into the device-control row.")
        if window.clear_button.parent() is not window.status_panel.parent():
            raise AssertionError("CT Clear button was not moved into the device-control row.")
        if window.theme_button.parent() is window.pause_button.parent():
            raise AssertionError("CT theme button should remain in the title row.")
        if window.selection_policy_label.text():
            raise AssertionError("CT monitor shows redundant physical-order status text.")
        expected_pv_count = len(window.measurement_elements) + len(window.fct_elements)
        if len(ct_pv_calls) != expected_pv_count:
            raise AssertionError("CT monitor created an unexpected number of PV monitors.")
        measurement_pvs = [pv for pv in ct_pv_calls if "FCT" not in pv.pvname]
        fct_pvs = [pv for pv in ct_pv_calls if "FCT" in pv.pvname]
        if any(pv.init_kwargs.get("form") != "time" for pv in measurement_pvs):
            raise AssertionError("CT monitor ICT callbacks must retain CA time metadata.")
        if any(pv.init_kwargs.get("form") != "ctrl" for pv in fct_pvs):
            raise AssertionError("CT monitor FCT callback must receive CTRL units metadata.")
        if any(pv.callback_kwargs.get("with_ctrlvars") is not False for pv in ct_pv_calls):
            raise AssertionError("CT monitor unexpectedly requested synchronous CTRL metadata.")
        timestamp = time.time()
        upstream_id, downstream_id = window._selected_ids()
        uses_coulomb_source = (
            window.measurement_channel == "charge" and spec.control_backend == "vm"
        )
        upstream_value = 5.5e-10 if uses_coulomb_source else 0.55
        downstream_value = 4.4e-10 if uses_coulomb_source else 0.44
        for index in range(8):
            sample_time = timestamp + index * 0.01
            window.store.update(upstream_id, value=upstream_value, timestamp=sample_time)
            window.store.update(downstream_id, value=downstream_value, timestamp=sample_time + 0.005)
            if window.fct_elements:
                window.store.update(
                    window.fct_elements[0].id,
                    value=12.0 + index,
                    timestamp=sample_time,
                )
        window._refresh()
        if len(window.transmission_history) != 8:
            raise AssertionError(
                "CT monitor did not preserve every queued sample between GUI refreshes."
            )
        if window.efficiency_card.value_label.text() != "80.00%":
            raise AssertionError("CT monitor did not display the queued-pair efficiency.")
        if window.fct_elements and len(window.fct_history) != 8:
            raise AssertionError("CT monitor did not preserve every queued FCT sample.")
        if not window.fct_elements and window.fct_history:
            raise AssertionError("CT monitor created FCT history without an available FCT channel.")
        if app_name == "ct_monitor_irfel_real":
            if window.measurement_channel != "current" or window.measurement_unit != "A":
                raise AssertionError("IRFEL CT monitor did not select current in amperes.")
            if window.upstream_card.value_label.text() != "0.55 A":
                raise AssertionError("IRFEL CT monitor did not display ICT current in amperes.")
            if window.measurement_axis.get_ylabel() != "Current (A)":
                raise AssertionError("IRFEL CT current trend has the wrong axis label.")
            metric_cards = (
                window.upstream_card,
                window.downstream_card,
                window.efficiency_card,
                window.statistics_card,
            )
            widths_before = [card.width() for card in metric_cards]
            if max(widths_before) - min(widths_before) > 1:
                raise AssertionError(
                    f"IRFEL CT metric cards are not equal width: {widths_before}."
                )
            window.efficiency_card.set_value(
                "N/A",
                "timestamp mismatch while waiting for paired update",
                True,
            )
            qt_app.processEvents()
            widths_after = [card.width() for card in metric_cards]
            if widths_after != widths_before:
                raise AssertionError("CT metric card widths changed with detail text.")
            if window.efficiency_card.detail_label.toolTip() != (
                "timestamp mismatch while waiting for paired update"
            ):
                raise AssertionError("CT metric detail tooltip did not preserve full text.")
        if window.trend_window_combo.count() != 4 or window.rolling_window_combo.count() != 4:
            raise AssertionError("CT monitor is missing trend or rolling window choices.")
        if not window.trend_window_combo.isEditable() or not window.rolling_window_combo.isEditable():
            raise AssertionError("CT monitor trend and rolling controls must accept manual input.")
        if not isinstance(window.trend_window_combo, module.CleanComboBox):
            raise AssertionError("CT monitor did not apply the clean combo-box style.")
        window.trend_window_combo.setEditText("90")
        window._apply_trend_window_input()
        window.rolling_window_combo.setEditText("75")
        window._apply_rolling_window_input()
        if window.trend_window_s != 90.0 or window.rolling_window != 75:
            raise AssertionError("CT monitor did not apply valid custom window values.")
        window.trend_window_combo.setEditText("9")
        window._apply_trend_window_input()
        window.rolling_window_combo.setEditText("1001")
        window._apply_rolling_window_input()
        if window.trend_window_s != 90.0 or window.rolling_window != 75:
            raise AssertionError("CT monitor did not reject out-of-range window values.")
        window.trend_window_combo.setEditText("30")
        window._apply_trend_window_input()
        window.rolling_window_combo.setEditText("100")
        window._apply_rolling_window_input()
        old_sample = module.TransmissionSample(timestamp - 200.0, 0.55, 0.44, 80.0)
        window.transmission_history.insert(0, old_sample)
        window._refresh()
        if old_sample not in window.transmission_history:
            raise AssertionError("CT rolling history was incorrectly trimmed by trend span.")
        if "n=9" not in window.statistics_card.detail_label.text():
            raise AssertionError("CT rolling statistics did not use retained valid samples.")
        plotted_x = window.measurement_axis.lines[0].get_xdata()
        if any(value < -30.0 for value in plotted_x if math.isfinite(value)):
            raise AssertionError("CT trend plotted samples outside the selected time span.")
        if window.measurement_axis.get_ylim()[0] > 0.0:
            raise AssertionError("CT measurement axis does not include zero.")
        if window.efficiency_axis.get_ylim()[1] < 110.0:
            raise AssertionError("CT efficiency axis default range is too narrow.")
        window.trend_gap_s = 3.0
        gap_x, gap_up, _gap_down, _gap_eff = window._transmission_plot_series(
            [
                module.TransmissionSample(timestamp - 10.0, 0.55, 0.44, 80.0),
                module.TransmissionSample(timestamp - 1.0, 0.55, 0.44, 80.0),
            ],
            timestamp,
        )
        if not any(math.isnan(value) for value in gap_up) or len(gap_x) != 3:
            raise AssertionError("CT trend did not break the line across a beam gap.")
        window._toggle_pause()
        paused_count = len(window.transmission_history)
        window.store.update(upstream_id, value=upstream_value, timestamp=timestamp + 1.0)
        window.store.update(downstream_id, value=downstream_value, timestamp=timestamp + 1.005)
        window._refresh()
        if len(window.transmission_history) != paused_count:
            raise AssertionError("CT monitor retained samples while paused.")
        pairing_text = window.status_panel._items["pairing"][1].text()
        if "Paused" not in pairing_text:
            raise AssertionError("CT monitor does not expose paused state in its compact badge.")
        if "discarded" not in window.statusBar().currentMessage():
            raise AssertionError("CT monitor does not explain its paused discard policy.")
        window._toggle_pause()
        window._swap_selection()
        if "Reverse order" not in window.selection_policy_label.text():
            raise AssertionError("CT monitor did not warn about a reverse-order CT pair.")

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
        expected_metrics = {"hv_setpoint", "hv_readback", "hv_mismatch"}
        if set(window._value_labels) != expected_metrics:
            raise AssertionError("HV feedback Latest Sample metrics are incomplete.")
        if window.feedback_unit_combo.count() != 1:
            raise AssertionError("HV feedback did not expose the configured feedback unit.")
        if window.feedback_channel_combo.count() != 2:
            raise AssertionError("HV feedback did not expose the unit's RF channels.")
        if set(window._rf_value_labels) != {"acc1", "buncher"}:
            raise AssertionError("HV feedback RF channel metrics are incomplete.")

        reference = window.config["reference"]
        timestamp = time.time()
        sample = {
            "event": "SAMPLE",
            "timestamp": timestamp,
            "hv_setpoint": reference["hv_kv"],
            "hv_readback": reference["hv_kv"],
        }
        for channel_id, values in reference["channels"].items():
            sample[f"rf.{channel_id}.amplitude"] = values["amplitude"]
            sample[f"rf.{channel_id}.phase"] = values["phase_deg"]
        window._operation = "monitor"
        window._append_sample(sample)
        window._append_hv_command(
            {"event": "CAPUT_HV", "timestamp": timestamp, "hv_next": reference["hv_kv"]}
        )
        window._draw_plots()
        if "ratio +0.000%" not in window._rf_value_labels["buncher"]["amplitude"].text():
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

        invalid_sample = dict(sample, timestamp=timestamp + 1.0)
        invalid_sample["rf.acc1.phase"] = None
        window._append_sample(invalid_sample)
        if window._rf_value_labels["acc1"]["phase"].text() != "INVALID":
            raise AssertionError("HV feedback retained a stale phase value after an invalid sample.")
        window.feedback_channel_combo.setCurrentIndex(1)
        if window.feedback_channel_id != "buncher":
            raise AssertionError("HV feedback did not switch the selected feedback channel.")
        if window._signal_history["time"]:
            raise AssertionError("HV feedback did not clear history after a channel switch.")
        window._set_busy(True)
        if window.feedback_unit_combo.isEnabled() or window.feedback_channel_combo.isEnabled():
            raise AssertionError("HV feedback selectors remained enabled during an operation.")
        window._set_busy(False)
        single = copy.deepcopy(window.config)
        single["feedback_unit_id"] = "single"
        single["feedback_unit_label"] = "Single-channel unit"
        single["rf_channels"] = single["rf_channels"][:1]
        single["reference"]["channels"] = {
            "acc1": single["reference"]["channels"]["acc1"]
        }
        single["safety"]["phase_limit_deg"] = {
            "acc1": single["safety"]["phase_limit_deg"]["acc1"]
        }
        single["pvs"] = {
            key: value
            for key, value in single["pvs"].items()
            if key in {
                "hv_setpoint",
                "hv_readback",
                "rf.acc1.amplitude",
                "rf.acc1.phase",
            }
        }
        window.base_configs["single"] = copy.deepcopy(single)
        window.unit_configs["single"] = copy.deepcopy(single)
        window.selected_feedback_channels["single"] = "acc1"
        window.feedback_unit_combo.addItem("Single-channel unit", "single")
        window.feedback_unit_combo.setCurrentIndex(1)
        if window.active_unit_id != "single":
            raise AssertionError("HV feedback did not switch feedback units.")
        if window.feedback_channel_combo.count() != 1:
            raise AssertionError("HV feedback did not rebuild a one-channel unit layout.")
        if set(window._rf_value_labels) != {"acc1"}:
            raise AssertionError("HV feedback retained channels from the previous unit.")
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
