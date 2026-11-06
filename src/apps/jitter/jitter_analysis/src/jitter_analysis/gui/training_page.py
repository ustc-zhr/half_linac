from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyqtgraph as pg
from pyqtgraph.exporters import ImageExporter
from PyQt5 import QtCore, QtWidgets

from ..training import (
    FIR_BPM04_FORECAST,
    RunRole,
    TrainingCancelled,
    TrainingExperimentSpec,
    TrainingService,
    export_model_package,
)


class TrainingWorker(QtCore.QObject):
    progress = QtCore.pyqtSignal(int, str)
    completed = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str)
    cancelled = QtCore.pyqtSignal()

    def __init__(self, service: TrainingService, spec: TrainingExperimentSpec) -> None:
        super().__init__()
        self.service = service
        self.spec = spec
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            result = self.service.run(
                self.spec,
                progress=lambda percent, text: self.progress.emit(percent, text),
                cancelled=lambda: self._cancel_requested,
            )
        except TrainingCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.completed.emit(result)


class TrainingPage(QtWidgets.QWidget):
    trainingStateChanged = QtCore.pyqtSignal(bool)
    logMessage = QtCore.pyqtSignal(str)

    ROLE_LABELS = {
        RunRole.IGNORE.value: "Ignore",
        RunRole.TRAIN.value: "Train",
        RunRole.TEST.value: "Test",
    }

    def __init__(
        self,
        *,
        settings: QtCore.QSettings | None = None,
        default_run_root: str = "runs",
        service: TrainingService | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.service = service or TrainingService()
        self._descriptors = []
        self._result = None
        self._metrics = pd.DataFrame()
        self._predictions = pd.DataFrame()
        self._thread = None
        self._worker = None
        self._training_active = False
        self._acquisition_active = False
        self._output_follows_root = True
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Ignored)
        self._build_ui()
        self.run_root_edit.setText(str(default_run_root))
        self.output_root_edit.setText(str(Path(default_run_root) / "training_experiments"))
        self._restore_preferences()
        self._update_target_note()
        self._update_start_enabled()

    def minimumSizeHint(self) -> QtCore.QSize:
        """Keep the hidden Training tab from forcing the whole window taller."""
        return QtCore.QSize(840, 440)

    @property
    def training_active(self) -> bool:
        return self._training_active

    def _build_ui(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        title_row = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Training")
        title.setProperty("role", "pageTitle")
        title_row.addWidget(title)
        title_row.addStretch(1)
        self.task_combo = QtWidgets.QComboBox()
        self.task_combo.addItem(FIR_BPM04_FORECAST.label, FIR_BPM04_FORECAST.id)
        self.task_combo.setMinimumWidth(290)
        title_row.addWidget(QtWidgets.QLabel("Task"))
        title_row.addWidget(self.task_combo)
        outer.addLayout(title_row)

        source_frame = QtWidgets.QFrame()
        source_frame.setProperty("role", "surface")
        source_layout = QtWidgets.QGridLayout(source_frame)
        source_layout.setContentsMargins(12, 10, 12, 10)
        source_layout.setHorizontalSpacing(8)
        source_layout.setVerticalSpacing(8)
        self.run_root_edit = QtWidgets.QLineEdit()
        self.run_root_browse_button = QtWidgets.QPushButton("Browse")
        self.refresh_button = QtWidgets.QPushButton("Refresh Runs")
        self.output_root_edit = QtWidgets.QLineEdit()
        self.output_browse_button = QtWidgets.QPushButton("Browse")
        source_layout.addWidget(QtWidgets.QLabel("Run Root"), 0, 0)
        source_layout.addWidget(self.run_root_edit, 0, 1)
        source_layout.addWidget(self.run_root_browse_button, 0, 2)
        source_layout.addWidget(self.refresh_button, 0, 3)
        source_layout.addWidget(QtWidgets.QLabel("Experiments"), 1, 0)
        source_layout.addWidget(self.output_root_edit, 1, 1, 1, 2)
        source_layout.addWidget(self.output_browse_button, 1, 3)

        self.subtract_orbit_check = QtWidgets.QCheckBox("Subtract upstream orbit")
        self.coefficient_a_spin = QtWidgets.QDoubleSpinBox()
        self.coefficient_b_spin = QtWidgets.QDoubleSpinBox()
        for spin in (self.coefficient_a_spin, self.coefficient_b_spin):
            spin.setRange(-1000.0, 1000.0)
            spin.setDecimals(6)
            spin.setEnabled(False)
        self.window_spin = QtWidgets.QDoubleSpinBox()
        self.window_spin.setRange(0.2, 3600.0)
        self.window_spin.setDecimals(1)
        self.window_spin.setValue(10.0)
        self.window_spin.setSuffix(" s")
        self.horizon_spin = QtWidgets.QSpinBox()
        self.horizon_spin.setRange(1, 30)
        self.horizon_spin.setValue(1)
        self.horizon_spin.setSuffix(" window")
        self.beam_threshold_spin = QtWidgets.QDoubleSpinBox()
        self.beam_threshold_spin.setRange(-1e12, 1e12)
        self.beam_threshold_spin.setDecimals(3)
        self.beam_threshold_spin.setValue(1000.0)
        parameter_row = QtWidgets.QHBoxLayout()
        parameter_row.addWidget(self.subtract_orbit_check)
        parameter_row.addWidget(QtWidgets.QLabel("a"))
        parameter_row.addWidget(self.coefficient_a_spin)
        parameter_row.addWidget(QtWidgets.QLabel("b"))
        parameter_row.addWidget(self.coefficient_b_spin)
        parameter_row.addSpacing(10)
        parameter_row.addWidget(QtWidgets.QLabel("Window"))
        parameter_row.addWidget(self.window_spin)
        parameter_row.addWidget(QtWidgets.QLabel("Horizon"))
        parameter_row.addWidget(self.horizon_spin)
        parameter_row.addWidget(QtWidgets.QLabel("BPM04.s gate"))
        parameter_row.addWidget(self.beam_threshold_spin)
        parameter_row.addStretch(1)
        source_layout.addLayout(parameter_row, 2, 0, 1, 4)
        self.target_note_label = QtWidgets.QLabel()
        self.target_note_label.setWordWrap(True)
        self.target_note_label.setProperty("role", "message")
        source_layout.addWidget(self.target_note_label, 3, 0, 1, 4)
        outer.addWidget(source_frame)

        self.main_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(7)

        runs_widget = QtWidgets.QWidget()
        runs_layout = QtWidgets.QVBoxLayout(runs_widget)
        runs_layout.setContentsMargins(0, 0, 0, 0)
        runs_layout.setSpacing(8)
        run_controls = QtWidgets.QHBoxLayout()
        self.auto_assign_button = QtWidgets.QPushButton("Auto Assign")
        self.validate_button = QtWidgets.QPushButton("Validate")
        self.start_button = QtWidgets.QPushButton("Start Training")
        self.cancel_button = QtWidgets.QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.start_button.setProperty("role", "control")
        self.cancel_button.setProperty("role", "danger")
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_label = QtWidgets.QLabel("Choose a run root and refresh.")
        run_controls.addWidget(self.auto_assign_button)
        run_controls.addWidget(self.validate_button)
        run_controls.addWidget(self.start_button)
        run_controls.addWidget(self.cancel_button)
        run_controls.addWidget(self.progress_bar, 1)
        runs_layout.addLayout(run_controls)
        runs_layout.addWidget(self.progress_label)

        self.run_table = QtWidgets.QTableWidget(0, 9)
        self.run_table.setHorizontalHeaderLabels(
            ["Role", "Run ID", "Created", "Status", "Machine", "Interval", "Samples", "Compatible", "Notes / issue"]
        )
        self.run_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.run_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.run_table.setAlternatingRowColors(True)
        self.run_table.verticalHeader().setVisible(False)
        self.run_table.horizontalHeader().setStretchLastSection(True)
        self.run_table.setMinimumHeight(120)
        runs_layout.addWidget(self.run_table, 1)
        self.main_splitter.addWidget(runs_widget)

        results_widget = QtWidgets.QWidget()
        results_layout = QtWidgets.QVBoxLayout(results_widget)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.setSpacing(8)
        result_header = QtWidgets.QHBoxLayout()
        self.recommendation_label = QtWidgets.QLabel("No training result.")
        self.recommendation_label.setWordWrap(True)
        self.export_button = QtWidgets.QPushButton("Export Selected Model")
        self.export_button.setEnabled(False)
        result_header.addWidget(self.recommendation_label, 1)
        result_header.addWidget(self.export_button)
        results_layout.addLayout(result_header)

        self.result_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.result_splitter.setChildrenCollapsible(False)
        self.metrics_table = QtWidgets.QTableWidget(0, 11)
        self.metrics_table.setHorizontalHeaderLabels(
            ["Recommended", "Scope", "Run", "Candidate", "Features", "Model", "N", "RMSE", "Skill", "Correlation", "Direction"]
        )
        self.metrics_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.metrics_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.metrics_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.metrics_table.setAlternatingRowColors(True)
        self.metrics_table.verticalHeader().setVisible(False)
        self.metrics_table.horizontalHeader().setStretchLastSection(True)
        self.result_splitter.addWidget(self.metrics_table)

        self.plot_tabs = QtWidgets.QTabWidget()
        self.prediction_plot = pg.PlotWidget()
        self.delta_plot = pg.PlotWidget()
        self.prediction_plot.setLabel("left", "Target", units="mm")
        self.prediction_plot.setLabel("bottom", "Window")
        self.delta_plot.setLabel("left", "Delta / residual", units="mm")
        self.delta_plot.setLabel("bottom", "Window")
        self.prediction_plot.showGrid(x=True, y=True, alpha=0.25)
        self.delta_plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot_tabs.addTab(self.prediction_plot, "Prediction")
        self.plot_tabs.addTab(self.delta_plot, "Delta & Residual")
        self.result_splitter.addWidget(self.plot_tabs)
        self.result_splitter.setSizes([220, 360])
        results_layout.addWidget(self.result_splitter, 1)
        self.main_splitter.addWidget(results_widget)
        self.main_splitter.setSizes([330, 430])
        outer.addWidget(self.main_splitter, 1)

        self.run_root_browse_button.clicked.connect(self._browse_run_root)
        self.output_browse_button.clicked.connect(self._browse_output_root)
        self.refresh_button.clicked.connect(self.refresh_runs)
        self.auto_assign_button.clicked.connect(self.auto_assign_roles)
        self.validate_button.clicked.connect(self.validate_selection)
        self.start_button.clicked.connect(self.start_training)
        self.cancel_button.clicked.connect(self.cancel_training)
        self.export_button.clicked.connect(self.export_selected_model)
        self.subtract_orbit_check.toggled.connect(self._on_subtract_toggled)
        self.coefficient_a_spin.valueChanged.connect(self._update_target_note)
        self.coefficient_b_spin.valueChanged.connect(self._update_target_note)
        self.run_root_edit.textChanged.connect(self._on_run_root_changed)
        self.output_root_edit.textEdited.connect(self._on_output_root_edited)
        self.metrics_table.itemSelectionChanged.connect(self._on_metric_selection_changed)

    def _restore_preferences(self) -> None:
        if self.settings is None:
            return
        run_root = str(self.settings.value("training/run_root", "") or "").strip()
        output_root = str(self.settings.value("training/output_root", "") or "").strip()
        if run_root:
            self.run_root_edit.setText(run_root)
        if output_root:
            self.output_root_edit.setText(output_root)
            self._output_follows_root = False

    def save_preferences(self) -> None:
        if self.settings is None:
            return
        self.settings.setValue("training/run_root", self.run_root_edit.text().strip())
        self.settings.setValue("training/output_root", self.output_root_edit.text().strip())

    def set_acquisition_active(self, active: bool) -> None:
        self._acquisition_active = bool(active)
        self._update_start_enabled()

    def prepare_close(self) -> bool:
        self.save_preferences()
        if not self._training_active:
            return True
        self.cancel_training()
        if self._thread is not None and not self._thread.wait(5000):
            self.progress_label.setText("Waiting for the current model fit to finish before closing.")
            return False
        return True

    def _browse_run_root(self) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Run Root", self.run_root_edit.text().strip() or str(Path.cwd())
        )
        if path:
            self.run_root_edit.setText(path)
            self.refresh_runs()

    def _browse_output_root(self) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Experiment Directory", self.output_root_edit.text().strip() or str(Path.cwd())
        )
        if path:
            self._output_follows_root = False
            self.output_root_edit.setText(path)

    def _on_run_root_changed(self, text: str) -> None:
        if self._output_follows_root:
            self.output_root_edit.setText(str(Path(text.strip() or "runs") / "training_experiments"))
        self._update_start_enabled()

    def _on_output_root_edited(self, _text: str) -> None:
        self._output_follows_root = False
        self._update_start_enabled()

    def _on_subtract_toggled(self, checked: bool) -> None:
        self.coefficient_a_spin.setEnabled(checked and not self._training_active)
        self.coefficient_b_spin.setEnabled(checked and not self._training_active)
        self._update_target_note()

    def _update_target_note(self, *_args) -> None:
        if self.subtract_orbit_check.isChecked():
            if self.coefficient_a_spin.value() == 0.0 and self.coefficient_b_spin.value() == 0.0:
                self.target_note_label.setText(
                    "Target: a = b = 0, so q is currently raw BPM04.x position, not an energy residual."
                )
            else:
                self.target_note_label.setText(
                    "Target: q = BPM04.x - a·BPM02.x - b·BPM03.x. Coefficients are supplied, not fitted."
                )
        else:
            self.target_note_label.setText(
                "Target: raw BPM04.x position. This is not an orbit-subtracted energy residual."
            )

    def refresh_runs(self) -> None:
        root = self.run_root_edit.text().strip()
        if not root:
            self.progress_label.setText("Choose a run root.")
            return
        try:
            self._descriptors = self.service.discover_runs(root, self.task_combo.currentData())
        except Exception as exc:
            self.progress_label.setText(f"Run discovery failed: {exc}")
            return
        self.run_table.setRowCount(len(self._descriptors))
        for row, descriptor in enumerate(self._descriptors):
            role_combo = QtWidgets.QComboBox()
            for role, label in self.ROLE_LABELS.items():
                role_combo.addItem(label, role)
            role_combo.setProperty("runPath", descriptor.path)
            role_combo.setEnabled(descriptor.compatible)
            role_combo.currentIndexChanged.connect(self._update_start_enabled)
            self.run_table.setCellWidget(row, 0, role_combo)
            values = [
                descriptor.run_id,
                descriptor.created_at.replace("T", " ")[:19],
                descriptor.status,
                descriptor.machine,
                f"{descriptor.sample_interval_seconds:g} s" if descriptor.sample_interval_seconds else "--",
                str(descriptor.logical_sample_count),
                "Yes" if descriptor.compatible else "No",
                descriptor.notes if descriptor.compatible else "; ".join(descriptor.issues),
            ]
            for column, value in enumerate(values, start=1):
                self.run_table.setItem(row, column, QtWidgets.QTableWidgetItem(value))
        self.run_table.resizeColumnsToContents()
        self.auto_assign_roles()
        compatible_count = sum(item.compatible for item in self._descriptors)
        self.progress_label.setText(f"Found {len(self._descriptors)} runs; {compatible_count} compatible timed acquisitions.")
        self._update_start_enabled()

    def auto_assign_roles(self) -> None:
        compatible_rows = [
            row for row, descriptor in enumerate(self._descriptors) if descriptor.compatible
        ]
        for row in range(self.run_table.rowCount()):
            combo = self.run_table.cellWidget(row, 0)
            if combo is not None:
                combo.setCurrentIndex(combo.findData(RunRole.IGNORE.value))
        if not compatible_rows:
            self._update_start_enabled()
            return
        newest = compatible_rows[0]
        for row in compatible_rows:
            combo = self.run_table.cellWidget(row, 0)
            role = RunRole.TEST.value if row == newest else RunRole.TRAIN.value
            combo.setCurrentIndex(combo.findData(role))
        self._update_start_enabled()

    def _run_roles(self) -> dict[str, str]:
        roles = {}
        for row in range(self.run_table.rowCount()):
            combo = self.run_table.cellWidget(row, 0)
            if combo is not None:
                roles[str(combo.property("runPath"))] = str(combo.currentData())
        return roles

    def _build_spec(self) -> TrainingExperimentSpec:
        return TrainingExperimentSpec(
            run_root=self.run_root_edit.text().strip(),
            output_root=self.output_root_edit.text().strip(),
            run_roles=self._run_roles(),
            task_id=str(self.task_combo.currentData()),
            subtract_upstream_orbit=self.subtract_orbit_check.isChecked(),
            coefficient_a=self.coefficient_a_spin.value(),
            coefficient_b=self.coefficient_b_spin.value(),
            beam_threshold=self.beam_threshold_spin.value(),
            window_seconds=self.window_spin.value(),
            horizon_windows=self.horizon_spin.value(),
        )

    def validate_selection(self) -> bool:
        try:
            descriptors = self.service.validate_spec(self._build_spec())
        except Exception as exc:
            self.progress_label.setText(f"Validation failed: {exc}")
            return False
        self.progress_label.setText(f"Validation passed for {len(descriptors)} selected runs.")
        return True

    def start_training(self) -> None:
        if self._training_active or self._acquisition_active:
            return
        spec = self._build_spec()
        try:
            self.service.validate_spec(spec)
        except Exception as exc:
            self.progress_label.setText(f"Validation failed: {exc}")
            return
        self.save_preferences()
        self._set_training_active(True)
        self.progress_bar.setValue(0)
        self.progress_label.setText("Starting training...")
        self._thread = QtCore.QThread(self)
        self._worker = TrainingWorker(self.service, spec)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.completed.connect(self._on_completed)
        self._worker.failed.connect(self._on_failed)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.completed.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._worker.cancelled.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()

    def cancel_training(self) -> None:
        if self._worker is not None:
            self._worker.request_cancel()
            self.cancel_button.setEnabled(False)
            self.progress_label.setText("Cancellation requested; waiting for the current fit to finish.")

    @QtCore.pyqtSlot(int, str)
    def _on_progress(self, percent: int, text: str) -> None:
        self.progress_bar.setValue(percent)
        self.progress_label.setText(text)

    @QtCore.pyqtSlot(object)
    def _on_completed(self, result) -> None:
        self._result = result
        self._metrics = pd.read_csv(result.metrics_path)
        self._predictions = pd.read_csv(result.predictions_path)
        self._populate_metrics()
        if result.recommended_candidate_id:
            self.recommendation_label.setText(f"Recommended: {result.recommended_candidate_id}")
        else:
            self.recommendation_label.setText("No model recommended: no exportable candidate achieved positive skill on every Test run.")
        self.progress_bar.setValue(100)
        self.progress_label.setText(f"Experiment saved to {result.experiment_dir}")
        self._select_candidate(result.plot_candidate_id)
        self._save_current_plots()
        self.logMessage.emit(f"Training experiment saved to {result.experiment_dir}")

    @QtCore.pyqtSlot(str)
    def _on_failed(self, message: str) -> None:
        self.progress_label.setText(f"Training failed: {message}")
        self.logMessage.emit(f"Training failed: {message}")

    @QtCore.pyqtSlot()
    def _on_cancelled(self) -> None:
        self.progress_label.setText("Training cancelled. No completed experiment was saved.")
        self.logMessage.emit("Training cancelled.")

    @QtCore.pyqtSlot()
    def _on_thread_finished(self) -> None:
        self._thread = None
        self._worker = None
        self._set_training_active(False)

    def _set_training_active(self, active: bool) -> None:
        self._training_active = bool(active)
        for widget in (
            self.run_root_edit,
            self.run_root_browse_button,
            self.refresh_button,
            self.output_root_edit,
            self.output_browse_button,
            self.task_combo,
            self.subtract_orbit_check,
            self.window_spin,
            self.horizon_spin,
            self.beam_threshold_spin,
            self.auto_assign_button,
            self.validate_button,
            self.run_table,
        ):
            widget.setEnabled(not active)
        self.coefficient_a_spin.setEnabled(not active and self.subtract_orbit_check.isChecked())
        self.coefficient_b_spin.setEnabled(not active and self.subtract_orbit_check.isChecked())
        self.cancel_button.setEnabled(active)
        self.export_button.setEnabled(not active and self._selected_exportable())
        self.trainingStateChanged.emit(active)
        self._update_start_enabled()

    def _update_start_enabled(self, *_args) -> None:
        roles = self._run_roles() if self.run_table.rowCount() else {}
        has_train = RunRole.TRAIN.value in roles.values()
        has_test = RunRole.TEST.value in roles.values()
        ready = bool(
            has_train
            and has_test
            and self.run_root_edit.text().strip()
            and self.output_root_edit.text().strip()
            and not self._training_active
            and not self._acquisition_active
        )
        self.start_button.setEnabled(ready)
        self.validate_button.setEnabled(not self._training_active and has_train and has_test)
        if self._acquisition_active:
            self.start_button.setToolTip("Stop acquisition before starting offline training.")
        elif not (has_train and has_test):
            self.start_button.setToolTip("Assign at least one Train and one Test run.")
        else:
            self.start_button.setToolTip("")

    def _populate_metrics(self) -> None:
        frame = self._metrics.copy()
        frame["scope_order"] = frame["scope"].map({"pooled": 0, "run": 1}).fillna(2)
        frame = frame.sort_values(["scope_order", "skill_vs_persistence"], ascending=[True, False])
        self.metrics_table.setRowCount(len(frame))
        for row, (_, item) in enumerate(frame.iterrows()):
            recommended = bool(
                self._result and item["candidate_id"] == self._result.recommended_candidate_id
            )
            values = [
                "★" if recommended else "",
                str(item["scope"]),
                str(item["run_id"]),
                str(item["candidate_id"]),
                str(item["feature_group"]),
                str(item["model"]),
                str(int(item["samples"])),
                f"{float(item['rmse_mm']):.6g}",
                f"{float(item['skill_vs_persistence']):+.2%}",
                f"{float(item['delta_correlation']):.3f}" if pd.notna(item["delta_correlation"]) else "--",
                f"{float(item['direction_accuracy_large_half']):.1%}" if pd.notna(item["direction_accuracy_large_half"]) else "--",
            ]
            for column, value in enumerate(values):
                table_item = QtWidgets.QTableWidgetItem(value)
                table_item.setData(QtCore.Qt.UserRole, int(item.name))
                self.metrics_table.setItem(row, column, table_item)
        self.metrics_table.resizeColumnsToContents()

    def _select_candidate(self, candidate_id: str | None) -> None:
        if not candidate_id:
            return
        for row in range(self.metrics_table.rowCount()):
            if (
                self.metrics_table.item(row, 1).text() == "pooled"
                and self.metrics_table.item(row, 3).text() == candidate_id
            ):
                self.metrics_table.selectRow(row)
                return

    def _selected_metric(self):
        row = self.metrics_table.currentRow()
        if row < 0 or self._metrics.empty:
            return None
        candidate_id = self.metrics_table.item(row, 3).text()
        scope = self.metrics_table.item(row, 1).text()
        run_id = self.metrics_table.item(row, 2).text()
        matches = self._metrics[
            (self._metrics["candidate_id"] == candidate_id)
            & (self._metrics["scope"] == scope)
            & (self._metrics["run_id"].astype(str) == run_id)
        ]
        return None if matches.empty else matches.iloc[0]

    def _selected_exportable(self) -> bool:
        item = self._selected_metric()
        return bool(item is not None and item["exportable"])

    def _on_metric_selection_changed(self) -> None:
        item = self._selected_metric()
        self.export_button.setEnabled(
            not self._training_active and bool(item is not None and item["exportable"])
        )
        if item is None or self._predictions.empty:
            return
        data = self._predictions[self._predictions["candidate_id"] == item["candidate_id"]]
        if item["scope"] == "run":
            data = data[data["run_id"].astype(str) == str(item["run_id"])]
        self.prediction_plot.clear()
        self.delta_plot.clear()
        colors = [(37, 99, 235), (220, 38, 38), (22, 163, 74), (147, 51, 234)]
        for color_index, (run_id, run_data) in enumerate(data.groupby("run_id", sort=False)):
            run_data = run_data.sort_values("window_index")
            x = run_data["window_index"].to_numpy()
            color = colors[color_index % len(colors)]
            self.prediction_plot.plot(
                x, run_data["actual_next_q_mm"].to_numpy(),
                pen=pg.mkPen(color, width=1.4), name=f"{run_id} measured",
            )
            self.prediction_plot.plot(
                x, run_data["predicted_next_q_mm"].to_numpy(),
                pen=pg.mkPen(color, width=1.0, style=QtCore.Qt.DashLine), name=f"{run_id} predicted",
            )
            self.delta_plot.plot(
                x, run_data["actual_delta_mm"].to_numpy(),
                pen=pg.mkPen(color, width=1.2), name=f"{run_id} actual delta",
            )
            self.delta_plot.plot(
                x, run_data["predicted_delta_mm"].to_numpy(),
                pen=pg.mkPen(color, width=1.0, style=QtCore.Qt.DashLine), name=f"{run_id} predicted delta",
            )
            residual = run_data["actual_delta_mm"].to_numpy() - run_data["predicted_delta_mm"].to_numpy()
            self.delta_plot.plot(
                x, residual, pen=pg.mkPen((107, 114, 128), width=1.0), name=f"{run_id} residual",
            )

    def _save_current_plots(self) -> None:
        if self._result is None or self.metrics_table.currentRow() < 0:
            return
        try:
            plots_dir = Path(self._result.experiment_dir) / "plots"
            plots_dir.mkdir(exist_ok=True)
            prediction_exporter = ImageExporter(self.prediction_plot.plotItem)
            prediction_exporter.parameters()["width"] = 1400
            prediction_exporter.export(str(plots_dir / "prediction.png"))
            residual_exporter = ImageExporter(self.delta_plot.plotItem)
            residual_exporter.parameters()["width"] = 1400
            residual_exporter.export(str(plots_dir / "delta_residual.png"))
        except Exception as exc:
            self.logMessage.emit(f"Could not save training plots: {exc}")

    def export_selected_model(self) -> None:
        if self._result is None:
            return
        item = self._selected_metric()
        if item is None or not item["exportable"]:
            return
        candidate_id = str(item["candidate_id"])
        if (
            candidate_id != self._result.recommended_candidate_id
            or float(item["skill_vs_persistence"]) <= 0.0
        ):
            QtWidgets.QMessageBox.warning(
                self,
                "Export Candidate",
                "This candidate is not the recommended positive-skill model. It will still be exported with its metrics.",
            )
        default_path = Path(self._result.experiment_dir) / f"{candidate_id}.zip"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export Model Package", str(default_path), "ZIP Packages (*.zip)"
        )
        if not path:
            return
        try:
            exported = export_model_package(self._result.experiment_dir, candidate_id, path)
        except Exception as exc:
            self.progress_label.setText(f"Export failed: {exc}")
            return
        self.progress_label.setText(f"Model package exported to {exported}")
        self.logMessage.emit(f"Model package exported to {exported}")
