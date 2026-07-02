from __future__ import annotations

from pathlib import Path
from datetime import datetime
import sys
import math
import json

try:
    from PyQt5 import QtCore, QtWidgets
except ImportError:  # pragma: no cover - optional runtime dependency
    QtCore = None
    QtWidgets = None

if __package__ in {None, ""}:
    SRC_ROOT = Path(__file__).resolve().parents[2]
    if str(SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(SRC_ROOT))

    from jitter_analysis.config.loader import load_config
    from jitter_analysis.analysis.correlation import compute_correlation_matrix
    from jitter_analysis.analysis.jitter import compute_jitter_stats
    from jitter_analysis.analysis.sensitivity import compute_single_knob_sensitivity
    from jitter_analysis.acquisition.sampler import AcquisitionSampler
    from jitter_analysis.acquisition.workers import KnobScanWorker, MultiKnobRandomWorker, TimedAcquisitionWorker
    from jitter_analysis.domain.types import (
        MultiKnobStepRecord,
        RunMode,
        RunResult,
        RunStatus,
        SampleRecord,
        ScanStepRecord,
        TimedRunSeriesSnapshot,
        WaveformRecord,
    )
    from jitter_analysis.epics.client import PyEpicsClient, require_pyepics
    from jitter_analysis.gui.dialogs.pv_selector_dialog import PVSelectorDialog
    from jitter_analysis.gui.dialogs.random_knob_config_dialog import RandomKnobConfigDialog
    from jitter_analysis.gui.dialogs.run_browser_dialog import RunBrowserDialog
    from jitter_analysis.gui.dialogs.setup_browser_dialog import SetupBrowserDialog
    from jitter_analysis.gui.config_snapshot import config_snapshot_text
    from jitter_analysis.gui.view_logic import (
        analysis_mode_key,
        connection_summary,
        estimate_series_sample_interval,
        estimate_series_sample_interval_from_sample_indices,
        jitter_filter_status_text,
        mode_display_name,
        mode_help_text,
        mode_ready_state,
        mode_key_from_run_mode,
        progress_tone,
        run_status_tone,
        single_knob_axis_name,
        single_knob_axis_summary_text,
        single_knob_step_axis_value,
    )
    from jitter_analysis.gui.scan_logic import (
        collect_random_knob_ranges,
        generate_random_targets,
        generate_values_by_points,
        generate_values_by_step,
        parse_manual_scan_values,
        random_preview_payload,
        resolve_random_seed,
        single_knob_preview_payload,
    )
    from jitter_analysis.gui.series_logic import (
        filtered_series_payload,
        series_sample_indices,
        series_step_indices,
    )
    from jitter_analysis.gui.selection_logic import normalize_selection_for_available_pvs
    from jitter_analysis.gui.run_logic import (
        current_record_count,
        has_run_data,
        loaded_run_object_count_hint,
        loaded_run_parameter_updates,
        resolve_loaded_run_selection,
        run_browser_scope_kind,
        validate_loaded_run_config,
    )
    from jitter_analysis.gui.setup_logic import list_saved_setups
    from jitter_analysis.gui.waveform_analysis import WaveformAnalysisWorker
    from jitter_analysis.gui.waveform_logic import (
        group_waveform_index_entries,
        has_waveform_data,
        waveform_counts_signature,
        waveform_ids_in_current_run,
        waveform_max_length_hint,
        waveform_record_counts,
    )
    from jitter_analysis.gui.plots.correlation_plot import CorrelationPlot
    from jitter_analysis.gui.plots.jitter_plot import JitterPlot
    from jitter_analysis.gui.plots.response_plot import ResponsePlot
    from jitter_analysis.gui.plots.sensitivity_plot import SensitivityPlot
    from jitter_analysis.gui.plots.spectrum_plot import SpectrumPlot
    from jitter_analysis.gui.plots.theme import apply_plot_theme, style_plot_widgets_in_tree
    from jitter_analysis.gui.theme import apply_app_theme, current_theme_id, theme_label
    from jitter_analysis.gui.plots.trend_plot import TrendPlot
    from jitter_analysis.gui.plots.waveform_plot import WaveformPlot
    from jitter_analysis.gui.state import AppState
    from jitter_analysis.gui.widgets.config_panel import ConfigPanel
    from jitter_analysis.gui.widgets.object_panel import ObjectPanel
    from jitter_analysis.gui.widgets.scan_panel import ScanPanel
    from jitter_analysis.gui.widgets.status_panel import StatusPanel
    from jitter_analysis.services.run_service import RunService
    from jitter_analysis.services.task_service import TaskService
else:
    from ..config.loader import load_config
    from ..analysis.correlation import compute_correlation_matrix
    from ..analysis.jitter import compute_jitter_stats
    from ..analysis.sensitivity import compute_single_knob_sensitivity
    from ..acquisition.sampler import AcquisitionSampler
    from ..acquisition.workers import KnobScanWorker, MultiKnobRandomWorker, TimedAcquisitionWorker
    from ..domain.types import (
        MultiKnobStepRecord,
        RunMode,
        RunResult,
        RunStatus,
        SampleRecord,
        ScanStepRecord,
        TimedRunSeriesSnapshot,
        WaveformRecord,
    )
    from ..epics.client import PyEpicsClient, require_pyepics
    from .dialogs.pv_selector_dialog import PVSelectorDialog
    from .dialogs.random_knob_config_dialog import RandomKnobConfigDialog
    from .dialogs.run_browser_dialog import RunBrowserDialog
    from .dialogs.setup_browser_dialog import SetupBrowserDialog
    from .config_snapshot import config_snapshot_text
    from .view_logic import (
        analysis_mode_key,
        connection_summary,
        estimate_series_sample_interval,
        estimate_series_sample_interval_from_sample_indices,
        jitter_filter_status_text,
        mode_display_name,
        mode_help_text,
        mode_ready_state,
        mode_key_from_run_mode,
        progress_tone,
        run_status_tone,
        single_knob_axis_name,
        single_knob_axis_summary_text,
        single_knob_step_axis_value,
    )
    from .scan_logic import (
        collect_random_knob_ranges,
        generate_random_targets,
        generate_values_by_points,
        generate_values_by_step,
        parse_manual_scan_values,
        random_preview_payload,
        resolve_random_seed,
        single_knob_preview_payload,
    )
    from .series_logic import (
        filtered_series_payload,
        series_sample_indices,
        series_step_indices,
    )
    from .selection_logic import normalize_selection_for_available_pvs
    from .run_logic import (
        current_record_count,
        has_run_data,
        loaded_run_object_count_hint,
        loaded_run_parameter_updates,
        resolve_loaded_run_selection,
        run_browser_scope_kind,
        validate_loaded_run_config,
    )
    from .setup_logic import list_saved_setups
    from .waveform_analysis import WaveformAnalysisWorker
    from .waveform_logic import (
        group_waveform_index_entries,
        has_waveform_data,
        waveform_counts_signature,
        waveform_ids_in_current_run,
        waveform_max_length_hint,
        waveform_record_counts,
    )
    from .plots.correlation_plot import CorrelationPlot
    from .plots.jitter_plot import JitterPlot
    from .plots.response_plot import ResponsePlot
    from .plots.sensitivity_plot import SensitivityPlot
    from .plots.spectrum_plot import SpectrumPlot
    from .plots.theme import apply_plot_theme, style_plot_widgets_in_tree
    from .theme import apply_app_theme, current_theme_id, theme_label
    from .plots.trend_plot import TrendPlot
    from .plots.waveform_plot import WaveformPlot
    from .state import AppState
    from .widgets.config_panel import ConfigPanel
    from .widgets.object_panel import ObjectPanel
    from .widgets.scan_panel import ScanPanel
    from .widgets.status_panel import StatusPanel
    from ..services.run_service import RunService
    from ..services.task_service import TaskService


def require_qt():
    if QtWidgets is None:
        raise RuntimeError("PyQt5 is required to launch the GUI")
    return QtWidgets


if QtWidgets is not None:
    _MainWindowBase = QtWidgets.QMainWindow
else:  # pragma: no cover - fallback for non-GUI tests
    _MainWindowBase = object


class MainWindow(_MainWindowBase):
    def __init__(self, state: AppState, run_service, task_service) -> None:
        if QtWidgets is None:
            raise RuntimeError("PyQt5 is required to create MainWindow")
        super().__init__()
        self.state = state
        self.run_service = run_service
        self.task_service = task_service
        self.loaded_config = None
        self.epics_client = PyEpicsClient()
        self.sampler = AcquisitionSampler(self.epics_client)
        self.acquisition_thread = None
        self.acquisition_worker = None
        self.current_run_metadata = None
        self.current_run_records = []
        self.current_run_steps = []
        self.current_run_record_count = 0
        self.current_run_sample_timestamps = []
        self.current_series_values = {}
        self.current_series_metadata = {}
        self.current_waveform_records = {}
        self.current_waveform_index = {}
        self.current_waveform_run_path = None
        self.current_run_details = {}
        self.current_run_mode = None
        self._last_connection_key = None
        self._has_analysis_data = False
        self._has_sensitivity_data = False
        self._has_waveform_data = False
        self._single_knob_axis_source = "readback"
        self._viewing_saved_run = False
        self._loaded_run_used_fast_path = False
        self._loaded_run_used_legacy_batch_reconstruction = False
        self._initial_window_position_applied = False
        self._analysis_tab_loaded: dict[int, bool] = {}
        self._analysis_tab_loading: int | None = None
        self._analysis_tab_loaders: dict[int, object] = {}
        self._waveform_analysis_thread = None
        self._waveform_analysis_worker = None
        self._waveform_analysis_signature = None
        self._waveform_analysis_inflight_signature = None
        self._waveform_analysis_result = {}
        self._pending_waveform_analysis_signature = None
        apply_plot_theme(current_theme_id(QtWidgets.QApplication.instance()))
        self.setWindowTitle("Jitter Analysis")
        self.resize(1540, 900)
        self._build_ui()
        self._apply_plot_widget_theme(current_theme_id(QtWidgets.QApplication.instance()))
        self._wire_actions()
        self._try_load_default_config()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._initial_window_position_applied:
            return
        self._initial_window_position_applied = True
        QtCore.QTimer.singleShot(0, self._ensure_window_visible_on_screen)

    def _ensure_window_visible_on_screen(self) -> None:
        if self.isMaximized() or self.isFullScreen():
            return

        app = QtWidgets.QApplication.instance()
        desktop = app.desktop() if app is not None else None
        if desktop is None:
            return

        available = desktop.availableGeometry(self)
        if not available.isValid():
            return

        width = min(self.width(), available.width())
        height = min(self.height(), available.height())
        if width != self.width() or height != self.height():
            self.resize(width, height)

        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        left = frame.left()
        top = frame.top()

        if frame.width() <= available.width():
            left = max(available.left(), min(left, available.right() - frame.width() + 1))
        else:
            left = available.left()

        if frame.height() <= available.height():
            top = max(available.top(), min(top, available.bottom() - frame.height() + 1))
        else:
            top = available.top()

        self.move(left, top)

    def _build_ui(self) -> None:
        self._build_actions()
        self._build_menu_bar()

        central = QtWidgets.QWidget()
        central.setObjectName("appShell")
        root_layout = QtWidgets.QVBoxLayout(central)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(10)
        self.scan_panel = ScanPanel()
        self._create_run_action_buttons()
        self.main_tabs = QtWidgets.QTabWidget()
        self.main_tabs.setObjectName("workspaceTabs")
        self.main_tabs.setDocumentMode(True)
        self.main_tabs.setMovable(False)
        self.main_tabs.tabBar().setObjectName("workspaceTabBar")
        set_draw_base = getattr(self.main_tabs.tabBar(), "setDrawBase", None)
        if callable(set_draw_base):
            set_draw_base(False)
        self.config_tab_index = self.main_tabs.addTab(self._build_config_tab(), "Config")
        self.run_tab_index = self.main_tabs.addTab(self._build_run_tab(), "Run")
        self.analysis_page_index = self.main_tabs.addTab(self._build_analysis_page(), "Analysis")
        self.main_tabs.setCurrentIndex(self.config_tab_index)

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("Warnings, caput results, timeout, disconnected PVs")
        self.log_view.setMaximumBlockCount(1000)
        self.log_toggle_button = self._build_log_toggle_button()

        workspace_frame = QtWidgets.QFrame()
        workspace_frame.setObjectName("workspaceFrame")
        workspace_layout = QtWidgets.QVBoxLayout(workspace_frame)
        workspace_layout.setContentsMargins(10, 10, 10, 10)
        workspace_layout.setSpacing(8)
        workspace_layout.addWidget(self.main_tabs, 1)
        workspace_layout.addWidget(self._build_log_section())

        root_layout.addWidget(self._build_app_header())
        root_layout.addWidget(workspace_frame, 1)
        self.setCentralWidget(central)
        self._sync_theme_actions()
        self.append_log("Application scaffold initialized")

    def _build_actions(self):
        self.action_connect_epics = QtWidgets.QAction("Check EPICS", self)
        self.action_start = QtWidgets.QAction("Start", self)
        self.action_stop = QtWidgets.QAction("Stop", self)

    def _build_theme_toggle_button(self):
        self.theme_toggle_button = QtWidgets.QToolButton(self)
        self.theme_toggle_button.setObjectName("themeToggleButton")
        self.theme_toggle_button.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        self.theme_toggle_button.setAutoRaise(False)
        self.theme_toggle_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.theme_toggle_button.setFixedSize(32, 32)
        button_font = self.theme_toggle_button.font()
        button_font.setPointSize(max(int(button_font.pointSize()), 13))
        self.theme_toggle_button.setFont(button_font)
        self.theme_toggle_button.clicked.connect(self._toggle_gui_theme)
        return self.theme_toggle_button

    def _build_log_toggle_button(self):
        button = QtWidgets.QToolButton(self)
        button.setObjectName("logToggleButton")
        button.setText("Log")
        button.setCheckable(True)
        button.setChecked(False)
        button.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        button.setAutoRaise(False)
        button.setCursor(QtCore.Qt.PointingHandCursor)
        button.setFixedSize(44, 32)
        button.setToolTip("Show log")
        button.setAccessibleName("Show log")
        button.toggled.connect(self._toggle_log_view)
        return button

    def _create_run_action_buttons(self) -> None:
        self.run_check_button = QtWidgets.QPushButton("Check EPICS")
        self.run_start_button = QtWidgets.QPushButton("Start")
        self.run_stop_button = QtWidgets.QPushButton("Stop")
        button_specs = (
            (self.run_check_button, 112, "info"),
            (self.run_start_button, 82, "control"),
            (self.run_stop_button, 82, "danger"),
        )
        for button, width, tone in button_specs:
            button.setFixedSize(width, 30)
            button.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
            button.setProperty("role", "statusAction")
            button.setProperty("tone", tone)
            button.setStyleSheet("")
            button.style().unpolish(button)
            button.style().polish(button)

    def _build_run_status_controls(self):
        controls = QtWidgets.QWidget()
        controls.setObjectName("runStatusControls")
        layout = QtWidgets.QHBoxLayout(controls)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.run_check_button)
        layout.addWidget(self.run_start_button)
        layout.addWidget(self.run_stop_button)
        return controls

    def _build_app_header(self):
        header = QtWidgets.QFrame()
        header.setObjectName("appHeader")
        layout = QtWidgets.QHBoxLayout(header)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        title_column = QtWidgets.QVBoxLayout()
        title_column.setContentsMargins(0, 0, 0, 0)
        title_column.setSpacing(2)

        app_title = QtWidgets.QLabel("Jitter Analysis")
        app_title.setObjectName("appTitle")
        app_subtitle = QtWidgets.QLabel("Status: Development / Internal Use  |  PV jitter workspace")
        app_subtitle.setObjectName("appSubtitle")

        title_column.addWidget(app_title)
        title_column.addWidget(app_subtitle)
        layout.addLayout(title_column, 1)

        layout.addWidget(self.log_toggle_button, 0, QtCore.Qt.AlignVCenter)
        layout.addWidget(self._build_theme_toggle_button(), 0, QtCore.Qt.AlignVCenter)
        return header

    def _apply_action_button_role(self, button, role: str) -> None:
        button.setProperty("role", role)
        button.setStyleSheet("")
        button.style().unpolish(button)
        button.style().polish(button)

    def _build_menu_bar(self) -> None:
        menu_bar = self.menuBar()
        menu_bar.clear()
        menu_bar.setVisible(False)

    def _toggle_gui_theme(self) -> None:
        active_theme_id = current_theme_id(QtWidgets.QApplication.instance())
        next_theme_id = "control_room" if active_theme_id == "night_shift" else "night_shift"
        self._set_gui_theme(next_theme_id)

    def _set_gui_theme(self, theme_id: str) -> None:
        app = QtWidgets.QApplication.instance()
        apply_app_theme(app, theme_id)
        apply_plot_theme(theme_id)
        self._apply_plot_widget_theme(theme_id)
        self._sync_theme_actions()
        self.append_log(f"GUI theme set to {theme_label(theme_id)}.")

    def _apply_plot_widget_theme(self, theme_id: str | None = None) -> None:
        style_plot_widgets_in_tree(self, theme_id)

    def _sync_theme_actions(self) -> None:
        active_theme_id = current_theme_id(QtWidgets.QApplication.instance())
        if not hasattr(self, "theme_toggle_button"):
            return
        if active_theme_id == "night_shift":
            next_theme_id = "control_room"
            toggle_icon = "☀"
        else:
            next_theme_id = "night_shift"
            toggle_icon = "☽"
        self.theme_toggle_button.setText(toggle_icon)
        self.theme_toggle_button.setAccessibleName(f"Switch to {theme_label(next_theme_id)} theme")
        self.theme_toggle_button.setToolTip(
            f"Switch to {theme_label(next_theme_id)} theme."
        )

    def _build_left_panel(self):
        panel = QtWidgets.QWidget()
        panel.setMinimumWidth(390)
        panel.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.config_panel = ConfigPanel()
        self.object_panel = ObjectPanel()
        self._apply_panel_button_roles()

        run_info_box = self._wrap_setup_section("Run Info", self.config_panel)
        pv_selection_box = self._wrap_setup_section("PV Selection", self.object_panel)
        run_info_box.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)
        pv_selection_box.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)

        layout.addWidget(run_info_box, 1)
        layout.addWidget(pv_selection_box, 1)
        return panel

    def _apply_panel_button_roles(self) -> None:
        for button in (
            self.config_panel.load_button,
            self.config_panel.load_setup_button,
            self.config_panel.save_setup_button,
            self.config_panel.save_dir_browse_button,
            self.object_panel.select_button,
        ):
            self._apply_action_button_role(button, "diagnostic")
            button.setMinimumHeight(40)
        self._apply_action_button_role(self.object_panel.clear_button, "danger")
        self.object_panel.clear_button.setMinimumHeight(40)

    def _build_mode_switcher(self):
        box = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        hint = QtWidgets.QLabel("Select a task type.")
        hint.setWordWrap(True)
        hint.setProperty("role", "pageHint")
        layout.addWidget(hint)

        row = QtWidgets.QHBoxLayout()
        self.mode_buttons = {}
        self.mode_button_group = QtWidgets.QButtonGroup(self)
        self.mode_button_group.setExclusive(True)
        for label, mode in (
            ("Monitor", "timed_acquisition"),
            ("Single Knob", "single_knob_scan"),
            ("Random Multi-Knob", "multi_knob_random"),
        ):
            button = QtWidgets.QToolButton()
            button.setText(label)
            button.setCheckable(True)
            button.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
            button.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
            button.setMinimumHeight(34)
            button.setCursor(QtCore.Qt.PointingHandCursor)
            button.setProperty("themeRole", "modeToggle")
            row.addWidget(button)
            self.mode_buttons[mode] = button
            self.mode_button_group.addButton(button)
        layout.addLayout(row)
        layout.addWidget(self._build_mode_compare_strip())
        return box

    def _build_mode_compare_strip(self):
        label = QtWidgets.QLabel(
            "Monitor = read PVs only   |   Single Knob = one control PV   |   "
            "Random Multi-Knob = many control PVs together"
        )
        label.setWordWrap(True)
        label.setProperty("role", "pageHint")
        return label

    def _create_section_group(self, title: str) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox(title)
        box.setProperty("themeSection", "main")
        return box

    def _create_page_hint(self, text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setWordWrap(True)
        label.setProperty("role", "pageHint")
        return label

    def _wrap_setup_section(self, title: str, child):
        box = self._create_section_group(title)
        layout = QtWidgets.QVBoxLayout(box)
        layout.setContentsMargins(8, 12, 8, 8)
        layout.addWidget(child)
        return box

    def _build_config_tab(self):
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_task_setup_group())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([400, 1000])
        layout.addWidget(splitter, 1)
        return container

    def _build_run_tab(self):
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        box = self._create_section_group("Run")
        inner = QtWidgets.QVBoxLayout(box)
        inner.setContentsMargins(12, 14, 12, 12)
        inner.setSpacing(10)

        self.status_panel = StatusPanel()
        self.status_panel.add_trailing_widget(self._build_run_status_controls())
        inner.addWidget(self.status_panel)

        self.trend_plot = TrendPlot()
        inner.addWidget(self.trend_plot, 1)
        layout.addWidget(box, 1)
        return container

    def _build_analysis_page(self):
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        box = QtWidgets.QFrame()
        box.setObjectName("analysisSection")
        inner = QtWidgets.QVBoxLayout(box)
        inner.setContentsMargins(12, 10, 12, 12)
        inner.setSpacing(10)

        section_header = QtWidgets.QHBoxLayout()
        section_header.setContentsMargins(4, 0, 0, 0)
        section_header.setSpacing(8)
        section_title = QtWidgets.QLabel("Analysis")
        section_title.setObjectName("analysisSectionTitle")
        section_header.addWidget(section_title, 0, QtCore.Qt.AlignVCenter)
        section_header.addStretch(1)
        self.analysis_open_run_button = QtWidgets.QPushButton("Run Browser")
        self._apply_action_button_role(self.analysis_open_run_button, "diagnostic")
        self.analysis_open_run_button.setProperty("compactControl", "true")
        self.analysis_open_run_button.setFixedSize(128, 28)
        section_header.addWidget(self.analysis_open_run_button, 0, QtCore.Qt.AlignVCenter)
        inner.addLayout(section_header)

        self.analysis_tabs = QtWidgets.QTabWidget()
        self.analysis_tabs.setObjectName("analysisTabs")
        self.analysis_tabs.setDocumentMode(True)
        self.analysis_tabs.setMovable(False)
        set_scroll_buttons = getattr(self.analysis_tabs, "setUsesScrollButtons", None)
        if callable(set_scroll_buttons):
            set_scroll_buttons(False)
        analysis_tab_bar = self.analysis_tabs.tabBar()
        analysis_tab_bar.setObjectName("analysisTabBar")
        set_analysis_draw_base = getattr(analysis_tab_bar, "setDrawBase", None)
        if callable(set_analysis_draw_base):
            set_analysis_draw_base(False)
        analysis_tab_bar.setElideMode(QtCore.Qt.ElideNone)
        analysis_tab_bar.setExpanding(True)

        corner = QtWidgets.QWidget()
        corner_layout = QtWidgets.QHBoxLayout(corner)
        corner_layout.setContentsMargins(8, 0, 0, 0)
        corner_layout.setSpacing(8)
        self.analysis_axis_label = QtWidgets.QLabel("X Axis")
        self.analysis_axis_label.setProperty("role", "field")
        corner_layout.addWidget(self.analysis_axis_label, 0)
        self.analysis_axis_combo = QtWidgets.QComboBox()
        self.analysis_axis_combo.addItem("Readback", "readback")
        self.analysis_axis_combo.addItem("Target", "target")
        self.analysis_axis_combo.setFixedHeight(32)
        self.analysis_axis_combo.setMinimumWidth(118)
        self.analysis_axis_combo.setToolTip(
            "Choose whether Single Knob response and sensitivity use knob readback or target values on the x-axis."
        )
        corner_layout.addWidget(self.analysis_axis_combo, 0)
        self.analysis_tabs.setCornerWidget(corner, QtCore.Qt.TopRightCorner)

        self.response_plot = ResponsePlot()
        self.response_tab_index = self.analysis_tabs.addTab(self.response_plot, "Response")
        self.waveform_plot = WaveformPlot()
        self.waveform_tab_index = self.analysis_tabs.addTab(self.waveform_plot, "Waveform")
        self.sensitivity_plot = SensitivityPlot()
        self.sensitivity_tab_index = self.analysis_tabs.addTab(self.sensitivity_plot, "Sensitivity")
        self.jitter_tab_index = self.analysis_tabs.addTab(self._build_jitter_summary(), "Jitter")
        self.correlation_plot = CorrelationPlot()
        self.correlation_tab_index = self.analysis_tabs.addTab(self.correlation_plot, "Correlation")
        self.spectrum_plot = SpectrumPlot()
        self.spectrum_tab_index = self.analysis_tabs.addTab(self.spectrum_plot, "Spectrum")
        self._analysis_tab_loaders = {
            self.waveform_tab_index: self._populate_waveform_view,
            self.jitter_tab_index: self._populate_jitter_table,
            self.sensitivity_tab_index: self._populate_sensitivity_view,
            self.correlation_tab_index: self._populate_correlation_view,
            self.spectrum_tab_index: self._populate_spectrum_view,
        }
        self._mark_analysis_tabs_dirty()
        inner.addWidget(self.analysis_tabs, 1)
        layout.addWidget(box, 1)
        return container

    def _build_analysis_outlier_filter_panel(self):
        frame = QtWidgets.QWidget()
        frame.setObjectName("analysisOutlierFilterPanel")
        frame.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        layout = QtWidgets.QHBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.jitter_outlier_filter_check = QtWidgets.QCheckBox("Filter Outliers")
        self.jitter_outlier_filter_check.setFixedHeight(28)
        self.jitter_outlier_filter_check.setToolTip(
            "Monitor only. Exclude extreme points from Jitter, Correlation, and Spectrum using a robust "
            "z-score computed from the series median and MAD."
        )
        layout.addWidget(self.jitter_outlier_filter_check, 0)
        self.jitter_outlier_filter_spin = QtWidgets.QDoubleSpinBox()
        self.jitter_outlier_filter_spin.setRange(0.5, 1000.0)
        self.jitter_outlier_filter_spin.setDecimals(1)
        self.jitter_outlier_filter_spin.setValue(10.0)
        self.jitter_outlier_filter_spin.setSingleStep(0.5)
        self.jitter_outlier_filter_spin.setSuffix(" sigma")
        self.jitter_outlier_filter_spin.setEnabled(False)
        self.jitter_outlier_filter_spin.setFixedHeight(28)
        self.jitter_outlier_filter_spin.setMinimumWidth(118)
        self.jitter_outlier_filter_spin.setToolTip(
            "Monitor only. Points whose robust z-score exceeds this threshold are excluded from "
            "Jitter, Correlation, and Spectrum."
        )
        layout.addWidget(self.jitter_outlier_filter_spin, 0)
        return frame

    def _build_task_setup_group(self):
        box = self._create_section_group("Task Setup")
        layout = QtWidgets.QVBoxLayout(box)
        layout.addWidget(self._build_mode_switcher())
        layout.addWidget(self._build_mode_status_banner())
        layout.addWidget(self.scan_panel)
        return box

    def _build_mode_status_banner(self):
        frame = QtWidgets.QFrame()
        frame.setObjectName("modeStatusBanner")
        frame.setProperty("tone", "subtle")
        layout = QtWidgets.QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        self.mode_status_title_label = QtWidgets.QLabel("Task Guidance")
        self.mode_status_title_label.setProperty("role", "title")
        self.mode_status_message_label = QtWidgets.QLabel("Load a PV library to begin.")
        self.mode_status_message_label.setWordWrap(True)
        self.mode_status_message_label.setProperty("role", "message")
        self.mode_status_context_label = QtWidgets.QLabel("New runs will be saved under runs.")
        self.mode_status_context_label.setWordWrap(True)
        self.mode_status_context_label.setProperty("role", "context")

        layout.addWidget(self.mode_status_title_label)
        layout.addWidget(self.mode_status_message_label)
        layout.addWidget(self.mode_status_context_label)
        return frame

    def _build_jitter_summary(self):
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.jitter_table = QtWidgets.QTableWidget(0, 9)
        self.jitter_table.setHorizontalHeaderLabels(
            ["PV", "Count", "Mean", "Std", "Jitter RMS", "P2P", "Min", "Max", "Unit"]
        )
        self.jitter_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.jitter_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.jitter_table.setAlternatingRowColors(True)
        self.jitter_table.verticalHeader().setVisible(False)
        self._apply_jitter_table_column_layout()
        layout.addWidget(self.jitter_table)
        self.jitter_plot = JitterPlot()
        self.analysis_outlier_filter_panel = self._build_analysis_outlier_filter_panel()
        self.jitter_plot.add_trailing_control_widget(self.analysis_outlier_filter_panel)
        layout.addWidget(self.jitter_plot, 1)
        return widget

    def _apply_jitter_table_column_layout(self) -> None:
        header = self.jitter_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(54)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        fixed_widths = {
            1: 70,
            2: 92,
            3: 86,
            4: 104,
            5: 80,
            6: 82,
            7: 82,
            8: 64,
        }
        for column, width in fixed_widths.items():
            header.setSectionResizeMode(column, QtWidgets.QHeaderView.Fixed)
            self.jitter_table.setColumnWidth(column, width)

    def _build_placeholder(self, text: str):
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        label = QtWidgets.QLabel(text)
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addStretch(1)
        return widget

    def _build_log_section(self):
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.log_container = QtWidgets.QWidget()
        log_layout = QtWidgets.QVBoxLayout(self.log_container)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.addWidget(self.log_view)
        self.log_container.setVisible(False)

        layout.addWidget(self.log_container)
        return widget

    def _toggle_log_view(self, visible: bool) -> None:
        self.log_toggle_button.setText("Log")
        self.log_toggle_button.setToolTip("Hide log" if visible else "Show log")
        self.log_toggle_button.setAccessibleName("Hide log" if visible else "Show log")
        self.log_toggle_button.setProperty("active", "true" if visible else "false")
        self.log_toggle_button.style().unpolish(self.log_toggle_button)
        self.log_toggle_button.style().polish(self.log_toggle_button)
        self.log_container.setVisible(visible)

    def append_log(self, message: str) -> None:
        self.log_view.appendPlainText(message)

    def _on_jitter_table_changed(self, current_row: int, _current_col: int, _old_row: int, _old_col: int) -> None:
        if current_row < 0:
            return
        item = self.jitter_table.item(current_row, 0)
        if item is None:
            return
        pv_id = item.data(QtCore.Qt.UserRole)
        if pv_id:
            self.jitter_plot.select_pv_id(str(pv_id))

    def _analysis_outlier_filter_available(self) -> bool:
        return self._analysis_mode_key(self.scan_panel.task_mode()) == "timed_acquisition"

    def _refresh_analysis_outlier_filter_affordances(self) -> None:
        available = self._analysis_outlier_filter_available()
        self.analysis_outlier_filter_panel.setVisible(available)
        self.jitter_outlier_filter_check.setEnabled(available)
        self.jitter_outlier_filter_spin.setEnabled(available and bool(self.jitter_outlier_filter_check.isChecked()))
        self._update_jitter_filter_status()

    def _jitter_outlier_filter_enabled(self) -> bool:
        return self._analysis_outlier_filter_available() and bool(self.jitter_outlier_filter_check.isChecked())

    def _jitter_outlier_filter_threshold(self) -> float:
        return float(self.jitter_outlier_filter_spin.value())

    def _update_jitter_filter_status(
        self,
        total_removed: int | None = None,
        affected_variables: int | None = None,
    ) -> None:
        if getattr(self, "jitter_filter_status_label", None) is None:
            return
        message = jitter_filter_status_text(
            available=self._analysis_outlier_filter_available(),
            enabled=self._jitter_outlier_filter_enabled(),
            threshold=self._jitter_outlier_filter_threshold(),
            total_removed=total_removed,
            affected_variables=affected_variables,
        )
        self.jitter_filter_status_label.setText(message)

    def _on_jitter_filter_toggled(self, enabled: bool) -> None:
        self.jitter_outlier_filter_spin.setEnabled(self._analysis_outlier_filter_available() and bool(enabled))
        self._on_jitter_filter_changed()

    def _on_jitter_filter_changed(self, *_args) -> None:
        self._update_jitter_filter_status()
        if self.loaded_config is None or not self._has_current_run_data():
            return
        self._analysis_tab_loaded[self.jitter_tab_index] = False
        self._analysis_tab_loaded[self.correlation_tab_index] = False
        self._analysis_tab_loaded[self.spectrum_tab_index] = False
        if self.main_tabs.currentIndex() == self.analysis_page_index:
            current_tab = self.analysis_tabs.currentIndex()
            if current_tab in {self.jitter_tab_index, self.correlation_tab_index, self.spectrum_tab_index}:
                self._ensure_analysis_tab_loaded(current_tab)

    def _on_analysis_tab_changed(self, index: int) -> None:
        self._ensure_analysis_tab_loaded(index)

    def _on_main_tab_changed(self, index: int) -> None:
        if index == self.analysis_page_index:
            self._ensure_visible_analysis_tab_loaded()

    def _wire_actions(self) -> None:
        self.action_connect_epics.triggered.connect(self.check_selected_connections)
        self.action_start.triggered.connect(self.start_selected_mode)
        self.action_stop.triggered.connect(self.stop_active_run)
        self.run_check_button.clicked.connect(self.check_selected_connections)
        self.run_start_button.clicked.connect(self.start_selected_mode)
        self.run_stop_button.clicked.connect(self.stop_active_run)
        self.analysis_open_run_button.clicked.connect(self.open_run_browser)
        self.analysis_axis_combo.currentIndexChanged.connect(self._on_single_knob_axis_changed)
        self.analysis_tabs.currentChanged.connect(self._on_analysis_tab_changed)
        self.main_tabs.currentChanged.connect(self._on_main_tab_changed)

        self.config_panel.load_button.clicked.connect(self.load_config_file)
        self.config_panel.load_setup_button.clicked.connect(self.open_setup_browser)
        self.config_panel.save_setup_button.clicked.connect(self.save_setup_file)
        self.config_panel.save_dir_browse_button.clicked.connect(self.browse_save_dir)
        self.config_panel.save_dir_edit.textChanged.connect(self._on_save_dir_text_changed)
        self.object_panel.select_button.clicked.connect(self.open_pv_selector)
        self.object_panel.clear_button.clicked.connect(self.clear_selected_pvs)
        self.jitter_table.currentCellChanged.connect(self._on_jitter_table_changed)
        self.jitter_outlier_filter_check.toggled.connect(self._on_jitter_filter_toggled)
        self.jitter_outlier_filter_spin.valueChanged.connect(self._on_jitter_filter_changed)
        self.scan_panel.active_knob_combo.currentIndexChanged.connect(self._sync_knob_from_combo)
        self.scan_panel.random_config_button.clicked.connect(self.open_random_knob_config_dialog)
        self.scan_panel.random_preview_button.clicked.connect(self.refresh_random_preview)
        self.correlation_plot.highlightRequested.connect(self._highlight_correlation_point)
        self.waveform_plot.viewChanged.connect(self._on_waveform_view_changed)
        for mode, button in self.mode_buttons.items():
            button.clicked.connect(lambda checked, target_mode=mode: checked and self._set_ui_task_mode(target_mode))
        self._wire_scan_preview_actions()
        self._sync_mode_buttons()
        self._set_running_state(False)
        self._refresh_ui_affordances()

    def _wire_scan_preview_actions(self) -> None:
        self.scan_panel.preview_refresh_button.clicked.connect(
            lambda: self.refresh_scan_preview(force_live_center=True)
        )
        self.scan_panel.scan_value_mode_combo.currentIndexChanged.connect(self.refresh_scan_preview)
        self.scan_panel.manual_scan_values_edit.textChanged.connect(self.refresh_scan_preview)
        self.scan_panel.range_start_spin.valueChanged.connect(self.refresh_scan_preview)
        self.scan_panel.range_stop_spin.valueChanged.connect(self.refresh_scan_preview)
        self.scan_panel.range_step_spin.valueChanged.connect(self.refresh_scan_preview)
        self.scan_panel.points_start_spin.valueChanged.connect(self.refresh_scan_preview)
        self.scan_panel.points_stop_spin.valueChanged.connect(self.refresh_scan_preview)
        self.scan_panel.points_count_spin.valueChanged.connect(self.refresh_scan_preview)
        self.scan_panel.symmetric_half_range_spin.valueChanged.connect(self.refresh_scan_preview)
        self.scan_panel.symmetric_points_spin.valueChanged.connect(self.refresh_scan_preview)
        self.scan_panel.active_knob_combo.currentIndexChanged.connect(self.refresh_scan_preview)

    def _set_ui_task_mode(self, mode: str) -> None:
        knob_modes_enabled = self.scan_panel.knob_scan_available()
        if mode != "timed_acquisition" and not knob_modes_enabled:
            mode = "timed_acquisition"
        self.scan_panel.set_task_mode(mode)
        self._sync_mode_buttons()
        self._refresh_ui_affordances()

    def _sync_mode_buttons(self) -> None:
        knob_modes_enabled = self.scan_panel.knob_scan_available()
        running = self.state.run_status == RunStatus.RUNNING
        current_mode = self.scan_panel.task_mode()
        if current_mode != "timed_acquisition" and not knob_modes_enabled:
            self.scan_panel.set_task_mode("timed_acquisition")
            current_mode = "timed_acquisition"

        blockers = [QtCore.QSignalBlocker(button) for button in self.mode_buttons.values()]
        try:
            for mode, button in self.mode_buttons.items():
                allowed = mode == "timed_acquisition" or knob_modes_enabled
                button.setEnabled(allowed and not running)
                button.setChecked(mode == current_mode)
        finally:
            del blockers

    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parents[3]

    def _default_config_path(self) -> Path:
        return self._repo_root() / "configs" / "irfel_pvlist.json"

    def _default_runs_dir(self) -> Path:
        return self._repo_root() / "runs"

    def _default_setup_dir(self) -> Path:
        return self._repo_root() / "saved_setups"

    def _try_load_default_config(self) -> None:
        if self.state.config_path:
            self._load_config_path(self.state.config_path)
            return
        default_path = self._default_config_path()
        if default_path.exists():
            self._load_config_path(default_path, quiet=True)

    def load_config_file(self) -> bool:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Load PV Library",
            str(self._repo_root() / "configs"),
            "JSON Files (*.json);;All Files (*)",
        )
        if path:
            return self._load_config_path(path)
        return False

    def browse_save_dir(self) -> bool:
        if self.state.run_status == RunStatus.RUNNING:
            QtWidgets.QMessageBox.warning(
                self,
                "Save Directory",
                "Stop the current run before changing the save directory.",
            )
            return False

        start_dir = self.config_panel.save_dir_edit.text().strip() or str(self._default_runs_dir())
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select Save Directory",
            start_dir,
        )
        if not path:
            return False

        self._leave_saved_run_context()
        self.config_panel.save_dir_edit.setText(path)
        self.append_log(f"Run save directory set to {path}.")
        return True

    def _on_save_dir_text_changed(self, text: str) -> None:
        self.state.save_dir = text.strip() or "runs"
        if self._viewing_saved_run:
            return
        self._refresh_ui_affordances()

    def save_setup_file(self) -> bool:
        if self.state.run_status == RunStatus.RUNNING:
            QtWidgets.QMessageBox.warning(
                self,
                "Save Setup",
                "Stop the current run before saving the current setup.",
            )
            return False

        setup_dir = self._default_setup_dir()
        setup_dir.mkdir(parents=True, exist_ok=True)
        suggested_name = datetime.now().strftime("setup_%Y%m%d_%H%M%S.json")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save Setup",
            str(setup_dir / suggested_name),
            "JSON Files (*.json);;All Files (*)",
        )
        if not path:
            return False

        payload = self._build_setup_payload()
        target = Path(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Save Setup", str(exc))
            self.append_log(f"Failed to save setup: {exc}")
            return False
        self.append_log(f"Saved setup to {target}.")
        return True

    def load_setup_file(self) -> bool:
        if self.state.run_status == RunStatus.RUNNING:
            QtWidgets.QMessageBox.warning(
                self,
                "Load Setup",
                "Stop the current run before loading a saved setup.",
            )
            return False

        setup_dir = self._default_setup_dir()
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Load Setup",
            str(setup_dir if setup_dir.exists() else self._repo_root()),
            "JSON Files (*.json);;All Files (*)",
        )
        return self._load_setup_path(path) if path else False

    def open_setup_browser(self) -> None:
        if self.state.run_status == RunStatus.RUNNING:
            QtWidgets.QMessageBox.warning(
                self,
                "Setup Browser",
                "Stop the current run before loading a saved setup.",
            )
            return

        root_dir = str(self._default_setup_dir())
        dialog = SetupBrowserDialog(root_dir=root_dir, parent=self)
        self._populate_setup_browser(dialog)
        dialog.refresh_button.clicked.connect(lambda: self._populate_setup_browser(dialog))
        dialog.browse_button.clicked.connect(lambda: self._browse_setup_browser_root(dialog))
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return

        selected_path = dialog.selected_setup_path()
        if selected_path:
            self._load_setup_path(selected_path)

    def _browse_setup_browser_root(self, dialog: SetupBrowserDialog) -> None:
        start_dir = dialog.root_dir() or str(self._default_setup_dir())
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select Setup Root",
            start_dir,
        )
        if not path:
            return
        dialog.set_root_dir(path)
        self._populate_setup_browser(dialog)

    def _populate_setup_browser(self, dialog: SetupBrowserDialog) -> None:
        root_dir = dialog.root_dir() or str(self._default_setup_dir())
        dialog.set_root_dir(root_dir)
        entries = list_saved_setups(root_dir, self._mode_display_name)
        dialog.set_setups(entries)
        self.append_log(f"Listed {len(entries)} saved setup(s) in {root_dir}.")

    def _load_setup_path(self, path: str | Path) -> bool:
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Load Setup", str(exc))
            self.append_log(f"Failed to load setup: {exc}")
            return False

        return self._apply_setup_payload(payload, Path(path))

    def _build_setup_payload(self) -> dict[str, object]:
        return {
            "schema_version": "1",
            "saved_at": datetime.now().isoformat(),
            "config_path": self.loaded_config.source_path if self.loaded_config is not None else "",
            "save_dir": self.config_panel.save_dir_edit.text().strip(),
            "operator": self.config_panel.operator_edit.text().strip(),
            "notes": self.config_panel.notes_edit.toPlainText().strip(),
            "selected_object_ids": list(self.state.selected_object_ids),
            "selected_knob_ids": list(self.state.selected_knob_ids),
            "active_knob_id": self.state.active_knob_id,
            "task_mode": self.scan_panel.task_mode(),
            "monitor": self.scan_panel.monitor_configuration(),
            "single_knob": self.scan_panel.single_knob_configuration(),
            "random_multi_knob": {
                **self.scan_panel.random_configuration(),
                "knob_state": self.scan_panel.random_knob_state(),
            },
        }

    def _apply_setup_payload(self, payload: dict[str, object], source_path: Path) -> bool:
        self._leave_saved_run_context()
        config_path = str(payload.get("config_path", "")).strip()
        if config_path:
            if not self._load_config_path(config_path):
                return False
        elif self.loaded_config is None:
            QtWidgets.QMessageBox.warning(
                self,
                "Load Setup",
                "This setup does not record a PV library. Load a PV library first, then reload the setup.",
            )
            return False

        self.config_panel.save_dir_edit.setText(str(payload.get("save_dir", self.config_panel.save_dir_edit.text())))
        self.config_panel.operator_edit.setText(str(payload.get("operator", "")))
        self.config_panel.notes_edit.setPlainText(str(payload.get("notes", "")))
        self.state.save_dir = self.config_panel.save_dir_edit.text().strip() or "runs"

        selected_object_ids = payload.get("selected_object_ids", [])
        selected_knob_ids = payload.get("selected_knob_ids", [])
        self.state.selected_object_ids = [str(item) for item in selected_object_ids if str(item).strip()]
        self.state.selected_knob_ids = [str(item) for item in selected_knob_ids if str(item).strip()]
        active_knob_id = str(payload.get("active_knob_id", "")).strip()
        self.state.active_knob_id = active_knob_id or None
        self._refresh_selected_pvs()

        task_mode = str(payload.get("task_mode", "timed_acquisition")).strip() or "timed_acquisition"
        self._set_ui_task_mode(task_mode)

        monitor_payload = payload.get("monitor")
        if isinstance(monitor_payload, dict):
            self.scan_panel.apply_monitor_configuration(monitor_payload)
        single_payload = payload.get("single_knob")
        if isinstance(single_payload, dict):
            self.scan_panel.apply_single_knob_configuration(single_payload)
        random_payload = payload.get("random_multi_knob")
        if isinstance(random_payload, dict):
            self.scan_panel.apply_random_configuration(random_payload)
            knob_state = random_payload.get("knob_state")
            if isinstance(knob_state, dict):
                self.scan_panel.set_random_knob_state(knob_state)

        self.refresh_scan_preview()
        self.refresh_random_preview()
        self._refresh_ui_affordances()
        self.main_tabs.setCurrentIndex(self.config_tab_index)
        self.append_log(f"Loaded setup from {source_path}.")
        return True

    def open_saved_run(self) -> None:
        if self.state.run_status == RunStatus.RUNNING:
            QtWidgets.QMessageBox.warning(
                self,
                "Open Saved Run",
                "Stop the current run before loading saved data.",
            )
            return

        start_dir = self.config_panel.save_dir_edit.text().strip()
        if not start_dir:
            start_dir = str(self._default_runs_dir())
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Open Saved Run",
            start_dir,
        )
        if not path:
            return

        self._load_saved_run_path(path)

    def open_run_browser(self) -> None:
        if self.state.run_status == RunStatus.RUNNING:
            QtWidgets.QMessageBox.warning(
                self,
                "Run Browser",
                "Stop the current run before opening a saved run.",
            )
            return

        root_dir = self.config_panel.save_dir_edit.text().strip() or str(self._default_runs_dir())
        dialog = RunBrowserDialog(root_dir=root_dir, parent=self)
        self._populate_run_browser(dialog)
        dialog.refresh_button.clicked.connect(lambda: self._populate_run_browser(dialog))
        dialog.browse_button.clicked.connect(lambda: self._browse_run_browser_root(dialog))
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return

        selected_path = dialog.selected_run_path()
        if selected_path:
            self._load_saved_run_path(selected_path)

    def _browse_run_browser_root(self, dialog: RunBrowserDialog) -> None:
        start_dir = dialog.root_dir() or str(self._default_runs_dir())
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select Run Root or Run Directory",
            start_dir,
        )
        if not path:
            return
        dialog.set_root_dir(path)
        self._populate_run_browser(dialog)

    def _populate_run_browser(self, dialog: RunBrowserDialog) -> None:
        root_dir = dialog.root_dir() or str(self._default_runs_dir())
        dialog.set_root_dir(root_dir)
        scope_kind = run_browser_scope_kind(root_dir)
        try:
            entries = self.run_service.list_runs(root_dir)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Run Browser", str(exc))
            self.append_log(f"Failed to list runs in {root_dir}: {exc}")
            return
        dialog.set_runs(entries, scope_kind=scope_kind)
        self.append_log(f"Listed {len(entries)} saved run(s) in {root_dir} ({scope_kind}).")

    def _load_saved_run_path(self, path: str | Path) -> bool:
        fast_snapshot = None
        try:
            fast_snapshot = self.run_service.load_timed_acquisition_series_fast(
                str(path),
                minimum_record_count=1,
            )
        except Exception as exc:
            self.append_log(f"Timed saved-run fast path unavailable, falling back to standard load: {exc}")

        if fast_snapshot is not None:
            stub_result = RunResult(
                metadata=fast_snapshot.metadata,
                status=fast_snapshot.status,
                warnings=list(fast_snapshot.warnings),
                details=dict(fast_snapshot.details),
            )
            if not self._ensure_loaded_run_config(stub_result, path):
                return False
            self._apply_loaded_timed_run_snapshot(fast_snapshot, Path(path))
            return True

        try:
            result = self.run_service.load_result(str(path))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Open Saved Run", str(exc))
            self.append_log(f"Failed to open saved run: {exc}")
            return False

        if not self._ensure_loaded_run_config(result, path):
            return False

        self._apply_loaded_run_result(result, Path(path))
        return True

    def _load_config_path(
        self,
        path: str | Path,
        quiet: bool = False,
        *,
        allow_legacy_field_names: bool = False,
    ) -> bool:
        try:
            config = load_config(path, allow_legacy_field_names=allow_legacy_field_names)
        except Exception as exc:
            if not quiet:
                QtWidgets.QMessageBox.critical(self, "Load PV Library", str(exc))
            self.append_log(f"Failed to load PV library: {exc}")
            return False

        self._leave_saved_run_context()
        self.loaded_config = config
        self._last_connection_key = None
        self.state.config_path = config.source_path
        self.config_panel.config_path_edit.setText(config.source_path or str(path))
        self._apply_loaded_config()
        derived_readbacks = sum(1 for obj in config.objects if "knob_readback" in obj.tags)
        self.append_log(
            f"Loaded PV library with {len(config.knobs)} control PVs and {len(config.objects)} read PVs"
            f" ({derived_readbacks} derived from knob readbacks)."
        )
        return True

    def _ensure_loaded_run_config(self, result: RunResult, run_path: str | Path) -> bool:
        recorded_config_path = str(result.metadata.config_path or "").strip()
        preferred_config_path = self.run_service.preferred_config_path(str(run_path), recorded_config_path)
        using_snapshot = preferred_config_path.endswith("config_snapshot.json")
        if not using_snapshot and self.loaded_config is not None:
            already_matches, _message = validate_loaded_run_config(result, self.loaded_config)
            if already_matches:
                return True
        if preferred_config_path:
            current_path = str(self.loaded_config.source_path) if self.loaded_config is not None else ""
            if current_path != preferred_config_path:
                loaded = self._load_config_path(
                    preferred_config_path,
                    quiet=True,
                    allow_legacy_field_names=using_snapshot,
                )
                if not loaded:
                    config_label = "PV library snapshot" if preferred_config_path.endswith("config_snapshot.json") else "PV library"
                    QtWidgets.QMessageBox.critical(
                        self,
                        "Open Saved Run",
                        f"Could not load {config_label} for the run:\n{preferred_config_path}",
                    )
                    self.append_log(f"Offline run load failed: missing {config_label} {preferred_config_path}")
                    return False

        if self.loaded_config is None:
            QtWidgets.QMessageBox.critical(
                self,
                "Open Saved Run",
                "Load the matching PV library first, then reopen the saved run.",
            )
            return False

        ok, message = validate_loaded_run_config(result, self.loaded_config)
        if not ok:
            QtWidgets.QMessageBox.critical(self, "Open Saved Run", message)
            self.append_log(f"Offline run load failed: {message}")
            return False
        return True

    def _apply_loaded_run_result(self, result: RunResult, path: Path) -> None:
        self.current_run_metadata = result.metadata
        self.current_run_records = result.samples
        self.current_run_steps = result.steps
        self.current_run_record_count = len(result.samples)
        self.current_run_sample_timestamps = []
        self.current_run_details = dict(result.details)
        self.current_run_mode = result.metadata.mode
        self.state.run_status = RunStatus.IDLE
        self._viewing_saved_run = True
        self._loaded_run_used_fast_path = False
        self._loaded_run_used_legacy_batch_reconstruction = bool(
            self.current_run_records and any(sample.batch_index is None for sample in self.current_run_records)
        )
        self._load_saved_run_waveform_state(path)

        self.config_panel.save_dir_edit.setText(str(path.parent if path.is_dir() else path.parent.parent))
        self.config_panel.operator_edit.setText(result.metadata.operator)
        self.config_panel.notes_edit.setPlainText(result.metadata.notes)

        self._apply_loaded_run_selection()
        self._apply_loaded_run_mode()
        self._apply_loaded_run_parameters()
        self._rebuild_loaded_run_views()

        self.main_tabs.setCurrentIndex(self.analysis_page_index)
        self._ensure_visible_analysis_tab_loaded()
        self.status_panel.set_connection("Offline Run", tone="info")
        self.status_panel.set_mode("Loaded Run", tone="info")
        self.status_panel.set_sample(str(self._current_record_count()), tone="subtle")
        self.status_panel.set_step(str(len(self.current_run_steps)), tone="subtle")
        self.status_panel.set_current(result.metadata.run_id, tone="subtle")
        self.status_panel.set_time(result.metadata.created_at.strftime("%Y-%m-%d %H:%M:%S"), tone="subtle")

        self.append_log(
            f"Loaded saved run {result.metadata.run_id} ({self._mode_display_name(self._mode_key_from_run_mode(result.metadata.mode))})."
        )
        if self._loaded_run_used_legacy_batch_reconstruction:
            self.append_log(
                "Saved run predates batch index persistence. Offline trend grouping uses compatibility reconstruction."
            )
        if result.warnings:
            self.append_log("Saved run warnings: " + " | ".join(str(item) for item in result.warnings[:6]))

    def _apply_loaded_timed_run_snapshot(self, snapshot: TimedRunSeriesSnapshot, path: Path) -> None:
        self.current_run_metadata = snapshot.metadata
        self.current_run_records = []
        self.current_run_steps = []
        self.current_run_record_count = int(snapshot.record_count)
        self.current_run_sample_timestamps = list(snapshot.sample_timestamps)
        self.current_run_details = dict(snapshot.details)
        self.current_run_mode = snapshot.metadata.mode
        self.state.run_status = RunStatus.IDLE
        self._viewing_saved_run = True
        self._loaded_run_used_fast_path = True
        self._loaded_run_used_legacy_batch_reconstruction = bool(snapshot.used_legacy_batch_reconstruction)
        self._load_saved_run_waveform_state(path)

        self.config_panel.save_dir_edit.setText(str(path.parent if path.is_dir() else path.parent.parent))
        self.config_panel.operator_edit.setText(snapshot.metadata.operator)
        self.config_panel.notes_edit.setPlainText(snapshot.metadata.notes)

        self._apply_loaded_run_selection()
        self._apply_loaded_run_mode()
        self._apply_loaded_run_parameters()

        selected_objects = self._selected_objects()
        scalar_objects = [obj for obj in selected_objects if not self._is_waveform_object(obj)]
        self.current_series_values = {
            pv_id: list(snapshot.series_values.get(pv_id, []))
            for pv_id in snapshot.ordered_object_ids
        }
        self.current_series_metadata = {
            pv_id: {"sample_indices": list(snapshot.series_sample_indices.get(pv_id, []))}
            for pv_id in snapshot.ordered_object_ids
        }
        self.trend_plot.reset_channels(scalar_objects)
        self.trend_plot.clear_highlight()
        self.response_plot.reset_channels("", "", [])
        self._reset_analysis_views()

        trend_history = {}
        for obj in scalar_objects:
            values = self.current_series_values.get(obj.id, [])
            sample_indices = self._series_sample_indices(obj.id, expected_length=len(values))
            trend_history[obj.id] = (sample_indices, values)
        self.trend_plot.set_series_history(
            trend_history,
            sample_timestamps=self.current_run_sample_timestamps,
        )

        self._prepare_analysis_views_for_current_data()
        self._refresh_ui_affordances()

        self.main_tabs.setCurrentIndex(self.analysis_page_index)
        self._ensure_visible_analysis_tab_loaded()
        self.status_panel.set_connection("Offline Run", tone="info")
        self.status_panel.set_mode("Loaded Run", tone="info")
        self.status_panel.set_sample(str(self._current_record_count()), tone="subtle")
        self.status_panel.set_step("0", tone="subtle")
        self.status_panel.set_current(snapshot.metadata.run_id, tone="subtle")
        self.status_panel.set_time(snapshot.metadata.created_at.strftime("%Y-%m-%d %H:%M:%S"), tone="subtle")

        mode_name = self._mode_display_name(self._mode_key_from_run_mode(snapshot.metadata.mode))
        self.append_log(
            f"Loaded saved run {snapshot.metadata.run_id} ({mode_name}) via timed fast path."
        )
        if self._loaded_run_used_legacy_batch_reconstruction:
            self.append_log(
                "Saved run predates batch index persistence. Offline trend grouping uses compatibility reconstruction."
            )
        if snapshot.warnings:
            self.append_log("Saved run warnings: " + " | ".join(str(item) for item in snapshot.warnings[:6]))

    def _apply_loaded_run_selection(self) -> None:
        if self.loaded_config is None:
            return

        selection = resolve_loaded_run_selection(
            self.current_run_details,
            self.current_run_mode,
            {obj.id for obj in self.loaded_config.objects},
            [sample.pv_id for sample in self.current_run_records],
            self.current_series_values.keys(),
        )
        self.state.selected_object_ids = list(selection["selected_object_ids"])
        self.state.selected_knob_ids = list(selection["selected_knob_ids"])
        self.state.active_knob_id = selection["active_knob_id"]
        self._refresh_selected_pvs()

    def _apply_loaded_run_mode(self) -> None:
        self._set_ui_task_mode(self._mode_key_from_run_mode(self.current_run_mode))

    def _apply_loaded_run_parameters(self) -> None:
        updates = loaded_run_parameter_updates(self.current_run_details, self.current_run_mode)
        if self.current_run_mode == RunMode.TIMED_ACQUISITION:
            if "shot_interval_sec" in updates:
                self.scan_panel.interval_spin.setValue(updates["shot_interval_sec"])
            if "sample_count" in updates:
                self.scan_panel.count_spin.setValue(updates["sample_count"])
            return

        if self.current_run_mode == RunMode.KNOB_SCAN:
            if "settle_delay_sec" in updates:
                self.scan_panel.settle_spin.setValue(updates["settle_delay_sec"])
            if "shot_interval_sec" in updates:
                self.scan_panel.scan_sample_interval_spin.setValue(updates["shot_interval_sec"])
            if "sample_count_per_step" in updates:
                self.scan_panel.step_sample_spin.setValue(updates["sample_count_per_step"])
            if "restore_initial_value" in updates:
                self.scan_panel.restore_check.setChecked(updates["restore_initial_value"])
            if "manual_scan_values_text" in updates:
                self.scan_panel.scan_value_mode_combo.setCurrentIndex(0)
                self.scan_panel.manual_scan_values_edit.setText(updates["manual_scan_values_text"])
            return

        if "settle_delay_sec" in updates:
            self.scan_panel.random_settle_spin.setValue(updates["settle_delay_sec"])
        if "shot_interval_sec" in updates:
            self.scan_panel.random_sample_interval_spin.setValue(updates["shot_interval_sec"])
        if "sample_count_per_point" in updates:
            self.scan_panel.random_samples_per_point_spin.setValue(updates["sample_count_per_point"])
        if "num_points" in updates:
            self.scan_panel.random_point_count_spin.setValue(updates["num_points"])
        if "seed" in updates:
            self.scan_panel.random_seed_edit.setText(updates["seed"])
        if "restore_initial_values" in updates:
            self.scan_panel.random_restore_check.setChecked(updates["restore_initial_values"])
        if "distribution" in updates:
            index = self.scan_panel.random_distribution_combo.findData(updates["distribution"])
            if index >= 0:
                self.scan_panel.random_distribution_combo.setCurrentIndex(index)
        if "knob_state" in updates:
            self.scan_panel.set_random_knob_state(updates["knob_state"])

    @staticmethod
    def _mode_key_from_run_mode(mode: RunMode | None) -> str:
        return mode_key_from_run_mode(mode)

    def _apply_loaded_config(self) -> None:
        if self.loaded_config is None:
            return

        selection = normalize_selection_for_available_pvs(
            self.state.selected_knob_ids,
            self.state.active_knob_id,
            self.state.selected_object_ids,
            [knob.id for knob in self.loaded_config.knobs],
            [obj.id for obj in self.loaded_config.objects],
        )
        self.state.selected_knob_ids = list(selection["selected_knob_ids"])
        self.state.active_knob_id = selection["active_knob_id"]
        self.state.selected_object_ids = list(selection["selected_object_ids"])

        group_labels = {group.id: group.label for group in self.loaded_config.groups}
        self.object_panel.set_library_objects(self.loaded_config.objects, group_labels)
        self.scan_panel.interval_spin.setValue(self.loaded_config.defaults.acquisition.shot_interval_sec)
        self.scan_panel.count_spin.setValue(self.loaded_config.defaults.acquisition.sample_count)
        self.scan_panel.settle_spin.setValue(self.loaded_config.defaults.scan.settle_delay_sec)
        self.scan_panel.step_sample_spin.setValue(self.loaded_config.defaults.scan.sample_count_per_step)
        self.scan_panel.scan_sample_interval_spin.setValue(self.loaded_config.defaults.acquisition.shot_interval_sec)
        self.scan_panel.restore_check.setChecked(self.loaded_config.defaults.scan.restore_initial_value)
        self.scan_panel.random_settle_spin.setValue(self.loaded_config.defaults.scan.settle_delay_sec)
        self.scan_panel.random_samples_per_point_spin.setValue(self.loaded_config.defaults.scan.sample_count_per_step)
        self.scan_panel.random_sample_interval_spin.setValue(
            self.loaded_config.defaults.acquisition.shot_interval_sec
        )
        self.scan_panel.random_restore_check.setChecked(self.loaded_config.defaults.scan.restore_initial_value)
        self._refresh_selected_pvs()

    def open_pv_selector(self) -> None:
        if self.loaded_config is None and not self.load_config_file():
            return
        if self.loaded_config is None:
            return

        source_label = Path(self.loaded_config.source_path).name if self.loaded_config.source_path else ""
        group_labels = {group.id: group.label for group in self.loaded_config.groups}
        dialog = PVSelectorDialog(
            knobs=self.loaded_config.knobs,
            objects=self.loaded_config.objects,
            group_labels=group_labels,
            current_knob_ids=set(self.state.selected_knob_ids),
            current_object_ids=set(self.state.selected_object_ids),
            source_label=source_label,
            parent=self,
        )
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return

        self._leave_saved_run_context()
        self.state.selected_knob_ids = dialog.selected_knob_ids()
        if self.state.active_knob_id not in self.state.selected_knob_ids:
            self.state.active_knob_id = self.state.selected_knob_ids[0] if self.state.selected_knob_ids else None
        self.state.selected_object_ids = dialog.selected_object_ids()
        self._refresh_selected_pvs()
        self.append_log(
            f"Selected {len(self.state.selected_knob_ids)} control PV(s) and {len(self.state.selected_object_ids)} read PV(s)."
        )

    def clear_selected_pvs(self) -> None:
        if self.state.run_status == RunStatus.RUNNING:
            QtWidgets.QMessageBox.warning(self, "Clear Selection", "Stop the current run before changing PV selection.")
            return
        self._leave_saved_run_context()
        self.state.selected_knob_ids = []
        self.state.active_knob_id = None
        self.state.selected_object_ids = []
        self._refresh_selected_pvs()
        self.append_log("Cleared selected PVs.")

    def _refresh_selected_pvs(self) -> None:
        if self.loaded_config is None:
            self.object_panel.set_selected_knobs([])
            self.object_panel.set_selected_objects([])
            self.object_panel.set_library_empty()
            self.scan_panel.set_knob_choices([], active_knob_id=None, group_labels={})
            self.scan_panel.set_knob_scan_enabled(False)
            self.scan_panel.apply_knob_spec(None)
            self.scan_panel.set_preview_message("Load a PV library, then choose read PVs and control PVs.")
            self.scan_panel.set_random_preview_message(
                "Load a PV library, then choose control PVs to use Random Multi-Knob."
            )
            self._sync_mode_buttons()
            self._refresh_ui_affordances()
            return

        knobs_by_id = {knob.id: knob for knob in self.loaded_config.knobs}
        objects_by_id = {obj.id: obj for obj in self.loaded_config.objects}

        selected_knobs = [
            knobs_by_id[knob_id]
            for knob_id in self.state.selected_knob_ids
            if knob_id in knobs_by_id
        ]
        selected_objects = [
            objects_by_id[object_id]
            for object_id in self.state.selected_object_ids
            if object_id in objects_by_id
        ]

        if self.state.active_knob_id not in {knob.id for knob in selected_knobs}:
            self.state.active_knob_id = selected_knobs[0].id if selected_knobs else None

        self.scan_panel.set_knob_choices(
            selected_knobs,
            active_knob_id=self.state.active_knob_id,
            group_labels={group.id: group.label for group in self.loaded_config.groups},
        )
        self.scan_panel.set_knob_scan_enabled(bool(selected_knobs))
        active_knob = next((knob for knob in selected_knobs if knob.id == self.state.active_knob_id), None)
        if active_knob is not None:
            self.scan_panel.apply_knob_spec(active_knob)
        else:
            self.scan_panel.apply_knob_spec(None)
        self.object_panel.set_selected_knobs(selected_knobs)
        self.object_panel.set_selected_objects(selected_objects)
        self.refresh_scan_preview()
        if selected_knobs:
            self.scan_panel.set_random_preview_message(
                "Open 'Configure Ranges...' and then refresh the preview."
            )
        else:
            self.scan_panel.set_random_preview_message(
                "Choose control PVs to enable Random Multi-Knob."
            )
        self._sync_mode_buttons()
        self._refresh_ui_affordances()

    def _sync_knob_from_combo(self) -> None:
        if self.loaded_config is None:
            return
        knob_id = self.scan_panel.selected_knob_id()
        self.state.active_knob_id = knob_id
        active_knob = self._active_knob()
        if active_knob is not None:
            self.scan_panel.apply_knob_spec(active_knob)
        else:
            self.scan_panel.apply_knob_spec(None)
        self.refresh_scan_preview()
        self._refresh_ui_affordances()

    def _selected_knobs(self):
        if self.loaded_config is None:
            return []
        knobs_by_id = {knob.id: knob for knob in self.loaded_config.knobs}
        return [
            knobs_by_id[knob_id]
            for knob_id in self.state.selected_knob_ids
            if knob_id in knobs_by_id
        ]

    def _selected_objects(self):
        if self.loaded_config is None:
            return []
        objects_by_id = {obj.id: obj for obj in self.loaded_config.objects}
        return [
            objects_by_id[object_id]
            for object_id in self.state.selected_object_ids
            if object_id in objects_by_id
        ]

    @staticmethod
    def _is_waveform_object(obj) -> bool:
        return str(getattr(obj, "capture_mode", "scalar") or "scalar").strip().lower() == "waveform"

    def _selected_scalar_objects(self):
        return [obj for obj in self._selected_objects() if not self._is_waveform_object(obj)]

    def _selected_waveform_objects(self):
        return [obj for obj in self._selected_objects() if self._is_waveform_object(obj)]

    def _waveform_label_for_id(self, pv_id: str) -> str:
        if self.loaded_config is None:
            return str(pv_id)
        for obj in self.loaded_config.objects:
            if obj.id == pv_id:
                return str(obj.name)
        return str(pv_id)

    def _active_knob(self):
        if self.loaded_config is None or not self.state.active_knob_id:
            return None
        knobs_by_id = {knob.id: knob for knob in self.loaded_config.knobs}
        return knobs_by_id.get(self.state.active_knob_id)

    def open_random_knob_config_dialog(self) -> None:
        selected_knobs = self._selected_knobs()
        if not selected_knobs:
            QtWidgets.QMessageBox.information(
                self,
                "Configure Ranges",
                "Choose one or more control PVs first.",
            )
            return

        if self.loaded_config is None:
            return

        dialog = RandomKnobConfigDialog(
            knobs=selected_knobs,
            group_labels={group.id: group.label for group in self.loaded_config.groups},
            current_state=self.scan_panel.random_knob_state(),
            epics_client=self.epics_client,
            parent=self,
        )
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return

        self.scan_panel.set_random_knob_state(dialog.selected_state())
        self.scan_panel.set_random_preview_message(
            "Ranges updated. Refresh the preview to inspect generated random points."
        )
        self.append_log("Updated random knob range configuration.")
        self._refresh_ui_affordances()

    def refresh_random_preview(self) -> None:
        if self.loaded_config is None:
            self.scan_panel.set_random_preview_message("Load a PV library to preview random targets.")
            return
        try:
            knob_ranges = self._collect_random_knob_ranges()
            config = self.scan_panel.random_configuration()
            seed = self._ensure_random_seed(config["seed_text"])
            target_steps = self._generate_random_targets(
                knob_ranges,
                distribution=str(config["distribution"]),
                num_points=int(config["num_points"]),
                seed=seed,
            )
        except ValueError as exc:
            self.scan_panel.set_random_preview_message(str(exc))
            return

        preview = random_preview_payload(
            knob_ranges,
            target_steps,
            distribution=str(config["distribution"]),
            seed=seed,
        )
        self.scan_panel.set_random_seed(seed)
        self.scan_panel.set_random_preview(
            preview["lines"],
            summary=preview["summary"],
            detail=preview["detail"],
        )

    def refresh_scan_preview(self, *args, force_live_center: bool = False) -> None:
        del args
        if self.loaded_config is None:
            self.scan_panel.set_preview_message("Load a PV library to preview scan points.")
            return

        active_knob = self._active_knob()
        if active_knob is None:
            self.scan_panel.set_preview_message("Choose an active control PV to preview scan points.")
            return

        try:
            values = self._resolve_scan_values(active_knob, preview_only=not force_live_center)
        except ValueError as exc:
            self.scan_panel.set_preview_message(str(exc))
            return

        mode = self.scan_panel.scan_value_mode()
        center = None
        if mode == "symmetric_points":
            try:
                center = self._read_knob_center_value(active_knob, preview_only=not force_live_center)
            except ValueError:
                center = None

        preview = single_knob_preview_payload(
            values,
            active_knob.name,
            active_knob.unit,
            mode,
            center=center,
        )
        self.scan_panel.set_preview_values(
            values,
            summary=preview["summary"],
            detail=preview["detail"],
        )

    def _mode_display_name(self, mode: str | None = None) -> str:
        return mode_display_name(mode or self.scan_panel.task_mode())

    def _mode_help_text(self, mode: str) -> str:
        return mode_help_text(mode)

    def _selection_key(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return (tuple(self.state.selected_knob_ids), tuple(self.state.selected_object_ids))

    def _mode_ready_state(self, mode: str) -> tuple[bool, str]:
        config_loaded = self.loaded_config is not None
        selected_objects = self._selected_objects() if config_loaded else []
        selected_knobs = self._selected_knobs() if config_loaded else []
        active_knob = self._active_knob() if config_loaded else None
        random_ranges_valid = False
        if config_loaded and selected_objects and selected_knobs:
            try:
                self._collect_random_knob_ranges()
                random_ranges_valid = True
            except ValueError:
                random_ranges_valid = False
        return mode_ready_state(
            mode,
            run_status=self.state.run_status,
            config_loaded=config_loaded,
            selected_object_count=len(selected_objects),
            selected_knob_count=len(selected_knobs),
            active_knob_available=active_knob is not None,
            random_ranges_valid=random_ranges_valid,
        )

    def _refresh_ui_affordances(self) -> None:
        mode = self.scan_panel.task_mode()
        running = self.state.run_status == RunStatus.RUNNING
        loaded = self.loaded_config is not None
        selected_objects = self._selected_objects() if loaded else []
        selected_knobs = self._selected_knobs() if loaded else []
        active_knob = self._active_knob() if loaded else None
        has_selection = bool(selected_objects or selected_knobs)
        ready, next_step = self._mode_ready_state(mode)

        self.action_start.setText("Start")
        if running:
            stop_label = self._mode_display_name(self.current_run_mode.value if self.current_run_mode else mode)
            self.action_stop.setText(f"Stop {stop_label}")
        else:
            self.action_stop.setText("Stop")

        self.run_check_button.setText(self.action_connect_epics.text())
        self.run_start_button.setText(self.action_start.text())
        self.run_stop_button.setText("Stop")
        self.run_stop_button.setToolTip(self.action_stop.text())

        self.object_panel.select_button.setEnabled(loaded and not running)
        self.action_connect_epics.setEnabled(loaded and has_selection and not running)
        self.action_start.setEnabled(ready)
        self.run_check_button.setEnabled(loaded and has_selection and not running)
        self.run_start_button.setEnabled(ready)
        self.analysis_open_run_button.setEnabled(not running)
        self.run_start_button.setToolTip(next_step)
        self.action_start.setToolTip(next_step)

        self.scan_panel.active_knob_combo.setEnabled(bool(selected_knobs) and not running)
        self.scan_panel.preview_refresh_button.setEnabled(bool(active_knob) and not running)
        self.scan_panel.random_config_button.setEnabled(bool(selected_knobs) and not running)
        self.scan_panel.random_preview_button.setEnabled(bool(selected_knobs) and not running)

        self._refresh_result_tabs(mode)
        self._update_mode_status_banner(mode, ready, next_step)

        if not running and not self._viewing_saved_run:
            current_key = self._selection_key() if loaded else None
            if not loaded:
                self.status_panel.set_connection("Load PV library", tone="subtle")
            elif not has_selection:
                self.status_panel.set_connection("Choose PVs", tone="subtle")
            elif current_key != self._last_connection_key:
                self.status_panel.set_connection("Not checked", tone="subtle")

        if self.state.run_status == RunStatus.IDLE and not self._viewing_saved_run:
            self.status_panel.set_mode(self._mode_display_name(mode), tone="info")
            self.status_panel.set_sample("-", tone="subtle")
            self.status_panel.set_step("-", tone="subtle")
            self.status_panel.set_current("--", tone="subtle")
            self.status_panel.set_time("--", tone="subtle")

    def _update_mode_status_banner(self, mode: str, ready: bool, next_step: str) -> None:
        if self.state.run_status == RunStatus.RUNNING:
            tone = "info"
            title = f"{self._mode_display_name(mode)} in progress"
            message = "The current run is active. Use Stop before changing PV selection, mode, or setup."
        elif self._viewing_saved_run and self.current_run_metadata is not None:
            tone = "info"
            title = "Saved run loaded"
            message = (
                f"Offline analysis is showing run {self.current_run_metadata.run_id} "
                f"in {self._mode_display_name(self._mode_key_from_run_mode(self.current_run_mode))} mode."
            )
        elif ready:
            tone = "success"
            title = f"{self._mode_display_name(mode)} is ready"
            message = next_step
        elif self.loaded_config is None:
            tone = "subtle"
            title = "Load the PV library first"
            message = next_step
        else:
            tone = "warning"
            title = "Next step"
            message = next_step

        self.mode_status_title_label.setText(title)
        self.mode_status_message_label.setText(message)
        if self._viewing_saved_run and self.current_run_metadata is not None:
            context = f"Offline run source: {self.current_run_metadata.run_id}"
        else:
            save_dir = self.config_panel.save_dir_edit.text().strip() or "runs"
            context = f"New runs will be saved under: {save_dir}"
        self.mode_status_context_label.setText(context)
        self.mode_status_title_label.parentWidget().setProperty("tone", tone)
        self.mode_status_title_label.parentWidget().style().unpolish(self.mode_status_title_label.parentWidget())
        self.mode_status_title_label.parentWidget().style().polish(self.mode_status_title_label.parentWidget())

    def _leave_saved_run_context(self) -> None:
        self._viewing_saved_run = False

    def _current_record_count(self) -> int:
        return current_record_count(self.current_run_records, self.current_run_record_count)

    def _has_current_run_data(self) -> bool:
        return has_run_data(self._current_record_count(), self.current_series_values)

    def _has_current_waveform_data(self) -> bool:
        return has_waveform_data(self.current_waveform_records, self.current_waveform_index)

    def _set_waveform_available(self, available: bool) -> None:
        self._has_waveform_data = bool(available)
        self._refresh_ui_affordances()

    def _reset_waveform_state(self, clear_widget: bool = False) -> None:
        self.current_waveform_records = {}
        self.current_waveform_index = {}
        self.current_waveform_run_path = None
        self._waveform_analysis_signature = None
        self._waveform_analysis_inflight_signature = None
        self._waveform_analysis_result = {}
        self._pending_waveform_analysis_signature = None
        self._set_waveform_available(False)
        if clear_widget and getattr(self, "waveform_plot", None) is not None:
            self.waveform_plot.clear_data(
                "Run a monitor acquisition with waveform objects to inspect waveform preview, features, and delay."
            )

    def _waveform_ids_in_current_run(self) -> list[str]:
        return waveform_ids_in_current_run(
            self.current_run_details,
            self.current_waveform_records,
            self.current_waveform_index,
            [obj.id for obj in self._selected_waveform_objects()],
        )

    def _current_waveform_labels(self) -> dict[str, str]:
        return {
            pv_id: self._waveform_label_for_id(pv_id)
            for pv_id in self._waveform_ids_in_current_run()
        }

    def _current_waveform_record_counts(self) -> dict[str, int]:
        return waveform_record_counts(
            self._waveform_ids_in_current_run(),
            self.current_waveform_records,
            self.current_waveform_index,
        )

    def _current_waveform_shot_count(self) -> int:
        counts = self._current_waveform_record_counts()
        return max(counts.values(), default=0)

    def _current_waveform_max_length_hint(self) -> int:
        return waveform_max_length_hint(
            self.current_waveform_records,
            self.current_waveform_index,
            self._waveform_analysis_result,
        )

    def _load_saved_run_waveform_state(self, path: Path) -> None:
        self.current_waveform_run_path = Path(path)
        self.current_waveform_records = {}
        self.current_waveform_index = {}
        try:
            entries = self.run_service.load_waveform_index(str(path))
        except Exception as exc:
            self.append_log(f"Saved run waveform index unavailable: {exc}")
            self._set_waveform_available(False)
            return

        grouped = group_waveform_index_entries(entries)
        self.current_waveform_index = grouped
        if grouped:
            waveform_ids = list(dict.fromkeys(grouped))
            self.current_run_details = dict(self.current_run_details)
            self.current_run_details.setdefault("waveform_count", len(entries))
            self.current_run_details.setdefault("waveform_object_ids", waveform_ids)
        self._waveform_analysis_signature = None
        self._waveform_analysis_result = {}
        self._pending_waveform_analysis_signature = None
        self._set_waveform_available(bool(grouped) and self.current_run_mode == RunMode.TIMED_ACQUISITION)

    def _load_waveform_for_display(self, pv_id: str, shot_index: int) -> WaveformRecord | None:
        if pv_id in self.current_waveform_records:
            records = self.current_waveform_records.get(pv_id, [])
            if 0 <= shot_index < len(records):
                return records[shot_index]
            return None
        if self.current_waveform_run_path is None:
            return None
        entries = self.current_waveform_index.get(pv_id, [])
        if 0 <= shot_index < len(entries):
            return self.run_service.load_waveform(str(self.current_waveform_run_path), entries[shot_index])
        return None

    def _current_waveform_analysis_signature_for_view(self) -> tuple[object, ...]:
        waveform_ids = self._waveform_ids_in_current_run()
        counts_signature = waveform_counts_signature(
            waveform_ids,
            self.current_waveform_records,
            self.current_waveform_index,
        )
        primary = self.waveform_plot.selected_primary_pv_id()
        secondary = self.waveform_plot.selected_secondary_pv_id()
        roi_start, roi_stop = self.waveform_plot.roi_bounds()
        return (
            tuple(waveform_ids),
            tuple(counts_signature),
            roi_start,
            roi_stop,
            primary,
            secondary,
            str(self.current_waveform_run_path or ""),
        )

    def _schedule_waveform_analysis_refresh(self) -> None:
        if not self._has_current_waveform_data():
            self.waveform_plot.clear_data(
                "Run a monitor acquisition with waveform objects to inspect waveform preview, features, and delay."
            )
            return
        signature = self._current_waveform_analysis_signature_for_view()
        if self._waveform_analysis_signature == signature and self._waveform_analysis_result:
            self._apply_waveform_analysis_result()
            return
        if self._waveform_analysis_thread is not None:
            self._pending_waveform_analysis_signature = signature
            return
        self._start_waveform_analysis_worker(signature)

    def _start_waveform_analysis_worker(self, signature) -> None:
        waveform_ids = self._waveform_ids_in_current_run()
        if not waveform_ids:
            self.waveform_plot.clear_data("No waveform objects are available for the current run.")
            return
        labels_by_pv = self._current_waveform_labels()
        roi_start, roi_stop = self.waveform_plot.roi_bounds()
        primary = self.waveform_plot.selected_primary_pv_id()
        secondary = self.waveform_plot.selected_secondary_pv_id() if len(waveform_ids) >= 2 else ""
        self._pending_waveform_analysis_signature = None
        self._waveform_analysis_inflight_signature = signature
        self.waveform_plot.info_label.setText("Computing waveform features and time delay in the background...")

        worker_kwargs = {
            "waveform_ids": waveform_ids,
            "labels_by_pv": labels_by_pv,
            "request_signature": signature,
            "roi_start_index": roi_start,
            "roi_stop_index": roi_stop,
            "primary_pv_id": primary,
            "secondary_pv_id": secondary,
        }
        if self.current_waveform_run_path is not None:
            waveform_run_path = str(self.current_waveform_run_path)
            worker_kwargs["waveform_entries_by_pv"] = self.current_waveform_index
            worker_kwargs["waveform_loader"] = lambda entry, run_path=waveform_run_path: self.run_service.load_waveform(
                run_path,
                entry,
            )
        else:
            worker_kwargs["in_memory_records_by_pv"] = self.current_waveform_records

        self._waveform_analysis_thread = QtCore.QThread(self)
        self._waveform_analysis_worker = WaveformAnalysisWorker(**worker_kwargs)
        self._waveform_analysis_worker.moveToThread(self._waveform_analysis_thread)
        self._waveform_analysis_thread.started.connect(self._waveform_analysis_worker.run)
        self._waveform_analysis_worker.signals.finished.connect(self._on_waveform_analysis_finished)
        self._waveform_analysis_worker.signals.finished.connect(self._waveform_analysis_thread.quit)
        self._waveform_analysis_worker.signals.failed.connect(self._on_waveform_analysis_failed)
        self._waveform_analysis_worker.signals.failed.connect(self._waveform_analysis_thread.quit)
        self._waveform_analysis_thread.finished.connect(self._cleanup_waveform_analysis_thread)
        self._waveform_analysis_thread.start()

    def _cleanup_waveform_analysis_thread(self) -> None:
        if self._waveform_analysis_worker is not None:
            self._waveform_analysis_worker.deleteLater()
        if self._waveform_analysis_thread is not None:
            self._waveform_analysis_thread.deleteLater()
        self._waveform_analysis_worker = None
        self._waveform_analysis_thread = None
        if (
            self._pending_waveform_analysis_signature is not None
            and self._pending_waveform_analysis_signature != self._waveform_analysis_signature
        ):
            pending_signature = self._pending_waveform_analysis_signature
            self._pending_waveform_analysis_signature = None
            self._start_waveform_analysis_worker(pending_signature)

    def _on_waveform_analysis_finished(self, payload) -> None:
        request_signature = tuple(payload.get("request_signature", ()))
        if request_signature != tuple(self._waveform_analysis_inflight_signature or ()):
            return
        self._waveform_analysis_inflight_signature = None
        self._waveform_analysis_signature = request_signature
        self._waveform_analysis_result = dict(payload)
        self._apply_waveform_analysis_result()

    def _on_waveform_analysis_failed(self, message: str) -> None:
        self._waveform_analysis_inflight_signature = None
        self.append_log(f"Waveform analysis failed: {message}")
        self.waveform_plot.info_label.setText(f"Waveform analysis failed: {message}")

    def _refresh_waveform_preview(self) -> None:
        if not self._has_current_waveform_data():
            return
        primary_id = self.waveform_plot.selected_primary_pv_id()
        secondary_id = self.waveform_plot.selected_secondary_pv_id()
        shot_index = self.waveform_plot.selected_shot_index()
        roi_start, roi_stop = self.waveform_plot.roi_bounds()
        primary_record = self._load_waveform_for_display(primary_id, shot_index) if primary_id else None
        secondary_record = (
            self._load_waveform_for_display(secondary_id, shot_index)
            if secondary_id and secondary_id != primary_id
            else None
        )

        def _payload(record: WaveformRecord | None, label: str):
            if record is None:
                return None
            x_values = [
                float(index * record.waveform_sample_interval_sec)
                for index in range(len(record.values))
            ]
            return {
                "label": label,
                "x_values": x_values,
                "y_values": list(record.values),
            }

        info_parts = []
        roi_bounds_sec = None
        if primary_record is not None:
            max_stop = min(roi_stop, len(primary_record.values))
            roi_bounds_sec = (
                float(roi_start * primary_record.waveform_sample_interval_sec),
                float(max_stop * primary_record.waveform_sample_interval_sec),
            )
            info_parts.append(
                f"{self._waveform_label_for_id(primary_id)} shot {shot_index + 1}: "
                f"{len(primary_record.values)} samples @ {primary_record.waveform_sample_interval_sec:.6g} s"
            )
        if secondary_record is not None and secondary_id != primary_id:
            info_parts.append(
                f"{self._waveform_label_for_id(secondary_id)} shot {shot_index + 1}: "
                f"{len(secondary_record.values)} samples @ {secondary_record.waveform_sample_interval_sec:.6g} s"
            )
        if not info_parts:
            info_parts.append("Select a waveform object and shot with available waveform data.")
        delay_summary = str(self._waveform_analysis_result.get("delay_summary", "")).strip()
        if delay_summary:
            info_parts.append(delay_summary)
        self.waveform_plot.set_preview_series(
            primary=_payload(primary_record, self._waveform_label_for_id(primary_id)),
            secondary=_payload(secondary_record, self._waveform_label_for_id(secondary_id)),
            roi_bounds=roi_bounds_sec,
            info_text=" | ".join(info_parts),
        )

    def _apply_waveform_analysis_result(self) -> None:
        if not self._waveform_analysis_result:
            return
        feature_key = self.waveform_plot.selected_feature_key()
        feature_label = next(
            (label for key, label in self.waveform_plot.FEATURE_OPTIONS if key == feature_key),
            feature_key,
        )
        feature_rows = []
        for row in self._waveform_analysis_result.get("feature_rows", []):
            feature_rows.append(
                {
                    "pv_id": row["pv_id"],
                    "label": row["label"],
                    "sample_indices": list(row.get("sample_indices", [])),
                    "values": list(row.get("features", {}).get(feature_key, [])),
                }
            )
        summary_text = str(self._waveform_analysis_result.get("delay_summary", "")).strip()
        self.waveform_plot.set_feature_rows(
            feature_rows,
            feature_key=feature_key,
            feature_label=feature_label,
            info_text=summary_text or self.waveform_plot.info_label.text(),
        )
        self.waveform_plot.set_delay_series(
            list(self._waveform_analysis_result.get("delay_sample_indices", [])),
            list(self._waveform_analysis_result.get("delay_series", [])),
            summary_text=summary_text,
        )
        self._refresh_waveform_preview()

    def _populate_waveform_view(self) -> None:
        if self.current_run_mode != RunMode.TIMED_ACQUISITION or not self._has_current_waveform_data():
            self.waveform_plot.clear_data(
                "Waveform analysis is available for Monitor runs that include waveform objects."
            )
            return
        option_rows = [
            (pv_id, self._waveform_label_for_id(pv_id))
            for pv_id in self._waveform_ids_in_current_run()
        ]
        self.waveform_plot.set_waveform_options(
            option_rows,
            shot_count=self._current_waveform_shot_count(),
            max_roi_stop=self._current_waveform_max_length_hint(),
            current_primary=self.waveform_plot.selected_primary_pv_id(),
            current_secondary=self.waveform_plot.selected_secondary_pv_id(),
        )
        self._refresh_waveform_preview()
        self._schedule_waveform_analysis_refresh()

    def _on_waveform_view_changed(self) -> None:
        if not self._has_current_waveform_data():
            return
        self._refresh_waveform_preview()
        signature = self._current_waveform_analysis_signature_for_view()
        if signature != self._waveform_analysis_signature:
            self._schedule_waveform_analysis_refresh()
            return
        self._apply_waveform_analysis_result()

    def _record_run_sample_timestamp(self, sample_index: int, timestamp: datetime | None) -> None:
        if timestamp is None or sample_index < 0:
            return
        if sample_index >= len(self.current_run_sample_timestamps):
            self.current_run_sample_timestamps.extend([None] * (sample_index + 1 - len(self.current_run_sample_timestamps)))
        if self.current_run_sample_timestamps[sample_index] is None:
            self.current_run_sample_timestamps[sample_index] = timestamp

    def _series_sample_indices(self, pv_id: str, expected_length: int | None = None) -> list[int]:
        metadata = self.current_series_metadata.get(pv_id, [])
        values = self.current_series_values.get(pv_id, [])
        return series_sample_indices(metadata, values, expected_length=expected_length)

    def _series_step_indices(self, pv_id: str, expected_length: int | None = None) -> list[int | None]:
        metadata = self.current_series_metadata.get(pv_id, [])
        values = self.current_series_values.get(pv_id, [])
        return series_step_indices(metadata, values, expected_length=expected_length)

    def _filtered_series_payload(self, pv_id: str) -> dict[str, object]:
        values = list(self.current_series_values.get(pv_id, []))
        sample_indices = self._series_sample_indices(pv_id, expected_length=len(values))
        step_indices = self._series_step_indices(pv_id, expected_length=len(values))
        return filtered_series_payload(
            values,
            sample_indices,
            step_indices,
            outlier_filter_enabled=self._jitter_outlier_filter_enabled(),
            outlier_filter_threshold=self._jitter_outlier_filter_threshold(),
        )

    def _analysis_mode_key(self, fallback_mode: str) -> str:
        return analysis_mode_key(fallback_mode, self._has_analysis_data, self.current_run_mode)

    def _single_knob_axis_name(self, knob_name: str = "") -> str:
        return single_knob_axis_name(self._single_knob_axis_source, knob_name)

    def _single_knob_axis_summary_text(self) -> str:
        return single_knob_axis_summary_text(self._single_knob_axis_source)

    def _single_knob_step_axis_value(self, step: ScanStepRecord) -> float | None:
        return single_knob_step_axis_value(self._single_knob_axis_source, step)

    def _refresh_single_knob_response_plot(self) -> None:
        if self.loaded_config is None:
            return
        if self.current_run_mode != RunMode.KNOB_SCAN:
            return

        selected_objects = self._selected_objects()
        knob_id = str(self.current_run_details.get("knob_id", ""))
        knob = next((item for item in self.loaded_config.knobs if item.id == knob_id), None)
        axis_name = self._single_knob_axis_name(knob.name if knob is not None else "Knob Value")
        axis_unit = knob.unit if knob is not None else ""
        self.response_plot.reset_channels(axis_name, axis_unit, selected_objects)
        for step in self.current_run_steps:
            if not isinstance(step, ScanStepRecord):
                continue
            axis_value = self._single_knob_step_axis_value(step)
            if axis_value is None or not math.isfinite(axis_value):
                continue
            self.response_plot.append_step(float(axis_value), step.samples)

    def _on_single_knob_axis_changed(self) -> None:
        axis_source = str(self.analysis_axis_combo.currentData() or "readback")
        if axis_source == self._single_knob_axis_source:
            return
        self._single_knob_axis_source = axis_source
        if self.current_run_mode == RunMode.KNOB_SCAN:
            self._refresh_single_knob_response_plot()
            self._analysis_tab_loaded[self.sensitivity_tab_index] = False
            if self.main_tabs.currentIndex() == self.analysis_page_index:
                self._ensure_analysis_tab_loaded(self.analysis_tabs.currentIndex())
        self._refresh_ui_affordances()

    def _set_analysis_tab_visible(self, tab_index: int, visible: bool) -> None:
        tab_bar = self.analysis_tabs.tabBar()
        set_visible = getattr(tab_bar, "setTabVisible", None)
        if callable(set_visible):
            set_visible(tab_index, visible)
            return
        self.analysis_tabs.setTabEnabled(tab_index, visible)

    def _refresh_result_tabs(self, mode: str) -> None:
        analysis_mode = self._analysis_mode_key(mode)
        analysis_tooltip = "Run a task to populate jitter, correlation, and spectrum analysis."
        single_knob_axis_enabled = analysis_mode == "single_knob_scan"
        response_visible = analysis_mode in {"single_knob_scan", "multi_knob_random"}
        sensitivity_visible = analysis_mode == "single_knob_scan"
        waveform_visible = analysis_mode == "timed_acquisition" and self._has_waveform_data
        self._refresh_analysis_outlier_filter_affordances()
        self.analysis_axis_label.setVisible(single_knob_axis_enabled)
        self.analysis_axis_combo.setVisible(single_knob_axis_enabled)
        self.analysis_axis_label.setEnabled(single_knob_axis_enabled)
        self.analysis_axis_combo.setEnabled(single_knob_axis_enabled)
        self.analysis_axis_combo.setToolTip(
            "Choose whether Single Knob response and sensitivity use knob readback or target values on the x-axis."
            if single_knob_axis_enabled
            else "Single Knob X Axis is only used for Single Knob runs."
        )
        self.main_tabs.setTabEnabled(self.analysis_page_index, True)
        self._set_analysis_tab_visible(self.response_tab_index, response_visible)
        self._set_analysis_tab_visible(self.waveform_tab_index, waveform_visible)
        self._set_analysis_tab_visible(self.sensitivity_tab_index, sensitivity_visible)
        self.analysis_tabs.setTabEnabled(self.jitter_tab_index, True)
        self.analysis_tabs.setTabEnabled(self.correlation_tab_index, True)
        self.analysis_tabs.setTabEnabled(self.spectrum_tab_index, True)
        self.analysis_tabs.setTabEnabled(self.waveform_tab_index, waveform_visible)
        sensitivity_enabled = self._has_sensitivity_data and analysis_mode == "single_knob_scan"
        self.analysis_tabs.setTabEnabled(self.sensitivity_tab_index, sensitivity_visible)
        self.analysis_tabs.tabBar().setTabToolTip(
            self.waveform_tab_index,
            "Waveform preview, feature trends, and waveform-to-waveform delay for Monitor runs."
            if waveform_visible
            else "Waveform analysis is available for Monitor runs that include waveform objects.",
        )
        self.analysis_tabs.tabBar().setTabToolTip(self.jitter_tab_index, analysis_tooltip)
        self.analysis_tabs.tabBar().setTabToolTip(self.correlation_tab_index, analysis_tooltip)
        self.analysis_tabs.tabBar().setTabToolTip(self.spectrum_tab_index, analysis_tooltip)
        if analysis_mode == "single_knob_scan":
            self.analysis_tabs.tabBar().setTabToolTip(
                self.sensitivity_tab_index,
                "Single-knob linear fit of mean read PV response versus knob value.",
            )
        elif self._has_analysis_data:
            self.analysis_tabs.tabBar().setTabToolTip(
                self.sensitivity_tab_index,
                "Single-Knob Sensitivity is only available for Single Knob runs.",
            )
        else:
            self.analysis_tabs.tabBar().setTabToolTip(
                self.sensitivity_tab_index,
                "Run Single Knob with at least two valid step points to populate Single-Knob Sensitivity.",
            )

        if analysis_mode == "timed_acquisition":
            if self.analysis_tabs.currentIndex() in {self.response_tab_index, self.sensitivity_tab_index}:
                self.analysis_tabs.setCurrentIndex(self.jitter_tab_index)
            if not waveform_visible and self.analysis_tabs.currentIndex() == self.waveform_tab_index:
                self.analysis_tabs.setCurrentIndex(self.jitter_tab_index)
            return

        self.analysis_tabs.setTabEnabled(self.response_tab_index, response_visible)
        if self.analysis_tabs.currentIndex() == self.waveform_tab_index:
            self.analysis_tabs.setCurrentIndex(self.response_tab_index if response_visible else self.jitter_tab_index)
        if analysis_mode == "single_knob_scan":
            self.analysis_tabs.setTabText(self.response_tab_index, "Response")
            self.analysis_tabs.tabBar().setTabToolTip(
                self.response_tab_index,
                "Mean read PV response versus knob value.",
            )
            if not sensitivity_enabled and self.analysis_tabs.currentIndex() == self.sensitivity_tab_index:
                self.analysis_tabs.setCurrentIndex(self.response_tab_index)
            return

        self.analysis_tabs.setTabText(self.response_tab_index, "Response")
        self.analysis_tabs.tabBar().setTabToolTip(
            self.response_tab_index,
            "Mean read PV response versus random point index.",
        )
        if self.analysis_tabs.currentIndex() == self.sensitivity_tab_index:
            self.analysis_tabs.setCurrentIndex(self.response_tab_index)

    def _set_analysis_available(self, available: bool) -> None:
        self._has_analysis_data = bool(available)
        self._refresh_ui_affordances()

    def _set_sensitivity_available(self, available: bool) -> None:
        self._has_sensitivity_data = bool(available)
        self._refresh_ui_affordances()

    def _mark_analysis_tabs_dirty(self) -> None:
        for tab_index in self._analysis_tab_loaders:
            self._analysis_tab_loaded[tab_index] = False

    def _prepare_analysis_views_for_current_data(self) -> None:
        self._mark_analysis_tabs_dirty()
        self._analysis_tab_loading = None
        self._waveform_analysis_signature = None
        self._waveform_analysis_result = {}
        self._pending_waveform_analysis_signature = None
        self.waveform_plot.clear_data("Open Waveform to inspect preview, feature trends, and waveform delay.")
        self._clear_jitter_table()
        self.jitter_plot.clear_data("Open Jitter to compute statistics and inspect point-by-point data.")
        self.correlation_plot.clear_data("Open Correlation to compute the matrix for the current run.")
        self.spectrum_plot.clear_data("Open Spectrum to compute spectra for the current run.")
        if self.current_run_mode == RunMode.KNOB_SCAN:
            self._clear_sensitivity_table()
            self.sensitivity_plot.clear_data(
                "Open Sensitivity to compute Single-Knob fits for the current run."
            )
            self._set_sensitivity_available(bool(self.current_run_steps))
        else:
            self._clear_sensitivity_table()
            self._set_sensitivity_available(False)
        self._set_analysis_available(self._has_current_run_data())
        self._set_waveform_available(
            self.current_run_mode == RunMode.TIMED_ACQUISITION and self._has_current_waveform_data()
        )

    def _ensure_visible_analysis_tab_loaded(self) -> None:
        if self.main_tabs.currentIndex() != self.analysis_page_index:
            return
        self._ensure_analysis_tab_loaded(self.analysis_tabs.currentIndex())

    def _ensure_analysis_tab_loaded(self, tab_index: int) -> None:
        if self.main_tabs.currentIndex() != self.analysis_page_index:
            return
        if tab_index not in self._analysis_tab_loaders:
            return
        if not self.analysis_tabs.isTabEnabled(tab_index):
            return
        if self._analysis_tab_loaded.get(tab_index, False):
            return
        if self._analysis_tab_loading == tab_index:
            return

        loader = self._analysis_tab_loaders.get(tab_index)
        if loader is None:
            return

        self._analysis_tab_loading = tab_index
        try:
            loader()
        finally:
            self._analysis_tab_loading = None
        self._analysis_tab_loaded[tab_index] = True

    def _ensure_random_seed(self, seed_text: str) -> int:
        seed, generated = resolve_random_seed(
            seed_text,
            int(QtCore.QDateTime.currentMSecsSinceEpoch()),
        )
        if generated:
            self.scan_panel.set_random_seed(seed)
        return seed

    def _collect_random_knob_ranges(self):
        return collect_random_knob_ranges(
            self._selected_knobs(),
            self.scan_panel.random_knob_state(),
        )

    @staticmethod
    def _generate_random_targets(knob_ranges, distribution: str, num_points: int, seed: int):
        return generate_random_targets(knob_ranges, distribution, num_points, seed)

    def _set_running_state(self, running: bool) -> None:
        self.action_stop.setEnabled(running)
        self.run_stop_button.setEnabled(running)
        self.config_panel.load_button.setEnabled(not running)
        self.config_panel.load_setup_button.setEnabled(not running)
        self.config_panel.save_setup_button.setEnabled(not running)
        self.config_panel.save_dir_edit.setEnabled(not running)
        self.config_panel.save_dir_browse_button.setEnabled(not running)
        self.object_panel.clear_button.setEnabled(not running)
        self.scan_panel.setEnabled(not running)
        self._sync_mode_buttons()
        self._refresh_ui_affordances()

    @staticmethod
    def _progress_tone(completed: int, total: int) -> str:
        return progress_tone(completed, total)

    @staticmethod
    def _run_status_tone(status: RunStatus) -> str:
        return run_status_tone(status)

    def _set_connection_summary(self, connected: int, total: int) -> None:
        label, tone = connection_summary(connected, total)
        self.status_panel.set_connection(label, tone=tone)

    def _ensure_epics_runtime(self) -> bool:
        try:
            require_pyepics()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "EPICS Runtime", str(exc))
            self.append_log(f"EPICS runtime unavailable: {exc}")
            return False
        return True

    def _create_run_metadata_or_warn(self, mode: RunMode, title: str):
        if self.loaded_config is None:
            return None
        operator = self.config_panel.operator_edit.text().strip()
        notes = self.config_panel.notes_edit.toPlainText().strip()
        save_dir = self.config_panel.save_dir_edit.text().strip() or "runs"
        self.run_service.configure_store(save_dir)
        try:
            config_snapshot_text = self._read_loaded_config_snapshot_text()
            return self.run_service.create_metadata(
                mode,
                machine=self.loaded_config.machine.name,
                config_path=self.loaded_config.source_path or "",
                config_snapshot_text=config_snapshot_text,
                operator=operator,
                notes=notes,
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, title, str(exc))
            self.append_log(f"{title} setup failed: {exc}")
            return None

    def _read_loaded_config_snapshot_text(self) -> str:
        return config_snapshot_text(self.loaded_config)

    def check_selected_connections(self) -> None:
        if not self._ensure_epics_runtime():
            return
        selected_objects = self._selected_objects()
        selected_knobs = self._selected_knobs()
        knob_pv_names = []
        for knob in selected_knobs:
            knob_pv_names.append((knob.write_pv, f"{knob.name} write"))
            if knob.readback_pv:
                knob_pv_names.append((knob.readback_pv, f"{knob.name} readback"))
        if not selected_objects and not selected_knobs:
            QtWidgets.QMessageBox.information(
                self,
                "Check EPICS",
                "Choose one or more read PVs or control PVs first.",
            )
            return

        connected = 0
        failed_names = []
        try:
            for obj in selected_objects:
                if self.epics_client.is_connected(obj.read_pv):
                    connected += 1
                else:
                    failed_names.append(obj.name)
            for pv_name, label in knob_pv_names:
                if self.epics_client.is_connected(pv_name):
                    connected += 1
                else:
                    failed_names.append(label)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Check EPICS", str(exc))
            self.append_log(f"Connection check failed: {exc}")
            return

        total = len(selected_objects) + len(knob_pv_names)
        self._last_connection_key = self._selection_key()
        self._set_connection_summary(connected, total)
        if failed_names:
            self.append_log("Disconnected PVs: " + ", ".join(failed_names))
        else:
            self.append_log(f"All {connected} selected PV endpoint(s) are connected.")

    def start_selected_mode(self) -> None:
        task_mode = self.scan_panel.task_mode()
        if task_mode == "single_knob_scan":
            self.start_knob_scan()
            return
        if task_mode == "multi_knob_random":
            self.start_multi_knob_random()
            return
        self.start_timed_acquisition()

    def start_timed_acquisition(self) -> None:
        if not self._ensure_epics_runtime():
            return
        if self.loaded_config is None:
            QtWidgets.QMessageBox.warning(self, "Monitor", "Load a PV library first.")
            return
        if self.state.run_status == RunStatus.RUNNING:
            return

        selected_objects = self._selected_objects()
        if not selected_objects:
            QtWidgets.QMessageBox.warning(self, "Monitor", "Choose one or more read PVs first.")
            return
        scalar_objects = [obj for obj in selected_objects if not self._is_waveform_object(obj)]
        waveform_objects = [obj for obj in selected_objects if self._is_waveform_object(obj)]

        shot_interval_sec = float(self.scan_panel.interval_spin.value())
        sample_count = int(self.scan_panel.count_spin.value())
        if shot_interval_sec <= 0 or sample_count <= 0:
            QtWidgets.QMessageBox.warning(self, "Monitor", "Interval and sample count must be positive.")
            return

        self._leave_saved_run_context()
        self.current_run_metadata = self._create_run_metadata_or_warn(RunMode.TIMED_ACQUISITION, "Monitor")
        if self.current_run_metadata is None:
            return

        self.current_run_records = []
        self.current_run_steps = []
        self.current_run_record_count = 0
        self.current_run_sample_timestamps = []
        self.current_series_values = {obj.id: [] for obj in scalar_objects}
        self.current_series_metadata = {obj.id: [] for obj in scalar_objects}
        self.current_waveform_records = {obj.id: [] for obj in waveform_objects}
        self.current_waveform_index = {}
        self.current_waveform_run_path = None
        self._waveform_analysis_signature = None
        self._waveform_analysis_inflight_signature = None
        self._waveform_analysis_result = {}
        self._pending_waveform_analysis_signature = None
        self.current_run_details = {
            "shot_interval_sec": shot_interval_sec,
            "sample_count": sample_count,
            "target_object_ids": [obj.id for obj in selected_objects],
            "scalar_object_ids": [obj.id for obj in scalar_objects],
            "waveform_object_ids": [obj.id for obj in waveform_objects],
        }
        self._loaded_run_used_fast_path = False
        self._loaded_run_used_legacy_batch_reconstruction = False
        self.trend_plot.reset_channels(scalar_objects)
        self.response_plot.reset_channels("", "", [])
        self._reset_analysis_views()
        self.current_run_mode = RunMode.TIMED_ACQUISITION
        self.state.run_status = RunStatus.RUNNING
        self._set_waveform_available(bool(waveform_objects))
        self._set_running_state(True)
        self.main_tabs.setCurrentIndex(self.run_tab_index)
        self.status_panel.set_mode("Starting", tone="info")
        self.task_service.start(
            {
                "mode": RunMode.TIMED_ACQUISITION.value,
                "shot_interval_sec": shot_interval_sec,
                "sample_count": sample_count,
                "targets": [obj.id for obj in selected_objects],
            }
        )

        self.acquisition_thread = QtCore.QThread(self)
        self.acquisition_worker = TimedAcquisitionWorker(
            self.sampler,
            selected_objects,
            shot_interval_sec,
            sample_count,
        )
        self.acquisition_worker.moveToThread(self.acquisition_thread)
        self.acquisition_thread.started.connect(self.acquisition_worker.run)
        self.acquisition_worker.signals.started.connect(self._on_acquisition_started)
        self.acquisition_worker.signals.batch_ready.connect(self._on_acquisition_batch)
        self.acquisition_worker.signals.connection_status.connect(self._on_acquisition_connection_status)
        self.acquisition_worker.signals.progress.connect(self._on_acquisition_progress)
        self.acquisition_worker.signals.finished.connect(self._on_acquisition_finished)
        self.acquisition_worker.signals.finished.connect(self.acquisition_thread.quit)
        self.acquisition_worker.signals.failed.connect(self._on_acquisition_failed)
        self.acquisition_worker.signals.failed.connect(self.acquisition_thread.quit)
        self.acquisition_thread.finished.connect(self._cleanup_acquisition_thread)
        self.acquisition_thread.start()

    def start_knob_scan(self) -> None:
        if not self._ensure_epics_runtime():
            return
        if self.loaded_config is None:
            QtWidgets.QMessageBox.warning(self, "Single Knob", "Load a PV library first.")
            return
        if self.state.run_status == RunStatus.RUNNING:
            return

        active_knob = self._active_knob()
        if active_knob is None:
            QtWidgets.QMessageBox.warning(self, "Single Knob", "Choose an active control PV first.")
            return

        selected_objects = self._selected_objects()
        if not selected_objects:
            QtWidgets.QMessageBox.warning(self, "Single Knob", "Choose one or more read PVs first.")
            return
        if any(self._is_waveform_object(obj) for obj in selected_objects):
            QtWidgets.QMessageBox.warning(
                self,
                "Single Knob",
                "Waveform capture is available in Monitor mode only. Deselect waveform read PVs or switch to Monitor.",
            )
            return

        try:
            scan_values = self._resolve_scan_values(active_knob)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Single Knob", str(exc))
            return

        settle_delay_sec = float(self.scan_panel.settle_spin.value())
        shot_interval_sec = float(self.scan_panel.scan_sample_interval_spin.value())
        sample_count_per_step = int(self.scan_panel.step_sample_spin.value())
        restore_initial_value = bool(self.scan_panel.restore_check.isChecked())

        if sample_count_per_step <= 0:
            QtWidgets.QMessageBox.warning(self, "Single Knob", "Samples / Point must be positive.")
            return

        if self.loaded_config.defaults.safety.confirm_before_write:
            answer = QtWidgets.QMessageBox.question(
                self,
                "Single Knob",
                (
                    f"Start Single Knob on {active_knob.name} with {len(scan_values)} point(s)?\n"
                    f"Range: {min(scan_values):.6g} to {max(scan_values):.6g} {active_knob.unit}"
                ),
            )
            if answer != QtWidgets.QMessageBox.Yes:
                return

        self._leave_saved_run_context()
        self.current_run_metadata = self._create_run_metadata_or_warn(RunMode.KNOB_SCAN, "Single Knob")
        if self.current_run_metadata is None:
            return

        self.current_run_records = []
        self.current_run_steps = []
        self.current_run_record_count = 0
        self.current_run_sample_timestamps = []
        self.current_series_values = {obj.id: [] for obj in selected_objects}
        self.current_series_metadata = {obj.id: [] for obj in selected_objects}
        self._reset_waveform_state(clear_widget=False)
        self.current_run_details = {
            "knob_id": active_knob.id,
            "scan_values": list(scan_values),
            "settle_delay_sec": settle_delay_sec,
            "shot_interval_sec": shot_interval_sec,
            "sample_count_per_step": sample_count_per_step,
            "target_object_ids": [obj.id for obj in selected_objects],
            "restore_initial_value": restore_initial_value,
        }
        self._loaded_run_used_fast_path = False
        self._loaded_run_used_legacy_batch_reconstruction = False
        self.trend_plot.reset_channels(selected_objects)
        self.response_plot.reset_channels(active_knob.name, active_knob.unit, selected_objects)
        self._reset_analysis_views()
        self.current_run_mode = RunMode.KNOB_SCAN
        self.state.run_status = RunStatus.RUNNING
        self._set_running_state(True)
        self.main_tabs.setCurrentIndex(self.run_tab_index)
        self.status_panel.set_mode("Starting", tone="info")
        self.task_service.start(
            {
                "mode": RunMode.KNOB_SCAN.value,
                "knob_id": active_knob.id,
                "scan_values": scan_values,
                "sample_count_per_step": sample_count_per_step,
                "targets": [obj.id for obj in selected_objects],
            }
        )

        self.acquisition_thread = QtCore.QThread(self)
        self.acquisition_worker = KnobScanWorker(
            self.epics_client,
            self.sampler,
            active_knob,
            selected_objects,
            scan_values,
            settle_delay_sec,
            sample_count_per_step,
            shot_interval_sec,
            restore_initial_value,
        )
        self.acquisition_worker.moveToThread(self.acquisition_thread)
        self.acquisition_thread.started.connect(self.acquisition_worker.run)
        self.acquisition_worker.signals.started.connect(self._on_scan_started)
        self.acquisition_worker.signals.batch_ready.connect(self._on_scan_batch)
        self.acquisition_worker.signals.step_ready.connect(self._on_scan_step_ready)
        self.acquisition_worker.signals.progress.connect(self._on_scan_progress)
        self.acquisition_worker.signals.message.connect(self.append_log)
        self.acquisition_worker.signals.finished.connect(self._on_scan_finished)
        self.acquisition_worker.signals.finished.connect(self.acquisition_thread.quit)
        self.acquisition_worker.signals.failed.connect(self._on_scan_failed)
        self.acquisition_worker.signals.failed.connect(self.acquisition_thread.quit)
        self.acquisition_thread.finished.connect(self._cleanup_acquisition_thread)
        self.acquisition_thread.start()

    def start_multi_knob_random(self) -> None:
        if not self._ensure_epics_runtime():
            return
        if self.loaded_config is None:
            QtWidgets.QMessageBox.warning(self, "Random Multi-Knob", "Load a PV library first.")
            return
        if self.state.run_status == RunStatus.RUNNING:
            return
        selected_knobs = self._selected_knobs()
        if not selected_knobs:
            QtWidgets.QMessageBox.warning(self, "Random Multi-Knob", "Choose one or more control PVs first.")
            return
        selected_objects = self._selected_objects()
        if not selected_objects:
            QtWidgets.QMessageBox.warning(self, "Random Multi-Knob", "Choose one or more read PVs first.")
            return
        if any(self._is_waveform_object(obj) for obj in selected_objects):
            QtWidgets.QMessageBox.warning(
                self,
                "Random Multi-Knob",
                "Waveform capture is available in Monitor mode only. Deselect waveform read PVs or switch to Monitor.",
            )
            return
        try:
            knob_ranges = self._collect_random_knob_ranges()
            config = self.scan_panel.random_configuration()
            seed = self._ensure_random_seed(str(config["seed_text"]))
            target_steps = self._generate_random_targets(
                knob_ranges,
                distribution=str(config["distribution"]),
                num_points=int(config["num_points"]),
                seed=seed,
            )
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Random Multi-Knob", str(exc))
            return

        settle_delay_sec = float(config["settle_delay_sec"])
        shot_interval_sec = float(config["shot_interval_sec"])
        sample_count_per_point = int(config["sample_count_per_point"])
        restore_initial_values = bool(config["restore_initial_values"])

        if sample_count_per_point <= 0:
            QtWidgets.QMessageBox.warning(self, "Random Multi-Knob", "Samples / Point must be positive.")
            return

        if self.loaded_config.defaults.safety.confirm_before_write:
            range_lines = [
                f"{spec['knob'].name}: {spec['low']:.6g} .. {spec['high']:.6g} {spec['knob'].unit}"
                for spec in knob_ranges
            ]
            answer = QtWidgets.QMessageBox.question(
                self,
                "Random Multi-Knob",
                (
                    f"Start Random Multi-Knob with {len(target_steps)} point(s), "
                    f"{len(knob_ranges)} knob(s), seed={seed}?\n\n"
                    + "\n".join(range_lines[:8])
                    + ("\n..." if len(range_lines) > 8 else "")
                ),
            )
            if answer != QtWidgets.QMessageBox.Yes:
                return

        self._leave_saved_run_context()
        self.current_run_metadata = self._create_run_metadata_or_warn(
            RunMode.MULTI_KNOB_RANDOM,
            "Random Multi-Knob",
        )
        if self.current_run_metadata is None:
            return

        self.current_run_records = []
        self.current_run_steps = []
        self.current_run_record_count = 0
        self.current_run_sample_timestamps = []
        self.current_series_values = {obj.id: [] for obj in selected_objects}
        self.current_series_metadata = {obj.id: [] for obj in selected_objects}
        self._reset_waveform_state(clear_widget=False)
        self.current_run_details = {
            "distribution": str(config["distribution"]),
            "seed": seed,
            "num_points": int(config["num_points"]),
            "settle_delay_sec": settle_delay_sec,
            "shot_interval_sec": shot_interval_sec,
            "sample_count_per_point": sample_count_per_point,
            "restore_initial_values": restore_initial_values,
            "target_object_ids": [obj.id for obj in selected_objects],
            "knob_ranges": [
                {
                    "knob_id": spec["knob"].id,
                    "low": spec["low"],
                    "high": spec["high"],
                }
                for spec in knob_ranges
            ],
            "target_steps": list(target_steps),
        }
        self._loaded_run_used_fast_path = False
        self._loaded_run_used_legacy_batch_reconstruction = False
        self.trend_plot.reset_channels(selected_objects)
        self.response_plot.reset_channels("Random Point Index", "", selected_objects)
        self._reset_analysis_views()
        self.current_run_mode = RunMode.MULTI_KNOB_RANDOM
        self.state.run_status = RunStatus.RUNNING
        self._set_running_state(True)
        self.main_tabs.setCurrentIndex(self.run_tab_index)
        self.status_panel.set_mode("Starting", tone="info")
        self.task_service.start(
            {
                "mode": RunMode.MULTI_KNOB_RANDOM.value,
                "seed": seed,
                "distribution": str(config["distribution"]),
                "num_points": int(config["num_points"]),
                "knob_ids": [spec["knob"].id for spec in knob_ranges],
                "targets": [obj.id for obj in selected_objects],
            }
        )

        self.acquisition_thread = QtCore.QThread(self)
        self.acquisition_worker = MultiKnobRandomWorker(
            self.epics_client,
            self.sampler,
            [spec["knob"] for spec in knob_ranges],
            selected_objects,
            target_steps,
            settle_delay_sec,
            sample_count_per_point,
            shot_interval_sec,
            restore_initial_values,
        )
        self.acquisition_worker.moveToThread(self.acquisition_thread)
        self.acquisition_thread.started.connect(self.acquisition_worker.run)
        self.acquisition_worker.signals.started.connect(self._on_random_started)
        self.acquisition_worker.signals.batch_ready.connect(self._on_random_batch)
        self.acquisition_worker.signals.step_ready.connect(self._on_random_step_ready)
        self.acquisition_worker.signals.progress.connect(self._on_random_progress)
        self.acquisition_worker.signals.message.connect(self.append_log)
        self.acquisition_worker.signals.finished.connect(self._on_random_finished)
        self.acquisition_worker.signals.finished.connect(self.acquisition_thread.quit)
        self.acquisition_worker.signals.failed.connect(self._on_random_failed)
        self.acquisition_worker.signals.failed.connect(self.acquisition_thread.quit)
        self.acquisition_thread.finished.connect(self._cleanup_acquisition_thread)
        self.acquisition_thread.start()

    def stop_active_run(self) -> None:
        if self.acquisition_worker is not None:
            self.acquisition_worker.stop()
            if self.current_run_mode == RunMode.KNOB_SCAN:
                mode_label = "Single Knob"
            elif self.current_run_mode == RunMode.MULTI_KNOB_RANDOM:
                mode_label = "Random Multi-Knob"
            else:
                mode_label = "Monitor"
            self.append_log(f"Stop requested for {mode_label}.")

    def _on_acquisition_started(self, sample_count: int) -> None:
        self._set_running_state(True)
        self.status_panel.set_mode("Monitor", tone="info")
        self.status_panel.set_sample(f"0/{sample_count}", tone=self._progress_tone(0, sample_count))
        self.status_panel.set_step("-", tone="subtle")
        self.status_panel.set_time("--", tone="subtle")
        self.append_log(
            f"Started Monitor run {self.current_run_metadata.run_id} with {sample_count} samples."
        )

    def _on_acquisition_batch(self, sample_index: int, batch) -> None:
        scalar_samples = list(getattr(batch, "scalar_samples", batch if isinstance(batch, list) else []))
        waveform_samples = list(getattr(batch, "waveform_samples", []))

        self.current_run_records.extend(scalar_samples)
        self.current_run_record_count = len(self.current_run_records)
        self.run_service.append_samples(scalar_samples)
        self.run_service.append_waveforms(waveform_samples)
        for sample in scalar_samples:
            recorded_sample_index = int(sample.batch_index) if sample.batch_index is not None else sample_index
            self._record_run_sample_timestamp(recorded_sample_index, sample.timestamp)
            self.current_series_values.setdefault(sample.pv_id, []).append(sample.value)
            self.current_series_metadata.setdefault(sample.pv_id, []).append(
                {
                    "sample_index": recorded_sample_index,
                    "step_index": sample.step_index,
                    "timestamp": sample.timestamp,
                    "value": sample.value,
                }
            )
        for waveform in waveform_samples:
            recorded_sample_index = int(waveform.batch_index) if waveform.batch_index is not None else sample_index
            self._record_run_sample_timestamp(recorded_sample_index, waveform.timestamp)
            self.current_waveform_records.setdefault(waveform.pv_id, []).append(waveform)
            while len(self.current_waveform_records.get(waveform.pv_id, [])) > 200:
                self.current_waveform_records[waveform.pv_id].pop(0)
        if scalar_samples:
            self.trend_plot.append_batch(sample_index if not scalar_samples else recorded_sample_index, scalar_samples)

        last_sample = scalar_samples[-1] if scalar_samples else (waveform_samples[-1] if waveform_samples else None)
        if last_sample is not None:
            if isinstance(last_sample, SampleRecord):
                current_value = "nan" if math.isnan(last_sample.value) else f"{last_sample.value:.6g}"
                current_text = f"{last_sample.pv_id}: {current_value}"
            else:
                current_text = f"{last_sample.pv_id}: {len(last_sample.values)} pts"
            self.status_panel.set_current(current_text, tone="subtle")
            self.status_panel.set_time(last_sample.timestamp.strftime("%H:%M:%S.%f")[:-3], tone="subtle")
        if waveform_samples and self.main_tabs.currentIndex() == self.analysis_page_index and self.analysis_tabs.currentIndex() == self.waveform_tab_index:
            self._analysis_tab_loaded[self.waveform_tab_index] = False
            self._populate_waveform_view()

    def _on_acquisition_connection_status(self, connected: int, total: int) -> None:
        self._set_connection_summary(connected, total)

    def _on_acquisition_progress(self, completed: int, total: int) -> None:
        self.status_panel.set_sample(f"{completed}/{total}", tone=self._progress_tone(completed, total))

    def _on_acquisition_finished(self, outcome: str) -> None:
        status = RunStatus.COMPLETED if outcome == "completed" else RunStatus.STOPPED
        self._finalize_run(status, warning=None)
        self.append_log(f"Monitor {outcome}.")

    def _on_acquisition_failed(self, message: str) -> None:
        self._finalize_run(RunStatus.FAILED, warning=message)
        QtWidgets.QMessageBox.critical(self, "Monitor", message)

    def _on_scan_started(self, total_steps: int, sample_count_per_step: int) -> None:
        self._set_running_state(True)
        self.status_panel.set_mode("Single Knob", tone="info")
        self.status_panel.set_sample(
            f"0/{sample_count_per_step}",
            tone=self._progress_tone(0, sample_count_per_step),
        )
        self.status_panel.set_step(f"0/{total_steps}", tone=self._progress_tone(0, total_steps))
        self.status_panel.set_time("--", tone="subtle")
        self.append_log(
            f"Started Single Knob run {self.current_run_metadata.run_id} with {total_steps} step(s)."
        )

    def _on_scan_batch(self, overall_index: int, step_index: int, sample_index: int, target_value: float, readback_value, batch) -> None:
        self.current_run_records.extend(batch)
        self.current_run_record_count = len(self.current_run_records)
        self.run_service.append_samples(batch)
        connected = 0
        for sample in batch:
            recorded_sample_index = int(sample.batch_index) if sample.batch_index is not None else overall_index
            if sample.connected:
                connected += 1
            self._record_run_sample_timestamp(recorded_sample_index, sample.timestamp)
            self.current_series_values.setdefault(sample.pv_id, []).append(sample.value)
            self.current_series_metadata.setdefault(sample.pv_id, []).append(
                {
                    "sample_index": recorded_sample_index,
                    "step_index": sample.step_index,
                    "timestamp": sample.timestamp,
                    "value": sample.value,
                }
            )
        self.trend_plot.append_batch(overall_index if not batch else recorded_sample_index, batch)
        self._set_connection_summary(connected, len(batch))

        last_sample = batch[-1] if batch else None
        if last_sample is not None:
            current_value = "nan" if math.isnan(last_sample.value) else f"{last_sample.value:.6g}"
            readback_text = "--" if readback_value is None else f"{readback_value:.6g}"
            self.status_panel.set_current(
                f"step={step_index + 1} knob={target_value:.6g} rb={readback_text} "
                f"last={last_sample.pv_id}:{current_value}",
                tone="subtle",
            )
            self.status_panel.set_time(last_sample.timestamp.strftime("%H:%M:%S.%f")[:-3], tone="subtle")

    def _on_scan_step_ready(self, step_index: int, total_steps: int, target_value: float, readback_value, step_samples) -> None:
        step_record = ScanStepRecord(
            step_index=step_index,
            target_value=target_value,
            readback_value=float(readback_value) if readback_value is not None else None,
            started_at=step_samples[0].timestamp if step_samples else self.current_run_metadata.created_at,
            settled_at=step_samples[-1].timestamp if step_samples else None,
            samples=list(step_samples),
        )
        self.current_run_steps.append(step_record)
        self.run_service.append_step(step_record)
        axis_value = self._single_knob_step_axis_value(step_record)
        if axis_value is not None and math.isfinite(axis_value):
            self.response_plot.append_step(float(axis_value), step_samples)
        self.status_panel.set_step(
            f"{step_index + 1}/{total_steps}",
            tone=self._progress_tone(step_index + 1, total_steps),
        )

    def _on_scan_progress(self, completed_steps: int, total_steps: int, completed_samples: int, samples_per_step: int) -> None:
        self.status_panel.set_step(
            f"{completed_steps}/{total_steps}",
            tone=self._progress_tone(completed_steps, total_steps),
        )
        self.status_panel.set_sample(
            f"{completed_samples}/{samples_per_step}",
            tone=self._progress_tone(completed_samples, samples_per_step),
        )

    def _on_scan_finished(self, outcome: str, restored: bool) -> None:
        status = RunStatus.COMPLETED if outcome == "completed" else RunStatus.STOPPED
        self._finalize_run(status, warning=None)
        if restored:
            self.append_log("Single Knob restored the initial control PV value.")
        self.append_log(f"Single Knob {outcome}.")

    def _on_scan_failed(self, message: str) -> None:
        self._finalize_run(RunStatus.FAILED, warning=message)
        QtWidgets.QMessageBox.critical(self, "Single Knob", message)

    def _on_random_started(self, total_steps: int, sample_count_per_point: int, knob_count: int) -> None:
        self._set_running_state(True)
        self.status_panel.set_mode("Random Multi-Knob", tone="info")
        self.status_panel.set_sample(
            f"0/{sample_count_per_point}",
            tone=self._progress_tone(0, sample_count_per_point),
        )
        self.status_panel.set_step(f"0/{total_steps}", tone=self._progress_tone(0, total_steps))
        self.status_panel.set_time("--", tone="subtle")
        self.append_log(
            f"Started Random Multi-Knob run {self.current_run_metadata.run_id} "
            f"with {total_steps} point(s) across {knob_count} knob(s)."
        )

    def _on_random_batch(self, overall_index: int, step_index: int, sample_index: int, target_values, readback_values, batch) -> None:
        self.current_run_records.extend(batch)
        self.current_run_record_count = len(self.current_run_records)
        self.run_service.append_samples(batch)
        connected = 0
        for sample in batch:
            recorded_sample_index = int(sample.batch_index) if sample.batch_index is not None else overall_index
            if sample.connected:
                connected += 1
            self._record_run_sample_timestamp(recorded_sample_index, sample.timestamp)
            self.current_series_values.setdefault(sample.pv_id, []).append(sample.value)
            self.current_series_metadata.setdefault(sample.pv_id, []).append(
                {
                    "sample_index": recorded_sample_index,
                    "step_index": sample.step_index,
                    "timestamp": sample.timestamp,
                    "value": sample.value,
                }
            )
        self.trend_plot.append_batch(overall_index if not batch else recorded_sample_index, batch)
        self._set_connection_summary(connected, len(batch))

        last_sample = batch[-1] if batch else None
        if last_sample is not None:
            current_value = "nan" if math.isnan(last_sample.value) else f"{last_sample.value:.6g}"
            summary_parts = []
            for knob_id, value in list(target_values.items())[:2]:
                summary_parts.append(f"{knob_id}={float(value):.6g}")
            knob_summary = ", ".join(summary_parts)
            if len(target_values) > 2:
                knob_summary += ", ..."
            self.status_panel.set_current(
                f"point={step_index + 1} sample={sample_index + 1} "
                f"[{knob_summary}] last={last_sample.pv_id}:{current_value}",
                tone="subtle",
            )
            self.status_panel.set_time(last_sample.timestamp.strftime("%H:%M:%S.%f")[:-3], tone="subtle")

    def _on_random_step_ready(self, step_index: int, total_steps: int, target_values, readback_values, step_samples) -> None:
        step_record = MultiKnobStepRecord(
            step_index=step_index,
            target_values={str(key): float(value) for key, value in dict(target_values).items()},
            readback_values={
                str(key): (float(value) if value is not None else None)
                for key, value in dict(readback_values).items()
            },
            started_at=step_samples[0].timestamp if step_samples else self.current_run_metadata.created_at,
            settled_at=step_samples[-1].timestamp if step_samples else None,
            samples=list(step_samples),
        )
        self.current_run_steps.append(step_record)
        self.run_service.append_step(step_record)
        self.response_plot.append_step(float(step_index + 1), step_samples)
        self.status_panel.set_step(
            f"{step_index + 1}/{total_steps}",
            tone=self._progress_tone(step_index + 1, total_steps),
        )

    def _on_random_progress(self, completed_steps: int, total_steps: int, completed_samples: int, samples_per_point: int) -> None:
        self.status_panel.set_step(
            f"{completed_steps}/{total_steps}",
            tone=self._progress_tone(completed_steps, total_steps),
        )
        self.status_panel.set_sample(
            f"{completed_samples}/{samples_per_point}",
            tone=self._progress_tone(completed_samples, samples_per_point),
        )

    def _on_random_finished(self, outcome: str, restored: bool) -> None:
        status = RunStatus.COMPLETED if outcome == "completed" else RunStatus.STOPPED
        self._finalize_run(status, warning=None)
        if restored:
            self.append_log("Random Multi-Knob restored the initial control PV values.")
        self.append_log(f"Random Multi-Knob {outcome}.")

    def _on_random_failed(self, message: str) -> None:
        self._finalize_run(RunStatus.FAILED, warning=message)
        QtWidgets.QMessageBox.critical(self, "Random Multi-Knob", message)

    def _finalize_run(self, status: RunStatus, warning: str | None) -> None:
        self.state.run_status = status
        self.task_service.status = status
        self._set_running_state(False)
        terminal_tone = self._run_status_tone(status)
        self.status_panel.set_mode(status.value, tone=terminal_tone)
        self.status_panel.set_sample(self.status_panel.sample_value.text(), tone=terminal_tone)
        self.status_panel.set_step(self.status_panel.step_value.text(), tone=terminal_tone)

        warnings = [warning] if warning else []
        if self.current_run_metadata is not None:
            result = RunResult(
                metadata=self.current_run_metadata,
                status=status,
                samples=list(self.current_run_records),
                steps=list(self.current_run_steps),
                warnings=warnings,
                details=dict(self.current_run_details),
            )
            try:
                path = self.run_service.save_result(result)
            except Exception as exc:
                self.append_log(f"Failed to save run output: {exc}")
                QtWidgets.QMessageBox.warning(
                    self,
                    "Save Run",
                    f"Run data could not be fully saved:\n{exc}",
                )
            else:
                self.append_log(f"Saved run summary to {path} and raw samples to {path.parent / 'raw.h5'}.")

        if status != RunStatus.FAILED:
            self._prepare_analysis_views_for_current_data()
            self._ensure_visible_analysis_tab_loaded()
        if warning:
            if self.current_run_mode == RunMode.KNOB_SCAN:
                mode_label = "Single Knob"
            elif self.current_run_mode == RunMode.MULTI_KNOB_RANDOM:
                mode_label = "Random Multi-Knob"
            else:
                mode_label = "Monitor"
            self.append_log(f"{mode_label} failed: {warning}")

    def _reset_analysis_views(self) -> None:
        self._mark_analysis_tabs_dirty()
        self._analysis_tab_loading = None
        self.waveform_plot.clear_data(
            "Run a monitor acquisition with waveform objects to inspect waveform preview, features, and delay."
        )
        self._clear_jitter_table()
        self._clear_sensitivity_table()
        self.correlation_plot.clear_data(
            "Run a task with at least two read PVs to populate the correlation matrix."
        )
        self.spectrum_plot.clear_data(
            "Run a task to populate spectrum analysis. Sample interval is estimated from valid timestamps."
        )

    @staticmethod
    def _parse_manual_scan_values(text: str) -> list[float]:
        return parse_manual_scan_values(text)

    def _resolve_scan_values(self, knob, preview_only: bool = False) -> list[float]:
        spec = self.scan_panel.scan_value_definition()
        mode = str(spec["mode"])
        if mode == "manual":
            return self._parse_manual_scan_values(str(spec["text"]))
        if mode == "range_step":
            return self._generate_values_by_step(
                float(spec["start"]),
                float(spec["stop"]),
                float(spec["step"]),
            )
        if mode == "range_points":
            return self._generate_values_by_points(
                float(spec["start"]),
                float(spec["stop"]),
                int(spec["num_points"]),
            )
        if mode == "symmetric_points":
            center = self._read_knob_center_value(knob, preview_only=preview_only)
            half_range = float(spec["half_range"])
            num_points = int(spec["num_points"])
            return self._generate_values_by_points(
                center - half_range,
                center + half_range,
                num_points,
            )
        raise ValueError(f"Unsupported scan value mode: {mode}")

    def _read_knob_center_value(self, knob, preview_only: bool = False) -> float:
        readback_pv = knob.readback_pv or knob.write_pv
        if preview_only:
            try:
                require_pyepics()
            except Exception as exc:
                raise ValueError(
                    "Symmetric preview needs EPICS runtime. Click 'Refresh Preview' after EPICS is available."
                ) from exc
        result = self.epics_client.read(readback_pv)
        if not result.connected or result.value is None:
            raise ValueError(
                f"Could not read current value from {knob.name} readback. Click 'Refresh Preview' after connection is ready."
            )
        try:
            return float(result.value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Current value of {knob.name} is not numeric.") from exc

    @staticmethod
    def _generate_values_by_step(start: float, stop: float, step: float) -> list[float]:
        return generate_values_by_step(start, stop, step)

    @staticmethod
    def _generate_values_by_points(start: float, stop: float, num_points: int) -> list[float]:
        return generate_values_by_points(start, stop, num_points)

    def _rebuild_loaded_run_views(self) -> None:
        selected_objects = self._selected_objects()
        scalar_objects = [obj for obj in selected_objects if not self._is_waveform_object(obj)]
        self.current_series_values = {obj.id: [] for obj in scalar_objects}
        self.current_series_metadata = {obj.id: [] for obj in scalar_objects}
        self.current_run_sample_timestamps = []
        self.trend_plot.reset_channels(scalar_objects)
        self.trend_plot.clear_highlight()
        self.response_plot.reset_channels("", "", [])
        self._reset_analysis_views()

        trend_x_values = {obj.id: [] for obj in scalar_objects}
        trend_sample_timestamps = []
        fallback_object_count: int | None = None
        for record_index, sample in enumerate(self.current_run_records):
            sample_index = sample.batch_index
            if sample_index is None:
                if fallback_object_count is None:
                    fallback_object_count = max(self._loaded_run_object_count_hint(), 1)
                sample_index = record_index // fallback_object_count
            sample_index = int(sample_index)

            self.current_series_values.setdefault(sample.pv_id, []).append(sample.value)
            self.current_series_metadata.setdefault(sample.pv_id, []).append(
                {
                    "sample_index": sample_index,
                    "step_index": sample.step_index,
                    "timestamp": sample.timestamp,
                    "value": sample.value,
                }
            )
            self._record_run_sample_timestamp(sample_index, sample.timestamp)
            if sample.pv_id in trend_x_values:
                trend_x_values[sample.pv_id].append(sample_index)
            if sample_index >= len(trend_sample_timestamps):
                trend_sample_timestamps.extend([None] * (sample_index + 1 - len(trend_sample_timestamps)))
            if trend_sample_timestamps[sample_index] is None:
                trend_sample_timestamps[sample_index] = sample.timestamp

        self.trend_plot.set_series_history(
            {
                pv_id: (trend_x_values[pv_id], self.current_series_values.get(pv_id, []))
                for pv_id in trend_x_values
            },
            sample_timestamps=trend_sample_timestamps,
        )

        if self.current_run_mode == RunMode.KNOB_SCAN:
            self._refresh_single_knob_response_plot()
        elif self.current_run_mode == RunMode.MULTI_KNOB_RANDOM:
            self.response_plot.reset_channels("Random Point Index", "", selected_objects)
            for step in self.current_run_steps:
                self.response_plot.append_step(float(step.step_index + 1), step.samples)
        else:
            self.response_plot.reset_channels("", "", [])

        self._prepare_analysis_views_for_current_data()
        self._refresh_ui_affordances()

    def _loaded_run_object_count_hint(self) -> int:
        return loaded_run_object_count_hint(
            self.current_run_details,
            [obj.id for obj in self._selected_objects()],
            [sample.pv_id for sample in self.current_run_records],
        )

    def _cleanup_acquisition_thread(self) -> None:
        if self.acquisition_worker is not None:
            self.acquisition_worker.deleteLater()
        if self.acquisition_thread is not None:
            self.acquisition_thread.deleteLater()
        self.acquisition_worker = None
        self.acquisition_thread = None

    def _clear_jitter_table(self) -> None:
        self.jitter_table.setRowCount(0)
        self._update_jitter_filter_status()
        self.jitter_plot.clear_data("Run a task to populate jitter statistics and inspect point-by-point data.")
        self._set_analysis_available(False)

    def _clear_sensitivity_table(self) -> None:
        self.sensitivity_plot.clear_data(
            "Single Knob sensitivity fits the mean read PV response versus the selected knob axis for each step."
        )
        self._set_sensitivity_available(False)

    def _populate_analysis_views(self) -> None:
        self._populate_jitter_table()
        self._populate_sensitivity_view()
        self._populate_correlation_view()
        self._populate_spectrum_view()
        for tab_index in self._analysis_tab_loaders:
            self._analysis_tab_loaded[tab_index] = True

    def _populate_jitter_table(self) -> None:
        if self.loaded_config is None:
            self._clear_jitter_table()
            return

        objects_by_id = {obj.id: obj for obj in self.loaded_config.objects}
        rows = []
        plot_rows = []
        total_removed = 0
        affected_variables = 0
        for pv_id in self.current_series_values:
            filtered = self._filtered_series_payload(pv_id)
            filtered_values = list(filtered["filtered_values"])
            if not filtered_values:
                continue
            filtered_sample_indices = list(filtered["filtered_sample_indices"])
            raw_count = int(filtered["raw_count"])
            removed_count = int(filtered["removed_count"])
            total_removed += removed_count
            if removed_count:
                affected_variables += 1

            stats = compute_jitter_stats(filtered_values)
            obj = objects_by_id.get(pv_id)
            name = obj.name if obj else pv_id
            unit = obj.unit if obj else ""
            if len(filtered_sample_indices) != len(filtered_values):
                filtered_sample_indices = list(range(len(filtered_values)))
            rows.append((pv_id, name, stats, unit, raw_count, removed_count))
            plot_rows.append(
                {
                    "pv_id": pv_id,
                    "label": name,
                    "unit": unit,
                    "sample_indices": filtered_sample_indices,
                    "values": filtered_values,
                    "mean": stats.mean,
                    "std": stats.std,
                    "rms": stats.rms,
                    "p2p": stats.peak_to_peak,
                    "raw_count": raw_count,
                    "removed_count": removed_count,
                    "filter_mode": str(filtered["filter_mode"]),
                }
            )

        self.jitter_table.setRowCount(len(rows))
        for row_index, (pv_id, name, stats, unit, raw_count, removed_count) in enumerate(rows):
            values = [
                name,
                str(stats.count),
                f"{stats.mean:.6g}",
                f"{stats.std:.6g}",
                f"{stats.rms:.6g}",
                f"{stats.peak_to_peak:.6g}",
                f"{stats.minimum:.6g}",
                f"{stats.maximum:.6g}",
                unit,
            ]
            tooltip = (
                f"Raw valid samples: {raw_count}\n"
                f"Samples kept in Jitter tab: {stats.count}\n"
                f"Filtered outliers: {removed_count}"
            )
            for col_index, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                if col_index == 0:
                    item.setData(QtCore.Qt.UserRole, pv_id)
                item.setToolTip(tooltip)
                self.jitter_table.setItem(row_index, col_index, item)
        self._apply_jitter_table_column_layout()
        self._update_jitter_filter_status(total_removed=total_removed, affected_variables=affected_variables)
        current_pv_ids = self.jitter_plot.current_pv_ids()
        focused_pv_id = self.jitter_plot.focused_pv_id()
        if plot_rows:
            self.jitter_plot.set_series_rows(
                plot_rows,
                current_pv_ids=current_pv_ids,
                focused_pv_id=focused_pv_id,
                sample_timestamps=self.current_run_sample_timestamps,
            )
        else:
            message = "No valid jitter samples are available."
            if self._jitter_outlier_filter_enabled() and total_removed:
                message = "All jitter samples were removed by the outlier filter."
            self.jitter_plot.clear_data(message)
        if plot_rows:
            blockers = [QtCore.QSignalBlocker(self.jitter_table)]
            try:
                target_row = 0
                if focused_pv_id:
                    for row_index in range(self.jitter_table.rowCount()):
                        item = self.jitter_table.item(row_index, 0)
                        if item is not None and str(item.data(QtCore.Qt.UserRole)) == focused_pv_id:
                            target_row = row_index
                            break
                self.jitter_table.setCurrentCell(target_row, 0)
            finally:
                del blockers
        self._set_analysis_available(self._has_current_run_data())

    def _populate_sensitivity_view(self) -> None:
        if self.loaded_config is None:
            self._clear_sensitivity_table()
            return
        if self.current_run_mode != RunMode.KNOB_SCAN:
            self._clear_sensitivity_table()
            self.sensitivity_plot.clear_data("Single-Knob Sensitivity is only available for Single Knob runs.")
            return

        objects_by_id = {obj.id: obj for obj in self.loaded_config.objects}
        selected_ids = list(self.current_run_details.get("target_object_ids", []))
        stats_by_id = {
            row.pv_id: row
            for row in compute_single_knob_sensitivity(
                self.current_run_steps,
                axis_source=self._single_knob_axis_source,
            )
        }
        ordered_ids = [pv_id for pv_id in selected_ids if pv_id in stats_by_id]
        for pv_id in stats_by_id:
            if pv_id not in ordered_ids:
                ordered_ids.append(pv_id)

        if not ordered_ids:
            self._clear_sensitivity_table()
            self.sensitivity_plot.clear_data(
                "Need at least two valid Single Knob step points per read PV to fit sensitivity."
            )
            return

        knob_id = str(self.current_run_details.get("knob_id", ""))
        knob = next((item for item in self.loaded_config.knobs if item.id == knob_id), None)
        knob_name = self._single_knob_axis_name(knob.name if knob is not None else "Knob Value")
        knob_unit = knob.unit if knob is not None else ""

        rows = []

        for pv_id in ordered_ids:
            stats = stats_by_id[pv_id]
            obj = objects_by_id.get(pv_id)
            pv_name = obj.name if obj is not None else pv_id
            object_unit = obj.unit if obj is not None else ""
            if object_unit and knob_unit:
                slope_unit = f"{object_unit}/{knob_unit}"
            elif object_unit:
                slope_unit = object_unit
            else:
                slope_unit = ""
            rows.append(
                {
                    "pv_id": pv_id,
                    "name": pv_name,
                    "unit": object_unit,
                    "slope_unit": slope_unit,
                    "point_count": stats.point_count,
                    "knob_span": stats.knob_span,
                    "response_span": stats.response_span,
                    "slope": stats.slope,
                    "intercept": stats.intercept,
                    "correlation": stats.correlation,
                    "r_squared": stats.r_squared,
                    "step_indices": list(stats.step_indices),
                    "knob_values": list(stats.knob_values),
                    "response_values": list(stats.response_values),
                }
            )

        self.sensitivity_plot.set_rows(
            rows,
            knob_name=knob_name,
            knob_unit=knob_unit,
            axis_summary_text=self._single_knob_axis_summary_text(),
        )
        self._set_sensitivity_available(True)

    def _populate_correlation_view(self) -> None:
        if self.loaded_config is None:
            self.correlation_plot.clear_data("Load a PV library to compute correlation.")
            return

        objects_by_id = {obj.id: obj for obj in self.loaded_config.objects}
        series_by_name = {}
        series_metadata_by_name = {}
        total_removed = 0
        affected_variables = 0
        for pv_id, values in self.current_series_values.items():
            if pv_id not in objects_by_id:
                continue
            filtered = self._filtered_series_payload(pv_id)
            aligned_values = list(filtered["aligned_values"])
            removed_count = int(filtered["removed_count"])
            total_removed += removed_count
            if removed_count:
                affected_variables += 1
            if sum(1 for value in aligned_values if not math.isnan(float(value))) < 2:
                continue
            base_name = objects_by_id[pv_id].name
            display_name = base_name
            if display_name in series_by_name:
                display_name = f"{base_name} ({pv_id})"
            series_by_name[display_name] = aligned_values
            series_metadata_by_name[display_name] = {
                "values": aligned_values,
                "sample_indices": list(filtered["aligned_sample_indices"]),
                "step_indices": list(filtered["aligned_step_indices"]),
                "pv_id": pv_id,
                "raw_count": int(filtered["raw_count"]),
                "removed_count": int(filtered["removed_count"]),
            }

        if len(series_by_name) < 2:
            message = "Need at least two read PV series with valid samples to compute correlation."
            if self._jitter_outlier_filter_enabled() and total_removed:
                message = "Need at least two read PV series with valid samples after outlier filtering."
            self.correlation_plot.clear_data(message)
            return

        try:
            result = compute_correlation_matrix(series_by_name)
        except ValueError as exc:
            self.correlation_plot.clear_data(str(exc))
            return
        self.correlation_plot.set_matrix(
            result.names,
            result.matrix,
            valid_counts=result.valid_counts,
            series_by_name=series_metadata_by_name,
        )
        if self._jitter_outlier_filter_enabled() and total_removed:
            self.correlation_plot.summary_label.setText(
                self.correlation_plot.summary_label.text()
                + f" Outlier filter removed {total_removed} point(s) across {affected_variables} variable(s)."
            )

    def _populate_spectrum_view(self) -> None:
        if self.loaded_config is None:
            self.spectrum_plot.clear_data("Load a PV library to compute spectrum analysis.")
            return

        objects_by_id = {obj.id: obj for obj in self.loaded_config.objects}
        series_sources = []
        seen_names: set[str] = set()
        total_removed = 0
        affected_variables = 0
        for pv_id, values in self.current_series_values.items():
            if pv_id not in objects_by_id:
                continue
            filtered = self._filtered_series_payload(pv_id)
            finite_values = list(filtered["filtered_values"])
            finite_sample_indices = list(filtered["filtered_sample_indices"])
            removed_count = int(filtered["removed_count"])
            total_removed += removed_count
            if removed_count:
                affected_variables += 1
            if len(finite_values) < 2:
                continue
            try:
                metadata = self.current_series_metadata.get(pv_id, [])
                if isinstance(metadata, dict) and "timestamps" not in metadata:
                    sample_interval_sec = self._estimate_series_sample_interval_from_sample_indices(
                        finite_sample_indices
                    )
                else:
                    timestamps = [
                        item.get("timestamp")
                        for value, item in zip(values, metadata)
                        if not math.isnan(float(value)) and item.get("timestamp") is not None
                    ]
                    sample_interval_sec = self._estimate_series_sample_interval(timestamps)
            except ValueError:
                continue
            obj = objects_by_id[pv_id]
            display_name = obj.name
            if display_name in seen_names:
                display_name = f"{obj.name} ({pv_id})"
            seen_names.add(display_name)
            series_sources.append(
                {
                    "pv_id": pv_id,
                    "name": obj.name,
                    "display_name": display_name,
                    "unit": obj.unit,
                    "sample_count": len(finite_values),
                    "series_sample_interval_sec": sample_interval_sec,
                    "values": finite_values,
                    "removed_count": removed_count,
                }
            )

        if not series_sources:
            message = "Need at least one read PV with two valid samples and usable timestamps to compute spectrum."
            if self._jitter_outlier_filter_enabled() and total_removed:
                message = "All usable spectrum samples were removed by the outlier filter."
            self.spectrum_plot.clear_data(message)
            return

        self.spectrum_plot.set_series_sources(series_sources)
        if self._jitter_outlier_filter_enabled() and total_removed:
            self.spectrum_plot.info_label.setText(
                self.spectrum_plot.info_label.text()
                + f" | Outlier filter removed {total_removed} point(s) across {affected_variables} variable(s)."
            )

    @staticmethod
    def _estimate_series_sample_interval(timestamps) -> float:
        return estimate_series_sample_interval(timestamps)

    def _estimate_series_sample_interval_from_sample_indices(self, sample_indices) -> float:
        return estimate_series_sample_interval_from_sample_indices(
            sample_indices,
            self.current_run_sample_timestamps,
        )

    def _highlight_correlation_point(self, payload) -> None:
        if not isinstance(payload, dict):
            return
        sample_index = int(payload.get("sample_index", 0))
        step_index = payload.get("step_index")
        label = str(payload.get("label", "Correlation point"))

        start_index = sample_index
        end_index = sample_index
        if step_index is not None:
            indices = []
            for pv_id, values in self.current_series_values.items():
                sample_indices = self._series_sample_indices(pv_id, expected_length=len(values))
                step_indices = self._series_step_indices(pv_id, expected_length=len(values))
                for series_step_index, series_sample_index in zip(step_indices, sample_indices):
                    if series_step_index == step_index:
                        indices.append(int(series_sample_index))
            if indices:
                start_index = min(indices)
                end_index = max(indices)
                label = f"{label} | step {int(step_index) + 1}"
        else:
            label = f"{label} | sample {sample_index + 1}"

        self.trend_plot.highlight_sample_range(start_index, end_index, label=label)
        self.main_tabs.setCurrentIndex(self.run_tab_index)


def launch_preview(argv: list[str] | None = None) -> int:
    qtwidgets = require_qt()
    app = qtwidgets.QApplication(list(sys.argv if argv is None else argv))
    apply_app_theme(app)
    apply_plot_theme(current_theme_id(app))
    window = MainWindow(
        state=AppState(),
        run_service=RunService(),
        task_service=TaskService(),
    )
    window.show()
    exec_fn = getattr(app, "exec", None) or getattr(app, "exec_", None)
    if exec_fn is None:
        raise RuntimeError("Unsupported Qt application object")
    return exec_fn()


if __name__ == "__main__":
    raise SystemExit(launch_preview())
