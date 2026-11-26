from __future__ import annotations

import math
from pathlib import Path
from typing import Any
from uuid import uuid4

from PyQt5.QtCore import QThread, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from half_linac.src.apps.rf_phase_scan.acceleration_check import (
    AccelerationCheck,
    discover_acceleration_checks,
    resolve_acceleration_capture,
)
from half_linac.src.apps.rf_phase_scan.joint_phase_scan import resolve_joint_phase_scan
from half_linac.src.apps.rf_phase_scan.phase_energy_scan import phase_difference_deg
from half_linac.src.apps.rf_phase_scan.scan_widgets import (
    parameter_card, prepare_scan_axes, scan_actions, scan_page_layout,
    scan_workspace, station_selector,
)
from half_linac.src.apps.rf_phase_scan.transmission_recovery import (
    TransmissionRecoveryCallbacks,
    TransmissionRecoveryRunner,
    TransmissionRecoverySettings,
    discover_recovery_stages,
    restore_recovery_snapshot,
    settings_from_config,
)
from half_linac.src.shared.machine_profile import (
    make_runtime_run_id,
    workflow_writes_allowed,
)


class TransmissionRecoveryThread(QThread):
    item = pyqtSignal(int, dict)
    point = pyqtSignal(int, dict)
    state = pyqtSignal(str)
    done = pyqtSignal(dict)

    def __init__(self, runner_factory):
        super().__init__()
        self.runner_factory = runner_factory
        self.runner = None
        self.stop_requested = False

    def run(self):
        callbacks = TransmissionRecoveryCallbacks(
            item=self.item.emit,
            point=self.point.emit,
            state=self.state.emit,
        )
        self.runner = self.runner_factory(callbacks)
        if self.stop_requested:
            self.runner.stop()
        self.done.emit(self.runner.run())

    def stop(self):
        self.stop_requested = True
        if self.runner is not None:
            self.runner.stop()


class TransmissionRecoveryRestoreThread(QThread):
    done = pyqtSignal(object, str)

    def __init__(self, operation):
        super().__init__()
        self.operation = operation

    def run(self):
        try:
            self.done.emit(self.operation(), "")
        except Exception as exc:
            self.done.emit(None, str(exc))


class AccelerationCheckThread(QThread):
    state = pyqtSignal(str)

    def __init__(self, row, **options):
        super().__init__()
        self.row = row
        self.check = AccelerationCheck(on_state=self.state.emit, **options)
        self.result = None

    def run(self):
        try:
            self.result = self.check.run()
        except Exception as exc:
            self.result = dict(self.check.result, error=str(exc))
            self.result["status"] = "undetermined"

    def stop(self):
        self.check.stop()


class TransmissionRecoveryPage(QWidget):
    acceleration_phase_changed = pyqtSignal(int, object, object, object)
    COLUMNS = (
        "Use",
        "LLRF",
        "Target BPM",
        "Initial",
        "Peak",
        "Peak SUM",
        "Applied",
        "Status",
        "Acceleration",
    )
    ACCELERATION_COLUMN = len(COLUMNS) - 1
    ACCELERATION_LABELS = {
        "accelerating": "Accelerating",
        "decelerating": "Decelerating",
        "undetermined": "Undetermined",
    }
    ACCELERATION_COLORS = {
        "accelerating": "#49c488",
        "decelerating": "#ff707e",
        "undetermined": "#a5aeb6",
    }

    def __init__(
        self,
        *,
        window,
        context,
        workflow,
        runtime_paths: dict[str, Path],
        write_enabled: bool,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("transmissionRecoveryPage")
        self.window = window
        self.context = context
        self.workflow = dict(workflow)
        self.config = dict(self.workflow.get("transmission_recovery", {}))
        self.runtime_paths = dict(runtime_paths)
        self.write_enabled = bool(write_enabled)
        resolution_mode = context.control_backend.name
        if not self.write_enabled:
            configured_modes = tuple(self.workflow.get("control_backends", ()))
            writable_modes = tuple(
                mode
                for mode in configured_modes
                if workflow_writes_allowed(
                    context, "rf_phase_scan", mode=mode
                )
            )
            available_modes = writable_modes or configured_modes
            if available_modes:
                resolution_mode = available_modes[0]
        self.candidates = discover_recovery_stages(
            context, self.config, mode=resolution_mode
        )
        self.default_settings = settings_from_config(self.config)
        self.check_configs, self.check_unavailable = discover_acceleration_checks(
            context, self.config, {stage.rf_element: stage for stage in self.candidates},
            self.default_settings,
        )
        self.capture_specs = {
            item["rf_element"]: item["acceleration_check"]
            for item in self.config["stages"]
            if isinstance(item.get("acceleration_check"), dict)
            and item["acceleration_check"].get("capture_enabled") is True
        }
        self.response_specs = {}
        self.response_default_steps = {}
        self.response_unavailable = {}
        self.corrector_steps = {}
        if self.write_enabled:
            for stage, item in zip(self.candidates, self.config["stages"]):
                spec = item.get("acceleration_check")
                if not isinstance(spec, dict) or "phase_response" not in spec:
                    continue
                try:
                    route = resolve_joint_phase_scan(context, stage, self.default_settings, spec)
                except (KeyError, ValueError, TypeError, OverflowError) as exc:
                    self.response_unavailable[stage.rf_element] = str(exc)
                else:
                    self.response_specs[stage.rf_element] = spec
                    self.response_default_steps[stage.rf_element] = route.response.corrector.step
        self.worker = None
        self.restore_worker = None
        self.check_worker = None
        self.phase_monitors = {}
        self.closing = False
        self.selected_rows: list[int] = []
        self.current_task_index = None
        self.current_points: list[dict[str, Any]] = []
        self.current_applied_index = None
        self.last_result: dict[str, Any] | None = None
        self.last_stages = ()
        self.last_settings = None
        self.palette = {
            "plot": "#11181e",
            "grid": "#2a3943",
            "trace": "#6cb6ff",
            "fit": "#ff5f6d",
            "muted": "#90a1ad",
            "border": "#24333d",
        }

        root, self.summary = scan_page_layout(self, "Transmission Recovery")
        self.summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.half_range = self._double_spin(
            self.default_settings.half_range_deg, 0.1, 180.0
        )
        self.points = QSpinBox(self)
        self.points.setRange(3, 721)
        self.points.setSingleStep(2)
        self.points.setValue(self.default_settings.points)
        self.settle = self._double_spin(
            self.default_settings.settle_time_s, 0.0, 60.0
        )
        self.settle.setSuffix(" s")
        self.samples = QSpinBox(self)
        self.samples.setRange(
            self.default_settings.min_valid_samples,
            100,
        )
        self.samples.setValue(self.default_settings.samples_per_point)
        self.sample_interval = self._double_spin(
            self.default_settings.sample_interval_s, 0.0, 60.0
        )
        self.sample_interval.setSuffix(" s")
        self.use_acceleration = QCheckBox("Use Accel / Decel", self)
        self.use_acceleration.setObjectName("useAccelerationInPhaseScan")
        self.use_acceleration.setEnabled(bool(self.response_specs))
        if self.response_specs:
            response_tip = (
                "Measure corrector response during the phase scan and apply a confirmed "
                f"accelerating phase with high BPM SUM. Available for {', '.join(self.response_specs)}."
            )
        elif self.response_unavailable:
            response_tip = "\n".join(
                f"{rf} · {error}" for rf, error in self.response_unavailable.items()
            )
        else:
            response_tip = "No phase-response path is available for this control backend."
        self.use_acceleration.setToolTip(response_tip)
        parameter_widgets = (
            ("Phase range", self._range_widget()),
            ("Points", self.points),
            ("Settle time", self.settle),
            ("Samples / point", self.samples),
            ("Sample interval", self.sample_interval),
            ("Phase selection", self.use_acceleration),
        )
        parameters = parameter_card(self, parameter_widgets)
        self.check_button = QPushButton("Check Accel / Decel", parameters)
        self.check_status = QLabel(parameters)
        self.check_status.setBuddy(self.check_button)
        self.capture_button = QPushButton("Capture Test Data…", parameters)
        self.capture_status = QLabel(parameters)
        self.capture_status.setBuddy(self.capture_button)
        fields = parameters.layout()
        check_column = len(parameter_widgets)
        fields.addWidget(self.check_status, 0, check_column)
        fields.addWidget(self.check_button, 1, check_column)
        fields.addWidget(self.capture_status, 0, check_column + 1)
        fields.addWidget(self.capture_button, 1, check_column + 1)
        fields.setColumnStretch(check_column, 0)
        fields.setColumnStretch(check_column + 2, 1)
        root.addWidget(parameters)

        self.all_button = QPushButton("All", self)
        self.clear_button = QPushButton("Clear", self)
        self.table = QTableWidget(len(self.candidates), len(self.COLUMNS), self)
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        splitter, self.plot, self.plot_title = scan_workspace(
            self, self.table, f"Stages · {len(self.candidates)} configured",
            (42, 68, 86, 65, 65, 89, 70, 96), (self.all_button, self.clear_button),
            "Current phase scan",
        )
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.stage_checkboxes: list[QCheckBox] = []
        for row, stage in enumerate(self.candidates):
            selector = station_selector(self.table, row, checked=True)
            selector.stateChanged.connect(lambda _state: self._update_summary())
            self.stage_checkboxes.append(selector)
            self.table.setItem(row, 1, QTableWidgetItem(stage.rf_element))
            self.table.setItem(row, 2, QTableWidgetItem(stage.target_bpm))
            for column in range(3, len(self.COLUMNS)):
                self.table.setItem(row, column, QTableWidgetItem("—"))
            self._reset_check(row)
        root.addWidget(splitter, 1)

        self.start_button = QPushButton("Start Selected", self)
        self.start_button.setObjectName("startButton")
        self.stop_button = QPushButton("Stop", self)
        self.stop_button.setObjectName("stopButton")
        self.restore_button = QPushButton("Restore Snapshot", self)
        self.stop_button.setEnabled(False)
        self.restore_button.setEnabled(False)
        actions, self.progress = scan_actions(
            self, (self.start_button, self.stop_button, self.restore_button),
            len(self.candidates),
        )
        root.addLayout(actions)

        self.all_button.clicked.connect(lambda: self._select_all(True))
        self.clear_button.clicked.connect(lambda: self._select_all(False))
        self.start_button.clicked.connect(self._start)
        self.stop_button.clicked.connect(self.stop)
        self.restore_button.clicked.connect(self._restore_snapshot)
        self.check_button.clicked.connect(self._start_acceleration_check)
        self.capture_button.clicked.connect(self._start_acceleration_capture)
        self.use_acceleration.toggled.connect(self._update_response_mode)
        self.table.itemSelectionChanged.connect(self._update_check_button)
        self.acceleration_phase_changed.connect(self._check_phase_changed, Qt.QueuedConnection)
        for control in (
            self.half_range,
            self.points,
            self.settle,
            self.samples,
            self.sample_interval,
        ):
            control.valueChanged.connect(self._update_summary)

        self._draw_plot()
        self._update_summary()
        if not self.write_enabled:
            self.start_button.setEnabled(False)
            self.summary.setText(
                "Unavailable: RF writes are blocked for the current control backend."
            )

    def _double_spin(self, value, minimum, maximum):
        widget = QDoubleSpinBox(self)
        widget.setRange(minimum, maximum)
        widget.setDecimals(2)
        widget.setSingleStep(0.1)
        widget.setMinimumWidth(96)
        widget.setValue(float(value))
        return widget

    def _range_widget(self):
        container = QWidget(self)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        layout.addWidget(QLabel("±", container))
        layout.addWidget(self.half_range)
        layout.addWidget(QLabel("deg", container))
        return container

    def _selected_rows(self):
        return [
            row
            for row, selector in enumerate(self.stage_checkboxes)
            if selector.isChecked() and (
                not self.use_acceleration.isChecked()
                or self.candidates[row].rf_element in self.response_specs
            )
        ]

    def _select_all(self, checked):
        for selector in self.stage_checkboxes:
            if not selector.isEnabled():
                continue
            signals_were_blocked = selector.blockSignals(True)
            selector.setChecked(bool(checked))
            selector.blockSignals(signals_were_blocked)
        self._update_summary()

    def _update_response_mode(self):
        joint = self.use_acceleration.isChecked()
        for stage, selector in zip(self.candidates, self.stage_checkboxes):
            available = stage.rf_element in self.response_specs
            selector.setEnabled(not joint or available)
            selector.setToolTip(
                self.response_unavailable.get(
                    stage.rf_element, "No phase-response path configured for this station."
                ) if joint and not available else ""
            )
        self._update_summary()

    def select_stations(self, stations):
        for stage, selector in zip(self.candidates, self.stage_checkboxes):
            previous = selector.blockSignals(True)
            selector.setChecked(stage.rf_element in stations)
            selector.blockSignals(previous)
        self._update_summary()

    def _settings(self):
        settings = TransmissionRecoverySettings(
            half_range_deg=float(self.half_range.value()),
            points=int(self.points.value()),
            settle_time_s=float(self.settle.value()),
            samples_per_point=int(self.samples.value()),
            sample_interval_s=float(self.sample_interval.value()),
            min_valid_samples=self.default_settings.min_valid_samples,
            readback_channel=self.default_settings.readback_channel,
            readback_tolerance_deg=self.default_settings.readback_tolerance_deg,
            readback_timeout_s=self.default_settings.readback_timeout_s,
            readback_poll_interval_s=(
                self.default_settings.readback_poll_interval_s
            ),
        )
        settings.validate()
        return settings

    def _update_summary(self):
        self._update_check_button()
        if not self.write_enabled:
            return
        rows = self._selected_rows()
        try:
            settings = self._settings()
            per_point = settings.settle_time_s + (
                settings.samples_per_point - 1
            ) * settings.sample_interval_s
            minutes = len(rows) * (
                settings.points * per_point + settings.settle_time_s
            ) / 60.0
            if self.use_acceleration.isChecked():
                names = ", ".join(self.candidates[row].rf_element for row in rows)
                detail = (
                    f"{names} selected · BPM SUM and corrector response at each usable phase."
                    if rows else f"Select a station with a phase-response path · {', '.join(self.response_specs)}."
                )
            else:
                detail = (
                    f"{len(rows)} selected · approximately {minutes:.1f} min · "
                    "each accepted BPM SUM maximum will be applied immediately."
                )
        except ValueError as exc:
            detail = f"Invalid settings · {exc}"
        self.summary.setText(detail)
        self.start_button.setEnabled(
            bool(rows)
            and not self.is_busy()
            and self.write_enabled
        )

    def _set_running(self, running):
        self.table.setEnabled(not running)
        for widget in (
            self.half_range,
            self.points,
            self.settle,
            self.samples,
            self.sample_interval,
            self.all_button,
            self.clear_button,
        ):
            widget.setEnabled(not running)
        self.use_acceleration.setEnabled(not running and bool(self.response_specs))
        self.start_button.setEnabled(
            not running and bool(self._selected_rows()) and self.write_enabled
        )
        self.stop_button.setEnabled(
            running and (self.worker is not None or self.check_worker is not None)
        )
        self.restore_button.setEnabled(
            not running and self.last_result is not None
        )
        self.window._set_recovery_running(running)
        self._update_check_button()

    def _confirm_joint_scan(self, stages, routes):
        dialog = QDialog(self)
        dialog.setWindowTitle("Start Transmission Recovery")
        layout = QFormLayout(dialog)
        description = QLabel(
            "LLRF AMP stays fixed. The corrector is stepped to both sides of its "
            "initial current at each usable phase. Confirmed accelerating points "
            "with high BPM SUM can be applied.",
            dialog,
        )
        description.setWordWrap(True)
        layout.addRow(description)
        controls = {}
        for stage in stages:
            response = routes[stage.rf_element].response
            corrector = response.corrector
            low, high = corrector.limits
            step = self._double_spin(0, 0, (high - low) / 2)
            step.setDecimals(3)
            step.setMinimum(0.001)
            step.setValue(self.corrector_steps.get(stage.rf_element, corrector.step))
            step.setSuffix(" A")
            step.setPrefix("± ")
            step.setToolTip("Corrector current step per side")
            layout.addRow(
                f"{stage.rf_element} · {stage.target_bpm} SUM · {corrector.element} → {response.bpm}",
                step,
            )
            controls[stage.rf_element] = step
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dialog)
        buttons.button(QDialogButtonBox.Ok).setText("Start Scan")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        steps = None
        if dialog.exec_() == QDialog.Accepted:
            steps = {rf: control.value() for rf, control in controls.items()}
        dialog.deleteLater()
        return steps

    def _start(self):
        if (
            self.is_busy()
            or not self.write_enabled
            or self.window._amp_busy()
            or self.window.thread is not None
            or (
                self.window.batch_dialog is not None
                and self.window.batch_dialog.worker is not None
            )
        ):
            return
        rows = self._selected_rows()
        if not rows:
            return
        try:
            settings = self._settings()
        except ValueError as exc:
            QMessageBox.warning(self, "Transmission Recovery", str(exc))
            return
        stages = tuple(self.candidates[row] for row in rows)
        routes = None
        if self.use_acceleration.isChecked():
            try:
                routes = {
                    stage.rf_element: resolve_joint_phase_scan(
                        self.context, stage, settings, self.response_specs[stage.rf_element],
                    )
                    for stage in stages
                }
                steps = self._confirm_joint_scan(stages, routes)
                if steps is None:
                    return
                routes = {
                    rf: route.with_corrector_step(steps[rf]) for rf, route in routes.items()
                }
            except (KeyError, ValueError, TypeError, OverflowError) as exc:
                QMessageBox.warning(self, "Transmission Recovery", str(exc))
                return
            self.corrector_steps.update(steps)
        else:
            mapping = "\n".join(f"{stage.rf_element} → {stage.target_bpm}" for stage in stages)
            if QMessageBox.question(
                self,
                "Start Transmission Recovery",
                f"Scan and apply {len(stages)} selected RF phases in configured order?\n\n{mapping}",
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Cancel,
            ) != QMessageBox.Yes:
                return

        self.selected_rows = list(rows)
        self.last_result = None
        self.last_stages = stages
        self.last_settings = settings
        self.current_task_index = None
        self.current_points = []
        self.current_applied_index = None
        for row in range(len(self.candidates)):
            for column in range(3, self.ACCELERATION_COLUMN):
                self.table.item(row, column).setText("—")
            self._reset_check(row)
            if row not in rows:
                self.table.item(row, 7).setText("Not Selected")
        self.progress.setRange(0, len(stages))
        self.progress.setValue(0)
        self.restore_button.setEnabled(False)
        self._draw_plot()

        run_dir = self.runtime_paths["runs_dir"] / make_runtime_run_id(
            "transmission_recovery", uuid4().hex[:8]
        )

        def runner_factory(callbacks):
            return TransmissionRecoveryRunner(
                context=self.context,
                candidates=self.candidates,
                stages=stages,
                settings=settings,
                directory=run_dir,
                callbacks=callbacks,
                configuration_snapshot=self.config,
                response_routes=routes,
            )

        self.worker = TransmissionRecoveryThread(runner_factory)
        self.worker.item.connect(self._item)
        self.worker.point.connect(self._point)
        self.worker.state.connect(self._state)
        self.worker.done.connect(self._done)
        self._set_running(True)
        self.summary.setText("Preparing Transmission Recovery...")
        self.worker.start()

    def stop(self):
        if self.check_worker is not None:
            self.check_worker.stop()
            self.stop_button.setEnabled(False)
            self.summary.setText("Stopping · restoring LLRF AMP and corrector current…")
        elif self.worker is not None:
            self.worker.stop()
            self.stop_button.setEnabled(False)
            self.summary.setText(
                "Stopping · restoring the current LLRF phase..."
            )

    def stop_and_wait(self, timeout_ms):
        if self.check_worker is not None and self.check_worker.isRunning():
            self.check_worker.stop()
            return self.check_worker.wait(timeout_ms)
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
            return self.worker.wait(timeout_ms)
        if self.restore_worker is not None and self.restore_worker.isRunning():
            return self.restore_worker.wait(timeout_ms)
        return True

    def is_running(self):
        return any(
            worker is not None and worker.isRunning()
            for worker in (self.worker, self.restore_worker, self.check_worker)
        )

    def is_busy(self):
        return any(worker is not None for worker in (self.worker, self.restore_worker, self.check_worker))

    def _highlighted_row(self):
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if len(rows) == 1 else None

    def _selected_check_item(self):
        row = self._highlighted_row()
        if row is None or not self.last_result:
            return None
        rf = self.candidates[row].rf_element
        for record in self.last_result.get("items", ()):
            if record["rf_element"] == rf and record["status"] == "applied":
                return row, record
        return None

    def _update_check_button(self):
        row = self._highlighted_row()
        rf = self.candidates[row].rf_element if row is not None else None
        ready = False
        if self.check_worker is not None:
            status = "Checking…"
            message = "Checking the applied phase…"
        elif self.is_busy():
            status = "Busy"
            message = "Wait for the current operation to finish."
        elif rf in self.check_unavailable:
            status = "Path unavailable"
            message = f"{rf} · {self.check_unavailable[rf]}"
        elif not self.check_configs:
            status = "No calibrated path"
            message = "Unavailable · No RF station has an enabled, calibrated measurement path."
        elif not self.write_enabled:
            status = "Writes blocked"
            message = "Unavailable · RF writes are blocked for this control backend."
        elif self._selected_check_item() is None:
            status = "Select an applied RF"
            message = "Select an RF station with an applied phase."
        else:
            ready = True
            status = "Ready"
            message = f"{rf} · Ready to check the applied phase."
        self.check_status.setText(status)
        self.check_status.setToolTip(message)
        self.check_button.setEnabled(ready)
        self.check_button.setToolTip(message)
        self._update_capture_button(rf)

    def _update_capture_button(self, rf):
        enabled = self.write_enabled and not self.is_busy() and rf in self.capture_specs
        if not self.capture_specs:
            status = "No test path"
        elif not self.write_enabled:
            status = "Writes blocked"
        elif self.is_busy():
            status = "Busy"
        elif rf in self.capture_specs:
            status = "Current phase"
        elif len(self.capture_specs) == 1:
            status = f"Select {next(iter(self.capture_specs))}"
        else:
            status = "Select a test station"
        self.capture_status.setText(status)
        self.capture_button.setEnabled(enabled)
        self.capture_button.setToolTip(
            "Capture BPM position and SUM at the current RF phase for calibration."
        )

    def _reset_check(self, row, reason=None):
        monitor = self.phase_monitors.pop(row, None)
        if monitor is not None:
            monitor[1].disconnect()
        rf = self.candidates[row].rf_element
        item = self.table.item(row, self.ACCELERATION_COLUMN)
        item.setText(self.ACCELERATION_LABELS["undetermined"])
        item.setToolTip(reason or self.check_unavailable.get(rf, "Check has not been run."))

    def _start_acceleration_check(self):
        selection = self._selected_check_item()
        if (
            selection is None or not self.check_button.isEnabled()
            or self.window._amp_busy() or self.window._phase_busy()
        ):
            return
        row, record = selection
        run_path = Path(self.last_result["json_path"])
        self._launch_acceleration_check(
            row, self.check_configs[record["rf_element"]],
            applied_phase=record["applied_phase_deg"], recovery_path=run_path,
            path=run_path.parent / f"acceleration_check_{uuid4().hex}.json",
        )

    def _capture_steps(self, stage, spec):
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Capture Test Data · {stage.rf_element}")
        layout = QFormLayout(dialog)
        description = QLabel(
            f"Use the current phase with beam at {spec['bpm']}. "
            f"The capture restores {stage.rf_element} AMP and {spec['corrector']} current.",
            dialog,
        )
        description.setWordWrap(True)
        layout.addRow(description)
        amplitude = self._double_spin(0, 0, 100)
        amplitude.setDecimals(3)
        current = self._double_spin(0, 0, 100)
        current.setDecimals(3)
        default_current_step = self.response_default_steps.get(stage.rf_element, 0)
        current.setValue(self.corrector_steps.get(stage.rf_element, default_current_step))
        layout.addRow("LLRF AMP drop (percentage points)", amplitude)
        layout.addRow("Corrector step per side (A)", current)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dialog)
        start = buttons.button(QDialogButtonBox.Ok)
        start.setText("Start Capture")
        start.setEnabled(False)

        def update_start():
            start.setEnabled(amplitude.value() > 0 and current.value() > 0)

        amplitude.valueChanged.connect(update_start)
        current.valueChanged.connect(update_start)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        accepted = dialog.exec_() == QDialog.Accepted
        steps = (amplitude.value(), current.value()) if accepted else None
        dialog.deleteLater()
        return steps

    def _start_acceleration_capture(self):
        row = self._highlighted_row()
        if (
            row is None or not self.capture_button.isEnabled()
            or self.window._amp_busy() or self.window._phase_busy()
        ):
            return
        stage = self.candidates[row]
        spec = self.capture_specs[stage.rf_element]
        steps = self._capture_steps(stage, spec)
        if steps is None or self.window._amp_busy() or self.window._phase_busy():
            return
        try:
            config = resolve_acceleration_capture(
                self.context, stage, self._settings(), spec,
                amplitude_step=steps[0], current_step=steps[1],
            )
        except (KeyError, TypeError, ValueError) as exc:
            QMessageBox.warning(self, "Capture Test Data", str(exc))
            return
        self.corrector_steps[stage.rf_element] = steps[1]
        run_dir = self.runtime_paths["runs_dir"] / make_runtime_run_id(
            "acceleration_capture", uuid4().hex[:8]
        )
        self._launch_acceleration_check(
            row, config, applied_phase=None, recovery_path=None, path=run_dir / "run.json"
        )

    def _launch_acceleration_check(self, row, config, *, applied_phase, recovery_path, path):
        self._reset_check(row, "Check running.")
        self.check_worker = AccelerationCheckThread(
            row,
            context=self.context,
            config=config,
            applied_phase=applied_phase,
            recovery_path=recovery_path,
            path=path,
        )
        self.check_worker.state.connect(self._state)
        self.check_worker.finished.connect(self._acceleration_done)
        self._set_running(True)
        operation = "test data capture" if config.capture_only else "acceleration check"
        self.summary.setText(f"Preparing {operation} · {config.rf_element}")
        self.check_worker.start()

    def _acceleration_done(self):
        worker = self.check_worker
        self.check_worker = None
        result = worker.result
        row = worker.row
        item = self.table.item(row, self.ACCELERATION_COLUMN)
        label = self.ACCELERATION_LABELS[result["status"]]
        item.setText(label)
        detail = result["reason"] or "No usable result."
        if result.get("error"):
            detail = f"{detail}\nLog error: {result['error']}"
        if result["restore_errors"]:
            restore_detail = "\n".join(result["restore_errors"].values())
            detail = f"{detail}\nRestore failed:\n{restore_detail}"
        if result.get("measured_phase_deg") is not None:
            detail = f"{detail}\nMeasured phase: {result['measured_phase_deg']:.3f}°"
        if result.get("mode") == "capture" and not result.get("error"):
            detail = f"{detail}\nData file: {result['json_path']}"
        item.setToolTip(detail)
        self._set_running(False)
        if result.get("capture_completed") and not result.get("error"):
            power = {
                "lower_at_low_amp": "KLY forward power fell at lower AMP",
                "higher_at_low_amp": "KLY forward power rose at lower AMP",
                "overlap": "KLY forward power samples overlap",
                "insufficient_samples": "KLY forward power samples incomplete",
            }.get(result.get("kly_forward_power_check"))
            heading = f"{result['rf_element']} · Test data saved"
            if power:
                heading = f"{heading} · {power}"
            self.summary.setText(f"{heading}\n{result['json_path']}")
        else:
            self.summary.setText(f"{result['rf_element']} · {label} · {detail.splitlines()[0]}")
        if result["restore_errors"]:
            QMessageBox.warning(self, "Acceleration Check Restore", detail)
        if result["status"] != "undetermined" and not self.closing:
            self._watch_checked_phase(row, result)
        worker.deleteLater()

    def _watch_checked_phase(self, row, result):
        import epics

        config = self.check_configs[result["rf_element"]]
        token = object()

        def readback_changed(value=None, severity=None, **_):
            self.acceleration_phase_changed.emit(row, token, value, severity)

        def connection_changed(conn=None, **_):
            if not conn:
                readback_changed()

        try:
            pv = epics.PV(
                config.phase_readback_pv, form="time", auto_monitor=True,
                callback=readback_changed, connection_callback=connection_changed,
            )
        except Exception as exc:
            self._reset_check(row, f"Phase monitor is unavailable: {exc}")
            return
        self.phase_monitors[row] = (token, pv, result["applied_phase_deg"], config.phase_tolerance_deg)

    def _check_phase_changed(self, row, token, value, severity):
        monitor = self.phase_monitors.get(row)
        if monitor is None or monitor[0] is not token:
            return
        try:
            value = float(value)
        except (TypeError, ValueError, OverflowError):
            value = math.nan
        if severity != 0 or not math.isfinite(value):
            self._reset_check(row, "Current RF phase is unavailable.")
        elif abs(phase_difference_deg(value, monitor[2])) > monitor[3]:
            self._reset_check(row, "RF phase changed after the check.")

    def stop_phase_monitors(self):
        self.closing = True
        for row in tuple(self.phase_monitors):
            self._reset_check(row)

    @staticmethod
    def _number(value, suffix="", precision=2):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "—"
        if not math.isfinite(number):
            return "—"
        return f"{number:.{precision}f}{suffix}"

    def _item(self, task_index, record):
        if task_index >= len(self.selected_rows):
            return
        row = self.selected_rows[task_index]
        values = {
            3: self._number(record.get("initial_phase_deg"), "°"),
            4: self._number(record.get("peak_phase_deg"), "°"),
            5: self._number(record.get("peak_sum"), precision=3),
            6: self._number(record.get("applied_phase_deg"), "°"),
            7: str(record.get("status", "—")).replace("_", " ").title(),
        }
        for column, value in values.items():
            self.table.item(row, column).setText(value)
        message = record.get("error") or ""
        self.table.item(row, 7).setToolTip(str(message))
        analysis = record.get("acceleration_analysis")
        if analysis:
            acceleration = self.table.item(row, self.ACCELERATION_COLUMN)
            applied = record.get("status") == "applied"
            retained = record.get("status") == "undetermined"
            status = "undetermined"
            if applied:
                status = analysis["status"]
            elif retained:
                status = record.get("initial_phase_acceleration", "undetermined")
            acceleration.setText(self.ACCELERATION_LABELS[status])
            if applied:
                acceleration.setToolTip(
                    f"Applied BPM SUM {record['selected_sum']:.3f}\n"
                    f"Corrector response {record['selected_response']:.4g} BPM units/A"
                )
            elif retained:
                acceleration.setToolTip(f"Initial phase retained. {analysis['reason']}")
            else:
                acceleration.setToolTip(analysis.get("reason") or message)
            if task_index == self.current_task_index:
                self.current_points = list(record["points"])
                self.current_applied_index = (
                    record.get("selected_acquisition_index") if applied else None
                )
                self._draw_plot()
        elif message and "response_snapshot" in record:
            self.table.item(row, self.ACCELERATION_COLUMN).setToolTip(str(message))
        if record.get("status") == "scanning":
            self.current_task_index = task_index
            self.current_points = []
            self.current_applied_index = None
            self.plot_title.setText(
                f"{record['rf_element']} → {record['target_bpm']}"
            )
            self._draw_plot()
        completed = sum(
            self.table.item(selected_row, 7).text()
            not in {"—", "Pending", "Scanning", "Applying"}
            for selected_row in self.selected_rows
        )
        self.progress.setValue(completed)

    def _point(self, task_index, point):
        if task_index != self.current_task_index:
            self.current_task_index = task_index
            self.current_points = []
            self.current_applied_index = None
        self.current_points.append(dict(point))
        self._draw_plot()

    def _state(self, state):
        self.summary.setText(str(state))

    def _done(self, result):
        worker = self.worker
        self.worker = None
        self.last_result = dict(result)
        self._set_running(False)
        status = str(result.get("status", "failed")).replace("_", " ").title()
        detail = result.get("error") or result.get("json_path", "")
        self.summary.setText(f"{status} · {detail}")
        self.progress.setValue(self.progress.maximum())
        self.restore_button.setEnabled(
            bool(result.get("initial_snapshot")) and bool(self.last_stages)
        )
        if worker is not None:
            worker.deleteLater()

    def _restore_snapshot(self):
        if (
            self.last_result is None
            or not self.last_stages
            or self.last_settings is None
            or self.is_busy()
            or self.window._amp_busy()
        ):
            return
        if QMessageBox.question(
            self,
            "Restore Transmission Recovery Snapshot",
            f"Restore the pre-run phases for {len(self.last_stages)} selected LLRFs?",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        ) != QMessageBox.Yes:
            return
        for row in self.selected_rows:
            self._reset_check(row, "Phase snapshot restore requested.")

        snapshot = self.last_result.get("initial_snapshot")
        expected = dict(self.last_result.get("expected_phases_deg") or {})

        def operation():
            return restore_recovery_snapshot(
                context=self.context,
                stages=self.last_stages,
                settings=self.last_settings,
                snapshot=snapshot,
                expected_phases=expected,
            )

        self.restore_worker = TransmissionRecoveryRestoreThread(operation)
        self.restore_worker.done.connect(self._restore_done)
        self._set_running(True)
        self.stop_button.setEnabled(False)
        self.summary.setText("Restoring the pre-run phase snapshot...")
        self.restore_worker.start()

    def _restore_done(self, restored, error):
        worker = self.restore_worker
        self.restore_worker = None
        self._set_running(False)
        if error:
            self.summary.setText(f"Snapshot restore failed · {error}")
            QMessageBox.warning(self, "Snapshot Restore", error)
        else:
            restored = dict(restored or {})
            for row, stage in enumerate(self.candidates):
                if stage.rf_element in restored:
                    value = restored[stage.rf_element]
                    self.table.item(row, 6).setText(
                        self._number(value, "°")
                    )
                    self.table.item(row, 7).setText("Restored")
            self.summary.setText(
                f"Snapshot restored · {len(restored)} LLRF phases."
            )
            self.restore_button.setEnabled(False)
            self.last_result = None
            self._update_check_button()
        if worker is not None:
            worker.deleteLater()

    def apply_theme(self, palette):
        self.palette = dict(palette)
        self._draw_plot()

    def _draw_plot(self):
        valid = [
            point
            for point in self.current_points
            if point.get("median_sum") is not None
        ]
        axes = prepare_scan_axes(self.plot, self.palette, has_data=bool(valid))
        if valid:
            axes.set_xlabel("Phase offset (deg)", color=self.palette["muted"])
            axes.set_ylabel("BPM SUM median", color=self.palette["muted"])
            ordered = sorted(valid, key=lambda point: float(point["offset_deg"]))
            x_values = [float(point["offset_deg"]) for point in ordered]
            y_values = [float(point["median_sum"]) for point in ordered]
            axes.plot(
                x_values,
                y_values,
                "o-",
                color=self.palette["trace"],
                linewidth=1.8,
                markersize=5,
            )
            if any("acceleration" in point for point in ordered):
                for status, color in self.ACCELERATION_COLORS.items():
                    classified = [point for point in ordered if point.get("acceleration") == status]
                    if classified:
                        axes.scatter(
                            [point["offset_deg"] for point in classified],
                            [point["median_sum"] for point in classified],
                            color=color, label=self.ACCELERATION_LABELS[status], s=32, zorder=3,
                        )
                selected = next(
                    (point for point in ordered if point["acquisition_index"] == self.current_applied_index),
                    None,
                )
                if selected is not None:
                    axes.scatter(
                        [selected["offset_deg"]], [selected["median_sum"]],
                        marker="*", color=self.ACCELERATION_COLORS["accelerating"],
                        s=130, label="Applied phase", zorder=4,
                    )
                axes.legend(
                    loc="best", facecolor=self.palette["plot"], edgecolor=self.palette["border"],
                    labelcolor=self.palette["muted"],
                )
            else:
                peak = max(ordered, key=lambda point: float(point["median_sum"]))
                axes.scatter(
                    [float(peak["offset_deg"])],
                    [float(peak["median_sum"])],
                    color=self.palette["fit"],
                    s=55,
                    zorder=3,
                )
        self.plot.fig.tight_layout()
        self.plot.canvas.draw_idle()

