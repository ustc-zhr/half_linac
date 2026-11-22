from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from PyQt5.QtCore import QSignalBlocker, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView, QComboBox, QDoubleSpinBox, QMessageBox, QPushButton, QSpinBox,
    QTableWidget, QTableWidgetItem, QWidget,
)

from half_linac.src.shared.machine_profile import MachineProfileError
from half_linac.src.shared.machine_profile.app_runtime import make_runtime_run_id

from .amp_scan import AmpScan, AmpSettings, discover_amp_stations
from .scan_widgets import (
    parameter_card, prepare_scan_axes, scan_actions, scan_page_layout,
    scan_workspace, station_selector,
)


class AmpScanWorker(QThread):
    updated = pyqtSignal(dict)

    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation = operation
        self.result = None
        self.error = ""

    def run(self):
        try:
            self.result = self.operation()
        except Exception as exc:
            self.error = str(exc)


class AmpScanPage(QWidget):
    applied = pyqtSignal(tuple)
    busy_changed = pyqtSignal(bool)

    def __init__(self, context, config, runs_dir, write_enabled, palette, parent=None):
        super().__init__(parent)
        self.context = context
        self.runs_dir = runs_dir
        self.palette = palette
        self.runner = self.worker = None
        self.records = {}
        self.settings = None
        self.stations = ()
        self.write_enabled = write_enabled
        self.progress_stations = ()
        self.applying = False
        root, self.status = scan_page_layout(self, "KLY Forward Power")

        try:
            self.settings = AmpSettings.from_config(config)
            self.stations = discover_amp_stations(context, config, self.settings)
        except MachineProfileError as exc:
            self.status.setText(str(exc))
            return

        root.addWidget(self._parameter_card())
        self.all_button = QPushButton("All", self)
        self.clear_button = QPushButton("Clear", self)
        self.table = QTableWidget(len(self.stations), 6, self)
        self.table.setHorizontalHeaderLabels(
            ("Use", "RF station", "Original AMP", "Suggested AMP", "Power change", "Status")
        )
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        splitter, self.plot, self.plot_title = scan_workspace(
            self, self.table, f"Stations · {len(self.stations)} configured",
            (46, 112, 110, 127, 112), (self.all_button, self.clear_button), "Current power scan",
        )
        self.selectors = []
        self.rows = {}
        for row, station in enumerate(self.stations):
            self.rows[station.element_id] = row
            selector = station_selector(self.table, row)
            selector.stateChanged.connect(self._refresh_controls)
            self.selectors.append(selector)
            self.table.setItem(row, 1, QTableWidgetItem(station.display_name))
            for column in range(2, 6):
                self.table.setItem(row, column, QTableWidgetItem("—"))
            status_item = self.table.item(row, 5)
            if station.window:
                status_item.setText("Ready")
                low, high = station.window
                self.table.item(row, 1).setToolTip(
                    f"Approved AMP window {low:g}–{high:g} {self.settings.unit}"
                )
            else:
                status_item.setText("Not approved")
        root.addWidget(splitter, 1)

        self.start_button = QPushButton("Scan Selected", self)
        self.start_button.setObjectName("startButton")
        self.stop_button = QPushButton("Stop", self)
        self.stop_button.setObjectName("stopButton")
        self.apply_button = QPushButton("Apply Selected", self)
        actions, self.progress = scan_actions(
            self, (self.start_button, self.stop_button, self.apply_button), len(self.stations)
        )
        root.addLayout(actions)

        self.all_button.clicked.connect(lambda: self._select_all(True))
        self.clear_button.clicked.connect(lambda: self._select_all(False))
        self.table.currentCellChanged.connect(lambda *_: self._redraw())
        self.start_button.clicked.connect(self._start)
        self.stop_button.clicked.connect(self.stop)
        self.apply_button.clicked.connect(self._apply)
        if not self.stations:
            self.status.setText("Power Scan requires KLY forward power channels in this backend.")
        elif not self.write_enabled:
            self.status.setText("AMP writes are disabled in this backend.")
        elif not any(station.window for station in self.stations):
            self.status.setText("No stations enabled for Power Scan.")
        else:
            self.status.setText("Select RF stations to scan.")
        self._select_all(self.write_enabled and config.get("default_selection") == "all")
        self._redraw()

    def _parameter_card(self):
        self.half_range = QDoubleSpinBox(self)
        self.half_range.setDecimals(3)
        widths = [
            station.window[1] - station.window[0] for station in self.stations
            if station.window is not None
        ]
        self.half_range.setRange(
            0.001, max(self.settings.half_range, max(widths, default=0) / 2, self.half_range.maximum())
        )
        self.half_range.setPrefix("± ")
        self.half_range.setSuffix(f" {self.settings.unit}")
        self.half_range.setSingleStep(0.1)
        self.half_range.setValue(self.settings.half_range)
        self.points = QSpinBox(self)
        self.points.setRange(3, max(721, self.settings.points))
        self.points.setSingleStep(2)
        self.points.setValue(self.settings.points)
        self.points.setToolTip("Use an odd count to include the original AMP and both sides.")

        self.settle = QDoubleSpinBox(self)
        self.sample_interval = QDoubleSpinBox(self)
        for spin, initial in (
            (self.settle, self.settings.settle_time_s),
            (self.sample_interval, self.settings.sample_interval_s),
        ):
            spin.setRange(0, max(60, initial))
            spin.setSingleStep(0.1)
            spin.setSuffix(" s")
            spin.setValue(initial)
        self.samples = QSpinBox(self)
        self.samples.setRange(1, max(100, self.settings.samples_per_point))
        self.samples.setValue(self.settings.samples_per_point)
        self.execution_mode = QComboBox(self)
        self.execution_mode.addItem("Serial", "serial")
        self.execution_mode.addItem("Parallel", "parallel")
        self.execution_mode.setCurrentIndex(self.execution_mode.findData(self.settings.execution_mode))

        parameters = (
            ("AMP range", self.half_range),
            ("Points", self.points),
            ("Settle time", self.settle),
            ("Samples / point", self.samples),
            ("Sample interval", self.sample_interval),
            ("Scan mode", self.execution_mode),
        )
        self.parameter_inputs = tuple(widget for _, widget in parameters)
        return parameter_card(self, parameters)

    def _scan_settings(self):
        settings = replace(
            self.settings,
            half_range=self.half_range.value(),
            points=self.points.value(),
            settle_time_s=self.settle.value(),
            samples_per_point=self.samples.value(),
            sample_interval_s=self.sample_interval.value(),
            execution_mode=self.execution_mode.currentData(),
        )
        settings.validate()
        return settings

    def apply_theme(self, palette):
        self.palette = palette
        if hasattr(self, "plot"):
            self._redraw()

    def is_running(self):
        return self.worker is not None

    def stop(self):
        if self.is_running():
            self.status.setText("Stopping AMP operation…")
            self.stop_button.setEnabled(False)
            self.runner.stop()

    def stop_and_wait(self, timeout_ms):
        if not self.is_running():
            return True
        self.stop()
        return self.worker.wait(timeout_ms)

    def _selected(self):
        return [
            station for station, selector in zip(self.stations, self.selectors)
            if station.window and selector.isChecked()
        ]

    def _selected_candidates(self):
        return [
            station.element_id for station in self._selected()
            if self.records.get(station.element_id, {}).get("status") == "candidate"
        ]

    def _select_all(self, checked):
        for station, selector in zip(self.stations, self.selectors):
            with QSignalBlocker(selector):
                selector.setChecked(checked and station.window is not None)
        self._refresh_controls()

    def _refresh_controls(self, *_):
        busy = self.is_running()
        allowed = self.write_enabled and not busy
        for widget in self.parameter_inputs:
            widget.setEnabled(not busy)
        for station, selector in zip(self.stations, self.selectors):
            selector.setEnabled(allowed and station.window is not None)
        approved_count = sum(station.window is not None for station in self.stations)
        selected_count = len(self._selected())
        self.all_button.setEnabled(allowed and selected_count < approved_count)
        self.clear_button.setEnabled(allowed and selected_count > 0)
        self.start_button.setEnabled(allowed and selected_count > 0)
        self.apply_button.setEnabled(
            allowed and self.runner is not None and self.runner.status == "completed"
            and bool(self._selected_candidates())
        )
        self.stop_button.setEnabled(busy)

    def _start(self):
        selected = self._selected()
        if self.is_running() or not self.write_enabled or not selected:
            return
        try:
            settings = self._scan_settings()
        except ValueError as exc:
            QMessageBox.warning(self, "Power Scan Settings", str(exc))
            return
        names = ", ".join(station.display_name for station in selected)
        scan_order = "in parallel" if settings.execution_mode == "parallel" else "in machine order"
        if QMessageBox.question(
            self, "Start Power Scan",
            f"Scan {names} {scan_order}?\n\n"
            f"±{settings.half_range:g} {settings.unit}, {settings.points} points, "
            f"{settings.samples_per_point} samples per point. "
            "Each station's original AMP will be restored.",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
        ) != QMessageBox.Yes:
            return
        self.records.clear()
        for row, station in enumerate(self.stations):
            if station.window is None:
                continue
            for column in (2, 3, 4):
                self.table.item(row, column).setText("—")
            status = "Queued" if station in selected else "Ready"
            self.table.item(row, 5).setText(status)
            self.table.item(row, 5).setToolTip("")
        self.table.selectRow(self.rows[selected[0].element_id])
        self._redraw()
        run_name = make_runtime_run_id("amp", uuid4().hex[:8])
        self.runner = AmpScan(
            context=self.context, stations=selected, settings=settings,
            path=self.runs_dir / run_name / "run.json",
        )
        self._launch(self.runner.run, f"Running power scan {scan_order}…", self.runner.stations)

    def _apply(self):
        selected = self._selected_candidates()
        if (self.is_running() or not self.write_enabled or not selected
                or self.runner is None or self.runner.status != "completed"):
            return
        settings = "\n".join(
            f"{station}  {self.records[station]['initial']:g} → "
            f"{self.records[station]['suggestion']:g} {self.settings.unit}"
            for station in selected
        )
        if QMessageBox.question(
            self, "Apply AMP Suggestions", f"Apply these AMP values?\n\n{settings}",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
        ) == QMessageBox.Yes:
            self._launch(lambda: self.runner.apply(selected), "Applying AMP…", selected, applying=True)

    def _launch(self, operation, message, stations, *, applying=False):
        self.progress_stations = tuple(stations)
        self.applying = applying
        self.progress.setRange(0, len(self.progress_stations))
        self.progress.setValue(0)
        self.worker = AmpScanWorker(operation, self)
        self.runner.on_update = self.worker.updated.emit
        self.worker.updated.connect(self._update)
        self.worker.finished.connect(self._finished)
        self.status.setText(message)
        self._refresh_controls()
        self.busy_changed.emit(True)
        self.worker.start()

    def _finished(self):
        worker, self.worker = self.worker, None
        result = worker.result
        self.runner.on_update = lambda _record: None
        if result:
            for record in result["stations"]:
                self._update(record)
            if result["error"]:
                text = result["error"]
            else:
                text = "AMP operation complete."
            self.status.setText(f"{text}  ·  {result['path']}")
            applied = tuple(row["station"] for row in result["stations"] if row["status"] == "applied")
            if applied:
                self.applied.emit(applied)
        else:
            self.status.setText(worker.error or "AMP operation did not finish.")
        self._select_all(False)
        self.busy_changed.emit(False)
        worker.deleteLater()

    def _update(self, record):
        element_id = record["station"]
        previous_status = self.records.get(element_id, {}).get("status")
        self.records[element_id] = record
        row = self.rows[element_id]
        initial = record.get("initial")
        suggestion = record.get("suggestion")
        initial_output = record.get("initial_output")
        suggested_output = record.get("suggested_output")
        self.table.item(row, 2).setText(f"{initial:g}" if initial is not None else "—")
        self.table.item(row, 3).setText(f"{suggestion:g}" if suggestion is not None else "—")
        if initial_output and suggested_output is not None:
            change = (suggested_output - initial_output) / initial_output
            self.table.item(row, 4).setText(f"{change:+.2%}")
        status = {
            "pending": "Pending", "scanning": "Scanning", "candidate": "Suggestion ready",
            "keep": "Keep original", "undetermined": "No suggestion", "applied": "Applied",
            "stopped": "Stopped", "failed": "Scan failed", "restore_failed": "Restore unverified",
            "applying": "Applying", "apply_failed": "Apply failed",
        }[record["status"]]
        self.table.item(row, 5).setText(status)
        self.table.item(row, 5).setToolTip(record.get("error") or status)
        follow_scan = self.runner.settings.execution_mode == "serial" and record["status"] == "scanning"
        if (follow_scan or record["status"] == "applying") and record["status"] != previous_status:
            self.table.selectRow(row)
        elif row == self.table.currentRow():
            self._redraw()
        self._update_progress()

    def _update_progress(self):
        if self.applying:
            complete = {"applied", "apply_failed", "restore_failed"}
        else:
            complete = {"undetermined", "stopped", "failed", "restore_failed"}
        finished = 0
        for station in self.progress_stations:
            record = self.records.get(station, {})
            status = record.get("status")
            if status in complete or (
                not self.applying and status in {"candidate", "keep"} and record.get("restored")
            ):
                finished += 1
        self.progress.setValue(finished)

    def _redraw(self):
        row = self.table.currentRow()
        record = {}
        if row >= 0:
            station = self.stations[row]
            self.plot_title.setText(f"{station.display_name} power scan")
            record = self.records.get(station.element_id, {})
        else:
            self.plot_title.setText("Current power scan")
        points = sorted(
            (point["amp"], point["output"]) for point in record.get("points", ())
            if "output" in point and point["purpose"] in {"scan", "baseline"}
        )
        palette = self.palette
        axes = prepare_scan_axes(self.plot, palette, has_data=bool(points))
        if points:
            axes.set_xlabel(f"LLRF AMP ({self.settings.unit})", color=palette["muted"])
            power_label = "KLY forward power"
            if self.settings.power_unit:
                power_label += f" ({self.settings.power_unit})"
            axes.set_ylabel(power_label, color=palette["muted"])
            axes.plot(*zip(*points), "o-", color=palette["trace"], linewidth=1.8, markersize=5)
            for key, label, color in (
                ("initial", "Original", "muted"), ("suggestion", "Suggested", "accent"),
            ):
                if key in record:
                    axes.axvline(record[key], color=palette[color], linestyle="--", label=label)
            if "initial" in record:
                axes.legend(facecolor=palette["plot"], labelcolor=palette["text"])
        self.plot.fig.tight_layout()
        self.plot.canvas.draw_idle()
