from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Any

import epics
import numpy as np
from epics import PV, caget
from PyQt5.QtCore import QThread, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from half_linac.src.shared.beam_diagnostics import ROIControl
from half_linac.src.shared.beam_diagnostics.background_store import (
    BackgroundStoreError,
    load_background,
    save_background,
)
from half_linac.src.shared.machine_profile.app_runtime import (
    resolve_app_runtime_paths,
)

from .model import DEVICES
from .mplwidget import MplWidget
from .profile_runtime import (
    TimingEnergyScanConfig,
    TimingGroup,
    TimingRuntime,
)
from .scan import TimingEnergyScanResult, TimingEnergyScanSettings
from .scan_executor import (
    TimingEnergyScanCallbacks,
    TimingEnergyScanExecutor,
    TimingEnergyScanRequest,
)


class ImageReadWorker(QThread):
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, pv_name: str, shape: tuple[int, int], flip_y: bool, parent=None):
        super().__init__(parent)
        self.pv_name = pv_name
        self.shape = tuple(shape)
        self.flip_y = bool(flip_y)

    def run(self) -> None:
        pv = None
        try:
            epics.ca.use_initial_context()
            pv = PV(self.pv_name, auto_monitor=False)
            if not pv.wait_for_connection(timeout=3):
                raise RuntimeError("ENY image PV connection timed out.")
            raw = pv.get(timeout=5)
            if raw is None:
                raise RuntimeError("ENY image PV returned no data.")
            image = np.asarray(raw, dtype=float).reshape(
                self.shape[1],
                self.shape[0],
            )
            if self.flip_y:
                image = np.flipud(image)
            self.done.emit(image)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if pv is not None:
                try:
                    pv.disconnect()
                except Exception:
                    pass


class TimingScanWorker(QThread):
    point = pyqtSignal(object)
    diagnostic = pyqtSignal(object, float, object)
    state = pyqtSignal(str)
    progress = pyqtSignal(int, int)
    match_state = pyqtSignal(object)
    done = pyqtSignal(object)

    def __init__(self, request: TimingEnergyScanRequest, parent=None):
        super().__init__(parent)
        self.request = request
        self.executor: TimingEnergyScanExecutor | None = None

    def run(self) -> None:
        callbacks = TimingEnergyScanCallbacks(
            point=self.point.emit,
            diagnostic=self.diagnostic.emit,
            state=self.state.emit,
            progress=self.progress.emit,
            match_state=self.match_state.emit,
        )
        self.executor = TimingEnergyScanExecutor(
            self.request,
            cancel_requested=self.isInterruptionRequested,
            callbacks=callbacks,
        )
        self.done.emit(self.executor.run())


class ApplyBestWorker(QThread):
    done = pyqtSignal(object)

    def __init__(
        self,
        executor: TimingEnergyScanExecutor,
        result: TimingEnergyScanResult,
        parent=None,
    ):
        super().__init__(parent)
        self.executor = executor
        self.result = result

    def run(self) -> None:
        try:
            payload = self.executor.apply_best(self.result)
        except Exception as exc:
            payload = {"status": "APPLY_FAILED", "message": str(exc)}
        self.done.emit(payload)


class EnergyMatchSettingsDialog(QDialog):
    def __init__(
        self,
        config: TimingEnergyScanConfig,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Energy Match Settings")
        defaults = dict(config.energy_match_defaults)
        measurement = dict(defaults.get("measurement", {}))
        stages = dict(defaults.get("stages", {}))
        center = dict(stages.get("center_lock", {}))
        search = dict(config.energy_match.get("range", {}))

        self.after_energy_change = self._double(
            float(search.get("settle_time_s", 3.0)),
            0.0,
            60.0,
            " s",
        )
        self.min_fit_r_squared = self._double(
            float(measurement.get("min_fit_r_squared", 0.3)),
            0.0,
            1.0,
        )
        beam_presence = dict(measurement.get("beam_presence", {}))
        self.beam_sigma = self._double(
            float(beam_presence.get("sigma_threshold", 6.0)),
            0.1,
            100.0,
        )
        self.beam_area = QSpinBox(self)
        self.beam_area.setRange(1, 1000000)
        self.beam_area.setValue(int(beam_presence.get("min_area_px", 50)))
        self.center_trigger = self._double(
            float(center.get("center_lock_trigger_mm", 10.0)),
            0.01,
            1000.0,
            " mm",
        )
        self.center_tolerance = self._double(
            float(center.get("center_tolerance_mm", 0.2)),
            0.001,
            100.0,
            " mm",
        )
        self.max_iterations = QSpinBox(self)
        self.max_iterations.setRange(1, 100)
        self.max_iterations.setValue(int(center.get("max_iterations", 6)))
        self.max_correction = self._double(
            float(center.get("max_correction_step_mev", 20.0)),
            0.01,
            1000.0,
            " MeV",
        )

        form = QFormLayout()
        form.addRow("After energy change", self.after_energy_change)
        form.addRow("Minimum Gaussian R²", self.min_fit_r_squared)
        form.addRow("Beam threshold σ", self.beam_sigma)
        form.addRow("Minimum beam area", self.beam_area)
        form.addRow("Center-lock trigger", self.center_trigger)
        form.addRow("Final center tolerance", self.center_tolerance)
        form.addRow("Maximum iterations", self.max_iterations)
        form.addRow("Maximum correction step", self.max_correction)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel", self)
        save = QPushButton("Use Settings", self)
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self._accept)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons)

    def _double(
        self,
        value: float,
        low: float,
        high: float,
        suffix: str = "",
    ) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(self)
        spin.setRange(low, high)
        spin.setDecimals(3)
        spin.setValue(value)
        spin.setSuffix(suffix)
        return spin

    def _accept(self) -> None:
        if self.center_trigger.value() < self.center_tolerance.value():
            QMessageBox.warning(
                self,
                "Energy Match Settings",
                "Center-lock trigger cannot be smaller than final tolerance.",
            )
            return
        self.accept()

    def values(self) -> dict[str, Any]:
        return {
            "settle_time_s": self.after_energy_change.value(),
            "min_fit_r_squared": self.min_fit_r_squared.value(),
            "beam_presence_sigma": self.beam_sigma.value(),
            "beam_presence_min_area_px": self.beam_area.value(),
            "center_lock_trigger_mm": self.center_trigger.value(),
            "center_tolerance_mm": self.center_tolerance.value(),
            "max_iterations": self.max_iterations.value(),
            "max_correction_step_mev": self.max_correction.value(),
        }


class LinkedTimingEnergyScanDialog(QDialog):
    scanActiveChanged = pyqtSignal(bool)

    def __init__(
        self,
        runtime: TimingRuntime,
        group: TimingGroup,
        *,
        theme: str = "dark",
        parent=None,
    ) -> None:
        super().__init__(parent)
        if runtime.linked_energy_scan is None:
            raise ValueError("Linked timing-energy scan is not configured.")
        self.runtime = runtime
        self.group = group
        self.config = runtime.linked_energy_scan
        self.theme = theme
        self.points_data: list[dict[str, Any]] = []
        self.result: TimingEnergyScanResult | None = None
        self.worker: TimingScanWorker | None = None
        self.last_executor: TimingEnergyScanExecutor | None = None
        self.apply_worker: ApplyBestWorker | None = None
        self.image_worker: ImageReadWorker | None = None
        self._image_action = "preview"
        self._close_requested = False
        self.background_image = None
        self._latest_fit_center_mm: float | None = None
        self.background_metadata: dict[str, Any] = {}
        self.runtime_paths = resolve_app_runtime_paths(
            Path(__file__).resolve().parent,
            runtime.context,
        )
        self.background_path = (
            self.runtime_paths["latest_dir"] / "linked_scan_background.npy"
        )
        self.background_metadata_path = (
            self.runtime_paths["latest_dir"] / "linked_scan_background.json"
        )

        self.setWindowTitle(
            f"{group.element_id} · Linked Timing Energy Scan"
        )
        self.setModal(True)
        self.resize(1420, 900)
        self.setMinimumSize(1120, 720)
        self._build_ui()
        self._load_latest_background(silent=True)
        self._apply_plot_theme()

    @staticmethod
    def _double(
        value: float,
        low: float,
        high: float,
        *,
        decimals: int = 3,
        suffix: str = "",
    ) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(low, high)
        spin.setDecimals(decimals)
        spin.setValue(value)
        spin.setSuffix(suffix)
        return spin

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        heading = QHBoxLayout()
        title = QLabel(
            f"{self.group.element_id} · HV / LLRF / SSA / KLY",
            self,
        )
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        heading.addWidget(title)
        heading.addStretch(1)
        self.machine_state = QLabel("Machine state will be restored after scanning", self)
        heading.addWidget(self.machine_state)
        root.addLayout(heading)

        splitter = QSplitter(Qt.Horizontal, self)
        splitter.setChildrenCollapsible(False)
        left = QWidget(splitter)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 6, 0)
        left_layout.setSpacing(8)

        scan_box = QGroupBox("Linked timing scan", left)
        scan_form = QFormLayout(scan_box)
        cfg = self.config
        self.low = self._double(
            cfg.offset_low_us,
            -cfg.hard_limit_us,
            0.0,
            suffix=" μs",
        )
        self.high = self._double(
            cfg.offset_high_us,
            0.0,
            cfg.hard_limit_us,
            suffix=" μs",
        )
        self.step = self._double(
            cfg.offset_step_us,
            0.001,
            cfg.hard_limit_us,
            decimals=3,
            suffix=" μs",
        )
        self.timing_settle = self._double(
            cfg.timing_settle_time_s,
            0.0,
            60.0,
            suffix=" s",
        )
        self.samples = QSpinBox(scan_box)
        self.samples.setRange(1, 100)
        self.samples.setValue(cfg.samples_per_point)
        self.sample_interval = self._double(
            cfg.sample_interval_s,
            0.0,
            60.0,
            suffix=" s",
        )
        scan_form.addRow("Negative limit", self.low)
        scan_form.addRow("Positive limit", self.high)
        scan_form.addRow("Step", self.step)
        scan_form.addRow("After timing change", self.timing_settle)
        scan_form.addRow("Frames per point", self.samples)
        scan_form.addRow("Frame interval", self.sample_interval)
        limit = QLabel(
            f"Hard limit ±{cfg.hard_limit_us:g} μs · zero is always measured first",
            scan_box,
        )
        limit.setWordWrap(True)
        scan_form.addRow(limit)
        left_layout.addWidget(scan_box)

        diagnostic_box = QGroupBox("ENY diagnostics", left)
        diagnostic_layout = QVBoxLayout(diagnostic_box)
        background_row = QHBoxLayout()
        self.use_background = QCheckBox("Use background", diagnostic_box)
        self.capture_background = QPushButton("Capture / Replace", diagnostic_box)
        self.preview = QPushButton("Preview", diagnostic_box)
        background_row.addWidget(self.use_background)
        background_row.addWidget(self.capture_background)
        background_row.addWidget(self.preview)
        diagnostic_layout.addLayout(background_row)
        self.background_status = QLabel("Background: none", diagnostic_box)
        self.background_status.setWordWrap(True)
        diagnostic_layout.addWidget(self.background_status)

        geometry = cfg.image_geometry
        self.roi_control = ROIControl(
            image_shape=(geometry.shape[1], geometry.shape[0]),
            runtime_path=(
                self.runtime_paths["latest_dir"]
                / "roi"
                / f"{cfg.flag_element}.json"
            ),
            configured=geometry.default_roi,
            parent=diagnostic_box,
        )
        self.roi_control.warningRaised.connect(self._show_status)
        self.roi_control.roiChanged.connect(
            lambda _roi, _enabled: self._redraw_latest_image()
        )
        diagnostic_layout.addWidget(self.roi_control)
        left_layout.addWidget(diagnostic_box)

        energy_box = QGroupBox("Energy Match", left)
        energy_layout = QVBoxLayout(energy_box)
        self.energy_settings_dialog = EnergyMatchSettingsDialog(cfg, self)
        self.energy_summary = QLabel(energy_box)
        self.energy_summary.setWordWrap(True)
        self.energy_settings = QPushButton("Advanced Settings…", energy_box)
        self.energy_settings.clicked.connect(self._show_energy_settings)
        energy_layout.addWidget(self.energy_summary)
        energy_layout.addWidget(self.energy_settings)
        left_layout.addWidget(energy_box)
        left_layout.addStretch(1)
        self._update_energy_summary()

        right = QWidget(splitter)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(6, 0, 0, 0)
        right_layout.setSpacing(8)
        self.energy_plot = MplWidget(right)
        self.energy_plot.setMinimumHeight(280)
        self.image_plot = MplWidget(right)
        self.image_plot.setMinimumHeight(260)
        right_layout.addWidget(self.energy_plot, 1)
        right_layout.addWidget(self.image_plot, 1)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([360, 1000])
        root.addWidget(splitter, 1)

        self.table = QTableWidget(0, 12, self)
        self.table.setHorizontalHeaderLabels(
            [
                "#",
                "Offset μs",
                "HV RB",
                "LLRF RB",
                "SSA RB",
                "KLY RB",
                "Energy MeV",
                "SEM",
                "Energy Set",
                "Brightness",
                "Status",
                "Error",
            ]
        )
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMaximumHeight(235)
        root.addWidget(self.table)

        bottom = QHBoxLayout()
        self.status = QLabel("Ready", self)
        self.status.setWordWrap(True)
        self.progress = QProgressBar(self)
        self.progress.setTextVisible(True)
        self.progress.setMinimumWidth(260)
        self.start = QPushButton("Start", self)
        self.stop = QPushButton("Stop", self)
        self.apply_best = QPushButton("Apply Best", self)
        self.close_button = QPushButton("Close", self)
        self.stop.setEnabled(False)
        self.apply_best.setEnabled(False)
        self.start.clicked.connect(self._start_scan)
        self.stop.clicked.connect(self._stop_scan)
        self.apply_best.clicked.connect(self._apply_best)
        self.close_button.clicked.connect(self.close)
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.progress)
        bottom.addWidget(self.start)
        bottom.addWidget(self.stop)
        bottom.addWidget(self.apply_best)
        bottom.addWidget(self.close_button)
        root.addLayout(bottom)

        self.capture_background.clicked.connect(
            lambda: self._read_image("background")
        )
        self.preview.clicked.connect(lambda: self._read_image("preview"))
        self._redraw_energy_plot()
        self._draw_image(None)

    def _show_energy_settings(self) -> None:
        self.energy_settings_dialog.exec_()
        self._update_energy_summary()

    def _update_energy_summary(self) -> None:
        values = self.energy_settings_dialog.values()
        self.energy_summary.setText(
            f"After energy change {values['settle_time_s']:g} s · "
            f"center return {2 * values['settle_time_s']:g} s · "
            f"R² ≥ {values['min_fit_r_squared']:.2f} · "
            f"center tolerance {values['center_tolerance_mm']:g} mm"
        )

    def _match_settings(self) -> dict[str, Any]:
        cfg = self.config
        energy_match = dict(cfg.energy_match)
        search = dict(energy_match.get("range", {}))
        defaults = dict(cfg.energy_match_defaults)
        stages = dict(defaults.get("stages", {}))
        center = dict(stages.get("center_lock", {}))
        measurement = dict(defaults.get("measurement", {}))
        values = self.energy_settings_dialog.values()
        center.update(
            {
                "center_lock_trigger_mm": values["center_lock_trigger_mm"],
                "center_tolerance_mm": values["center_tolerance_mm"],
                "max_iterations": values["max_iterations"],
                "max_correction_step_mev": values["max_correction_step_mev"],
            }
        )
        return {
            "settle_time_s": values["settle_time_s"],
            "restore_initial_on_failure": bool(
                search.get("restore_initial_on_failure", True)
            ),
            "profile_fit_method": str(
                measurement.get("profile_fit_method", "Gauss fit")
            ),
            "min_fit_r_squared": values["min_fit_r_squared"],
            "beam_presence_sigma": values["beam_presence_sigma"],
            "beam_presence_min_area_px": values[
                "beam_presence_min_area_px"
            ],
            "x_reference_mm": cfg.x_reference_mm,
            "design_eta_m": cfg.design_eta_m,
            "image_flip_y": cfg.image_geometry.flip_y,
            "center_lock_trigger_mm": center["center_lock_trigger_mm"],
            "center_tolerance_mm": center["center_tolerance_mm"],
            "max_iterations": center["max_iterations"],
            "max_correction_step_mev": center["max_correction_step_mev"],
            "pipelines": dict(energy_match.get("pipelines", {})),
            "stages": {
                "center_lock": {
                    **center,
                    "strategy": str(
                        center.get("strategy", "secant_dispersion")
                    ),
                }
            },
        }

    def _build_request(self) -> TimingEnergyScanRequest:
        settings_values = self.energy_settings_dialog.values()
        settings = TimingEnergyScanSettings(
            low_offset_us=self.low.value(),
            high_offset_us=self.high.value(),
            step_us=self.step.value(),
            hard_limit_us=self.config.hard_limit_us,
            timing_settle_time_s=self.timing_settle.value(),
            energy_settle_time_s=settings_values["settle_time_s"],
            tracking_half_window_mev=self.config.tracking_half_window_mev,
            energy_low_mev=self.config.energy_low_mev,
            energy_high_mev=self.config.energy_high_mev,
            minimum_delay_us=self.runtime.minimum_us,
            min_relative_brightness=self.config.min_relative_brightness,
        )
        metadata = dict(self.background_metadata)
        metadata["path"] = (
            str(self.background_path)
            if self.background_image is not None
            else None
        )
        metadata["shape"] = (
            list(self.background_image.shape)
            if self.background_image is not None
            else None
        )
        return TimingEnergyScanRequest(
            context=self.runtime.context,
            group=self.group,
            settings=settings,
            scan=self._match_settings(),
            image_pv=self.config.flag_image_pv,
            image_shape=self.config.image_geometry.shape,
            pixel_width_mm=self.config.image_geometry.pixel_width_mm,
            energy_pv=self.config.energy_pv,
            remove_bg=(
                self.use_background.isChecked()
                and self.background_image is not None
            ),
            bg_image=self.background_image,
            background_metadata=metadata,
            point_measurement={
                "samples": self.samples.value(),
                "interval_s": self.sample_interval.value(),
                "min_valid_samples": min(
                    self.config.min_valid_samples,
                    self.samples.value(),
                ),
            },
            roi=self.roi_control.active_roi(),
            readback_tolerance_us=self.runtime.readback_tolerance_us,
            readback_timeout_s=self.config.readback_timeout_s,
            readback_poll_interval_s=self.config.readback_poll_interval_s,
        )

    def _start_scan(self) -> None:
        try:
            request = self._build_request()
        except Exception as exc:
            QMessageBox.warning(self, "Invalid Scan Settings", str(exc))
            return
        answer = QMessageBox.question(
            self,
            "Start Linked Timing Energy Scan",
            f"Scan {self.group.element_id} from {request.settings.low_offset_us:g} "
            f"to {request.settings.high_offset_us:g} μs in "
            f"{request.settings.step_us:g} μs steps?\n\n"
            "HV, LLRF, SSA, and KLY delays will move together. "
            "The initial timing and energy will be restored at the end.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.points_data.clear()
        self.result = None
        self.status.setStyleSheet("")
        self._latest_fit_center_mm = None
        self.table.setRowCount(0)
        self.progress.setRange(0, len(request.settings.offsets()))
        self.progress.setValue(0)
        self.apply_best.setEnabled(False)
        self._redraw_energy_plot()
        self.worker = TimingScanWorker(request, self)
        self.worker.point.connect(self._point_received)
        self.worker.diagnostic.connect(self._diagnostic_received)
        self.worker.state.connect(self._show_status)
        self.worker.progress.connect(self._progress_received)
        self.worker.match_state.connect(self._match_state_received)
        self.worker.done.connect(self._scan_done)
        self._set_running(True)
        self.worker.start()

    def _stop_scan(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.requestInterruption()
            self.stop.setEnabled(False)
            self._show_status("Stop requested · restoring machine state")

    def _scan_done(self, result: TimingEnergyScanResult) -> None:
        self.result = result
        worker = self.worker
        self.last_executor = worker.executor if worker is not None else None
        self.worker = None
        self._set_running(False)
        if result.best_point is not None:
            self._redraw_energy_plot(result.best_point.to_mapping())
        can_apply = bool(
            result.status == "DONE"
            and result.best_point is not None
            and result.best_point.energy_setpoint_mev is not None
            and result.timing_restored
            and result.energy_restored
        )
        self.apply_best.setEnabled(can_apply)
        if result.status == "DONE":
            text = "Scan complete · initial timing and energy restored"
            if result.best_point is not None:
                text += (
                    f" · best {result.best_point.offset_us:+g} μs / "
                    f"{result.best_point.matched_energy_mev:.3f} MeV"
                )
            if result.best_at_boundary:
                text += " · WARNING: best point is on the scan boundary"
        else:
            text = result.message or result.status
        self._show_status(text)
        if result.status == "RESTORE_FAILED":
            self.status.setStyleSheet(
                "color: #ff5f6d; font-weight: 600;"
            )
            self.machine_state.setText("RESTORE FAILED · verify machine state")
        if worker is not None:
            worker.deleteLater()
        if self._close_requested:
            if result.status == "RESTORE_FAILED":
                self._close_requested = False
            else:
                self.accept()

    def _apply_best(self) -> None:
        if (
            self.result is None
            or self.result.best_point is None
            or self.last_executor is None
        ):
            return
        best = self.result.best_point
        warning = (
            "\n\nThe best point is on the scan boundary."
            if self.result.best_at_boundary
            else ""
        )
        answer = QMessageBox.question(
            self,
            "Apply Best Timing and Energy",
            f"Apply {best.offset_us:+g} μs to all four timing channels and "
            f"set coordinated energy to {best.energy_setpoint_mev:.3f} MeV?"
            f"{warning}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.apply_best.setEnabled(False)
        self.start.setEnabled(False)
        self.close_button.setEnabled(False)
        self.apply_worker = ApplyBestWorker(
            self.last_executor,
            self.result,
            self,
        )
        self.apply_worker.done.connect(self._apply_done)
        self._show_status("Applying best timing and energy")
        self.apply_worker.start()

    def _apply_done(self, payload: dict[str, Any]) -> None:
        self.start.setEnabled(True)
        self.close_button.setEnabled(True)
        status = str(payload.get("status", "APPLY_FAILED"))
        if status == "APPLIED":
            message = (
                f"Best point applied · {float(payload['offset_us']):+g} μs · "
                f"{float(payload['energy_setpoint_mev']):.3f} MeV"
            )
            if payload.get("log_warning"):
                message += f" · {payload['log_warning']}"
            self._show_status(message)
            self.machine_state.setText("Best timing and energy are applied")
        else:
            message = str(payload.get("message", "Apply Best failed"))
            rollback = payload.get("rollback_errors") or {}
            if rollback:
                message += f" · rollback errors: {rollback}"
            self._show_status(message)
            QMessageBox.warning(self, "Apply Best Failed", message)
        if self.apply_worker is not None:
            self.apply_worker.deleteLater()
            self.apply_worker = None

    def _point_received(self, point: dict[str, Any]) -> None:
        self.points_data.append(dict(point))
        row = self.table.rowCount()
        self.table.insertRow(row)
        readbacks = dict(point.get("readbacks_us") or {})
        values = [
            str(row + 1),
            f"{float(point['offset_us']):+.3f}",
            self._number(readbacks.get("hv"), 6),
            self._number(readbacks.get("llrf"), 6),
            self._number(readbacks.get("ssa"), 6),
            self._number(readbacks.get("kly"), 6),
            self._number(point.get("matched_energy_mev"), 3),
            self._number(point.get("energy_standard_error_mev"), 4),
            self._number(point.get("energy_setpoint_mev"), 3),
            self._number(point.get("brightness"), 1),
            str(point.get("status", "")),
            str(point.get("message") or ""),
        ]
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setToolTip(str(point.get("message") or ""))
            self.table.setItem(row, column, item)
        self.table.scrollToBottom()
        self._redraw_energy_plot()

    @staticmethod
    def _number(value: Any, decimals: int) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "—"
        return f"{number:.{decimals}f}" if math.isfinite(number) else "—"

    def _diagnostic_received(
        self,
        image: Any,
        energy_mev: float,
        quality: dict[str, Any],
    ) -> None:
        self._latest_image = np.asarray(image, dtype=float)
        try:
            center = float(quality.get("center_mm"))
            self._latest_fit_center_mm = (
                center if math.isfinite(center) else None
            )
        except (TypeError, ValueError):
            self._latest_fit_center_mm = None
        self._draw_image(self._latest_image)
        details = [
            f"ENY {energy_mev:.3f} MeV · "
            f"{quality.get('valid_frames', '—')}/"
            f"{quality.get('total_frames', '—')} valid frames"
        ]
        if self._latest_fit_center_mm is not None:
            details.append(
                f"center {self._latest_fit_center_mm:+.3f} mm"
            )
        try:
            fit_r_squared = float(quality.get("fit_r_squared"))
            if math.isfinite(fit_r_squared):
                details.append(f"R² {fit_r_squared:.3f}")
        except (TypeError, ValueError):
            pass
        self.machine_state.setText(" · ".join(details))

    def _match_state_received(self, payload: dict[str, Any]) -> None:
        stage = payload.get("stage")
        energy = payload.get("energy_mev")
        if stage and energy is not None:
            self._show_status(
                f"Energy Match · {stage} · {float(energy):.3f} MeV"
            )

    def _progress_received(self, complete: int, total: int) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(complete)

    def _redraw_energy_plot(
        self,
        best: dict[str, Any] | None = None,
    ) -> None:
        axes = self.energy_plot.axes
        axes.clear()
        valid = [
            point
            for point in self.points_data
            if point.get("status") == "ok"
            and point.get("matched_energy_mev") is not None
        ]
        if valid:
            x = [float(point["offset_us"]) for point in valid]
            y = [float(point["matched_energy_mev"]) for point in valid]
            error = [
                float(point.get("energy_standard_error_mev") or 0.0)
                for point in valid
            ]
            axes.errorbar(
                x,
                y,
                yerr=error,
                marker="o",
                color="#45d0bc",
                ecolor="#90a1ad",
                capsize=3,
                label="Valid points",
            )
        excluded = [
            point
            for point in self.points_data
            if point.get("status") != "ok"
            and point.get("matched_energy_mev") is not None
        ]
        if excluded:
            axes.scatter(
                [float(point["offset_us"]) for point in excluded],
                [float(point["matched_energy_mev"]) for point in excluded],
                marker="x",
                color="#e4b86f",
                label="Excluded",
            )
        current = (
            self.points_data[-1]
            if self.points_data
            and self.points_data[-1].get("matched_energy_mev") is not None
            else None
        )
        if current is not None:
            axes.scatter(
                [float(current["offset_us"])],
                [float(current["matched_energy_mev"])],
                marker="s",
                facecolors="none",
                edgecolors="#f4f7f9",
                label="Current",
                zorder=4,
            )
        if best is not None and best.get("matched_energy_mev") is not None:
            axes.scatter(
                [float(best["offset_us"])],
                [float(best["matched_energy_mev"])],
                marker="*",
                s=180,
                color="#ff5f6d",
                label="Best",
                zorder=5,
            )
        axes.set_xlabel("Linked timing offset (μs)")
        axes.set_ylabel("Measured beam energy (MeV)")
        axes.grid(True, alpha=0.25)
        if valid or excluded or current or best:
            axes.legend(loc="best")
        self.energy_plot.canvas.draw_idle()

    def _draw_image(self, image: np.ndarray | None) -> None:
        axes = self.image_plot.axes
        axes.clear()
        geometry = self.config.image_geometry
        width = geometry.shape[0] * geometry.pixel_width_mm
        height = geometry.shape[1] * geometry.pixel_width_mm
        extent = (-width / 2, width / 2, -height / 2, height / 2)
        if image is None:
            axes.text(
                0.5,
                0.5,
                "ENY image waiting",
                ha="center",
                va="center",
                transform=axes.transAxes,
            )
        else:
            analysis = np.asarray(image, dtype=float)
            if (
                self.use_background.isChecked()
                and self.background_image is not None
            ):
                analysis = np.maximum(analysis - self.background_image, 0.0)
            axes.imshow(
                analysis,
                origin="lower",
                extent=extent,
                aspect="auto",
                cmap="viridis",
            )
            self.roi_control.attach_axes(axes, extent=extent)
            if self._latest_fit_center_mm is not None:
                axes.axvline(
                    self._latest_fit_center_mm,
                    color="#ff5f6d",
                    linewidth=1.5,
                    linestyle="--",
                    label="Fitted center",
                )
                axes.legend(loc="upper right")
        axes.set_xlabel("x (mm)")
        axes.set_ylabel("y (mm)")
        self.image_plot.canvas.draw_idle()

    def _redraw_latest_image(self) -> None:
        self._draw_image(getattr(self, "_latest_image", None))

    def _read_image(self, action: str) -> None:
        if self.image_worker is not None and self.image_worker.isRunning():
            return
        if action == "background":
            answer = QMessageBox.question(
                self,
                "Capture ENY Background",
                "Confirm that beam is absent before capturing the background.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        self._image_action = action
        self.image_worker = ImageReadWorker(
            self.config.flag_image_pv,
            self.config.image_geometry.shape,
            self.config.image_geometry.flip_y,
            self,
        )
        self.image_worker.done.connect(self._image_read_done)
        self.image_worker.failed.connect(self._image_read_failed)
        self.preview.setEnabled(False)
        self.capture_background.setEnabled(False)
        self.image_worker.start()

    def _image_read_done(self, image: np.ndarray) -> None:
        self._latest_image = np.asarray(image, dtype=float)
        self._latest_fit_center_mm = None
        if self._image_action == "background":
            metadata = {
                "created_at": datetime.now().astimezone().isoformat(
                    timespec="seconds"
                ),
                "machine_id": self.runtime.context.machine.id,
                "backend": self.runtime.context.control_backend.name,
                "flag_element": self.config.flag_element,
                "shape": list(self._latest_image.shape),
                "exposure_s": (
                    caget(self.config.flag_exposure_pv)
                    if self.config.flag_exposure_pv
                    else None
                ),
            }
            try:
                save_background(
                    self._latest_image,
                    self.background_path,
                    self.background_metadata_path,
                    metadata,
                )
                self.background_image = self._latest_image.copy()
                self.background_metadata = metadata
                self.use_background.setChecked(True)
                self._update_background_status()
            except (BackgroundStoreError, OSError, ValueError) as exc:
                QMessageBox.warning(self, "Background", str(exc))
        self._draw_image(self._latest_image)
        self._finish_image_worker()

    def _image_read_failed(self, message: str) -> None:
        QMessageBox.warning(self, "ENY Image", message)
        self._finish_image_worker()

    def _finish_image_worker(self) -> None:
        self.preview.setEnabled(True)
        self.capture_background.setEnabled(True)
        if self.image_worker is not None:
            self.image_worker.deleteLater()
            self.image_worker = None

    def _load_latest_background(self, *, silent: bool = False) -> bool:
        if not self.background_path.is_file():
            self._update_background_status()
            return False
        try:
            image, metadata = load_background(
                self.background_path,
                self.background_metadata_path,
                expected_shape=(
                    self.config.image_geometry.shape[1],
                    self.config.image_geometry.shape[0],
                ),
            )
            if metadata.get("machine_id") not in (
                None,
                self.runtime.context.machine.id,
            ):
                raise ValueError("Background belongs to another machine.")
            if metadata.get("backend") not in (
                None,
                self.runtime.context.control_backend.name,
            ):
                raise ValueError("Background belongs to another backend.")
            if metadata.get("flag_element") not in (
                None,
                self.config.flag_element,
            ):
                raise ValueError("Background belongs to another screen.")
            self.background_image = image
            self.background_metadata = metadata
            self.use_background.setChecked(True)
            self._update_background_status()
            return True
        except (BackgroundStoreError, OSError, ValueError) as exc:
            self.use_background.setChecked(False)
            if not silent:
                QMessageBox.warning(self, "Background", str(exc))
            self._update_background_status()
            return False

    def _update_background_status(self) -> None:
        if self.background_image is None:
            self.background_status.setText("Background: none")
        else:
            created = self.background_metadata.get("created_at", "saved")
            self.background_status.setText(f"Background: {created}")

    def _set_running(self, running: bool) -> None:
        for widget in (
            self.low,
            self.high,
            self.step,
            self.timing_settle,
            self.samples,
            self.sample_interval,
            self.use_background,
            self.capture_background,
            self.preview,
            self.energy_settings,
            self.roi_control,
        ):
            widget.setEnabled(not running)
        self.start.setEnabled(not running)
        self.stop.setEnabled(running)
        self.close_button.setEnabled(True)
        self.scanActiveChanged.emit(running)

    def _show_status(self, message: str) -> None:
        self.status.setText(str(message))

    def _apply_plot_theme(self) -> None:
        dark = self.theme != "light"
        background = "#11181e" if dark else "#fffdf8"
        text = "#90a1ad" if dark else "#746c62"
        for widget in (self.energy_plot, self.image_plot):
            widget.fig.patch.set_facecolor(background)
            widget.axes.set_facecolor(background)
            widget.axes.tick_params(colors=text)
            for spine in widget.axes.spines.values():
                spine.set_color(text)
            widget.axes.xaxis.label.set_color(text)
            widget.axes.yaxis.label.set_color(text)
            widget.canvas.draw_idle()

    def closeEvent(self, event) -> None:
        if self.worker is not None and self.worker.isRunning():
            self._close_requested = True
            self._stop_scan()
            event.ignore()
            return
        if self.apply_worker is not None and self.apply_worker.isRunning():
            self._show_status("Wait for Apply Best to finish before closing")
            event.ignore()
            return
        super().closeEvent(event)
