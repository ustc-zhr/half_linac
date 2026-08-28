from __future__ import annotations

import copy
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import QSize, Qt, QTimer
try:
    import sip
except ImportError:  # pragma: no cover
    from PyQt5 import sip
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gotacc.interfaces.policies import POLICY_REGISTRY

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

# -----------------------------------------------------------------------------
# UI imports
# -----------------------------------------------------------------------------
try:
    from .ui_main_window import Ui_MainWindow
    from .ui_task_builder import Ui_TaskBuilderPage
    from .ui_machine import Ui_MachinePage
    from .ui_offline_setup import Ui_OfflineSetupPage
    from .ui_run_monitor import Ui_RunMonitorPage
    from .run_session import RunSession
    from .view_adapter import GuiViewAdapter
    from .tool_dialogs import (
        MappingPolicyManagerDialog,
        PolicyTemplatePickerDialog,
        PVMonitorDialog,
        SampleGuardRuleEditorDialog,
    )
except ImportError:  # pragma: no cover - local script fallback
    CURRENT_DIR = Path(__file__).resolve().parent
    if str(CURRENT_DIR) not in sys.path:
        sys.path.insert(0, str(CURRENT_DIR))
    from ui_main_window import Ui_MainWindow
    from ui_task_builder import Ui_TaskBuilderPage
    from ui_machine import Ui_MachinePage
    from ui_offline_setup import Ui_OfflineSetupPage
    from ui_run_monitor import Ui_RunMonitorPage
    from run_session import RunSession
    from view_adapter import GuiViewAdapter
    from tool_dialogs import (
        MappingPolicyManagerDialog,
        PolicyTemplatePickerDialog,
        PVMonitorDialog,
        SampleGuardRuleEditorDialog,
    )

# -----------------------------------------------------------------------------
# Service/worker imports
# -----------------------------------------------------------------------------
try:
    from ..theme import (
        DARK_THEME_KEY,
        LIGHT_THEME_KEY,
        apply_theme,
        current_theme_key,
        save_theme_key,
        theme_label,
        theme_palette,
    )
    from ..state import GuiSessionState
    from ..services.task_service import TaskService
    from .controllers import (
        MachineController,
        ResultsController,
        RunCompletionPresenter,
        RunController,
        RunPreparationPresenter,
        RunResultsPresenter,
        RunSessionPresenter,
        RuntimeStatusController,
        TaskBuilderController,
    )
except ImportError:  # pragma: no cover - local script fallback
    CURRENT_DIR = Path(__file__).resolve().parent
    GUI_ROOT = CURRENT_DIR.parent
    for path in (CURRENT_DIR, GUI_ROOT, GUI_ROOT / "services"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    from theme import (
        DARK_THEME_KEY,
        LIGHT_THEME_KEY,
        apply_theme,
        current_theme_key,
        save_theme_key,
        theme_label,
        theme_palette,
    )
    from state import GuiSessionState
    from task_service import TaskService
    from controllers import (
        MachineController,
        ResultsController,
        RunCompletionPresenter,
        RunController,
        RunPreparationPresenter,
        RunResultsPresenter,
        RunSessionPresenter,
        RuntimeStatusController,
        TaskBuilderController,
    )




class SimpleMatplotlibCanvas(FigureCanvas):
    def __init__(self, parent: QWidget | None = None) -> None:
        self.figure = Figure(figsize=(5, 3), tight_layout=True)
        self.axes = self.figure.add_subplot(111)
        super().__init__(self.figure)
        self.setParent(parent)
        # Avoid backend_qt negative/near-zero resize crashes during early layout.
        self.setMinimumSize(160, 120)
        self.apply_theme_to_axes(self.axes)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        size = event.size()
        if size.width() <= 2 or size.height() <= 2:
            event.accept()
            return
        try:
            super().resizeEvent(event)
        except ValueError:
            # Matplotlib can raise when Qt briefly reports invalid intermediate sizes.
            event.accept()

    def clear_with_message(self, title: str, message: str) -> None:
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        ax.set_title(title)
        ax.text(0.5, 0.5, message, ha="center", va="center", transform=ax.transAxes)
        ax.set_xticks([])
        ax.set_yticks([])
        self.apply_theme_to_axes(ax)
        self.draw_idle()

    def apply_theme_to_axes(self, ax) -> None:
        app = QApplication.instance()
        theme_key = current_theme_key(app)
        palette = theme_palette(theme_key)
        figure_bg = palette.get("panel_bg", "#ffffff")
        axes_bg = palette.get("input_bg", figure_bg)
        text_color = palette.get("text_main", "#202020")
        muted_color = palette.get("header_text", text_color)
        grid_color = palette.get("table_grid", "#d0d0d0")
        accent = palette.get("accent", text_color)

        self.figure.patch.set_facecolor(figure_bg)
        ax.set_facecolor(axes_bg)
        ax.title.set_color(text_color)
        ax.xaxis.label.set_color(muted_color)
        ax.yaxis.label.set_color(muted_color)
        ax.tick_params(axis="both", colors=muted_color, labelcolor=muted_color)
        for spine in ax.spines.values():
            spine.set_color(grid_color)
        for text in ax.texts:
            text.set_color(muted_color)
        for line in [*ax.get_xgridlines(), *ax.get_ygridlines()]:
            line.set_color(grid_color)
            line.set_alpha(0.55)
        legend = ax.get_legend()
        if legend is not None:
            legend.get_frame().set_facecolor(figure_bg)
            legend.get_frame().set_edgecolor(grid_color)
            for text in legend.get_texts():
                text.set_color(text_color)
        for collection in ax.collections:
            if not collection.get_facecolor().size:
                collection.set_facecolor(accent)


class TaskBuilderPageWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ui = Ui_TaskBuilderPage()
        self.ui.setupUi(self)


class MachinePageWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ui = Ui_MachinePage()
        self.ui.setupUi(self)


class OfflineSetupPageWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ui = Ui_OfflineSetupPage()
        self.ui.setupUi(self)


class RunMonitorPageWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ui = Ui_RunMonitorPage()
        self.ui.setupUi(self)


class MainWindow(QMainWindow):
    PAGE_OVERVIEW = 0
    PAGE_DASHBOARD = PAGE_OVERVIEW
    PAGE_CONFIGURE = 1
    PAGE_RUN = 2

    PAGE_TASK_BUILDER = 101
    PAGE_MACHINE = 102
    PAGE_OFFLINE = 104
    PAGE_RUN_MONITOR = 201
    PAGE_RESULTS = 202

    CONFIGURE_TAB_TASK_BUILDER = 0
    CONFIGURE_TAB_MACHINE = 1
    CONFIGURE_TAB_OFFLINE = 2
    RUN_TAB_LIVE = 0
    RUN_TAB_RESULTS = 1

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)

        self.session_state = GuiSessionState()
        self._close_when_run_finishes = False
        self.run_session = RunSession(self, on_idle=self._on_run_session_idle)
        self.view_adapter = GuiViewAdapter(self)
        self.task_builder_controller = TaskBuilderController(self)
        self.machine_controller = MachineController(self)
        self.results_controller = ResultsController(self, SimpleMatplotlibCanvas)
        self.run_completion_presenter = RunCompletionPresenter(self)
        self.run_preparation_presenter = RunPreparationPresenter(self)
        self.run_results_presenter = RunResultsPresenter(self)
        self.run_session_presenter = RunSessionPresenter(self)
        self.run_controller = RunController(self)
        self.runtime_status_controller = RuntimeStatusController(self)
        self.workspace_shell_layout: QVBoxLayout | None = None
        self.log_toggle_button: QToolButton | None = None
        self.theme_toggle_button: QToolButton | None = None

        self._suppress_autofill = False

        self._compose_pages_from_generated_ui()
        self._init_workspace_header()
        self._init_basic_state()
        self._apply_half_linac_shell_conventions()
        self._init_plot_canvases()
        self._init_tables()
        self.machine_controller.init_machine_page()
        self._configure_tab_text_sizing()
        self._init_dashboard()
        self._init_theme_toggle()
        self._simplify_menu_bar()
        self._init_results_page()
        self._connect_signals()
        self.set_embedded_mode(os.environ.get("GOTACC_EMBEDDED", "").strip() in {"1", "true", "yes", "on"})
        self._reset_layout()
        self._refresh_task_preview()
        self._sync_status_panels()

        self.statusBar().showMessage("Ready")
        self._log_console("GOTAcc Studio initialized.")
        self._log_console("Main window is using pyuic5-generated ui_*.py modules directly.")
        self._log_console("Runner pipeline is ready: TaskBuilder -> TaskService -> EngineWorker.")

    @property
    def state(self) -> GuiSessionState:
        return self.session_state

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if not self.run_session.is_running():
            event.accept()
            return
        if self._close_when_run_finishes:
            event.ignore()
            return
        if not self._confirm_close_active_run():
            event.ignore()
            return

        self._close_when_run_finishes = True
        if self.state.run.phase in {"Running", "Stopping"}:
            task = self.state.latest_task_snapshot or self._current_task()
            is_online = str(task.get("mode", "")).strip().lower() == "online epics"
            if is_online and bool((task.get("machine", {}) or {}).get("restore_on_abort", True)):
                self.run_controller.abort_and_restore()
            else:
                self.run_controller.stop_run()
        self.statusBar().showMessage("Waiting for the run to stop safely before closing.")
        event.ignore()

    def _confirm_close_active_run(self) -> bool:
        task = self.state.latest_task_snapshot or self._current_task()
        is_online = str(task.get("mode", "")).strip().lower() == "online epics"
        restore_enabled = is_online and bool(
            (task.get("machine", {}) or {}).get("restore_on_abort", True)
        )
        already_stopping = self.state.run.phase in {"Abort Requested", "Restoring"}

        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Warning)
        dialog.setWindowTitle("Run Active")
        dialog.setText("An optimization run is still active.")
        if already_stopping:
            detail = "The run is already stopping. Keep the window open until shutdown completes?"
            action_text = "Exit When Safe"
        elif restore_enabled:
            detail = "Abort the run, restore the initial machine state, and exit after restoration completes?"
            action_text = "Abort, Restore and Exit"
        else:
            detail = "Abort the run without restoration and exit after the worker stops?"
            action_text = "Abort and Exit"
        dialog.setInformativeText(detail)
        action_button = dialog.addButton(action_text, QMessageBox.AcceptRole)
        cancel_button = dialog.addButton(QMessageBox.Cancel)
        dialog.setDefaultButton(cancel_button)
        dialog.setEscapeButton(cancel_button)
        dialog.exec_()
        return dialog.clickedButton() is action_button

    def _on_run_session_idle(self) -> None:
        if self.state.run.phase not in self.runtime_status_controller.ACTIVE_PHASES:
            self._set_run_buttons_enabled(start=True, stop=False)
        if self._close_when_run_finishes:
            QTimer.singleShot(0, self._complete_deferred_close)

    def _complete_deferred_close(self) -> None:
        if self.run_session.is_running():
            return
        if self.state.run.phase in {"Error", "Restore Failed"}:
            self._close_when_run_finishes = False
            self.statusBar().showMessage("Automatic exit cancelled because the run did not stop safely.")
            QMessageBox.critical(
                self,
                "Exit Cancelled",
                "The run ended with an error or restoration failure. Review the machine state before closing.",
            )
            return
        self.close()

    # ------------------------------------------------------------------
    # Page composition
    # ------------------------------------------------------------------
    def _compose_pages_from_generated_ui(self) -> None:
        self.task_builder_page = TaskBuilderPageWidget(self)
        self.machine_page = MachinePageWidget(self)
        self.offline_setup_page = OfflineSetupPageWidget(self)
        self.run_monitor_page = RunMonitorPageWidget(self)

        self.task_ui = self.task_builder_page.ui
        self.machine_ui = self.machine_page.ui
        self.offline_ui = self.offline_setup_page.ui
        self.run_ui = self.run_monitor_page.ui

        self._remove_stacked_page(self.ui.page_taskBuilder)
        self._remove_stacked_page(self.ui.page_machineInterface)
        self._remove_stacked_page(self.ui.page_runMonitor)
        self._move_stacked_page_to_tab(
            self.ui.page_results,
            self.ui.tabWidget_runWorkspace,
            self.ui.page_runResults,
        )
        self._replace_tab_page(
            self.ui.tabWidget_configure,
            self.ui.page_configureBuilder,
            self.task_builder_page,
        )
        self._replace_tab_page(
            self.ui.tabWidget_configure,
            self.ui.page_configureMachine,
            self.machine_page,
        )
        self.ui.tabWidget_configure.addTab(self.offline_setup_page, "Offline Setup")
        self._mount_offline_setup_controls()
        self._replace_tab_page(
            self.ui.tabWidget_runWorkspace,
            self.ui.page_runLive,
            self.run_monitor_page,
        )

    def _init_workspace_header(self) -> None:
        self.workspace_shell = QWidget(self.ui.centralwidget)
        shell_layout = QVBoxLayout(self.workspace_shell)
        self.workspace_shell_layout = shell_layout
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        self.ui.horizontalLayout_main.removeWidget(self.ui.splitter_main)
        self.ui.horizontalLayout_main.addWidget(self.workspace_shell)

        self.frame_workspace_header = QFrame(self.workspace_shell)
        self.frame_workspace_header.setObjectName("summaryPanel")
        self.frame_workspace_header.setFixedHeight(90)
        self.frame_workspace_header.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        outer_layout = QVBoxLayout(self.frame_workspace_header)
        outer_layout.setContentsMargins(12, 7, 10, 7)
        outer_layout.setSpacing(5)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(10)

        title_column = QWidget(self.frame_workspace_header)
        title_layout = QVBoxLayout(title_column)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(1)

        self.label_workspace_title = QLabel("GOTAcc Studio", title_column)
        self.label_workspace_title.setObjectName("summaryTitle")
        self.label_workspace_subtitle = QLabel("", title_column)
        self.label_workspace_subtitle.setObjectName("summarySubtitle")
        self.label_workspace_subtitle.setVisible(False)
        title_layout.addWidget(self.label_workspace_title)

        self.log_toggle_button = QToolButton(self.frame_workspace_header)
        self.log_toggle_button.setObjectName("logToggleButton")
        self.log_toggle_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.log_toggle_button.setFixedSize(40, 28)
        self.log_toggle_button.setText("Log")
        self.log_toggle_button.setToolTip("Show log panel.")
        self.log_toggle_button.clicked.connect(self._toggle_log_panel)

        self.theme_toggle_button = QToolButton(self.frame_workspace_header)
        self.theme_toggle_button.setObjectName("themeToggleButton")
        self.theme_toggle_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.theme_toggle_button.setFixedSize(28, 28)
        self.theme_toggle_button.clicked.connect(self._toggle_gui_theme)

        header_layout.addWidget(title_column, 1)
        header_layout.addWidget(self.log_toggle_button, 0, Qt.AlignRight | Qt.AlignVCenter)
        header_layout.addWidget(self.theme_toggle_button, 0, Qt.AlignRight | Qt.AlignVCenter)
        outer_layout.addLayout(header_layout)

        self.frame_workspace_status = QFrame(self.frame_workspace_header)
        self.frame_workspace_status.setObjectName("statusStrip")
        status_layout = QHBoxLayout(self.frame_workspace_status)
        status_layout.setContentsMargins(8, 4, 8, 4)
        status_layout.setSpacing(0)
        self._workspace_status_layout = status_layout

        task_item, self.label_workspace_task = self._status_strip_item("TASK", "Untitled Task")
        mode_item, self.label_workspace_mode = self._status_strip_item("MODE", "Offline")
        algorithm_item, self.label_workspace_algorithm = self._status_strip_item("ALGORITHM", "BO")
        run_item, self.label_workspace_run = self._status_strip_item("RUN", "Idle")
        machine_item, self.label_workspace_machine = self._status_strip_item("MACHINE", "Offline")
        self._add_status_strip_items(task_item, mode_item, algorithm_item, run_item, machine_item)
        outer_layout.addWidget(self.frame_workspace_status)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(8, 0, 8, 0)
        header_row.setSpacing(0)
        header_row.addWidget(self.frame_workspace_header)
        shell_layout.addLayout(header_row)
        shell_layout.addWidget(self.ui.splitter_main, 1)
        self._promote_bottom_log_panel()

    def _status_strip_item(
        self,
        title: str,
        value: str,
        parent: QWidget | None = None,
    ) -> tuple[QFrame, QLabel]:
        item = QFrame(parent or self.frame_workspace_status)
        item.setObjectName("statusItem")
        item.setProperty("tone", "subtle")
        item.setMinimumWidth(102)
        item.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        layout = QVBoxLayout(item)
        layout.setContentsMargins(10, 0, 8, 0)
        layout.setSpacing(2)
        title_label = QLabel(title, item)
        title_label.setProperty("role", "title")
        value_label = QLabel(value, item)
        value_label.setProperty("role", "value")
        value_label.setProperty("tone", "subtle")
        value_label.setWordWrap(False)
        value_label.setMinimumWidth(40)
        value_label.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        layout.addWidget(title_label)
        layout.addWidget(value_label)
        return item, value_label

    def _add_status_strip_items(self, *items: QFrame) -> None:
        for index, item in enumerate(items):
            if index:
                separator = QFrame(self.frame_workspace_status)
                separator.setObjectName("statusSeparator")
                separator.setFrameShape(QFrame.VLine)
                separator.setFrameShadow(QFrame.Plain)
                self._workspace_status_layout.addWidget(separator)
            self._workspace_status_layout.addWidget(item)
        self._workspace_status_layout.addStretch(1)

    def _remove_stacked_page(self, page: QWidget) -> None:
        stacked = self.ui.stackedWidget_pages
        index = stacked.indexOf(page)
        if index < 0:
            return
        stacked.removeWidget(page)
        page.hide()

    def _replace_tab_page(self, tab_widget, placeholder_page: QWidget, new_page: QWidget) -> None:
        index = tab_widget.indexOf(placeholder_page)
        if index < 0:
            return
        label = tab_widget.tabText(index)
        tab_widget.removeTab(index)
        placeholder_page.hide()
        tab_widget.insertTab(index, new_page, label)

    def _move_stacked_page_to_tab(self, page: QWidget, tab_widget, placeholder_page: QWidget) -> None:
        self._remove_stacked_page(page)
        self._replace_tab_page(tab_widget, placeholder_page, page)

    def _move_stacked_page_to_container(self, page: QWidget, container: QWidget) -> None:
        self._remove_stacked_page(page)
        layout = container.layout()
        if layout is None:
            layout = QVBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
        else:
            while layout.count():
                item = layout.takeAt(0)
                child = item.widget()
                if child is not None:
                    child.hide()
        layout.addWidget(page)
        page.show()

    def _mount_offline_setup_controls(self) -> None:
        form_layout = getattr(self.task_ui, "formLayout_algorithm", None)
        offline_form = getattr(self.offline_ui, "formLayout_offlineConfig", None)
        label = getattr(self.task_ui, "label_testFunction", None)
        combo = getattr(self.task_ui, "comboBox_testFunction", None)
        if form_layout is None or offline_form is None or label is None or combo is None:
            return
        if (
            label.parent() is self.offline_ui.groupBox_benchmark
            and combo.parent() is self.offline_ui.groupBox_benchmark
        ):
            return
        form_layout.removeWidget(label)
        form_layout.removeWidget(combo)
        label.setParent(self.offline_ui.groupBox_benchmark)
        combo.setParent(self.offline_ui.groupBox_benchmark)
        offline_form.addRow(label, combo)
        self.offline_ui.frame_offlineHero.setVisible(False)
        self.offline_ui.frame_offlinePlaceholder.setVisible(False)
        self.offline_ui.groupBox_benchmark.setTitle("Benchmark")
        self.offline_ui.label_offlineHint.setText(
            "Used only for Offline mode. Choose tradeoff for multi-objective smoke tests."
        )
        self.offline_ui.verticalLayout_main.setContentsMargins(0, 0, 0, 0)
        self.offline_ui.verticalLayout_main.setSpacing(8)
        self.offline_ui.verticalLayout_benchmark.setContentsMargins(10, 14, 10, 10)
        self.offline_ui.verticalLayout_benchmark.setSpacing(6)

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------
    def _init_basic_state(self) -> None:
        self._configure_navigation_cards()
        self.ui.listWidget_navPages.setCurrentRow(self.PAGE_OVERVIEW)
        self.ui.stackedWidget_pages.setCurrentIndex(self.PAGE_OVERVIEW)
        self.ui.listWidget_navPages.setSpacing(8)
        self.ui.label_appTitle.setVisible(False)
        self.ui.label_appSubtitle.setVisible(False)
        self.ui.tabWidget_configure.setCurrentIndex(self.CONFIGURE_TAB_TASK_BUILDER)
        self.ui.tabWidget_runWorkspace.setCurrentIndex(self.RUN_TAB_LIVE)
        self._retire_runtime_status_dock()

        self.ui.progressBar_run.setRange(0, 100)
        self.ui.progressBar_run.setValue(0)

        self.ui.label_cardCurrentTaskValue.setText("Not validated")
        self.ui.label_cardModeValue.setText("--")
        self.ui.label_cardAlgorithmValue.setText("Offline benchmark")
        self.ui.label_cardStatusValue.setText("No run yet")

        self.ui.label_statusTaskValue.setText("Untitled Task")
        self.ui.label_statusModeValue.setText("Offline")
        self.ui.label_statusAlgorithmValue.setText("BO")
        self.ui.label_statusConnectionValue.setText("Disconnected")
        self.ui.label_statusBestValue.setText("--")

        self.task_ui.lineEdit_taskName.setText("demo_task")
        runs_dir = Path(__file__).resolve().parents[4] / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        self.task_ui.lineEdit_workdir.setText(str(runs_dir))
        self.task_ui.comboBox_testFunction.setCurrentText("rosenbrock")

        self.machine_ui.lineEdit_caAddress.setText("")
        self.machine_ui.label_statusValue.setText("Disconnected")
        self.machine_ui.checkBox_restore.setChecked(True)
        self._set_readback_tolerance_enabled(
            self.machine_ui.checkBox_readbackCheck.isChecked()
        )
        self.machine_ui.doubleSpinBox_setInterval.setValue(1.0)
        self.machine_ui.doubleSpinBox_sampleInterval.setValue(0.2)
        self.machine_ui.doubleSpinBox_timeout.setValue(2.0)

        self.run_ui.label_evalValue.setText("0")
        self.run_ui.label_elapsedValue.setText("00:00:00")
        self.run_ui.label_bestValue.setText("--")
        self.run_ui.label_feasibilityValue.setText("1.00")
        self.run_ui.label_phaseValue.setText("Idle")

        self.ui.plainTextEdit_consoleLog.setReadOnly(True)
        self.ui.plainTextEdit_warningError.setReadOnly(True)
        self.ui.plainTextEdit_pvLog.setReadOnly(True)
        self.run_ui.plainTextEdit_events.setReadOnly(True)

        self._set_run_buttons_enabled(start=True, stop=False)
        self.state.last_test_read_status = "Not checked"
        self.state.last_test_read_detail = ""

    def _apply_half_linac_shell_conventions(self) -> None:
        self._align_workspace_card_grid()
        self.ui.frame_leftNav.setMinimumWidth(220)
        self.ui.frame_leftNav.setMaximumWidth(250)
        self.ui.verticalLayout_leftNav.setContentsMargins(8, 8, 2, 8)
        self.ui.verticalLayout_leftNav.setSpacing(8)
        self.ui.verticalLayout_primaryNav.setContentsMargins(10, 12, 10, 10)
        self.ui.verticalLayout_quickActions.setContentsMargins(10, 12, 10, 10)
        self.ui.verticalLayout_quickActions.setSpacing(8)
        self.ui.verticalLayout_leftTools.setContentsMargins(0, 0, 0, 0)
        self.ui.verticalLayout_leftTools.setSpacing(8)
        self._balance_left_tool_cards()
        self.ui.gridLayout_runActions.setContentsMargins(10, 12, 10, 10)
        self.ui.gridLayout_runActions.setHorizontalSpacing(8)
        self.ui.gridLayout_runActions.setVerticalSpacing(8)
        self._clarify_project_task_actions()
        self._simplify_bottom_output_tabs()
        self._simplify_task_builder_table_tabs()
        self._compact_task_builder_inline_actions()
        self._configure_run_readiness_actions()
        self._compact_run_monitor_actions()
        self._configure_results_workspace_layout()
        self._configure_tab_text_sizing()
        self._compact_overview_panels()

        for button in (
            self.ui.pushButton_newOfflineTask,
            self.ui.pushButton_newOnlineTask,
            self.ui.pushButton_openConfig,
            self.ui.pushButton_saveProject,
            self.ui.pushButton_preview,
            self.ui.pushButton_validateTask,
            self.ui.pushButton_startRun,
            self.ui.pushButton_stopRun,
            self.ui.pushButton_checkEnvironment,
        ):
            button.setProperty("compact", True)

        for button in (self.ui.pushButton_startRun,):
            button.setProperty("primary", True)
        self.ui.pushButton_stopRun.setProperty("danger", True)

        for widget in (
            self.ui.pushButton_newOfflineTask,
            self.ui.pushButton_newOnlineTask,
            self.ui.pushButton_openConfig,
            self.ui.pushButton_saveProject,
            self.ui.pushButton_validateTask,
            self.ui.pushButton_startRun,
            self.ui.pushButton_stopRun,
            self.ui.pushButton_checkEnvironment,
            self.task_ui.pushButton_browseWorkdir,
            self.task_ui.pushButton_openAlgorithmDetail,
            self.task_ui.pushButton_openBoundsTools,
            self.ui.pushButton_preview,
            self.run_ui.pushButton_abortRestore,
            self.run_ui.pushButton_restoreInitial,
            self.run_ui.pushButton_setBest,
        ):
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def _align_workspace_card_grid(self) -> None:
        top_level_layouts = (
            self.ui.verticalLayout_dashboard,
            self.ui.verticalLayout_configureShell,
            self.ui.verticalLayout_runShell,
        )
        for layout in top_level_layouts:
            layout.setContentsMargins(2, 8, 8, 8)
            layout.setSpacing(8)

        embedded_page_layouts = (
            self.ui.verticalLayout_resultsPage,
            self.task_ui.verticalLayout_main,
            self.machine_ui.verticalLayout_main,
            self.offline_ui.verticalLayout_main,
            self.run_ui.verticalLayout_main,
        )
        for layout in embedded_page_layouts:
            layout.setContentsMargins(8, 8, 8, 8)
            layout.setSpacing(8)

    def _balance_left_tool_cards(self) -> None:
        layout = self.ui.verticalLayout_leftTools
        while layout.count() > 2:
            item = layout.takeAt(layout.count() - 1)
            if item.widget() is not None:
                layout.addItem(item)
                break

        for index, card in enumerate(
            (self.ui.groupBox_quickActions, self.ui.groupBox_runActions)
        ):
            card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            layout.setStretch(index, 1)

        self.ui.verticalLayout_quickActions.setAlignment(Qt.AlignTop)
        self.ui.gridLayout_runActions.setAlignment(Qt.AlignTop)

    def _clarify_project_task_actions(self) -> None:
        self.ui.pushButton_newOfflineTask.setText("New Task")
        self.ui.pushButton_newOfflineTask.setToolTip("Choose an Offline or Online EPICS task.")
        self.new_task_menu = QMenu(self.ui.pushButton_newOfflineTask)
        self.new_offline_task_action = self.new_task_menu.addAction("Offline Task")
        self.new_online_task_action = self.new_task_menu.addAction("Online EPICS Task")
        self.new_offline_task_action.triggered.connect(self._create_new_offline_task)
        self.new_online_task_action.triggered.connect(self._create_new_online_task)
        self.ui.pushButton_newOfflineTask.setMenu(self.new_task_menu)
        self.ui.pushButton_newOnlineTask.setVisible(False)
        self.ui.pushButton_newOnlineTask.setEnabled(False)
        self.ui.pushButton_openConfig.setText("Open Project")
        self.ui.pushButton_openConfig.setToolTip("Load a saved GOTAcc Studio project.")
        self.ui.pushButton_saveProject.setText("Save Project")
        self.ui.pushButton_saveProject.setToolTip("Save the current GUI project for later editing.")
        self.ui.actionNewTask.setText("New Offline Task")
        self.ui.actionOpenConfig.setText("Open Project")
        self.ui.actionSaveProject.setText("Save Project")

    def _simplify_bottom_output_tabs(self) -> None:
        bottom_tabs = self.ui.tabWidget_bottomOutput
        bottom_tabs.setTabText(bottom_tabs.indexOf(self.ui.tab_consoleLog), "Log")

        for tab in (self.ui.tab_warningError, self.ui.tab_pvLog, self.ui.tab_runHistory):
            index = bottom_tabs.indexOf(tab)
            if index >= 0:
                bottom_tabs.removeTab(index)
            tab.setVisible(False)

    def _simplify_task_builder_table_tabs(self) -> None:
        tabs = self.task_ui.tabWidget_tables
        labels = (
            (self.task_ui.tab_variables, "Variables"),
            (self.task_ui.tab_objectives, "Objectives"),
            (self.task_ui.tab_constraints, "Constraints"),
        )
        for widget, label in labels:
            index = tabs.indexOf(widget)
            if index >= 0:
                tabs.setTabText(index, label)
                tabs.setTabToolTip(index, label)

    def _compact_task_builder_inline_actions(self) -> None:
        for button in (
            self.task_ui.pushButton_browseWorkdir,
            self.task_ui.pushButton_openAlgorithmDetail,
            self.task_ui.pushButton_openBoundsTools,
        ):
            button.setProperty("inlineAction", True)
            button.setFixedHeight(24)
            button.setFixedWidth(88)
            button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self.task_ui.horizontalLayout_workdir.setSpacing(6)
        self.task_ui.horizontalLayout_algorithmDetail.setSpacing(6)
        self.task_ui.pushButton_openAlgorithmDetail.setFixedWidth(96)
        self.task_ui.pushButton_openBoundsTools.setText("Bounds")
        self.task_ui.pushButton_openBoundsTools.setToolTip("Open Bounds Tools.")
        self.task_ui.horizontalLayout_variablesToolbar.takeAt(0)
        self.task_ui.horizontalLayout_variablesToolbar.setContentsMargins(8, 3, 8, 3)
        self.task_ui.horizontalLayout_variablesToolbar.setSpacing(6)
        self.task_ui.horizontalLayout_variablesToolbarActions.setSpacing(6)
        self.task_ui.horizontalLayout_variablesToolbar.addStretch(1)
        self.task_ui.frame_variablesToolbar.setMaximumHeight(34)

    def _configure_run_readiness_actions(self) -> None:
        self.ui.pushButton_preview.setToolTip("Preview the complete TaskConfig before validation or start.")
        self.ui.label_validationStatus.setProperty("tone", "subtle")
        self.ui.gridLayout_runActions.setColumnStretch(0, 1)
        self.ui.gridLayout_runActions.setColumnStretch(1, 1)
        for button in (
            self.ui.pushButton_preview,
            self.ui.pushButton_validateTask,
            self.ui.pushButton_startRun,
            self.ui.pushButton_stopRun,
        ):
            button.setProperty("runControl", True)
            button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

    def _compact_run_monitor_actions(self) -> None:
        self._run_primary_actions_in_sidebar = True
        self.run_ui.frame_runHero.setVisible(False)
        self._configure_run_workspace_layout()
        self._compact_run_snapshot()
        self.run_ui.pushButton_start.setVisible(False)

        for button in (
            self.run_ui.pushButton_stop,
            self.run_ui.pushButton_abortRestore,
            self.run_ui.pushButton_restoreInitial,
            self.run_ui.pushButton_setBest,
        ):
            button.setProperty("inlineAction", True)
            button.setFixedHeight(24)
            button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

        self.run_ui.pushButton_abortRestore.setProperty("danger", True)
        self.run_ui.pushButton_stop.setProperty("danger", True)
        self.run_ui.groupBox_actions.setVisible(False)

        for column, button in (
            (8, self.run_ui.pushButton_stop),
            (9, self.run_ui.pushButton_abortRestore),
        ):
            self.run_ui.horizontalLayout_actions.removeWidget(button)
            button.setParent(self.run_ui.groupBox_runtime)
            self.run_ui.gridLayout_runtime.addWidget(button, 0, column, 1, 1, Qt.AlignVCenter)
        self.run_ui.gridLayout_runtime.setColumnStretch(7, 1)

        layout = self.run_ui.verticalLayout_main
        layout.removeWidget(self.run_ui.groupBox_runtime)
        layout.removeWidget(self.run_ui.groupBox_actions)
        layout.insertWidget(1, self.run_ui.groupBox_runtime)

    def _configure_run_workspace_layout(self) -> None:
        self.run_ui.splitter_main.setOrientation(Qt.Vertical)
        self.run_ui.splitter_main.setChildrenCollapsible(False)
        self.run_ui.splitter_main.setStretchFactor(0, 3)
        self.run_ui.splitter_main.setStretchFactor(1, 2)

        self.run_ui.splitter_runRight.setOrientation(Qt.Horizontal)
        self.run_ui.splitter_runRight.setChildrenCollapsible(False)
        self.run_ui.splitter_runRight.setStretchFactor(0, 2)
        self.run_ui.splitter_runRight.setStretchFactor(1, 5)

        self.run_ui.groupBox_livePlots.setMinimumHeight(260)
        self.run_ui.groupBox_events.setMinimumWidth(220)
        self.run_ui.groupBox_table.setMinimumWidth(360)
        self.run_ui.tabWidget_plots.setDocumentMode(True)
        self.run_ui.verticalLayout_livePlots.setContentsMargins(10, 22, 10, 10)
        self.run_ui.verticalLayout_livePlots.setSpacing(0)
        self.run_ui.verticalLayout_events.setContentsMargins(10, 22, 10, 10)
        self.run_ui.verticalLayout_table.setContentsMargins(10, 22, 10, 10)

        for layout in (
            self.run_ui.verticalLayout_obj,
            self.run_ui.verticalLayout_constraints,
            self.run_ui.verticalLayout_pareto,
            self.run_ui.verticalLayout_variables,
        ):
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)

        for frame in (
            self.run_ui.frame_obj,
            self.run_ui.frame_constraints,
            self.run_ui.frame_pareto,
            self.run_ui.frame_variables,
        ):
            frame.setProperty("plotHost", True)
            frame.setFrameShape(QFrame.NoFrame)

    def _compact_run_snapshot(self) -> None:
        self.run_ui.groupBox_runtime.setMaximumHeight(94)
        self.run_ui.groupBox_runtime.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.run_ui.gridLayout_runtime.setContentsMargins(10, 22, 10, 8)
        self.run_ui.gridLayout_runtime.setHorizontalSpacing(0)
        self.run_ui.gridLayout_runtime.setVerticalSpacing(0)

        frames = (
            self.run_ui.frame_eval,
            self.run_ui.frame_elapsed,
            self.run_ui.frame_best,
            self.run_ui.frame_feasibility,
        )
        self.run_ui.frame_phase.setVisible(False)
        separators = []
        frame_min_widths = (108, 108, 132, 132)
        frame_widths = (118, 118, 176, 142)
        for index, (frame, min_width, width) in enumerate(
            zip(frames, frame_min_widths, frame_widths)
        ):
            frame.setObjectName("statusItem")
            frame.setProperty("tone", "subtle")
            frame.setMinimumHeight(42)
            frame.setMaximumHeight(44)
            frame.setMinimumWidth(min_width)
            frame.setMaximumWidth(width)
            frame.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            if index:
                separator = QFrame(self.run_ui.groupBox_runtime)
                separator.setObjectName("statusSeparator")
                separator.setFrameShape(QFrame.VLine)
                separator.setFrameShadow(QFrame.Plain)
                self.run_ui.gridLayout_runtime.addWidget(separator, 0, index * 2 - 1, 1, 1)
                separators.append(separator)
            self.run_ui.gridLayout_runtime.addWidget(frame, 0, index * 2, 1, 1)

        layouts = (
            self.run_ui.verticalLayout_eval,
            self.run_ui.verticalLayout_elapsed,
            self.run_ui.verticalLayout_best,
            self.run_ui.verticalLayout_feasibility,
        )
        for layout in layouts:
            layout.setContentsMargins(10, 0, 8, 0)
            layout.setSpacing(2)

        title_labels = (
            self.run_ui.label_evalTitle,
            self.run_ui.label_elapsedTitle,
            self.run_ui.label_bestTitle,
            self.run_ui.label_feasibilityTitle,
        )
        for label in title_labels:
            label.setProperty("role", "title")
            label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            label.setMaximumHeight(14)

        value_labels = (
            self.run_ui.label_evalValue,
            self.run_ui.label_elapsedValue,
            self.run_ui.label_bestValue,
            self.run_ui.label_feasibilityValue,
        )
        for label in value_labels:
            label.setProperty("role", "value")
            label.setProperty("tone", "subtle")
            label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            label.setMaximumHeight(20)

        for widget in (*frames, *separators, *title_labels, *value_labels):
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def _configure_results_workspace_layout(self) -> None:
        self.ui.verticalLayout_resultsPage.setContentsMargins(8, 8, 8, 8)
        self.ui.verticalLayout_resultsPage.setSpacing(8)

        self.frame_results_source = QFrame(self.ui.page_results)
        self.frame_results_source.setObjectName("statusStrip")
        self.frame_results_source.setFixedHeight(58)
        source_layout = QHBoxLayout(self.frame_results_source)
        source_layout.setContentsMargins(8, 4, 8, 4)
        source_layout.setSpacing(0)
        source_items = (
            self._status_strip_item("RESULT TASK", "No run", self.frame_results_source),
            self._status_strip_item("OUTCOME", "--", self.frame_results_source),
            self._status_strip_item("OUTPUT", "--", self.frame_results_source),
        )
        for item, _label in source_items:
            source_layout.addWidget(item)
        for (item, _label), width in zip(source_items, (220, 170, 220)):
            item.setMaximumWidth(width)
            item.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        source_layout.addStretch(1)
        self.label_results_source_task = source_items[0][1]
        self.label_results_source_outcome = source_items[1][1]
        self.label_results_source_output = source_items[2][1]
        source_layout.addSpacing(8)
        for button in (
            self.run_ui.pushButton_restoreInitial,
            self.run_ui.pushButton_setBest,
        ):
            self.run_ui.horizontalLayout_actions.removeWidget(button)
            button.setParent(self.frame_results_source)
            source_layout.addWidget(button, 0, Qt.AlignVCenter)
        self.run_ui.pushButton_setBest.setText("Set Best")
        self.ui.verticalLayout_resultsPage.insertWidget(0, self.frame_results_source)

        self.ui.splitter_resultsMain.setChildrenCollapsible(False)
        self.ui.splitter_resultsMain.setStretchFactor(0, 0)
        self.ui.splitter_resultsMain.setStretchFactor(1, 1)
        self.ui.groupBox_runList.setMinimumWidth(250)
        self.ui.groupBox_runList.setMaximumWidth(300)
        self.ui.treeWidget_runList.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.ui.treeWidget_runList.setTextElideMode(Qt.ElideMiddle)

        self.ui.splitter_resultsRight.setChildrenCollapsible(False)
        self.ui.splitter_resultsRight.setStretchFactor(0, 3)
        self.ui.splitter_resultsRight.setStretchFactor(1, 2)
        self.ui.splitter_convergencePlots.setOrientation(Qt.Horizontal)
        self.ui.splitter_convergencePlots.setChildrenCollapsible(False)
        self.ui.splitter_convergencePlots.setStretchFactor(0, 1)
        self.ui.splitter_convergencePlots.setStretchFactor(1, 1)

        self.ui.tabWidget_resultsViews.setDocumentMode(True)
        for layout in (
            self.ui.verticalLayout_convergence,
            self.ui.verticalLayout_convergencePlot,
            self.ui.verticalLayout_variablePlot,
            self.ui.verticalLayout_pareto,
        ):
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)

        for group in (
            self.ui.groupBox_convergencePlot,
            self.ui.groupBox_variablePlot,
        ):
            group.setTitle("")
            group.setProperty("plotPanel", True)

        for frame in (
            self.ui.frame_plotConvergence,
            self.ui.frame_plotVariables,
            self.ui.frame_plotParetoFinal,
        ):
            frame.setProperty("plotHost", True)
            frame.setFrameShape(QFrame.NoFrame)

        self.ui.widget_resultsTables.setVisible(False)
        self.ui.groupBox_evalHistory.setVisible(False)
        self.ui.horizontalLayout_resultsTables.setContentsMargins(0, 0, 0, 0)
        self.ui.horizontalLayout_resultsTables.setSpacing(0)

    def _promote_bottom_log_panel(self) -> None:
        if self.workspace_shell_layout is None:
            return
        bottom_tabs = self.ui.tabWidget_bottomOutput
        bottom_tabs.setParent(self.workspace_shell)
        bottom_tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        bottom_tabs.setFixedHeight(132)
        self.workspace_shell_layout.addWidget(bottom_tabs, 0)
        self._set_log_panel_visible(False)

    def _set_log_panel_visible(self, visible: bool) -> None:
        self.ui.tabWidget_bottomOutput.setVisible(visible)
        self._sync_log_toggle()

    def _toggle_log_panel(self) -> None:
        self._set_log_panel_visible(not self.ui.tabWidget_bottomOutput.isVisible())

    def _sync_log_toggle(self) -> None:
        if self.log_toggle_button is None:
            return
        visible = self.ui.tabWidget_bottomOutput.isVisible()
        self.log_toggle_button.setProperty("active", visible)
        self.log_toggle_button.setToolTip("Hide log panel." if visible else "Show log panel.")
        self.log_toggle_button.style().unpolish(self.log_toggle_button)
        self.log_toggle_button.style().polish(self.log_toggle_button)

    def _compact_overview_panels(self) -> None:
        self.ui.groupBox_dashboardSummary.setMaximumHeight(146)
        self.ui.gridLayout_dashboardSummary.setContentsMargins(10, 10, 10, 8)
        self.ui.gridLayout_dashboardSummary.setHorizontalSpacing(8)
        self.ui.gridLayout_dashboardSummary.setVerticalSpacing(8)

        cards = (
            (self.ui.frame_cardCurrentTask, self.ui.verticalLayout_cardCurrentTask),
            (self.ui.frame_cardMode, self.ui.verticalLayout_cardMode),
            (self.ui.frame_cardAlgorithm, self.ui.verticalLayout_cardAlgorithm),
            (self.ui.frame_cardStatus, self.ui.verticalLayout_cardStatus),
        )
        for frame, layout in cards:
            frame.setFixedHeight(84)
            frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            layout.setContentsMargins(8, 6, 8, 6)
            layout.setSpacing(2)

        for label in (
            self.ui.label_cardCurrentTaskTitle,
            self.ui.label_cardModeTitle,
            self.ui.label_cardAlgorithmTitle,
            self.ui.label_cardStatusTitle,
        ):
            label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        for label in (
            self.ui.label_cardCurrentTaskValue,
            self.ui.label_cardModeValue,
            self.ui.label_cardAlgorithmValue,
            self.ui.label_cardStatusValue,
        ):
            label.setWordWrap(True)
            label.setToolTip(label.text())

        self.ui.groupBox_environmentStatus.setVisible(False)
        self.ui.groupBox_recentProjects.setMinimumWidth(0)
        self.ui.splitter_dashboardLower.setSizes([1, 0])

    def _configure_tab_text_sizing(self) -> None:
        primary_tab_widgets: tuple[QTabWidget, ...] = (
            self.ui.tabWidget_configure,
            self.ui.tabWidget_runWorkspace,
            self.ui.tabWidget_resultsViews,
            self.ui.tabWidget_bottomOutput,
            self.task_ui.tabWidget_tables,
            self.machine_ui.tabWidget_machine,
            self.run_ui.tabWidget_plots,
        )
        tab_widgets = list(dict.fromkeys((*primary_tab_widgets, *self.findChildren(QTabWidget))))
        for tab_widget in tab_widgets:
            tab_widget.setElideMode(Qt.ElideNone)
            tab_widget.setUsesScrollButtons(True)
            tab_widget.tabBar().setElideMode(Qt.ElideNone)
            tab_widget.tabBar().setUsesScrollButtons(True)
            tab_widget.tabBar().setExpanding(False)
            for index in range(tab_widget.count()):
                tab_widget.setTabToolTip(index, tab_widget.tabText(index))

    def _configure_navigation_cards(self) -> None:
        entries = [
            ("Overview", "Dashboard, current task summary and recent activity."),
            ("Configure", "Build the task and wire machine settings."),
            ("Run", "Start the run and inspect outputs."),
        ]
        nav = self.ui.listWidget_navPages
        nav.setWordWrap(True)
        nav.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for index, (text, tooltip) in enumerate(entries):
            item = nav.item(index)
            if item is None:
                continue
            item.setText(text)
            item.setToolTip(tooltip)
            item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            item.setSizeHint(QSize(0, 40))

    def _init_tables(self) -> None:
        self._init_task_builder_tables()
        self._init_machine_tables()
        self._init_run_tables()
        self._init_main_window_tables()
        self._configure_task_builder_layout()

    def _configure_task_builder_layout(self) -> None:
        if hasattr(self.task_ui, "horizontalLayout_topForms"):
            self.task_ui.horizontalLayout_topForms.setStretch(0, 1)
            self.task_ui.horizontalLayout_topForms.setStretch(1, 1)
            self.task_ui.horizontalLayout_topForms.setSpacing(8)

        for spinbox in (self.task_ui.spinBox_seed, self.task_ui.spinBox_maxEval):
            spinbox.setMaximumWidth(160)
            spinbox.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        for combo in (
            self.task_ui.comboBox_mode,
            self.task_ui.comboBox_objectiveType,
            self.task_ui.comboBox_algorithm,
        ):
            combo.setMinimumWidth(180)

        self.task_ui.lineEdit_workdir.setToolTip(self.task_ui.lineEdit_workdir.text())
        self.task_ui.tabWidget_tables.setDocumentMode(True)
        self._configure_task_table_columns()
        self._configure_task_table_row_actions()

    def _configure_task_table_columns(self) -> None:
        table_specs = (
            (self.task_ui.tableWidget_variables, {0: 70, 2: 110, 3: 110, 4: 110}, (1, 5)),
            (self.task_ui.tableWidget_objectives, {0: 70, 2: 120, 3: 90, 4: 90}, (1, 5)),
            (self.task_ui.tableWidget_constraints, {0: 70, 2: 110, 3: 110}, (1, 4)),
        )
        for table, fixed_columns, stretch_columns in table_specs:
            table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            header = table.horizontalHeader()
            for column, width in fixed_columns.items():
                header.setSectionResizeMode(column, QHeaderView.Fixed)
                table.setColumnWidth(column, width)
            for column in stretch_columns:
                header.setSectionResizeMode(column, QHeaderView.Stretch)

    def _configure_task_table_row_actions(self) -> None:
        specs = (
            (
                "variables",
                self.task_ui.tab_variables,
                self.task_ui.verticalLayout_variables,
                self.task_ui.tableWidget_variables,
                self.task_ui.horizontalLayout_variablesToolbar,
            ),
            (
                "objectives",
                self.task_ui.tab_objectives,
                self.task_ui.verticalLayout_objectives,
                self.task_ui.tableWidget_objectives,
                None,
            ),
            (
                "constraints",
                self.task_ui.tab_constraints,
                self.task_ui.verticalLayout_constraints,
                self.task_ui.tableWidget_constraints,
                None,
            ),
        )
        for field, tab, tab_layout, table, existing_layout in specs:
            if existing_layout is None:
                toolbar = QFrame(tab)
                toolbar.setObjectName(f"frame_{field}Toolbar")
                toolbar_layout = QHBoxLayout(toolbar)
                toolbar_layout.setContentsMargins(8, 3, 8, 3)
                toolbar_layout.setSpacing(6)
                toolbar.setMaximumHeight(34)
                tab_layout.insertWidget(0, toolbar)
            else:
                toolbar = self.task_ui.frame_variablesToolbar
                toolbar_layout = existing_layout
            hint = QLabel(toolbar)
            hint.setWordWrap(False)
            add_button = QPushButton("Add Row", toolbar)
            remove_button = QPushButton("Remove Selected", toolbar)
            for button, width in ((add_button, 82), (remove_button, 124)):
                button.setProperty("inlineAction", True)
                button.setFixedSize(width, 24)
                button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            if field == "variables":
                toolbar_layout.insertWidget(0, hint, 1)
                toolbar_layout.addWidget(add_button)
                toolbar_layout.addWidget(remove_button)
            else:
                toolbar_layout.addWidget(hint, 1)
                toolbar_layout.addWidget(add_button)
                toolbar_layout.addWidget(remove_button)
            add_button.clicked.connect(
                lambda _checked=False, target_field=field: (
                    self.task_builder_controller.add_task_table_row(target_field)
                )
            )
            remove_button.clicked.connect(
                lambda _checked=False, target_field=field: (
                    self.task_builder_controller.remove_selected_task_rows(target_field)
                )
            )
            setattr(self.task_ui, f"label_{field}EmptyState", hint)
            setattr(self.task_ui, f"pushButton_add{field.title()[:-1]}Row", add_button)
            setattr(self.task_ui, f"pushButton_remove{field.title()[:-1]}Rows", remove_button)
            table.setProperty("taskField", field)
        self.task_builder_controller.refresh_task_table_empty_states()

    def _init_task_builder_tables(self) -> None:
        variables_headers = ["Enable", "Name", "Lower", "Upper", "Initial", "Group"]
        objectives_headers = ["Enable", "Name", "Direction", "Weight", "Samples", "Math"]
        constraints_headers = ["Enable", "Name", "Lower", "Upper", "Math"]
        dynamic_headers = ["Parameter", "Value", "Type", "Description"]

        self._setup_table(self.task_ui.tableWidget_variables, variables_headers, 0)
        self._setup_table(self.task_ui.tableWidget_objectives, objectives_headers, 0)
        self._setup_table(self.task_ui.tableWidget_constraints, constraints_headers, 0)
        self._setup_table(self.task_ui.tableWidget_dynamicParams, dynamic_headers, 4)
        for table in (
            self.task_ui.tableWidget_variables,
            self.task_ui.tableWidget_objectives,
            self.task_ui.tableWidget_constraints,
        ):
            table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.task_builder_controller.sync_algorithm_options_with_objective_type(
            preferred_algorithm="BO",
            update_params=False,
        )
        self.task_builder_controller.apply_recommended_dynamic_params(
            "BO",
            preserve_custom=False,
            log_change=False,
        )
        self.task_builder_controller.set_algorithm_overrides_expanded(False)
        self.task_builder_controller.init_bounds_tool()
        self.task_builder_controller.refresh_task_table_empty_states()

    def _init_machine_tables(self) -> None:
        mapping_headers = [
            "Role",
            "Name",
            "PV Name",
            "Readback",
            "Group",
            "Note",
            "Policies",
            "Policy Action",
        ]
        write_headers = ["Source Index", "Target PV", "Enabled"]
        self._setup_table(self.machine_ui.tableWidget_mapping, mapping_headers, 0)
        self._setup_table(self.machine_ui.tableWidget_writeLinks, write_headers, 0)
        self.machine_ui.policy_bindings = []
        self.machine_ui.policy_presets = []
        self.machine_ui.machine_profile = {
            "profile_id": "embedded",
            "name": "Embedded Machine",
            "version": 1,
            "source": "",
        }
        self.machine_ui.tableWidget_writeLinks.setSelectionMode(QAbstractItemView.ExtendedSelection)

        self.task_builder_controller.refresh_write_link_editors()
        self._refresh_mapping_policy_widgets()

    def _init_run_tables(self) -> None:
        recent_headers = ["Eval", "Time", "Status", "X", "Y", "Constraints"]
        self._setup_table(self.run_ui.tableWidget_recent, recent_headers, 0)
        self._configure_recent_eval_table(self.run_ui.tableWidget_recent)

    def _init_main_window_tables(self) -> None:
        self._setup_table(
            self.ui.tableWidget_recentProjects,
            ["Event", "Task", "Mode", "Algorithm", "Status", "Timestamp"],
            0,
        )
        self._update_recent_activity_empty_state()

        self._setup_table(self.ui.tableWidget_runHistory, ["Run", "Task", "Mode", "Algorithm", "Status", "Time"], 0)
        self._setup_table(self.ui.tableWidget_recentEvaluations, ["Eval", "Time", "Status", "X", "Y", "Constraints"], 0)
        self._configure_recent_eval_table(self.ui.tableWidget_recentEvaluations)
        self._setup_table(self.ui.tableWidget_solutionInspector, ["Field", "Value"], 4)
        self._set_table_row(self.ui.tableWidget_solutionInspector, 0, ["Run", "None"])
        self._set_table_row(self.ui.tableWidget_solutionInspector, 1, ["Point", "None"])
        self._set_table_row(self.ui.tableWidget_solutionInspector, 2, ["Objective", "--"])
        self._set_table_row(self.ui.tableWidget_solutionInspector, 3, ["Constraints", "--"])

    def _init_plot_canvases(self) -> None:
        self.results_controller.init_plot_canvases()

    def _attach_plot_canvas(self, frame: QWidget) -> SimpleMatplotlibCanvas:
        return self.results_controller.attach_plot_canvas(frame)

    def _reset_plot_data(self) -> None:
        self.results_controller.reset_plot_data()

    def _redraw_plots(self) -> None:
        self.results_controller.redraw_plots()

    def _draw_objective_plot(self, canvas: SimpleMatplotlibCanvas, *, title: str) -> None:
        self.results_controller.draw_objective_plot(canvas, title=title)

    def _draw_pareto_plot(self, canvas: SimpleMatplotlibCanvas, *, title: str) -> None:
        self.results_controller.draw_pareto_plot(canvas, title=title)

    # =============================
    # Variable Trajectories
    # =============================
    def _draw_variable_trajectories(self):
        self.results_controller.draw_variable_trajectories()

    # =============================
    # populate history table
    # =============================
    def _populate_history_table(self):
        self.results_controller.populate_history_table()

    def _on_history_row_clicked(self, row):
        self.results_controller.on_history_row_clicked(row)

    def _init_dashboard(self) -> None:
        self._configure_dashboard_layout()
        self._refresh_overview_activity_table()
        self._refresh_overview_readiness()

    def _configure_dashboard_layout(self) -> None:
        self.ui.frame_dashboardHero.setVisible(False)
        self.ui.groupBox_dashboardSummary.setTitle("Run Readiness")
        self.ui.label_cardCurrentTaskTitle.setText("Task Readiness")
        self.ui.label_cardModeTitle.setText("Run Plan")
        self.ui.label_cardAlgorithmTitle.setText("Backend Readiness")
        self.ui.label_cardStatusTitle.setText("Last Outcome")
        self.ui.label_recentActivityHint.setVisible(False)
        self.ui.label_readinessHint.setVisible(False)
        self.ui.label_recentActivityEmpty.setText("No recent activity.")
        self._refresh_overview_cards()

    def _init_theme_toggle(self) -> None:
        self.ui.menuView.removeAction(self.ui.menuTheme.menuAction())
        active_theme = current_theme_key(QApplication.instance())
        if active_theme not in {LIGHT_THEME_KEY, DARK_THEME_KEY}:
            self._set_gui_theme(DARK_THEME_KEY, persist=True, log_change=False)
        else:
            self._sync_theme_toggle(active_theme)

    def _simplify_menu_bar(self) -> None:
        self.ui.menuView.removeAction(self.ui.actionResetLayout)
        self.ui.menuView.removeAction(self.ui.actionToggleRuntimeDock)
        if not self.ui.menuTools.actions() or not self.ui.menuTools.actions()[-1].isSeparator():
            self.ui.menuTools.addSeparator()
        self.ui.menuTools.addAction(self.ui.actionResetLayout)
        self.ui.menubar.removeAction(self.ui.menuView.menuAction())

    def _retire_runtime_status_dock(self) -> None:
        self.removeDockWidget(self.ui.dockWidget_runtimeStatus)
        self.ui.dockWidget_runtimeStatus.hide()
        self.ui.actionToggleRuntimeDock.setVisible(False)

    def set_embedded_mode(self, enabled: bool) -> None:
        enabled = bool(enabled)
        self.ui.menubar.setVisible(not enabled)
        self.statusBar().setVisible(not enabled)

    def _init_results_page(self) -> None:
        self.results_controller.init_results_page()

    # ------------------------------------------------------------------
    # Signal connections
    # ------------------------------------------------------------------
    def _connect_signals(self) -> None:
        self.ui.listWidget_navPages.currentRowChanged.connect(self._on_nav_changed)

        self.ui.pushButton_openConfig.clicked.connect(self._open_config)
        self.ui.pushButton_saveProject.clicked.connect(self._save_project)
        self.ui.pushButton_validateTask.clicked.connect(self.validate_task)
        self.ui.pushButton_startRun.clicked.connect(self.start_run)
        self.ui.pushButton_stopRun.clicked.connect(self.stop_run)
        self.ui.pushButton_checkEnvironment.clicked.connect(self._check_environment)

        self.ui.actionNewTask.triggered.connect(self._create_new_offline_task)
        self.ui.actionOpenConfig.triggered.connect(self._open_config)
        self.ui.actionSaveProject.triggered.connect(self._save_project)
        self.ui.actionExportResults.triggered.connect(self.export_results)
        self.ui.actionExit.triggered.connect(self.close)
        self.ui.actionValidate.triggered.connect(self.validate_task)
        self.ui.actionStart.triggered.connect(self.start_run)
        self.ui.actionStop.triggered.connect(self.stop_run)
        self.ui.actionRestoreMachine.triggered.connect(self.abort_and_restore)
        self.ui.actionEnvironmentCheck.triggered.connect(self._check_environment)
        self.ui.actionPVMonitor.triggered.connect(self._show_pv_monitor_stub)
        self.ui.actionPolicyEditor.triggered.connect(self._show_policy_editor)
        self.ui.actionResetLayout.triggered.connect(self._reset_layout)
        self.ui.actionAboutGOTAcc.triggered.connect(self._show_about)

        self.task_ui.lineEdit_taskName.textChanged.connect(self._refresh_task_preview)
        self.task_ui.comboBox_mode.currentTextChanged.connect(self._refresh_task_preview)
        self.task_ui.comboBox_objectiveType.currentTextChanged.connect(self._on_objective_type_changed)
        self.task_ui.comboBox_algorithm.currentTextChanged.connect(self._on_algorithm_changed)
        self.task_ui.comboBox_testFunction.currentTextChanged.connect(self._on_test_function_changed)
        self.task_ui.spinBox_seed.valueChanged.connect(self._refresh_task_preview)
        self.task_ui.spinBox_maxEval.valueChanged.connect(self._refresh_task_preview)
        self.task_ui.lineEdit_workdir.textChanged.connect(self._refresh_task_preview)
        self.task_ui.lineEdit_workdir.textChanged.connect(self.task_ui.lineEdit_workdir.setToolTip)
        self.task_ui.pushButton_browseWorkdir.clicked.connect(self._browse_workdir)
        self.ui.pushButton_preview.clicked.connect(self._show_task_preview)
        self.task_ui.pushButton_openBoundsTools.clicked.connect(self._open_bounds_tools)
        self.task_ui.pushButton_openAlgorithmDetail.clicked.connect(self._open_algorithm_detail)
        self.task_ui.toolButton_toggleAlgorithmOverrides.toggled.connect(self._toggle_algorithm_overrides)

        # Refresh preview when table cells change.
        self.task_ui.tableWidget_variables.itemChanged.connect(lambda *_: self._refresh_task_preview())
        self.task_ui.tableWidget_objectives.itemChanged.connect(lambda *_: self._refresh_task_preview())
        self.task_ui.tableWidget_constraints.itemChanged.connect(lambda *_: self._refresh_task_preview())
        self.task_ui.tableWidget_variables.itemChanged.connect(self._sync_table_item_tooltip)
        self.task_ui.tableWidget_objectives.itemChanged.connect(self._sync_table_item_tooltip)
        self.task_ui.tableWidget_constraints.itemChanged.connect(self._sync_table_item_tooltip)
        self.task_ui.tableWidget_dynamicParams.itemChanged.connect(self._on_dynamic_param_table_changed)
        self.machine_ui.tableWidget_mapping.itemChanged.connect(lambda *_: self._refresh_task_preview())
        self.machine_ui.tableWidget_mapping.itemChanged.connect(lambda *_: self.machine_controller.refresh_selected_library_tables())
        self.machine_ui.tableWidget_mapping.itemChanged.connect(
            lambda *_: self._refresh_mapping_policy_widgets()
        )
        self.machine_ui.tableWidget_writeLinks.itemChanged.connect(lambda *_: self._refresh_task_preview())

        self.machine_ui.pushButton_connect.clicked.connect(self.connect_machine)
        self.machine_ui.pushButton_disconnect.clicked.connect(self.disconnect_machine)
        self.machine_ui.pushButton_test.clicked.connect(self.test_machine_read)
        self.machine_ui.pushButton_loadPvLibrary.clicked.connect(self._load_external_pv_library)
        self.machine_ui.pushButton_pickKnobsFromLibrary.clicked.connect(self._open_knob_library_dialog)
        self.machine_ui.pushButton_clearSelectedKnobs.clicked.connect(self._clear_selected_knobs)
        self.machine_ui.pushButton_pickObjectivesFromLibrary.clicked.connect(self._open_objective_library_dialog)
        self.machine_ui.pushButton_clearSelectedObjectives.clicked.connect(self._clear_selected_objectives)
        self.machine_ui.pushButton_pickConstraintsFromLibrary.clicked.connect(self._open_constraint_library_dialog)
        self.machine_ui.pushButton_clearSelectedConstraints.clicked.connect(self._clear_selected_constraints)
        self.machine_ui.pushButton_applySelectedPvLibrary.clicked.connect(self._apply_selected_pv_library_entries)
        self.machine_ui.pushButton_addWriteLink.clicked.connect(self._add_write_link_row)
        self.machine_ui.pushButton_removeWriteLink.clicked.connect(self._remove_write_link_rows)
        self.machine_ui.comboBox_policy.currentTextChanged.connect(self._log_machine_policy_change)
        self.task_ui.comboBox_mode.currentTextChanged.connect(
            lambda _text: self.machine_controller.refresh_machine_summary()
        )
        self.machine_ui.checkBox_autoConnect.toggled.connect(self._refresh_task_preview)
        self.machine_ui.checkBox_restore.toggled.connect(self._refresh_task_preview)
        self.machine_ui.checkBox_readbackCheck.toggled.connect(
            self._set_readback_tolerance_enabled
        )
        self.machine_ui.checkBox_readbackCheck.toggled.connect(self._refresh_task_preview)
        self.machine_ui.doubleSpinBox_readbackTol.valueChanged.connect(self._refresh_task_preview)
        self.machine_ui.doubleSpinBox_setInterval.valueChanged.connect(self._refresh_task_preview)
        self.machine_ui.doubleSpinBox_sampleInterval.valueChanged.connect(self._refresh_task_preview)
        self.machine_ui.doubleSpinBox_timeout.valueChanged.connect(self._refresh_task_preview)
        self.machine_ui.lineEdit_caAddress.textChanged.connect(self._refresh_task_preview)

        self.run_ui.pushButton_start.clicked.connect(self.start_run)
        self.run_ui.pushButton_stop.clicked.connect(self.stop_run)
        self.run_ui.pushButton_abortRestore.clicked.connect(self.abort_and_restore)
        self.run_ui.pushButton_restoreInitial.clicked.connect(self.restore_initial_to_machine)
        self.run_ui.pushButton_setBest.clicked.connect(self.set_best_to_machine)

        self.ui.treeWidget_runList.itemDoubleClicked.connect(self._open_selected_result_item)
        self.ui.treeWidget_runList.itemSelectionChanged.connect(self._on_results_tree_selection_changed)

        if hasattr(self.ui, "tableWidget_history"):
            self.ui.tableWidget_history.cellClicked.connect(
                lambda r, c: self._on_history_row_clicked(r)
            )
        self.ui.tableWidget_recentEvaluations.cellClicked.connect(
            lambda r, c: self._on_history_row_clicked(r)
        )

    # ------------------------------------------------------------------
    # Generic helpers
    # ------------------------------------------------------------------
    def _setup_table(self, table, headers, row_count: int) -> None:
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setRowCount(row_count)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setWordWrap(False)
        table.verticalHeader().setVisible(False)
        header = table.horizontalHeader()
        header.setStretchLastSection(True)
        for idx in range(len(headers) - 1):
            header.setSectionResizeMode(idx, header.Stretch)

    @staticmethod
    def _sync_table_item_tooltip(item: QTableWidgetItem) -> None:
        item.setToolTip(item.text())

    def _configure_recent_eval_table(self, table) -> None:
        table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        header = table.horizontalHeader()
        widths = (46, 76, 62, 90, 72, 104)
        for idx, width in enumerate(widths):
            if idx >= table.columnCount():
                break
            header.setSectionResizeMode(idx, header.Interactive)
            table.setColumnWidth(idx, width)
        header.setStretchLastSection(True)

    def _add_table_row(self, table, values=None) -> int:
        row = table.rowCount()
        table.insertRow(row)
        if values is None:
            values = [""] * table.columnCount()
        self._set_table_row(table, row, values)
        return row

    def _remove_selected_table_row(self, table) -> None:
        row = table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Remove Row", "Please select a row first.")
            return
        table.removeRow(row)

    def _add_write_link_row(self) -> None:
        variable_names = self.task_builder_controller._current_write_link_variable_names()
        default_source = variable_names[0] if variable_names else ""
        row = self._add_table_row(
            self.machine_ui.tableWidget_writeLinks,
            [default_source, "", "True"],
        )
        self.task_builder_controller.refresh_write_link_editors()
        self.machine_ui.tableWidget_writeLinks.selectRow(row)
        self._refresh_task_preview()

    def _remove_write_link_rows(self) -> None:
        table = self.machine_ui.tableWidget_writeLinks
        rows = sorted({index.row() for index in table.selectionModel().selectedRows()}, reverse=True)
        if not rows:
            QMessageBox.information(self, "Remove Link", "Please select one or more rows first.")
            return
        for row in rows:
            table.removeRow(row)
        if table.rowCount() == 0:
            self._add_table_row(table, ["", "", "True"])
        self.task_builder_controller.refresh_write_link_editors()
        self._refresh_task_preview()

    def _policy_target_names(self, kind: str) -> list[str]:
        mapping_rows = TaskService.table_to_records(self.machine_ui.tableWidget_mapping)
        return [
            str(row.get("Name", "")).strip()
            for row in mapping_rows
            if str(row.get("Role", "")).strip().lower() == kind
            and str(row.get("Name", "")).strip()
        ]

    def _policy_target_pv(self, kind: str, target: str) -> str:
        mapping_rows = TaskService.table_to_records(self.machine_ui.tableWidget_mapping)
        row = next(
            (
                item
                for item in mapping_rows
                if str(item.get("Role", "")).strip().lower() == kind
                and str(item.get("Name", "")).strip() == target
            ),
            None,
        )
        return str((row or {}).get("PV Name", "")).strip()

    @staticmethod
    def _policy_rule_summary(kwargs: dict) -> str:
        conditions = kwargs.get("conditions", []) or []
        action = str((kwargs.get("action", {}) or {}).get("type", "policy")).strip()
        match = str(kwargs.get("match", "all")).strip()
        action_labels = {
            "replace": "Replace result",
            "add_offset": "Add offset",
            "violate_bound": "Mark infeasible",
        }
        if len(conditions) == 1:
            condition = conditions[0]
            operator_labels = {
                "gt": ">",
                "ge": "≥",
                "lt": "<",
                "le": "≤",
                "eq": "=",
                "ne": "≠",
            }
            metric_labels = {
                "mean_abs": "Mean absolute sample",
                "max_abs": "Maximum absolute sample",
                "peak_to_peak": "Signal variation",
                "mean": "Mean sample",
                "std": "Sample standard deviation",
                "reduced": "Processed result",
            }
            metric = metric_labels.get(
                str(condition.get("metric")), condition.get("metric", "Value")
            )
            condition_text = (
                f"{metric} "
                f"{operator_labels.get(str(condition.get('operator')), condition.get('operator', ''))} "
                f"{condition.get('value', '')}"
            )
        else:
            condition_text = f"{match.title()} of {len(conditions)} conditions"
        return f"{condition_text} → {action_labels.get(action, action)}"

    def _mapping_target_for_policy(self, kind: str, kwargs: dict) -> str:
        target = str(kwargs.get("target") or "").strip()
        if target:
            return target
        names = self._policy_target_names(kind)
        try:
            target_col = int(kwargs.get("target_col", 0) or 0)
        except (TypeError, ValueError):
            return ""
        return names[target_col] if 0 <= target_col < len(names) else ""

    def _policy_binding_issues_by_index(self) -> dict[int, dict]:
        return {
            int(issue["binding_index"]): issue
            for issue in TaskService.policy_binding_issues(self._current_task())
            if issue.get("binding_index") is not None
        }

    def _bound_policy_rows(self, kind: str, target: str) -> list[dict]:
        results: list[dict] = []
        issues_by_index = self._policy_binding_issues_by_index()
        for index, binding in enumerate(self.machine_ui.policy_bindings):
            if binding.get("kind") != kind or binding.get("target") != target:
                continue
            policy = binding.get("policy", {}) or {}
            kwargs = copy.deepcopy(policy.get("kwargs", {}) or {})
            preset_name = str(binding.get("preset", "custom") or "custom")
            preset_label = self._policy_template_display_name(kind, preset_name)
            issue = issues_by_index.get(index)
            enabled = bool(binding.get("enabled", True))
            status = "Disabled" if not enabled else ("Issue" if issue else "Ready")
            results.append(
                {
                    "row": index,
                    "enabled": enabled,
                    "preset": preset_label,
                    "is_template": preset_name != "custom",
                    "summary": self._policy_rule_summary(kwargs),
                    "status": status,
                    "issue": str(issue.get("message", "")) if issue else "",
                    "kwargs": kwargs,
                    "binding": binding,
                }
            )
        return results

    def _load_policy_bindings(self, machine: dict) -> None:
        bindings: list[dict] = []
        if "policy_bindings" in machine:
            raw_bindings = machine.get("policy_bindings", []) or []
            for raw in raw_bindings:
                if not isinstance(raw, dict):
                    continue
                kind = str(raw.get("kind", "")).strip().lower()
                if kind not in {"objective", "constraint"}:
                    continue
                policy = raw.get("policy", {}) or {}
                if not isinstance(policy, dict):
                    continue
                kwargs = copy.deepcopy(policy.get("kwargs", {}) or {})
                if not isinstance(kwargs, dict):
                    continue
                target = str(raw.get("target") or kwargs.get("target") or "").strip()
                if not target:
                    target = self._mapping_target_for_policy(kind, kwargs)
                if target:
                    kwargs["target"] = target
                enabled_value = raw.get("enabled", True)
                enabled = (
                    enabled_value
                    if isinstance(enabled_value, bool)
                    else TaskService._is_enabled(enabled_value)
                )
                preset = str(raw.get("preset", "custom") or "custom").strip().lower()
                custom_ids = {
                    str(item.get("id", ""))
                    for item in self.machine_ui.policy_presets
                    if item.get("kind") == kind
                }
                if (
                    preset not in POLICY_REGISTRY.preset_names(kind)
                    and preset not in custom_ids
                ):
                    preset = "custom"
                bindings.append(
                    {
                        "target": target,
                        "kind": kind,
                        "enabled": bool(enabled),
                        "preset": preset,
                        "policy": {
                            "name": str(policy.get("name", "sample_guard")).strip().lower(),
                            "kwargs": kwargs,
                        },
                    }
                )
        else:
            for kind, field in (
                ("objective", "objective_policies"),
                ("constraint", "constraint_policies"),
            ):
                for row in machine.get(field, []) or []:
                    if not isinstance(row, dict):
                        continue
                    enabled_text = row.get("Enabled", "")
                    if enabled_text and not TaskService._is_enabled(enabled_text):
                        enabled = False
                    else:
                        enabled = True
                    name = str(row.get("Policy Name", row.get("name", ""))).strip().lower()
                    kwargs_source = row.get("Kwargs JSON", row.get("kwargs", {}))
                    try:
                        kwargs = (
                            copy.deepcopy(kwargs_source)
                            if isinstance(kwargs_source, dict)
                            else TaskService._parse_json_text(kwargs_source)
                        )
                    except ValueError:
                        kwargs = {}
                    preset = "custom"
                    try:
                        resolved_name = POLICY_REGISTRY.resolve(kind, name).name
                    except ValueError:
                        resolved_name = name
                    if resolved_name in POLICY_REGISTRY.preset_names(kind):
                        spec = POLICY_REGISTRY.expand_preset(
                            kind,
                            resolved_name,
                            legacy_kwargs=kwargs,
                        )
                        preset = resolved_name
                        policy_name = spec["name"]
                        kwargs = spec["kwargs"]
                    else:
                        policy_name = resolved_name or "sample_guard"
                        preset_label = str(row.get("Preset", "")).strip()
                        for preset_name in POLICY_REGISTRY.preset_names(kind):
                            if (
                                POLICY_REGISTRY.resolve_preset(
                                    kind, preset_name
                                ).display_name
                                == preset_label
                            ):
                                preset = preset_name
                                break
                    target = str(kwargs.get("target") or "").strip()
                    if not target:
                        target = self._mapping_target_for_policy(kind, kwargs)
                    if target:
                        kwargs["target"] = target
                    bindings.append(
                        {
                            "target": target,
                            "kind": kind,
                            "enabled": enabled,
                            "preset": preset,
                            "policy": {"name": policy_name, "kwargs": kwargs},
                        }
                    )
        self.machine_ui.policy_bindings = bindings
        self._refresh_mapping_policy_widgets()

    def _load_policy_presets(self, machine: dict) -> None:
        presets: list[dict] = []
        seen_ids: set[str] = set()
        for raw in machine.get("policy_presets", []) or []:
            if not isinstance(raw, dict):
                continue
            kind = str(raw.get("kind", "")).strip().lower()
            preset_id = str(raw.get("id", "")).strip().lower()
            display_name = str(raw.get("name", "")).strip()
            policy = raw.get("policy", {}) or {}
            kwargs = (policy.get("kwargs", {}) or {}) if isinstance(policy, dict) else {}
            if (
                kind not in {"objective", "constraint"}
                or not preset_id
                or preset_id == "custom"
                or preset_id in seen_ids
                or preset_id in POLICY_REGISTRY.preset_names(kind)
                or not display_name
                or not isinstance(policy, dict)
                or not isinstance(kwargs, dict)
            ):
                continue
            policy_name = str(policy.get("name", "sample_guard")).strip().lower()
            try:
                policy_name = POLICY_REGISTRY.resolve(kind, policy_name).name
                template_kwargs = copy.deepcopy(kwargs)
                template_kwargs["target"] = None
                template_kwargs["target_col"] = 0
                POLICY_REGISTRY.validate(kind, policy_name, template_kwargs)
            except (TypeError, ValueError):
                continue
            presets.append(
                {
                    "id": preset_id,
                    "name": display_name,
                    "kind": kind,
                    "description": str(raw.get("description", "")).strip(),
                    "policy": {"name": policy_name, "kwargs": template_kwargs},
                }
            )
            seen_ids.add(preset_id)
        self.machine_ui.policy_presets = presets
        if hasattr(self, "machine_controller"):
            self.machine_controller.refresh_policy_preset_browser()

    def _custom_policy_preset(self, kind: str, preset_id: str) -> dict | None:
        normalized = str(preset_id or "").strip().lower()
        return next(
            (
                preset
                for preset in self.machine_ui.policy_presets
                if preset.get("kind") == kind and preset.get("id") == normalized
            ),
            None,
        )

    def _policy_template_display_name(self, kind: str, preset_id: str) -> str:
        if preset_id == "custom":
            return "Custom Policy"
        custom_preset = self._custom_policy_preset(kind, preset_id)
        if custom_preset is not None:
            return str(custom_preset.get("name", preset_id))
        try:
            return POLICY_REGISTRY.resolve_preset(kind, preset_id).display_name
        except ValueError:
            return preset_id

    def _refresh_mapping_policy_widgets(self) -> None:
        if not hasattr(self.machine_ui, "tableWidget_mapping"):
            return
        table = self.machine_ui.tableWidget_mapping
        headers = self.task_builder_controller.table_headers(table)
        if "Policies" not in headers or "Policy Action" not in headers:
            return
        policy_col = headers.index("Policies")
        action_col = headers.index("Policy Action")
        old_state = table.blockSignals(True)
        try:
            for row in range(table.rowCount()):
                role_item = table.item(row, headers.index("Role"))
                name_item = table.item(row, headers.index("Name"))
                role = role_item.text().strip().lower() if role_item is not None else ""
                target = name_item.text().strip() if name_item is not None else ""
                if role not in {"objective", "constraint"} or not target:
                    summary_item = QTableWidgetItem("—")
                    summary_item.setFlags(summary_item.flags() & ~Qt.ItemIsEditable)
                    table.setItem(row, policy_col, summary_item)
                    table.setItem(row, action_col, QTableWidgetItem(""))
                    table.removeCellWidget(row, action_col)
                    continue
                bound = self._bound_policy_rows(role, target)
                enabled_count = sum(bool(policy["enabled"]) for policy in bound)
                if not bound:
                    summary = "No policies"
                    tooltip = summary
                else:
                    labels = [str(policy["preset"]) for policy in bound]
                    summary = ", ".join(labels)
                    if any(policy["status"] == "Issue" for policy in bound):
                        status = "Issue"
                    elif enabled_count:
                        status = "Ready"
                    else:
                        status = "Disabled"
                    summary += f" · {status}"
                    if enabled_count != len(bound):
                        summary += f" ({enabled_count}/{len(bound)} enabled)"
                    tooltip_lines = [
                        policy["issue"]
                        or f"{policy['preset']}: {policy['summary']} ({policy['status']})"
                        for policy in bound
                    ]
                    tooltip = "\n".join(tooltip_lines)
                summary_item = QTableWidgetItem(summary)
                summary_item.setToolTip(tooltip)
                summary_item.setFlags(summary_item.flags() & ~Qt.ItemIsEditable)
                table.setItem(row, policy_col, summary_item)
                table.setItem(row, action_col, QTableWidgetItem(""))
                table.removeCellWidget(row, action_col)
        finally:
            table.blockSignals(old_state)
        if table.currentRow() < 0 and table.rowCount():
            table.setCurrentCell(0, headers.index("Name"))
        if hasattr(self, "machine_controller"):
            self.machine_controller.refresh_mapping_detail()

    def _edit_policy_rule_row(
        self,
        kind: str,
        row: int,
        *,
        locked_target: str | None = None,
    ) -> bool:
        bindings = self.machine_ui.policy_bindings
        if row < 0 or row >= len(bindings):
            return False
        binding = bindings[row]
        if binding.get("kind") != kind:
            return False
        policy = binding.get("policy", {}) or {}
        preset_name = str(binding.get("preset", "custom") or "custom")
        target = locked_target or str(binding.get("target", ""))

        def create_dialog(*, read_only: bool, selected_preset: str | None):
            return SampleGuardRuleEditorDialog(
                kind=kind,
                target_names=self._policy_target_names(kind),
                policy_name=str(policy.get("name", "sample_guard")),
                kwargs=copy.deepcopy(policy.get("kwargs", {}) or {}),
                preset_name=selected_preset,
                custom_presets=copy.deepcopy(self.machine_ui.policy_presets),
                locked_target=target,
                pv_name=self._policy_target_pv(kind, target),
                read_only=read_only,
                template_display_name=self._policy_template_display_name(
                    kind, preset_name
                ),
                parent=self,
            )

        template_binding = preset_name != "custom"
        dialog = create_dialog(
            read_only=template_binding,
            selected_preset=preset_name if template_binding else None,
        )
        if dialog.exec_() != QDialog.Accepted:
            return False
        if template_binding:
            dialog = create_dialog(read_only=False, selected_preset=None)
            if dialog.exec_() != QDialog.Accepted:
                return False
        state = dialog.rule_state()
        binding["target"] = str(state["kwargs"].get("target") or locked_target or "")
        binding["preset"] = "custom" if template_binding else state["preset"]
        binding["policy"] = {"name": state["name"], "kwargs": state["kwargs"]}
        self._refresh_mapping_policy_widgets()
        self._refresh_task_preview()
        issue = self._policy_binding_issues_by_index().get(row)
        if issue is not None:
            QMessageBox.warning(
                self,
                "Policy Needs Setup",
                str(issue["message"]),
            )
        return True

    @staticmethod
    def _policy_preset_id(display_name: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "_", display_name.strip().lower()).strip("_")
        return f"custom_{normalized or 'rule'}"

    def _save_policy_binding_as_preset(self, kind: str, binding_index: int) -> None:
        if binding_index < 0 or binding_index >= len(self.machine_ui.policy_bindings):
            return
        binding = self.machine_ui.policy_bindings[binding_index]
        if binding.get("kind") != kind:
            return
        if str(binding.get("preset", "custom") or "custom") != "custom":
            QMessageBox.information(
                self,
                "Save Policy Template",
                "Customize this policy first. Existing templates are already reusable.",
            )
            return
        display_name, accepted = QInputDialog.getText(
            self,
            "Save Policy Template",
            "Template name:",
        )
        display_name = display_name.strip()
        if not accepted or not display_name:
            return
        built_in_names = {
            POLICY_REGISTRY.resolve_preset(kind, preset_name).display_name.casefold()
            for preset_name in POLICY_REGISTRY.preset_names(kind)
        }
        if display_name.casefold() in built_in_names or any(
            str(preset.get("name", "")).strip().casefold() == display_name.casefold()
            and preset.get("kind") == kind
            for preset in self.machine_ui.policy_presets
        ):
            QMessageBox.warning(
                self,
                "Save Policy Template",
                f"A {kind} Policy Template named {display_name!r} already exists.",
            )
            return
        preset_id = self._policy_preset_id(display_name)
        used_ids = {str(preset.get("id", "")) for preset in self.machine_ui.policy_presets}
        base_id = preset_id
        suffix = 2
        while preset_id in used_ids or preset_id in POLICY_REGISTRY.preset_names(kind):
            preset_id = f"{base_id}_{suffix}"
            suffix += 1
        policy = copy.deepcopy(binding.get("policy", {}) or {})
        kwargs = copy.deepcopy(policy.get("kwargs", {}) or {})
        kwargs["target"] = None
        kwargs["target_col"] = 0
        preset = {
            "id": preset_id,
            "name": display_name,
            "kind": kind,
            "description": self._policy_rule_summary(kwargs),
            "policy": {
                "name": str(policy.get("name", "sample_guard")),
                "kwargs": kwargs,
            },
        }
        self.machine_ui.policy_presets.append(preset)
        binding["preset"] = preset_id
        self.machine_controller.refresh_policy_preset_browser()
        self._refresh_mapping_policy_widgets()
        self._refresh_task_preview()

    def _rename_custom_policy_preset(self, preset_id: str) -> None:
        preset = next(
            (
                item
                for item in self.machine_ui.policy_presets
                if item.get("id") == preset_id
            ),
            None,
        )
        if preset is None:
            return
        display_name, accepted = QInputDialog.getText(
            self,
            "Rename Policy Template",
            "Template name:",
            text=str(preset.get("name", "")),
        )
        display_name = display_name.strip()
        if not accepted or not display_name:
            return
        kind = str(preset.get("kind", ""))
        built_in_names = {
            POLICY_REGISTRY.resolve_preset(kind, preset_name).display_name.casefold()
            for preset_name in POLICY_REGISTRY.preset_names(kind)
        }
        if display_name.casefold() in built_in_names or any(
            item is not preset
            and item.get("kind") == kind
            and str(item.get("name", "")).strip().casefold() == display_name.casefold()
            for item in self.machine_ui.policy_presets
        ):
            QMessageBox.warning(
                self,
                "Rename Policy Template",
                "That template name is already in use.",
            )
            return
        preset["name"] = display_name
        self.machine_controller.refresh_policy_preset_browser()
        self._refresh_mapping_policy_widgets()
        self._refresh_task_preview()

    def _delete_custom_policy_preset(self, preset_id: str) -> None:
        preset = next(
            (item for item in self.machine_ui.policy_presets if item.get("id") == preset_id),
            None,
        )
        if preset is None:
            return
        answer = QMessageBox.question(
            self,
            "Delete Policy Template",
            f"Delete template {preset.get('name', preset_id)!r}? Existing policy "
            "bindings will keep their behavior as Custom Policy.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.machine_ui.policy_presets.remove(preset)
        for binding in self.machine_ui.policy_bindings:
            if binding.get("preset") == preset_id:
                binding["preset"] = "custom"
        self.machine_controller.refresh_policy_preset_browser()
        self._refresh_mapping_policy_widgets()
        self._refresh_task_preview()

    def _constraint_target_has_bound(self, target: str) -> bool:
        rows = TaskService.table_to_records(self.task_ui.tableWidget_constraints)
        row = next(
            (
                item
                for item in rows
                if str(item.get("Name", "")).strip() == target
            ),
            None,
        )
        if row is None:
            return False
        try:
            TaskService._constraint_bounds_from_rows([row])
        except (TypeError, ValueError):
            return False
        return True

    def _add_policy_for_mapping(
        self,
        kind: str,
        target: str,
        pv_name: str = "",
    ) -> bool:
        picker = PolicyTemplatePickerDialog(
            kind=kind,
            target=target,
            pv_name=pv_name,
            custom_presets=copy.deepcopy(self.machine_ui.policy_presets),
            constraint_bound_ready=(
                kind != "constraint" or self._constraint_target_has_bound(target)
            ),
            parent=self,
        )
        if picker.exec_() != QDialog.Accepted:
            return False
        template = picker.selected_template()
        if template is None:
            return False
        preset_id = str(template.get("id", "custom") or "custom")
        if preset_id != "custom" and any(
            binding.get("kind") == kind
            and binding.get("target") == target
            and binding.get("preset") == preset_id
            for binding in self.machine_ui.policy_bindings
        ):
            QMessageBox.information(
                self,
                "Add Policy",
                f"{template.get('name', preset_id)} is already assigned to {target}.",
            )
            return False

        policy = copy.deepcopy(template.get("policy") or {})
        if bool(template.get("custom_rule")):
            policy = {
                "name": "sample_guard",
                "kwargs": POLICY_REGISTRY.resolve(kind, "sample_guard").defaults(),
            }
            preset_id = "custom"
        if not isinstance(policy, dict):
            QMessageBox.warning(self, "Add Policy", "The selected template is invalid.")
            return False
        names = self._policy_target_names(kind)
        kwargs = copy.deepcopy(policy.get("kwargs", {}) or {})
        kwargs["target"] = target
        kwargs["target_col"] = names.index(target) if target in names else 0
        binding = {
            "target": target,
            "kind": kind,
            "enabled": True,
            "preset": preset_id,
            "policy": {
                "name": str(policy.get("name", "sample_guard")),
                "kwargs": kwargs,
            },
        }
        self.machine_ui.policy_bindings.append(binding)
        row = len(self.machine_ui.policy_bindings) - 1
        if bool(template.get("custom_rule")):
            if not self._edit_policy_rule_row(kind, row, locked_target=target):
                self.machine_ui.policy_bindings.pop()
                self._refresh_mapping_policy_widgets()
                return False
            return True

        self._refresh_mapping_policy_widgets()
        self._refresh_task_preview()
        return True

    def _manage_mapping_policies(self, mapping_row: int) -> None:
        table = self.machine_ui.tableWidget_mapping
        headers = self.task_builder_controller.table_headers(table)
        if mapping_row < 0 or mapping_row >= table.rowCount():
            return
        role = table.item(mapping_row, headers.index("Role"))
        name = table.item(mapping_row, headers.index("Name"))
        pv = table.item(mapping_row, headers.index("PV Name"))
        kind = role.text().strip().lower() if role is not None else ""
        target = name.text().strip() if name is not None else ""
        pv_name = pv.text().strip() if pv is not None else ""
        if kind not in {"objective", "constraint"} or not target:
            return
        if not self._bound_policy_rows(kind, target):
            self._add_policy_for_mapping(kind, target, pv_name)
            return
        while True:
            bound = self._bound_policy_rows(kind, target)
            manager = MappingPolicyManagerDialog(
                target=target,
                pv_name=pv_name,
                policies=bound,
                parent=self,
            )
            if manager.exec_() != QDialog.Accepted:
                break
            request = manager.requested_action()
            if request is None:
                break
            action, selected = request
            if action == "add":
                self._add_policy_for_mapping(kind, target, pv_name)
            elif selected is not None and selected < len(bound) and action == "edit":
                self._edit_policy_rule_row(
                    kind,
                    int(bound[selected]["row"]),
                    locked_target=target,
                )
            elif selected is not None and selected < len(bound) and action == "remove":
                answer = QMessageBox.question(
                    self,
                    "Remove Policy",
                    f"Remove {bound[selected]['preset']} from {target}?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if answer == QMessageBox.Yes:
                    del self.machine_ui.policy_bindings[int(bound[selected]["row"])]
                    self._refresh_mapping_policy_widgets()
            elif selected is not None and selected < len(bound) and action == "toggle":
                policy_row = int(bound[selected]["row"])
                self.machine_ui.policy_bindings[policy_row]["enabled"] = not bool(
                    bound[selected]["enabled"]
                )
                self._refresh_mapping_policy_widgets()
                self._refresh_task_preview()
            elif (
                selected is not None
                and selected < len(bound)
                and action in {"move_up", "move_down"}
            ):
                neighbor = selected - 1 if action == "move_up" else selected + 1
                if 0 <= neighbor < len(bound):
                    policy_row = int(bound[selected]["row"])
                    neighbor_row = int(bound[neighbor]["row"])
                    bindings = self.machine_ui.policy_bindings
                    bindings[policy_row], bindings[neighbor_row] = (
                        bindings[neighbor_row],
                        bindings[policy_row],
                    )
                    self._refresh_mapping_policy_widgets()
                    self._refresh_task_preview()
            elif selected is not None and selected < len(bound) and action == "save_preset":
                self._save_policy_binding_as_preset(
                    kind,
                    int(bound[selected]["row"]),
                )

    def _qobj_alive(self, obj) -> bool:
        return obj is not None and not sip.isdeleted(obj)

    def _living_tables(self, *tables):
        return [table for table in tables if self._qobj_alive(table)]

    def _dynamic_table_records(self):
        return self.task_builder_controller.dynamic_table_records()

    def _apply_recommended_dynamic_params(
        self,
        algorithm_text: str,
        *,
        preserve_custom: bool = True,
        log_change: bool = True,
    ) -> None:
        self.task_builder_controller.apply_recommended_dynamic_params(
            algorithm_text,
            preserve_custom=preserve_custom,
            log_change=log_change,
        )

    def _on_algorithm_changed(self, text: str) -> None:
        self.task_builder_controller.on_algorithm_changed(text)

    def _on_objective_type_changed(self, text: str) -> None:
        self.task_builder_controller.on_objective_type_changed(text)

    def _on_test_function_changed(self, text: str) -> None:
        self.task_builder_controller.on_test_function_changed(text)

    def _set_table_row(self, table, row: int, values) -> None:
        if table.rowCount() <= row:
            table.setRowCount(row + 1)
        for col, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            item.setToolTip(str(value))
            if col == 0:
                item.setTextAlignment(Qt.AlignCenter)
            table.setItem(row, col, item)

    def _append_recent_eval(self, payload: dict) -> None:
        self.results_controller.append_recent_eval(payload)

    def _log_console(self, message: str) -> None:
        self.ui.plainTextEdit_consoleLog.appendPlainText(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

    def _log_warning(self, message: str) -> None:
        line = f"[{datetime.now().strftime('%H:%M:%S')}] [WARN] {message}"
        self.ui.plainTextEdit_consoleLog.appendPlainText(line)
        self.ui.plainTextEdit_warningError.appendPlainText(line)

    def _log_pv(self, message: str) -> None:
        line = f"[{datetime.now().strftime('%H:%M:%S')}] [PV] {message}"
        self.ui.plainTextEdit_consoleLog.appendPlainText(line)
        self.ui.plainTextEdit_pvLog.appendPlainText(line)

    def _log_event(self, message: str) -> None:
        self.run_ui.plainTextEdit_events.appendPlainText(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

    def _append_overview_activity(
        self,
        event: str,
        *,
        status: str,
        task: str | None = None,
        mode: str | None = None,
        algorithm: str | None = None,
    ) -> None:
        try:
            current_task = self._current_task()
        except Exception:
            current_task = {}
        self.state.add_recent_activity(
            {
                "event": event,
                "task": task or str(current_task.get("task_name", "")).strip() or "untitled_task",
                "mode": mode or str(current_task.get("mode", "")).strip() or "--",
                "algorithm": algorithm or str(current_task.get("algorithm", "")).strip() or "--",
                "status": status,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        self._refresh_overview_activity_table()

    def _refresh_overview_activity_table(self) -> None:
        table = getattr(self.ui, "tableWidget_recentProjects", None)
        if not self._qobj_alive(table):
            return
        table.setRowCount(0)
        for row, entry in enumerate(self.state.recent_activity):
            table.insertRow(row)
            self._set_table_row(
                table,
                row,
                [
                    entry.get("event", ""),
                    entry.get("task", ""),
                    entry.get("mode", ""),
                    entry.get("algorithm", ""),
                    entry.get("status", ""),
                    entry.get("timestamp", ""),
                ],
            )
        self._update_recent_activity_empty_state()

    def _update_recent_activity_empty_state(self) -> None:
        empty_label = getattr(self.ui, "label_recentActivityEmpty", None)
        table = getattr(self.ui, "tableWidget_recentProjects", None)
        if empty_label is None or table is None:
            return
        empty_label.setVisible(table.rowCount() == 0)

    def _refresh_overview_cards(self, task: dict | None = None) -> None:
        if task is None:
            try:
                task = self._current_task()
            except Exception:
                task = {}

        validation_label = getattr(self.ui, "label_validationStatus", None)
        validation_text = validation_label.text().strip() if validation_label is not None else "Not validated"
        validation_tooltip = validation_label.toolTip() if validation_label is not None else ""

        variables = TaskService._enabled_rows(task.get("variables", []))
        objectives = TaskService._enabled_rows(task.get("objectives", []))
        constraints = TaskService._enabled_rows(task.get("constraints", []))
        objective_type = str(task.get("objective_type", "--")).replace(" Objective", "").strip() or "--"
        budget = int(task.get("max_evaluations", 0) or 0)
        run_plan = (
            f"{objective_type} · Vars {len(variables)} · Obj {len(objectives)} · "
            f"Cons {len(constraints)} · {budget} evals"
        )

        mode = str(task.get("mode", "Offline")).strip()
        if mode == "Offline":
            backend_text = "Offline benchmark"
            backend_tooltip = "No machine connection is required for this task."
            backend_tone = "success"
        else:
            machine_status = self.machine_ui.label_statusValue.text().strip() or "Disconnected"
            test_status = self.state.last_test_read_status or "Not checked"
            backend_text = f"{machine_status} · PV {test_status}"
            backend_tooltip = self.state.last_test_read_detail or "Run PV Check before an Online start."
            readiness_text = f"{machine_status} {test_status}".lower()
            if "failed" in readiness_text or "error" in readiness_text:
                backend_tone = "danger"
            elif "passed" in machine_status.lower() and "passed" in test_status.lower():
                backend_tone = "success"
            else:
                backend_tone = "warning"

        run = self.state.run
        has_run = bool(self.state.latest_task_snapshot) or run.phase != "Idle" or run.eval_count > 0
        if has_run:
            last_outcome = f"{run.phase} · {run.eval_count} evals"
            run_task = str((self.state.latest_task_snapshot or {}).get("task_name", "")).strip()
            outcome_tooltip = f"Run task: {run_task}" if run_task else "Latest run in this GUI session."
        else:
            last_outcome = "No run yet"
            outcome_tooltip = "No optimization run has started in this GUI session."

        card_values = (
            (self.ui.label_cardCurrentTaskValue, validation_text, validation_tooltip),
            (self.ui.label_cardModeValue, run_plan, run_plan),
            (self.ui.label_cardAlgorithmValue, backend_text, backend_tooltip),
            (self.ui.label_cardStatusValue, last_outcome, outcome_tooltip),
        )
        for label, text, tooltip in card_values:
            label.setText(text)
            label.setToolTip(tooltip or text)

        validation_tone = str(validation_label.property("tone") or "subtle") if validation_label else "subtle"
        if validation_tone == "subtle" and validation_text == "Not validated":
            validation_tone = "warning"
        outcome_tone = {
            "Running": "success",
            "Finished": "success",
            "Completed": "success",
            "Stopping": "warning",
            "Aborted": "warning",
            "Restoring": "warning",
            "Abort Requested": "danger",
            "Error": "danger",
            "Failed": "danger",
            "Restore Failed": "danger",
        }.get(run.phase, "subtle")
        card_tones = (
            (self.ui.frame_cardCurrentTask, self.ui.label_cardCurrentTaskValue, validation_tone),
            (self.ui.frame_cardMode, self.ui.label_cardModeValue, "info"),
            (self.ui.frame_cardAlgorithm, self.ui.label_cardAlgorithmValue, backend_tone),
            (self.ui.frame_cardStatus, self.ui.label_cardStatusValue, outcome_tone),
        )
        for frame, label, tone in card_tones:
            frame.setProperty("tone", tone)
            label.setProperty("tone", tone)
            for widget in (frame, label):
                widget.style().unpolish(widget)
                widget.style().polish(widget)

    def _sync_workspace_status(self, task: dict | None = None) -> None:
        if not hasattr(self, "label_workspace_mode"):
            return
        if task is None:
            try:
                task = self._current_task()
            except Exception:
                task = {}

        mode = str(task.get("mode", "Offline")).strip() or "Offline"
        self.label_workspace_task.setText(str(task.get("task_name", "untitled_task")))
        self.label_workspace_mode.setText(mode)
        self.label_workspace_algorithm.setText(str(task.get("algorithm", "--")))
        self.label_workspace_run.setText(self.state.run.phase)

        if mode == "Offline":
            machine_text = "Offline"
            machine_tone = "subtle"
        else:
            machine_text = self.machine_ui.label_statusValue.text().strip() or "Disconnected"
            normalized_machine = machine_text.lower()
            if normalized_machine in {"ready", "connected"} or "passed" in normalized_machine:
                machine_tone = "success"
            elif "error" in normalized_machine or "failed" in normalized_machine:
                machine_tone = "danger"
            else:
                machine_tone = "warning"
        self.label_workspace_machine.setText(machine_text)

        run_tone = {
            "Running": "success",
            "Finished": "success",
            "Completed": "success",
            "Stopping": "warning",
            "Aborted": "warning",
            "Restoring": "warning",
            "Abort Requested": "danger",
            "Error": "danger",
            "Failed": "danger",
            "Restore Failed": "danger",
        }.get(self.state.run.phase, "subtle")
        self._set_status_label_tone(self.label_workspace_run, run_tone)
        self._set_status_label_tone(self.label_workspace_machine, machine_tone)
        self._resize_workspace_status_items()

    @staticmethod
    def _set_status_label_tone(label: QLabel, tone: str) -> None:
        frame = label.parentWidget()
        label.setProperty("tone", tone)
        frame.setProperty("tone", tone)
        for widget in (frame, label):
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def _refresh_overview_readiness(self) -> None:
        task = self.state.latest_task_snapshot or self._current_task()
        online_task = self._is_online_task(task)

        self.ui.label_readinessPythonValue.setText(sys.version.split()[0])
        self.ui.label_readinessGuiValue.setText("Ready (PyQt5)")

        try:
            import epics  # noqa: F401

            epics_text = "Available"
        except Exception as exc:
            epics_text = f"Unavailable ({type(exc).__name__})"
        self.ui.label_readinessEpicsValue.setText(epics_text)

        machine_status = self.machine_ui.label_statusValue.text().strip() or "Disconnected"
        if online_task:
            self.ui.label_readinessMachineValue.setText(machine_status)
        else:
            self.ui.label_readinessMachineValue.setText("Offline")

        self.ui.label_readinessTestReadValue.setText(self.state.last_test_read_status or "Not checked")

        detail_parts: list[str] = []
        if online_task:
            inherited_ca = os.environ.get("EPICS_CA_ADDR_LIST", "").strip()
            auto_discovery = os.environ.get("EPICS_CA_AUTO_ADDR_LIST", "").strip()
            if inherited_ca:
                detail_parts.append(f"CA: {inherited_ca}")
            elif auto_discovery:
                detail_parts.append(f"CA auto: {auto_discovery}")
            else:
                detail_parts.append("CA: EPICS defaults/network discovery")
        else:
            detail_parts.append("Offline task. Machine optional.")

        if self.state.last_test_read_detail:
            detail_parts.append(self.state.last_test_read_detail)
        self.ui.label_readinessDetail.setText("  ".join(detail_parts))
        self._sync_workspace_status(self._current_task())
        self._refresh_overview_cards(self._current_task())

    def _current_task(self) -> dict:
        return TaskService.collect_task_data(self.task_ui, self.machine_ui)

    def _apply_task_payload(self, task: dict, *, source_label: str | None = None, goto_builder: bool = True) -> None:
        self.task_builder_controller.apply_task_payload(
            task,
            source_label=source_label,
            goto_builder=goto_builder,
        )

    def _is_online_task(self, task: dict | None = None) -> bool:
        return self.machine_controller.is_online_task(task)

    def _set_machine_status(self, text: str) -> None:
        self.machine_controller.set_machine_status(text)

    def _resolve_epics_read_pv(self, task: dict) -> str:
        return self.machine_controller.resolve_epics_read_pv(task)

    def _ensure_machine_ready_for_online(self, task: dict) -> bool:
        return self.machine_controller.ensure_machine_ready_for_online(task)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------
    def _on_nav_changed(self, row: int) -> None:
        if row < 0:
            return
        self.ui.stackedWidget_pages.setCurrentIndex(row)
        labels = ["Overview", "Configure", "Run"]
        label = labels[row] if row < len(labels) else f"Page {row}"
        self.statusBar().showMessage(f"Switched to {label}")

    def go_to_page(self, page_index: int) -> None:
        if page_index in {self.PAGE_OVERVIEW, self.PAGE_DASHBOARD}:
            self.ui.listWidget_navPages.setCurrentRow(self.PAGE_OVERVIEW)
            return

        if page_index in {self.PAGE_CONFIGURE, self.PAGE_TASK_BUILDER, self.PAGE_MACHINE, self.PAGE_OFFLINE}:
            task = self._current_task()
            online_task = self._is_online_task(task)
            self.ui.listWidget_navPages.setCurrentRow(self.PAGE_CONFIGURE)
            if page_index == self.PAGE_MACHINE:
                if not online_task:
                    self.ui.tabWidget_configure.setCurrentIndex(self.CONFIGURE_TAB_TASK_BUILDER)
                    return
                self.ui.tabWidget_configure.setCurrentIndex(self.CONFIGURE_TAB_MACHINE)
            elif page_index == self.PAGE_OFFLINE:
                if online_task:
                    self.ui.tabWidget_configure.setCurrentIndex(self.CONFIGURE_TAB_TASK_BUILDER)
                    return
                self.ui.tabWidget_configure.setCurrentIndex(self.CONFIGURE_TAB_OFFLINE)
            elif page_index in {self.PAGE_CONFIGURE, self.PAGE_TASK_BUILDER}:
                self.ui.tabWidget_configure.setCurrentIndex(self.CONFIGURE_TAB_TASK_BUILDER)
            return

        if page_index in {self.PAGE_RUN, self.PAGE_RUN_MONITOR, self.PAGE_RESULTS}:
            self.ui.listWidget_navPages.setCurrentRow(self.PAGE_RUN)
            if page_index == self.PAGE_RESULTS:
                self.ui.tabWidget_runWorkspace.setCurrentIndex(self.RUN_TAB_RESULTS)
            else:
                self.ui.tabWidget_runWorkspace.setCurrentIndex(self.RUN_TAB_LIVE)
            return

        self.ui.listWidget_navPages.setCurrentRow(self.PAGE_OVERVIEW)

    # ------------------------------------------------------------------
    # Task builder actions
    # ------------------------------------------------------------------
    def _create_new_task(self) -> None:
        self.task_builder_controller.create_new_offline_task()

    def _create_new_offline_task(self) -> None:
        self.task_builder_controller.create_new_offline_task()

    def _create_new_online_task(self) -> None:
        self.task_builder_controller.create_new_online_task()

    def _browse_workdir(self) -> None:
        self.task_builder_controller.browse_workdir()

    def _refresh_task_preview(self) -> None:
        self.task_builder_controller.refresh_task_preview()
        self._sync_mode_specific_setup_tabs()
        self.runtime_status_controller.sync_run_workspace()

    def _set_readback_tolerance_enabled(self, enabled: bool) -> None:
        self.machine_ui.label_readbackTol.setEnabled(enabled)
        self.machine_ui.doubleSpinBox_readbackTol.setEnabled(enabled)

    def _sync_mode_specific_setup_tabs(self, task: dict | None = None) -> None:
        if task is None:
            task = self._current_task()
        online_task = self._is_online_task(task)
        machine_enabled = online_task
        offline_enabled = not online_task
        current_index = self.ui.tabWidget_configure.currentIndex()
        self._set_configure_tab_available(self.CONFIGURE_TAB_MACHINE, machine_enabled)
        self._set_configure_tab_available(self.CONFIGURE_TAB_OFFLINE, offline_enabled)
        self.offline_ui.groupBox_benchmark.setEnabled(offline_enabled)

        if online_task and current_index == self.CONFIGURE_TAB_OFFLINE:
            self.ui.tabWidget_configure.setCurrentIndex(self.CONFIGURE_TAB_MACHINE)
        elif not online_task and current_index == self.CONFIGURE_TAB_MACHINE:
            self.ui.tabWidget_configure.setCurrentIndex(self.CONFIGURE_TAB_OFFLINE)

    def _set_configure_tab_available(self, index: int, available: bool) -> None:
        self.ui.tabWidget_configure.setTabEnabled(index, available)
        if hasattr(self.ui.tabWidget_configure, "setTabVisible"):
            self.ui.tabWidget_configure.setTabVisible(index, available)

    def _show_task_preview(self) -> None:
        self.task_builder_controller.show_task_preview()

    def _open_bounds_tools(self) -> None:
        self.task_builder_controller.open_bounds_tool_dialog()

    def _open_algorithm_detail(self) -> None:
        self.task_builder_controller.open_algorithm_detail_dialog()

    def _toggle_algorithm_overrides(self, checked: bool) -> None:
        self.task_builder_controller.toggle_algorithm_overrides(checked)

    def _on_dynamic_param_table_changed(self) -> None:
        self.task_builder_controller.on_dynamic_param_table_changed()

    def _validate_task_build(self, task: dict) -> tuple[bool, list[str]]:
        return self.task_builder_controller.validate_task_build(task)

    def validate_task(self) -> bool:
        return self.task_builder_controller.validate_task()

    def validate_task_silent(self, task: dict | None = None) -> bool:
        return self.task_builder_controller.validate_task_silent(task)

    def export_config(self) -> None:
        self.task_builder_controller.export_config()

    def _open_config(self) -> None:
        self.task_builder_controller.open_config()

    def _save_project(self) -> None:
        self.task_builder_controller.save_project()

    def _table_headers(self, table) -> list[str]:
        return self.task_builder_controller.table_headers(table)

    def _fill_table_from_records(self, table, records) -> None:
        self.task_builder_controller.fill_table_from_records(table, records)

    def load_task_draft(self, path: str | Path) -> None:
        self.task_builder_controller.load_task_draft(path)

    # ------------------------------------------------------------------
    # Machine actions
    # ------------------------------------------------------------------
    def connect_machine(self) -> None:
        self.machine_controller.connect_machine()

    def disconnect_machine(self) -> None:
        self.machine_controller.disconnect_machine()

    def test_machine_read(self) -> None:
        self.machine_controller.test_machine_read()

    def _log_machine_policy_change(self, text: str) -> None:
        self.machine_controller.log_machine_policy_change(text)

    def _update_pv_library_summary(self) -> None:
        self.machine_controller.update_pv_library_summary()

    def _load_external_pv_library(self) -> None:
        self.machine_controller.load_external_pv_library()

    def _open_knob_library_dialog(self) -> None:
        self.machine_controller.open_knob_library_dialog()

    def _clear_selected_knobs(self) -> None:
        self.machine_controller.clear_selected_knobs()

    def _open_objective_library_dialog(self) -> None:
        self.machine_controller.open_objective_library_dialog()

    def _clear_selected_objectives(self) -> None:
        self.machine_controller.clear_selected_objectives()

    def _open_constraint_library_dialog(self) -> None:
        self.machine_controller.open_constraint_library_dialog()

    def _clear_selected_constraints(self) -> None:
        self.machine_controller.clear_selected_constraints()

    def _apply_selected_pv_library_entries(self) -> None:
        self.machine_controller.apply_selected_pv_library_entries()

    # ------------------------------------------------------------------
    # Run actions
    # ------------------------------------------------------------------
    def start_run(self) -> None:
        self.run_controller.start_run()

    def stop_run(self) -> None:
        self.run_controller.stop_run()

    def abort_and_restore(self) -> None:
        self.run_controller.abort_and_restore()

    def set_best_to_machine(self) -> None:
        self.run_controller.set_best_to_machine()

    def set_selected_pareto_to_machine(self) -> None:
        self.run_controller.set_selected_pareto_to_machine()

    def restore_initial_to_machine(self) -> None:
        self.run_controller.restore_initial_to_machine()

    def _update_runtime_labels(self) -> None:
        self.runtime_status_controller.update_runtime_labels()

    def _set_run_buttons_enabled(self, *, start: bool, stop: bool) -> None:
        self.runtime_status_controller.set_run_buttons_enabled(start=start, stop=stop)

    def _set_run_phase(self, text: str) -> None:
        self.runtime_status_controller.set_run_phase(text)

    def _append_run_history(self, status: str) -> None:
        self.runtime_status_controller.append_run_history(status)

    def _sync_status_panels(self) -> None:
        self.runtime_status_controller.sync_status_panels()

    def _resize_workspace_status_items(self) -> None:
        value_labels = (
            self.label_workspace_task,
            self.label_workspace_mode,
            self.label_workspace_algorithm,
            self.label_workspace_run,
            self.label_workspace_machine,
        )
        for value_label in value_labels:
            item = value_label.parentWidget()
            text_width = max(
                label.sizeHint().width() for label in item.findChildren(QLabel)
            )
            item.setMinimumWidth(max(102, text_width + 18))
            item.updateGeometry()

    def _summarize_x_values(self, x_values: dict | None) -> str:
        return self.results_controller.summarize_x_values(x_values)

    def _populate_results_tree(self) -> None:
        self.results_controller.populate_results_tree()

    def _update_results_summary_table(self, selected_item=None) -> None:
        self.results_controller.update_results_summary_table(selected_item)

    def _on_results_tree_selection_changed(self) -> None:
        self.results_controller.on_results_tree_selection_changed()

    def _open_selected_result_item(self, item, _column: int) -> None:
        self.results_controller.open_selected_result_item(item, _column)

    def _update_results_after_start(self, task: dict) -> None:
        self.results_controller.update_results_after_start(task)
        self.runtime_status_controller.sync_run_workspace(task)

    def _update_results_after_evaluation(self, payload: dict) -> None:
        self.results_controller.update_results_after_evaluation(payload)
        self.runtime_status_controller.sync_run_workspace()

    def _update_results_after_finish(self, payload: dict) -> None:
        self.results_controller.update_results_after_finish(payload)
        self.runtime_status_controller.sync_run_workspace()

    # ------------------------------------------------------------------
    # Tools / dialogs
    # ------------------------------------------------------------------

    def _sync_theme_toggle(self, theme_key: str) -> None:
        if self.theme_toggle_button is None:
            return
        if theme_key == DARK_THEME_KEY:
            self.theme_toggle_button.setText("☀")
            self.theme_toggle_button.setToolTip("Switch to light theme.")
        else:
            self.theme_toggle_button.setText("☾")
            self.theme_toggle_button.setToolTip("Switch to dark theme.")

    def _set_gui_theme(self, theme_key: str, *, persist: bool = True, log_change: bool = True) -> None:
        app = QApplication.instance()
        active_theme = apply_theme(app, theme_key)
        if persist:
            save_theme_key(active_theme)
        self._sync_theme_toggle(active_theme)
        if hasattr(self, "results_controller"):
            try:
                self._redraw_plots()
            except Exception:
                pass
        if log_change:
            self._log_console(f"Theme changed to: {theme_label(active_theme)}")
            self.statusBar().showMessage(f"Theme: {theme_label(active_theme)}", 4000)

    def _toggle_gui_theme(self) -> None:
        active_theme = current_theme_key(QApplication.instance())
        next_theme = LIGHT_THEME_KEY if active_theme == DARK_THEME_KEY else DARK_THEME_KEY
        self._set_gui_theme(next_theme)

    def _check_environment(self) -> None:
        self._refresh_overview_readiness()
        summary = (
            f"Python: {self.ui.label_readinessPythonValue.text()}\n"
            f"GUI: {self.ui.label_readinessGuiValue.text()}\n"
            f"pyepics: {self.ui.label_readinessEpicsValue.text()}\n"
            f"Machine: {self.ui.label_readinessMachineValue.text()}\n"
            f"Last Test Read: {self.ui.label_readinessTestReadValue.text()}"
        )
        self._append_overview_activity("Check", status="Refreshed run readiness.")
        self._log_console("Run readiness refreshed.")
        QMessageBox.information(self, "Run Readiness", summary)

    def _show_pv_monitor_stub(self) -> None:
        dialog = PVMonitorDialog(
            self._current_task,
            timeout_provider=lambda: float(self.machine_ui.doubleSpinBox_timeout.value()),
            parent=self,
        )
        dialog.exec_()

    def _show_policy_editor(self) -> None:
        self.ui.tabWidget_configure.setCurrentIndex(self.CONFIGURE_TAB_MACHINE)
        self.machine_ui.tabWidget_machine.setCurrentWidget(self.machine_ui.tab_mapping)
        self.go_to_page(self.PAGE_MACHINE)
        self._log_console("Opened Machine Setup -> PV Mapping policy management.")
        table = self.machine_ui.tableWidget_mapping
        headers = self.task_builder_controller.table_headers(table)
        for row in range(table.rowCount()):
            role_item = table.item(row, headers.index("Role"))
            role = role_item.text().strip().lower() if role_item is not None else ""
            if role in {"objective", "constraint"}:
                table.selectRow(row)
                self._manage_mapping_policies(row)
                return
        QMessageBox.information(
            self,
            "Policy Editor",
            "Add an objective or constraint row to PV Mapping before assigning a policy.",
        )

    def _reset_layout(self) -> None:
        self.ui.splitter_main.setSizes([230, 1370])
        if self.ui.splitter_centerVertical.count() > 1:
            self.ui.splitter_centerVertical.setSizes([760, 220])
        else:
            self.ui.splitter_centerVertical.setSizes([1])
        if hasattr(self.ui, "splitter_dashboardLower"):
            self.ui.splitter_dashboardLower.setSizes([780, 560])
        if hasattr(self.ui, "splitter_configureMain"):
            self.ui.splitter_configureMain.setSizes([1320])
        if hasattr(self.ui, "splitter_resultsMain"):
            self.ui.splitter_resultsMain.setSizes([270, 1070])
        if hasattr(self.ui, "splitter_resultsRight"):
            self.ui.splitter_resultsRight.setSizes([460, 260])
        if hasattr(self.ui, "splitter_convergencePlots"):
            self.ui.splitter_convergencePlots.setSizes([1, 1])
        if hasattr(self.run_ui, "splitter_main"):
            self.run_ui.splitter_main.setSizes([430, 240])
        if hasattr(self.run_ui, "splitter_runRight"):
            self.run_ui.splitter_runRight.setSizes([280, 720])
        self.statusBar().showMessage("Layout reset")

    def _show_about(self) -> None:
        QMessageBox.information(
            self,
            "About GOTAcc Studio",
            "GOTAcc Studio\n"
            "Optimization Workbench for Accelerator Applications\n\n"
            "PyQt5 GUI shell for task configuration, machine connection,\n"
            "run monitoring, and results inspection.",
        )

    def export_results(self) -> None:
        default_name = f"{self.task_ui.lineEdit_taskName.text().strip() or 'task'}_results_summary.json"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Results Summary",
            str(Path(self.task_ui.lineEdit_workdir.text().strip() or Path.cwd()) / default_name),
            "JSON Files (*.json);;All Files (*)",
        )
        if not path:
            return
        export_dir = Path(path).parent
        export_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._redraw_plots()
            self.results_controller.save_result_images(export_dir)
        except Exception as exc:
            self._log_warning(f"Results image export failed: {exc}")
        summary = {
            "task": self.state.latest_task_snapshot or self._current_task(),
            "run_state": self.state.latest_finish_payload.get("state", self.state.run.phase)
            if self.state.latest_finish_payload
            else self.state.run.phase,
            "best_value": self.state.run.best_value,
            "best_x": self.state.latest_best_x,
            "history_path": self.state.latest_history_path,
            "plot_path": self.state.latest_plot_path,
            "result_plot_paths": self.state.latest_result_plot_paths,
            "output_directory": self.state.latest_result_output_dir,
            "latest_evaluation": self.state.latest_eval_payload,
            "objective_dim": self.state.objective_dim,
            "eval_count": self.state.run.eval_count,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        self._log_console(f"Results summary exported to: {path}")
        QMessageBox.information(self, "Export Results", f"Results summary exported to:\n{path}")


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
