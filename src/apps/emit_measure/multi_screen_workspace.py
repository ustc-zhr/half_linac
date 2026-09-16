"""PyQt workspace for read-only multi-screen emittance measurements.

The widget deliberately keeps machine actions out of this first integration:
model preparation is read-only, and acquisition only reads configured image PVs.
Manual samples make the workflow useful for archived/offline VM validation too.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from functools import partial
import json
from pathlib import Path
from typing import Callable, Mapping

import epics
import numpy as np
from PyQt5.QtCore import QTimer, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QCheckBox,
    QDoubleSpinBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QSplitter,
    QAbstractItemView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from matplotlib.colors import Normalize

from half_linac.src.shared.beam_diagnostics import (
    BackgroundStoreError,
    BEAM_IMAGE_COLORMAPS,
    DEFAULT_BEAM_IMAGE_COLORMAP,
    analyze_beam_image,
    analyze_raw_beam_image,
    assess_projection_quality,
    resolve_image_display_scale,
    ROIControl,
    load_background,
    resolve_beam_background_paths,
)
from half_linac.src.apps.emit_measure.mplwidget import MplWidget
from half_linac.src.apps.emit_measure.sample_selection import configure_sample_selection
from half_linac.src.shared.machine_profile import (
    build_model_backend,
    get_emit_multi_screen_preset,
    make_runtime_run_id,
    resolve_app_runtime_paths,
    resolve_channel,
    resolve_element_image_geometry,
)
from half_linac.src.shared.beam_matrix import normalized_emittance
from half_linac.src.apps.emit_measure.multi_screen import (
    BeamSizeSample,
    MultiScreenMeasurementSession,
    assess_multi_screen_observability,
    build_multi_screen_optics,
    load_multi_screen_archive,
    reconstruct_multi_screen_measurement,
    reconstruction_from_archive_payload,
    save_multi_screen_archive,
)

class _SyntheticProjection:
    normalized_projection = None
    fitted_projection = None
    axis = np.array([], dtype=float)
    center = None
    residual_rms = None


class _SyntheticFit:
    status = "valid"
    message = "legacy image reader returned fitted sizes"
    x_projection = _SyntheticProjection()
    y_projection = _SyntheticProjection()

    def __init__(self, sigx_mm: float, sigy_mm: float):
        self.sigx_mm = float(sigx_mm)
        self.sigy_mm = float(sigy_mm)

    @property
    def valid(self):
        return np.isfinite(self.sigx_mm) and self.sigx_mm > 0 and np.isfinite(self.sigy_mm) and self.sigy_mm > 0


class MultiScreenWorkspace(QWidget):
    """Configuring/acquisition/fit surface embedded in the Emittance window."""

    status_changed = pyqtSignal(str, str)

    def __init__(self, app_context, *, image_reader: Callable | None = None, parent=None):
        super().__init__(parent)
        self.app_context = app_context
        self.image_reader = image_reader
        self.session: MultiScreenMeasurementSession | None = None
        self.reconstruction = None
        self._archive_review = False
        self._last_frame = None
        self._last_fit = None
        self._last_frame_extent = None
        self._last_frame_screen = None
        self.beam_image_colormap = DEFAULT_BEAM_IMAGE_COLORMAP
        self.beam_image_logarithmic = False
        self.beam_image_overlays = True
        self.beam_width_method = "Gaussian fit"
        self.roi_status = "Off"
        self.background_status = "Off"
        self._background_image = None
        self._background_screen = None
        self._background_metadata = {}
        self.roi_control = None
        self._roi_screen = None
        self.roi_dialog = None
        self.state = "Configuring"
        self._configuration_guard = False
        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.setInterval(2000)
        self._auto_refresh_timer.timeout.connect(self._auto_refresh_current_image)
        self._build_ui()
        self._load_default_preset()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(9, 9, 6, 6)
        root.setSpacing(10)

        config = QFrame(self)
        config.setObjectName("controlCard")
        config.setMaximumWidth(620)
        config_layout = QVBoxLayout(config)
        config_layout.setContentsMargins(10, 10, 10, 10)
        config_layout.setSpacing(8)
        config_title = QLabel("Measurement Setup", config)
        config_title.setObjectName("panelTitle")
        config_layout.addWidget(config_title)
        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(4)
        self.preset_combo = QComboBox(config)
        self.preset_combo.currentIndexChanged.connect(self._apply_selected_preset)
        self.model_line_edit = QComboBox(config)
        self.model_line_edit.setEditable(False)
        self.reference_combo = QComboBox(config)
        self.energy_spin = QDoubleSpinBox(config)
        self.energy_spin.setRange(0.001, 1.0e6)
        self.energy_spin.setDecimals(3)
        self.energy_spin.setSuffix(" MeV")
        self.samples_spin = QSpinBox(config)
        self.samples_spin.setRange(1, 10_000)
        self.samples_spin.setValue(3)
        # Kept in the preset state for compatibility; Multi-Screen acquisition
        # is explicit and does not apply a client-side delay between clicks.
        self.interval_spin = QDoubleSpinBox(config)
        self.interval_spin.setRange(0.0, 3600.0)
        self.interval_spin.setDecimals(2)
        self.interval_spin.setSuffix(" s")
        self.interval_spin.hide()
        self.model_line_edit.currentIndexChanged.connect(self._configuration_changed)
        self.reference_combo.currentIndexChanged.connect(self._configuration_changed)
        self.energy_spin.valueChanged.connect(self._configuration_changed)
        self.samples_spin.valueChanged.connect(self._configuration_changed)
        form.addWidget(QLabel("Preset", config), 0, 0)
        form.addWidget(self.preset_combo, 0, 1, 1, 3)
        form.addWidget(QLabel("Model line", config), 1, 0)
        form.addWidget(self.model_line_edit, 1, 1)
        form.addWidget(QLabel("Reference", config), 1, 2)
        form.addWidget(self.reference_combo, 1, 3)
        form.addWidget(QLabel("Energy", config), 2, 0)
        form.addWidget(self.energy_spin, 2, 1)
        form.addWidget(QLabel("Required samples / screen", config), 2, 2)
        form.addWidget(self.samples_spin, 2, 3)
        form.setColumnStretch(1, 1)
        form.setColumnStretch(3, 1)
        config_layout.addLayout(form)

        actions_card = QFrame(self)
        actions_card.setObjectName("controlCard")
        actions_outer = QVBoxLayout(actions_card)
        actions_outer.setContentsMargins(10, 8, 10, 8)
        actions_outer.setSpacing(6)
        actions_title = QLabel("Actions", actions_card)
        actions_title.setObjectName("panelTitle")
        actions_outer.addWidget(actions_title)
        controls = QHBoxLayout()
        controls.setSpacing(12)
        self.prepare_button = QPushButton("Prepare Measurement", self)
        self.new_button = QPushButton("Clear", self)
        self.acquire_button = QPushButton("Acquire Sample", self)
        self.preview_button = QPushButton("Preview", self)
        self.auto_refresh_checkbox = QCheckBox("Auto refresh", self)
        self.auto_refresh_checkbox.setChecked(True)
        self.auto_refresh_checkbox.toggled.connect(self._update_auto_refresh)
        self.reconstruct_button = QPushButton("Reconstruct", self)
        self.save_button = QPushButton("Save As...", self)
        self.load_button = QPushButton("Load Archive", self)
        self.prepare_button.setText("Prepare")
        self.new_button.setText("Clear")
        self.acquire_button.setText("Acquire Sample")
        self.reconstruct_button.setText("Reconstruct")
        self.prepare_button.setToolTip("Prepare the optics model and start a new measurement session.")
        self.new_button.setToolTip("Clear the current samples and reconstruction result.")
        self.acquire_button.setToolTip("Read the selected screen image, fit it locally, and accept the sample.")
        self.reconstruct_button.setToolTip("Reconstruct the transverse beam matrix from accepted samples.")
        self.prepare_button.setProperty("role", "primary")
        self.acquire_button.setProperty("role", "primary")
        for button in (
            self.prepare_button,
            self.new_button,
            self.acquire_button,
            self.reconstruct_button,
            self.save_button,
            self.load_button,
        ):
            button.setProperty("compact", True)
        for button in (
            self.prepare_button,
            self.acquire_button,
            self.reconstruct_button,
            self.new_button,
        ):
            controls.addWidget(button)
        controls.addStretch(1)
        self.prepare_button.clicked.connect(self.prepare_measurement)
        self.new_button.clicked.connect(self.new_measurement)
        self.acquire_button.clicked.connect(self.acquire_sample)
        self.preview_button.clicked.connect(self.preview_sample)
        self.reconstruct_button.clicked.connect(self.reconstruct)
        self.save_button.clicked.connect(self.save_archive)
        self.load_button.clicked.connect(self.load_archive)
        actions_outer.addLayout(controls)
        self._setup_card = config
        self._actions_card = actions_card

        body = QGridLayout()
        body.setHorizontalSpacing(10)
        body.setVerticalSpacing(10)
        left_column = QVBoxLayout()
        left_column.setContentsMargins(0, 0, 0, 0)
        left_column.setSpacing(10)
        right_column = QVBoxLayout()
        right_column.setContentsMargins(0, 0, 0, 0)
        right_column.setSpacing(10)
        screens_box = QFrame(self)
        screens_box.setObjectName("plotCard")
        screens_layout = QVBoxLayout(screens_box)
        screens_layout.setContentsMargins(10, 10, 10, 10)
        screens_layout.setSpacing(8)
        screens_title = QLabel("Screen Sequence", screens_box)
        screens_title.setObjectName("panelTitle")
        screens_layout.addWidget(screens_title)
        self.screen_list = QListWidget(screens_box)
        self.screen_list.setFixedHeight(104)
        self.screen_list.currentRowChanged.connect(self._screen_changed)
        screens_layout.addWidget(self.screen_list)
        screen_actions = QHBoxLayout()
        self.screen_candidate_combo = QComboBox(screens_box)
        self.add_screen_button = QPushButton("Add", screens_box)
        self.remove_screen_button = QPushButton("Remove", screens_box)
        self.add_screen_button.setProperty("compact", True)
        self.remove_screen_button.setProperty("compact", True)
        self.add_screen_button.clicked.connect(self._add_screen)
        self.remove_screen_button.clicked.connect(self._remove_screen)
        screen_actions.addWidget(self.screen_candidate_combo, 1)
        screen_actions.addWidget(self.add_screen_button)
        screen_actions.addWidget(self.remove_screen_button)
        screens_layout.addLayout(screen_actions)
        left_column.addWidget(screens_box)

        image_box = QFrame(self)
        image_box.setObjectName("plotCard")
        image_layout = QVBoxLayout(image_box)
        image_layout.setContentsMargins(10, 10, 10, 10)
        image_layout.setSpacing(8)
        self.image_title_label = QLabel("Current Screen Image", image_box)
        self.image_title_label.setObjectName("panelTitle")
        image_header = QHBoxLayout()
        image_header.setSpacing(6)
        image_header.addWidget(self.image_title_label)
        image_header.addStretch(1)
        self.image_status_label = QLabel("No image read yet. Click Refresh to read the selected screen.", image_box)
        self.image_status_label.setWordWrap(True)
        self.image_status_label.setProperty("role", "field")
        self.image_status_label.hide()
        self.image_fit_label = QLabel("--", image_box)
        self.pv_cross_check_label = QLabel("--", image_box)
        self.selected_optics_label = QLabel("--", image_box)
        for value_label in (
            self.image_fit_label,
            self.pv_cross_check_label,
            self.selected_optics_label,
        ):
            value_label.setObjectName("multiScreenDetail")
        self.beam_fit_summary_label = QLabel("Fit: Gaussian · No image", image_box)
        self.beam_fit_summary_label.setProperty("role", "field")
        self.roi_status_label = QLabel("ROI: Off", image_box)
        self.roi_status_label.setProperty("role", "field")
        self.roi_button = QPushButton("Edit...", image_box)
        self.roi_button.setProperty("compact", True)
        self.roi_button.clicked.connect(self._show_roi_info)
        self.preview_button.setText("Refresh")
        self.preview_button.setProperty("compact", True)
        image_header.addWidget(self.beam_fit_summary_label)
        image_header.addWidget(self.roi_status_label)
        image_header.addWidget(self.roi_button)
        image_header.addWidget(self.auto_refresh_checkbox)
        image_header.addWidget(self.preview_button)
        image_layout.addLayout(image_header)
        display_tools = QHBoxLayout()
        display_tools.setSpacing(6)
        display_tools.addWidget(QLabel("BG:", image_box))
        self.beam_background_status_label = QLabel("Off", image_box)
        self.beam_background_status_label.setProperty("role", "field")
        self.beam_background_manage_button = QPushButton("Manage...", image_box)
        self.beam_background_manage_button.setProperty("compact", True)
        self.beam_background_manage_button.clicked.connect(self._show_background_info)
        self.beam_image_background_checkbox = QCheckBox("Apply", image_box)
        self.beam_image_background_checkbox.setToolTip("Background subtraction is available through the Scan image workflow.")
        self.beam_image_background_checkbox.toggled.connect(self._background_toggled)
        self.beam_image_display_button = QPushButton("Display...", image_box)
        self.beam_image_display_button.setProperty("compact", True)
        self.beam_image_display_button.clicked.connect(self._show_display_dialog)
        display_tools.addWidget(self.beam_background_status_label)
        display_tools.addStretch(1)
        display_tools.addWidget(self.beam_background_manage_button)
        display_tools.addWidget(self.beam_image_background_checkbox)
        display_tools.addWidget(self.beam_image_display_button)
        image_layout.addLayout(display_tools)
        self.image_widget = MplWidget(image_box)
        self.image_widget.fig.clear()
        self.image_axes = self.image_widget.fig.add_subplot(111)
        self.image_widget.setMinimumHeight(300)
        image_layout.addWidget(self.image_widget, 1)
        image_details = QGridLayout()
        image_details.setHorizontalSpacing(12)
        image_details.setVerticalSpacing(2)
        for column, (title, value_label) in enumerate(
            (
                ("Local fit", self.image_fit_label),
                ("Published PV", self.pv_cross_check_label),
                ("Optics", self.selected_optics_label),
            )
        ):
            title_label = QLabel(title, image_box)
            title_label.setProperty("role", "field")
            value_label.setWordWrap(True)
            image_details.addWidget(title_label, 0, column)
            image_details.addWidget(value_label, 1, column)
            image_details.setColumnStretch(column, 1)
        image_layout.addLayout(image_details)
        self.review_tabs = QTabWidget(self)
        self.review_tabs.addTab(image_box, "Image")
        right_column.addWidget(self.review_tabs, 1)

        samples_card = QFrame(self)
        samples_card.setObjectName("plotCard")
        samples_layout = QVBoxLayout(samples_card)
        samples_layout.setContentsMargins(10, 10, 10, 10)
        samples_layout.setSpacing(8)
        self.samples_summary_label = QLabel("No samples", samples_card)
        self.samples_summary_label.setProperty("role", "field")
        samples_layout.addWidget(self.samples_summary_label)
        samples_splitter = QSplitter(Qt.Vertical, samples_card)
        self.samples_plot = MplWidget(samples_splitter)
        self.samples_plot.fig.clear()
        self.sample_axes = self.samples_plot.fig.subplots(2, 1, sharex=True)
        self.samples_plot.setMinimumHeight(220)
        self.samples_plot.canvas.mpl_connect("pick_event", self._sample_picked)
        self.samples_table = QTableWidget(0, 6, samples_splitter)
        configure_sample_selection(self.samples_table)
        self.samples_table.setHorizontalHeaderLabels(
            ("Use", "Screen", "#", "σx (mm)", "σy (mm)", "Quality")
        )
        self.samples_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.samples_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.samples_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.samples_table.setAlternatingRowColors(True)
        self.samples_table.verticalHeader().hide()
        self.samples_table.setMinimumHeight(160)
        header = self.samples_table.horizontalHeader()
        for column in range(5):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        self.samples_table.itemChanged.connect(self._sample_use_changed)
        self.samples_table.itemSelectionChanged.connect(self._draw_samples)
        samples_splitter.addWidget(self.samples_plot)
        samples_splitter.addWidget(self.samples_table)
        samples_splitter.setStretchFactor(0, 3)
        samples_splitter.setStretchFactor(1, 2)
        samples_splitter.setSizes([300, 220])
        samples_layout.addWidget(samples_splitter, 1)
        archive_row = QHBoxLayout()
        archive_row.setSpacing(6)
        self.exclude_samples_button = QPushButton("Exclude Selected", samples_card)
        self.restore_samples_button = QPushButton("Use All", samples_card)
        self.recalculate_button = QPushButton("Recalculate", samples_card)
        self.recalculate_button.clicked.connect(self.reconstruct)
        self.recalculate_button.setToolTip(
            "Recalculate from checked samples. Each screen needs at least one active sample; "
            "the acquisition target does not have to be met."
        )
        self.exclude_samples_button.clicked.connect(self._exclude_selected_samples)
        self.restore_samples_button.clicked.connect(self._restore_samples)
        self.restore_samples_button.setToolTip("Enable all samples with fitted sizes, including quality-rejected samples.")
        for button in (self.exclude_samples_button, self.restore_samples_button, self.recalculate_button):
            button.setProperty("compact", True)
            archive_row.addWidget(button)
        archive_row.addStretch(1)
        archive_row.addWidget(self.save_button)
        archive_row.addWidget(self.load_button)
        samples_layout.addLayout(archive_row)
        self.review_tabs.addTab(samples_card, "Samples")
        self.review_tabs.currentChanged.connect(lambda _index: self._draw_samples())

        results_box = QFrame(self)
        results_box.setObjectName("resultCard")
        results_outer = QVBoxLayout(results_box)
        results_outer.setContentsMargins(10, 10, 10, 10)
        results_outer.setSpacing(8)
        results_title = QLabel("Measurement Results", results_box)
        results_title.setObjectName("panelTitle")
        results_outer.addWidget(results_title)
        results_layout = QGridLayout()
        results_layout.setVerticalSpacing(8)
        self.status_label = QLabel("Configuring", results_box)
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("multiScreenStatus")
        self.diagnostic_label = QLabel("Optics: no model prepared", results_box)
        self.diagnostic_label.setWordWrap(True)
        self.results_label = QLabel("No reconstruction", results_box)
        self.results_label.setWordWrap(True)
        results_layout.addWidget(self.status_label, 0, 0, 1, 5)
        results_layout.addWidget(self.diagnostic_label, 1, 0, 1, 5)
        self.result_value_labels = {
            plane: [QLabel("--", results_box) for _ in range(4)]
            for plane in ("X", "Y")
        }
        for column, title in enumerate(("Plane", "εg (mm·mrad)", "εn (mm·mrad)", "β (m)", "α")):
            title_label = QLabel(title, results_box)
            title_label.setProperty("role", "field")
            results_layout.addWidget(title_label, 2, column)
        for row, plane in enumerate(("X", "Y"), start=3):
            plane_label = QLabel(plane, results_box)
            plane_label.setObjectName("multiScreenStatus")
            results_layout.addWidget(plane_label, row, 0)
            for column, value_label in enumerate(self.result_value_labels[plane], start=1):
                results_layout.addWidget(value_label, row, column)
        results_layout.addWidget(self.results_label, 5, 0, 1, 5)
        results_layout.setColumnStretch(0, 1)
        for column in range(1, 5):
            results_layout.setColumnStretch(column, 2)
        results_layout.setAlignment(Qt.AlignTop)
        results_outer.addLayout(results_layout)
        results_outer.addStretch(1)
        right_column.addWidget(results_box)

        left_column.insertWidget(0, config)
        left_column.insertWidget(1, actions_card)
        left_widget = QWidget(self)
        left_widget.setMaximumWidth(620)
        left_widget.setLayout(left_column)
        right_widget = QWidget(self)
        right_widget.setLayout(right_column)
        body.addWidget(left_widget, 0, 0, Qt.AlignTop)
        body.addWidget(right_widget, 0, 1)
        # Match the Quad Scan grid: a compact 2/8 control column beside the
        # wider 6/8 plot-and-data column.
        body.setColumnStretch(0, 2)
        body.setColumnStretch(1, 6)
        root.addLayout(body, 1)
        self._update_button_state()
        self._clear_image_display()

    @staticmethod
    def _screen_id(item):
        return item.data(Qt.UserRole) or item.text()

    def _refresh_screen_counts(self):
        counts = self.session.acquisition.sample_counts if self.session else {}
        for row in range(self.screen_list.count()):
            item = self.screen_list.item(row)
            screen = self._screen_id(item)
            item.setData(Qt.UserRole, screen)
            item.setText(
                f"{screen} · {counts.get(screen, 0)}/{self.session.acquisition.target_samples_per_screen}"
                if self.session else screen
            )

    def _screen_changed(self, *_args) -> None:
        self._update_button_state()
        item = self.screen_list.currentItem()
        if item is None:
            self._clear_image_display()
            return
        screen = self._screen_id(item)
        try:
            self._ensure_roi_control(screen)
            self._roi_changed()
        except Exception:
            self.roi_status = "Off"
        if self._last_frame_screen != screen:
            self._clear_image_display(f"{screen} selected. Click Refresh to read its current image.")
        self._update_selected_optics(screen)

    def _load_default_preset(self) -> None:
        workflow = self.app_context.emit_measure_workflow
        if workflow is None:
            self._set_state("Invalid", "emit_measure workflow is unavailable")
            return
        for preset in workflow.multi_screen_presets:
            self.preset_combo.addItem(preset.id, preset.id)
        self._apply_selected_preset()

    def _apply_selected_preset(self) -> None:
        if self.preset_combo.currentData() is None:
            return
        try:
            preset = get_emit_multi_screen_preset(
                self.app_context,
                self.preset_combo.currentData(),
            )
        except Exception as exc:
            self._set_state("Invalid", str(exc))
            return
        self.model_line_edit.clear()
        self.model_line_edit.addItem(preset.model_line)
        self.model_line_edit.setCurrentText(preset.model_line)
        self.reference_combo.clear()
        self.reference_combo.addItems(preset.screens)
        self.reference_combo.setCurrentText(preset.reference_element)
        self.energy_spin.setValue(float(preset.energy_mev or 0.001))
        self.samples_spin.setValue(preset.sampling.samples_per_screen)
        self.interval_spin.setValue(preset.sampling.sample_interval_s)
        self.screen_list.clear()
        self.screen_list.addItems(preset.screens)
        if self.screen_list.count():
            self.screen_list.setCurrentRow(0)
        self._refresh_candidates()
        self.new_measurement()

    def _refresh_candidates(self) -> None:
        selected = {self._screen_id(self.screen_list.item(index)) for index in range(self.screen_list.count())}
        self.screen_candidate_combo.clear()
        try:
            candidates = [element.id for element in self.app_context.profile.elements if element.kind == "flag"]
        except Exception:
            candidates = []
        self.screen_candidate_combo.addItems([item for item in candidates if item not in selected])

    def _add_screen(self) -> None:
        screen = self.screen_candidate_combo.currentText().strip()
        if screen and screen not in [self._screen_id(self.screen_list.item(i)) for i in range(self.screen_list.count())]:
            self.screen_list.addItem(screen)
            self.new_measurement()
            self._refresh_candidates()

    def _remove_screen(self) -> None:
        if self.screen_list.count() <= 3:
            self._set_state("Configuring", "At least three screens are required")
            return
        row = self.screen_list.currentRow()
        if row >= 0:
            self.screen_list.takeItem(row)
            self.new_measurement()
            self._refresh_candidates()

    def _set_state(self, state: str, message: str = "") -> None:
        self.state = state
        if state == "Archived":
            self._archive_review = True
        self.status_label.setText(f"{state}: {message}" if message else state)
        self.status_changed.emit(state, message)
        self._update_button_state()

    def _configuration_changed(self, *_args) -> None:
        if self._configuration_guard or self.session is None:
            return
        if self.state not in {"Ready", "Acquiring", "Fit Ready", "Complete", "Partial", "Archived"}:
            return
        self.new_measurement()
        self._set_state("Configuring", "Configuration changed; prepare a new measurement")

    def _update_button_state(self, *_args) -> None:
        prepared = self.session is not None and self.state not in {"Invalid", "Configuring"}
        writable = prepared and not self._archive_review
        self.acquire_button.setEnabled(writable and not self.session.acquisition.complete)
        self.preview_button.setEnabled(writable)
        self.reconstruct_button.setEnabled(
            self.session is not None and self.session.acquisition.can_reconstruct
        )
        self.recalculate_button.setEnabled(self.reconstruct_button.isEnabled())
        self.save_button.setEnabled(self.session is not None)
        self.add_screen_button.setEnabled(not self._archive_review)
        self.remove_screen_button.setEnabled(
            not self._archive_review and self.screen_list.count() > 3
        )
        self._update_auto_refresh()

    def _update_auto_refresh(self, *_args) -> None:
        active = (
            not self._archive_review
            and self.auto_refresh_checkbox.isChecked()
            and self.session is not None
            and self.state in {"Ready", "Acquiring", "Fit Ready", "Complete", "Partial"}
        )
        if active and not self._auto_refresh_timer.isActive():
            self._auto_refresh_timer.start()
        elif not active and self._auto_refresh_timer.isActive():
            self._auto_refresh_timer.stop()

    def _auto_refresh_current_image(self) -> None:
        if self.session is None or self._archive_review:
            return
        self.preview_sample(auto=True)

    def new_measurement(self) -> None:
        self._archive_review = False
        self.session = None
        self.reconstruction = None
        self._refresh_samples_table()
        self.diagnostic_label.setText("Optics: no model prepared")
        self.results_label.setText("No reconstruction")
        self._reset_result_metrics()
        self.image_fit_label.setText("--")
        self.pv_cross_check_label.setText("--")
        self._clear_image_display()
        self._set_state("Configuring")

    def prepare_measurement(self) -> None:
        self.new_measurement()
        screens = tuple(self._screen_id(self.screen_list.item(index)) for index in range(self.screen_list.count()))
        if len(screens) < 3 or len(set(screens)) != len(screens):
            self._set_state("Invalid", "Select at least three unique screens")
            return
        self._set_state("Preparing")
        try:
            backend = build_model_backend(
                self.app_context,
                energy_mev=self.energy_spin.value(),
                line_name=self.model_line_edit.currentText().strip() or None,
            )
            optics = build_multi_screen_optics(
                backend,
                self.reference_combo.currentText().strip(),
                screens,
            )
            observability = assess_multi_screen_observability(optics)
            self.session = MultiScreenMeasurementSession.create(
                machine=self.app_context.profile.machine.id,
                backend=self.app_context.control_backend.name,
                preset=self.preset_combo.currentData(),
                model_line=backend.line_name,
                energy_mev=self.energy_spin.value(),
                optics=optics,
                target_samples_per_screen=self.samples_spin.value(),
            )
            self.diagnostic_label.setText(
                f"Optics: {observability.status.upper()} · X rank {observability.x.rank}/3, "
                f"cond {observability.x.condition_number:.3g}; "
                f"Y rank {observability.y.rank}/3, cond {observability.y.condition_number:.3g}"
            )
            self.session = replace(self.session, beam_width_method=self.beam_width_method)
            if observability.status in {"invalid", "poor"}:
                self._set_state("Invalid", observability.message)
            else:
                self._set_state("Ready", observability.message)
            self._refresh_samples_table()
            self._update_selected_optics()
        except Exception as exc:
            self.session = None
            self._set_state("Invalid", str(exc))

    def add_manual_sample(self) -> None:
        if self.session is None:
            return
        screen = self._screen_id(self.screen_list.currentItem()) if self.screen_list.currentItem() else self.session.acquisition.next_screen
        if screen is None:
            return
        sigma_x, accepted_x = QInputDialog.getDouble(self, "Manual Sample", f"{screen} σx (mm)", 1.0, 0.000001, 1e6, 6)
        if not accepted_x:
            return
        sigma_y, accepted_y = QInputDialog.getDouble(self, "Manual Sample", f"{screen} σy (mm)", 1.0, 0.000001, 1e6, 6)
        if not accepted_y:
            return
        self._accept_sample(screen, sigma_x / 1000.0, sigma_y / 1000.0, "manual")

    def acquire_sample(self) -> None:
        if self.session is None or self._archive_review:
            return
        item = self.screen_list.currentItem()
        screen = self._screen_id(item) if item is not None else self.session.acquisition.next_screen
        if screen is None:
            return
        if self.session.acquisition.sample_counts[screen] >= self.session.acquisition.target_samples_per_screen:
            self._set_state("Ready", f"{screen} already has enough active samples; select a screen needing samples")
            return
        try:
            payload = self._read_image_payload(screen)
            fit = payload["fit"]
            self._display_payload(screen, payload)
        except Exception as exc:
            self._set_state("Ready", f"{screen}: {exc}")
            return
        quality = self._fit_quality(fit, payload.get("pv_sigx"), payload.get("pv_sigy"))
        rejected = (
            not fit.valid
            or any(quality.get(f"{plane}_status") in {"clipped", "underresolved", "poor_fit"} for plane in ("x", "y"))
        )
        def size_m(value):
            return float(value) / 1000 if value is not None and np.isfinite(value) and value > 0 else None
        x, y = size_m(fit.sigx_mm), size_m(fit.sigy_mm)
        rejected = rejected or x is None or y is None
        if rejected:
            quality["rejected"] = True
        self._accept_sample(screen, x, y, "image", quality=quality, enabled=not rejected)

    def preview_sample(self, *, auto: bool = False) -> None:
        if self._archive_review:
            return
        item = self.screen_list.currentItem()
        screen = self._screen_id(item) if item is not None else None
        if not screen:
            self._set_state("Ready", "Select a screen to preview")
            return
        try:
            payload = self._read_image_payload(screen)
        except Exception as exc:
            self._clear_image_display(f"{screen}: {exc}")
            self._set_state("Ready", f"{screen}: {exc}")
            return
        self._display_frame(
            screen,
            payload.get("image"),
            payload["fit"],
            payload.get("extent"),
            payload.get("pv_sigx"),
            payload.get("pv_sigy"),
        )
        fit = payload["fit"]
        if fit.valid:
            if not auto:
                self._set_state("Ready", f"{screen}: preview updated; not accepted")
        else:
            if not auto:
                self._set_state("Ready", f"{screen}: fit {fit.status}; sample not accepted")

    def _read_image_sample(self, screen: str) -> tuple[float, float]:
        payload = self._read_image_payload(screen)
        fit = payload["fit"]
        self._display_payload(screen, payload)
        if not fit.valid or fit.sigx_mm is None or fit.sigy_mm is None:
            raise RuntimeError(f"image fit {fit.status}: {fit.message}")
        return fit.sigx_mm / 1000.0, fit.sigy_mm / 1000.0

    def _read_image_payload(self, screen: str) -> dict[str, object]:
        if self.image_reader is not None:
            values = self.image_reader(screen)
            if isinstance(values, Mapping):
                image = values.get("image")
                fit = values.get("fit")
                extent = values.get("extent")
                pv_sigx = values.get("pv_sigx")
                pv_sigy = values.get("pv_sigy")
                if fit is None and image is not None and extent is not None:
                    _prepared, fit = analyze_beam_image(
                        np.asarray(image, dtype=float), extent=extent, method=self.beam_width_method
                    )
                if fit is not None:
                    payload = {
                        "image": None if image is None else np.asarray(image, dtype=float),
                        "fit": fit,
                        "extent": extent,
                        "pv_sigx": pv_sigx,
                        "pv_sigy": pv_sigy,
                    }
                    return payload
            if not hasattr(values, "__len__") or len(values) < 2:
                raise RuntimeError("image reader must return (sigma_x_m, sigma_y_m)")
            sigma_x, sigma_y = float(values[0]), float(values[1])
            return {
                "image": None,
                "fit": _SyntheticFit(sigma_x * 1000.0, sigma_y * 1000.0),
                "extent": None,
                "pv_sigx": None,
                "pv_sigy": None,
            }
        geometry = resolve_element_image_geometry(
            self.app_context,
            screen,
            self.app_context.control_backend.name,
        )
        image_pv = resolve_channel(self.app_context, screen, "image")
        raw = epics.caget(image_pv, timeout=1.0)
        if raw is None:
            raise RuntimeError(f"image PV unavailable: {image_pv}")
        width = geometry.shape[0] * geometry.pixel_width_mm
        height = geometry.shape[1] * geometry.pixel_width_mm
        extent = (-width / 2, width / 2, -height / 2, height / 2)
        self._ensure_roi_control(screen)
        roi = self.roi_control.active_roi() if self.roi_control is not None else None
        background = (
            self._background_image
            if self.beam_image_background_checkbox.isChecked()
            and self._background_screen == screen
            else None
        )
        try:
            prepared, fit = analyze_raw_beam_image(
                raw,
                pixel_shape=geometry.shape,
                flip_y=geometry.flip_y,
                extent=extent,
                background=background,
                roi=roi,
                full_frame_for_roi=True,
                analyzer=partial(analyze_beam_image, method=self.beam_width_method),
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeError(str(exc)) from exc
        pv_sigx = self._read_optional_size_channel(screen, "sigx")
        pv_sigy = self._read_optional_size_channel(screen, "sigy")
        payload = {
            "image": prepared,
            "fit": fit,
            "extent": extent,
            "pv_sigx": pv_sigx,
            "pv_sigy": pv_sigy,
        }
        return payload

    def _background_toggled(self, checked: bool) -> None:
        self.background_status = "Applied" if checked and self._background_image is not None else "Off"
        self.beam_background_status_label.setText(self.background_status)

    def _display_payload(self, screen: str, payload: Mapping[str, object]) -> None:
        self._display_frame(
            screen,
            payload.get("image"),
            payload["fit"],
            payload.get("extent"),
            payload.get("pv_sigx"),
            payload.get("pv_sigy"),
        )

    def _read_optional_size_channel(self, screen: str, logical_channel: str):
        try:
            value = epics.caget(
                resolve_channel(self.app_context, screen, logical_channel),
                timeout=0.2,
            )
            value = float(value)
            return value if np.isfinite(value) and value > 0.0 else None
        except Exception:
            return None

    def _display_image_comparison(self, screen, sigma_x, sigma_y, pv_sigx, pv_sigy, *, used=True):
        if used and np.isfinite(sigma_x) and np.isfinite(sigma_y):
            self.image_fit_label.setText(
                f"{screen}: σx={float(sigma_x) * 1000:.6g} mm, "
                f"σy={float(sigma_y) * 1000:.6g} mm"
            )
        else:
            self.image_fit_label.setText(f"{screen}: invalid; not used")
        if pv_sigx is None and pv_sigy is None:
            self.pv_cross_check_label.setText(
                "Unavailable; not used"
            )
            return
        x_text = "--" if pv_sigx is None else f"{float(pv_sigx):.6g} mm"
        y_text = "--" if pv_sigy is None else f"{float(pv_sigy):.6g} mm"
        self.pv_cross_check_label.setText(
            f"{screen}: σx={x_text}, σy={y_text} (not used)"
        )

    def _display_frame(self, screen, image, fit, extent, pv_sigx, pv_sigy) -> None:
        self._last_frame = None if image is None else np.asarray(image, dtype=float)
        self._last_fit = fit
        self._last_frame_extent = None if extent is None else tuple(float(v) for v in extent)
        self._last_frame_screen = screen
        self._display_image_comparison(
            screen,
            fit.sigx_mm / 1000.0 if fit.sigx_mm is not None else float("nan"),
            fit.sigy_mm / 1000.0 if fit.sigy_mm is not None else float("nan"),
            pv_sigx,
            pv_sigy,
            used=fit.valid,
        )
        self.beam_fit_summary_label.setText(
            f"Fit: {getattr(fit, 'method', self.beam_width_method)} · {'valid' if fit.valid else fit.status}"
        )
        self.roi_status_label.setText(f"ROI: {self.roi_status}")
        self.beam_background_status_label.setText(self.background_status)
        self.image_status_label.setText(
            f"{screen}: {fit.status} · {fit.message}" if fit.message else f"{screen}: {fit.status}"
        )
        self.image_title_label.setText(f"Current Screen Image · {screen}")
        self._update_selected_optics(screen)
        self.image_axes.clear()
        self._style_image_axes()
        if self._last_frame is not None:
            finite = self._last_frame[np.isfinite(self._last_frame)]
            if finite.size:
                vmin, vmax = float(np.min(finite)), float(np.max(finite))
                if vmin >= vmax:
                    vmax = vmin + 1.0
                display_image, display_norm, _warning = resolve_image_display_scale(
                    self._last_frame,
                    logarithmic=self.beam_image_logarithmic,
                )
                self.image_axes.imshow(
                    display_image,
                    origin="lower",
                    extent=self._last_frame_extent,
                    aspect="auto",
                    cmap=self.beam_image_colormap,
                    norm=display_norm if display_norm is not None else Normalize(vmin=vmin, vmax=vmax),
                )
                self.image_axes.set_xlabel("x (mm)")
                self.image_axes.set_ylabel("y (mm)")
                self.image_axes.set_title("")
                if self.beam_image_overlays and fit.x_projection.center is not None:
                    self.image_axes.axvline(
                        fit.x_projection.center, color="white", linestyle="--", alpha=0.8
                    )
                if self.beam_image_overlays and fit.y_projection.center is not None:
                    self.image_axes.axhline(
                        fit.y_projection.center, color="white", linestyle="--", alpha=0.8
                    )
                width = abs(self._last_frame_extent[1] - self._last_frame_extent[0])
                height = abs(self._last_frame_extent[3] - self._last_frame_extent[2])
                x_projection = fit.x_projection
                y_projection = fit.y_projection
                if self.beam_image_overlays and x_projection.normalized_projection is not None:
                    self.image_axes.plot(
                        x_projection.axis,
                        self._last_frame_extent[2] + x_projection.normalized_projection * height * 0.25,
                        "--",
                        color="cyan",
                        label="projection",
                    )
                if self.beam_image_overlays and x_projection.fitted_projection is not None:
                    self.image_axes.plot(
                        x_projection.axis,
                        self._last_frame_extent[2] + x_projection.fitted_projection * height * 0.25,
                        color="orange",
                        label="Gaussian fit",
                    )
                if self.beam_image_overlays and y_projection.normalized_projection is not None:
                    self.image_axes.plot(
                        self._last_frame_extent[0] + y_projection.normalized_projection * width * 0.25,
                        y_projection.axis,
                        "--",
                        color="cyan",
                    )
                if self.beam_image_overlays and y_projection.fitted_projection is not None:
                    self.image_axes.plot(
                        self._last_frame_extent[0] + y_projection.fitted_projection * width * 0.25,
                        y_projection.axis,
                        color="orange",
                    )
                if (
                    self.roi_control is not None
                    and self._roi_screen == screen
                    and self.roi_control.use_roi.isChecked()
                ):
                    self.roi_control.attach_axes(
                        self.image_axes,
                        extent=self._last_frame_extent,
                    )
                if self.image_axes.lines:
                    self.image_axes.legend(loc="upper right", fontsize=8)
        else:
            self.image_axes.text(0.5, 0.5, "No image frame", ha="center", va="center", transform=self.image_axes.transAxes)
            self.image_axes.set_axis_off()
        self.image_widget.canvas.draw_idle()

    def _clear_image_display(self, message: str = "No image read yet. Click Refresh to read the selected screen.") -> None:
        self._last_frame = None
        self._last_fit = None
        self._last_frame_extent = None
        self._last_frame_screen = None
        if not hasattr(self, "image_axes"):
            return
        self.image_axes.clear()
        self._style_image_axes()
        self.image_axes.text(0.5, 0.5, "No image preview", ha="center", va="center", transform=self.image_axes.transAxes)
        self.image_axes.set_axis_off()
        self.image_status_label.setText(message)
        self.image_title_label.setText("Current Screen Image")
        self.beam_fit_summary_label.setText(f"Fit: {self.beam_width_method} · No image")
        self.image_widget.canvas.draw_idle()
        self.image_fit_label.setText("--")
        self.pv_cross_check_label.setText("--")
        self.selected_optics_label.setText("--")

    def _style_image_axes(self) -> None:
        """Keep the Multi-Screen canvas consistent with the Quad Scan image card."""
        if hasattr(self.image_widget.fig, "set_layout_engine"):
            self.image_widget.fig.set_layout_engine(None)
        else:
            self.image_widget.fig.set_constrained_layout(False)
        self.image_widget.fig.subplots_adjust(
            left=0.07,
            right=0.995,
            bottom=0.02,
            top=0.985,
        )
        palette = None
        owner = self.window()
        if hasattr(owner, "_palette"):
            palette = owner._palette()
        if palette is None:
            palette = {"plot_card_bg": "#121a20", "plot_bg": "#11181e", "plot_text": "#d7e2ea", "plot_spine": "#445764", "plot_grid": "#2a3943"}
        self.image_widget.fig.patch.set_facecolor(palette["plot_card_bg"])
        self.image_axes.set_facecolor(palette["plot_bg"])
        self.image_axes.tick_params(colors=palette["plot_text"], which="both", labelsize=9)
        self.image_axes.tick_params(axis="x", pad=1)
        self.image_axes.xaxis.labelpad = 0
        self.image_axes.xaxis.label.set_color(palette["plot_text"])
        self.image_axes.yaxis.label.set_color(palette["plot_text"])
        for spine in self.image_axes.spines.values():
            spine.set_edgecolor(palette["plot_spine"])
        self.image_axes.grid(alpha=0.75, linestyle="--", color=palette["plot_grid"])

    def _show_roi_info(self) -> None:
        item = self.screen_list.currentItem()
        if item is None:
            return
        screen = self._screen_id(item)
        if self.roi_dialog is None:
            self._ensure_roi_control(screen)
            self.roi_dialog = QDialog(self)
            self.roi_dialog.setAttribute(Qt.WA_DeleteOnClose, False)
            self.roi_dialog.setMinimumWidth(340)
            self.roi_dialog.setStyleSheet(self.window().styleSheet())
            self.roi_dialog_layout = QVBoxLayout(self.roi_dialog)
            self.roi_dialog_layout.setContentsMargins(10, 10, 10, 10)
            self.roi_dialog_layout.setSpacing(6)
            self.roi_dialog_layout.addWidget(self.roi_control)
            close_button = QPushButton("Close", self.roi_dialog)
            close_button.setProperty("compact", True)
            close_button.clicked.connect(self.roi_dialog.hide)
            self.roi_dialog_layout.addWidget(close_button)
            self.roi_dialog_close_button = close_button
        else:
            self._ensure_roi_control(screen)
            if self.roi_control.parent() is not self.roi_dialog:
                self.roi_control.setParent(self.roi_dialog)
        self.roi_dialog.setWindowTitle(f"Software ROI - {screen}")
        self.roi_control.show()
        self.roi_dialog.adjustSize()
        self.roi_dialog.show()
        self.roi_dialog.raise_()
        self.roi_dialog.activateWindow()

    def _show_background_info(self) -> None:
        item = self.screen_list.currentItem()
        if item is None:
            return
        screen = self._screen_id(item)
        paths = resolve_beam_background_paths(self.app_context, screen)
        geometry = resolve_element_image_geometry(self.app_context, screen, self.app_context.control_backend.name)
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Beam Background - {screen}")
        layout = QVBoxLayout(dialog)
        load_button = QPushButton("Load saved background", dialog)
        load_button.clicked.connect(lambda: self._load_background(screen, geometry, paths))
        layout.addWidget(QLabel(f"Expected reference: {paths['background_image_path']}"))
        layout.addWidget(load_button)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, parent=dialog)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec_()

    def _ensure_roi_control(self, screen: str) -> None:
        geometry = resolve_element_image_geometry(self.app_context, screen, self.app_context.control_backend.name)
        paths = resolve_app_runtime_paths(Path(__file__).resolve().parent, self.app_context)
        roi_path = paths["latest_dir"] / "roi" / f"{screen}.json"
        if self.roi_control is None:
            self.roi_control = ROIControl(
                image_shape=(geometry.shape[1], geometry.shape[0]),
                runtime_path=roi_path,
                configured=geometry.default_roi,
            )
            self.roi_control.roiChanged.connect(lambda *_args: self._roi_changed())
        elif self._roi_screen != screen:
            self.roi_control.reconfigure(
                image_shape=(geometry.shape[1], geometry.shape[0]),
                runtime_path=roi_path,
                configured=geometry.default_roi,
            )
        self._roi_screen = screen

    def _roi_changed(self) -> None:
        if self.roi_control is None:
            return
        self.roi_status = (
            f"{self.roi_control.roi().width} x {self.roi_control.roi().height} px"
            if self.roi_control.use_roi.isChecked() else "Off"
        )
        self.roi_status_label.setText(f"ROI: {self.roi_status}")
        if self._last_frame is not None and self._last_fit is not None:
            self._redraw_image()

    def _load_background(self, screen, geometry, paths) -> None:
        try:
            image, metadata = load_background(
                paths["background_image_path"],
                paths["background_metadata_path"],
                expected_shape=(geometry.shape[1], geometry.shape[0]),
            )
        except (BackgroundStoreError, OSError, ValueError) as exc:
            self.background_status = f"Error: {exc}"
            self.beam_background_status_label.setText(self.background_status)
            return
        self._background_image = np.asarray(image, dtype=float)
        self._background_screen = screen
        self._background_metadata = metadata
        self.background_status = f"{screen} loaded"
        self.beam_background_status_label.setText(self.background_status)

    def _show_display_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Image Display")
        layout = QVBoxLayout(dialog)
        form = QGridLayout()
        method = QComboBox(dialog)
        method.addItem("Gaussian fit", "Gaussian fit")
        method.addItem("Projection RMS", "RMS moments")
        method.setCurrentIndex(max(0, method.findData(self.beam_width_method)))
        method.setToolTip(
            "Width used for preview and samples. Projection RMS is sensitive to background "
            "and ROI clipping. Start a new measurement to change method after sampling."
        )
        method.setEnabled(not self._archive_review and not (
            self.session is not None and self.session.acquisition.samples
        ))
        method.currentIndexChanged.connect(lambda _index: self._set_beam_width_method(method.currentData()))
        form.addWidget(QLabel("Beam width"), 2, 0)
        form.addWidget(method, 2, 1)
        form.addWidget(QLabel("Colormap"), 0, 0)
        cmap = QComboBox(dialog)
        cmap.addItems(BEAM_IMAGE_COLORMAPS)
        cmap.setCurrentText(self.beam_image_colormap)
        cmap.currentTextChanged.connect(self._set_image_colormap)
        form.addWidget(cmap, 0, 1)
        log = QCheckBox("Logarithmic intensity", dialog)
        log.setChecked(self.beam_image_logarithmic)
        log.toggled.connect(self._set_image_logarithmic)
        form.addWidget(log, 1, 0, 1, 2)
        layout.addLayout(form)
        close = QDialogButtonBox(QDialogButtonBox.Close, parent=dialog)
        close.rejected.connect(dialog.reject)
        close.accepted.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec_()

    def _set_beam_width_method(self, value: str) -> None:
        if value not in {"Gaussian fit", "RMS moments"}:
            raise ValueError(f"Unsupported beam width method: {value}")
        if self._archive_review or (self.session is not None and self.session.acquisition.samples):
            return
        self.beam_width_method = value
        if self.session is not None:
            self.session = replace(self.session, beam_width_method=value)
            self.preview_sample()
        else:
            self._clear_image_display()

    def _set_image_colormap(self, value: str) -> None:
        self.beam_image_colormap = value
        self._redraw_image()

    def _set_image_logarithmic(self, value: bool) -> None:
        self.beam_image_logarithmic = bool(value)
        self._redraw_image()

    def _set_image_overlays(self, value: bool) -> None:
        self.beam_image_overlays = bool(value)
        self._redraw_image()

    def _redraw_image(self) -> None:
        if self._last_frame is None or self._last_fit is None:
            return
        self._display_frame(
            self._last_frame_screen,
            self._last_frame,
            self._last_fit,
            self._last_frame_extent,
            None,
            None,
        )

    def _update_selected_optics(self, screen: str | None = None) -> None:
        if screen is None:
            item = self.screen_list.currentItem()
            screen = self._screen_id(item) if item is not None else None
        if self.session is None or not screen or screen not in self.session.optics.observation_elements:
            self.selected_optics_label.setText("--")
            return
        index = self.session.optics.observation_elements.index(screen)
        r11, r12 = self.session.optics.x_projections[index]
        r33, r34 = self.session.optics.y_projections[index]
        self.selected_optics_label.setText(
            f"{screen}: X (R11={r11:.6g}, R12={r12:.6g} m); "
            f"Y (R33={r33:.6g}, R34={r34:.6g} m)"
        )

    @staticmethod
    def _fit_quality(fit, pv_sigx, pv_sigy) -> dict[str, object]:
        x_quality = assess_projection_quality(fit.x_projection)
        y_quality = assess_projection_quality(fit.y_projection)
        return {
            "fit_status": fit.status,
            "beam_width_method": getattr(fit, "method", None),
            "fit_message": fit.message,
            "x_status": x_quality["status"],
            "y_status": y_quality["status"],
            "x_residual_rms": fit.x_projection.residual_rms,
            "y_residual_rms": fit.y_projection.residual_rms,
            "pv_sigx_mm": pv_sigx,
            "pv_sigy_mm": pv_sigy,
        }

    def _accept_sample(self, screen: str, sigma_x_m: float | None, sigma_y_m: float | None, source: str, quality=None, *, enabled=True) -> None:
        if self.session is None:
            return
        try:
            if enabled and self.session.acquisition.sample_counts.get(screen, 0) >= self.session.acquisition.target_samples_per_screen:
                raise ValueError(
                    f"{screen} already has the target {self.session.acquisition.target_samples_per_screen} sample(s)"
                )
            self.session = self.session.add_sample(
                BeamSizeSample(
                    screen,
                    sigma_x_m,
                    sigma_y_m,
                    datetime.now(timezone.utc).timestamp(),
                    source,
                    quality=quality,
                    enabled=enabled,
                )
            )
            self._samples_changed()
            if not enabled:
                self._set_state("Acquiring", f"{screen}: rejected sample recorded — {(quality or {}).get('fit_status', '')} "
                                f"{(quality or {}).get('x_status', '')}/{(quality or {}).get('y_status', '')}. "
                                f"{(quality or {}).get('fit_message', '')} "
                                "Check the image, ROI and background, then retry Acquire.")
        except Exception as exc:
            self._set_state("Ready", str(exc))

    def _refresh_samples_table(self) -> None:
        samples = self.session.acquisition.samples if self.session else ()
        self.samples_table.blockSignals(True)
        self.samples_table.setRowCount(len(samples))
        numbers = {}
        for row, sample in enumerate(samples):
            numbers[sample.screen] = numbers.get(sample.screen, 0) + 1
            quality = sample.quality or {}
            status = "/".join(str(quality.get(f"{plane}_status", "--")) for plane in ("x", "y"))
            if quality.get("rejected"):
                status = "Rejected · " + status
            values = ("", sample.screen, str(numbers[sample.screen]),
                      "--" if sample.sigma_x_m is None else f"{sample.sigma_x_m * 1000:.6g}",
                      "--" if sample.sigma_y_m is None else f"{sample.sigma_y_m * 1000:.6g}", status)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip("\n".join(f"{key}: {value}" for key, value in quality.items()))
                if column == 0:
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    item.setCheckState(Qt.Checked if sample.enabled else Qt.Unchecked)
                    if sample.sigma_x_m is None or sample.sigma_y_m is None:
                        item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
                        item.setToolTip("No valid fitted sizes; this sample cannot be used.")
                self.samples_table.setItem(row, column, item)
        self.samples_table.blockSignals(False)
        active = sum(sample.enabled for sample in samples)
        self.samples_summary_label.setText(f"{active} active / {len(samples)} recorded · Gray points are excluded")
        self._refresh_screen_counts()
        self._draw_samples()

    def _samples_changed(self):
        self.reconstruction = None
        self._reset_result_metrics()
        self.results_label.setText("Samples changed · Recalculate to update results")
        self._refresh_samples_table()
        acquisition = self.session.acquisition
        missing = ", ".join(f"{screen} {count}/{acquisition.target_samples_per_screen}"
                            for screen, count in acquisition.sample_counts.items()
                            if count < acquisition.target_samples_per_screen)
        self._set_state("Archived" if self._archive_review else "Fit Ready" if acquisition.complete else "Acquiring",
                        (f"Below sampling target: {missing} · Recalculate available"
                         if acquisition.can_reconstruct else f"Need samples: {missing}")
                        if missing else "Ready to reconstruct")

    def _sample_use_changed(self, item):
        if item.column() != 0 or self.session is None:
            return
        self.session = replace(self.session, acquisition=self.session.acquisition.set_sample_enabled(
            item.row(), item.checkState() == Qt.Checked))
        self._samples_changed()

    def _exclude_selected_samples(self):
        if self.session is None:
            return
        acquisition = self.session.acquisition
        for row in {index.row() for index in self.samples_table.selectionModel().selectedRows()}:
            acquisition = acquisition.set_sample_enabled(row, False)
        self.session = replace(self.session, acquisition=acquisition)
        self._samples_changed()

    def _restore_samples(self):
        if self.session is None:
            return
        acquisition = self.session.acquisition
        for row, sample in enumerate(acquisition.samples):
            if sample.sigma_x_m is not None and sample.sigma_y_m is not None:
                acquisition = acquisition.set_sample_enabled(row, True)
        self.session = replace(self.session, acquisition=acquisition)
        self._samples_changed()

    def _sample_picked(self, event):
        rows = getattr(event.artist, "_sample_rows", ())
        if rows and len(event.ind):
            row = rows[event.ind[0]]
            self.samples_table.selectRow(row)
            self.samples_table.scrollToItem(self.samples_table.item(row, 0))

    def _draw_samples(self):
        samples = self.session.acquisition.samples if self.session else ()
        screens = self.session.acquisition.observation_elements if self.session else ()
        selected = {index.row() for index in self.samples_table.selectionModel().selectedRows()}
        palette = self.window()._palette() if hasattr(self.window(), "_palette") else {
            "plot_card_bg": "#121a20", "plot_bg": "#11181e", "plot_text": "#d7e2ea", "plot_spine": "#445764", "plot_grid": "#2a3943"}
        self.samples_plot.fig.patch.set_facecolor(palette["plot_card_bg"])
        for axis, field, color, label in zip(self.sample_axes, ("sigma_x_m", "sigma_y_m"),
                                              ("#40c9bd", "#f2b35d"), ("σx (mm)", "σy (mm)")):
            axis.clear()
            axis.set_facecolor(palette["plot_bg"])
            axis.tick_params(colors=palette["plot_text"], labelsize=9)
            axis.set_ylabel(label, color=palette["plot_text"])
            for spine in axis.spines.values():
                spine.set_color(palette["plot_spine"])
            axis.grid(alpha=0.5, color=palette["plot_grid"])
            for screen_index, screen in enumerate(screens):
                rows = [i for i, sample in enumerate(samples) if sample.screen == screen]
                offsets = np.linspace(-0.18, 0.18, len(rows)) if len(rows) > 1 else [0]
                for row, offset in zip(rows, offsets):
                    sample = samples[row]
                    value = getattr(sample, field)
                    if value is None:
                        continue
                    artist = axis.scatter([screen_index + offset], [value * 1000],
                        c=[color if sample.enabled else "#87929b"], marker="o" if sample.enabled else "x",
                        s=65 if row in selected else 28, picker=6, zorder=3)
                    artist._sample_rows = [row]
                    if row in selected:
                        axis.scatter([screen_index + offset], [value * 1000], s=120,
                                     facecolors="none", edgecolors=palette["plot_text"], zorder=4)
                values = [getattr(samples[row], field) * 1000 for row in rows if samples[row].enabled]
                if values:
                    error = np.std(values, ddof=1) / np.sqrt(len(values)) if len(values) > 1 else 0
                    axis.errorbar(screen_index, np.mean(values), yerr=error, fmt="_", markersize=18,
                                  color=palette["plot_text"], capsize=5, zorder=5)
            axis.set_xticks(range(len(screens)), screens)
            axis.set_xlim(-0.5, max(len(screens) - 0.5, 0.5))
        self.sample_axes[0].set_title("Individual samples · Mean ± SE", color=palette["plot_text"], fontsize=10)
        self.samples_plot.canvas.draw_idle()

    def reconstruct(self) -> None:
        if self.session is None or not self.session.acquisition.can_reconstruct:
            return
        try:
            result = reconstruct_multi_screen_measurement(
                self.session.optics,
                self.session.acquisition.aggregate(require_target=False),
            )
            self.reconstruction = result
            self._update_result_metrics(result)
            self.results_label.setText(
                "X and Y reconstructed" if result.valid else f"Reconstruction {result.status}"
            )
            state = {
                "valid": "Complete",
                "partial": "Partial",
                "invalid": "Invalid",
            }[result.status]
            archive_paths = None if self._archive_review else self._write_runtime_archives()
            detail = "reconstruction updated"
            if archive_paths:
                detail += f"; auto-saved {archive_paths[0].parent.name}"
            self._set_state("Archived" if self._archive_review else state, detail)
        except Exception as exc:
            self._set_state("Invalid", str(exc))

    def _reset_result_metrics(self) -> None:
        for values in getattr(self, "result_value_labels", {}).values():
            for label in values:
                label.setText("--")

    def _update_result_metrics(self, result) -> None:
        for plane_name, plane in (("X", result.x), ("Y", result.y)):
            values = self.result_value_labels[plane_name]
            if not plane.valid or plane.geometric_emittance_m_rad is None:
                values[0].setText(plane.status)
                for label in values[1:]:
                    label.setText("--")
                continue
            geometric = plane.geometric_emittance_m_rad * 1.0e6
            normalized = normalized_emittance(plane.geometric_emittance_m_rad, self.energy_spin.value()) * 1.0e6
            values[0].setText(f"{geometric:.4g}")
            values[1].setText(f"{normalized:.4g}")
            values[2].setText("--" if plane.beta_m is None else f"{plane.beta_m:.4g}")
            values[3].setText("--" if plane.alpha is None else f"{plane.alpha:.4g}")

    def _format_results(self, result) -> str:
        lines = []
        if self.session is not None:
            try:
                data = self.session.acquisition.aggregate()
                if data.rms_x_errors_m is None or data.rms_y_errors_m is None:
                    lines.append("Fit weighting: unweighted (one or more size SE values are zero/unavailable)")
            except ValueError:
                pass
        for name, plane in (("X", result.x), ("Y", result.y)):
            if not plane.valid:
                lines.append(f"{name}: {plane.status} ({plane.message})")
                continue
            emit = plane.geometric_emittance_m_rad
            normalized = None
            if emit is not None and self.energy_spin.value() > 0:
                normalized = normalized_emittance(emit, self.energy_spin.value())
            lines.append(
                f"{name}: ε={emit:.4g} m·rad, εn={normalized:.4g} m·rad, "
                f"β={plane.beta_m:.4g} m, α={plane.alpha:.4g}, "
                f"cond={plane.condition_number:.3g}, dof={plane.degrees_of_freedom}"
            )
        return "\n".join(lines)

    def save_archive(self) -> None:
        if self.session is None:
            return
        paths = resolve_app_runtime_paths(Path(__file__).resolve().parent, self.app_context)
        paths["runs_dir"].mkdir(parents=True, exist_ok=True)
        default_path = paths["runs_dir"] / "multi_screen_measurement.json"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Multi-Screen Archive",
            str(default_path),
            "JSON (*.json)",
        )
        if not path:
            return
        try:
            save_multi_screen_archive(path, self.session, reconstruction=self.reconstruction)
            self.status_label.setText(f"Saved: {Path(path).name}")
        except Exception as exc:
            self._set_state("Ready", str(exc))

    def _write_runtime_archives(self):
        if self.session is None:
            return None
        paths = resolve_app_runtime_paths(Path(__file__).resolve().parent, self.app_context)
        run_dir = paths["runs_dir"] / make_runtime_run_id("multi_screen")
        run_path = save_multi_screen_archive(
            run_dir / "measurement.json",
            self.session,
            reconstruction=self.reconstruction,
        )
        latest_path = save_multi_screen_archive(
            paths["latest_dir"] / "multi_screen_measurement.json",
            self.session,
            reconstruction=self.reconstruction,
        )
        return run_path, latest_path

    def load_archive(self) -> None:
        paths = resolve_app_runtime_paths(Path(__file__).resolve().parent, self.app_context)
        paths["runs_dir"].mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Multi-Screen Archive",
            str(paths["runs_dir"]),
            "JSON (*.json)",
        )
        if not path:
            return
        try:
            archive_path = Path(path)
            self.session = load_multi_screen_archive(archive_path)
            self._archive_review = True
            self._reset_result_metrics()
            archive_payload = json.loads(archive_path.read_text(encoding="utf-8"))
            self.reconstruction = reconstruction_from_archive_payload(
                archive_payload.get("reconstruction")
                if isinstance(archive_payload, Mapping)
                else None
            )
            self._load_session_controls()
            self.diagnostic_label.setText(
                f"Optics: archived transfer matrices · Reference {self.session.optics.reference_element}"
            )
            self._refresh_samples_table()
            if self.reconstruction is not None:
                self._update_result_metrics(self.reconstruction)
                self.results_label.setText("Archived reconstruction restored")
            else:
                self.results_label.setText("No reconstruction in archive")
            self._clear_image_display("Archive loaded; select a screen to review archived samples")
            self._update_selected_optics()
            self.review_tabs.setCurrentIndex(1)
            self._set_state("Archived", f"{Path(path).name} · offline review")
        except Exception as exc:
            self._set_state("Invalid", str(exc))

    def _load_session_controls(self) -> None:
        if self.session is None:
            return
        self.beam_width_method = self.session.beam_width_method
        self._configuration_guard = True
        try:
            self.model_line_edit.setCurrentText(self.session.model_line)
            self.reference_combo.clear()
            self.reference_combo.addItems(self.session.optics.observation_elements)
            self.reference_combo.setCurrentText(self.session.optics.reference_element)
            self.screen_list.clear()
            self.screen_list.addItems(self.session.optics.observation_elements)
            if self.screen_list.count():
                self.screen_list.setCurrentRow(0)
            self.samples_spin.setValue(self.session.acquisition.target_samples_per_screen)
            if self.session.energy_mev is not None:
                self.energy_spin.setValue(self.session.energy_mev)
        finally:
            self._configuration_guard = False

    def stop(self) -> None:
        """Reserved for future acquisition workers; currently all reads are bounded."""
        self._auto_refresh_timer.stop()


__all__ = ["MultiScreenWorkspace"]
