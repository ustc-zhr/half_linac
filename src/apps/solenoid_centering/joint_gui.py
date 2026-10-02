"""Operator window for the coupled centering workflow."""
from dataclasses import replace

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
    QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QAbstractItemView, QHeaderView,
    QFrame, QSplitter, QWidget, QTabWidget, QProgressBar, QScrollArea,
    QMenu, QToolButton, QStackedWidget,
)

from half_linac.src.apps.solenoid_centering.joint import JointScanner, JointTarget, load_joint_plans
from half_linac.src.apps.solenoid_centering.scan import StopRequested
from half_linac.src.apps.solenoid_centering.gui.theme import build_stylesheet, theme_palette
from half_linac.src.shared.app_theme import resolve_initial_theme
from half_linac.src.shared.machine_profile import list_elements


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
    busy_changed = pyqtSignal(bool)
    status_changed = pyqtSignal()

    def __init__(self, context, parent=None, *, embedded=False):
        super().__init__(parent)
        self.context = context
        self.plans = load_joint_plans(context)
        self.worker = None
        self.scanner = None
        self.result = None
        self.ready = False
        self._loading = False
        self._custom = False
        workflow = context.solenoid_centering_workflow
        self.available_presets = tuple(p for p in workflow.presets if p.solenoid and p.motion_verification)
        backend = context.control_backend.name
        x_bpms = {e.id for e in list_elements(context, kind="bpm", logical_channel="x", control_backend=backend)}
        y_bpms = {e.id for e in list_elements(context, kind="bpm", logical_channel="y", control_backend=backend)}
        self.available_bpms = tuple(e.id for e in list_elements(context, kind="bpm")
                                    if e.id in x_bpms and e.id in y_bpms)
        self.available_correctors = tuple(dict.fromkeys(
            c for p in self.available_presets for c in (p.hcorr, p.vcorr)))
        self.setWindowTitle(f"Joint Solenoid Centering — {context.machine.display_name} / {context.control_backend.name}")
        self.operation = ""
        if not embedded:
            self.resize(1180, 800)
            self.setMinimumSize(960, 680)
        self.palette_colors = theme_palette(getattr(parent, "current_theme", resolve_initial_theme()))
        self.setStyleSheet(self._stylesheet())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self.state_label = QLabel("NOT CHECKED", self)
        self.state_label.setProperty("role", "statusValue")
        if embedded:
            self.state_label.hide()
        else:
            header = QHBoxLayout()
            title = QLabel("Joint Solenoid Centering")
            title.setObjectName("summaryTitle")
            header.addWidget(title)
            header.addStretch()
            context_label = QLabel(f"{context.machine.display_name}  /  {context.control_backend.name.upper()}")
            context_label.setProperty("muted", True)
            header.addWidget(context_label)
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
        config_layout.setSpacing(7)
        config_header = QHBoxLayout()
        heading = QLabel("Joint configuration")
        heading.setObjectName("panelTitle")
        config_header.addWidget(heading)
        config_header.addStretch()
        self.preflight_button = QPushButton("Check PVs")
        self.preflight_button.setProperty("compact", True)
        self.preflight_button.setAutoDefault(False)
        self.preflight_button.setToolTip("Check connections and current limits, then estimate scan time.")
        self.preflight_button.clicked.connect(self.preflight)
        config_header.addWidget(self.preflight_button)
        config_layout.addLayout(config_header)
        targets_card = QFrame()
        targets_card.setObjectName("configSectionCard")
        target_layout = QVBoxLayout(targets_card)
        target_layout.setContentsMargins(10, 8, 10, 10)
        target_layout.setSpacing(6)
        target_header = QHBoxLayout()
        target_heading = QLabel("Targets & correctors")
        target_heading.setObjectName("sectionTitle")
        target_header.addWidget(target_heading)
        target_header.addStretch()
        target_layout.addLayout(target_header)
        preset_row = QHBoxLayout()
        preset_row.setSpacing(10)
        self.preset_label = QLabel("Preset")
        self.preset_label.setProperty("role", "field")
        preset_row.addWidget(self.preset_label)
        self.group = QComboBox()
        self.group.setToolTip("Choose a starting preset for the joint scan.")
        for plan in self.plans:
            self.group.addItem(plan.display_name)
        preset_row.addWidget(self.group, 1)
        target_layout.addLayout(preset_row)
        self.template_notice = QWidget()
        template_row = QHBoxLayout(self.template_notice)
        template_row.setContentsMargins(0, 0, 0, 0)
        self.plan_source = QLabel("Custom plan")
        self.plan_source.setProperty("muted", True)
        template_row.addWidget(self.plan_source)
        template_row.addStretch()
        self.reset_button = QPushButton("Reset")
        self.reset_button.setProperty("compact", "true")
        self.reset_button.setToolTip("Restore the selected preset's targets, correctors and scan settings.")
        self.reset_button.clicked.connect(self._load_group)
        template_row.addWidget(self.reset_button)
        target_header.addWidget(self.template_notice)
        self.targets = self._table(["Solenoid", "BPMs", "±A", ""])
        self.targets.verticalHeader().setDefaultSectionSize(34)
        self.targets.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self.targets.horizontalHeader().resizeSection(2, 82)
        self.targets.horizontalHeader().setSectionResizeMode(3, QHeaderView.Fixed)
        self.targets.horizontalHeader().resizeSection(3, 32)
        target_layout.addWidget(self.targets)
        self.add_target_button = QPushButton("+ Add solenoid")
        self.add_target_button.setProperty("compact", "true")
        self.add_target_button.clicked.connect(self._add_target)
        target_layout.addWidget(self.add_target_button)
        corrector_heading = QLabel("Correctors")
        corrector_heading.setObjectName("sectionTitle")
        corrector_row = QHBoxLayout()
        corrector_row.addWidget(corrector_heading)
        corrector_row.addStretch()
        self.corrector_button = QToolButton()
        self.corrector_button.setObjectName("jointCorrectorButton")
        self.corrector_button.setText("Select correctors  ▾")
        self.corrector_button.setPopupMode(QToolButton.InstantPopup)
        self.corrector_button.setMenu(QMenu(self.corrector_button))
        for corrector in self.available_correctors:
            action = self.corrector_button.menu().addAction(corrector)
            action.setCheckable(True)
            action.toggled.connect(self._correctors_changed)
        corrector_row.addWidget(self.corrector_button)
        target_layout.addLayout(corrector_row)
        self.corrector_summary = QLabel()
        self.corrector_summary.setWordWrap(True)
        self.corrector_summary.setProperty("muted", True)
        target_layout.addWidget(self.corrector_summary)
        config_layout.addWidget(targets_card)

        settings_card = QFrame()
        settings_card.setObjectName("configSectionCard")
        settings_layout = QVBoxLayout(settings_card)
        settings_layout.setContentsMargins(10, 8, 10, 10)
        settings_layout.setSpacing(6)
        settings_header = QHBoxLayout()
        heading = QLabel("Scan settings")
        heading.setObjectName("sectionTitle")
        settings_header.addWidget(heading)
        settings_header.addStretch()
        self.advanced_button = QPushButton("Sampling && iterations  ▸")
        self.advanced_button.setCheckable(True)
        self.advanced_button.setProperty("compact", "true")
        settings_header.addWidget(self.advanced_button)
        settings_layout.addLayout(settings_header)
        form = QFormLayout()
        form.setVerticalSpacing(5)
        advanced_form = QFormLayout()
        advanced_form.setVerticalSpacing(8)
        self.probe = self._spin(0.001, 5)
        self.step = self._spin(0.001, 5)
        self.excursion = self._spin(0.001, 5)
        self.floor = self._spin(0.0001, 10, decimals=4)
        self.max_target_worsening = self._spin(0.1, 99.9, decimals=1)
        self.max_target_worsening.setSingleStep(1)
        self.max_target_worsening.setSuffix(" %")
        self.samples_per_point = QSpinBox()
        self.samples_per_point.setRange(1, 100)
        self.sample_interval = self._spin(0, 60)
        self.sample_interval.setSingleStep(0.1)
        self.settle_time = self._spin(0, 60)
        self.settle_time.setSingleStep(0.1)
        self.iterations = QSpinBox()
        self.iterations.setRange(1, 10)
        for index, (label, widget, tip) in enumerate((
            ("Corrector probe (±A)", self.probe, "Positive and negative offsets used to measure the response matrix."),
            ("Max. step (A)", self.step, "Maximum current change in one iteration."),
            ("Max. total offset (A)", self.excursion, "Maximum deviation from the initial corrector current."),
            ("Response tolerance (mm)", self.floor, "Confirm this threshold against measured BPM noise."),
            ("Max. target worsening (%)", self.max_target_worsening,
             "Each target must stay at or below max(previous response × (1 + this percentage), response tolerance)."),
            ("Samples / point", self.samples_per_point, "BPM readings averaged at each solenoid current point."),
            ("Sample interval (s)", self.sample_interval, "Wait between BPM readings at the same point."),
            ("Settle after change (s)", self.settle_time, "Wait after changing a solenoid or corrector before measuring."),
            ("Max. iterations", self.iterations, "Reuse the measured response matrix for this many iterations."),
        )):
            widget.setMaximumWidth(150)
            widget.setToolTip(tip)
            (form if index < 5 else advanced_form).addRow(label, widget)
            widget.valueChanged.connect(self._mark_custom)
        settings_layout.addLayout(form)
        self.advanced_panel = QWidget()
        self.advanced_panel.setLayout(advanced_form)
        self.advanced_panel.hide()
        settings_layout.addWidget(self.advanced_panel)
        self.advanced_button.toggled.connect(self._toggle_advanced)
        settings_layout.addStretch()
        scroll = QScrollArea()
        scroll.setObjectName("configurationScroll")
        scroll.setWidgetResizable(True)
        scroll.setWidget(settings_card)
        config_layout.addWidget(scroll, 1)
        configuration.setMinimumWidth(490)
        configuration.setMaximumWidth(560)
        self.splitter.addWidget(configuration)

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
        overview = QFrame()
        overview.setObjectName("jointOverview")
        overview_layout = QHBoxLayout(overview)
        overview_layout.setContentsMargins(16, 12, 16, 12)
        overview_layout.setSpacing(18)
        for caption, attr in (("Solenoids", "overview_targets"), ("BPMs", "overview_bpms"),
                              ("Correctors", "overview_correctors"), ("Estimated wait", "overview_estimate")):
            metric = QWidget()
            metric_layout = QVBoxLayout(metric)
            metric_layout.setContentsMargins(0, 0, 0, 0)
            metric_layout.setSpacing(3)
            title = QLabel(caption)
            title.setProperty("role", "field")
            value = QLabel("—")
            value.setObjectName("jointOverviewValue")
            metric_layout.addWidget(title)
            metric_layout.addWidget(value)
            overview_layout.addWidget(metric, 1)
            setattr(self, attr, value)
        workspace_layout.addWidget(overview)
        self.tabs = QTabWidget()
        self.responses = self._table(["Solenoid", "Initial RMS\n(mm)", "Final RMS\n(mm)", "Improvement"])
        self.results = self._table(["Corrector", "Initial (A)", "Candidate (A)", "Change (A)"])
        self.sensitivities = self._table([
            "Corrector", "BPM", "Plane", "Base mm/A", "− probe mm/A",
            "+ probe mm/A", "Sensitivity mm/A²",
        ])
        self.sensitivities.setToolTip(
            "Slopes: mm per SS01 A. Sensitivity: mm per SS01 A per corrector A."
        )
        self.tabs.addTab(self.responses, "Response")
        self.tabs.addTab(self.results, "Correctors")
        self.tabs.addTab(self.sensitivities, "Sensitivity")
        self.result_stack = QStackedWidget()
        self.empty_results = QFrame()
        self.empty_results.setObjectName("resultCard")
        empty_layout = QVBoxLayout(self.empty_results)
        empty_layout.setAlignment(Qt.AlignCenter)
        empty_layout.setSpacing(10)
        empty_title = QLabel("Results appear after the scan")
        empty_title.setObjectName("jointEmptyTitle")
        empty_title.setAlignment(Qt.AlignCenter)
        empty_layout.addWidget(empty_title)
        self.empty_detail = QLabel("Configure targets  →  Check PVs  →  Scan")
        self.empty_detail.setObjectName("jointEmptyDetail")
        self.empty_detail.setAlignment(Qt.AlignCenter)
        empty_layout.addWidget(self.empty_detail)
        self.result_stack.addWidget(self.empty_results)
        self.result_stack.addWidget(self.tabs)
        workspace_layout.addWidget(self.result_stack, 1)
        self.splitter.addWidget(workspace)
        self.splitter.setSizes([520, 800])

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
        self.start_button = QPushButton("Start Joint Scan")
        self.stop_button = QPushButton("Stop and Restore")
        self.stop_button.setProperty("role", "danger")
        self.apply_button = QPushButton("Apply Recommended")
        self.restore_button = QPushButton("Restore Initial")
        for button, handler in ((self.start_button, self.start), (self.stop_button, self.stop),
                                (self.apply_button, self.apply), (self.restore_button, self.restore)):
            button.setAutoDefault(False)
            button.setProperty("compact", True)
            row.addWidget(button)
            button.clicked.connect(handler)
        row.addStretch()
        self.log_button = QPushButton("Show log")
        self.log_button.setCheckable(True)
        self.log_button.setAutoDefault(False)
        self.log_button.setProperty("compact", True)
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

    def _stylesheet(self):
        colors = self.palette_colors
        return "QWidget { font-size: 12px; }\n" + build_stylesheet(colors) + f"""
QToolButton#jointBpmButton, QToolButton#jointCorrectorButton {{
    background: {colors['input_bg']};
    border: 1px solid {colors['input_border']};
    border-radius: 8px;
    color: {colors['input_fg']};
    min-height: 28px;
    padding: 1px 8px;
}}
QToolButton#jointBpmButton:hover, QToolButton#jointCorrectorButton:hover {{
    border-color: {colors['metric_active_fg']};
    background: {colors['button_hover_bg']};
}}
QToolButton#jointBpmButton:disabled, QToolButton#jointCorrectorButton:disabled {{
    color: {colors['button_disabled_fg']};
    border-color: {colors['button_disabled_border']};
}}
QToolButton#jointRemoveButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    color: {colors['muted_fg']};
    font-size: 16px;
    font-weight: 400;
    margin: 3px;
    padding: 0;
}}
QToolButton#jointRemoveButton:hover {{
    background: {colors['button_hover_bg']};
    border-color: {colors['metric_danger_fg']};
    color: {colors['metric_danger_fg']};
}}
QToolButton#jointRemoveButton:disabled {{
    color: {colors['button_disabled_fg']};
}}
QMenu {{
    background: {colors['panel_bg']};
    border: 1px solid {colors['input_border']};
    color: {colors['input_fg']};
    padding: 4px;
}}
QMenu::item {{ padding: 5px 20px; }}
QMenu::item:selected {{ background: {colors['button_hover_bg']}; }}
QFrame#jointOverview {{
    background: {colors['summary_bg']};
    border: 1px solid {colors['summary_border']};
    border-radius: 11px;
}}
QLabel#jointOverviewValue {{
    color: {colors['summary_title_fg']};
    font-size: 17px;
    font-weight: 700;
}}
QLabel#jointEmptyTitle {{
    color: {colors['summary_title_fg']};
    font-size: 18px;
    font-weight: 700;
}}
QLabel#jointEmptyDetail {{
    color: {colors['muted_fg']};
    font-size: 12px;
}}
"""

    def set_theme(self, theme):
        self.palette_colors = theme_palette(theme)
        self.setStyleSheet(self._stylesheet())
        if self.result and "baseline_scores_mm" in self.result and "final_scores_mm" in self.result:
            for row, (before, after) in enumerate(zip(
                    self.result["baseline_scores_mm"], self.result["final_scores_mm"])):
                item = self.responses.item(row, 3)
                if item is not None and before > 1e-15:
                    color = "metric_active_fg" if after <= before else "metric_danger_fg"
                    item.setForeground(QColor(self.palette_colors[color]))

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

    def _toggle_advanced(self, visible):
        self.advanced_panel.setVisible(visible)
        self.advanced_button.setText("Sampling && iterations  ▾" if visible
                                     else "Sampling && iterations  ▸")

    def _toggle_log(self, visible):
        self.log.setVisible(visible)
        self.log_button.setText("Hide log" if visible else "Show log")

    def _state(self, text, tone="subtle"):
        self.state_label.setText(text)
        self.state_label.setProperty("tone", tone)
        self.state_label.style().unpolish(self.state_label)
        self.state_label.style().polish(self.state_label)
        self.status_changed.emit()

    @staticmethod
    def _spin(low, high, decimals=3):
        widget = QDoubleSpinBox()
        widget.setDecimals(decimals)
        widget.setRange(low, high)
        widget.setSingleStep(0.01)
        return widget

    def group_display_name(self):
        if not self.plans:
            return "--"
        return "Custom" if self._custom else self.plans[self.group.currentIndex()].display_name

    def _mark_custom(self, *_):
        if self._loading:
            return
        self._custom = True
        self.plan_source.setText("Custom plan")
        self.template_notice.show()
        self._invalidate()

    @staticmethod
    def _checked(menu):
        return tuple(action.text() for action in menu.actions() if action.isChecked())

    def _selected_correctors(self):
        selected = self._checked(self.corrector_button.menu())
        order = getattr(self, "_corrector_order", ())
        return tuple(c for c in order if c in selected) + tuple(c for c in selected if c not in order)

    def _set_correctors(self, ids):
        was_loading = self._loading
        self._loading = True
        for action in self.corrector_button.menu().actions():
            action.setChecked(action.text() in ids)
        self._correctors_changed()
        self._loading = was_loading

    def _correctors_changed(self, *_):
        ids = self._selected_correctors()
        self.corrector_summary.setText(f"{len(ids)} corrector channels: " + ", ".join(ids))
        self._mark_custom()

    def _bpm_changed(self, button):
        ids = self._checked(button.menu())
        button.setText(", ".join(ids) if ids else "Select BPMs")
        button.setToolTip(", ".join(ids))
        self._mark_custom()

    def _append_target(self, target):
        row = self.targets.rowCount()
        self.targets.insertRow(row)
        selector = QComboBox()
        for preset in self.available_presets:
            selector.addItem(preset.solenoid, preset.id)
        selector.setCurrentIndex(selector.findData(target.preset_id))
        selector.currentIndexChanged.connect(lambda _index, combo=selector: self._target_changed(combo))
        self.targets.setCellWidget(row, 0, selector)
        bpm_button = QToolButton()
        bpm_button.setObjectName("jointBpmButton")
        bpm_button.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(bpm_button)
        for bpm in self.available_bpms:
            action = menu.addAction(bpm)
            action.setCheckable(True)
            action.setChecked(bpm in target.bpms)
            action.toggled.connect(lambda _checked, button=bpm_button: self._bpm_changed(button))
        bpm_button.setMenu(menu)
        self.targets.setCellWidget(row, 1, bpm_button)
        self._bpm_changed(bpm_button)
        amplitude = self._spin(0.001, 20)
        amplitude.setValue(target.modulation_a)
        amplitude.valueChanged.connect(self._mark_custom)
        self.targets.setCellWidget(row, 2, amplitude)
        remove = QToolButton()
        remove.setObjectName("jointRemoveButton")
        remove.setText("×")
        remove.setToolTip("Remove solenoid from this scan")
        remove.clicked.connect(lambda _checked=False, button=remove: self._remove_target(button))
        self.targets.setCellWidget(row, 3, remove)
        self._resize_targets()

    def _resize_targets(self):
        self.targets.setFixedHeight(self.targets.horizontalHeader().sizeHint().height()
                                    + 34 * self.targets.rowCount() + 8)

    def _target_changed(self, selector):
        if self._loading:
            return
        preset = self.context.solenoid_centering_workflow.presets_by_id[selector.currentData()]
        for row in range(self.targets.rowCount()):
            if self.targets.cellWidget(row, 0) is selector:
                button = self.targets.cellWidget(row, 1)
                self._loading = True
                for action in button.menu().actions():
                    action.setChecked(action.text() == preset.bpm)
                self._loading = False
                break
        self._mark_custom()

    def _add_target(self):
        used = {self.targets.cellWidget(row, 0).currentData()
                for row in range(self.targets.rowCount())}
        preset = next((p for p in self.available_presets if p.id not in used), None)
        if preset is None:
            return
        self._loading = True
        amplitude = self.plans[self.group.currentIndex()].targets[0].modulation_a
        self._append_target(JointTarget(preset.id, (preset.bpm,), amplitude))
        self._loading = False
        self._mark_custom()
        self._refresh()

    def _remove_target(self, button):
        if self.targets.rowCount() <= 1:
            return
        for row in range(self.targets.rowCount()):
            if self.targets.cellWidget(row, 3) is button:
                self.targets.removeRow(row)
                break
        self._resize_targets()
        self._mark_custom()
        self._refresh()

    def _load_group(self, *_):
        if not self.plans:
            self.status.setText("No joint centering groups configured for this machine.")
            self._refresh()
            return
        plan = self.plans[self.group.currentIndex()]
        first_preset = self.context.solenoid_centering_workflow.presets_by_id[plan.targets[0].preset_id]
        self._loading = True
        self.targets.setRowCount(0)
        for target in plan.targets:
            self._append_target(target)
        correctors = plan.correctors or tuple(dict.fromkeys(
            c for target in plan.targets
            for p in (self.context.solenoid_centering_workflow.presets_by_id[target.preset_id],)
            for c in (p.hcorr, p.vcorr)))
        self._corrector_order = tuple(correctors)
        self._set_correctors(correctors)
        self.start_button.setText("Start Response Scan" if plan.mode == "response_diagnostic"
                                  else "Start Joint Scan")
        for widget, value in ((self.probe, plan.probe_a), (self.step, plan.max_step_a),
                              (self.excursion, plan.max_excursion_a), (self.floor, plan.response_floor_mm),
                              (self.max_target_worsening, plan.max_target_degradation * 100),
                              (self.samples_per_point, plan.samples_per_point or first_preset.samples_per_point),
                              (self.sample_interval, first_preset.sample_interval_s if plan.sample_interval_s is None
                               else plan.sample_interval_s),
                              (self.settle_time, first_preset.settle_time_s if plan.settle_time_s is None
                               else plan.settle_time_s),
                              (self.iterations, plan.max_iterations)):
            widget.setValue(value)
        self._loading = False
        self._custom = False
        self.plan_source.setText("Preset plan")
        self.template_notice.hide()
        self._invalidate()

    def _update_overview(self, estimate="—"):
        bpms = {bpm for row in range(self.targets.rowCount())
                for bpm in self._checked(self.targets.cellWidget(row, 1).menu())}
        self.overview_targets.setText(str(self.targets.rowCount()))
        self.overview_bpms.setText(str(len(bpms)))
        self.overview_correctors.setText(str(len(self._selected_correctors())))
        self.overview_estimate.setText(estimate)

    def _invalidate(self, *_):
        self._update_overview()
        self.ready = False
        self.result = None
        self.scanner = None
        self.results.setRowCount(0)
        self.responses.setRowCount(0)
        self.sensitivities.setRowCount(0)
        self.result_stack.setCurrentWidget(self.empty_results)
        self.empty_detail.setText("Configure targets  →  Check PVs  →  Scan")
        self.result_summary.setText("Results")
        self.result_hint.setText("Check connections and limits before scanning.")
        self.status.setText("Configuration changed. Run preflight before scanning.")
        self._state("NOT CHECKED")
        self._refresh()

    def _plan(self):
        plan = self.plans[self.group.currentIndex()]
        targets = tuple(JointTarget(
            self.targets.cellWidget(row, 0).currentData(),
            self._checked(self.targets.cellWidget(row, 1).menu()),
            self.targets.cellWidget(row, 2).value(),
        ) for row in range(self.targets.rowCount()))
        correctors = self._selected_correctors()
        if not correctors:
            raise ValueError("Select at least one corrector channel.")
        return replace(plan, id="custom" if self._custom else plan.id,
                       display_name="Custom joint centering" if self._custom else plan.display_name,
                       targets=targets, correctors=correctors,
                       probe_a=self.probe.value(), max_step_a=self.step.value(),
                       max_excursion_a=self.excursion.value(), response_floor_mm=self.floor.value(),
                       max_target_degradation=self.max_target_worsening.value() / 100,
                       samples_per_point=self.samples_per_point.value(),
                       sample_interval_s=self.sample_interval.value(),
                       settle_time_s=self.settle_time.value(),
                       max_iterations=self.iterations.value())

    def _refresh(self):
        busy = self.worker is not None
        applied = bool(self.result and self.result.get("applied"))
        for widget in (self.group, self.targets, self.probe, self.samples_per_point,
                       self.sample_interval, self.settle_time, self.reset_button):
            widget.setEnabled(not busy and not applied)
        diagnostic = bool(self.plans and self.plans[self.group.currentIndex()].mode == "response_diagnostic")
        self.add_target_button.setEnabled(not busy and not applied and not diagnostic and
                                          self.targets.rowCount() < len(self.available_presets))
        self.corrector_button.setEnabled(not busy and not applied and not diagnostic)
        for row in range(self.targets.rowCount()):
            self.targets.cellWidget(row, 0).setEnabled(not busy and not applied and not diagnostic)
            self.targets.cellWidget(row, 1).setEnabled(not busy and not applied and not diagnostic)
            self.targets.cellWidget(row, 3).setEnabled(not busy and not applied and not diagnostic
                                                        and self.targets.rowCount() > 1)
        for widget in (self.step, self.excursion, self.floor, self.max_target_worsening,
                       self.iterations):
            widget.setEnabled(not busy and not applied and not diagnostic)
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
        self.apply_button.setVisible(bool(self.result) and not applied and not diagnostic)
        self.tabs.setTabEnabled(1, not diagnostic)
        self.tabs.setTabEnabled(2, diagnostic)
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
        self.busy_changed.emit(True)
        self.worker.start()

    def _progress(self, message):
        self.log.appendPlainText(message)
        if not self.worker or not self.worker.stop_requested:
            self.status.setText(message)

    def _done(self):
        self.worker.deleteLater()
        self.worker = None
        self._refresh()
        self.busy_changed.emit(False)

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
        self._update_overview(f"{report['estimated_minimum_seconds'] / 60:.1f} min")
        self.result_hint.setText(f"Up to {report['points_upper_bound']} measurement points. "
                                "Readback and communication delays may increase scan time.")
        self.empty_detail.setText("Preflight passed. Review the estimate, then start the scan.")
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
        self.result_stack.setCurrentWidget(self.tabs)
        if result.get("mode") == "response_diagnostic":
            self._diagnostic_done(result)
            return
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

    def _diagnostic_done(self, result):
        self._state("MEASURED", "success")
        self.result_summary.setText("SS01 response sensitivity")
        drift = result.get("baseline_recheck_difference_mm_per_a", {})
        max_drift = max((abs(value) for planes in drift.values() for value in planes.values()),
                        default=0.0)
        self.result_hint.setText(
            f"Baseline recheck difference: up to {max_drift:.5f} mm/A. "
            "Downstream BPM slopes do not uniquely determine SS01 entrance position or angle. "
            "Initial settings restored; no correction is proposed."
        )
        self.status.setText("Response scan complete. Initial settings restored and verified.")
        self.log.appendPlainText(result["archive_path"])
        baseline = result["baseline_slopes_mm_per_a"]
        rows = result["sensitivities"]
        self.sensitivities.setRowCount(len(rows))
        for row, entry in enumerate(rows):
            values = (
                entry["corrector"], entry["bpm"], entry["plane"].upper(),
                baseline[entry["bpm"]][entry["plane"]],
                entry["slope_minus_mm_per_a"], entry["slope_plus_mm_per_a"],
                entry["sensitivity_mm_per_a2"],
            )
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value) if col < 3 else f"{value:+.5f}")
                if col >= 3:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.sensitivities.setItem(row, col, item)
        self.tabs.setCurrentIndex(2)
        self._refresh()

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
