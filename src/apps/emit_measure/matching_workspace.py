"""Matching suggestions, explicit K1 application/restoration and remeasurement."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
import json
import math
import uuid
from pathlib import Path

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QLineEdit, QComboBox, QPushButton, QTableWidget, QTableWidgetItem,
    QFileDialog, QMessageBox, QMenu, QDialog, QDialogButtonBox, QSplitter, QHeaderView, QScrollArea, QFrame, QTabWidget, QSizePolicy, QAbstractItemView)
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from half_linac.src.shared.machine_profile import (
    build_model_backend, build_model_snapshot, model_snapshot_lattice_overrides,
    resolve_app_runtime_paths, resolve_write_target, resolve_channel, require_workflow_write_allowed,
)
from .matching import (Point, Twiss, MeasurementBaseline, MagnetLimit, MatchingRequest, Cancelled,
                       solve_matching, compare_measurement, save_result, load_result, export_csv)
from .matching_model import ElegantMatchingModel
from .matching_import import import_measurement, measurement_from_dict
from .matching_execution import K1Execution, save_execution


class MatchingWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try:
            result = self.operation(self.isInterruptionRequested)
            if not self.isInterruptionRequested():
                self.completed.emit(result)
            else:
                self.failed.emit("Cancelled")
        except Exception as exc:
            self.failed.emit(str(exc))


class MatchingWorkspace(QWidget):
    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.context = context
        self.owner = parent
        self.worker = None
        self.operation_buttons = []
        self.execution = None
        self.active_execution_record = None
        self.result = None
        self.comparison = None
        self.stale = True
        self.provenance = {"kind": "manual"}
        self.loaded_measurement = None
        self.extra_overrides = {}
        self.model = None
        self.revision = 0
        self.updating = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(8)
        self.status = QLabel("Load a dual-plane measurement to prepare matching.")
        self.status.setObjectName("twissStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        split = QSplitter(Qt.Horizontal)
        layout.addWidget(split, 1)
        scroll = QScrollArea()
        self.input_scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        panel = QWidget()
        scroll.setWidget(panel)
        self.input_tabs = QTabWidget()
        self.input_tabs.setMinimumWidth(500)
        self.input_tabs.addTab(scroll, "Measurement")
        self.input_tabs.setTabToolTip(0, "Measurement input and matching target")
        split.addWidget(self.input_tabs)
        left = QVBoxLayout(panel)
        left.setContentsMargins(0, 0, 6, 0)
        left.setSpacing(12)
        self.input_panel = self.input_tabs
        measurement_card = self._card(left, "Measurement input",
                                      "Load both planes and check the reference. Energy uses measurement + model RF.")
        measurement_actions = QHBoxLayout()
        measurement_card.addLayout(measurement_actions)
        load_button = QPushButton("Load Measurement…")
        load_button.clicked.connect(self.load_measurement)
        self.use_result_button = QPushButton("Use Result")
        self.use_result_button.setToolTip("Load both planes from a measurement result for matching.")
        self.use_result_menu = QMenu(self.use_result_button)
        self.use_quad_scan_result_action = self.use_result_menu.addAction("Quad-Scan Result")
        self.use_multi_screen_result_action = self.use_result_menu.addAction("Multi-Screen Result")
        self.use_quad_scan_result_action.triggered.connect(self.latest_scan)
        self.use_multi_screen_result_action.triggered.connect(self.latest_multi)
        self.use_result_button.setMenu(self.use_result_menu)
        for button in (load_button, self.use_result_button):
            button.setProperty("role", "diagnostic")
            button.setProperty("compact", "true")
            button.setMinimumHeight(30)
            measurement_actions.addWidget(button)
            self.operation_buttons.append(button)
        grid = QGridLayout()
        measurement_card.addLayout(grid)
        target_card = self._card(left, "Matching target")
        target_grid = QGridLayout()
        target_card.addLayout(target_grid)
        self.line = QComboBox()
        self.source = QComboBox()
        self.target = QComboBox()
        for combo in (self.source, self.target):
            combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.source_edge = QComboBox(); self.source_edge.addItems(["entrance", "exit"])
        self.target_edge = QComboBox(); self.target_edge.addItems(["exit", "entrance"])
        self.energy = QLineEdit()
        self.tolerance = QLineEdit("0.01")
        self.acceptance = QLineEdit("0.01")
        self.edits = {}
        for i, (label, widget) in enumerate([
            ("Model line", self.line), ("Energy [MeV]", self.energy),
            ("Mismatch tolerance (Bmag − 1)", self.tolerance),
        ]):
            field_grid = grid if i < 2 else target_grid
            field_row = i if i < 2 else 1
            field_grid.addWidget(QLabel(label), field_row, 0)
            field_grid.addWidget(widget, field_row, 1)
        reference_row = QHBoxLayout()
        reference_row.addWidget(QLabel("Measurement"))
        reference_row.addWidget(self.source, 1)
        reference_row.addWidget(QLabel("Boundary"))
        reference_row.addWidget(self.source_edge)
        grid.addLayout(reference_row, 2, 0, 1, 2)
        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("Target"))
        target_row.addWidget(self.target, 1)
        target_row.addWidget(QLabel("Boundary"))
        target_row.addWidget(self.target_edge)
        target_grid.addLayout(target_row, 0, 0, 1, 2)
        twiss_grid = QGridLayout()
        grid.addLayout(twiss_grid, 3, 0, 1, 2)
        for column, label in enumerate(("Plane", "β [m]", "α", "ε [mm mrad]")):
            heading = QLabel(label)
            heading.setProperty("role", "field")
            twiss_grid.addWidget(heading, 0, column)
        for row, plane in enumerate(("x", "y"), 1):
            twiss_grid.addWidget(QLabel(plane.upper()), row, 0)
            for column, field in enumerate(("beta", "alpha", "emittance"), 1):
                edit = QLineEdit()
                edit.setMinimumWidth(0)
                edit.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
                edit.setAlignment(Qt.AlignRight)
                edit.setToolTip(f"{plane.upper()} · " + ("β [m]" if field == "beta" else
                                "α" if field == "alpha" else "Geometric ε [mm mrad]"))
                self.edits[plane, field] = edit
                twiss_grid.addWidget(edit, row, column)
                twiss_grid.setColumnStretch(column, 1)
        self.envelopes = {}
        self.envelope_button = QPushButton("Beam size limits… · None")
        self.envelope_button.setProperty("role", "diagnostic")
        self.envelope_button.setProperty("compact", "true")
        self.envelope_button.clicked.connect(self.edit_envelope_limits)
        target_grid.addWidget(self.envelope_button, 2, 0, 1, 2)
        for plane in ("x", "y"):
            edit = QLineEdit(self)
            edit.hide()
            edit.textChanged.connect(self.update_envelope_summary)
            self.envelopes[plane] = edit
        left.addStretch()
        magnet_scroll = QScrollArea()
        magnet_scroll.setWidgetResizable(True)
        magnet_scroll.setFrameShape(QFrame.NoFrame)
        self.input_scroll = magnet_scroll
        magnet_panel = QWidget()
        magnet_scroll.setWidget(magnet_panel)
        magnet_layout = QVBoxLayout(magnet_panel)
        magnet_layout.setContentsMargins(0, 0, 6, 0)
        self.input_tabs.addTab(magnet_scroll, "Magnets && limits")
        magnet_card = self._card(magnet_layout, "Magnet snapshot & limits",
                                 "K1 [m⁻²] · Set device bounds and max |ΔK1|; scan limits are not used.")
        self._buttons(magnet_card, [("Read current K1 snapshot", self.current_snapshot)])
        preset_row = QHBoxLayout(); magnet_card.addLayout(preset_row)
        self.presets = QComboBox(); preset_row.addWidget(self.presets, 1)
        preset = QPushButton("Select group"); preset.setProperty("compact", "true"); preset.clicked.connect(self.select_group); preset_row.addWidget(preset)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Use", "Quad", "Current K1", "Lower", "Upper", "Max |Δ|"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setMinimumHeight(230)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeaderItem(5).setText("Max |ΔK1|")
        magnet_card.addWidget(self.table, 1)
        self.calculate = QPushButton("Calculate suggestion")
        self.calculate.setProperty("role", "primary")
        self.calculate.clicked.connect(self.calculate_match)
        self.result_tabs = QTabWidget()
        split.addWidget(self.result_tabs)
        right = QWidget()
        self.result_tabs.addTab(right, "Matching result")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(6, 0, 0, 0)
        right_layout.setSpacing(8)
        optics_card = self._card(right_layout, "Optics & proposed K1",
                                 "Compare calculated optics and proposed K1 changes.")
        self.figure = Figure(figsize=(7, 3.5), tight_layout=True)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setMinimumHeight(200)
        self.canvas.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        optics_card.addWidget(self.canvas, 1)
        self.results_table = QTableWidget(0, 4)
        self.results_table.setHorizontalHeaderLabels(["Quad", "Current K1", "Suggested K1", "ΔK1"])
        self.results_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.results_table.setMinimumHeight(140)
        self.results_table.setMaximumHeight(210)
        self.results_table.verticalHeader().hide()
        self.results_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.results_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.results_table.setAlternatingRowColors(True)
        self.results_table.setToolTip("K1 and ΔK1 in m⁻²")
        optics_card.addWidget(self.results_table)
        self._buttons(optics_card, [("Save JSON", self.save), ("Export K1 CSV", self.export),
                                     ("Open matching archive", self.open_archive)])
        execution_scroll = QScrollArea()
        execution_scroll.setWidgetResizable(True)
        execution_scroll.setFrameShape(QFrame.NoFrame)
        execution_panel = QWidget()
        execution_scroll.setWidget(execution_panel)
        execution_layout = QVBoxLayout(execution_panel)
        execution_layout.setContentsMargins(6, 0, 0, 0)
        execution_layout.setSpacing(10)
        self.result_tabs.addTab(execution_scroll, "Apply && verify")
        for tabs in (self.input_tabs, self.result_tabs):
            tabs.setElideMode(Qt.ElideNone)
            tabs.setUsesScrollButtons(True)
            tabs.tabBar().setExpanding(False)
        execution_card = self._card(execution_layout, "Apply & restore",
                                    "Apply writes K1 to the displayed backend. Energy settings remain unchanged.")
        execution_row = QHBoxLayout(); execution_card.addLayout(execution_row)
        self.apply_button = QPushButton("Apply suggested K1")
        self.restore_button = QPushButton("Restore previous K1")
        self.apply_button.setProperty("role", "control")
        self.restore_button.setProperty("role", "danger")
        self.apply_button.clicked.connect(self.apply_suggestion)
        self.restore_button.clicked.connect(self.restore_previous)
        execution_row.addWidget(self.apply_button); execution_row.addWidget(self.restore_button)
        self.execution_label = QLabel("No K1 application. Energy settings remain unchanged.")
        self.execution_label.setWordWrap(True); execution_card.addWidget(self.execution_label)
        comparison_card = self._card(execution_layout, "Verify with remeasurement",
                                     "Requires a calibrated model. Twiss does not certify orbit, losses, dispersion or coupling.")
        acceptance_row = QHBoxLayout(); comparison_card.addLayout(acceptance_row)
        acceptance_row.addWidget(QLabel("Remeasurement Bmag − 1 tolerance")); acceptance_row.addWidget(self.acceptance)
        self._buttons(comparison_card, [("Compare measurement JSON", self.compare_file),
                                     ("Compare edited inputs", self.compare_inputs)])
        self.comparison_label = QLabel("No remeasurement. Acceptance tolerance is user-defined.")
        self.comparison_label.setWordWrap(True); comparison_card.addWidget(self.comparison_label)
        execution_layout.addStretch()
        split.setChildrenCollapsible(False)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        split.setSizes([560, 840])
        self.cancel = QPushButton("Cancel calculation")
        self.cancel.setProperty("role", "danger")
        self.cancel.clicked.connect(self.stop); self.cancel.setEnabled(False)
        actions = QHBoxLayout(); actions.addStretch(); actions.addWidget(self.calculate); actions.addWidget(self.cancel)
        actions.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(actions)
        self.line.currentIndexChanged.connect(self.change_line)
        for widget in [self.source, self.target, self.source_edge, self.target_edge]:
            widget.currentIndexChanged.connect(self.invalidate)
        for edit in [self.energy, self.tolerance, *self.edits.values(), *self.envelopes.values()]:
            edit.textChanged.connect(self.invalidate)
        self.table.itemChanged.connect(self.invalidate)
        try:
            backend = build_model_backend(context)
            self.line.blockSignals(True)
            for line in backend.get_model_lines():
                self.line.addItem(line.name, line.name)
            default = self.line.findData(backend.line_name)
            self.line.setCurrentIndex(max(0, default))
            self.line.blockSignals(False)
            self.change_line()
        except Exception as exc:
            self.status.setText("Model unavailable: " + str(exc)); self.calculate.setEnabled(False)
        self.timer = QTimer(self); self.timer.setInterval(3000)
        self.timer.timeout.connect(self.check_model); self.timer.start()
        self.plot_palette = {"plot_bg": "#ffffff", "plot_card_bg": "#ffffff",
                             "plot_text": "#333333", "plot_grid": "#dddddd"}
        self.apply_theme(self.plot_palette)
        paths = resolve_app_runtime_paths(Path(__file__).resolve().parent, self.context)
        self.execution_runs = paths["runs_dir"]
        self.execution_latest = self.execution_runs.parent / "matching_execution_latest.json"
        if self.execution_latest.exists():
            try:
                self.execution = json.loads(self.execution_latest.read_text())
                self.validate_execution_identity(self.execution)
                self.execution_label.setText("Previous K1 operation: " + self.execution["status"])
            except Exception as exc:
                self.execution = None
                self.execution_label.setText("Could not load previous K1 operation: " + str(exc))
        self.update_execution_buttons()

    def apply_theme(self, palette):
        self.plot_palette = palette
        self.figure.patch.set_facecolor(palette["plot_card_bg"])
        if not self.figure.axes:
            axes = self.figure.add_subplot(111)
            axes.set_axis_off()
            axes.text(0.5, 0.5, "Calculate a suggestion to compare optics",
                      ha="center", va="center", transform=axes.transAxes)
        for axes in self.figure.axes:
            axes.set_facecolor(palette["plot_bg"])
            axes.tick_params(colors=palette["plot_text"], labelsize=8)
            axes.xaxis.label.set_color(palette["plot_text"])
            axes.yaxis.label.set_color(palette["plot_text"])
            for text in axes.texts: text.set_color(palette["plot_text"])
            for spine in axes.spines.values(): spine.set_color(palette["plot_grid"])
            legend = axes.get_legend()
            if legend:
                legend.get_frame().set_facecolor(palette["plot_bg"])
                for text in legend.get_texts(): text.set_color(palette["plot_text"])
        self.canvas.draw_idle()

    def _card(self, layout, title, description=None):
        card = QFrame(self)
        card.setObjectName("plotCard")
        content = QVBoxLayout(card)
        content.setContentsMargins(12, 10, 12, 12)
        content.setSpacing(8)
        heading = QLabel(title)
        heading.setObjectName("panelTitle")
        font = heading.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 1)
        heading.setFont(font)
        content.addWidget(heading)
        if description:
            hint = QLabel(description)
            hint.setProperty("role", "field")
            hint.setWordWrap(True)
            content.addWidget(hint)
        layout.addWidget(card)
        return content

    def _buttons(self, layout, buttons):
        row = QHBoxLayout(); layout.addLayout(row)
        for text, callback in buttons:
            button = QPushButton(text)
            button.setProperty("role", "diagnostic")
            button.setProperty("compact", "true")
            button.setMinimumHeight(30)
            button.clicked.connect(callback); row.addWidget(button)
            self.operation_buttons.append(button)

    def update_envelope_summary(self):
        limits = [f"{plane.upper()} ≤ {edit.text().strip()} mm"
                  for plane, edit in self.envelopes.items() if edit.text().strip()]
        summary = ", ".join(limits) if limits else "None"
        self.envelope_button.setText("Beam size limits… · " + summary)
        self.envelope_button.setToolTip("Optional RMS (1σ) limits along the matching path. " + summary)

    def edit_envelope_limits(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Beam size limits")
        layout = QVBoxLayout(dialog)
        note = QLabel("Optional RMS (1σ) limits along the matching path. Leave blank for no limit.")
        note.setWordWrap(True)
        layout.addWidget(note)
        grid = QGridLayout()
        layout.addLayout(grid)
        edits = {}
        for row, (plane, current) in enumerate(self.envelopes.items()):
            edit = QLineEdit(current.text())
            edit.setPlaceholderText("No limit")
            edits[plane] = edit
            grid.addWidget(QLabel(plane.upper() + " max RMS [mm]"), row, 0)
            grid.addWidget(edit, row, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        layout.addWidget(buttons)

        def accept():
            for plane, edit in edits.items():
                raw = edit.text().strip()
                if not raw:
                    continue
                try:
                    value = float(raw)
                    if not math.isfinite(value) or value <= 0:
                        raise ValueError
                except ValueError:
                    QMessageBox.warning(dialog, "Beam size limits",
                                        f"{plane.upper()} max RMS must be a positive finite number, or blank.")
                    edit.setFocus()
                    edit.selectAll()
                    return
            dialog.accept()

        buttons.accepted.connect(accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec_() == QDialog.Accepted:
            for plane, edit in edits.items():
                self.envelopes[plane].setText(edit.text().strip())
        dialog.deleteLater()

    def error(self, message):
        self.status.setText(str(message))
        QMessageBox.warning(self, "Twiss Matching", str(message))

    def invalidate(self, *_):
        if self.updating:
            return
        self.revision += 1
        self.stale = True
        self.update_execution_buttons()
        if self.result:
            self.status.setText("Suggestion is stale: inputs changed. Recalculate before exporting K1.")

    def check_model(self):
        if self.worker and self.worker.isRunning():
            return
        if self.model and self.result and not self.stale:
            try:
                self.model.assert_unchanged(self.result.model["fingerprint"])
            except Exception as exc:
                self.invalidate(); self.status.setText(str(exc))

    def change_line(self):
        try:
            model = ElegantMatchingModel(build_model_backend(self.context, line_name=self.line.currentData()))
            self.model = model
            self.extra_overrides = {}
            self.updating = True
            for combo in (self.source, self.target):
                combo.clear(); combo.addItems([n for n in model.names if model.names.count(n) == 1])
            quads = [n for n in model.names if model.elements[n]["TYPE"].upper() == "QUAD"]
            self.table.setRowCount(len(quads))
            for r, name in enumerate(quads):
                check = QTableWidgetItem(); check.setCheckState(Qt.Unchecked)
                self.table.setItem(r, 0, check)
                item = QTableWidgetItem(name); item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(r, 1, item)
                for c in range(2, 6): self.table.setItem(r, c, QTableWidgetItem(""))
                element = next((e for e in self.context.profile.elements if e.id == name), None)
                # Only explicitly named K1 bounds, never scan presets or current limits.
                k1 = dict(element.limits_for("K1")) if element else {}
                if isinstance(k1, dict):
                    for c, key, alias in ((3, "lower", "low"), (4, "upper", "high"), (5, "max_change", "max_change")):
                        value = k1.get(key, k1.get(alias))
                        if value is not None: self.table.item(r, c).setText(str(value))
            self.presets.clear()
            for names in ([f"QL{i:02d}" for i in range(7, 13)], [f"QT{i:02d}" for i in range(1, 7)]):
                if set(names) <= set(quads): self.presets.addItem(names[0] + "–" + names[-1], names)
            self.updating = False
            self.invalidate()
        except Exception as exc:
            self.updating = False; self.model = None; self.status.setText(str(exc))

    def select_group(self):
        names = self.presets.currentData()
        if not names: return
        for r in range(self.table.rowCount()):
            self.table.item(r, 0).setCheckState(Qt.Checked if self.table.item(r, 1).text() in names else Qt.Unchecked)
        self.target.setCurrentText(names[-1]); self.target_edge.setCurrentText("exit")
        row = next(r for r in range(self.table.rowCount()) if self.table.item(r, 1).text() == names[0])
        self.table.scrollToItem(self.table.item(row, 1))

    def overrides(self):
        result = deepcopy(self.extra_overrides)
        for r in range(self.table.rowCount()):
            text = self.table.item(r, 2).text().strip()
            if text: result.setdefault(self.table.item(r, 1).text(), {})["K1"] = float(text)
        return result

    def measurement(self):
        model = self.model
        if model is None: raise ValueError("Model unavailable")
        planes = {p: Twiss(float(self.edits[p, "beta"].text()), float(self.edits[p, "alpha"].text()),
                           float(self.edits[p, "emittance"].text()) * 1e-6) for p in ("x", "y")}
        provenance = deepcopy(self.provenance)
        provenance.pop("declaration_at", None)
        provenance["state_assumption"] = "matching_immediately_after_measurement"
        provenance["entered_values"] = {p: asdict(t) for p, t in planes.items()}
        if self.loaded_measurement is None or planes != self.loaded_measurement.planes:
            provenance.pop("uncertainty", None)
            provenance["kind"] = "manual_or_edited"
        value = MeasurementBaseline(Point(self.source.currentText(), self.source_edge.currentText()),
            float(self.energy.text()), planes, self.context.profile.machine.id,
            self.context.control_backend.name, self.line.currentData(), self.overrides(), provenance,
            # Keep the archive/solver field compatible; the GUI assumes matching
            # follows measurement, rather than requesting a manual declaration.
            same_state_declared=True)
        value.validate()
        return value

    def populate(self, measurement):
        if measurement.machine != self.context.profile.machine.id or measurement.backend != self.context.control_backend.name:
            raise ValueError("Measurement machine/control backend differs from this workspace")
        if measurement.point.element not in self.model.names:
            raise ValueError("Measurement reference is not on this line")
        self.updating = True
        self.source.setCurrentText(measurement.point.element); self.source_edge.setCurrentText(measurement.point.edge)
        self.energy.setText(str(measurement.energy_mev))
        for p, twiss in measurement.planes.items():
            for key in ("beta", "alpha", "emittance"):
                value = getattr(twiss, key) * (1e6 if key == "emittance" else 1)
                self.edits[p, key].setText(f"{value:.12g}")
        self.provenance = deepcopy(measurement.provenance)
        self.loaded_measurement = deepcopy(measurement)
        self.extra_overrides = {name: {k: v for k, v in fields.items() if k != "K1"}
                                for name, fields in measurement.overrides.items()}
        for r in range(self.table.rowCount()):
            k1 = measurement.overrides.get(self.table.item(r, 1).text(), {}).get("K1")
            self.table.item(r, 2).setText("" if k1 is None else str(k1))
        self.updating = False; self.invalidate()
        self.status.setText("Measurement loaded. Read current K1 snapshot if required K1 values are missing.")

    def load_measurement(self):
        path, _ = QFileDialog.getOpenFileName(self, "Measurement / scan metadata JSON", "", "JSON (*.json)")
        if path:
            try: self.populate(import_measurement(json.loads(Path(path).read_text()), line=self.line.currentData()))
            except Exception as exc: self.error(exc)

    def latest_scan(self):
        try:
            parent = self.owner
            metadata = deepcopy(parent.loaded_scan_metadata or parent.pending_scan_metadata or {})
            metadata["fit_summary"] = deepcopy(parent.latest_emit_fit_summary or {})
            metadata["quad"] = parent._latest_fit_source_quad()
            self.populate(import_measurement(metadata, line=self.line.currentData()))
        except Exception as exc: self.error(exc)

    def latest_multi(self):
        try:
            from .multi_screen import measurement_archive_payload
            workspace = self.owner.multi_screen_workspace
            payload = measurement_archive_payload(workspace.session, reconstruction=workspace.reconstruction)
            self.populate(import_measurement(payload, line=self.line.currentData()))
        except Exception as exc: self.error(exc)

    def apply_snapshot(self, metadata):
        if metadata.get("machine_id") != self.context.profile.machine.id or metadata.get("control_backend") != self.context.control_backend.name:
            self.error("Snapshot machine/control backend differs from this workspace")
            return
        overrides = model_snapshot_lattice_overrides(metadata)
        if not overrides:
            self.error("Snapshot contains no usable K1 values")
            return
        self.provenance["supplemental_snapshot"] = deepcopy(metadata)
        for r in range(self.table.rowCount()):
            value = overrides.get(self.table.item(r, 1).text(), {}).get("K1")
            if value is not None: self.table.item(r, 2).setText(str(value))
        self.status.setText("Current K1 snapshot loaded.")

    def current_snapshot(self):
        if not self.model: return
        fields = [(self.table.item(r, 1).text(), "K1") for r in range(self.table.rowCount())]
        def read_snapshot(cancel):
            from epics import caget

            def read(pv):
                if cancel():
                    raise Cancelled("Snapshot read cancelled")
                return caget(pv, timeout=2.0)

            return build_model_snapshot(
                self.context, fields, source="live_from_" + self.context.control_backend.name,
                pv_reader=read,
            ).as_metadata()

        self.run_task(read_snapshot, self.apply_snapshot)

    def run_task(self, operation, completed):
        if self.worker and self.worker.isRunning():
            self.error("A matching operation is already running"); return
        self.input_panel.setEnabled(False)
        self.apply_button.setEnabled(False)
        self.restore_button.setEnabled(False)
        self.calculate.setEnabled(False)
        self.cancel.setEnabled(True)
        for button in self.operation_buttons: button.setEnabled(False)
        self.worker = MatchingWorker(operation, self)
        self.worker.completed.connect(completed)
        self.worker.failed.connect(lambda message: self.status.setText(message))
        self.worker.finished.connect(self.task_finished)
        self.worker.start()

    def task_finished(self):
        self.cancel.setEnabled(False)
        self.input_panel.setEnabled(True)
        self.calculate.setEnabled(self.model is not None)
        for button in self.operation_buttons: button.setEnabled(True)
        if self.active_execution_record is not None and self.worker.isInterruptionRequested():
            self.execution_finished(self.active_execution_record)
        self.active_execution_record = None
        self.update_execution_buttons()

    def selected_magnet_limits(self):
        magnets = {}
        problems = []
        first_invalid = None
        labels = {2: "Current K1", 3: "Lower", 4: "Upper", 5: "Max |ΔK1|"}
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).checkState() != Qt.Checked:
                continue
            name = self.table.item(row, 1).text()
            values = {}
            for column, label in labels.items():
                item = self.table.item(row, column)
                raw = item.text().strip()
                try:
                    value = float(raw)
                    if not math.isfinite(value):
                        raise ValueError()
                    values[column] = value
                except ValueError:
                    problems.append(f"{name}: {label} — " + ("required" if not raw else "enter a finite number"))
                    if first_invalid is None:
                        first_invalid = item
            if len(values) == 4:
                limit = MagnetLimit(values[3], values[4], values[5])
                try:
                    limit.bounds(values[2])
                except ValueError as exc:
                    problems.append(f"{name}: {exc}")
                    if first_invalid is None:
                        first_invalid = self.table.item(row, 3)
                magnets[name] = limit
        if problems:
            self.input_tabs.setCurrentIndex(1)
            self.input_scroll.ensureWidgetVisible(self.table)
            self.table.setCurrentItem(first_invalid)
            self.table.scrollToItem(first_invalid)
            self.table.setFocus()
            shown = problems[:12]
            if len(problems) > 12:
                shown.append(f"… and {len(problems) - 12} more fields.")
            raise ValueError("Complete the selected quadrupole settings [K1: m⁻²]:\n" +
                             "\n".join(shown) +
                             "\nLower / Upper bound K1; Max |ΔK1| limits the change from Current K1.")
        if not magnets:
            raise ValueError("Select at least one matching quadrupole.")
        return magnets

    def calculate_match(self):
        try:
            magnets = self.selected_magnet_limits()
            measurement = self.measurement()
            envelope = {p: float(edit.text()) * 1e-3 if edit.text().strip() else None for p, edit in self.envelopes.items()}
            request = MatchingRequest(measurement, Point(self.target.currentText(), self.target_edge.currentText()),
                                      magnets, float(self.tolerance.text()), envelope)
            self.model.assert_unchanged(self.model.fingerprint)
            self.stale = True
            self.status.setText("Calculating fixed-boundary dual-plane match…")
            self.run_task(lambda cancel: solve_matching(self.model, request, cancel), self.show_result)
        except Exception as exc: self.error(exc)

    def show_result(self, result):
        self.result = result; self.comparison = None; self.stale = False
        self.comparison_label.setText("Not yet remeasured")
        self.draw_result()
        self.update_execution_buttons()
        self.archive()

    def draw_result(self):
        result = self.result
        labels = {"model_target_met": "Model target met", "model_improved": "Model improved; target not met",
                  "no_usable_suggestion": "No usable suggestion"}
        self.status.setText(f"{labels[result.status]} · Bmag X/Y: "
            f"{result.diagnostics['after_bmag']['x']:.5g} / {result.diagnostics['after_bmag']['y']:.5g} · "
            f"response rank {result.diagnostics['rank']}/4 · {result.diagnostics['solver_message']}\n"
            "Envelope: quadrupole extrema + boundaries; RF/other interiors are not certified. Design RMS uses measured normalized emittance.")
        self.figure.clear()
        axes = self.figure.subplots(3, 2)
        for col, p in enumerate(("x", "y")):
            for label, profiles in (("Current", result.current), ("Design", result.design), ("Candidate", result.candidate)):
                rows = profiles[p]
                for row, key in enumerate(("beta", "alpha", "sigma_m")):
                    axes[row, col].plot([v["s_m"] for v in rows],
                        [v[key] * (1e3 if key == "sigma_m" else 1) for v in rows], label=label)
                    axes[row, col].set_ylabel(p.upper() + " " + ("RMS [mm]" if key == "sigma_m" else key))
                    axes[row, col].grid(alpha=0.2)
            axes[0, col].legend(fontsize=8); axes[2, col].set_xlabel("s from match entrance [m]")
        self.apply_theme(self.plot_palette)
        self.results_table.setRowCount(len(result.magnets))
        for r, (q, value) in enumerate(result.magnets.items()):
            for c, text in enumerate((q, value["current"], value["suggested"], value["change"])):
                item = QTableWidgetItem(str(text)); item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.results_table.setItem(r, c, item)

    def archive(self):
        try:
            paths = resolve_app_runtime_paths(Path(__file__).resolve().parent, self.context)
            if not hasattr(self, "archive_path") or self.comparison is None:
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                self.archive_path = paths["runs_dir"] / ("matching_" + stamp) / "matching.json"
            save_result(self.archive_path, self.result, self.comparison)
        except Exception as exc: self.status.setText("Result available; archive failed: " + str(exc))

    def update_execution_buttons(self):
        if not hasattr(self, "apply_button"):
            return
        busy = bool(self.worker and self.worker.isRunning())
        pending = self.execution and self.execution.get("attempted") and self.execution.get("status") not in ("applied", "restored")
        self.apply_button.setEnabled(bool(self.result and not self.stale and
            self.result.status in ("model_target_met", "model_improved") and not busy and not pending))
        self.restore_button.setEnabled(bool(self.execution and self.execution.get("attempted") and
            self.execution.get("status") != "restored" and not busy))

    def validate_execution_identity(self, record):
        if record.get("machine") != self.context.profile.machine.id or record.get("backend") != self.context.control_backend.name:
            raise ValueError("Saved K1 operation belongs to a different machine/backend")
        uuid.UUID(hex=record["execution_id"])
        for name, target in record["targets"].items():
            expected = resolve_write_target(self.context, name, quantity="K1").pv_name
            if target["pv"] != expected:
                raise ValueError(f"{name}: K1 channel mapping changed; cannot restore automatically")

    def persist_execution(self, record):
        identifier = uuid.UUID(hex=record["execution_id"]).hex
        path = self.execution_runs / ("matching_execution_" + identifier) / "execution.json"
        save_execution(path, record)
        if record.get("attempted"):
            save_execution(self.execution_latest, record)
            self.execution = record

    def execution_allowed(self):
        require_workflow_write_allowed(self.context, "emit_measure", "Matching K1 application/restoration")

    def execution_io(self, cancel):
        from epics import caget, caput
        return K1Execution(lambda pv: caget(pv, timeout=2.0),
                           lambda pv, value: caput(pv, value, wait=True, timeout=5.0),
                           self.persist_execution, self.execution_allowed, cancel)

    def check_other_operations(self):
        if self.worker and self.worker.isRunning():
            raise ValueError("Wait for the current matching operation")
        if self.owner and hasattr(self.owner, "_scan_is_running") and self.owner._scan_is_running():
            raise ValueError("Stop the emittance scan before applying or restoring K1")

    def apply_suggestion(self):
        try:
            self.check_other_operations()
            self.execution_allowed()
            if not self.result or self.stale or self.result.status not in ("model_target_met", "model_improved"):
                raise ValueError("Calculate a current, usable suggestion first")
            if self.execution and self.execution.get("attempted") and self.execution["status"] not in ("applied", "restored"):
                raise ValueError("Restore the incomplete K1 operation before applying another suggestion")
            self.model.assert_unchanged(self.result.model["fingerprint"])
            measurement = self.result.request["measurement"]
            if measurement["machine"] != self.context.profile.machine.id or measurement["backend"] != self.context.control_backend.name:
                raise ValueError("Suggestion machine/backend does not match this workspace")
            targets = {}
            for name, values in self.result.magnets.items():
                target = resolve_write_target(self.context, name, quantity="K1")
                value = values["suggested"]
                limit = MagnetLimit(**self.result.request["magnets"][name])
                lower, upper = limit.bounds(values["current"])
                if not lower <= value <= upper or not target.machine_limit.contains(value):
                    raise ValueError(f"{name}: suggested K1 violates configured limits")
                targets[name] = {"pv": target.pv_name, **values}
            points = [Point(**measurement["point"]), Point(**self.result.start), Point(**self.result.request["target"])]
            expected = {}
            from half_linac.src.shared.machine_profile.model_snapshot import resolve_model_snapshot_field_spec
            for name in self.model.required_quads(points):
                spec = resolve_model_snapshot_field_spec(self.context, name, "K1")
                if spec.get("conversion", {}).get("type", "direct") != "direct":
                    raise ValueError(f"{name}: automatic application requires a direct K1 mapping")
                pv = resolve_channel(self.context, name, spec["logical_channel"])
                if name in targets and pv != targets[name]["pv"]:
                    raise ValueError(f"{name}: model K1 and writable K1 use different channels; automatic application is unavailable")
                expected[pv] = measurement["overrides"][name]["K1"]
            record = {"schema": "matching_k1_execution_v1", "execution_id": uuid.uuid4().hex,
                      "machine": self.context.profile.machine.id, "backend": self.context.control_backend.name,
                      "suggestion_created_at": self.result.created_at,
                      "model_fingerprint": self.result.model["fingerprint"],
                      "measurement_energy_mev": measurement["energy_mev"]}
            self.stale = True
            self.active_execution_record = record
            self.execution_label.setText("Applying K1 to " + self.context.control_backend.name + "…")
            self.run_task(lambda cancel: self.execution_io(cancel).apply(record, targets, expected), self.execution_finished)
        except Exception as exc:
            self.error(exc)

    def restore_previous(self):
        try:
            self.check_other_operations()
            self.execution_allowed()
            if not self.execution or not self.execution.get("attempted") or self.execution["status"] == "restored":
                raise ValueError("No previous K1 application to restore")
            self.validate_execution_identity(self.execution)
            for name in self.execution["attempted"]:
                target = resolve_write_target(self.context, name, quantity="K1")
                if not target.machine_limit.contains(self.execution["original"][name]):
                    raise ValueError(f"{name}: saved K1 is outside current device limits")
            record = self.execution
            self.stale = True
            self.active_execution_record = record
            self.execution_label.setText("Restoring saved K1 to " + self.context.control_backend.name + "…")
            self.run_task(lambda cancel: self.execution_io(cancel).restore(record), self.execution_finished)
        except Exception as exc:
            self.error(exc)

    def execution_finished(self, record):
        self.invalidate()
        labels = {"applied": "Suggested K1 applied and PV values verified; remeasure the beam.",
                  "restored": "Previous K1 restored and PV values verified.",
                  "apply_failed": "Application stopped. Restore remains available for attempted magnets.",
                  "restore_failed": "Restoration incomplete; retry Restore previous K1."}
        message = labels.get(record["status"], record["status"])
        if record["status"] == "apply_failed" and not record.get("attempted"):
            message = "Preflight failed; no K1 writes were attempted."
        if record.get("errors"):
            message += "\n" + "\n".join(record["errors"])
        self.execution_label.setText(message)
        self.status.setText(message)
        self.update_execution_buttons()

    def save(self):
        if not self.result: return
        path, _ = QFileDialog.getSaveFileName(self, "Save matching result", "matching.json", "JSON (*.json)")
        if path:
            try: save_result(path, self.result, self.comparison)
            except Exception as exc: self.error(exc)

    def export(self):
        try:
            if not self.result or self.stale: raise ValueError("Calculate a current suggestion before exporting K1")
            self.model.assert_unchanged(self.result.model["fingerprint"])
            path, _ = QFileDialog.getSaveFileName(self, "Export K1 suggestion", "matching_k1.csv", "CSV (*.csv)")
            if path: export_csv(path, self.result)
        except Exception as exc: self.error(exc)

    def open_archive(self):
        path, _ = QFileDialog.getOpenFileName(self, "Matching archive", "", "JSON (*.json)")
        if not path: return
        try:
            result, comparison = load_result(path)
            line_index = self.line.findData(result.request["measurement"]["line"])
            if line_index < 0: raise ValueError("Archive line is unavailable")
            self.line.setCurrentIndex(line_index)
            self.populate(measurement_from_dict(result.request["measurement"]))
            self.updating = True
            target = result.request["target"]
            self.target.setCurrentText(target["element"]); self.target_edge.setCurrentText(target["edge"])
            self.tolerance.setText(str(result.request["tolerance"]))
            for p, value in result.request["envelope_m"].items():
                self.envelopes[p].setText("" if value is None else str(value * 1e3))
            for r in range(self.table.rowCount()):
                limit = result.request["magnets"].get(self.table.item(r, 1).text())
                self.table.item(r, 0).setCheckState(Qt.Checked if limit else Qt.Unchecked)
                if limit:
                    for c, key in ((3, "lower"), (4, "upper"), (5, "max_change")):
                        self.table.item(r, c).setText(str(limit[key]))
            self.updating = False
            self.result = result; self.comparison = comparison; self.stale = True
            self.draw_result()
            self.status.setText("Archived suggestion (historical): recalculate before exporting K1.")
            if comparison: self.show_comparison(comparison, persist=False)
        except Exception as exc:
            self.updating = False; self.error(exc)

    def compare_file(self):
        if not self.result: return
        path, _ = QFileDialog.getOpenFileName(self, "Remeasurement JSON", "", "JSON (*.json)")
        if not path: return
        try:
            measurement = import_measurement(json.loads(Path(path).read_text()), line=self.line.currentData())
            self.populate(measurement)
            self.status.setText("Remeasurement loaded: supply actual executed K1, then Compare edited inputs.")
        except Exception as exc: self.error(exc)

    def compare_inputs(self):
        try:
            if not self.result: raise ValueError("Load or calculate a matching result first")
            measurement = self.measurement()
            tolerance = float(self.acceptance.text())
            self.run_task(lambda cancel: compare_measurement(self.model, self.result, measurement, tolerance), self.show_comparison)
        except Exception as exc: self.error(exc)

    def show_comparison(self, comparison, persist=True):
        self.comparison = comparison
        lines = ["Direct remeasurement" if comparison["kind"] == "measured" else "Remeasurement transported using executed snapshot"]
        for p, v in comparison["planes"].items():
            measured = v["measured"]
            lines.append(f"{p.upper()}: β={measured['beta']:.5g}, α={measured['alpha']:.5g}, "
                         f"ε={measured['emittance']*1e6:.5g} mm mrad; Bmag={v['bmag']:.5g}; "
                         f"improvement={v['improvement']:.5g}; within user tolerance={v['within_tolerance']}; "
                         f"prediction Δβ={v['prediction_error']['beta']:.4g}, Δα={v['prediction_error']['alpha']:.4g}")
        lines.append(comparison["note"])
        if comparison.get("uncertainty"):
            lines.append("Imported fit quality / uncertainty available in tooltip and archive.")
            self.comparison_label.setToolTip(json.dumps(comparison["uncertainty"], indent=2))
        else:
            self.comparison_label.setToolTip("")
        if comparison.get("executed_minus_suggested"):
            differences = comparison["executed_minus_suggested"]
            lines.append("Executed − suggested K1: " + ", ".join(f"{q} {v:+.3g}" for q, v in differences.items()))
        self.comparison_label.setText("\n".join(lines))
        if persist: self.archive()

    def stop(self):
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
            self.status.setText("Cancelling after the current model call…")

    def shutdown(self):
        self.stop()
        return not self.worker or not self.worker.isRunning() or self.worker.wait(100)
