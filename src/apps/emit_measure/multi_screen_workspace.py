"""PyQt workspace for read-only multi-screen emittance measurements.

The widget deliberately keeps machine actions out of this first integration:
model preparation is read-only, and acquisition only reads configured image PVs.
Manual samples make the workflow useful for archived/offline VM validation too.
"""

from __future__ import annotations

from datetime import datetime, timezone
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
        self._last_frame = None
        self._last_fit = None
        self._last_frame_extent = None
        self._last_frame_screen = None
        self.beam_image_colormap = DEFAULT_BEAM_IMAGE_COLORMAP
        self.beam_image_logarithmic = False
        self.beam_image_overlays = True
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
        self.manual_button = QPushButton("Add Manual Sample", self)
        self.reconstruct_button = QPushButton("Reconstruct", self)
        self.save_button = QPushButton("Save As...", self)
        self.load_button = QPushButton("Load Archive", self)
        self.prepare_button.setText("Prepare")
        self.new_button.setText("Clear")
        self.acquire_button.setText("Acquire Sample")
        self.manual_button.setText("Manual Sample")
        self.reconstruct_button.setText("Reconstruct")
        self.prepare_button.setToolTip("Prepare the optics model and start a new measurement session.")
        self.new_button.setToolTip("Clear the current samples and reconstruction result.")
        self.acquire_button.setToolTip("Read the selected screen image, fit it locally, and accept the sample.")
        self.manual_button.setToolTip("Enter beam sizes manually and add a sample.")
        self.reconstruct_button.setToolTip("Reconstruct the transverse beam matrix from accepted samples.")
        def action_group(title, buttons, stretch=0):
            group = QVBoxLayout()
            group.setSpacing(4)
            group_title = QLabel(title, actions_card)
            group_title.setProperty("role", "sectionTitle")
            group.addWidget(group_title)
            row = QHBoxLayout()
            row.setSpacing(6)
            for button in buttons:
                row.addWidget(button)
            group.addLayout(row)
            controls.addLayout(group, stretch)

        self.prepare_button.setProperty("role", "primary")
        self.acquire_button.setProperty("role", "primary")
        for button in (
            self.prepare_button,
            self.new_button,
            self.acquire_button,
            self.manual_button,
            self.reconstruct_button,
            self.save_button,
            self.load_button,
        ):
            button.setProperty("compact", True)
        action_group("Session", (self.prepare_button,), stretch=1)
        action_group(
            "Samples",
            (
                self.acquire_button,
                self.manual_button,
                self.reconstruct_button,
                self.new_button,
            ),
            stretch=3,
        )
        self.prepare_button.clicked.connect(self.prepare_measurement)
        self.new_button.clicked.connect(self.new_measurement)
        self.acquire_button.clicked.connect(self.acquire_sample)
        self.preview_button.clicked.connect(self.preview_sample)
        self.manual_button.clicked.connect(self.add_manual_sample)
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
        right_column.addWidget(image_box, 1)

        samples_card = QFrame(self)
        samples_card.setObjectName("plotCard")
        samples_layout = QVBoxLayout(samples_card)
        samples_layout.setContentsMargins(10, 10, 10, 10)
        samples_layout.setSpacing(8)
        samples_title = QLabel("Accepted Samples", samples_card)
        samples_title.setObjectName("panelTitle")
        samples_layout.addWidget(samples_title)
        self.samples_table = QTableWidget(0, 8, samples_card)
        self.samples_table.setHorizontalHeaderLabels(
            ("Screen", "N", "σx (mm)", "σy (mm)", "σx SE", "σy SE", "Source", "Quality")
        )
        self.samples_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.samples_table.setFixedHeight(120)
        self.samples_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        samples_layout.addWidget(self.samples_table)
        archive_row = QHBoxLayout()
        archive_row.setSpacing(6)
        archive_row.addStretch(1)
        archive_row.addWidget(self.save_button)
        archive_row.addWidget(self.load_button)
        samples_layout.addLayout(archive_row)
        left_column.addWidget(samples_card)

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

    def _screen_changed(self, *_args) -> None:
        self._update_button_state()
        item = self.screen_list.currentItem()
        if item is None:
            self._clear_image_display()
            return
        screen = item.text()
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
        selected = {self.screen_list.item(index).text() for index in range(self.screen_list.count())}
        self.screen_candidate_combo.clear()
        try:
            candidates = [element.id for element in self.app_context.profile.elements if element.kind == "flag"]
        except Exception:
            candidates = []
        self.screen_candidate_combo.addItems([item for item in candidates if item not in selected])

    def _add_screen(self) -> None:
        screen = self.screen_candidate_combo.currentText().strip()
        if screen and screen not in [self.screen_list.item(i).text() for i in range(self.screen_list.count())]:
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
        self.status_label.setText(f"{state}: {message}" if message else state)
        self.status_changed.emit(state, message)
        self._update_button_state()

    def _configuration_changed(self, *_args) -> None:
        if self._configuration_guard or self.session is None:
            return
        if self.state not in {"Ready", "Acquiring", "Fit Ready", "Complete", "Partial"}:
            return
        self.new_measurement()
        self._set_state("Configuring", "Configuration changed; prepare a new measurement")

    def _update_button_state(self, *_args) -> None:
        prepared = self.session is not None and self.state not in {"Invalid", "Configuring"}
        writable = prepared and self.state != "Archived"
        self.acquire_button.setEnabled(writable and not self.session.acquisition.complete)
        self.preview_button.setEnabled(writable)
        self.manual_button.setEnabled(writable and not self.session.acquisition.complete)
        self.reconstruct_button.setEnabled(
            writable and self.session.acquisition.complete
        )
        self.save_button.setEnabled(self.session is not None)
        self.add_screen_button.setEnabled(self.state != "Archived")
        self.remove_screen_button.setEnabled(
            self.state != "Archived" and self.screen_list.count() > 3
        )
        self._update_auto_refresh()

    def _update_auto_refresh(self, *_args) -> None:
        active = (
            self.auto_refresh_checkbox.isChecked()
            and self.session is not None
            and self.state in {"Ready", "Acquiring", "Fit Ready", "Complete", "Partial"}
        )
        if active and not self._auto_refresh_timer.isActive():
            self._auto_refresh_timer.start()
        elif not active and self._auto_refresh_timer.isActive():
            self._auto_refresh_timer.stop()

    def _auto_refresh_current_image(self) -> None:
        if self.session is None or self.state == "Archived":
            return
        self.preview_sample(auto=True)

    def new_measurement(self) -> None:
        self.session = None
        self.reconstruction = None
        self.samples_table.setRowCount(0)
        self.diagnostic_label.setText("Optics: no model prepared")
        self.results_label.setText("No reconstruction")
        self._reset_result_metrics()
        self.image_fit_label.setText("--")
        self.pv_cross_check_label.setText("--")
        self._clear_image_display()
        self._set_state("Configuring")

    def prepare_measurement(self) -> None:
        screens = tuple(self.screen_list.item(index).text() for index in range(self.screen_list.count()))
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
            if observability.status in {"invalid", "poor"}:
                self._set_state("Invalid", observability.message)
            else:
                self._set_state("Ready", observability.message)
            self._update_selected_optics()
        except Exception as exc:
            self.session = None
            self._set_state("Invalid", str(exc))

    def add_manual_sample(self) -> None:
        if self.session is None:
            return
        screen = self.screen_list.currentItem().text() if self.screen_list.currentItem() else self.session.acquisition.next_screen
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
        if self.session is None:
            return
        item = self.screen_list.currentItem()
        screen = item.text() if item is not None else self.session.acquisition.next_screen
        if screen is None:
            return
        try:
            payload = self._read_image_payload(screen)
            fit = payload["fit"]
            self._display_payload(screen, payload)
            if not fit.valid or fit.sigx_mm is None or fit.sigy_mm is None:
                raise RuntimeError(f"image fit {fit.status}: {fit.message}")
        except Exception as exc:
            self._set_state("Ready", f"{screen}: {exc}; use manual sample if appropriate")
            return
        quality = self._fit_quality(payload["fit"], payload.get("pv_sigx"), payload.get("pv_sigy"))
        if quality.get("x_status") in {"clipped", "underresolved", "poor_fit"} or quality.get("y_status") in {"clipped", "underresolved", "poor_fit"}:
            self._set_state("Ready", f"{screen}: image quality rejected ({quality.get('x_status')}/{quality.get('y_status')})")
            return
        self._accept_sample(
            screen,
            fit.sigx_mm / 1000.0,
            fit.sigy_mm / 1000.0,
            "image",
            quality=quality,
        )

    def preview_sample(self, *, auto: bool = False) -> None:
        item = self.screen_list.currentItem()
        screen = item.text() if item is not None else None
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
                        np.asarray(image, dtype=float), extent=extent
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
            f"Fit: Gaussian · {'valid' if fit.valid else fit.status}"
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
        screen = item.text()
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
        screen = item.text()
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
        overlays = QCheckBox("Show fit and projection overlays", dialog)
        overlays.setChecked(self.beam_image_overlays)
        overlays.toggled.connect(self._set_image_overlays)
        form.addWidget(overlays, 2, 0, 1, 2)
        layout.addLayout(form)
        close = QDialogButtonBox(QDialogButtonBox.Close, parent=dialog)
        close.rejected.connect(dialog.reject)
        close.accepted.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec_()

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
            screen = item.text() if item is not None else None
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
            "fit_message": fit.message,
            "x_status": x_quality["status"],
            "y_status": y_quality["status"],
            "x_residual_rms": fit.x_projection.residual_rms,
            "y_residual_rms": fit.y_projection.residual_rms,
            "pv_sigx_mm": pv_sigx,
            "pv_sigy_mm": pv_sigy,
        }

    def _accept_sample(self, screen: str, sigma_x_m: float, sigma_y_m: float, source: str, quality=None) -> None:
        if self.session is None:
            return
        try:
            if self.session.acquisition.sample_counts.get(screen, 0) >= self.session.acquisition.target_samples_per_screen:
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
                )
            )
            self._refresh_samples_table()
            self._set_state("Fit Ready" if self.session.acquisition.complete else "Acquiring", f"{source} sample accepted")
        except Exception as exc:
            self._set_state("Ready", str(exc))

    def _refresh_samples_table(self) -> None:
        if self.session is None:
            return
        screens = self.session.acquisition.observation_elements
        self.samples_table.setRowCount(len(screens))
        for row, screen in enumerate(screens):
            samples = [sample for sample in self.session.acquisition.samples if sample.screen == screen]
            x_values = np.asarray([sample.sigma_x_m for sample in samples], dtype=float)
            y_values = np.asarray([sample.sigma_y_m for sample in samples], dtype=float)
            x_error = float(np.std(x_values, ddof=1) / np.sqrt(x_values.size)) if x_values.size > 1 else None
            y_error = float(np.std(y_values, ddof=1) / np.sqrt(y_values.size)) if y_values.size > 1 else None
            values = (
                screen,
                str(len(samples)),
                "--" if not samples else f"{float(np.mean(x_values)) * 1000:.6g}",
                "--" if not samples else f"{float(np.mean(y_values)) * 1000:.6g}",
                "--" if x_error is None else f"{x_error * 1000:.3g}",
                "--" if y_error is None else f"{y_error * 1000:.3g}",
                self._screen_source(screen),
                self._screen_quality(screen),
            )
            for column, value in enumerate(values):
                self.samples_table.setItem(row, column, QTableWidgetItem(value))

    def _screen_source(self, screen: str) -> str:
        if self.session is None:
            return "--"
        sources = [sample.source for sample in self.session.acquisition.samples if sample.screen == screen]
        return sources[-1] if sources else "--"

    def _screen_quality(self, screen: str) -> str:
        if self.session is None:
            return "--"
        samples = [sample for sample in self.session.acquisition.samples if sample.screen == screen]
        if not samples:
            return "--"
        quality = samples[-1].quality or {}
        x_status = quality.get("x_status")
        y_status = quality.get("y_status")
        if x_status and y_status:
            return str(x_status) if x_status == y_status else f"{x_status}/{y_status}"
        return str(quality.get("fit_status", "accepted"))

    def reconstruct(self) -> None:
        if self.session is None or not self.session.acquisition.complete:
            return
        try:
            result = reconstruct_multi_screen_measurement(
                self.session.optics,
                self.session.acquisition.aggregate(),
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
            archive_paths = self._write_runtime_archives()
            detail = "reconstruction updated"
            if archive_paths:
                detail += f"; auto-saved {archive_paths[0].parent.name}"
            self._set_state(state, detail)
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
        path, _ = QFileDialog.getSaveFileName(self, "Save Multi-Screen Archive", "multi_screen_measurement.json", "JSON (*.json)")
        if not path:
            return
        try:
            save_multi_screen_archive(path, self.session, reconstruction=self.reconstruction)
            self._set_state("Archived", Path(path).name)
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
        path, _ = QFileDialog.getOpenFileName(self, "Load Multi-Screen Archive", "", "JSON (*.json)")
        if not path:
            return
        try:
            archive_path = Path(path)
            self.session = load_multi_screen_archive(archive_path)
            archive_payload = json.loads(archive_path.read_text(encoding="utf-8"))
            self.reconstruction = reconstruction_from_archive_payload(
                archive_payload.get("reconstruction")
                if isinstance(archive_payload, Mapping)
                else None
            )
            self._load_session_controls()
            self._refresh_samples_table()
            if self.reconstruction is not None:
                self._update_result_metrics(self.reconstruction)
                self.results_label.setText("Archived reconstruction restored")
            else:
                self.results_label.setText("No reconstruction in archive")
            self._clear_image_display("Archive loaded; select a screen to review archived samples")
            self._update_selected_optics()
            self._set_state("Archived", f"{Path(path).name} · read-only")
        except Exception as exc:
            self._set_state("Invalid", str(exc))

    def _load_session_controls(self) -> None:
        if self.session is None:
            return
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
