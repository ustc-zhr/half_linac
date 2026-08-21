from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "repo_bootstrap.py").is_file())
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from repo_bootstrap import ensure_repo_import_path
ensure_repo_import_path(__file__)

import numpy as np
from epics import PV, caget, caput
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QSizePolicy, QSpinBox, QToolBar, QToolButton, QVBoxLayout, QWidget,
)
from half_linac.src.shared.app_theme import resolve_initial_theme

from half_linac.src.apps.rf_phase_scan.energy_match_tuner import (
    RFPhaseEnergyMatcher, reference_x_pixel,
)
from half_linac.src.apps.rf_phase_scan.image_acquisition import RFImageAcquisition
from half_linac.src.apps.rf_phase_scan.mplwidget import MplWidget
from half_linac.src.apps.rf_phase_scan.spectrum_profile import (
    SpectrumProfileError, fit_projection_profile, project_image_profiles,
)
from half_linac.src.shared.beam_diagnostics.background_store import (
    BackgroundStoreError, load_background, save_background,
)


PALETTES = {
    "dark": {
        "window": "#0f1519", "panel": "#172027", "border": "#24333d",
        "summary": "#1b262d", "text": "#e6edf2", "muted": "#90a1ad",
        "input": "#10171c", "button": "#11191f", "button_hover": "#18242c",
        "accent": "#45d0bc", "warning": "#e4b86f", "plot": "#11181e",
        "grid": "#2a3943", "trace": "#6cb6ff",
    },
    "light": {
        "window": "#f2ede5", "panel": "#fffdf9", "border": "#d7cec1",
        "summary": "#fcf9f3", "text": "#2c3942", "muted": "#7c7368",
        "input": "#fffdf9", "button": "#f8f3eb", "button_hover": "#efe6d9",
        "accent": "#2d7f6d", "warning": "#a97118", "plot": "#fffdf8",
        "grid": "#ddd4c7", "trace": "#2f7dc5",
    },
}


def build_style(p):
    return f"""
QMainWindow, QDialog, QWidget#centralwidget {{ background: {p['window']}; color: {p['text']}; font-family: "IBM Plex Sans", "Source Han Sans SC", "Segoe UI", sans-serif; }}
QFrame#summaryPanel {{ background: {p['summary']}; border: 1px solid {p['border']}; border-radius: 14px; }}
QFrame#workspaceCard, QFrame#plotCard {{ background: {p['panel']}; border: 1px solid {p['border']}; border-radius: 14px; }}
QFrame#backgroundCard, QGroupBox#dialogCard {{ background: {p['panel']}; border: 1px solid {p['border']}; border-radius: 12px; margin-top: 0; padding: 0; }}
QDialog#energySpectrumDialog QToolBar {{ background-color: {p['plot']}; border: none; spacing: 2px; }}
QDialog#energySpectrumDialog QToolBar QToolButton {{ background: transparent; border: none; border-radius: 4px; padding: 3px; }}
QDialog#energySpectrumDialog QToolBar QToolButton:hover {{ background-color: {p['button_hover']}; }}
QLabel {{ color: {p['text']}; font-size: 12px; font-weight: 600; background: transparent; }}
QLabel#summaryTitle {{ font-size: 22px; font-weight: 700; }}
QLabel#summarySubtitle, QLabel[role="muted"] {{ color: {p['muted']}; font-size: 11px; }}
QLabel#cardTitle, QLabel#dialogCardTitle {{ font-size: 14px; font-weight: 700; }}
QLabel#metricValue {{ color: {p['accent']}; font-size: 17px; font-weight: 700; }}
QComboBox, QDoubleSpinBox, QSpinBox {{ background: {p['input']}; border: 1px solid {p['border']}; border-radius: 9px; color: {p['text']}; padding: 5px 8px; min-height: 20px; }}
QPushButton {{ background: {p['button']}; border: 1px solid {p['border']}; border-radius: 10px; color: {p['text']}; padding: 6px 10px; min-height: 28px; font-weight: 700; }}
QPushButton:hover {{ background: {p['button_hover']}; }}
QPushButton:disabled {{ color: {p['muted']}; }}
QPushButton#startButton {{ border-color: {p['accent']}; }}
QPushButton#stopButton {{ border-color: {p['warning']}; }}
QToolButton#themeToggleButton {{ background: {p['button']}; border: 1px solid {p['border']}; border-radius: 11px; color: {p['text']}; font-size: 14px; font-weight: 700; }}
QToolButton#themeToggleButton:hover {{ background: {p['button_hover']}; }}
QToolButton#themeToggleButton:pressed {{ background: {p['input']}; }}
QCheckBox {{ color: {p['text']}; font-size: 12px; font-weight: 600; spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {p['border']}; border-radius: 4px; background-color: {p['input']}; }}
QCheckBox::indicator:checked {{ background-color: {p['accent']}; border: 2px solid {p['text']}; }}
QProgressBar {{ background: {p['input']}; border: 1px solid {p['border']}; border-radius: 7px; color: {p['text']}; min-height: 12px; text-align: center; }}
QProgressBar::chunk {{ background: {p['accent']}; border-radius: 6px; }}
"""


def build_status_strip_style(p):
    return f"""
QWidget#statusStrip {{ background: {p['input']}; border: 1px solid {p['border']}; border-radius: 10px; }}
QFrame#statusItem {{ background: transparent; border: none; border-left: 4px solid {p['muted']}; border-radius: 0; }}
QFrame#statusItem[tone="success"] {{ border-left-color: {p['accent']}; }}
QFrame#statusItem[tone="warning"] {{ border-left-color: {p['warning']}; }}
QFrame#statusSeparator {{ background: {p['border']}; min-width: 1px; max-width: 1px; border: none; }}
QLabel[role="title"] {{ color: {p['muted']}; background: transparent; border: none; font-size: 9px; font-weight: 700; letter-spacing: 0.8px; }}
QLabel[role="value"][tone="subtle"] {{ color: {p['text']}; background: transparent; border: none; font-size: 13px; font-weight: 700; }}
QLabel[role="value"][tone="success"] {{ color: {p['accent']}; background: transparent; border: none; font-size: 13px; font-weight: 700; }}
QLabel[role="value"][tone="warning"] {{ color: {p['warning']}; background: transparent; border: none; font-size: 13px; font-weight: 700; }}
"""


class RFPhaseStatusStrip(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = {}
        self.setObjectName("statusStrip")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(0)
        self._layout = layout

    def add_item(self, key, title, value):
        if self._items:
            separator = QFrame(self)
            separator.setObjectName("statusSeparator")
            separator.setFrameShape(QFrame.VLine)
            separator.setFrameShadow(QFrame.Plain)
            self._layout.addWidget(separator)
        container = QFrame(self)
        container.setObjectName("statusItem")
        container.setProperty("tone", "subtle")
        container.setMinimumWidth(112)
        inner = QVBoxLayout(container)
        inner.setContentsMargins(8, 0, 6, 0)
        inner.setSpacing(2)
        title_label = QLabel(title, container)
        title_label.setProperty("role", "title")
        value_label = QLabel(value, container)
        value_label.setProperty("role", "value")
        value_label.setProperty("tone", "subtle")
        value_label.setWordWrap(True)
        inner.addWidget(title_label)
        inner.addWidget(value_label)
        self._layout.addWidget(container)
        self._items[key] = (container, value_label)

    def finish(self):
        self._layout.addStretch(1)

    def apply_theme(self, palette):
        self.setStyleSheet(build_status_strip_style(palette))
        for container, value_label in self._items.values():
            self._refresh_tone(container, value_label)

    def set_item(self, key, text, tone="subtle", tooltip=None):
        item = self._items.get(key)
        if item is None:
            return
        container, value_label = item
        container.setProperty("tone", tone)
        value_label.setProperty("tone", tone)
        value_label.setText(text)
        container.setToolTip(tooltip or "")
        value_label.setToolTip(tooltip or "")
        self._refresh_tone(container, value_label)

    @staticmethod
    def _refresh_tone(container, value_label):
        for widget in (container, value_label):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()
from half_linac.src.apps.rf_phase_scan.phase_energy_scan import (
    EnergyMatchResult, PhaseEnergyScanner, PhaseScanSettings, phase_difference_deg,
)
from half_linac.src.apps.rf_phase_scan.phase_scan_log import PhaseEnergyScanLog
from half_linac.src.shared.machine_profile.app_runtime import resolve_app_runtime_paths
from half_linac.src.shared.machine_profile import (
    MachineProfileError, RuntimeContextWidget, get_workflow, list_elements, load_app_context,
    require_workflow_write_allowed, resolve_channel, resolve_element_image_geometry,
    resolve_write_target, EnergyControlLock,
)


def _factory(*, image_pv, image_shape, pixel_width_mm, bend_pv, scan, progress, cancel,
             remove_bg=False, bg_image=None):
    return RFPhaseEnergyMatcher(
        flag_pv_obj=image_pv, flag_pixel=image_shape, bend_pv=bend_pv,
        design_eta_m=float(scan["design_eta_m"]),
        progress_callback=progress, remove_bg=remove_bg, bg_image=bg_image,
        settle_time_s=float(scan.get("settle_time_s", 1)),
        restore_initial_on_failure=bool(scan.get("restore_initial_on_failure", True)),
        cancel_requested=cancel,
        restore_initial_on_cancel=True,
        frame_samples=int(scan.get("frame_samples", 3)),
        min_valid_frames=int(scan.get("min_valid_frames", 2)),
        verification_frame_samples=int(scan.get("verification_frame_samples", 5)),
        verification_min_valid_frames=int(scan.get("verification_min_valid_frames", 3)),
        frame_interval_s=float(scan.get("frame_interval_s", 0.2)),
        pixel_width_mm=float(pixel_width_mm),
        profile_fit_method=str(scan.get("profile_fit_method", "Gauss fit")),
        x_reference_mm=float(scan.get("x_reference_mm", 0.0)),
        center_tolerance_mm=float(scan.get("center_tolerance_mm", 0.2)),
        max_iterations=int(scan.get("max_iterations", 6)),
        max_correction_step_mev=float(scan.get("max_correction_step_mev", 25)),
    )


class ScanThread(QThread):
    point = pyqtSignal(dict)
    diagnostic = pyqtSignal(object, float)
    done = pyqtSignal(dict)
    state = pyqtSignal(str)
    progress = pyqtSignal(int, int)
    match_state = pyqtSignal(dict)

    def __init__(self, *, context, target, settings, scan, image_pv, image_shape,
                 pixel_width_mm, phase_pv, energy_pv, remove_bg, bg_image,
                 background_metadata, point_measurement):
        super().__init__()
        self.context, self.target, self.settings = context, target, settings
        self.scan, self.image_pv, self.image_shape = dict(scan), image_pv, image_shape
        self.pixel_width_mm = float(pixel_width_mm)
        self.phase_pv, self.energy_pv = phase_pv, energy_pv
        self.remove_bg = bool(remove_bg)
        self.bg_image = bg_image
        self.acquisition = RFImageAcquisition(
            image_pv,
            image_shape,
            pixel_width_mm,
            background=bg_image if remove_bg else None,
        )
        self.background_metadata = dict(background_metadata or {})
        self.point_measurement = dict(point_measurement)

    def _match(self, center, low, high, attempt):
        scan = dict(self.scan)
        scan.update(min=float(low), max=float(high), start_energy=float(center))
        tuner = _factory(image_pv=self.image_pv, image_shape=self.image_shape,
                         pixel_width_mm=self.pixel_width_mm,
                         bend_pv=self.energy_pv, scan=scan, progress=self.match_state.emit,
                         cancel=self.isInterruptionRequested,
                         remove_bg=self.remove_bg, bg_image=self.bg_image)
        best = tuner.run(B_min=float(low), B_max=float(high),
                         start_energy=float(center),
                         reacquire_steps=int(scan.get("reacquire_steps", 16)))
        info = tuner.center_lock_result or {}
        if best is not None:
            samples = int(self.point_measurement["samples"])
            interval_s = float(self.point_measurement["interval_s"])
            min_valid = int(self.point_measurement["min_valid_samples"])
            try:
                measurement = self.acquisition.sample_profile(
                    samples=samples,
                    min_valid=min_valid,
                    interval_s=interval_s,
                    fit_method=str(scan.get("profile_fit_method", "Gauss fit")),
                    cancel_requested=self.isInterruptionRequested,
                )
            except InterruptedError:
                return EnergyMatchResult(False, "CANCELLED", message="Point measurement cancelled.")
            if measurement is None:
                return EnergyMatchResult(
                    False, "MEASUREMENT_FAILED", energy_mev=float(best),
                    message=f"Point measurement did not retain the required {min_valid}/{samples} valid frames.",
                    valid_frames=0,
                )
            self.diagnostic.emit(measurement["raw_image"], float(best))
            return EnergyMatchResult(
                ok=True, status=tuner.get_last_status(), energy_mev=float(best),
                message=tuner.get_last_message(),
                center_offset_mm=float(measurement["center_mm"]) - float(scan.get("x_reference_mm", 0.0)),
                brightness=float(measurement["brightness"]),
                valid_frames=int(measurement["valid_frames"]),
                fit_method=str(measurement["fit_method"]),
                fit_r_squared=measurement["fit_r_squared"],
            )
        return EnergyMatchResult(
            ok=best is not None, status=tuner.get_last_status(),
            energy_mev=None if best is None else float(best), message=tuner.get_last_message(),
            center_offset_mm=info.get("final_offset_mm"), brightness=info.get("brightness"),
            valid_frames=info.get("valid_frames"), fit_method=info.get("fit_method"),
            fit_r_squared=info.get("fit_r_squared"))

    def run(self):
        log = None
        lock = None
        result = None
        try:
            self.state.emit("Preparing")
            require_workflow_write_allowed(self.context, "rf_phase_scan", "RF phase scan")
            lock = EnergyControlLock.for_machine(
                self.context.machine.id,
                {"app": "rf_phase_scan", "operation": "RF phase scan"},
            )
            lock.acquire()
            initial_phase, initial_energy = caget(self.phase_pv), caget(self.energy_pv)
            if initial_phase is None or initial_energy is None:
                raise RuntimeError("Initial phase and energy setpoints must be readable.")
            paths = resolve_app_runtime_paths(Path(__file__).resolve().parent, self.context)
            log = PhaseEnergyScanLog.create(paths["runs_dir"], {
                "machine_id": self.context.machine.id, "backend": self.context.control_backend.name,
                "station_id": "eny", "element_id": self.target.element_id,
                "phase_pv": self.phase_pv, "energy_pv": self.energy_pv,
                "initial_phase_deg": float(initial_phase), "initial_energy_mev": float(initial_energy),
                "background_used": self.remove_bg,
                "background_path": self.background_metadata.get("path"),
                "background_shape": self.background_metadata.get("shape"),
                "phase_mode": self.settings.phase_mode,
                "point_measurement": dict(self.point_measurement),
                "measurement_samples": self.point_measurement["samples"],
                "measurement_interval_s": self.point_measurement["interval_s"],
                "measurement_min_valid_samples": self.point_measurement["min_valid_samples"],
                "energy_match_settings": dict(self.scan),
            })
            scanner = PhaseEnergyScanner(
                settings=self.settings, read_phase=lambda: caget(self.phase_pv),
                set_phase=self._set_phase,
                read_energy=lambda: caget(self.energy_pv),
                set_energy=self._set_energy,
                match_energy=self._match, cancel_requested=self.isInterruptionRequested,
                progress_callback=lambda payload: self._progress(payload, log),
            )
            result = scanner.run().to_mapping()
            result["csv_path"] = str(log.csv_path)
            result["json_path"] = str(log.json_path)
            log.finish(result)
        except Exception as exc:
            result = {
                "status": "FAILED", "message": str(exc), "points": [], "fit": None,
                "phase_restored": None, "energy_restored": None,
            }
        finally:
            if log is not None:
                log.close()
            if lock is not None:
                lock.release()
        self.done.emit(result)

    def _set_phase(self, value):
        if not caput(self.phase_pv, float(value), wait=True, timeout=5):
            raise RuntimeError("LLRF phase setpoint write failed.")
        echo = caget(self.phase_pv)
        if echo is None or abs(phase_difference_deg(float(echo), float(value))) > 0.1:
            raise RuntimeError("LLRF phase setpoint echo verification failed.")

    def _set_energy(self, value):
        if not caput(self.energy_pv, float(value), wait=True, timeout=10):
            raise RuntimeError("Coordinated energy setpoint write failed.")
        echo = caget(self.energy_pv)
        if echo is None or not np.isclose(float(echo), float(value), atol=0.01, rtol=0):
            raise RuntimeError("Coordinated energy setpoint echo verification failed.")

    def _progress(self, payload, log):
        event = str(payload.get("event", ""))
        if event in {"phase_set", "match_start"}:
            self.state.emit("Running")
        elif event == "restore":
            self.state.emit("Restoring")
        if event in {"start", "phase_set", "match_start", "restore"}:
            log.write(event, **{key: value for key, value in payload.items() if key != "event"})
        if event == "point":
            point = dict(payload.get("point") or {})
            log.record_point(point)
            self.point.emit(point)
            self.progress.emit(
                int(point.get("acquisition_index", point.get("index", 0))) + 1,
                int(self.settings.points),
            )


class RFPhaseScanWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(1200, 900)
        self.resize(1360, 960)
        self.context = load_app_context("rf_phase_scan")
        self.current_theme = resolve_initial_theme()
        self.setWindowTitle(f"{self.context.machine.display_name} RF Phase Scan")
        self.config = dict(get_workflow(self.context.profile, "rf_phase_scan"))
        self.station = self.config
        self.diagnostics = dict(self.config["diagnostics"])
        self.targets = []
        if self.context.control_backend.name == "real" and self.config.get("enabled", True):
            for element in list_elements(self.context, kind="rf"):
                if "llrf" in element.tags and "wrapped_phase" in element.tags:
                    try: self.targets.append(resolve_write_target(self.context, element.id, logical_channel="phase_set", unit="deg"))
                    except MachineProfileError: pass
        self.combo = QComboBox()
        self.combo.addItems([t.element_id for t in self.targets])
        default = str(self.config.get("default_element", ""))
        idx = self.combo.findText(default)
        if idx >= 0:
            self.combo.setCurrentIndex(idx)
        scan_config = dict(self.config["scan"])
        phase_config = dict(scan_config["phase"])
        tracking_config = dict(scan_config["energy_tracking"])
        sampling_config = dict(scan_config["point_measurement"])
        self.phase_mode = QComboBox()
        self.phase_mode.addItem("Relative", "relative")
        self.phase_mode.addItem("Absolute", "absolute")
        mode_index = self.phase_mode.findData(str(phase_config.get("mode", "relative")))
        self.phase_mode.setCurrentIndex(max(mode_index, 0))
        self.low = self._spin(float(phase_config.get("low", -30)), -720, 720)
        self.high = self._spin(float(phase_config.get("high", 30)), -720, 720)
        self.points = QSpinBox()
        self.points.setRange(3, 721)
        self.points.setValue(int(phase_config.get("steps", 13)))
        self.settle = self._spin(float(sampling_config.get("settle_time_s", 1)), 0, 60)
        self.tracking = self._spin(float(tracking_config.get("tracking_half_window_mev", 25)), 0.01, 1000)
        self.fallback = self._spin(float(tracking_config.get("fallback_half_window_mev", 100)), 0.01, 2000)
        self.measurement_samples = QSpinBox()
        self.measurement_samples.setRange(1, 100)
        self.measurement_samples.setValue(int(sampling_config.get("samples_per_point", 5)))
        self.measurement_interval = self._spin(float(sampling_config.get("sample_interval_s", 0.2)), 0, 60)
        self.auto_tune_config = dict(self.config["energy_match"])
        self.auto_tune_defaults = dict(self.config["energy_match_defaults"])
        auto_scan = dict(self.auto_tune_config["search"])
        auto_center_lock = dict(self.auto_tune_defaults["center_lock"])
        self.auto_tune_min = self._spin(float(auto_scan["low"]), 0, 10000)
        self.auto_tune_max = self._spin(float(auto_scan["high"]), 0, 10000)
        self.auto_tune_coarse_steps = QSpinBox()
        self.auto_tune_coarse_steps.setRange(2, 2000)
        self.auto_tune_coarse_steps.setValue(int(auto_scan["reacquire_steps"]))
        self.auto_tune_settle = self._spin(float(auto_scan["settle_time_s"]), 0, 60)
        self.auto_tune_frame_samples = QSpinBox()
        self.auto_tune_min_valid_frames = QSpinBox()
        self.auto_tune_verification_samples = QSpinBox()
        self.auto_tune_verification_min_valid = QSpinBox()
        for spin in (
            self.auto_tune_frame_samples,
            self.auto_tune_min_valid_frames,
            self.auto_tune_verification_samples,
            self.auto_tune_verification_min_valid,
        ):
            spin.setRange(1, 100)
        self.auto_tune_frame_samples.setValue(int(auto_center_lock["frame_samples"]))
        self.auto_tune_min_valid_frames.setValue(int(auto_center_lock["min_valid_frames"]))
        self.auto_tune_verification_samples.setValue(int(auto_center_lock["verification_frame_samples"]))
        self.auto_tune_verification_min_valid.setValue(int(auto_center_lock["verification_min_valid_frames"]))
        self.auto_tune_frame_interval = self._spin(float(auto_center_lock["frame_interval_s"]), 0, 10)
        self.auto_tune_center_tolerance = self._spin(float(auto_center_lock["center_tolerance_mm"]), 0.01, 10)
        self.auto_tune_max_offset = self._spin(float(auto_center_lock["max_correction_step_mev"]), 0.01, 1000)
        self.auto_tune_fine_steps = QSpinBox()
        self.auto_tune_fine_steps.setRange(1, 100)
        self.auto_tune_fine_steps.setValue(int(auto_center_lock["max_iterations"]))
        self.auto_tune_fit_method = QComboBox()
        self.auto_tune_fit_method.addItems(["Gauss fit", "Direct"])
        fit_index = self.auto_tune_fit_method.findText(str(self.auto_tune_defaults["profile_fit_method"]))
        self.auto_tune_fit_method.setCurrentIndex(max(fit_index, 0))
        self.auto_tune_settings_button = QPushButton("Settings...")
        self.auto_tune_settings_summary = QLabel()
        self.auto_tune_settings_summary.setProperty("role", "muted")
        self.auto_tune_settings_summary.setWordWrap(True)
        self._build_auto_tune_dialog()
        self._auto_tune_default_values = self._auto_tune_settings_values()
        self._update_auto_tune_settings_summary()
        self.use_background = QCheckBox("Use background")
        self.load_background_button = QPushButton("Load File")
        self.capture_background_button = QPushButton("Sample BG")
        self.background_status = QLabel("Background disabled")
        self.background_image = None
        self.background_metadata = {}
        self.background_image_path = None
        self.runtime_paths = resolve_app_runtime_paths(Path(__file__).resolve().parent, self.context)
        self.runtime_paths["background_image_path"] = self.runtime_paths["latest_dir"] / "background.npy"
        self.runtime_paths["background_metadata_path"] = self.runtime_paths["latest_dir"] / "background.json"
        self.progress = QProgressBar()
        self.progress.setRange(0, self.points.value())
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.result = QLabel("Machine state unchanged")
        self.result.setProperty("role", "muted")
        self.result.setWordWrap(True)
        self.plot = MplWidget()
        for toolbar in self.plot.findChildren(QToolBar):
            toolbar.hide()
        self.plot.setMinimumHeight(260)
        self.plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.image_plot = MplWidget()
        for toolbar in self.image_plot.findChildren(QToolBar):
            toolbar.hide()
        self.image_plot.setMinimumHeight(280)
        self.image_plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.energy_axis = None
        self.latest_diagnostic = None
        self.last_fit = None
        self.last_initial_phase = None
        self.active_phase_mode = str(self.phase_mode.currentData())
        self.spectrum_summary = QLabel("Energy N/A  |  Spread N/A  |  Fit waiting")
        self.spectrum_summary.setProperty("role", "muted")
        self.start = QPushButton("Start scan")
        self.start.setObjectName("startButton")
        self.stop = QPushButton("Stop")
        self.stop.setObjectName("stopButton")
        self.stop.setEnabled(False)
        self.start.clicked.connect(self.start_scan)
        self.stop.clicked.connect(self.stop_scan)
        self.load_background_button.clicked.connect(self.load_background_file)
        self.capture_background_button.clicked.connect(self.capture_background)
        self.use_background.toggled.connect(self._background_toggled)
        body = QWidget()
        body.setObjectName("centralwidget")
        root = QVBoxLayout(body)
        root.setContentsMargins(16, 14, 16, 16)
        root.setSpacing(12)

        summary = QFrame()
        summary.setObjectName("summaryPanel")
        summary_layout = QVBoxLayout(summary)
        summary_layout.setContentsMargins(12, 10, 12, 10)
        summary_layout.setSpacing(6)
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(12)
        title = QLabel("RF Phase Scan")
        title.setObjectName("summaryTitle")
        runtime_context = RuntimeContextWidget(
            machine_id=self.context.machine.id,
            machine_display_name=self.context.machine.display_name,
            control_backend=self.context.control_backend.name,
            parent=summary,
        )
        self.theme_toggle_button = QToolButton(summary)
        self.theme_toggle_button.setObjectName("themeToggleButton")
        self.theme_toggle_button.setFixedSize(32, 32)
        self.theme_toggle_button.clicked.connect(self._toggle_theme)
        header_layout.addWidget(title)
        header_layout.addStretch(1)
        header_layout.addWidget(runtime_context)
        header_layout.addWidget(self.theme_toggle_button)
        summary_layout.addLayout(header_layout)

        self.status_panel = RFPhaseStatusStrip(summary)
        self.status_panel.add_item("device", "Device", self.combo.currentText() or "No LLRF")
        self.status_panel.add_item("station", "Station", str(self.diagnostics["flag_element"]))
        self.status_panel.add_item(
            "scan", "Scan", "Ready" if self.targets else "Unavailable"
        )
        self.status_panel.add_item("restore", "Restore", "Not run")
        self.status_panel.finish()
        self.status_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        summary_layout.addWidget(self.status_panel)
        root.addWidget(summary)

        workspace = QHBoxLayout()
        workspace.setSpacing(12)
        control_column = QVBoxLayout()
        control_column.setSpacing(10)
        scan_card = QFrame()
        scan_card.setObjectName("workspaceCard")
        scan_card.setFixedWidth(360)
        controls_layout = QVBoxLayout(scan_card)
        controls_layout.setContentsMargins(14, 13, 14, 14)
        controls_layout.setSpacing(10)
        controls_layout.addWidget(self._section_title("Scan setup"))
        phase_form = QFormLayout()
        phase_form.setSpacing(7)
        phase_form.addRow("LLRF", self.combo)
        phase_form.addRow("Phase mode", self.phase_mode)
        self.low_label = QLabel()
        self.high_label = QLabel()
        phase_form.addRow(self.low_label, self.low)
        phase_form.addRow(self.high_label, self.high)
        phase_form.addRow("Points", self.points)
        phase_form.addRow("Settle time", self.settle)
        controls_layout.addLayout(phase_form)
        controls_layout.addWidget(self._separator())
        controls_layout.addWidget(self._section_title("Energy tracking"))
        energy_form = QFormLayout()
        energy_form.setSpacing(7)
        energy_form.addRow("Tracking window", self.tracking)
        energy_form.addRow("Fallback window", self.fallback)
        controls_layout.addLayout(energy_form)
        controls_layout.addWidget(self._separator())
        auto_tune_header = QHBoxLayout()
        auto_tune_header.addWidget(self._section_title("Energy Match"))
        auto_tune_header.addStretch(1)
        auto_tune_header.addWidget(self.auto_tune_settings_button)
        controls_layout.addLayout(auto_tune_header)
        controls_layout.addWidget(self.auto_tune_settings_summary)
        controls_layout.addWidget(self._separator())
        controls_layout.addWidget(self._section_title("Point measurement"))
        measurement_form = QFormLayout()
        measurement_form.setSpacing(7)
        measurement_form.addRow("Samples", self.measurement_samples)
        measurement_form.addRow("Interval", self.measurement_interval)
        controls_layout.addLayout(measurement_form)
        controls_layout.addStretch(1)
        action_layout = QHBoxLayout()
        action_layout.addWidget(self.start, 1)
        action_layout.addWidget(self.stop)
        controls_layout.addLayout(action_layout)
        controls_layout.addWidget(self.progress)

        background_card = QFrame()
        background_card.setObjectName("backgroundCard")
        background_card.setFixedWidth(360)
        background_layout = QVBoxLayout(background_card)
        background_layout.setContentsMargins(12, 10, 12, 11)
        background_layout.setSpacing(7)
        background_layout.addWidget(self._section_title("Background Reference"))
        self.use_background.setText("Subtract background")
        background_layout.addWidget(self.use_background)
        self.background_status.setProperty("role", "field")
        self.background_status.setWordWrap(True)
        background_layout.addWidget(self.background_status)
        self.background_settings_button = QPushButton("Background...")
        self.background_settings_button.clicked.connect(self._show_background_dialog)
        background_layout.addWidget(self.background_settings_button)
        control_column.addWidget(scan_card, 1)
        control_column.addWidget(background_card)

        plot_card = QFrame()
        plot_card.setObjectName("plotCard")
        plot_layout = QVBoxLayout(plot_card)
        plot_layout.setContentsMargins(14, 13, 14, 12)
        plot_layout.setSpacing(8)
        plot_layout.addWidget(self._section_title("Phase - Energy"))
        plot_layout.addWidget(self.plot, 2)
        diagnostic_header = QHBoxLayout()
        diagnostic_header.setContentsMargins(0, 0, 0, 0)
        diagnostic_header.addWidget(
            self._section_title(f"{self.diagnostics['flag_element']} Image / Energy Spectrum")
        )
        diagnostic_header.addStretch(1)
        diagnostic_header.addWidget(self.spectrum_summary)
        plot_layout.addLayout(diagnostic_header)
        plot_layout.addWidget(self.image_plot, 2)
        plot_layout.addWidget(self.result)
        workspace.addLayout(control_column)
        workspace.addWidget(plot_card, 1)
        root.addLayout(workspace, 1)
        self.setCentralWidget(body)
        self.thread = None
        self.points_data = []
        self.combo.currentTextChanged.connect(
            lambda text: self.status_panel.set_item("device", text or "No LLRF")
        )
        self.phase_mode.currentIndexChanged.connect(self._sync_phase_mode_labels)
        self.auto_tune_settings_button.clicked.connect(self._show_auto_tune_settings)
        self._sync_phase_mode_labels()
        self._apply_theme()
        self._redraw_plot()
        self._draw_diagnostic_placeholders()
        self._build_background_dialog()
        self._load_latest_background(silent=True)

        for widget in (self.low, self.high):
            widget.setSuffix(" deg")
        self.settle.setSuffix(" s")
        self.measurement_interval.setSuffix(" s")
        for widget in (self.auto_tune_min, self.auto_tune_max, self.auto_tune_max_offset):
            widget.setSuffix(" MeV")
        self.auto_tune_settle.setSuffix(" s")
        self.auto_tune_frame_interval.setSuffix(" s")
        self.auto_tune_center_tolerance.setSuffix(" mm")
        for widget in (self.tracking, self.fallback):
            widget.setSuffix(" MeV")

    @staticmethod
    def _spin(value, low, high):
        spin = QDoubleSpinBox()
        spin.setRange(low, high)
        spin.setDecimals(2)
        spin.setValue(value)
        return spin

    @staticmethod
    def _section_title(text):
        label = QLabel(text)
        label.setObjectName("cardTitle")
        return label

    @staticmethod
    def _separator():
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Plain)
        return line

    def _sync_phase_mode_labels(self):
        relative = self.phase_mode.currentData() == "relative"
        self.low_label.setText("Low offset" if relative else "Start phase")
        self.high_label.setText("High offset" if relative else "Stop phase")

    def _build_auto_tune_dialog(self):
        self.auto_tune_settings_dialog = QDialog(self)
        self.auto_tune_settings_dialog.setObjectName("energySpectrumDialog")
        self.auto_tune_settings_dialog.setWindowTitle("Energy Match Settings")
        self.auto_tune_settings_dialog.setModal(True)
        self.auto_tune_settings_dialog.resize(560, 680)
        layout = QVBoxLayout(self.auto_tune_settings_dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        def add_group(title, fields):
            group = QGroupBox("", self.auto_tune_settings_dialog)
            group.setObjectName("dialogCard")
            grid = QGridLayout(group)
            grid.setContentsMargins(10, 9, 10, 10)
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(7)
            title_label = QLabel(title, group)
            title_label.setObjectName("dialogCardTitle")
            grid.addWidget(title_label, 0, 0, 1, 2)
            for row, (text, widget) in enumerate(fields, start=1):
                grid.addWidget(QLabel(text, group), row, 0)
                grid.addWidget(widget, row, 1)
            grid.setColumnStretch(1, 1)
            layout.addWidget(group)

        add_group(
            "Search",
            (
                ("Minimum", self.auto_tune_min),
                ("Maximum", self.auto_tune_max),
                ("Reacquire points", self.auto_tune_coarse_steps),
                ("Settle time", self.auto_tune_settle),
            ),
        )
        add_group(
            "Sampling",
            (
                ("Fine/center frames", self.auto_tune_frame_samples),
                ("Minimum valid frames", self.auto_tune_min_valid_frames),
                ("Verification frames", self.auto_tune_verification_samples),
                ("Verification minimum", self.auto_tune_verification_min_valid),
                ("Frame gap", self.auto_tune_frame_interval),
            ),
        )
        add_group(
            "Center Tracking",
            (
                ("Center tolerance", self.auto_tune_center_tolerance),
                ("Maximum iterations", self.auto_tune_fine_steps),
                ("Maximum correction", self.auto_tune_max_offset),
                ("Profile fit", self.auto_tune_fit_method),
            ),
        )
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.RestoreDefaults,
            parent=self.auto_tune_settings_dialog,
        )
        buttons.button(QDialogButtonBox.Ok).setText("Apply")
        buttons.accepted.connect(self.auto_tune_settings_dialog.accept)
        buttons.rejected.connect(self.auto_tune_settings_dialog.reject)
        buttons.button(QDialogButtonBox.RestoreDefaults).clicked.connect(
            self._reset_auto_tune_settings
        )
        layout.addWidget(buttons)

    def _auto_tune_settings_values(self):
        return {
            "minimum": self.auto_tune_min.value(),
            "maximum": self.auto_tune_max.value(),
            "reacquire_steps": self.auto_tune_coarse_steps.value(),
            "max_iterations": self.auto_tune_fine_steps.value(),
            "settle_time_s": self.auto_tune_settle.value(),
            "frame_samples": self.auto_tune_frame_samples.value(),
            "min_valid_frames": self.auto_tune_min_valid_frames.value(),
            "verification_frame_samples": self.auto_tune_verification_samples.value(),
            "verification_min_valid_frames": self.auto_tune_verification_min_valid.value(),
            "frame_interval_s": self.auto_tune_frame_interval.value(),
            "center_tolerance_mm": self.auto_tune_center_tolerance.value(),
            "max_correction_step_mev": self.auto_tune_max_offset.value(),
            "profile_fit_method": self.auto_tune_fit_method.currentText(),
        }

    def _set_auto_tune_settings_values(self, values):
        controls = (
            (self.auto_tune_min, "minimum"),
            (self.auto_tune_max, "maximum"),
            (self.auto_tune_coarse_steps, "reacquire_steps"),
            (self.auto_tune_fine_steps, "max_iterations"),
            (self.auto_tune_settle, "settle_time_s"),
            (self.auto_tune_frame_samples, "frame_samples"),
            (self.auto_tune_min_valid_frames, "min_valid_frames"),
            (self.auto_tune_verification_samples, "verification_frame_samples"),
            (self.auto_tune_verification_min_valid, "verification_min_valid_frames"),
            (self.auto_tune_frame_interval, "frame_interval_s"),
            (self.auto_tune_center_tolerance, "center_tolerance_mm"),
            (self.auto_tune_max_offset, "max_correction_step_mev"),
        )
        for widget, key in controls:
            widget.setValue(values[key])
        fit_index = self.auto_tune_fit_method.findText(str(values["profile_fit_method"]))
        self.auto_tune_fit_method.setCurrentIndex(max(fit_index, 0))

    def _reset_auto_tune_settings(self):
        self._set_auto_tune_settings_values(self._auto_tune_default_values)

    def _validate_auto_tune_settings(self):
        values = self._auto_tune_settings_values()
        if values["minimum"] >= values["maximum"]:
            raise ValueError("Energy Match minimum energy must be less than maximum energy.")
        if values["min_valid_frames"] > values["frame_samples"]:
            raise ValueError("Minimum valid frames cannot exceed Fine/center frames.")
        if values["verification_min_valid_frames"] > values["verification_frame_samples"]:
            raise ValueError("Verification minimum cannot exceed verification frames.")

    def _show_auto_tune_settings(self):
        if self.thread is not None:
            return
        previous = self._auto_tune_settings_values()
        if self.auto_tune_settings_dialog.exec_() != QDialog.Accepted:
            self._set_auto_tune_settings_values(previous)
            return
        try:
            self._validate_auto_tune_settings()
        except ValueError as exc:
            QMessageBox.warning(self, "Energy Match Settings", str(exc))
            self._set_auto_tune_settings_values(previous)
            return
        self._update_auto_tune_settings_summary()

    def _update_auto_tune_settings_summary(self):
        self.auto_tune_settings_summary.setText(
            f"{self.auto_tune_min.value():g}-{self.auto_tune_max.value():g} MeV · "
            f"{self.auto_tune_coarse_steps.value()} reacquire pts · "
            f"step <= {self.auto_tune_max_offset.value():g} MeV · "
            f"tol {self.auto_tune_center_tolerance.value():g} mm"
        )

    def _resolved_auto_tune_settings(self, geometry):
        scan = self._auto_tune_settings_values()
        configured_scan = dict(self.auto_tune_config["search"])
        scan["min"] = scan.pop("minimum")
        scan["max"] = scan.pop("maximum")
        scan["restore_initial_on_failure"] = bool(
            configured_scan["restore_initial_on_failure"]
        )
        scan["pixel_width_mm"] = float(geometry.pixel_width_mm)
        scan["x_reference_mm"] = float(self.diagnostics.get("x_reference_mm", 0.0))
        scan["design_eta_m"] = float(self.diagnostics["design_eta_m"])
        scan["target_x_pixel"] = reference_x_pixel(
            scan["x_reference_mm"], geometry.shape[0], geometry.pixel_width_mm
        )
        return scan

    def _apply_theme(self):
        palette = PALETTES.get(self.current_theme, PALETTES["dark"])
        self.setStyleSheet(build_style(palette))
        self.status_panel.apply_theme(palette)
        self.status_panel.setFixedHeight(self.status_panel.sizeHint().height())
        for widget in (self.plot, self.image_plot):
            figure = widget.fig
            axes = widget.axes
            figure.patch.set_facecolor(palette["plot"])
            axes.set_facecolor(palette["plot"])
            axes.tick_params(colors=palette["muted"])
            for spine in axes.spines.values():
                spine.set_color(palette["border"])
            axes.xaxis.label.set_color(palette["muted"])
            axes.yaxis.label.set_color(palette["muted"])
        self._update_theme_button()

    def _set_scan_status(self, text, tooltip=None):
        normalized = str(text).strip().lower()
        if normalized in {"running", "preparing", "done", "success"} or normalized.startswith("point "):
            tone = "success"
        elif any(
            marker in normalized
            for marker in ("fail", "invalid", "stopping", "restoring", "cancel", "unavailable")
        ):
            tone = "warning"
        else:
            tone = "subtle"
        self.status_panel.set_item("scan", str(text), tone, tooltip)

    def _update_theme_button(self):
        if self.current_theme == "dark":
            self.theme_toggle_button.setText("\u2600")
            self.theme_toggle_button.setToolTip("Switch to light theme.")
        else:
            self.theme_toggle_button.setText("\u263D")
            self.theme_toggle_button.setToolTip("Switch to dark theme.")

    def _toggle_theme(self):
        self.current_theme = "light" if self.current_theme == "dark" else "dark"
        self._redraw_plot(self.last_fit, self.last_initial_phase)
        if self.latest_diagnostic is None:
            self._draw_diagnostic_placeholders()
        else:
            image, reference_energy = self.latest_diagnostic
            self._update_diagnostics(image, reference_energy)
        self._refresh_background_preview()

    def _style_axes(self, axes, xlabel, ylabel):
        palette = PALETTES.get(self.current_theme, PALETTES["dark"])
        axes.set_facecolor(palette["plot"])
        axes.set_xlabel(xlabel)
        axes.set_ylabel(ylabel)
        axes.tick_params(colors=palette["muted"], labelsize=8)
        axes.xaxis.label.set_color(palette["muted"])
        axes.yaxis.label.set_color(palette["muted"])
        axes.grid(True, color=palette["grid"], linewidth=0.6, alpha=0.55)
        for spine in axes.spines.values():
            spine.set_color(palette["border"])

    def _draw_diagnostic_placeholders(self):
        palette = PALETTES.get(self.current_theme, PALETTES["dark"])
        self._remove_energy_axis()
        self.image_plot.axes.clear()
        self._style_axes(self.image_plot.axes, "x (mm)", "y (mm)")
        self.image_plot.axes.text(
            0.5, 0.5, "Waiting for matched beam",
            ha="center", va="center", color=palette["muted"],
            transform=self.image_plot.axes.transAxes,
        )
        self.image_plot.fig.tight_layout()
        self.image_plot.canvas.draw_idle()
        self.spectrum_summary.setText("Energy N/A  |  Spread N/A  |  Fit waiting")

    def _remove_energy_axis(self):
        if self.energy_axis is not None:
            self.energy_axis.remove()
            self.energy_axis = None

    def _update_diagnostics(self, raw_image, reference_energy_mev):
        palette = PALETTES.get(self.current_theme, PALETTES["dark"])
        image = np.asarray(raw_image, dtype=float)
        self.latest_diagnostic = (image.copy(), float(reference_energy_mev))
        geometry = resolve_element_image_geometry(
            self.context,
            self.diagnostics["flag_element"],
            self.context.control_backend.name,
        )
        width_mm = geometry.shape[0] * geometry.pixel_width_mm
        height_mm = geometry.shape[1] * geometry.pixel_width_mm
        extent = (-width_mm / 2, width_mm / 2, -height_mm / 2, height_mm / 2)

        self._remove_energy_axis()
        self.image_plot.axes.clear()
        self._style_axes(self.image_plot.axes, "x (mm)", "y (mm)")
        self.image_plot.axes.grid(False)
        self.image_plot.axes.imshow(
            image, origin="lower", cmap="viridis", extent=extent, aspect="auto",
        )
        analysis_image = image.copy()
        if self.use_background.isChecked() and self.background_image is not None:
            analysis_image -= self.background_image
            analysis_image[analysis_image < 0] = 0
        try:
            projection = project_image_profiles(analysis_image, geometry.pixel_width_mm)
            profile_fit = fit_projection_profile(
                projection.x_mm,
                projection.density_x,
                "Gauss fit",
            )
            eta_m = float(self.diagnostics["design_eta_m"])
            x_reference_mm = float(self.diagnostics.get("x_reference_mm", 0.0))
            energy_center = float(reference_energy_mev) * (
                1.0 + (profile_fit.center_mm - x_reference_mm) * 1e-3 / eta_m
            )
            spread_percent = abs(
                profile_fit.sigma_mm * 1e-3 / eta_m
                * float(reference_energy_mev) / energy_center
            ) * 100.0
            projection_height = height_mm * 0.25
            projection_base = -height_mm / 2 + height_mm * 0.03
            self.image_plot.axes.plot(
                projection.x_mm,
                projection_base + profile_fit.normalized_density * projection_height,
                "--", color=palette["trace"], linewidth=1.3, label="projection",
            )
            self.image_plot.axes.plot(
                projection.x_mm,
                projection_base + profile_fit.fitted_density * projection_height,
                "-", color=palette["accent"], linewidth=1.3,
                label=profile_fit.method,
            )
            self.energy_axis = self.image_plot.axes.secondary_xaxis(
                "top",
                functions=(
                    lambda x: float(reference_energy_mev)
                    * (1.0 + (x - x_reference_mm) * 1e-3 / eta_m),
                    lambda energy: x_reference_mm
                    + (energy / float(reference_energy_mev) - 1.0) * eta_m * 1e3,
                ),
            )
            self.energy_axis.set_xlabel("Energy (MeV)", color=palette["muted"])
            self.energy_axis.tick_params(colors=palette["muted"], labelsize=8)
            legend = self.image_plot.axes.legend(
                frameon=False, fontsize=8, loc="upper right",
            )
            for text in legend.get_texts():
                text.set_color(palette["text"])
            fit_quality = (
                "N/A" if profile_fit.r_squared is None
                else f"{profile_fit.r_squared:.3f}"
            )
            self.spectrum_summary.setText(
                f"Energy {energy_center:.2f} MeV  |  "
                f"Spread {spread_percent:.3f}%  |  R2 {fit_quality}"
            )
        except (KeyError, SpectrumProfileError, TypeError, ValueError, ZeroDivisionError) as exc:
            self.spectrum_summary.setText("Energy N/A  |  Spread N/A  |  Fit unavailable")
            self.image_plot.axes.text(
                0.02, 0.04, f"Spectrum unavailable: {exc}",
                ha="left", va="bottom", color=palette["muted"],
                transform=self.image_plot.axes.transAxes,
            )
        self.image_plot.fig.tight_layout()
        self.image_plot.canvas.draw_idle()

    def _redraw_plot(self, fit=None, initial_phase=None):
        palette = PALETTES.get(self.current_theme, PALETTES["dark"])
        axes = self.plot.axes
        axes.clear()
        valid = [
            point for point in self.points_data
            if point.get("matched_energy_mev") is not None
        ]
        if valid:
            x_values = [
                point["requested_phase_unwrapped_deg"]
                if self.active_phase_mode == "absolute"
                else point["offset_deg"]
                for point in valid
            ]
            axes.plot(
                x_values,
                [point["matched_energy_mev"] for point in valid],
                "o-", color=palette["trace"], linewidth=1.6, markersize=5,
            )
        failed = [point for point in self.points_data if point.get("status") == "failed"]
        if failed:
            x_failed = [
                point["requested_phase_unwrapped_deg"]
                if self.active_phase_mode == "absolute"
                else point["offset_deg"]
                for point in failed
            ]
            baseline = (
                min(point["matched_energy_mev"] for point in valid) if valid else 0.0
            )
            axes.plot(
                x_failed, [baseline] * len(x_failed), "x", color=palette["warning"],
                markersize=7, markeredgewidth=1.5, label="match failed",
            )
        if fit and initial_phase is not None and self.points_data:
            if self.active_phase_mode == "absolute":
                low = min(point["requested_phase_unwrapped_deg"] for point in self.points_data)
                high = max(point["requested_phase_unwrapped_deg"] for point in self.points_data)
                x_values = np.linspace(low, high, 240)
                phase = x_values
            else:
                low = min(point["offset_deg"] for point in self.points_data)
                high = max(point["offset_deg"] for point in self.points_data)
                x_values = np.linspace(low, high, 240)
                phase = float(initial_phase) + x_values
            energy = float(fit["baseline_energy_mev"]) + float(fit["amplitude_mev"]) * np.cos(
                np.deg2rad(phase - float(fit["crest_phase_unwrapped_deg"]))
            )
            axes.plot(x_values, energy, "--", color=palette["accent"], linewidth=1.4)
            crest = float(fit["crest_phase_unwrapped_deg"])
            crest_x = crest if self.active_phase_mode == "absolute" else crest - float(initial_phase)
            if crest_x < low or crest_x > high:
                axes.text(
                    0.02, 0.96, "Crest outside scan range", ha="left", va="top",
                    color=palette["warning"], transform=axes.transAxes,
                )
            elif min(abs(crest_x - low), abs(high - crest_x)) <= max((high - low) * 0.05, 1.0):
                axes.text(
                    0.02, 0.96, "Crest near scan boundary", ha="left", va="top",
                    color=palette["warning"], transform=axes.transAxes,
                )
        axes.set_xlabel(
            "Phase (deg)" if self.active_phase_mode == "absolute" else "Phase offset (deg)"
        )
        axes.set_ylabel("Matched energy (MeV)")
        axes.grid(True, color=palette["grid"], linewidth=0.7, alpha=0.7)
        if failed:
            legend = axes.legend(frameon=False, fontsize=8, loc="best")
            for text in legend.get_texts():
                text.set_color(palette["text"])
        self._apply_theme()
        self.plot.fig.tight_layout()
        self.plot.canvas.draw_idle()

    def _build_background_dialog(self):
        self.background_dialog = QDialog(self)
        self.background_dialog.setObjectName("energySpectrumDialog")
        self.background_dialog.setWindowTitle("Background Reference")
        self.background_dialog.setModal(True)
        self.background_dialog.resize(760, 620)
        layout = QVBoxLayout(self.background_dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        self.background_plot = MplWidget(self.background_dialog)
        self.background_plot.setObjectName("background_plot")
        self.background_plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.background_plot.setMinimumHeight(400)
        layout.addWidget(self.background_plot, 1)
        controls = QGroupBox("", self.background_dialog)
        controls.setObjectName("dialogCard")
        grid = QGridLayout(controls)
        grid.setContentsMargins(10, 9, 10, 10)
        grid.setHorizontalSpacing(7)
        grid.setVerticalSpacing(7)
        controls_title = self._section_title("Background Controls")
        controls_title.setObjectName("dialogCardTitle")
        grid.addWidget(controls_title, 0, 0, 1, 5)
        self.background_samples_spin = QSpinBox()
        self.background_samples_spin.setRange(1, 100)
        self.background_samples_spin.setValue(3)
        self.background_interval_spin = self._spin(1.0, 0, 60)
        self.background_interval_spin.setSuffix(" s")
        grid.addWidget(QLabel("Samples"), 1, 0)
        grid.addWidget(self.background_samples_spin, 1, 1)
        grid.addWidget(QLabel("Interval"), 1, 2)
        grid.addWidget(self.background_interval_spin, 1, 3)
        grid.addWidget(self.capture_background_button, 1, 4)
        self.save_background_button = QPushButton("Save As")
        self.load_latest_background_button = QPushButton("Load Latest")
        background_action_layout = QHBoxLayout()
        background_action_layout.setContentsMargins(0, 0, 0, 0)
        background_action_layout.setSpacing(7)
        background_action_layout.addWidget(self.save_background_button, 1)
        background_action_layout.addWidget(self.load_background_button, 1)
        background_action_layout.addWidget(self.load_latest_background_button, 1)
        grid.addLayout(background_action_layout, 2, 0, 1, 5)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        layout.addWidget(controls)
        self.background_path_label = QLabel("No background loaded")
        self.background_path_label.setProperty("role", "field")
        self.background_path_label.setWordWrap(True)
        layout.addWidget(self.background_path_label)
        close_buttons = QDialogButtonBox(QDialogButtonBox.Close)
        for button in close_buttons.buttons():
            button.setProperty("dialogAction", True)
        close_buttons.rejected.connect(self.background_dialog.reject)
        layout.addWidget(close_buttons)
        self.save_background_button.clicked.connect(self.save_background_file)
        self.load_latest_background_button.clicked.connect(self._load_latest_background)
        self._refresh_background_preview()

    def _refresh_background_preview(self):
        if not hasattr(self, "background_plot"):
            return
        palette = PALETTES.get(self.current_theme, PALETTES["dark"])
        axes = self.background_plot.axes
        axes.clear()
        axes.set_facecolor(palette["plot"])
        geometry = resolve_element_image_geometry(
            self.context,
            self.diagnostics["flag_element"],
            self.context.control_backend.name,
        )
        width_mm = geometry.shape[0] * geometry.pixel_width_mm
        height_mm = geometry.shape[1] * geometry.pixel_width_mm
        if self.background_image is not None:
            axes.imshow(
                self.background_image,
                origin="lower",
                cmap="viridis",
                extent=(-width_mm / 2, width_mm / 2, -height_mm / 2, height_mm / 2),
                aspect="auto",
            )
        else:
            axes.text(0.5, 0.5, "No background", ha="center", va="center", color=palette["muted"], transform=axes.transAxes)
        axes.set_title("Background Preview", color=palette["text"], fontsize=11, fontweight="bold", loc="left")
        axes.set_xlabel("x (mm)", color=palette["muted"])
        axes.set_ylabel("y (mm)", color=palette["muted"])
        axes.tick_params(colors=palette["muted"])
        for spine in axes.spines.values():
            spine.set_color(palette["border"])
        self.background_plot.fig.patch.set_facecolor(palette["plot"])
        self.background_plot.fig.tight_layout()
        self.background_plot.canvas.draw_idle()

    def _update_background_status(self):
        if self.background_image is None:
            summary = "Background: None"
            detail = "No background loaded"
        else:
            created = str(self.background_metadata.get("created_at", "unknown time"))
            filename = self.background_image_path.name if self.background_image_path else "in memory"
            summary = f"Background: {filename} · {created}"
            detail = str(self.background_image_path or "Sampled background is not saved")
        self.background_status.setText(summary)
        if hasattr(self, "background_path_label"):
            self.background_path_label.setText(detail)

    def _show_background_dialog(self):
        if self.thread is not None:
            return
        self._refresh_background_preview()
        self._update_background_status()
        self.background_dialog.exec_()

    def _load_latest_background(self, _checked=False, *, silent=False):
        path = self.runtime_paths["background_image_path"]
        if not path.is_file():
            if not silent:
                QMessageBox.warning(self, "Background", f"No latest background exists at {path}.")
            self._update_background_status()
            return False
        try:
            geometry = resolve_element_image_geometry(self.context, self.diagnostics["flag_element"], self.context.control_backend.name)
            image, metadata = load_background(path, self.runtime_paths["background_metadata_path"], expected_shape=(geometry.shape[1], geometry.shape[0]))
            self._set_background(image, metadata, path)
        except BackgroundStoreError as exc:
            if not silent:
                QMessageBox.warning(self, "Background", str(exc))
            return False
        return True

    def _set_background(self, image, metadata, path):
        self.background_image = image
        self.background_metadata = dict(metadata)
        self.background_image_path = Path(path)
        self._update_background_status()
        self._refresh_background_preview()

    def save_background_file(self):
        if self.background_image is None:
            QMessageBox.warning(self, "Background", "No background image is available to save.")
            return
        default = self.runtime_paths["runs_dir"] / f"background_{datetime.now().astimezone():%Y%m%d_%H%M%S}.npy"
        path, _ = QFileDialog.getSaveFileName(self, "Save Background Image", str(default), "NumPy image (*.npy)")
        if not path:
            return
        image_path = Path(path).with_suffix(".npy")
        metadata = dict(self.background_metadata, source="save_as", created_at=datetime.now().astimezone().isoformat(timespec="seconds"))
        save_background(self.background_image, image_path, image_path.with_suffix(".json"), metadata)
        self._set_background(self.background_image, metadata, image_path)

    def start_scan(self):
        if not self.targets or self.thread is not None: return
        target = self.targets[self.combo.currentIndex()]
        energy_element = str(self.config["energy_element"])
        if self.low.value() >= self.high.value() or self.high.value() - self.low.value() > 360:
            self._set_scan_status("Invalid phase range")
            return
        if self.fallback.value() < self.tracking.value():
            self._set_scan_status("Invalid energy windows", "Fallback window must cover tracking window.")
            return
        try:
            self._validate_auto_tune_settings()
        except ValueError as exc:
            self._set_scan_status("Invalid Energy Match settings", str(exc))
            QMessageBox.warning(self, "Energy Match Settings", str(exc))
            return
        scan_config = dict(self.config["scan"])
        tracking_config = dict(scan_config["energy_tracking"])
        sampling_config = dict(scan_config["point_measurement"])
        auto_tune_settings = self._resolved_auto_tune_settings(
            resolve_element_image_geometry(
                self.context,
                self.diagnostics["flag_element"],
                self.context.control_backend.name,
            )
        )
        phase = PhaseScanSettings(
            low_offset_deg=float(self.low.value()),
            high_offset_deg=float(self.high.value()),
            points=self.points.value(),
            phase_settle_time_s=float(self.settle.value()),
            tracking_half_window_mev=float(self.tracking.value()),
            fallback_half_window_mev=float(self.fallback.value()),
            max_consecutive_failures=int(tracking_config["max_consecutive_failures"]),
            energy_low_mev=float(auto_tune_settings["min"]),
            energy_high_mev=float(auto_tune_settings["max"]),
            phase_mode=str(self.phase_mode.currentData()),
        )
        image_pv = PV(resolve_channel(
            self.context,
            self.diagnostics["flag_element"],
            self.diagnostics["flag_image_channel"],
        ))
        geometry = resolve_element_image_geometry(self.context, self.diagnostics["flag_element"], self.context.control_backend.name)
        point_measurement = {
            "samples": self.measurement_samples.value(),
            "interval_s": self.measurement_interval.value(),
            "min_valid_samples": min(
                int(sampling_config["min_valid_samples"]),
                self.measurement_samples.value(),
            ),
        }
        energy_pv = resolve_write_target(
            self.context,
            energy_element,
            logical_channel=self.config["energy_set_channel"],
            unit="MeV",
        ).pv_name
        self.points_data = []
        self.active_phase_mode = phase.phase_mode
        self.latest_diagnostic = None
        self.last_fit = None
        self.last_initial_phase = None
        self._draw_diagnostic_placeholders()
        self.progress.setRange(0, phase.points)
        self.progress.setValue(0)
        self.progress.setFormat(f"0 / {phase.points}")
        self.progress.setTextVisible(True)
        self.status_panel.set_item("restore", "Pending", "subtle")
        background_metadata = dict(self.background_metadata)
        background_metadata["path"] = str(self.runtime_paths["background_image_path"]) if self.background_image is not None else None
        background_metadata["shape"] = list(self.background_image.shape) if self.background_image is not None else None
        background_metadata["phase_mode"] = phase.phase_mode
        background_metadata["point_measurement"] = dict(point_measurement)
        background_metadata["energy_match_settings"] = dict(auto_tune_settings)
        self.thread = ScanThread(context=self.context, target=target, settings=phase, scan=auto_tune_settings, image_pv=image_pv, image_shape=geometry.shape, pixel_width_mm=geometry.pixel_width_mm, phase_pv=target.pv_name, energy_pv=energy_pv, remove_bg=self.use_background.isChecked() and self.background_image is not None, bg_image=self.background_image, background_metadata=background_metadata, point_measurement=point_measurement)
        for widget in (self.combo, self.phase_mode, self.low, self.high, self.points, self.settle, self.tracking, self.fallback, self.measurement_samples, self.measurement_interval, self.auto_tune_settings_button, self.use_background, self.background_settings_button):
            widget.setEnabled(False)
        self.thread.point.connect(self._point)
        self.thread.diagnostic.connect(self._update_diagnostics)
        self.thread.state.connect(self._set_scan_status)
        self.thread.match_state.connect(self._match_state)
        self.thread.progress.connect(self._update_progress)
        self.thread.done.connect(self.finished)
        self._set_scan_status("Preparing")
        self.thread.start()
        self.start.setEnabled(False)
        self.stop.setEnabled(True)

    def _point(self, point):
        self.points_data.append(point)
        self.points_data.sort(key=lambda item: int(item.get("index", 0)))
        self._redraw_plot()
        point_number = int(point.get("acquisition_index", point.get("index", 0))) + 1
        self._set_scan_status(
            f"Point {point_number}/{self.points.value()} | {point.get('status')}",
            f"Search {point.get('search_low_mev', '')}..{point.get('search_high_mev', '')} MeV",
        )

    def _match_state(self, payload):
        stage = str(payload.get("stage", "match")).replace("_", " ").title()
        energy = payload.get("energy_mev")
        detail = None if energy is None else f"Coordinated energy {float(energy):.2f} MeV"
        self._set_scan_status(f"Energy Match: {stage}", detail)

    def _update_progress(self, completed, total):
        self.progress.setRange(0, int(total))
        self.progress.setValue(int(completed))
        self.progress.setFormat(f"{int(completed)} / {int(total)}")

    def stop_scan(self):
        if self.thread is not None:
            self.thread.requestInterruption()
            self._set_scan_status("Stopping")

    def finished(self, result):
        self._set_scan_status(str(result.get("status", "FAILED")))
        self.stop.setEnabled(False); self.start.setEnabled(True); self.thread = None
        fit = result.get("fit") or {}
        self.last_fit = fit or None
        self.last_initial_phase = result.get("initial_phase_deg")
        self._redraw_plot(self.last_fit, self.last_initial_phase)
        self.result.setText(
            f"Restore: phase {'OK' if result.get('phase_restored') else 'FAILED'}  |  "
            f"energy {'OK' if result.get('energy_restored') else 'FAILED'}"
            + (f"  |  crest {float(fit['crest_phase_command_deg']):+.2f} deg  |  "
               f"amplitude {float(fit['amplitude_mev']):.2f} MeV  |  "
               f"RMSE {float(fit['rmse_mev']):.3f} MeV  |  R2 {float(fit['r_squared']):.3f}"
               if fit else "  |  fit unavailable")
            + (f"\nCSV: {result.get('csv_path')}\nJSON: {result.get('json_path')}"
               if result.get("csv_path") or result.get("json_path") else "")
        )
        phase_restored = bool(result.get("phase_restored"))
        energy_restored = bool(result.get("energy_restored"))
        if phase_restored and energy_restored:
            self.status_panel.set_item("restore", "Phase + energy OK", "success")
        elif phase_restored or energy_restored:
            self.status_panel.set_item("restore", "Partial failure", "warning")
        else:
            self.status_panel.set_item("restore", "Failed", "warning")
        for widget in (self.combo, self.phase_mode, self.low, self.high, self.points, self.settle, self.tracking, self.fallback, self.measurement_samples, self.measurement_interval, self.auto_tune_settings_button, self.use_background, self.background_settings_button):
            widget.setEnabled(True)

    def closeEvent(self, event):
        if self.thread is not None and self.thread.isRunning():
            self.thread.requestInterruption()
            if not self.thread.wait(15000):
                self._set_scan_status("Restoring", "Machine state restore is still in progress; close deferred.")
                event.ignore()
                return
        super().closeEvent(event)

    def _background_toggled(self, enabled):
        if enabled and self.background_image is None:
            self.use_background.blockSignals(True)
            self.use_background.setChecked(False)
            self.use_background.blockSignals(False)
        self._update_background_status()

    def load_background_file(self):
        image_path, _ = QFileDialog.getOpenFileName(self, "Load background", str(self.runtime_paths["background_image_path"]), "NumPy image (*.npy)")
        if not image_path:
            return
        try:
            geometry = resolve_element_image_geometry(self.context, self.diagnostics["flag_element"], self.context.control_backend.name)
            image, metadata = load_background(
                Path(image_path), Path(image_path).with_suffix(".json"),
                expected_shape=(geometry.shape[1], geometry.shape[0]),
            )
            self._set_background(image, metadata, Path(image_path))
            self.use_background.setChecked(True)
        except (BackgroundStoreError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "Background", str(exc))

    def capture_background(self):
        try:
            image_pv = PV(resolve_channel(
                self.context,
                self.diagnostics["flag_element"],
                self.diagnostics["flag_image_channel"],
            ))
            geometry = resolve_element_image_geometry(self.context, self.diagnostics["flag_element"], self.context.control_backend.name)
            acquisition = RFImageAcquisition(
                image_pv,
                geometry.shape,
                geometry.pixel_width_mm,
            )
            images = []
            for index in range(self.background_samples_spin.value()):
                if index and self.background_interval_spin.value() > 0:
                    time.sleep(self.background_interval_spin.value())
                images.append(acquisition.read_raw())
            image = np.mean(images, axis=0)
            metadata = {
                "schema_version": "rf_phase_scan_background_v1",
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "machine_id": self.context.machine.id,
                "backend": self.context.control_backend.name,
                "station_id": "eny",
                "flag_element": self.diagnostics["flag_element"],
                "flag_pv": image_pv.pvname,
                "shape": list(image.shape),
                "pixel_width_mm": geometry.pixel_width_mm,
                "sample_count": len(images),
                "sample_interval_s": self.background_interval_spin.value(),
                "source": "sampled_latest",
            }
            path, metadata_path = save_background(image, self.runtime_paths["background_image_path"], self.runtime_paths["background_metadata_path"], metadata)
            image, metadata = load_background(path, metadata_path, expected_shape=(geometry.shape[1], geometry.shape[0]))
            self._set_background(image, metadata, path)
            self.use_background.setChecked(True)
        except (BackgroundStoreError, OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Background", str(exc))


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = RFPhaseScanWindow()
    window.show()
    sys.exit(app.exec_())
