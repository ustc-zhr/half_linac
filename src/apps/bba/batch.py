from __future__ import annotations

import json
import math
import numpy as np
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QComboBox, QGridLayout, QGroupBox, QLineEdit, QMessageBox, QFileDialog,
)

from half_linac.src.shared.machine_profile.app_runtime import sanitize_runtime_token
from half_linac.src.shared.machine_profile.loader import machine_root, load_bba_workflow
from half_linac.src.apps.bba.fit_quality import fit_bba1_center


def save_preset_scans(path, expected_text, originals, presets, backend, profile=None):
    text = path.read_text(encoding="utf-8")
    if text != expected_text:
        raise ValueError("Configuration changed on disk. Reopen Batch before saving.")
    data = json.loads(text)
    changed = {preset.id: (original.scan, preset.scan) for original, preset in zip(originals, presets)
               if original.scan != preset.scan}
    decoder = json.JSONDecoder()
    position = text.index("[", text.index('"presets"')) + 1
    edits = []
    for raw in data["presets"]:
        while text[position].isspace() or text[position] == ",":
            position += 1
        _, end = decoder.raw_decode(text, position)
        if raw["id"] in changed:
            before, after = changed[raw["id"]]
            scan = raw.setdefault("scan", {})
            if set(scan) - {"corrector", "quadrupole", "sampling"}:
                raise ValueError("Save requires structured scan configuration.")
            for key, prefix in (("corrector", "corr"), ("quadrupole", "quad")):
                updates = {}
                for field, suffix in (("low", "from"), ("high", "end"), ("steps", "steps"), ("mode", "mode")):
                    name = f"{prefix}_{suffix}"
                    if getattr(before, name) != getattr(after, name):
                        updates[field] = getattr(after, name)
                if updates:
                    ranges = scan.setdefault(key, {})
                    if any(field in ranges for field in ("low", "high", "unit", "steps", "mode")):
                        common = {field: value for field, value in ranges.items() if field not in ("vm", "real")}
                        ranges = {mode: {**common, **ranges.get(mode, {})} for mode in ("vm", "real")}
                        scan[key] = ranges
                    ranges.setdefault(backend, {}).update(updates)
            for key, field in (("samples_per_point", "samples"), ("settle_time_s", "settle_time"), ("sample_interval_s", "sample_interval")):
                if getattr(before, field) != getattr(after, field):
                    scan.setdefault("sampling", {})[key] = getattr(after, field)
            def compact(value, indent):
                inline = json.dumps(value, ensure_ascii=False)
                if not isinstance(value, dict) or len(inline) + indent <= 135:
                    return inline
                lines = []
                scalars = []
                for key, item in value.items():
                    entry = json.dumps(key) + ": " + compact(item, indent + 2)
                    if isinstance(item, (dict, list)):
                        if scalars:
                            lines.append(", ".join(scalars))
                            scalars = []
                        lines.append(entry)
                    else:
                        if scalars and len(", ".join(scalars + [entry])) + indent + 2 > 135:
                            lines.append(", ".join(scalars))
                            scalars = []
                        scalars.append(entry)
                if scalars:
                    lines.append(", ".join(scalars))
                return "{\n" + ",\n".join(" " * (indent + 2) + line for line in lines) + "\n" + " " * indent + "}"
            edits.append((position, end, compact(raw, 4)))
        position = end
    if set(changed) - {raw["id"] for raw in data["presets"]}:
        raise ValueError("A preset is missing from the configuration.")
    for start, end, replacement in reversed(edits):
        text = text[:start] + replacement + text[end:]
    if profile is not None:
        load_bba_workflow(replace(profile, workflows={**profile.workflows, "bba": json.loads(text)}), backend)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        if path.read_text(encoding="utf-8") != expected_text:
            raise ValueError("Configuration changed on disk. Reopen Batch before saving.")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return text


class BBABatchThread(QThread):
    trigger = pyqtSignal(dict)
    progress = pyqtSignal(int, dict)

    def __init__(self, tasks, scan_factory, directory):
        super().__init__()
        self.tasks = tasks
        self.scan_factory = scan_factory
        self.directory = Path(directory)
        self.active = None
        self.stopping = False
        self.records = [
            {"preset_id": task.preset_id, "status": "pending", "archive": str(task.archive_dir)}
            for task in tasks
        ]
        self.status = "pending"
        self.error = ""

    def stop(self):
        self.stopping = True
        active = self.active
        if active is not None:
            active.stop()

    def _save(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / "batch.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({
            "status": self.status, "error": self.error,
            "machine": self.tasks[0].app_context.machine.id,
            "backend": self.tasks[0].control_backend,
            "items": self.records,
        }, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def run(self):
        index = 0
        try:
            self.status = "preflight"
            self._save()
            for index, task in enumerate(self.tasks):
                if self.stopping:
                    return
                self.progress.emit(index, {"status": "checking"})
                candidate = self.scan_factory(task)
                candidate.preflight()
            for index, task in enumerate(self.tasks):
                if self.stopping:
                    return
                self.status = "running"
                self.records[index]["status"] = "running"
                self._save()
                self.progress.emit(index, self.records[index])
                self.active = self.scan_factory(task)
                self.active.trigger.connect(self.trigger, Qt.DirectConnection)
                if self.stopping:
                    self.active.stop()
                self.active.run()
                self.records[index].update(self.active.outcome)
                self.progress.emit(index, dict(self.records[index]))
                self._save()
                if self.active.outcome["status"] != "success" or not self.active.outcome["restored"]:
                    self.status = self.active.outcome["status"]
                    self.error = self.active.outcome.get("error", "")
                    if self.status == "success":
                        raise RuntimeError("Restoration was not confirmed.")
                    return
                self.active = None
            self.status = "success"
        except Exception as exc:
            self.status = "failed"
            self.error = str(exc)
            self.records[index].update(status="failed", error=self.error)
            self.progress.emit(index, dict(self.records[index]))
        finally:
            self.active = None
            if self.stopping and self.status != "failed":
                self.status = "stopped"
            for pending_index, record in enumerate(self.records):
                if record["status"] in {"pending", "running"}:
                    record["status"] = "not_run"
                    self.progress.emit(pending_index, dict(record))
            try:
                self._save()
            except Exception as exc:
                self.status = "failed"
                self.error = f"Batch summary could not be saved: {exc}"


class BBABatchDialog(QDialog):
    def __init__(self, window, scan_factory):
        super().__init__(window)
        self.window = window
        self.scan_factory = scan_factory
        self.worker = None
        self.results = {}
        self.presets = list(window._bba_presets_for_family("bba1"))
        self.originals = list(self.presets)
        self.editor_row = None
        self.config_path = machine_root(window.machine_profile.machine.id) / "apps" / "bba.json"
        self.config_text = self.config_path.read_text(encoding="utf-8") if self.config_path.is_file() else None
        self.selected_rows = []
        self.setWindowTitle(f"BBA-1 Batch · {window.machine_profile.machine.display_name} · {window.app_context.control_backend.name}")
        self.resize(1240, 800)
        layout = QVBoxLayout(self)
        note = QLabel("Check presets to run. Select a row to edit its parameters below.")
        note.setWordWrap(True)
        layout.addWidget(note)
        actions = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find Quad, COR or BPM…")
        self.search.textChanged.connect(self._filter)
        actions.addWidget(self.search, 1)
        self.selection_buttons = []
        for label, plane in (("All", "all"), ("X", "x"), ("Y", "y"), ("Clear", "none")):
            button = QPushButton(label)
            button.setProperty("compact", True)
            button.setFixedWidth(65)
            button.clicked.connect(lambda checked=False, selected=plane: self._select(selected))
            actions.addWidget(button)
            self.selection_buttons.append(button)
        layout.addLayout(actions)
        self.review_button = QPushButton("Select Needs Review")
        self.review_button.setProperty("compact", True)
        self.review_button.clicked.connect(self._select_review)
        actions.addWidget(self.review_button)
        self.selection_buttons.append(self.review_button)
        self.table = QTableWidget(len(self.presets), 10)
        palette = window._palette()
        self.table.setStyleSheet(
            "QTableWidget::indicator {width: 16px; height: 16px; border-radius: 3px; "
            f"border: 1px solid {palette['input_border']}; background: {palette['input_bg']};}}"
            f"QTableWidget::indicator:checked {{background: {palette['metric_active_fg']};}}"
        )
        self.table.setHorizontalHeaderLabels(("Use", "Preset", "COR range", "Quad range", "Sampling", "Status", "Center ± 1σ (mm)", "Archive", "Quality", "R²"))
        self.table.hideColumn(7)
        self.table.hideColumn(4)
        self.table.cellDoubleClicked.connect(self._view_result)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        if self.presets:
            scan = self.presets[0].scan
            self.table.horizontalHeaderItem(2).setText(f"COR ({scan.corr_unit})")
            self.table.horizontalHeaderItem(3).setText(f"Quad ({scan.quad_unit})")
        for row, preset in enumerate(self.presets):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            check.setCheckState(Qt.Unchecked)
            self.table.setItem(row, 0, check)
            scan = preset.scan
            values = (
                window._bba_preset_label(preset),
                f"{scan.corr_from:g} … {scan.corr_end:g} {scan.corr_unit} ({scan.corr_mode})",
                f"{scan.quad_from:g} … {scan.quad_end:g} {scan.quad_unit} ({scan.quad_mode})",
                f"{scan.samples} / {scan.settle_time:g}s / {scan.sample_interval:g}s",
                "", "", "", "", "",
            )
            for column, value in enumerate(values, 1):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(row, column, item)
            self.table.item(row, 4).setToolTip(
                f"COR × Quad: {scan.corr_steps} × {scan.quad_steps} points\n"
                f"Samples/point: {scan.samples}\nSettle: {scan.settle_time:g} s\nInterval: {scan.sample_interval:g} s"
            )
            self._refresh_row(row)
        layout.addWidget(self.table, 1)
        self.editor = QGroupBox("Scan parameters")
        editor_layout = QGridLayout(self.editor)
        self.fields = {}
        for column, label in enumerate(("", "From", "To", "Steps", "Mode", "Unit")):
            editor_layout.addWidget(QLabel(label), 0, column)
        self.unit_labels = {}
        for row, (label, prefix) in enumerate((("COR", "corr"), ("Quad", "quad")), 1):
            editor_layout.addWidget(QLabel(label), row, 0)
            for column, suffix in enumerate(("from", "end", "steps"), 1):
                field = QLineEdit()
                self.fields[f"{prefix}_{suffix}"] = field
                editor_layout.addWidget(field, row, column)
            mode = QComboBox()
            mode.addItem("Relative to initial", "relative")
            mode.addItem("Absolute", "absolute")
            self.fields[f"{prefix}_mode"] = mode
            editor_layout.addWidget(mode, row, 4)
            self.unit_labels[prefix] = QLabel()
            editor_layout.addWidget(self.unit_labels[prefix], row, 5)
        for column, (key, label) in enumerate((("samples", "Samples / point"), ("settle_time", "Settle (s)"), ("sample_interval", "Interval (s)"))):
            editor_layout.addWidget(QLabel(label), 3, column * 2)
            self.fields[key] = QLineEdit()
            editor_layout.addWidget(self.fields[key], 3, column * 2 + 1)
        editor_actions = QHBoxLayout()
        self.edit_note = QLabel("Changes apply to this batch. Save Defaults keeps them for future runs.")
        editor_actions.addWidget(self.edit_note, 1)
        for label, handler in (("Apply to Row", self._commit_editor), ("Sampling → Checked", self._apply_sampling), ("Save Defaults", self._save_defaults)):
            button = QPushButton(label)
            button.setProperty("compact", True)
            button.clicked.connect(handler)
            if label == "Save Defaults":
                button.setToolTip("Save all modified rows to bba.json. Ranges affect the current backend; sampling is shared by VM and real.")
            editor_actions.addWidget(button)
        editor_layout.addLayout(editor_actions, 4, 0, 1, 6)
        layout.addWidget(self.editor)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        controls = QHBoxLayout()
        self.start_button = QPushButton("Start Selected")
        self.stop_button = QPushButton("Stop / Restore")
        self.close_button = QPushButton("Close")
        self.stop_button.setEnabled(False)
        self.start_button.clicked.connect(self._start)
        self.stop_button.clicked.connect(self._stop)
        self.close_button.clicked.connect(self.reject)
        for button in (self.start_button, self.stop_button, self.close_button):
            button.setFixedWidth(155 if button != self.close_button else 85)
            controls.addWidget(button)
        controls.insertStretch(2, 1)
        for label, handler in (("View Fit", self._view_result), ("Load Result…", self._load_result)):
            button = QPushButton(label)
            button.clicked.connect(handler)
            controls.insertWidget(2, button)
        layout.addLayout(controls)
        self.table.itemChanged.connect(self._estimate)
        self.table.currentCellChanged.connect(self._edit_row)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        for field in self.fields.values():
            if isinstance(field, QLineEdit):
                field.returnPressed.connect(self._commit_editor)
        self.table.setCurrentCell(0, 1)
        self._estimate()

    def _filter(self, text):
        for row, preset in enumerate(self.presets):
            self.table.setRowHidden(row, text.casefold() not in self.window._bba_preset_label(preset).casefold())

    def _edit_row(self, row, column=0, previous_row=-1, previous_column=0):
        if row < 0 or row == self.editor_row:
            return
        if not self._commit_editor():
            self.table.blockSignals(True)
            self.table.setCurrentCell(self.editor_row, 1)
            self.table.blockSignals(False)
            return
        self.editor_row = row
        preset = self.presets[row]
        self.editor.setTitle(self.window._bba_preset_label(preset))
        for key, field in self.fields.items():
            value = getattr(preset.scan, key)
            if isinstance(field, QComboBox):
                field.setCurrentIndex(field.findData(value))
            else:
                field.setText(f"{value:g}")
        for prefix, label in self.unit_labels.items():
            label.setText(getattr(preset.scan, f"{prefix}_unit"))

    def _commit_editor(self, *_):
        if self.editor_row is None:
            return True
        try:
            values = {}
            for key, field in self.fields.items():
                if isinstance(field, QComboBox):
                    values[key] = field.currentData()
                elif key in ("corr_steps", "quad_steps", "samples"):
                    values[key] = int(field.text())
                    if values[key] < (1 if key == "samples" else 2):
                        raise ValueError("Use at least two scan steps and one sample per point.")
                else:
                    values[key] = float(field.text())
                    if not math.isfinite(values[key]):
                        raise ValueError("Values must be finite.")
            if values["settle_time"] < 0 or values["sample_interval"] < 0:
                raise ValueError("Timing must be non-negative.")
            for prefix in ("corr", "quad"):
                if values[f"{prefix}_from"] >= values[f"{prefix}_end"]:
                    raise ValueError(f"{prefix.upper()}: From must be less than To.")
            preset = self.presets[self.editor_row]
            self.presets[self.editor_row] = replace(preset, scan=replace(preset.scan, **values))
            self._refresh_row(self.editor_row)
            self.edit_note.setText("Modified for this batch · Save Defaults to keep changes." if self.presets != self.originals else "Changes apply to this batch. Save Defaults keeps them for future runs.")
            self._estimate()
            return True
        except ValueError as exc:
            self.edit_note.setText(str(exc))
            return False

    def _refresh_row(self, row):
        scan = self.presets[row].scan
        self.table.blockSignals(True)
        for column, prefix in ((2, "corr"), (3, "quad")):
            self.table.item(row, column).setText(
                ("Δ " if getattr(scan, prefix + '_mode') == "relative" else "") +
                f"{getattr(scan, prefix + '_from'):g} … {getattr(scan, prefix + '_end'):g} "
            )
            self.table.item(row, column).setToolTip(f"{getattr(scan, prefix + '_steps')} points · {getattr(scan, prefix + '_mode')} · {getattr(scan, prefix + '_unit')}")
        self.table.item(row, 4).setText(f"{scan.samples} / {scan.settle_time:g}s / {scan.sample_interval:g}s")
        self.table.item(row, 4).setToolTip("Samples per point / settle time / sample interval")
        self.table.blockSignals(False)

    def _apply_sampling(self):
        if not self._commit_editor() or self.editor_row is None:
            return
        scan = self.presets[self.editor_row].scan
        for row in self._rows():
            preset = self.presets[row]
            self.presets[row] = replace(preset, scan=replace(preset.scan, samples=scan.samples, settle_time=scan.settle_time, sample_interval=scan.sample_interval))
            self._refresh_row(row)
        self._estimate()

    def _save_defaults(self):
        if not self._commit_editor():
            return
        try:
            self.config_text = save_preset_scans(self.config_path, self.config_text, self.originals, self.presets, self.window.app_context.control_backend.name, self.window.machine_profile)
            data = json.loads(self.config_text)
            profile = replace(self.window.machine_profile, workflows={**self.window.machine_profile.workflows, "bba": data})
            workflow = load_bba_workflow(profile, self.window.app_context.control_backend.name)
            self.window.machine_profile = profile
            self.window.bba_workflow = workflow
            self.window.app_context = replace(self.window.app_context, profile=profile, bba_workflow=workflow)
            self.originals = list(self.presets)
            self.window._apply_selected_bba1_preset()
            self.edit_note.setText("Saved to bba.json. Range changes affect this backend; sampling is shared by this preset.")
        except (OSError, ValueError) as exc:
            self.edit_note.setText(f"Save failed: {exc}")

    def _select(self, plane):
        for row, preset in enumerate(self.presets):
            self.table.item(row, 0).setCheckState(
                Qt.Checked if not self.table.isRowHidden(row) and (plane == "all" or preset.plane.lower() == plane) else Qt.Unchecked
            )

    def _rows(self):
        return [row for row in range(len(self.presets)) if self.table.item(row, 0).checkState() == Qt.Checked]

    def _estimate(self, *_):
        if self.worker is not None and self.worker.isRunning():
            return
        seconds = 0
        rows = self._rows()
        for row in rows:
            scan = self.presets[row].scan
            per_point = scan.settle_time + (scan.samples - 1) * scan.sample_interval
            reference = per_point if self.window.bba1_bpm1_mode_combo.currentData() == "initial_k1" else 0
            seconds += scan.corr_steps * (scan.quad_steps * per_point + reference + 1) + scan.settle_time
        self.summary.setText(f"{len(rows)} selected · approximately {seconds / 60:.1f} min plus PV communication and restoration checks")
        self.start_button.setEnabled(bool(rows))

    def _start(self):
        if self.window._scan_is_running():
            return
        if not self._commit_editor():
            return
        self.selected_rows = self._rows()
        if not self.selected_rows:
            return
        if not self.window._require_write_allowed("BBA-1 batch", self.window.app_context.control_backend.name):
            return
        directory = self.window._bba_runtime_paths(self.window.app_context.control_backend.name)["runs_dir"] / (
            "batch_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:8]
        )
        tasks = []
        for index, row in enumerate(self.selected_rows):
            preset = self.presets[row]
            params = self.window.get_setting(preset)
            if params is None:
                return
            params.batch = True
            archive = directory / f"{index + 1:03d}_{sanitize_runtime_token(preset.id)}"
            params.archive_dir = archive
            params.bba1_data_path = archive / "m1S.txt"
            params.bba1_quad_scan_path = archive / "bba1_quad_scan.txt"
            params.bba1_metadata_path = archive / "metadata.json"
            tasks.append(params)
        self.table.blockSignals(True)
        for row in self.selected_rows:
            self.results.pop(row, None)
            for column in range(5, 10):
                self.table.item(row, column).setText("")
                self.table.item(row, column).setToolTip("")
            self.table.item(row, 5).setText("pending")
        self.table.blockSignals(False)
        self.worker = BBABatchThread(tasks, self.scan_factory, directory)
        self.worker.trigger.connect(self._display)
        self.worker.progress.connect(self._progress)
        self.worker.finished.connect(self._finished)
        self.window.scan = self.worker
        self.window.scan_mode = "scan"
        self.window.scan_family = "bba1"
        self.window.tabWidget.setEnabled(False)
        self.editor.setEnabled(False)
        self.search.setEnabled(False)
        self.table.setEnabled(False)
        for button in self.selection_buttons + [self.start_button, self.close_button]:
            button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.worker.start()
        self.window._refresh_status()

    def _display(self, payload):
        if "error" not in payload:
            self.window.display(payload)

    def _select_review(self):
        for row in range(len(self.presets)):
            quality = self.results.get(row, {}).get("fit_quality", {}).get("quality")
            self.table.item(row, 0).setCheckState(Qt.Checked if quality in ("review", "invalid") else Qt.Unchecked)

    def _load_result(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load BBA1 result", str(self.window._bba_runtime_paths(self.window.app_context.control_backend.name)["runs_dir"]), "Metadata (metadata.json)")
        if not path:
            return
        try:
            metadata = json.loads(Path(path).read_text())
            if metadata.get("family") != "bba1":
                raise ValueError("Select BBA1 metadata.")
            record = dict(metadata.get("outcome", {}), archive=str(Path(path).parent))
            if "fit_quality" not in record:
                data = np.loadtxt(Path(path).parent / "m1S.txt", ndmin=2)
                record["fit_quality"] = fit_bba1_center(data[:, 0], data[:, 1])
            self._show_fit(record, metadata.get("preset_id") or "BBA1")
        except (OSError, ValueError, KeyError, IndexError) as exc:
            QMessageBox.warning(self, "BBA1 result", str(exc))

    def _view_result(self, row=None, column=0):
        if row is None or isinstance(row, bool):
            row = self.table.currentRow()
        record = self.results.get(row)
        if record is None or "fit_quality" not in record:
            self.edit_note.setText("No final fit available for this row yet.")
            return
        self._show_fit(record, self.presets[row].id)

    def _show_fit(self, record, title):
        from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg, NavigationToolbar2QT
        from matplotlib.figure import Figure
        quality = record["fit_quality"]
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(1050, 600)
        layout = QVBoxLayout(dialog)
        offset, sigma = quality.get("offset_m"), quality.get("offset_sigma_m")
        center = "Unavailable" if offset is None else f"{offset * 1000:.4f} mm"
        if sigma is not None:
            center += f" ± {sigma * 1000:.4f} mm (1σ)"
        score = quality.get("r_squared")
        score_text = "—" if score is None else f"{score:.4f}"
        note = QLabel(f"{quality['quality']} · Center: {center} · R²: {score_text}\n" +
                      ("; ".join(quality["reasons"]) or "No review flags at the current thresholds.") +
                      "\n1σ is an approximate fit statistic; it excludes systematic errors and BPM1 measurement uncertainty.")
        note.setWordWrap(True)
        layout.addWidget(note)
        figure = Figure(tight_layout=True)
        canvas = FigureCanvasQTAgg(figure)
        layout.addWidget(NavigationToolbar2QT(canvas, dialog))
        layout.addWidget(canvas)
        final_axes, raw_axes = figure.subplots(1, 2)
        positions = np.asarray(quality["positions_m"]) * 1000
        final_axes.plot(positions, quality["responses"], "o")
        if len(quality["fitted"]) == len(positions):
            order = np.argsort(positions)
            final_axes.plot(positions[order], np.asarray(quality["fitted"])[order], "-")
        final_axes.axhline(0, color="gray", linewidth=0.8)
        if offset is not None:
            final_axes.axvline(offset * 1000, color="tab:red", linestyle="--")
            if sigma is not None:
                final_axes.axvspan((offset - sigma) * 1000, (offset + sigma) * 1000, color="tab:red", alpha=0.12)
        final_axes.set(xlabel="BPM1 (mm)", ylabel="dBPM2/dK1", title="Final fit")
        try:
            raw = np.loadtxt(Path(record["archive"]) / "bba1_quad_scan.txt", ndmin=2)
            for corrector in np.unique(raw[:, 0]):
                points = raw[raw[:, 0] == corrector]
                raw_axes.plot(points[:, 1], points[:, 3] * 1000, ".", label=f"COR {corrector:g}")
            raw_axes.legend(fontsize=8)
        except (OSError, ValueError, IndexError, KeyError):
            raw_axes.text(0.5, 0.5, "Raw points unavailable", ha="center", transform=raw_axes.transAxes)
        raw_axes.set(xlabel="K1 (1/m²)", ylabel="BPM2 (mm)", title="Original quad scans")
        dialog.exec_()

    def _progress(self, index, record):
        row = self.selected_rows[index]
        status = record["status"]
        self.table.item(row, 5).setText(status)
        self.table.item(row, 5).setToolTip(record.get("error", ""))
        if status == "running":
            self.results.pop(row, None)
            for column in (8, 9):
                self.table.item(row, column).setText("")
            self.window.display({"clear": True, "clear_points": True})
            self.window._set_bba_preset_combo_current(self.window.bba1_preset_combo, self.presets[row].id)
            self.window._apply_bba1_preset(self.presets[row])
            self.window.bba1_loaded_source_dir = self.worker.tasks[index].archive_dir
            self.table.item(row, 6).setText("")
        if record.get("offset_m") is not None:
            self.table.item(row, 6).setText(f"{record['offset_m'] * 1000:.4f}")
        quality = record.get("fit_quality")
        if quality:
            self.results[row] = dict(record)
            sigma = quality.get("offset_sigma_m")
            if sigma is not None:
                self.table.item(row, 6).setText(f"{quality['offset_m'] * 1000:.4f} ± {sigma * 1000:.4f}")
            elif quality.get("offset_m") is None:
                self.table.item(row, 6).setText("—")
            self.table.item(row, 8).setText({"good": "Normal", "review": "Review", "invalid": "Invalid"}[quality["quality"]])
            self.table.item(row, 8).setForeground(QColor(self.window._palette()["metric_active_fg" if quality["quality"] == "good" else "metric_warning_fg"]))
            self.table.item(row, 8).setToolTip("; ".join(quality["reasons"]) or "No review flags. Double-click to inspect fit.")
            score = quality.get("r_squared")
            self.table.item(row, 9).setText("—" if score is None else f"{score:.3f}")
        self.table.item(row, 7).setText("Saved" if status == "success" else "")
        self.table.item(row, 7).setToolTip(record.get("archive", ""))
        self.summary.setText(f"{index + 1}/{len(self.selected_rows)} · {self.presets[row].id} · {status}")

    def _stop(self):
        if self.worker is not None:
            self.worker.stop()
            self.stop_button.setEnabled(False)
            self.summary.setText("Stopping; waiting for restoration and data saving…")

    def _finished(self):
        self.window._on_scan_finished()
        self.editor.setEnabled(True)
        self.search.setEnabled(True)
        self.table.setEnabled(True)
        for button in self.selection_buttons + [self.start_button, self.close_button]:
            button.setEnabled(True)
        self.stop_button.setEnabled(False)
        completed = sum(record["status"] == "success" for record in self.worker.records)
        review = sum(record.get("fit_quality", {}).get("quality") in ("review", "invalid")
                     for record in self.worker.records)
        self.summary.setText(f"{self.worker.status}: {completed}/{len(self.selected_rows)} completed. "
                             f"{review} need fit review. {self.worker.error}\nSaved: {self.worker.directory}")

    def reject(self):
        if self.worker is not None and self.worker.isRunning():
            self._stop()
            return
        valid = self._commit_editor()
        if not valid or self.presets != self.originals:
            answer = QMessageBox.question(self, "Unsaved parameters", "Discard parameter changes that have not been saved as defaults?", QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel)
            if answer != QMessageBox.Discard:
                return
        super().reject()

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            self._stop()
            event.ignore()
            return
        super().closeEvent(event)
