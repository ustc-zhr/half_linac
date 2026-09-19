"""Operator window for the coupled centering workflow."""
from dataclasses import replace

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
    QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QAbstractItemView, QHeaderView,
    QFrame, QSplitter, QWidget, QTabWidget, QProgressBar, QScrollArea,
)

from half_linac.src.apps.solenoid_centering.joint import JointScanner, load_joint_plans
from half_linac.src.apps.solenoid_centering.scan import StopRequested
from half_linac.src.apps.solenoid_centering.gui.theme import build_stylesheet, theme_palette
from half_linac.src.shared.app_theme import resolve_initial_theme


class JointWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(object)
    progress = pyqtSignal(str)

    def __init__(self, action, parent):
        super().__init__(parent)
        self.action = action
        self.stop_requested = False

    def run(self):
        try:
            self.completed.emit(self.action())
        except Exception as exc:
            self.failed.emit(exc)


class JointCenteringDialog(QDialog):
    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.context = context
        self.plans = load_joint_plans(context)
        self.worker = None
        self.scanner = None
        self.result = None
        self.ready = False
        self.setWindowTitle(f"Joint Solenoid Centering — {context.machine.display_name} / {context.control_backend.name}")
        self.operation = ""
        self.resize(1180, 800)
        self.setMinimumSize(960, 680)
        self.palette_colors = theme_palette(getattr(parent, "current_theme", resolve_initial_theme()))
        self.setStyleSheet("QWidget { font-size: 12px; }\n" + build_stylesheet(self.palette_colors))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Joint Solenoid Centering")
        title.setObjectName("summaryTitle")
        header.addWidget(title)
        header.addStretch()
        context_label = QLabel(f"{context.machine.display_name}  /  {context.control_backend.name.upper()}")
        context_label.setProperty("muted", True)
        header.addWidget(context_label)
        self.state_label = QLabel("NOT CHECKED")
        self.state_label.setProperty("role", "statusValue")
        header.addSpacing(16)
        header.addWidget(self.state_label)
        layout.addLayout(header)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        layout.addWidget(self.splitter, 1)
        configuration = QFrame()
        configuration.setObjectName("controlCard")
        config_layout = QVBoxLayout(configuration)
        config_layout.setContentsMargins(14, 14, 14, 14)
        config_layout.setSpacing(10)
        heading = QLabel("Scan configuration")
        heading.setObjectName("panelTitle")
        config_layout.addWidget(heading)
        self.group = QComboBox()
        self.group.setToolTip("Select the solenoids to center together.")
        for plan in self.plans:
            self.group.addItem(plan.display_name)
        config_layout.addWidget(self.group)
        self.targets = self._table(["Solenoid", "BPMs", "Modulation ±A"])
        config_layout.addWidget(self.targets)
        self.corrector_summary = QLabel()
        self.corrector_summary.setWordWrap(True)
        self.corrector_summary.setProperty("muted", True)
        config_layout.addWidget(self.corrector_summary)

        heading = QLabel("Correction limits")
        heading.setObjectName("sectionTitle")
        config_layout.addWidget(heading)
        form = QFormLayout()
        form.setVerticalSpacing(10)
        self.probe = self._spin(0.001, 5)
        self.step = self._spin(0.001, 5)
        self.excursion = self._spin(0.001, 5)
        self.floor = self._spin(0.0001, 10, decimals=4)
        self.iterations = QSpinBox()
        self.iterations.setRange(1, 10)
        for label, widget, tip in (
            ("Corrector probe (±A)", self.probe, "Positive and negative offsets used to measure the response matrix."),
            ("Max. step (A)", self.step, "Maximum current change in one iteration."),
            ("Max. total offset (A)", self.excursion, "Maximum deviation from the initial corrector current."),
            ("Response tolerance (mm)", self.floor, "Confirm this threshold against measured BPM noise."),
            ("Max. iterations", self.iterations, "Reuse the measured response matrix for this many iterations."),
        ):
            widget.setMaximumWidth(150)
            widget.setToolTip(tip)
            form.addRow(label, widget)
            widget.valueChanged.connect(self._invalidate)
        config_layout.addLayout(form)
        config_layout.addStretch()
        note = QLabel("Initial settings are restored after scanning.\nValidated results are applied separately.")
        note.setWordWrap(True)
        note.setProperty("muted", True)
        config_layout.addWidget(note)
        scroll = QScrollArea()
        scroll.setObjectName("configurationScroll")
        scroll.setWidgetResizable(True)
        scroll.setWidget(configuration)
        scroll.setMinimumWidth(390)
        self.splitter.addWidget(scroll)

        workspace = QWidget()
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(8, 0, 0, 0)
        workspace_layout.setSpacing(10)
        self.result_summary = QLabel("No scan results yet")
        self.result_summary.setObjectName("panelTitle")
        workspace_layout.addWidget(self.result_summary)
        self.result_hint = QLabel("Run preflight to check connections, current limits and scan time.")
        self.result_hint.setWordWrap(True)
        self.result_hint.setProperty("muted", True)
        workspace_layout.addWidget(self.result_hint)
        self.tabs = QTabWidget()
        self.responses = self._table(["Solenoid", "Initial RMS\n(mm)", "Final RMS\n(mm)", "Improvement"])
        self.results = self._table(["Corrector", "Initial (A)", "Candidate (A)", "Change (A)"])
        self.tabs.addTab(self.responses, "Response")
        self.tabs.addTab(self.results, "Correctors")
        workspace_layout.addWidget(self.tabs, 1)
        self.splitter.addWidget(workspace)
        self.splitter.setSizes([440, 700])

        self.status = QLabel("Review scan amplitudes, then run preflight.")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setStyleSheet("QProgressBar { min-height: 4px; border: none; }")
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        row = QHBoxLayout()
        self.preflight_button = QPushButton("Check PVs / Estimate")
        self.start_button = QPushButton("Start Joint Scan")
        self.stop_button = QPushButton("Stop and Restore")
        self.stop_button.setProperty("role", "danger")
        self.apply_button = QPushButton("Apply Recommended")
        self.restore_button = QPushButton("Restore Initial")
        for button, handler in ((self.preflight_button, self.preflight), (self.start_button, self.start),
                                (self.stop_button, self.stop), (self.apply_button, self.apply),
                                (self.restore_button, self.restore)):
            button.setAutoDefault(False)
            row.addWidget(button)
            button.clicked.connect(handler)
        row.addStretch()
        self.log_button = QPushButton("Show log")
        self.log_button.setCheckable(True)
        self.log_button.setAutoDefault(False)
        row.addWidget(self.log_button)
        layout.addLayout(row)
        self.log = QPlainTextEdit()
        self.log.setObjectName("logView")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(3000)
        self.log.setMaximumHeight(150)
        self.log.hide()
        self.log_button.toggled.connect(self._toggle_log)
        layout.addWidget(self.log)
        self.group.currentIndexChanged.connect(self._load_group)
        self._load_group()

    @staticmethod
    def _table(headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(38)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        return table

    def _toggle_log(self, visible):
        self.log.setVisible(visible)
        self.log_button.setText("Hide log" if visible else "Show log")

    def _state(self, text, tone="subtle"):
        self.state_label.setText(text)
        self.state_label.setProperty("tone", tone)
        self.state_label.style().unpolish(self.state_label)
        self.state_label.style().polish(self.state_label)

    @staticmethod
    def _spin(low, high, decimals=3):
        widget = QDoubleSpinBox()
        widget.setDecimals(decimals)
        widget.setRange(low, high)
        widget.setSingleStep(0.01)
        return widget

    def _load_group(self):
        if not self.plans:
            self.status.setText("No joint centering groups configured for this machine.")
            self._refresh()
            return
        plan = self.plans[self.group.currentIndex()]
        self.targets.setRowCount(len(plan.targets))
        correctors = []
        for row, target in enumerate(plan.targets):
            preset = self.context.solenoid_centering_workflow.presets_by_id[target.preset_id]
            correctors.extend((preset.hcorr, preset.vcorr))
            self.targets.setItem(row, 0, QTableWidgetItem(preset.solenoid))
            self.targets.setItem(row, 1, QTableWidgetItem(", ".join(target.bpms)))
            amplitude = self._spin(0.001, 20)
            amplitude.setValue(target.modulation_a)
            amplitude.valueChanged.connect(self._invalidate)
            self.targets.setCellWidget(row, 2, amplitude)
        self.targets.setFixedHeight(self.targets.horizontalHeader().sizeHint().height()
                                    + 38 * len(plan.targets) + 8)
        correctors = list(dict.fromkeys(correctors))
        self.corrector_summary.setText(f"{len(correctors)} corrector channels: " + ", ".join(correctors))
        for widget, value in ((self.probe, plan.probe_a), (self.step, plan.max_step_a),
                              (self.excursion, plan.max_excursion_a), (self.floor, plan.response_floor_mm),
                              (self.iterations, plan.max_iterations)):
            widget.setValue(value)
        self._invalidate()

    def _invalidate(self, *_):
        self.ready = False
        self.result = None
        self.scanner = None
        self.results.setRowCount(0)
        self.responses.setRowCount(0)
        self.result_summary.setText("No scan results yet")
        self.result_hint.setText("Run preflight to check connections, current limits and scan time.")
        self.status.setText("Configuration changed. Run preflight before scanning.")
        self._state("NOT CHECKED")
        self._refresh()

    def _plan(self):
        plan = self.plans[self.group.currentIndex()]
        return replace(plan, targets=tuple(replace(t, modulation_a=self.targets.cellWidget(i, 2).value())
                                          for i, t in enumerate(plan.targets)),
                       probe_a=self.probe.value(), max_step_a=self.step.value(),
                       max_excursion_a=self.excursion.value(), response_floor_mm=self.floor.value(),
                       max_iterations=self.iterations.value())

    def _refresh(self):
        busy = self.worker is not None
        applied = bool(self.result and self.result.get("applied"))
        for widget in (self.group, self.targets, self.probe, self.step, self.excursion,
                       self.floor, self.iterations):
            widget.setEnabled(not busy and not applied)
        self.preflight_button.setEnabled(bool(self.plans) and not busy and not applied)
        self.start_button.setEnabled(self.ready and not busy and not applied)
        self.stop_button.setEnabled(busy)
        self.stop_button.setVisible(busy)
        self.start_button.setVisible(not busy)
        self.progress_bar.setVisible(busy)
        self.apply_button.setEnabled(bool(self.result and self.result.get("recommendation_available"))
                                     and not busy and not applied)
        self.restore_button.setEnabled(applied and not busy)
        self.restore_button.setVisible(applied)
        self.apply_button.setVisible(bool(self.result) and not applied)
        for button, primary in ((self.preflight_button, not self.ready and not self.result and not busy),
                                (self.start_button, self.ready and not busy),
                                (self.apply_button, self.apply_button.isEnabled())):
            button.setProperty("role", "primary" if primary else "")
            button.style().unpolish(button)
            button.style().polish(button)

    def _launch(self, action, callback, operation):
        self.operation = operation
        self._state(operation.upper())
        self.status.setText({"preflight": "Checking PV connections, readbacks and current limits…",
                             "scanning": "Starting joint scan…",
                             "applying": "Applying candidate currents and verifying readbacks…",
                             "restoring": "Restoring initial currents and verifying readbacks…"}[operation])
        self.worker = JointWorker(action, self)
        if self.scanner:
            worker = self.worker
            self.scanner.progress = worker.progress.emit
            self.scanner.helper.stop_requested = lambda: worker.stop_requested
        self.worker.progress.connect(self._progress)
        self.worker.completed.connect(callback)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self._done)
        self._refresh()
        self.worker.start()

    def _progress(self, message):
        self.log.appendPlainText(message)
        if not self.worker or not self.worker.stop_requested:
            self.status.setText(message)

    def _done(self):
        self.worker.deleteLater()
        self.worker = None
        self._refresh()

    def _failed(self, message):
        cancelled = isinstance(message, StopRequested)
        message = str(message)
        self.ready = False
        if self.scanner and self.scanner.last_result:
            self.result = self.scanner.last_result
        restore = self.result.get("restore", "unknown") if self.result else None
        if cancelled and restore == "verified":
            self._state("STOPPED", "warning")
            self.status.setText("Scan stopped. Initial settings restored and verified.")
            self.result_hint.setText("Scan stopped before validation; no candidate can be applied.")
            self.log.appendPlainText(self.status.text())
            return
        detail = ("Preflight failed; no settings changed." if self.operation == "preflight"
                  else f"Operation failed. Restore status: {restore or 'unavailable'}.")
        self.status.setText(detail + "\n" + message)
        self._state("CHECK FAILED" if self.operation == "preflight" else "FAILED", "danger")
        self.result_hint.setText(detail)
        self.log.appendPlainText(message)
        self.log_button.setChecked(True)
        if self.result:
            self.log.appendPlainText(f"Restore status: {self.result.get('restore', 'unknown')}")
        if self.operation != "preflight":
            QMessageBox.critical(self, "Joint Centering", message)

    def preflight(self):
        self._invalidate()
        self.operation = "preflight"
        try:
            self.scanner = JointScanner(self.context, self._plan())
        except Exception as exc:
            self._failed(str(exc))
            return
        self._launch(self.scanner.preflight, self._preflight_done, "preflight")

    def _preflight_done(self, report):
        self.ready = True
        self.scanner.prepared_state = dict(report["original"])
        text = (f"Preflight passed. {len(report['correctors'])} corrector channels, up to {report['points_upper_bound']} measurement points. "
                f"Estimated waiting time: {report['estimated_minimum_seconds'] / 60:.1f} min; actual duration may be longer.")
        self.status.setText(text)
        self._state("READY", "success")
        self.result_summary.setText(f"Estimated scan time: {report['estimated_minimum_seconds'] / 60:.1f} min")
        self.result_hint.setText(f"Up to {report['points_upper_bound']} measurement points. "
                                "Readback and communication delays may increase scan time.")
        self.log.appendPlainText(text)
        self.log.appendPlainText("Correctors: " + ", ".join(report["correctors"]))
        for e, bounds in report["ranges_a"].items():
            self.log.appendPlainText(f"{e}: {bounds[0]:g} … {bounds[1]:g} A")

    def start(self):
        if QMessageBox.question(self, "Start Joint Scan",
                                "The listed solenoids and correctors will be varied using the configured amplitudes. Initial settings will be restored when the scan finishes or stops. Continue?") != QMessageBox.Yes:
            return
        self.ready = False
        self.result = None
        self._launch(self.scanner.run, self._scan_done, "scanning")

    def _scan_done(self, result):
        self.result = result
        available = result["recommendation_available"]
        self._state("VALIDATED" if available else "NOT VALIDATED", "success" if available else "warning")
        self.result_summary.setText(f"Overall response improvement: {result['relative_improvement']:.1%}")
        self.result_hint.setText("Initial settings restored. Review each solenoid before applying."
                                if available else "Initial settings restored. Candidate did not pass validation.")
        self.status.setText(f"Scan complete. Initial settings restored. Improvement: {result['relative_improvement']:.1%}; "
                            + ("Validation passed; recommended values can be applied." if available else "Validation failed; applying results is disabled."))
        self.log.appendPlainText(str(result.get("diagnostics", {})))
        self.log.appendPlainText(result["archive_path"])
        rows = [(c, result["original"][c], value, value - result["original"][c])
                for c, value in result["recommended"].items()]
        self.results.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value) if col == 0 else f"{value:+.4f}" if col == 3 else f"{value:.4f}")
                if col:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.results.setItem(row, col, item)
        self.responses.setRowCount(len(self.scanner.presets))
        for row, (preset, before, after) in enumerate(zip(
                self.scanner.presets, result["baseline_scores_mm"], result["final_scores_mm"])):
            improvement = 1 - after / before if before > 1e-15 else None
            values = (preset.solenoid, f"{before:.5f}", f"{after:.5f}",
                      f"{improvement:+.1%}" if improvement is not None else "—")
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if col == 3 and improvement is not None:
                    color = "metric_active_fg" if improvement >= 0 else "metric_danger_fg"
                    item.setForeground(QColor(self.palette_colors[color]))
                self.responses.setItem(row, col, item)
        self.tabs.setCurrentIndex(0)

    def stop(self):
        if self.worker:
            self.worker.stop_requested = True
            self._state("STOPPING", "warning")
            self.status.setText("Stopping and restoring. Waiting for readback verification.")

    def apply(self):
        if QMessageBox.question(self, "Apply Joint Result", "Apply the recommended corrector currents shown in the table?") == QMessageBox.Yes:
            self._launch(lambda: self.scanner.apply(self.result), self._applied, "applying")

    def _applied(self, _):
        self.status.setText("Recommended values applied. Initial values can be restored.")
        self._state("APPLIED", "success")
        self.result_hint.setText("Candidate corrector currents are now applied to the machine.")

    def restore(self):
        self._launch(lambda: self.scanner.restore_applied(self.result), self._restored, "restoring")

    def _restored(self, _):
        self.status.setText("Initial corrector currents restored.")
        self._state("RESTORED", "success")
        self.result_hint.setText("Initial settings restored. The validated candidate remains available.")

    def reject(self):
        if self.worker is not None:
            self.stop()
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker is not None:
            self.stop()
            event.ignore()
        else:
            event.accept()
