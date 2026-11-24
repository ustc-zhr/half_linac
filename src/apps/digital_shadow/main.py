from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import signal
import sys
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from time import time

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file())
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from repo_bootstrap import ensure_repo_import_path
ensure_repo_import_path(__file__)

from PyQt5.QtCore import Qt, QTimer, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QPlainTextEdit,
    QPushButton, QScrollArea, QSplitter, QTableWidget, QTableWidgetItem,
    QTabWidget, QToolButton, QVBoxLayout, QWidget,
)
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from half_linac.src.apps.digital_shadow.forms import RequestForm
from half_linac.src.apps.digital_shadow.jobs import read_archive, read_object, ShadowJobController
from half_linac.src.shared.app_theme import resolve_initial_theme
from half_linac.src.shared.digital_shadow.example import write_example
from half_linac.src.shared.digital_shadow.impact_model import IMPACT_NOMINAL, IMPACT_VARIANT_LABELS
from half_linac.src.shared.digital_shadow.models import ShadowRequest, timestamp
from half_linac.src.shared.digital_shadow.particle_capture import CAPTURE_COMMAND, CAPTURE_SOURCES, EVIDENCE_FILE, FOLLOW_COMMAND
from half_linac.src.shared.digital_shadow.particle_model import (
    DEFAULT_COUPLED_ELEGANT_TIMEOUT_S, DEFAULT_IMPACT_TIMEOUT_S, PARTICLE_REQUEST_SCHEMA, PARTICLE_RUN_KIND,
    SOURCE_LABELS, ParticleModelRequest,
)
from half_linac.src.shared.digital_shadow.pipeline import prepare_model
from half_linac.src.shared.digital_shadow.vm_capture import build_vm_capture_plan
from half_linac.src.shared.digital_shadow.real_capture import build_real_capture_plan, verify_real_evidence
from half_linac.src.shared.digital_shadow.real_demo import demo_request
from half_linac.src.shared.digital_shadow.image_store import store_usage
from half_linac.src.shared.machine_profile.models import ControlBackendConfig
from half_linac.src.shared.machine_profile.loader import load_model_context
from half_linac.src.shared.machine_state import load_snapshot
from half_linac.src.shared.runtime_state import read_runtime_state, write_runtime_state
from half_linac.src.shared.window_activation import install_qt_window_raise_handler


IMPACT_STATISTICS = (
    ("Forward particles", "forward_particles", "particles"),
    ("Selected particles", "particles", "particles"),
    ("Selected charge", "charge_pc", "pC"),
    ("Reference momentum", "p_ref_mev_c", "MeV/c"),
    ("Mean kinetic energy", "mean_kinetic_mev", "MeV"),
    ("RMS energy spread", "rms_energy_kev", "keV"),
    ("Horizontal RMS", "sigma_x_mm", "mm"),
    ("Vertical RMS", "sigma_y_mm", "mm"),
    ("RMS duration", "sigma_t_ps", "ps"),
    ("Horizontal normalized emittance", "emit_nx_mm_mrad", "mm·mrad"),
    ("Vertical normalized emittance", "emit_ny_mm_mrad", "mm·mrad"),
)


class DigitalShadowWindow(QMainWindow):
    def __init__(self, *, output_root=None, parent=None):
        super().__init__(parent)
        self.context = load_model_context("half")  # Model-only; never inherits a live real connection.
        self.current_archive = None
        self._close_pending = False
        self._imported_archives = set()
        self._particle_evidence_selection = (None, None)
        self._follow_state = None
        self._displayed_follow_run = None
        self._dark = resolve_initial_theme() == "dark"
        self.setWindowTitle("HALF · Digital Shadow")
        self.resize(1360, 900)
        self.setMinimumSize(1020, 740)
        self.controller = ShadowJobController(ROOT, self)
        self.controller.message.connect(self._append_log)
        self.controller.state_changed.connect(self._job_state)
        self.controller.completed.connect(self._job_completed)
        self.controller.follow_update.connect(self._show_follow_update)
        self._follow_timer = QTimer(self)
        self._follow_timer.timeout.connect(self._update_follow_status)

        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        header = QHBoxLayout()
        title = QLabel("Digital Shadow", central)
        title.setObjectName("title")
        self.context_label = QLabel("HALF · ELEGANT · VM / Offline · READ ONLY", central)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.context_label)
        layout.addLayout(header)
        self.tabs = QTabWidget(central)
        layout.addWidget(self.tabs, 1)
        self._build_run_page(output_root or ROOT / "runs/digital_shadow_gui/half")
        self._build_history_page()
        footer = QHBoxLayout()
        self.status_label = QLabel("Ready · no PV reads until a live capture is explicitly started", central)
        self.status_label.setWordWrap(True)
        self.controller.progress.connect(self.status_label.setText)
        self.log_toggle = QToolButton(central)
        self.log_toggle.setText("Show log")
        self.log_toggle.setCheckable(True)
        footer.addWidget(self.status_label, 1)
        footer.addWidget(self.log_toggle)
        layout.addLayout(footer)
        self.log = QPlainTextEdit(central)
        self.log.setReadOnly(True)
        self.log.document().setMaximumBlockCount(1500)
        self.log.setMaximumHeight(160)
        self.log.hide()
        self.log_toggle.toggled.connect(self.log.setVisible)
        layout.addWidget(self.log)
        self._apply_theme()
        self._mode_changed()
        self.refresh_history()

    def _build_run_page(self, output_root):
        page = QWidget(self)
        page_layout = QVBoxLayout(page)
        toolbar = QHBoxLayout()
        self.mode = QComboBox(page)
        self.mode.addItem("Offline snapshot replay", "replay")
        self.mode.addItem("VM · capture once and predict", "run-vm")
        self.mode.addItem("Offline real capture demo · NO PV", "demo-real")
        self.mode.addItem("Real · capture once · READ ONLY", "run-real")
        self.mode.addItem("Offline particle model", "run-model")
        self.import_button = QPushButton("Import request…", page)
        self.export_button = QPushButton("Export request…", page)
        self.example_button = QPushButton("Load VM example", page)
        self.run_button = QPushButton("Run replay", page)
        self.stop_button = QPushButton("Stop", page)
        self.stop_button.setEnabled(False)
        for widget in (self.mode, self.import_button, self.export_button, self.example_button):
            toolbar.addWidget(widget)
        toolbar.addStretch()
        toolbar.addWidget(self.run_button)
        toolbar.addWidget(self.stop_button)
        page_layout.addLayout(toolbar)
        paths = QFormLayout()
        self.snapshot_edit = QLineEdit(page)
        self.evidence_edit = QLineEdit(page)
        self.runtime_edit = QLineEdit(page)
        self.runtime_edit.setPlaceholderText("Default: this checkout's VM runtime; browse for another VM instance")
        self.output_edit = QLineEdit(str(Path(output_root)), page)
        self.snapshot_row = self._path_row(self.snapshot_edit, lambda: self._choose_file(self.snapshot_edit))
        self.evidence_row = self._path_row(self.evidence_edit, lambda: self._choose_file(self.evidence_edit))
        self.runtime_row = self._path_row(self.runtime_edit, lambda: self._choose_file(self.runtime_edit))
        self.snapshot_label = QLabel("Snapshot JSON", page)
        self.evidence_label = QLabel("Capture evidence JSON", page)
        self.runtime_label = QLabel("VM runtime JSON", page)
        paths.addRow(self.snapshot_label, self.snapshot_row)
        paths.addRow(self.evidence_label, self.evidence_row)
        paths.addRow(self.runtime_label, self.runtime_row)
        paths.addRow("Output directory", self._path_row(self.output_edit, self._choose_output))
        image_row = QWidget(page)
        image_layout = QHBoxLayout(image_row)
        image_layout.setContentsMargins(0, 0, 0, 0)
        self.save_images = QCheckBox("Save one original frame per screen", page)
        self.save_images.setToolTip("Optional lossless archive; no continuous recording. Replay reuses existing image objects.")
        self.image_limit = QDoubleSpinBox(page)
        self.image_limit.setRange(.01, 10000)
        self.image_limit.setValue(10)
        self.image_limit.setSuffix(" GiB limit")
        self.image_usage = QLabel(page)
        image_layout.addWidget(self.save_images)
        image_layout.addWidget(self.image_limit)
        image_layout.addWidget(self.image_usage, 1)
        paths.addRow("Image archive", image_row)
        self.image_row = image_row
        self.image_label = paths.labelForField(image_row)
        self.background_edit = QLineEdit(page)
        self.background_edit.setPlaceholderText("Optional per-screen background path/provenance JSON; native camera orientation")
        self.background_row = self._path_row(self.background_edit, lambda: self._choose_file(self.background_edit))
        self.background_label = QLabel("Backgrounds JSON", page)
        paths.addRow(self.background_label, self.background_row)
        self.save_images.toggled.connect(self._image_options_changed)
        self.image_limit.valueChanged.connect(self._update_image_usage)
        self.output_edit.editingFinished.connect(self._update_image_usage)
        self.path_panel = QWidget(page)
        self.path_panel.setLayout(paths)
        page_layout.addWidget(self.path_panel)
        splitter = QSplitter(Qt.Horizontal, page)
        scroll = QScrollArea(splitter)
        scroll.setWidgetResizable(True)
        self.form = RequestForm(self.context)
        input_panel = QWidget(scroll)
        input_layout = QVBoxLayout(input_panel)
        input_layout.setContentsMargins(0, 0, 0, 0)
        input_layout.addWidget(self.form)
        self._build_particle_form(input_layout)
        scroll.setWidget(input_panel)
        result_panel = QWidget(splitter)
        result_layout = QVBoxLayout(result_panel)
        self.result_title = QLabel("No result loaded", result_panel)
        self.result_title.setWordWrap(True)
        result_layout.addWidget(self.result_title)
        self.result_banner = QLabel("Local uncoupled transverse optics · RMS diagnostics only. Completion is not a physics accuracy certificate.", result_panel)
        self.result_banner.setWordWrap(True)
        result_layout.addWidget(self.result_banner)
        self.comparison_source = QComboBox(result_panel)
        self.comparison_source.addItem("SIZE / scalar diagnostics", "scalar")
        self.comparison_source.addItem("Image ROI RMS", "image")
        self.comparison_source.setToolTip("Image RMS comparisons retain their own source, ROI and background. SIZE values keep their original definitions.")
        result_layout.addWidget(self.comparison_source)
        self.comparison_hint = QLabel(result_panel)
        self.comparison_hint.setWordWrap(True)
        self.comparison_hint.hide()
        result_layout.addWidget(self.comparison_hint)
        self.results = QTableWidget(0, 7, result_panel)
        self.results.setHorizontalHeaderLabels(["Diagnostic", "Predicted RMS", "Observed width", "Residual", "Unit", "Status", "Reason"])
        self.results.setEditTriggers(QTableWidget.NoEditTriggers)
        self.results.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.results.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        self.results.verticalHeader().hide()
        self.results.setMinimumHeight(160)
        result_layout.addWidget(self.results, 1)
        self.figure = Figure(figsize=(6, 3), tight_layout=True)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setMinimumHeight(190)
        result_layout.addWidget(self.canvas, 1)
        self.details_toggle = QToolButton(result_panel)
        self.details_toggle.setText("Snapshot, request and result details")
        self.details_toggle.setCheckable(True)
        self.details = QPlainTextEdit(result_panel)
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(190)
        self.details.hide()
        self.details_toggle.toggled.connect(self.details.setVisible)
        result_layout.addWidget(self.details_toggle)
        result_layout.addWidget(self.details)
        self.images_toggle = QToolButton(result_panel)
        self.images_toggle.setText("Saved images · ROI RMS [mm]")
        self.images_toggle.setCheckable(True)
        self.images_toggle.hide()
        self.image_results = QTableWidget(0, 6, result_panel)
        self.image_results.setHorizontalHeaderLabels(["Screen", "Original dtype", "Stored KiB", "RMS X", "RMS Y", "Status / reason"])
        self.image_results.setEditTriggers(QTableWidget.NoEditTriggers)
        self.image_results.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.image_results.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.image_results.verticalHeader().hide()
        self.image_results.setMaximumHeight(155)
        self.image_results.hide()
        self.images_toggle.toggled.connect(self.image_results.setVisible)
        result_layout.addWidget(self.images_toggle)
        result_layout.addWidget(self.image_results)
        self.comparison_source.currentIndexChanged.connect(self._show_comparison_rows)
        splitter.setSizes([500, 800])
        page_layout.addWidget(splitter, 1)
        self.tabs.addTab(page, "Run & Compare")
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.import_button.clicked.connect(self._import_request_dialog)
        self.export_button.clicked.connect(self._export_request_dialog)
        self.example_button.clicked.connect(self.load_example)
        self.run_button.clicked.connect(self.run_task)
        self.stop_button.clicked.connect(self.controller.stop)

    def _build_particle_form(self, parent_layout):
        self.particle_form = QWidget(self)
        form = QFormLayout(self.particle_form)
        self.particle_scope = QComboBox(self.particle_form)
        for label, value in (("Downstream only", "downstream"), ("Pre-injector only", "preinjector"),
                             ("Coupled", "coupled")):
            self.particle_scope.addItem(label, value)
        self.particle_source = QComboBox(self.particle_form)
        for value, label in SOURCE_LABELS.items():
            self.particle_source.addItem(label, value)
        self.particle_parameter_source = QComboBox(self.particle_form)
        self.particle_parameter_source.addItem("Design", "design")
        self.particle_parameter_source.addItem("Saved quadrupole snapshot", "snapshot")
        self.particle_path = QLineEdit(self.particle_form)
        self.particle_path_row = self._path_row(self.particle_path, self._choose_particle_file)
        self.particle_snapshot = QLineEdit(self.particle_form)
        self.particle_snapshot_row = self._path_row(self.particle_snapshot, lambda: self._choose_file(self.particle_snapshot))
        self.particle_capture_controls = QWidget(self.particle_form)
        actions = QHBoxLayout(self.particle_capture_controls)
        actions.setContentsMargins(0, 0, 0, 0)
        self.particle_capture_source = QComboBox(self.particle_capture_controls)
        for value, label in CAPTURE_SOURCES.items():
            self.particle_capture_source.addItem(label, value)
        self.capture_particle_button = QPushButton("Capture snapshot", self.particle_capture_controls)
        self.follow_particle_button = QPushButton("Follow", self.particle_capture_controls)
        for widget in (self.particle_capture_source, self.capture_particle_button, self.follow_particle_button):
            actions.addWidget(widget)
        actions.addStretch()
        self.capture_particle_button.clicked.connect(lambda: self._start_particle_capture(CAPTURE_COMMAND))
        self.follow_particle_button.clicked.connect(lambda: self._start_particle_capture(FOLLOW_COMMAND))
        self.particle_handoff = QLineEdit(self.particle_form)
        self.particle_handoff.setPlaceholderText("Optional")
        self.particle_handoff_row = self._path_row(self.particle_handoff, lambda: self._choose_file(self.particle_handoff))
        self.impact_benchmark = QLineEdit(os.environ.get("HALF_IMPACT_BENCHMARK", ""), self.particle_form)
        self.impact_library = QLineEdit(os.environ.get("HALF_IMPACT_LIB_DIR", ""), self.particle_form)
        self.impact_variant = QComboBox(self.particle_form)
        for value, label in IMPACT_VARIANT_LABELS.items():
            self.impact_variant.addItem(label, value)
        self.impact_benchmark_row = self._path_row(self.impact_benchmark, lambda: self._choose_directory(self.impact_benchmark))
        self.impact_library_row = self._path_row(self.impact_library, lambda: self._choose_directory(self.impact_library))
        self.particle_timeout_s = ParticleModelRequest().model_timeout_s
        self.impact_timeout_s = DEFAULT_IMPACT_TIMEOUT_S
        self.coupled_impact_timeout_s = DEFAULT_IMPACT_TIMEOUT_S
        self.coupled_elegant_timeout_s = DEFAULT_COUPLED_ELEGANT_TIMEOUT_S
        self.particle_hint = QLabel(self.particle_form)
        self.particle_hint.setWordWrap(True)
        form.addRow("Scope", self.particle_scope)
        form.addRow("Beam source", self.particle_source)
        form.addRow("Beam file", self.particle_path_row)
        form.addRow("Model parameters", self.particle_parameter_source)
        form.addRow("Snapshot JSON", self.particle_snapshot_row)
        form.addRow("Capture source", self.particle_capture_controls)
        form.addRow("IMPACT-T handoff JSON", self.particle_handoff_row)
        form.addRow("Upstream case", self.impact_variant)
        form.addRow("P0 benchmark directory", self.impact_benchmark_row)
        form.addRow("IMPACT-T library directory", self.impact_library_row)
        form.addRow(self.particle_hint)
        for field in (self.particle_scope, self.particle_source, self.particle_parameter_source, self.particle_capture_source):
            field.currentIndexChanged.connect(self._particle_options_changed)
        parent_layout.addWidget(self.particle_form)
        parent_layout.addStretch()

    def _choose_particle_file(self):
        file_filter = ("Saved beam JSON (*.json)" if self.particle_source.currentData() == "archived"
                       else "SDDS particle files (*.sdds *.bun *.dat);;All files (*)")
        filename, _ = QFileDialog.getOpenFileName(self, "Select particle input", self.particle_path.text(), file_filter)
        if filename:
            self.particle_path.setText(filename)

    def _particle_options_changed(self, *args):
        if self.mode.currentData() != "run-model":
            return
        scope = self.particle_scope.currentData()
        active = scope == "downstream"
        preinjector = scope == "preinjector"
        coupled = scope == "coupled"
        form = self.particle_form.layout()
        for field in (self.particle_source, self.particle_parameter_source, self.particle_path_row):
            visible = active and (field is not self.particle_path_row or self.particle_source.currentData() != "generated")
            field.setVisible(visible)
            form.labelForField(field).setVisible(visible)
        snapshot = coupled or (active and self.particle_parameter_source.currentData() == "snapshot")
        snapshot_label = form.labelForField(self.particle_snapshot_row)
        snapshot_label.setText("VM snapshot JSON" if coupled else "Snapshot JSON")
        snapshot_label.setVisible(snapshot)
        self.particle_snapshot_row.setVisible(snapshot)
        handoff = active and snapshot and self.particle_source.currentData() == "archived"
        form.labelForField(self.particle_handoff_row).setVisible(handoff)
        self.particle_handoff_row.setVisible(handoff)
        live_controls = active and snapshot
        form.labelForField(self.particle_capture_controls).setVisible(live_controls)
        self.particle_capture_controls.setVisible(live_controls)
        self.follow_particle_button.setEnabled(handoff)
        show_runtime = live_controls and self.particle_capture_source.currentData() == "vm"
        self.runtime_label.setVisible(show_runtime)
        self.runtime_row.setVisible(show_runtime)
        for row in (self.impact_variant, self.impact_benchmark_row, self.impact_library_row):
            form.labelForField(row).setVisible(preinjector or coupled)
            row.setVisible(preinjector or coupled)
        hint = "ALL_MAIN · C#1 entrance · ELEGANT tracking"
        if snapshot:
            hint += "\nUses all required snapshot quadrupoles. Other settings come from the design model."
        if self.particle_source.currentData() == "generated":
            hint += "\nUses the tracking template's bunch, reference momentum and seed."
        elif self.particle_source.currentData() == "archived":
            hint += "\nRequires a particle archive for this entrance. RF settings come from the design model."
        if preinjector:
            hint = "Native screen at z = 9.2994809 m\nAllow about 1–1.5 hours for tracking."
        if coupled:
            hint = "ALL_MAIN · C#1 entrance\n0 mm nominal handoff · Physical datum pending verification"
        engine = {"preinjector": "IMPACT-T", "coupled": "IMPACT-T → ELEGANT", "downstream": "ELEGANT"}[scope]
        self.context_label.setText(f"HALF · {engine} · Offline particle model · READ ONLY")
        self.particle_hint.setText(hint)
        self.run_button.setText({"preinjector": "Run IMPACT-T", "coupled": "Run coupled model",
                                 "downstream": "Run particle model"}[scope])
        self.run_button.setEnabled(not self.controller.busy)

    def _current_request(self):
        if self.mode.currentData() != "run-model":
            return self.form.request()
        scope = self.particle_scope.currentData()
        if scope in ("preinjector", "coupled"):
            values = {
                "schema": PARTICLE_REQUEST_SCHEMA, "scope": scope,
                "impact_benchmark_dir": self.impact_benchmark.text(),
                "impact_library_dir": self.impact_library.text(),
                "impact_variant": self.impact_variant.currentData(),
                "model_timeout_s": self.impact_timeout_s,
            }
            if scope == "coupled":
                values.update(parameter_source="snapshot", snapshot_path=self.particle_snapshot.text(),
                              model_timeout_s=self.coupled_impact_timeout_s, elegant_timeout_s=self.coupled_elegant_timeout_s)
            return ParticleModelRequest.from_dict(values, base=Path.cwd())
        source = self.particle_source.currentData()
        parameters = self.particle_parameter_source.currentData()
        handoff_path = None
        if source == "archived" and parameters == "snapshot":
            handoff_path = self.particle_handoff.text().strip() or None
        snapshot_path = self.particle_snapshot.text().strip() if parameters == "snapshot" else None
        evidence_path = None
        if snapshot_path:
            snapshot_file = Path(snapshot_path).expanduser().resolve()
            selected, evidence = self._particle_evidence_selection
            sidecar = snapshot_file.parent / EVIDENCE_FILE
            evidence_path = evidence if str(snapshot_file) == selected else str(sidecar) if sidecar.is_file() else None
        return ParticleModelRequest.from_dict({
            "schema": PARTICLE_REQUEST_SCHEMA, "scope": scope,
            "beam_source": source, "beam_path": self.particle_path.text() if source != "generated" else None,
            "parameter_source": parameters, "snapshot_path": snapshot_path,
            "handoff_path": handoff_path, "capture_evidence_path": evidence_path,
            "model_timeout_s": self.particle_timeout_s,
        }, base=Path.cwd())

    def _build_history_page(self):
        page = QWidget(self)
        layout = QVBoxLayout(page)
        toolbar = QHBoxLayout()
        for label, action in (("Refresh", self.refresh_history), ("Open archive…", self._open_archive_dialog),
                              ("Open snapshot…", self._open_snapshot_dialog), ("Open directory", self.open_directory)):
            button = QPushButton(label, page)
            button.clicked.connect(action)
            toolbar.addWidget(button)
        toolbar.addStretch()
        self.use_inputs_button = QPushButton("Use archived inputs for replay", page)
        self.use_inputs_button.setEnabled(False)
        self.use_inputs_button.clicked.connect(self._use_archive_inputs)
        toolbar.addWidget(self.use_inputs_button)
        layout.addLayout(toolbar)
        splitter = QSplitter(page)
        self.history = QListWidget(splitter)
        self.history_details = QPlainTextEdit(splitter)
        self.history_details.setReadOnly(True)
        self.history.currentItemChanged.connect(self._history_selected)
        splitter.setSizes([380, 820])
        layout.addWidget(splitter)
        self.tabs.addTab(page, "History")

    def _path_row(self, edit, action):
        widget = QWidget(self)
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        button = QToolButton(widget)
        button.setText("…")
        button.clicked.connect(action)
        layout.addWidget(edit, 1)
        layout.addWidget(button)
        return widget

    def _apply_theme(self):
        background, panel, text, muted, border, accent = (
            ("#0f1519", "#172027", "#e6edf2", "#91a2ad", "#31424d", "#45d0bc") if self._dark else
            ("#f4f1eb", "#fffdf9", "#293740", "#746f68", "#d6cec3", "#287866"))
        self.setStyleSheet(f"""
            QWidget {{ background:{background}; color:{text}; font-size:12px; }}
            QLabel#title {{ font-size:23px; font-weight:600; }}
            QLineEdit,QComboBox,QPlainTextEdit,QTableWidget,QListWidget {{ background:{panel}; border:1px solid {border}; padding:3px; }}
            QGroupBox {{ border:1px solid {border}; border-radius:6px; margin-top:9px; padding-top:10px; }}
            QGroupBox::title {{ subcontrol-origin:margin; left:8px; }}
            QPushButton,QToolButton {{ background:{panel}; border:1px solid {border}; border-radius:5px; padding:6px 9px; }}
            QPushButton:hover,QToolButton:hover {{ border-color:{accent}; }}
            QPushButton:disabled,QToolButton:disabled {{ color:{muted}; }}
            QHeaderView::section {{ background:{panel}; padding:5px; border:1px solid {border}; }}
            QTabBar::tab {{ padding:7px 12px; }}
        """)
        self._plot([])

    def _mode_changed(self, *args):
        mode = self.mode.currentData()
        particle = mode == "run-model"
        self.form.setVisible(not particle)
        self.particle_form.setVisible(particle)
        self.form.set_diagnostic_backend("real" if mode in {"demo-real", "run-real"} else "vm")
        for widget in (self.snapshot_label, self.snapshot_row, self.evidence_label, self.evidence_row):
            widget.setVisible(mode == "replay")
        self.runtime_label.setVisible(mode == "run-vm")
        self.runtime_row.setVisible(mode == "run-vm")
        self.run_button.setText({"run-vm": "Capture VM & predict", "run-real": "Read real PVs & predict",
                                 "demo-real": "Run offline real demo", "replay": "Run replay",
                                 "run-model": "Run particle model"}[mode])
        self.run_button.setEnabled(not self.controller.busy)
        self.example_button.setVisible(not particle)
        self.example_button.setText("Load real demo" if mode in {"demo-real", "run-real"} else "Load VM example")
        label = {"run-vm": "VM capture", "run-real": "REAL PV READS · explicit action", "demo-real": "SYNTHETIC · NO PV",
                 "replay": "Offline replay", "run-model": "Offline particle model"}[mode]
        self.context_label.setText("HALF · ELEGANT · " + label + " · READ ONLY")
        self._image_options_changed()
        self._particle_options_changed()

    def _image_options_changed(self, *args):
        mode = self.mode.currentData()
        capture = mode not in ("replay", "run-model")
        self.image_label.setVisible(mode != "run-model")
        self.image_row.setVisible(mode != "run-model")
        self.save_images.setEnabled(capture)
        self.image_limit.setEnabled(capture and self.save_images.isChecked())
        show_background = capture and self.save_images.isChecked()
        self.background_label.setVisible(show_background)
        self.background_row.setVisible(show_background)
        self._update_image_usage()

    def _update_image_usage(self, *args):
        try:
            usage = store_usage(Path(self.output_edit.text()).expanduser() / "image_store")
            used, limit = usage["used_bytes"] / 1024**3, self.image_limit.value()
            warning = " · near/full limit" if used >= limit * .8 else ""
            self.image_usage.setText(f"Images {used*1024:.1f} MiB / {limit:g} GiB · disk free {usage['free_bytes']/1024**3:.1f} GiB{warning}")
        except OSError as error:
            self.image_usage.setText(f"Storage unavailable: {error}")

    def _choose_file(self, edit):
        path, _ = QFileDialog.getOpenFileName(self, "Select JSON", edit.text(), "JSON files (*.json)")
        if path:
            edit.setText(path)

    def _choose_directory(self, edit):
        path = QFileDialog.getExistingDirectory(self, "Select directory", edit.text())
        if path:
            edit.setText(path)

    def _choose_output(self):
        path = QFileDialog.getExistingDirectory(self, "Output directory", self.output_edit.text())
        if path:
            self.output_edit.setText(path)
            self._update_image_usage()
            self.refresh_history()

    def _error(self, error):
        self.status_label.setText(f"Error: {error}")
        self._append_log(f"Error: {error}\n")

    def _append_log(self, text):
        self.log.moveCursor(self.log.textCursor().End)
        self.log.insertPlainText(text)
        self.log.moveCursor(self.log.textCursor().End)

    def load_request_file(self, path):
        data = read_object(path)
        if data.get("schema") == PARTICLE_REQUEST_SCHEMA:
            request = ParticleModelRequest.from_dict(data, base=Path(path).resolve().parent)
            self.mode.setCurrentIndex(self.mode.findData("run-model"))
            for widget, value in ((self.particle_scope, request.scope), (self.particle_source, request.beam_source),
                                  (self.particle_parameter_source, request.parameter_source)):
                widget.setCurrentIndex(widget.findData(value))
            self.particle_path.setText(request.beam_path or "")
            self.particle_snapshot.setText(request.snapshot_path or "")
            self.particle_handoff.setText(request.handoff_path or "")
            self._particle_evidence_selection = (request.snapshot_path, request.capture_evidence_path)
            if request.scope in ("preinjector", "coupled"):
                self.impact_variant.setCurrentIndex(self.impact_variant.findData(request.impact_variant))
                self.impact_benchmark.setText(request.impact_benchmark_dir)
                self.impact_library.setText(request.impact_library_dir)
                if request.scope == "coupled":
                    self.coupled_impact_timeout_s = request.model_timeout_s
                    self.coupled_elegant_timeout_s = request.elegant_timeout_s
                else:
                    self.impact_timeout_s = request.model_timeout_s
            else:
                self.particle_timeout_s = request.model_timeout_s
            self.status_label.setText("Offline particle request loaded")
            return
        request = ShadowRequest.from_dict(data)
        if self.mode.currentData() == "run-model":
            self.mode.setCurrentIndex(self.mode.findData("replay"))
        self.form.load_request(request)
        self.status_label.setText(f"Request loaded · {request.beam_basis} · {request.beam_provenance}")

    def _import_request_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import shadow request", "", "JSON files (*.json)")
        if path:
            try:
                self.load_request_file(path)
            except (ValueError, KeyError, TypeError, OSError) as error:
                self._error(error)

    def _export_request_dialog(self):
        try:
            request = self._current_request()
            path, _ = QFileDialog.getSaveFileName(self, "Export shadow request", "request.json", "JSON files (*.json)")
            if path:
                write_runtime_state(path, request.as_metadata())
                self.status_label.setText("Request exported")
        except (ValueError, KeyError, TypeError, OSError) as error:
            self._error(error)

    def load_example(self):
        try:
            if self.mode.currentData() in {"demo-real", "run-real"}:
                self.form.load_request(demo_request())
                self.mode.setCurrentIndex(self.mode.findData("demo-real"))
                self.snapshot_edit.clear()
                self.evidence_edit.clear()
                self.status_label.setText("SYNTHETIC real-capture demo loaded · fabricated beam and observations · NO PV")
                return
            directory = Path(self.output_edit.text()).expanduser().resolve() / "examples" / f"example_{uuid.uuid4().hex}"
            write_example(directory, vm=True)
            self.load_request_file(directory / "request.json")
            self.snapshot_edit.setText(str(directory / "machine_snapshot.json"))
            self.evidence_edit.clear()
            self.mode.setCurrentIndex(0)
            self.status_label.setText("SYNTHETIC VM example loaded · assumed beam · no PV access")
            self.result_banner.setText("SYNTHETIC inputs and assumed entrance beam. Example residuals do not measure real-machine accuracy.")
        except (ValueError, KeyError, TypeError, OSError) as error:
            self._error(error)

    def run_task(self):
        if self.controller.busy:
            return
        try:
            mode = self.mode.currentData()
            request = self._current_request()
            if mode != "run-model":
                prepare_model(self.context, request)
            if mode == "run-vm":
                build_vm_capture_plan(self.context, request)
            elif mode in {"demo-real", "run-real"}:
                real_context = replace(self.context, control_backend=ControlBackendConfig("real"))
                build_real_capture_plan(real_context, request)
                if mode == "run-real" and "SYNTHETIC" in request.beam_provenance.upper():
                    raise ValueError("Replace the synthetic demo beam/source before live real capture")
            elif mode == "replay":
                snapshot = load_snapshot(self.snapshot_edit.text())
                if snapshot.machine_id != "half" or snapshot.backend not in {"vm", "real"} or (snapshot.backend == "real" and not self.evidence_edit.text().strip()):
                    raise ValueError("Real or non-HALF snapshots are view-only; no model replay is started")
                if snapshot.backend == "real":
                    verify_real_evidence(replace(self.context, control_backend=ControlBackendConfig("real")), snapshot,
                                         request, read_object(self.evidence_edit.text()))
            if not self.output_edit.text().strip():
                raise ValueError("Select an output directory")
            self.log.clear()
            self.results.setRowCount(0)
            self.images_toggle.setChecked(False)
            self.images_toggle.hide()
            self.image_results.hide()
            self.image_results.setRowCount(0)
            self._plot([])
            self.details.clear()
            self.current_archive = None
            self.comparison_hint.hide()
            self.result_title.setText("Running · result pending")
            if mode == "run-model":
                engine = {"preinjector": "IMPACT-T", "coupled": "IMPACT-T → ELEGANT"}.get(request.scope)
                banner = (f"{engine} · {IMPACT_VARIANT_LABELS[request.impact_variant]}" if engine
                          else SOURCE_LABELS[request.beam_source])
            else:
                banner = f"{request.beam_basis} entrance beam · {request.beam_provenance}\nCompletion describes calculation and data alignment, not physical accuracy."
            self.result_banner.setText(banner)
            self.controller.start(request, Path(self.output_edit.text()).expanduser(), mode=mode,
                snapshot_path=self.snapshot_edit.text(), evidence_path=self.evidence_edit.text().strip() or None,
                runtime_json=self.runtime_edit.text().strip() or None,
                save_images=mode not in ("replay", "run-model") and self.save_images.isChecked(), image_limit_gib=self.image_limit.value(),
                image_backgrounds=self.background_edit.text().strip() or None)
        except (ValueError, KeyError, TypeError, OSError) as error:
            self._error(error)

    def _start_particle_capture(self, mode):
        if self.controller.busy:
            return
        try:
            output = self.output_edit.text().strip()
            if not output:
                raise ValueError("Select an output directory")
            self.log.clear()
            self._follow_state = self._displayed_follow_run = None
            self.controller.start(None, Path(output).expanduser(), mode=mode,
                capture_source=self.particle_capture_source.currentData(), runtime_json=self.runtime_edit.text().strip(),
                beam_path=self.particle_path.text().strip(), handoff_path=self.particle_handoff.text().strip(),
                model_timeout=self.particle_timeout_s)
            if mode == FOLLOW_COMMAND:
                self.current_archive = None
                self.results.setRowCount(0)
                self._plot([])
                self.details.clear()
                self.images_toggle.hide()
                self.image_results.hide()
                self.result_title.setText("Follow · waiting for first result")
                self.result_banner.setText("Saved six-dimensional particles · " + Path(self.particle_path.text()).name)
                self._follow_timer.start(1000)
        except (ValueError, KeyError, TypeError, OSError) as error:
            self._error(error)

    def _show_follow_update(self, event):
        self._follow_state = event
        result = event.get("result")
        if result and result["run_dir"] != self._displayed_follow_run:
            try:
                self.show_archive(result["run_dir"])
                self._displayed_follow_run = result["run_dir"]
                displayed = datetime.now(timezone.utc)
                write_runtime_state(Path(result["run_dir"]).parent / "presentation.json", {
                    "snapshot_id": result["snapshot_id"], "displayed_at": displayed.isoformat(),
                    "capture_to_display_s": max(0.0, displayed.timestamp() - timestamp(result["capture_finished_at"])),
                })
            except (ValueError, KeyError, TypeError, OSError) as error:
                self._append_log(f"Cannot display Follow result: {error}\n")
        self._update_follow_status()

    def _update_follow_status(self):
        event = self._follow_state
        if not event or not self.controller.busy:
            return
        parts = [CAPTURE_SOURCES[event["source"]], f"Follow {event['state']}"]
        if result := event.get("result"):
            age = max(0.0, time() - timestamp(result["capture_finished_at"]))
            freshness = "previous result" if event.get("result_stale") else "result"
            parts.append(f"{freshness} age {age:.1f} s · calculation {result['calculation_s']:.2f} s")
            if result.get("vm_calculation") is not None:
                parts.append(f"VM calculation {result['vm_calculation']}")
        if event.get("pending_snapshot_id") or (result and event.get("result_stale") and event.get("active_snapshot_id")):
            parts.append("newer working point in progress")
        if event.get("error"):
            parts.append(event["error"])
        self.status_label.setText(" · ".join(parts))

    def _use_particle_snapshot(self, archive):
        self.mode.setCurrentIndex(self.mode.findData("run-model"))
        self.particle_scope.setCurrentIndex(self.particle_scope.findData("downstream"))
        self.particle_parameter_source.setCurrentIndex(self.particle_parameter_source.findData("snapshot"))
        self._particle_evidence_selection = (archive["snapshot_path"], archive["particle_evidence_path"])
        self.particle_snapshot.setText(archive["snapshot_path"])
        self.status_label.setText("Full-line snapshot loaded")

    def _job_state(self, state):
        busy = self.controller.busy
        self.comparison_source.setEnabled(not busy)
        for widget in (self.form, self.particle_form, self.path_panel, self.mode, self.import_button,
                       self.export_button, self.example_button, self.run_button):
            widget.setEnabled(not busy)
        self._particle_options_changed()
        self.stop_button.setEnabled(busy and state != "Stopping")
        if busy:
            self.use_inputs_button.setEnabled(False)
        self.status_label.setText(state)

    def _job_completed(self, record):
        self._follow_timer.stop()
        self._job_state(record["status"].title())
        self.refresh_history()
        self._update_image_usage()
        try:
            archive = self.show_archive(record["job_dir"])
            if record["mode"] == CAPTURE_COMMAND and record["status"] == "complete":
                self._use_particle_snapshot(archive)
        except (ValueError, KeyError, TypeError, OSError) as error:
            self._error(error)
        if record.get("error") and record["status"] == "failed":
            error = record["error"].strip()
            if record["mode"] == CAPTURE_COMMAND:
                error += " · Check the working point and capture again."
            self._error(error)
        if self._close_pending:
            self.close()

    @staticmethod
    def _format(value):
        return format(value, ".8g") if isinstance(value, (int, float)) and math.isfinite(value) else ""

    def show_archive(self, directory):
        archive = read_archive(directory)
        self.current_archive = archive
        result, snapshot = archive.get("result") or {}, archive.get("snapshot") or {}
        manifest = archive.get("manifest") or {}
        backend = archive["backend"]
        source_label = "REAL SIMULATION" if _is_simulated_particle_capture(archive) else backend.upper()
        self.result_title.setText(f"{source_label} · {archive['status']} · {Path(directory).name} · archived result")
        if manifest.get("kind") == PARTICLE_RUN_KIND or (archive.get("job") or {}).get("mode") == "run-model":
            request = archive.get("request") or {}
            source = SOURCE_LABELS.get(request.get("beam_source"), "Particle input")
            if manifest.get("beam_file"):
                source += f" ({manifest['beam_file']})"
            parameters = ("Saved quadrupoles and design settings" if request.get("parameter_source") == "snapshot"
                          else "Design settings")
            lines = [manifest.get("error", {}).get("message") or f"{source} · {parameters} · Predictions only"]
            handoff = manifest.get("impact_handoff")
            variant = manifest.get("impact_variant", request.get("impact_variant", IMPACT_NOMINAL))
            case_label = IMPACT_VARIANT_LABELS.get(variant, variant)
            if _is_preinjector(archive) and not manifest.get("error"):
                lines = [f"IMPACT-T · {case_label}"]
                if handoff:
                    lines.append(f"Native screen {handoff['physical_screen_id']} · z = {handoff['native_z_m']} m")
            if manifest.get("scope") == "coupled":
                if not manifest.get("error"):
                    lines = [f"IMPACT-T → ELEGANT · {case_label}"]
                for name, label in (("upstream", "IMPACT-T"), ("downstream", "ELEGANT")):
                    status = manifest.get("stages", {}).get(name, {}).get("status", "pending")
                    if status != "complete":
                        lines.append(f"{label} · {status}")
                if (count := (manifest.get("beam_statistics") or {}).get("particles")) is not None:
                    lines.append(f"Handoff particles {count:,}")
            if (count := result.get("end_particles")) is not None:
                lines.append(f"End particles {count:,}")
            if manifest.get("capture_source") == "demo-real":
                lines.insert(0, "SYNTHETIC · Real readbacks · NO PV")
            if manifest.get("capture_source") and (beam_hash := (manifest.get("particle_input") or {}).get("source_npz_sha256")):
                lines.append(f"Entrance beam SHA-256 {beam_hash[:12]}…")
            if isinstance(handoff, dict) and handoff.get("extra_drift_m") == 0:
                lines.append("IMPACT-T · 0 mm nominal handoff · Physical datum pending verification")
            snapshot_id = manifest.get("snapshot_id") or snapshot.get("snapshot_id")
            if snapshot_id:
                label = "VM snapshot ID" if snapshot.get("machine", {}).get("control_backend") == "vm" else "Snapshot ID"
                lines.append(f"{label} {snapshot_id}")
            self.result_banner.setText("\n".join(lines))
            self.result_banner.setToolTip("")
            self.images_toggle.hide()
            self.image_results.hide()
            self._show_comparison_rows()
            self.details.setPlainText(json.dumps(archive, indent=2, ensure_ascii=False))
            self.status_label.setText("Archive loaded · " + archive["status"])
            return archive
        if archive.get("particle_evidence_path") and not result and not manifest:
            source = read_object(archive["particle_evidence_path"])["source"]
            self.result_banner.setText(f"{CAPTURE_SOURCES[source]} · full-line snapshot · {snapshot['snapshot_id']}")
            self.details.setPlainText(json.dumps(archive, indent=2, ensure_ascii=False))
            self._show_comparison_rows()
            return archive
        beam = result.get("beam_basis", (archive.get("request") or {}).get("beam", {}).get("basis", "unknown"))
        provenance = result.get("beam_provenance", (archive.get("request") or {}).get("beam", {}).get("provenance", ""))
        synthetic = snapshot.get("snapshot_id", "").startswith("synthetic_")
        real_hint = "Real archive: view only. Gaussian SIZE values are not certified RMS."
        if archive.get("real_evidence_path"):
            real_hint = ("Prediction complete. " if result.get("prediction_status") == "complete" else "Prediction unavailable. ") + "Gaussian/unknown widths have no RMS accuracy residual."
        self.result_banner.setText(("SYNTHETIC · " if synthetic else "") + f"{beam} entrance beam · {provenance}\n" +
                                  (real_hint if backend == "real" else "Completion is not a physics accuracy certificate."))
        images = archive.get("image_index")
        self.images_toggle.setVisible(bool(images))
        self.image_results.setVisible(bool(images) and self.images_toggle.isChecked())
        self.image_results.setRowCount(len(images["frames"]) if images else 0)
        if images:
            summary = images["summary"]
            hint = f"Images saved {summary['saved']}/{summary['requested']} · {summary['status']} · ROI RMS kept separately from SIZE."
            self.result_banner.setText(self.result_banner.text() + "\n" + hint)
            self.result_banner.setToolTip("\n".join(summary["warnings"]))
            if "image_comparisons" in result:
                count = sum(row["status"] == "compared" for row in result["image_comparisons"])
                self.result_banner.setText(self.result_banner.text() + f" Image comparison {count}/{len(result['image_comparisons'])} usable.")
            for index, frame in enumerate(images["frames"]):
                obj, rms = frame.get("object", {}), frame.get("derived_rms", {})
                values = [frame["screen"], obj.get("dtype", ""), self._format(obj.get("stored_bytes", 0)/1024),
                          self._format(rms.get("sigx_mm")), self._format(rms.get("sigy_mm")),
                          frame.get("detail") or rms.get("status", frame["quality"])]
                for column, value in enumerate(values):
                    item = QTableWidgetItem(str(value))
                    item.setToolTip(json.dumps(frame, indent=2))
                    self.image_results.setItem(index, column, item)
        else:
            self.result_banner.setToolTip("")
        self.comparison_source.setEnabled(not self.controller.busy)
        self._show_comparison_rows()
        self.details.setPlainText(json.dumps(archive, indent=2, ensure_ascii=False))
        self.status_label.setText("Archive loaded · " + archive["status"])
        return archive

    def _show_comparison_rows(self, *args):
        archive = self.current_archive or {}
        result = archive.get("result") or {}
        preinjector = _is_preinjector(archive)
        particle = ((archive.get("manifest") or {}).get("kind") == PARTICLE_RUN_KIND
                    or (archive.get("job") or {}).get("mode") == "run-model")
        self.canvas.setVisible(not preinjector)
        self.details_toggle.setText("Request and result details" if preinjector else "Snapshot, request and result details")
        self.comparison_source.setVisible(not particle)
        for column in (2, 3):
            self.results.setColumnHidden(column, particle)
        for column in (5, 6):
            self.results.setColumnHidden(column, preinjector)
        self.results.horizontalHeaderItem(0).setText("Quantity" if preinjector else "Diagnostic")
        self.results.horizontalHeaderItem(1).setText("Value" if preinjector else "Predicted RMS")
        if preinjector:
            self.comparison_hint.hide()
            stats = result.get("beam_statistics", {}) if archive["status"] == "complete" else {}
            self.results.setRowCount(len(IMPACT_STATISTICS) if stats else 0)
            for row, (label, key, unit) in enumerate(IMPACT_STATISTICS if stats else ()):
                for column, value in ((0, label), (1, self._format(stats.get(key))), (4, unit)):
                    self.results.setItem(row, column, QTableWidgetItem(value))
            return
        field = "predictions" if particle else (
            "image_comparisons" if self.comparison_source.currentData() == "image" else "comparisons"
        )
        hint = ""
        if field == "image_comparisons":
            if not result:
                hint = "No completed result loaded. Open an archive in History or run a capture with saved images."
            elif "image_comparisons" not in result:
                if archive.get("image_index"):
                    hint = "This archive has saved images but no image comparisons. In History, use archived inputs for replay, then Run replay."
                else:
                    hint = "This result has no saved images. Capture with 'Save one original frame per screen', or open an image archive in History. Replaying this snapshot cannot add missing images."
        self.comparison_hint.setText(hint)
        self.comparison_hint.setVisible(bool(hint))
        rows = result.get(field, []) if archive.get("status") in {"complete", "partial"} else []
        self.results.setRowCount(len(rows))
        for row, data in enumerate(rows):
            if not isinstance(data, dict):
                raise ValueError("Malformed diagnostic comparison")
            values = [data.get("key", ""), self._format(data.get("predicted")), self._format(data.get("measured")),
                      self._format(data.get("residual")) if data.get("status") == "compared" else "",
                      data.get("unit", ""), data.get("status", ""), data.get("detail", "")]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(json.dumps(data, indent=2))
                self.results.setItem(row, column, item)
        self._plot(rows)

    def _plot(self, rows):
        self.figure.clear()
        color = "#e6edf2" if self._dark else "#293740"
        background = "#172027" if self._dark else "#fffdf9"
        self.figure.set_facecolor(background)
        axes = self.figure.subplots(1, 2)
        for axis, plane in zip(axes, ("sigx", "sigy")):
            axis.set_facecolor(background)
            data = [row for row in rows if isinstance(row, dict) and str(row.get("prediction_key", row.get("key", ""))).endswith("/" + plane)]
            for index, row in enumerate(data):
                scale = 1000 if row.get("unit") == "m" else 1
                for key, marker, label in (("predicted", "o", "Predicted"), ("measured", "x", "Measured")):
                    value = row.get(key)
                    if isinstance(value, (int, float)) and math.isfinite(value) and (key == "predicted" or row.get("status") in {"compared", "incompatible", "unverified", "limited"}):
                        label = "Predicted RMS" if key == "predicted" else "Observed " + row.get("measured_width_definition", "rms")
                        if row.get("measured_width_definition") == "image_roi_projection_rms":
                            label = "Model RMS" if key == "predicted" else "Image ROI RMS"
                        axis.plot(index, value * scale, marker, color="#45bcb0" if key == "predicted" else "#e7ad65", label=label)
            axis.set_xticks(range(len(data)))
            axis.set_xticklabels([str(row.get("key", "")).split("/")[0] for row in data], rotation=25)
            axis.set_title("X" if plane == "sigx" else "Y", color=color)
            axis.set_ylabel("Width [mm]" if any(row.get("status") == "incompatible" for row in data) else "RMS [mm]", color=color)
            axis.tick_params(colors=color)
            for spine in axis.spines.values():
                spine.set_color(color)
            handles, labels = axis.get_legend_handles_labels()
            if handles:
                unique = dict(zip(labels, handles))
                axis.legend(unique.values(), unique.keys(), facecolor=background, labelcolor=color, fontsize=8)
        self.canvas.draw_idle()

    def refresh_history(self):
        if not hasattr(self, "history"):
            return
        root = Path(self.output_edit.text()).expanduser()
        paths = set(root.glob("job_*/job.json")) | set(root.glob("shadow_*/manifest.json")) if root.is_dir() else set()
        if root.is_dir():
            paths.update(root.glob("job_*/cycles/cycle_*/shadow_model/manifest.json"))
        directories = {path.parent for path in paths} | self._imported_archives
        self.history.clear()
        for directory in sorted(directories, key=lambda path: str(path), reverse=True):
            try:
                archive = read_archive(directory)
                name = directory.name if directory.name != "shadow_model" else f"{directory.parents[2].name}/{directory.parent.name}"
                source = "simulated Real" if _is_simulated_particle_capture(archive) else archive["backend"]
                label = f"{archive['status']} · {source}\n{name}"
            except (ValueError, KeyError, TypeError, OSError) as error:
                label = f"Unreadable · {directory.name}\n{error}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, str(directory))
            self.history.addItem(item)

    def _history_selected(self, item, previous=None):
        self.use_inputs_button.setEnabled(False)
        if not item:
            return
        try:
            archive = self.show_archive(item.data(Qt.UserRole))
            self.history_details.setPlainText(json.dumps(archive, indent=2, ensure_ascii=False))
            particle = (archive.get("manifest") or {}).get("kind") == PARTICLE_RUN_KIND
            legacy = (archive["backend"] == "vm" or bool(archive.get("real_evidence_path"))) and bool(archive.get("snapshot_path"))
            reusable = archive["status"] in ("complete", "partial") if particle else legacy
            capture = bool(archive.get("particle_evidence_path") and not archive.get("manifest"))
            self.use_inputs_button.setEnabled(not self.controller.busy and (capture or (bool(archive.get("request")) and reusable)))
        except (ValueError, KeyError, TypeError, OSError) as error:
            self.history_details.setPlainText(f"Cannot read archive: {error}")
            self._error(error)

    def _open_archive_dialog(self):
        directory = QFileDialog.getExistingDirectory(self, "Open shadow archive", self.output_edit.text())
        if directory:
            self._open_saved(directory)

    def _open_snapshot_dialog(self):
        filename, _ = QFileDialog.getOpenFileName(self, "View machine snapshot", "", "JSON files (*.json)")
        if filename:
            try:
                snapshot = load_snapshot(filename)
                self.history_details.setPlainText(json.dumps(read_runtime_state(filename), indent=2))
                self.use_inputs_button.setEnabled(False)
                self.current_archive = {"directory": str(Path(filename).parent), "snapshot_path": filename, "backend": snapshot.backend}
                self.status_label.setText(f"{snapshot.backend.upper()} snapshot · view only · {snapshot.name}")
            except (ValueError, KeyError, TypeError, OSError) as error:
                self._error(error)

    def _open_saved(self, directory):
        try:
            self.show_archive(directory)
            self._imported_archives.add(Path(directory).resolve())
            self.refresh_history()
            for row in range(self.history.count()):
                if Path(self.history.item(row).data(Qt.UserRole)) == Path(directory).resolve():
                    self.history.setCurrentRow(row)
                    break
            self.tabs.setCurrentIndex(1)
        except (ValueError, KeyError, TypeError, OSError) as error:
            self._error(error)

    def _use_archive_inputs(self):
        archive = self.current_archive
        if not archive or self.controller.busy:
            return
        try:
            if archive.get("particle_evidence_path") and not archive.get("manifest"):
                self._use_particle_snapshot(archive)
                self.tabs.setCurrentIndex(0)
                return
            if not archive.get("request"):
                return
            if (archive.get("manifest") or {}).get("kind") == PARTICLE_RUN_KIND:
                if _is_preinjector(archive) and archive["status"] != "complete":
                    return
                read_archive(archive["run_dir"], verify_particle_inputs=True)
                self.load_request_file(Path(archive["run_dir"]) / "request.json")
                self.tabs.setCurrentIndex(0)
                return
            if archive["backend"] != "vm" and not archive.get("real_evidence_path"):
                return
            self.snapshot_edit.setText(archive["snapshot_path"])
            evidence = Path(archive["snapshot_path"]).parent / ("real_capture_evidence.json" if archive["backend"] == "real" else "vm_capture_evidence.json")
            self.evidence_edit.setText(str(evidence) if evidence.exists() else "")
            self.mode.setCurrentIndex(0)
            self.form.set_diagnostic_backend(archive["backend"])
            self.form.load_request(ShadowRequest.from_dict(archive["request"]))
            self.tabs.setCurrentIndex(0)
            self.status_label.setText("Archived inputs loaded for offline replay; no live reconnection")
        except (ValueError, KeyError, TypeError, OSError) as error:
            self._error(error)

    def open_directory(self):
        path = (self.current_archive or {}).get("directory", self.output_edit.text())
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve())))

    def closeEvent(self, event):
        if self.controller.busy:
            self._close_pending = True
            self.controller.stop()
            event.ignore()
        else:
            event.accept()


def _is_preinjector(archive):
    return ((archive.get("manifest") or {}).get("scope") == "preinjector"
            or (archive.get("job") or {}).get("scope") == "preinjector")


def _is_simulated_particle_capture(archive):
    return (archive.get("snapshot") or {}).get("snapshot_id", "").startswith("synthetic_real_particle_")


def main(argv=None):
    parser = argparse.ArgumentParser(description="HALF digital shadow GUI · offline demos and explicit read-only capture")
    parser.add_argument("--machine", choices=("half",), default=os.environ.get("HALF_LINAC_MACHINE_ID") or os.environ.get("HALF_MACHINE_ID") or "half")
    args = parser.parse_args(argv)
    if args.machine != "half":
        parser.error("Digital Shadow v1 supports HALF only")
    app = QApplication(sys.argv[:1])
    window = DigitalShadowWindow()
    install_qt_window_raise_handler(window)
    app.aboutToQuit.connect(window.controller.shutdown_now)
    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, lambda signum, frame: window.close())
    heartbeat = QTimer(window)
    heartbeat.timeout.connect(lambda: None)  # Keep Python signal handling responsive in Qt's event loop.
    heartbeat.start(100)
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
