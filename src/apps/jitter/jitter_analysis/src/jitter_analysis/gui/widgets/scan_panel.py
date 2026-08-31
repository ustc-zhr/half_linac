from __future__ import annotations

try:
    from PyQt5 import QtCore, QtWidgets
except ImportError:  # pragma: no cover - optional runtime dependency
    QtCore = None
    QtWidgets = None


if QtWidgets is not None:

    class ScanPanel(QtWidgets.QWidget):
        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            self._last_applied_knob_id = None
            self._group_labels = {}
            self._knobs_by_id = {}
            self._knob_modes_enabled = False
            self._random_knob_state = {}
            self._preview_text = ""
            self._random_preview_text = ""

            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            self.pages = QtWidgets.QStackedWidget()
            self.monitor_page = self._build_monitor_page()
            self.single_knob_page = self._build_single_knob_page()
            self.random_page = self._build_random_page()
            self.pages.addWidget(self.monitor_page)
            self.pages.addWidget(self.single_knob_page)
            self.pages.addWidget(self.random_page)
            layout.addWidget(self.pages)

        def _create_page_section(self, title: str):
            box = QtWidgets.QGroupBox(title)
            box.setProperty("themeSection", "main")
            return box

        @staticmethod
        def _configure_form_layout(form) -> None:
            form.setContentsMargins(12, 12, 12, 12)
            form.setHorizontalSpacing(12)
            form.setVerticalSpacing(10)

        @staticmethod
        def _apply_button_role(button, role: str) -> None:
            button.setProperty("role", role)
            button.setMinimumHeight(40)

        def _build_monitor_page(self):
            widget = QtWidgets.QWidget()
            form = QtWidgets.QFormLayout(widget)
            self.interval_spin = QtWidgets.QDoubleSpinBox()
            self.interval_spin.setRange(0.01, 3600.0)
            self.interval_spin.setValue(0.2)
            self.interval_spin.setDecimals(3)
            self.count_spin = QtWidgets.QSpinBox()
            self.count_spin.setRange(1, 1000000)
            self.count_spin.setValue(200)
            self.stop_condition_combo = QtWidgets.QComboBox()
            self.stop_condition_combo.addItem("Total Samples", "samples")
            self.stop_condition_combo.addItem("Total Duration", "duration")
            self.stop_condition_combo.addItem("Continuous", "continuous")
            self.duration_spin = QtWidgets.QDoubleSpinBox()
            self.duration_spin.setRange(0.1, 365 * 24 * 3600.0)
            self.duration_spin.setValue(60.0)
            self.duration_spin.setDecimals(1)
            self.duration_spin.setSuffix(" s")
            self.monitor_estimate_label = QtWidgets.QLabel()
            self.monitor_estimate_label.setProperty("role", "pageHint")
            self.monitor_estimate_label.setWordWrap(True)
            form.addRow("Sample Interval [s]", self.interval_spin)
            form.addRow("Stop Condition", self.stop_condition_combo)
            form.addRow("Total Samples", self.count_spin)
            form.addRow("Total Duration", self.duration_spin)
            form.addRow("", self.monitor_estimate_label)
            self.count_label = form.labelForField(self.count_spin)
            self.duration_label = form.labelForField(self.duration_spin)
            self.stop_condition_combo.currentIndexChanged.connect(self._update_monitor_controls)
            self.interval_spin.valueChanged.connect(self._update_monitor_estimate)
            self.count_spin.valueChanged.connect(self._update_monitor_estimate)
            self.duration_spin.valueChanged.connect(self._update_monitor_estimate)
            self._update_monitor_controls()
            return widget

        def _build_single_knob_page(self):
            widget = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(widget)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)

            form_frame = QtWidgets.QFrame()
            form_frame.setObjectName("singleKnobForm")
            form_layout = QtWidgets.QVBoxLayout(form_frame)
            form_layout.setContentsMargins(14, 12, 14, 12)
            form_layout.setSpacing(10)

            active_title = QtWidgets.QLabel("Active Knob")
            active_title.setProperty("role", "formSectionTitle")
            form_layout.addWidget(active_title)
            self.active_knob_combo = QtWidgets.QComboBox()
            self.active_knob_combo.setObjectName("activeKnobCombo")
            self.active_knob_combo.setEditable(True)
            self.active_knob_combo.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
            self.active_knob_combo.setSizeAdjustPolicy(
                QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon
            )
            self.active_knob_combo.lineEdit().setPlaceholderText("Type to search selected control PVs")
            completer = self.active_knob_combo.completer()
            if completer is not None:
                completer.setCompletionMode(QtWidgets.QCompleter.PopupCompletion)
                completer.setFilterMode(QtCore.Qt.MatchContains)
                completer.setCaseSensitivity(QtCore.Qt.CaseInsensitive)

            self.active_knob_dropdown_button = QtWidgets.QToolButton()
            self.active_knob_dropdown_button.setObjectName("activeKnobDropdownButton")
            self.active_knob_dropdown_button.setText("\u25be")
            self.active_knob_dropdown_button.setToolTip("Show selected control PVs")
            self.active_knob_dropdown_button.setAccessibleName("Show control PV list")
            self.active_knob_dropdown_button.setFixedWidth(36)
            self.active_knob_dropdown_button.setFocusPolicy(QtCore.Qt.NoFocus)
            self.active_knob_dropdown_button.clicked.connect(self.active_knob_combo.showPopup)

            self.active_knob_combo.setFixedHeight(32)
            self.active_knob_dropdown_button.setFixedHeight(32)

            active_knob_control = QtWidgets.QWidget()
            active_knob_control_layout = QtWidgets.QHBoxLayout(active_knob_control)
            active_knob_control_layout.setContentsMargins(0, 0, 0, 0)
            active_knob_control_layout.setSpacing(0)
            active_knob_control_layout.addWidget(self.active_knob_combo, 1)
            active_knob_control_layout.addWidget(self.active_knob_dropdown_button)

            self.active_knob_summary_label = QtWidgets.QLabel("No active control PV selected")
            self.active_knob_summary_label.setWordWrap(True)
            self.single_scope_label = QtWidgets.QLabel(
                "Writes: only the active control PV. Other selected control PVs stay fixed."
            )
            self.single_scope_label.setWordWrap(True)
            self.single_scope_label.setProperty("role", "context")

            self.scan_value_mode_combo = QtWidgets.QComboBox()
            self.scan_value_mode_combo.addItem("Manual List", "manual")
            self.scan_value_mode_combo.addItem("Start / Stop / Step", "range_step")
            self.scan_value_mode_combo.addItem("Start / Stop / Num Points", "range_points")
            self.scan_value_mode_combo.addItem("Symmetric Around Current", "symmetric_points")
            self.scan_value_mode_combo.setCurrentIndex(
                self.scan_value_mode_combo.findData("symmetric_points")
            )

            self.scan_value_stack = QtWidgets.QStackedWidget()
            self.scan_value_stack.setSizePolicy(
                QtWidgets.QSizePolicy.Expanding,
                QtWidgets.QSizePolicy.Fixed,
            )
            self.manual_page = self._build_manual_scan_value_page()
            self.range_step_page = self._build_range_step_page()
            self.range_points_page = self._build_range_points_page()
            self.symmetric_page = self._build_symmetric_points_page()
            self.scan_value_stack.addWidget(self.manual_page)
            self.scan_value_stack.addWidget(self.range_step_page)
            self.scan_value_stack.addWidget(self.range_points_page)
            self.scan_value_stack.addWidget(self.symmetric_page)

            self.step_sample_spin = QtWidgets.QSpinBox()
            self.step_sample_spin.setRange(1, 1000000)
            self.step_sample_spin.setValue(5)
            self.settle_spin = QtWidgets.QDoubleSpinBox()
            self.settle_spin.setRange(0.0, 3600.0)
            self.settle_spin.setValue(1.5)
            self.scan_sample_interval_spin = QtWidgets.QDoubleSpinBox()
            self.scan_sample_interval_spin.setRange(0.0, 3600.0)
            self.scan_sample_interval_spin.setValue(0.0)
            self.scan_sample_interval_spin.setDecimals(3)
            self.restore_check = QtWidgets.QCheckBox("Restore initial knob value after scan")
            self.restore_check.setChecked(True)

            active_field = self._compact_field("Active Control PV", active_knob_control)
            form_layout.addWidget(active_field)
            form_layout.addWidget(self.active_knob_summary_label)
            form_layout.addWidget(self.single_scope_label)
            form_layout.addWidget(self._single_form_separator())

            points_title = QtWidgets.QLabel("Scan Points")
            points_title.setProperty("role", "formSectionTitle")
            form_layout.addWidget(points_title)
            points_row = QtWidgets.QHBoxLayout()
            points_row.setSpacing(10)
            generator_field = self._compact_field("Point Generator", self.scan_value_mode_combo)
            generator_field.setFixedWidth(250)
            points_row.addWidget(generator_field)
            points_row.addWidget(self._compact_field("Scan Values", self.scan_value_stack), 1)
            form_layout.addLayout(points_row)

            acquisition_row = QtWidgets.QHBoxLayout()
            acquisition_row.setSpacing(10)
            acquisition_row.addWidget(self._compact_field("Samples / Point", self.step_sample_spin), 1)
            acquisition_row.addWidget(self._compact_field("Settle Delay [s]", self.settle_spin), 1)
            acquisition_row.addWidget(
                self._compact_field("Sample Interval [s]", self.scan_sample_interval_spin), 1
            )
            form_layout.addLayout(acquisition_row)
            form_layout.addWidget(self.restore_check)
            form_layout.addWidget(self._single_form_separator())

            preview_bar = QtWidgets.QFrame()
            preview_bar.setObjectName("singlePreviewSummary")
            preview_layout = QtWidgets.QHBoxLayout(preview_bar)
            preview_layout.setContentsMargins(10, 7, 8, 7)
            preview_layout.setSpacing(8)
            self.preview_summary_label = QtWidgets.QLabel(
                "Choose a control PV to preview generated points."
            )
            self.preview_summary_label.setWordWrap(True)
            self.preview_refresh_button = QtWidgets.QPushButton("Refresh Current")
            self.preview_show_button = QtWidgets.QPushButton("Details")
            self.preview_show_button.setEnabled(False)
            self._apply_button_role(self.preview_refresh_button, "diagnostic")
            self._apply_button_role(self.preview_show_button, "diagnostic")
            self.preview_refresh_button.setMinimumHeight(32)
            self.preview_show_button.setMinimumHeight(32)
            preview_layout.addWidget(self.preview_summary_label, 1)
            preview_layout.addWidget(self.preview_refresh_button)
            preview_layout.addWidget(self.preview_show_button)
            form_layout.addWidget(preview_bar)

            layout.addWidget(form_frame)
            layout.addStretch(1)

            self.scan_value_mode_combo.currentIndexChanged.connect(self._update_scan_value_mode)
            self.preview_show_button.clicked.connect(
                lambda: self._show_text_dialog("Single Knob Preview", self._preview_text)
            )
            self._update_scan_value_mode()
            return widget

        @staticmethod
        def _compact_field(label_text: str, field):
            container = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(4)
            label = QtWidgets.QLabel(label_text)
            label.setProperty("role", "field")
            layout.addWidget(label)
            layout.addWidget(field)
            return container

        @staticmethod
        def _single_form_separator():
            separator = QtWidgets.QFrame()
            separator.setObjectName("singleFormSeparator")
            separator.setFrameShape(QtWidgets.QFrame.HLine)
            separator.setFrameShadow(QtWidgets.QFrame.Plain)
            return separator

        def _build_random_page(self):
            widget = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(widget)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)

            form_frame = QtWidgets.QFrame()
            form_frame.setObjectName("randomKnobForm")
            form_layout = QtWidgets.QVBoxLayout(form_frame)
            form_layout.setContentsMargins(14, 12, 14, 12)
            form_layout.setSpacing(10)

            knobs_title = QtWidgets.QLabel("Control PV Ranges")
            knobs_title.setProperty("role", "formSectionTitle")
            form_layout.addWidget(knobs_title)

            self.random_scope_label = QtWidgets.QLabel(
                "Writes: all enabled control PVs together at each point. Disabled control PVs stay fixed."
            )
            self.random_scope_label.setWordWrap(True)
            self.random_scope_label.setProperty("role", "context")

            self.random_config_summary_label = QtWidgets.QLabel(
                "Choose control PVs, then configure the sampling ranges."
            )
            self.random_config_summary_label.setWordWrap(True)
            self.random_config_button = QtWidgets.QPushButton("Configure Ranges...")
            self._apply_button_role(self.random_config_button, "control")
            range_actions = QtWidgets.QHBoxLayout()
            range_actions.setSpacing(8)
            range_actions.addWidget(self.random_config_summary_label, 1)
            range_actions.addWidget(self.random_config_button)
            form_layout.addLayout(range_actions)
            form_layout.addWidget(self.random_scope_label)
            form_layout.addWidget(self._single_form_separator())

            sampling_title = QtWidgets.QLabel("Sampling Plan")
            sampling_title.setProperty("role", "formSectionTitle")
            form_layout.addWidget(sampling_title)
            self.random_sampling_method_combo = QtWidgets.QComboBox()
            self.random_sampling_method_combo.addItem("Uniform Random", "uniform_random")
            self.random_sampling_method_combo.addItem("Grid", "grid")
            self.random_sampling_method_combo.setToolTip(
                "Uniform Random scales to many control PVs and is recommended for Influence. "
                "Grid reveals response surfaces for up to 3 changing control PVs."
            )
            # Compatibility alias for integrations that referenced the old widget name.
            self.random_distribution_combo = self.random_sampling_method_combo
            self.random_point_count_spin = QtWidgets.QSpinBox()
            self.random_point_count_spin.setRange(1, 1000000)
            self.random_point_count_spin.setValue(20)
            self.random_levels_spin = QtWidgets.QSpinBox()
            self.random_levels_spin.setRange(2, 100)
            self.random_levels_spin.setValue(3)
            self.random_count_stack = QtWidgets.QStackedWidget()
            self.random_count_stack.addWidget(
                self._compact_field("Random Points", self.random_point_count_spin)
            )
            self.random_count_stack.addWidget(
                self._compact_field("Levels / Knob", self.random_levels_spin)
            )
            self.random_samples_per_point_spin = QtWidgets.QSpinBox()
            self.random_samples_per_point_spin.setRange(1, 1000000)
            self.random_samples_per_point_spin.setValue(5)
            sampling_row = QtWidgets.QHBoxLayout()
            sampling_row.setSpacing(10)
            sampling_row.addWidget(
                self._compact_field("Sampling Method", self.random_sampling_method_combo), 1
            )
            sampling_row.addWidget(self.random_count_stack, 1)
            sampling_row.addWidget(
                self._compact_field("Samples / Point", self.random_samples_per_point_spin), 1
            )
            form_layout.addLayout(sampling_row)
            self.random_grid_summary_label = QtWidgets.QLabel()
            self.random_grid_summary_label.setProperty("role", "context")
            form_layout.addWidget(self.random_grid_summary_label)

            acquisition_row = QtWidgets.QHBoxLayout()
            acquisition_row.setSpacing(10)
            self.random_settle_spin = QtWidgets.QDoubleSpinBox()
            self.random_settle_spin.setRange(0.0, 3600.0)
            self.random_settle_spin.setDecimals(3)
            self.random_settle_spin.setValue(1.5)
            self.random_sample_interval_spin = QtWidgets.QDoubleSpinBox()
            self.random_sample_interval_spin.setRange(0.0, 3600.0)
            self.random_sample_interval_spin.setDecimals(3)
            self.random_sample_interval_spin.setValue(0.0)
            self.random_restore_check = QtWidgets.QCheckBox("Restore initial knob values after sampling")
            self.random_restore_check.setChecked(True)
            acquisition_row.addWidget(
                self._compact_field("Settle Delay [s]", self.random_settle_spin), 1
            )
            acquisition_row.addWidget(
                self._compact_field("Sample Interval [s]", self.random_sample_interval_spin), 1
            )
            form_layout.addLayout(acquisition_row)
            form_layout.addWidget(self.random_restore_check)
            form_layout.addWidget(self._single_form_separator())

            preview_bar = QtWidgets.QFrame()
            preview_bar.setObjectName("randomPreviewSummary")
            preview_layout = QtWidgets.QHBoxLayout(preview_bar)
            preview_layout.setContentsMargins(10, 7, 8, 7)
            preview_layout.setSpacing(8)
            self.random_preview_button = QtWidgets.QPushButton("Refresh")
            self.random_preview_show_button = QtWidgets.QPushButton("Details")
            self.random_preview_show_button.setEnabled(False)
            self._apply_button_role(self.random_preview_button, "diagnostic")
            self._apply_button_role(self.random_preview_show_button, "diagnostic")
            self.random_preview_summary_label = QtWidgets.QLabel(
                "Configure ranges, then preview generated scan points."
            )
            self.random_preview_summary_label.setWordWrap(True)
            preview_layout.addWidget(self.random_preview_summary_label, 1)
            preview_layout.addWidget(self.random_preview_button)
            preview_layout.addWidget(self.random_preview_show_button)
            form_layout.addWidget(preview_bar)

            layout.addWidget(form_frame)
            layout.addStretch(1)
            self.random_preview_show_button.clicked.connect(
                lambda: self._show_text_dialog("Multi-Knob Preview", self._random_preview_text)
            )
            self.random_sampling_method_combo.currentIndexChanged.connect(
                self._update_random_sampling_method
            )
            self.random_levels_spin.valueChanged.connect(self._update_random_sampling_method)
            self._update_random_sampling_method()
            return widget

        def _update_random_sampling_method(self, *_args) -> None:
            is_grid = self.random_sampling_method_combo.currentData() == "grid"
            self.random_count_stack.setCurrentIndex(1 if is_grid else 0)
            self.random_grid_summary_label.setVisible(is_grid)
            if is_grid:
                varying = 0
                for row in self._random_knob_state.values():
                    if not row.get("enabled", True):
                        continue
                    try:
                        if float(row.get("low_text", "")) != float(row.get("high_text", "")):
                            varying += 1
                    except (TypeError, ValueError):
                        continue
                levels = int(self.random_levels_spin.value())
                total_points = levels**varying if varying else 1
                if varying > 3:
                    text = f"{varying} changing control PVs · Grid supports at most 3"
                elif total_points > 1000:
                    text = f"{varying} changing control PVs · {total_points} total points (maximum 1000)"
                else:
                    text = f"{varying} changing control PVs · {total_points} total grid points"
                self.random_grid_summary_label.setText(text)
            self.random_restore_check.setText(
                "Restore initial knob values after scan"
                if is_grid
                else "Restore initial knob values after sampling"
            )

        def _build_manual_scan_value_page(self):
            widget = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(widget)
            layout.setContentsMargins(0, 0, 0, 0)
            self.manual_scan_values_edit = QtWidgets.QLineEdit("-0.2, -0.1, 0.0, 0.1, 0.2")
            self.manual_scan_values_edit.setPlaceholderText("Comma-separated values")
            layout.addWidget(self.manual_scan_values_edit)
            return widget

        def _build_range_step_page(self):
            widget = QtWidgets.QWidget()
            layout = QtWidgets.QHBoxLayout(widget)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)
            self.range_start_spin = self._new_scan_value_spinbox()
            self.range_stop_spin = self._new_scan_value_spinbox()
            self.range_step_spin = self._new_positive_step_spinbox(default=0.05)
            layout.addWidget(self._compact_field("Start", self.range_start_spin), 1)
            layout.addWidget(self._compact_field("Stop", self.range_stop_spin), 1)
            layout.addWidget(self._compact_field("Step", self.range_step_spin), 1)
            return widget

        def _build_range_points_page(self):
            widget = QtWidgets.QWidget()
            layout = QtWidgets.QHBoxLayout(widget)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)
            self.points_start_spin = self._new_scan_value_spinbox()
            self.points_stop_spin = self._new_scan_value_spinbox()
            self.points_count_spin = QtWidgets.QSpinBox()
            self.points_count_spin.setRange(1, 1000000)
            self.points_count_spin.setValue(5)
            layout.addWidget(self._compact_field("Start", self.points_start_spin), 1)
            layout.addWidget(self._compact_field("Stop", self.points_stop_spin), 1)
            layout.addWidget(self._compact_field("Num Points", self.points_count_spin), 1)
            return widget

        def _build_symmetric_points_page(self):
            widget = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(widget)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(4)
            fields = QtWidgets.QHBoxLayout()
            fields.setSpacing(8)
            self.symmetric_half_range_spin = self._new_positive_step_spinbox(default=0.2)
            self.symmetric_points_spin = QtWidgets.QSpinBox()
            self.symmetric_points_spin.setRange(1, 1000000)
            self.symmetric_points_spin.setValue(5)
            self.symmetric_info_label = QtWidgets.QLabel(
                "Center is read from the knob readback when the scan starts."
            )
            self.symmetric_info_label.setWordWrap(True)
            fields.addWidget(self._compact_field("Half Range", self.symmetric_half_range_spin), 1)
            fields.addWidget(self._compact_field("Num Points", self.symmetric_points_spin), 1)
            layout.addLayout(fields)
            layout.addWidget(self.symmetric_info_label)
            return widget

        def _new_scan_value_spinbox(self):
            spin = QtWidgets.QDoubleSpinBox()
            spin.setRange(-1.0e9, 1.0e9)
            spin.setDecimals(6)
            spin.setValue(0.0)
            return spin

        def _new_positive_step_spinbox(self, default: float):
            spin = QtWidgets.QDoubleSpinBox()
            spin.setRange(0.000001, 1.0e9)
            spin.setDecimals(6)
            spin.setValue(default)
            return spin

        def _update_scan_value_mode(self) -> None:
            self.scan_value_stack.setCurrentIndex(self.scan_value_mode_combo.currentIndex())
            current_page = self.scan_value_stack.currentWidget()
            if current_page is not None:
                page_layout = current_page.layout()
                if page_layout is not None:
                    page_layout.activate()
                self.scan_value_stack.setFixedHeight(max(32, current_page.sizeHint().height()))
            is_symmetric = self.scan_value_mode_combo.currentData() == "symmetric_points"
            self.preview_refresh_button.setVisible(is_symmetric)

        def set_knob_choices(self, knobs, active_knob_id: str | None = None, group_labels: dict[str, str] | None = None) -> None:
            self._group_labels = dict(group_labels or {})
            self._knobs_by_id = {knob.id: knob for knob in knobs}

            previous_state = self.random_knob_state()
            self._random_knob_state = {}
            for knob in knobs:
                row_state = previous_state.get(knob.id, {})
                self._random_knob_state[knob.id] = {
                    "enabled": bool(row_state.get("enabled", True)),
                    "current_text": str(row_state.get("current_text", "--")),
                    "low_text": str(row_state.get("low_text", f"{float(knob.limits.low):.6g}")),
                    "high_text": str(row_state.get("high_text", f"{float(knob.limits.high):.6g}")),
                }

            blocker = QtCore.QSignalBlocker(self.active_knob_combo)
            self.active_knob_combo.clear()
            for knob in knobs:
                self.active_knob_combo.addItem(f"{knob.name}  [{knob.write_pv}]", knob.id)
            if active_knob_id:
                index = self.active_knob_combo.findData(active_knob_id)
                if index >= 0:
                    self.active_knob_combo.setCurrentIndex(index)
            elif knobs:
                self.active_knob_combo.setCurrentIndex(0)
            del blocker
            active_knob = self._knobs_by_id.get(active_knob_id or self.selected_knob_id() or "")
            self._update_single_scope_summary(active_knob)
            self._update_random_summary()
            self._update_random_sampling_method()

        def selected_knob_id(self) -> str | None:
            value = self.active_knob_combo.currentData()
            return str(value) if value else None

        def current_mode(self) -> str:
            return self.task_mode()

        def task_mode(self) -> str:
            index = self.pages.currentIndex()
            if index == 0:
                return "timed_acquisition"
            if index == 1:
                return "single_knob_scan"
            return "multi_knob_random"

        def set_task_mode(self, mode: str) -> None:
            if mode == "timed_acquisition":
                self.pages.setCurrentIndex(0)
                return
            if mode == "single_knob_scan":
                self.pages.setCurrentIndex(1)
                return
            if mode == "multi_knob_random":
                self.pages.setCurrentIndex(2)
                return
            raise ValueError(f"Unsupported task mode: {mode}")

        def knob_scan_available(self) -> bool:
            return self._knob_modes_enabled

        def random_configuration(self) -> dict[str, object]:
            sampling_method = self.random_sampling_method_combo.currentData()
            return {
                "sampling_method": str(sampling_method) if sampling_method else "uniform_random",
                "num_points": int(self.random_point_count_spin.value()),
                "levels_per_knob": int(self.random_levels_spin.value()),
                "settle_delay_sec": float(self.random_settle_spin.value()),
                "shot_interval_sec": float(self.random_sample_interval_spin.value()),
                "sample_count_per_point": int(self.random_samples_per_point_spin.value()),
                "restore_initial_values": bool(self.random_restore_check.isChecked()),
            }

        def monitor_configuration(self) -> dict[str, object]:
            return {
                "shot_interval_sec": float(self.interval_spin.value()),
                "stop_mode": str(self.stop_condition_combo.currentData() or "samples"),
                "sample_count": int(self.count_spin.value()),
                "duration_sec": float(self.duration_spin.value()),
            }

        def single_knob_configuration(self) -> dict[str, object]:
            return {
                "scan_value_definition": dict(self.scan_value_definition()),
                "sample_count_per_step": int(self.step_sample_spin.value()),
                "settle_delay_sec": float(self.settle_spin.value()),
                "shot_interval_sec": float(self.scan_sample_interval_spin.value()),
                "restore_initial_value": bool(self.restore_check.isChecked()),
            }

        def random_knob_state(self) -> dict[str, dict[str, object]]:
            return {
                knob_id: {
                    "enabled": bool(row.get("enabled", True)),
                    "current_text": str(row.get("current_text", "")),
                    "low_text": str(row.get("low_text", "")),
                    "high_text": str(row.get("high_text", "")),
                }
                for knob_id, row in self._random_knob_state.items()
            }

        def set_random_knob_state(self, state: dict[str, dict[str, object]]) -> None:
            for knob_id, knob in self._knobs_by_id.items():
                row = state.get(knob_id, {})
                self._random_knob_state[knob_id] = {
                    "enabled": bool(row.get("enabled", True)),
                    "current_text": str(row.get("current_text", "--")),
                    "low_text": str(row.get("low_text", f"{float(knob.limits.low):.6g}")),
                    "high_text": str(row.get("high_text", f"{float(knob.limits.high):.6g}")),
                }
            self._update_random_summary()
            self._update_random_sampling_method()

        def scan_value_mode(self) -> str:
            value = self.scan_value_mode_combo.currentData()
            return str(value) if value else "manual"

        def scan_value_definition(self) -> dict[str, float | int | str]:
            mode = self.scan_value_mode()
            if mode == "manual":
                return {"mode": mode, "text": self.manual_scan_values_edit.text()}
            if mode == "range_step":
                return {
                    "mode": mode,
                    "start": float(self.range_start_spin.value()),
                    "stop": float(self.range_stop_spin.value()),
                    "step": float(self.range_step_spin.value()),
                }
            if mode == "range_points":
                return {
                    "mode": mode,
                    "start": float(self.points_start_spin.value()),
                    "stop": float(self.points_stop_spin.value()),
                    "num_points": int(self.points_count_spin.value()),
                }
            return {
                "mode": "symmetric_points",
                "half_range": float(self.symmetric_half_range_spin.value()),
                "num_points": int(self.symmetric_points_spin.value()),
            }

        def apply_monitor_configuration(self, config: dict[str, object]) -> None:
            if "shot_interval_sec" in config:
                self.interval_spin.setValue(float(config["shot_interval_sec"]))
            stop_mode = str(config.get("stop_mode", "samples")).strip().lower()
            index = self.stop_condition_combo.findData(stop_mode)
            if index >= 0:
                self.stop_condition_combo.setCurrentIndex(index)
            if "sample_count" in config:
                self.count_spin.setValue(int(config["sample_count"]))
            if "duration_sec" in config:
                self.duration_spin.setValue(float(config["duration_sec"]))
            self._update_monitor_controls()

        def monitor_stop_mode(self) -> str:
            return str(self.stop_condition_combo.currentData() or "samples")

        def _update_monitor_controls(self) -> None:
            mode = self.monitor_stop_mode()
            samples_visible = mode == "samples"
            duration_visible = mode == "duration"
            self.count_label.setVisible(samples_visible)
            self.count_spin.setVisible(samples_visible)
            self.duration_label.setVisible(duration_visible)
            self.duration_spin.setVisible(duration_visible)
            self.monitor_estimate_label.setVisible(mode != "continuous")
            self._update_monitor_estimate()

        def _update_monitor_estimate(self) -> None:
            mode = self.monitor_stop_mode()
            interval = float(self.interval_spin.value())
            if mode == "samples":
                duration = max(0.0, (int(self.count_spin.value()) - 1) * interval)
                self.monitor_estimate_label.setText(
                    f"Estimated duration: {self._format_duration(duration)}"
                )
            elif mode == "duration":
                duration = float(self.duration_spin.value())
                estimate = int(duration / interval) + 1 if interval > 0 else 0
                self.monitor_estimate_label.setText(
                    f"Estimated samples: {estimate:,}  |  Duration: {self._format_duration(duration)}"
                )
            else:
                self.monitor_estimate_label.setText("Runs until you click Stop.")

        @staticmethod
        def _format_duration(seconds: float) -> str:
            total_seconds = max(0, int(round(seconds)))
            hours, remainder = divmod(total_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            if hours:
                return f"{hours} h {minutes:02d} min"
            if minutes:
                return f"{minutes} min {seconds:02d} s"
            return f"{seconds} s"

        def apply_single_knob_configuration(self, config: dict[str, object]) -> None:
            definition = config.get("scan_value_definition")
            if isinstance(definition, dict):
                self.apply_scan_value_definition(definition)
            if "sample_count_per_step" in config:
                self.step_sample_spin.setValue(int(config["sample_count_per_step"]))
            if "settle_delay_sec" in config:
                self.settle_spin.setValue(float(config["settle_delay_sec"]))
            if "shot_interval_sec" in config:
                self.scan_sample_interval_spin.setValue(float(config["shot_interval_sec"]))
            if "restore_initial_value" in config:
                self.restore_check.setChecked(bool(config["restore_initial_value"]))

        def apply_random_configuration(self, config: dict[str, object]) -> None:
            sampling_method = str(config.get("sampling_method", "")).strip()
            if not sampling_method:
                legacy_distribution = str(config.get("distribution", "")).strip()
                if legacy_distribution:
                    sampling_method = "uniform_random"
            if sampling_method:
                index = self.random_sampling_method_combo.findData(sampling_method)
                if index >= 0:
                    self.random_sampling_method_combo.setCurrentIndex(index)
            if "num_points" in config:
                self.random_point_count_spin.setValue(int(config["num_points"]))
            if "levels_per_knob" in config:
                self.random_levels_spin.setValue(int(config["levels_per_knob"]))
            if "sample_count_per_point" in config:
                self.random_samples_per_point_spin.setValue(int(config["sample_count_per_point"]))
            if "settle_delay_sec" in config:
                self.random_settle_spin.setValue(float(config["settle_delay_sec"]))
            if "shot_interval_sec" in config:
                self.random_sample_interval_spin.setValue(float(config["shot_interval_sec"]))
            if "restore_initial_values" in config:
                self.random_restore_check.setChecked(bool(config["restore_initial_values"]))

        def apply_scan_value_definition(self, definition: dict[str, object]) -> None:
            mode = str(definition.get("mode", "manual"))
            index = self.scan_value_mode_combo.findData(mode)
            if index < 0 and mode == "symmetric_points":
                index = self.scan_value_mode_combo.findData("symmetric_points")
            if index < 0:
                index = 0
            self.scan_value_mode_combo.setCurrentIndex(index)
            if mode == "manual":
                self.manual_scan_values_edit.setText(str(definition.get("text", "")))
                return
            if mode == "range_step":
                self.range_start_spin.setValue(float(definition.get("start", self.range_start_spin.value())))
                self.range_stop_spin.setValue(float(definition.get("stop", self.range_stop_spin.value())))
                self.range_step_spin.setValue(float(definition.get("step", self.range_step_spin.value())))
                return
            if mode == "range_points":
                self.points_start_spin.setValue(float(definition.get("start", self.points_start_spin.value())))
                self.points_stop_spin.setValue(float(definition.get("stop", self.points_stop_spin.value())))
                self.points_count_spin.setValue(int(definition.get("num_points", self.points_count_spin.value())))
                return
            self.symmetric_half_range_spin.setValue(
                float(definition.get("half_range", self.symmetric_half_range_spin.value()))
            )
            self.symmetric_points_spin.setValue(
                int(definition.get("num_points", self.symmetric_points_spin.value()))
            )

        def apply_knob_spec(self, knob) -> None:
            if knob is None:
                self._last_applied_knob_id = None
                self.active_knob_summary_label.setText("No active control PV selected")
                self._update_single_scope_summary(None)
                self.preview_summary_label.setText(
                    "Choose a control PV to preview generated points."
                )
                return

            low = float(knob.limits.low)
            high = float(knob.limits.high)
            group_label = self._group_labels.get(knob.group, knob.group)
            self.active_knob_summary_label.setText(
                f"{knob.name}  |  Group: {group_label}  |  Range: {low:.6g} to {high:.6g} {knob.unit}"
            )
            self._update_single_scope_summary(knob)
            if self._last_applied_knob_id == knob.id:
                return

            span = max(abs(high - low), abs(knob.step_hint), 1.0)
            half_span = max(span / 2.0, abs(knob.step_hint))
            step_hint = max(abs(float(knob.step_hint)), 0.000001)

            for spin in (
                self.range_start_spin,
                self.range_stop_spin,
                self.points_start_spin,
                self.points_stop_spin,
            ):
                spin.setRange(low, high)
            self.range_step_spin.setMaximum(max(span, step_hint))
            self.symmetric_half_range_spin.setMaximum(max(span, half_span))
            self.range_step_spin.setValue(step_hint)
            self.symmetric_half_range_spin.setValue(half_span)
            self.symmetric_info_label.setText(
                f"Center is read from {knob.name} readback when the scan starts."
            )
            self._last_applied_knob_id = knob.id

        def set_preview_values(self, values, summary: str = "", detail: str = "") -> None:
            formatted = [f"{index + 1:03d}: {value:.6g}" for index, value in enumerate(values)]
            self._preview_text = "\n".join(formatted)
            self.preview_summary_label.setText(summary or f"{len(values)} point(s)")
            self.preview_summary_label.setToolTip(detail or "")
            self.preview_show_button.setEnabled(bool(formatted))

        def set_preview_message(self, message: str) -> None:
            self.preview_summary_label.setText(message)
            self.preview_summary_label.setToolTip("")
            self._preview_text = ""
            self.preview_show_button.setEnabled(False)

        def set_random_preview(self, lines, summary: str = "", detail: str = "") -> None:
            self._random_preview_text = "\n".join(lines)
            self.random_preview_summary_label.setText(summary or f"{len(lines)} line(s)")
            self.random_preview_summary_label.setToolTip(detail or "")
            self.random_preview_show_button.setEnabled(bool(lines))

        def set_random_preview_message(self, message: str) -> None:
            self.random_preview_summary_label.setText(message)
            self.random_preview_summary_label.setToolTip("")
            self._random_preview_text = ""
            self.random_preview_show_button.setEnabled(False)

        def set_knob_scan_enabled(self, enabled: bool) -> None:
            self._knob_modes_enabled = bool(enabled)
            self.active_knob_combo.setEnabled(self._knob_modes_enabled)
            self.active_knob_dropdown_button.setEnabled(self._knob_modes_enabled)
            self.random_config_button.setEnabled(self._knob_modes_enabled)
            self.preview_refresh_button.setEnabled(self._knob_modes_enabled)
            self.preview_show_button.setEnabled(self._knob_modes_enabled and bool(self._preview_text))
            self.random_preview_button.setEnabled(self._knob_modes_enabled)
            self.random_preview_show_button.setEnabled(
                self._knob_modes_enabled and bool(self._random_preview_text)
            )
            if not self._knob_modes_enabled and self.task_mode() != "timed_acquisition":
                self.set_task_mode("timed_acquisition")
            if not self._knob_modes_enabled:
                self._update_single_scope_summary(None)
            self._update_random_summary()

        def _update_random_summary(self) -> None:
            total = len(self._random_knob_state)
            enabled = sum(1 for row in self._random_knob_state.values() if row.get("enabled", True))
            if total <= 0:
                self.random_config_summary_label.setText(
                    "Choose control PVs to enable Multi-Knob."
                )
                self.random_scope_label.setText(
                    "Writes: all enabled control PVs together at each point. Disabled control PVs stay fixed."
                )
                return
            ready_ranges = 0
            for row in self._random_knob_state.values():
                if not row.get("enabled", True):
                    continue
                try:
                    low = float(row.get("low_text", ""))
                    high = float(row.get("high_text", ""))
                except (TypeError, ValueError):
                    continue
                if low < high:
                    ready_ranges += 1
            if enabled <= 0:
                self.random_config_summary_label.setText("No control PVs enabled")
                self.random_scope_label.setText(
                    "No control PVs are enabled yet. Multi-Knob only writes the rows enabled in 'Configure Ranges...'."
                )
            else:
                self.random_config_summary_label.setText(
                    f"{enabled} enabled · {ready_ranges}/{enabled} ranges ready"
                )
                self.random_scope_label.setText(
                    f"Writes: {enabled} enabled control PV(s) together at each scan point. "
                    "Disabled control PVs stay fixed."
                )

        def _update_single_scope_summary(self, knob) -> None:
            total_selected = len(self._knobs_by_id)
            if knob is None:
                self.single_scope_label.clear()
                self.single_scope_label.setVisible(False)
                return
            other_count = max(total_selected - 1, 0)
            if other_count <= 0:
                self.single_scope_label.clear()
                self.single_scope_label.setVisible(False)
                return
            self.single_scope_label.setText(
                f"Only {knob.name} will move; the other {other_count} selected control PV(s) stay fixed."
            )
            self.single_scope_label.setVisible(True)

        def _show_text_dialog(self, title: str, text: str) -> None:
            if not text:
                return
            dialog = QtWidgets.QDialog(self)
            dialog.setWindowTitle(title)
            dialog.resize(560, 420)
            layout = QtWidgets.QVBoxLayout(dialog)
            edit = QtWidgets.QPlainTextEdit(dialog)
            edit.setReadOnly(True)
            edit.setPlainText(text)
            layout.addWidget(edit)
            buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close, dialog)
            buttons.rejected.connect(dialog.reject)
            buttons.accepted.connect(dialog.accept)
            buttons.button(QtWidgets.QDialogButtonBox.Close).clicked.connect(dialog.accept)
            layout.addWidget(buttons)
            dialog.exec_()

else:

    class ScanPanel:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create ScanPanel")
