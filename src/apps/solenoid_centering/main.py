from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

_REPO_BOOTSTRAP_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file()
)
if str(_REPO_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_BOOTSTRAP_ROOT))

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from half_linac.src.shared.machine_profile import (
    MachineProfileError,
    SolenoidCenteringPreset,
    SolenoidCenteringScanRange,
    list_elements,
    load_app_context,
    resolve_channel,
    workflow_writes_allowed,
)
from half_linac.src.shared.window_activation import install_qt_window_raise_handler
from half_linac.src.apps.solenoid_centering.mplwidget import MplWidget
from half_linac.src.apps.solenoid_centering.scan import (
    CenteringResult,
    SolenoidCenteringScanner,
    StopRequested,
)


class ScanWorker(QThread):
    progress_changed = pyqtSignal(str, int, int)
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, context, preset, parent=None):
        super().__init__(parent)
        self.context = context
        self.preset = preset
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def run(self):
        try:
            scanner = SolenoidCenteringScanner(
                self.context,
                self.preset,
                progress=self.progress_changed.emit,
                stop_requested=lambda: self._stop_requested,
            )
            result = scanner.run()
        except StopRequested as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished_ok.emit(result)


class PreflightWorker(QThread):
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, context, preset, parent=None):
        super().__init__(parent)
        self.context = context
        self.preset = preset

    def run(self):
        try:
            scanner = SolenoidCenteringScanner(self.context, self.preset)
            report = scanner.preflight()
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished_ok.emit(report)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        install_qt_window_raise_handler(self)
        self.context = load_app_context("solenoid_centering")
        if self.context.solenoid_centering_workflow is None:
            raise MachineProfileError("Solenoid-centering workflow is not available.")
        self.workflow = self.context.solenoid_centering_workflow
        self.worker: ScanWorker | None = None
        self.preflight_worker: PreflightWorker | None = None
        self.last_result: CenteringResult | None = None
        self.last_result_preset: SolenoidCenteringPreset | None = None

        self.setWindowTitle("Solenoid Centering")
        self.resize(1120, 760)
        self._build_ui()
        self._load_device_choices()
        self._load_presets()
        self._refresh_write_state()

    def _build_ui(self):
        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        top = QHBoxLayout()
        self.preset_combo = QComboBox(central)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        self.status_label = QLabel("Idle", central)
        top.addWidget(QLabel("Preset", central))
        top.addWidget(self.preset_combo, 1)
        top.addWidget(self.status_label)
        layout.addLayout(top)

        content = QHBoxLayout()
        content.addWidget(self._build_control_panel(central), 0)

        right = QVBoxLayout()
        self.plot = MplWidget(central)
        right.addWidget(self.plot, 1)
        self.result_table = QTableWidget(0, 6, central)
        self.result_table.setHorizontalHeaderLabels(
            ["Axis", "Round", "Corrector", "Score", "Slope X", "Slope Y"]
        )
        right.addWidget(self.result_table, 1)
        content.addLayout(right, 1)
        layout.addLayout(content, 1)

        self.setCentralWidget(central)

    def _build_control_panel(self, parent):
        panel = QWidget(parent)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        device_group = QGroupBox("Devices", panel)
        device_layout = QFormLayout(device_group)
        self.solenoid_pv_label = QLabel("--", device_group)
        self.hcorr_combo = QComboBox(device_group)
        self.vcorr_combo = QComboBox(device_group)
        self.bpm_combo = QComboBox(device_group)
        device_layout.addRow("Solenoid PV", self.solenoid_pv_label)
        device_layout.addRow("HCOR", self.hcorr_combo)
        device_layout.addRow("VCOR", self.vcorr_combo)
        device_layout.addRow("BPM", self.bpm_combo)
        layout.addWidget(device_group)

        scan_group = QGroupBox("Scan", panel)
        scan_layout = QGridLayout(scan_group)
        self.sol_from = self._double_spin(-1e6, 1e6, 0.01, 4)
        self.sol_to = self._double_spin(-1e6, 1e6, 0.01, 4)
        self.sol_steps = self._int_spin(2, 999)
        self.cor_from = self._double_spin(-1e6, 1e6, 0.0001, 6)
        self.cor_to = self._double_spin(-1e6, 1e6, 0.0001, 6)
        self.cor_steps = self._int_spin(2, 999)
        self.samples = self._int_spin(1, 999)
        self.settle = self._double_spin(0.0, 3600.0, 0.5, 2)
        self.sample_interval = self._double_spin(0.0, 3600.0, 0.1, 2)
        self.max_rounds = self._int_spin(1, 99)

        fields = [
            ("Sol from", self.sol_from),
            ("Sol to", self.sol_to),
            ("Sol steps", self.sol_steps),
            ("Cor from", self.cor_from),
            ("Cor to", self.cor_to),
            ("Cor steps", self.cor_steps),
            ("Samples", self.samples),
            ("Settle s", self.settle),
            ("Sample interval s", self.sample_interval),
            ("Max rounds", self.max_rounds),
        ]
        for row, (label, widget) in enumerate(fields):
            scan_layout.addWidget(QLabel(label, scan_group), row, 0)
            scan_layout.addWidget(widget, row, 1)
        layout.addWidget(scan_group)

        self.progress = QProgressBar(panel)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        buttons = QHBoxLayout()
        self.check_button = QPushButton("Check PVs", panel)
        self.start_button = QPushButton("Start Scan", panel)
        self.stop_button = QPushButton("Stop", panel)
        self.apply_button = QPushButton("Apply Recommended", panel)
        self.restore_button = QPushButton("Restore Original", panel)
        self.stop_button.setEnabled(False)
        self.apply_button.setEnabled(False)
        self.restore_button.setEnabled(False)
        self.check_button.clicked.connect(self.run_preflight)
        self.start_button.clicked.connect(self.start_scan)
        self.stop_button.clicked.connect(self.stop_scan)
        self.apply_button.clicked.connect(self.apply_recommended)
        self.restore_button.clicked.connect(self.restore_original)
        buttons.addWidget(self.check_button)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.stop_button)
        layout.addLayout(buttons)
        layout.addWidget(self.apply_button)
        layout.addWidget(self.restore_button)
        layout.addStretch(1)
        return panel

    @staticmethod
    def _double_spin(minimum, maximum, step, decimals):
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        spin.setDecimals(decimals)
        return spin

    @staticmethod
    def _int_spin(minimum, maximum):
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        return spin

    def _load_device_choices(self):
        self._populate_element_combo(
            self.hcorr_combo,
            self._corrector_choices("x", ("HC", "HIC", "XCOR")),
        )
        self._populate_element_combo(
            self.vcorr_combo,
            self._corrector_choices("y", ("VC", "VIC", "YCOR")),
        )
        bpms = [
            element
            for element in list_elements(self.context, kind="bpm", logical_channel="x")
            if "y" in element.channels
        ]
        self._populate_element_combo(self.bpm_combo, bpms)

    def _corrector_choices(self, plane, fallback_tokens):
        correctors = list_elements(self.context, kind="corr", logical_channel="current_set")
        by_plane = [element for element in correctors if element.plane == plane]
        if by_plane:
            return by_plane
        tokens = tuple(token.upper() for token in fallback_tokens)
        by_name = [
            element
            for element in correctors
            if any(
                token in f"{element.id} {element.display_name}".upper()
                for token in tokens
            )
        ]
        return by_name or correctors

    @staticmethod
    def _populate_element_combo(combo, elements):
        combo.clear()
        for element in elements:
            combo.addItem(element.display_name or element.id, element.id)

    def _load_presets(self):
        self.preset_combo.clear()
        for preset in self.workflow.presets:
            self.preset_combo.addItem(preset.display_name, preset.id)
        index = self.preset_combo.findData(self.workflow.default_preset)
        if index >= 0:
            self.preset_combo.setCurrentIndex(index)
        self._on_preset_changed()

    def _current_preset(self) -> SolenoidCenteringPreset:
        preset_id = self.preset_combo.currentData()
        return self.workflow.presets_by_id[str(preset_id)]

    def _on_preset_changed(self):
        if self.preset_combo.count() <= 0:
            return
        preset = self._current_preset()
        self.solenoid_pv_label.setText(self._solenoid_setpoint_label(preset))
        self._set_combo_value(self.hcorr_combo, preset.hcorr, "HCOR")
        self._set_combo_value(self.vcorr_combo, preset.vcorr, "VCOR")
        self._set_combo_value(self.bpm_combo, preset.bpm, "BPM")
        self.sol_from.setValue(preset.solenoid_scan.relative_from)
        self.sol_to.setValue(preset.solenoid_scan.relative_to)
        self.sol_steps.setValue(preset.solenoid_scan.steps)
        self.cor_from.setValue(preset.corrector_scan.relative_from)
        self.cor_to.setValue(preset.corrector_scan.relative_to)
        self.cor_steps.setValue(preset.corrector_scan.steps)
        self.samples.setValue(preset.samples_per_point)
        self.settle.setValue(preset.settle_time_s)
        self.sample_interval.setValue(preset.sample_interval_s)
        self.max_rounds.setValue(preset.max_rounds)

    def _solenoid_setpoint_label(self, preset: SolenoidCenteringPreset) -> str:
        if preset.solenoid:
            try:
                return resolve_channel(self.app_context, preset.solenoid, "current_set")
            except MachineProfileError:
                return preset.solenoid
        return preset.solenoid_setpoint_pv or ""

    @staticmethod
    def _set_combo_value(combo, value, label):
        index = combo.findData(value)
        if index < 0:
            raise MachineProfileError(f"{label} {value!r} is not available for this machine/backend.")
        combo.setCurrentIndex(index)

    @staticmethod
    def _combo_value(combo, label) -> str:
        value = combo.currentData()
        if value is None:
            raise MachineProfileError(f"{label} selection is empty.")
        return str(value)

    def _preset_with_overrides(self) -> SolenoidCenteringPreset:
        preset = self._current_preset()
        if self.sol_from.value() == self.sol_to.value():
            raise ValueError("Solenoid scan range must not be zero.")
        if self.cor_from.value() == self.cor_to.value():
            raise ValueError("Corrector scan range must not be zero.")
        return replace(
            preset,
            hcorr=self._combo_value(self.hcorr_combo, "HCOR"),
            vcorr=self._combo_value(self.vcorr_combo, "VCOR"),
            bpm=self._combo_value(self.bpm_combo, "BPM"),
            solenoid_scan=SolenoidCenteringScanRange(
                relative_from=self.sol_from.value(),
                relative_to=self.sol_to.value(),
                steps=self.sol_steps.value(),
            ),
            corrector_scan=SolenoidCenteringScanRange(
                relative_from=self.cor_from.value(),
                relative_to=self.cor_to.value(),
                steps=self.cor_steps.value(),
            ),
            samples_per_point=self.samples.value(),
            settle_time_s=self.settle.value(),
            sample_interval_s=self.sample_interval.value(),
            max_rounds=self.max_rounds.value(),
        )

    def _refresh_write_state(self):
        allowed = workflow_writes_allowed(self.context, "solenoid_centering")
        self.start_button.setEnabled(allowed)
        if not allowed:
            self.status_label.setText(
                f"Writes blocked for {self.context.machine.id}/{self.context.control_backend.name}"
            )

    def run_preflight(self):
        if self.worker is not None and self.worker.isRunning():
            return
        if self.preflight_worker is not None and self.preflight_worker.isRunning():
            return
        try:
            preset = self._preset_with_overrides()
        except Exception as exc:
            QMessageBox.warning(self, "Solenoid Centering", str(exc))
            return
        self.status_label.setText("Checking PVs")
        self.progress.setValue(0)
        self.preflight_worker = PreflightWorker(self.context, preset, self)
        self.preflight_worker.finished_ok.connect(self._on_preflight_finished)
        self.preflight_worker.failed.connect(self._on_preflight_failed)
        self.preflight_worker.finished.connect(self._on_preflight_done)
        self.preflight_worker.start()
        self.check_button.setEnabled(False)
        self.start_button.setEnabled(False)

    def start_scan(self):
        if self.worker is not None and self.worker.isRunning():
            return
        if self.preflight_worker is not None and self.preflight_worker.isRunning():
            return
        try:
            preset = self._preset_with_overrides()
        except Exception as exc:
            QMessageBox.warning(self, "Solenoid Centering", str(exc))
            return
        self.last_result = None
        self.last_result_preset = None
        self.apply_button.setEnabled(False)
        self.restore_button.setEnabled(False)
        self.result_table.setRowCount(0)
        self.plot.clear()
        self.progress.setValue(0)
        self.status_label.setText("Running")
        self.worker = ScanWorker(self.context, preset, self)
        self.worker.progress_changed.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_scan_finished)
        self.worker.failed.connect(self._on_scan_failed)
        self.worker.finished.connect(self._on_worker_done)
        self.worker.start()
        self.check_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)

    def stop_scan(self):
        if self.worker is not None and self.worker.isRunning():
            self.status_label.setText("Stopping")
            self.worker.request_stop()

    def apply_recommended(self):
        if self.last_result is None or self.last_result_preset is None:
            return
        try:
            scanner = SolenoidCenteringScanner(self.context, self.last_result_preset)
            scanner.apply_recommended(
                self.last_result.recommended_hcorr,
                self.last_result.recommended_vcorr,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Solenoid Centering", str(exc))
            return
        self.status_label.setText("Recommended correctors applied")

    def restore_original(self):
        if self.last_result is None or self.last_result_preset is None:
            return
        try:
            scanner = SolenoidCenteringScanner(self.context, self.last_result_preset)
            scanner.apply_recommended(
                self.last_result.original_hcorr,
                self.last_result.original_vcorr,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Solenoid Centering", str(exc))
            return
        self.status_label.setText("Original correctors restored")

    def _on_progress(self, message, completed, total):
        percent = int(round(completed / total * 100)) if total else 0
        self.progress.setValue(max(0, min(100, percent)))
        self.status_label.setText(f"{message} ({completed}/{total})")

    def _on_preflight_finished(self, report):
        self.progress.setValue(100)
        self.status_label.setText("Preflight READY")
        QMessageBox.information(self, "Solenoid Centering Preflight", report.as_text())

    def _on_preflight_failed(self, message):
        self.status_label.setText("Preflight NOT READY")
        QMessageBox.warning(self, "Solenoid Centering Preflight", f"NOT READY\n{message}")

    def _on_preflight_done(self):
        self.check_button.setEnabled(True)
        self.start_button.setEnabled(workflow_writes_allowed(self.context, "solenoid_centering"))

    def _on_scan_finished(self, result):
        self.last_result = result
        self.last_result_preset = self.worker.preset if self.worker is not None else None
        self.progress.setValue(100)
        self.status_label.setText(
            f"Recommended H={result.recommended_hcorr:.6g}, V={result.recommended_vcorr:.6g}"
        )
        self.apply_button.setEnabled(workflow_writes_allowed(self.context, "solenoid_centering"))
        self.restore_button.setEnabled(workflow_writes_allowed(self.context, "solenoid_centering"))
        self._populate_result_table(result)
        self.plot.plot_result(result)

    def _on_scan_failed(self, message):
        self.status_label.setText(message)
        QMessageBox.warning(self, "Solenoid Centering", message)

    def _on_worker_done(self):
        self.check_button.setEnabled(True)
        self.start_button.setEnabled(workflow_writes_allowed(self.context, "solenoid_centering"))
        self.stop_button.setEnabled(False)

    def _populate_result_table(self, result):
        rows = [scan.best for scan in result.axis_scans]
        self.result_table.setRowCount(len(rows))
        for row, candidate in enumerate(rows):
            values = [
                candidate.axis.upper(),
                str(candidate.round_index + 1),
                f"{candidate.corrector_value:.8g}",
                f"{candidate.score.score:.6g}",
                f"{candidate.score.slope_x:.6g}",
                f"{candidate.score.slope_y:.6g}",
            ]
            for col, value in enumerate(values):
                self.result_table.setItem(row, col, QTableWidgetItem(value))
        self.result_table.resizeColumnsToContents()


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
