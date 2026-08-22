from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import QSize, Qt
try:
    import sip
except ImportError:  # pragma: no cover
    from PyQt5 import sip
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

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
    from .tool_dialogs import PVMonitorDialog
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
    from tool_dialogs import PVMonitorDialog

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
        self.run_session = RunSession(self)
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
        backend_item, self.label_workspace_backend = self._status_strip_item("EPICS", "Disconnected")
        best_item, self.label_workspace_best = self._status_strip_item("BEST", "--")
        self._add_status_strip_items(task_item, mode_item, algorithm_item, backend_item, best_item)
        outer_layout.addWidget(self.frame_workspace_status)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(8, 0, 8, 0)
        header_row.setSpacing(0)
        header_row.addWidget(self.frame_workspace_header)
        shell_layout.addLayout(header_row)
        shell_layout.addWidget(self.ui.splitter_main, 1)
        self._promote_bottom_log_panel()

    def _status_strip_item(self, title: str, value: str) -> tuple[QFrame, QLabel]:
        item = QFrame(self.frame_workspace_status)
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

        self.ui.label_cardCurrentTaskValue.setText("Untitled Task")
        self.ui.label_cardModeValue.setText("Offline")
        self.ui.label_cardAlgorithmValue.setText("BO")
        self.ui.label_cardStatusValue.setText("Idle")

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
        self.machine_ui.checkBox_confirm.setChecked(True)
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

        self._set_run_buttons_enabled(start=True, pause=False, resume=False, stop=False)
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
        self._compact_task_builder_footer_actions()
        self._compact_run_monitor_actions()
        self._configure_tab_text_sizing()
        self._compact_overview_panels()

        for button in (
            self.ui.pushButton_newOfflineTask,
            self.ui.pushButton_newOnlineTask,
            self.ui.pushButton_openConfig,
            self.ui.pushButton_saveProject,
            self.ui.pushButton_validateTask,
            self.ui.pushButton_startRun,
            self.ui.pushButton_pauseRun,
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
            self.ui.pushButton_pauseRun,
            self.ui.pushButton_stopRun,
            self.ui.pushButton_checkEnvironment,
            self.task_ui.pushButton_browseWorkdir,
            self.task_ui.pushButton_openAlgorithmDetail,
            self.task_ui.pushButton_openBoundsTools,
            self.task_ui.pushButton_preview,
            self.task_ui.pushButton_validate,
            self.task_ui.pushButton_export,
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
        self.ui.pushButton_newOfflineTask.setToolTip("Create a new Online task. Change Mode in Task Builder if Offline is needed.")
        self.ui.pushButton_newOnlineTask.setVisible(False)
        self.ui.pushButton_newOnlineTask.setEnabled(False)
        self.ui.pushButton_openConfig.setText("Open Project")
        self.ui.pushButton_openConfig.setToolTip("Load a saved GOTAcc Studio project.")
        self.ui.pushButton_saveProject.setText("Save Project")
        self.ui.pushButton_saveProject.setToolTip("Save the current GUI project for later editing.")
        self.ui.actionNewTask.setText("New Task")
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
        self.task_ui.pushButton_openBoundsTools.setText("Bounds")
        self.task_ui.pushButton_openBoundsTools.setToolTip("Open Bounds Tools.")
        self.task_ui.horizontalLayout_variablesToolbar.takeAt(0)
        self.task_ui.horizontalLayout_variablesToolbar.setContentsMargins(8, 3, 8, 3)
        self.task_ui.horizontalLayout_variablesToolbar.setSpacing(6)
        self.task_ui.horizontalLayout_variablesToolbarActions.setSpacing(6)
        self.task_ui.horizontalLayout_variablesToolbar.addStretch(1)
        self.task_ui.frame_variablesToolbar.setMaximumHeight(34)

    def _compact_task_builder_footer_actions(self) -> None:
        actions = (
            self.task_ui.pushButton_preview,
            self.task_ui.pushButton_validate,
            self.task_ui.pushButton_export,
        )
        for button in actions:
            button.setProperty("inlineAction", True)
            button.setFixedHeight(24)
            button.setFixedWidth(88)
            button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self.task_ui.pushButton_export.setText("Export Task")
        self.task_ui.pushButton_export.setToolTip("Export a runnable TaskConfig.")

        layout = self.task_ui.horizontalLayout_actionBar
        layout.setContentsMargins(0, 3, 0, 0)
        layout.setSpacing(6)
        layout.removeWidget(self.task_ui.pushButton_export)
        layout.insertWidget(2, self.task_ui.pushButton_export)

    def _compact_run_monitor_actions(self) -> None:
        self._run_primary_actions_in_sidebar = True
        self.run_ui.frame_runHero.setVisible(False)
        self._compact_run_snapshot()
        self.run_ui.groupBox_actions.setTitle("Machine Actions")
        self.run_ui.verticalLayout_actionsBox.setContentsMargins(8, 10, 8, 8)
        self.run_ui.verticalLayout_actionsBox.setSpacing(4)
        self.run_ui.horizontalLayout_actions.setSpacing(6)

        for button in (
            self.run_ui.pushButton_start,
            self.run_ui.pushButton_pause,
            self.run_ui.pushButton_resume,
            self.run_ui.pushButton_stop,
        ):
            button.setVisible(False)

        for button in (
            self.run_ui.pushButton_abortRestore,
            self.run_ui.pushButton_restoreInitial,
            self.run_ui.pushButton_setBest,
        ):
            button.setProperty("inlineAction", True)
            button.setFixedHeight(24)
            button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

        self.run_ui.pushButton_abortRestore.setProperty("danger", True)
        self.run_ui.groupBox_actions.setMaximumHeight(54)

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
            self.run_ui.frame_phase,
        )
        separators = []
        for index, frame in enumerate(frames):
            frame.setObjectName("statusItem")
            frame.setProperty("tone", "subtle")
            frame.setMinimumHeight(42)
            frame.setMaximumHeight(44)
            frame.setMinimumWidth(102)
            frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
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
            self.run_ui.verticalLayout_phase,
        )
        for layout in layouts:
            layout.setContentsMargins(10, 0, 8, 0)
            layout.setSpacing(2)

        title_labels = (
            self.run_ui.label_evalTitle,
            self.run_ui.label_elapsedTitle,
            self.run_ui.label_bestTitle,
            self.run_ui.label_feasibilityTitle,
            self.run_ui.label_phaseTitle,
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
            self.run_ui.label_phaseValue,
        )
        for label in value_labels:
            label.setProperty("role", "value")
            label.setProperty("tone", "subtle")
            label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            label.setMaximumHeight(20)

        for widget in (*frames, *separators, *title_labels, *value_labels):
            widget.style().unpolish(widget)
            widget.style().polish(widget)

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
        self.ui.groupBox_dashboardSummary.setMaximumHeight(170)
        self.ui.gridLayout_dashboardSummary.setContentsMargins(10, 12, 10, 10)
        self.ui.gridLayout_dashboardSummary.setHorizontalSpacing(8)
        self.ui.gridLayout_dashboardSummary.setVerticalSpacing(8)

        for frame in (
            self.ui.frame_cardCurrentTask,
            self.ui.frame_cardMode,
            self.ui.frame_cardAlgorithm,
            self.ui.frame_cardStatus,
        ):
            frame.setMinimumHeight(82)
            frame.setMaximumHeight(108)
            frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

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

    def _init_task_builder_tables(self) -> None:
        variables_headers = ["Enable", "Name", "Lower", "Upper", "Initial", "Group"]
        objectives_headers = ["Enable", "Name", "Direction", "Weight", "Samples", "Math"]
        constraints_headers = ["Enable", "Name", "Lower", "Upper", "Math"]
        dynamic_headers = ["Parameter", "Value", "Type", "Description"]

        self._setup_table(self.task_ui.tableWidget_variables, variables_headers, 2)
        self._setup_table(self.task_ui.tableWidget_objectives, objectives_headers, 1)
        self._setup_table(self.task_ui.tableWidget_constraints, constraints_headers, 1)
        self._setup_table(self.task_ui.tableWidget_dynamicParams, dynamic_headers, 4)
        self.task_ui.tableWidget_variables.setSelectionMode(QAbstractItemView.ExtendedSelection)

        self._set_table_row(self.task_ui.tableWidget_variables, 0, ["Y", "x0", "0.0", "1.0", "0.5", "main"])
        self._set_table_row(self.task_ui.tableWidget_variables, 1, ["Y", "x1", "0.0", "1.0", "0.5", "main"])
        self.task_builder_controller.fill_table_from_records(
            self.task_ui.tableWidget_objectives,
            [
                {
                    "Enable": "Y",
                    "Name": "obj0",
                    "Direction": "maximize",
                    "Weight": "1.0",
                    "Samples": "1",
                    "Math": "mean",
                }
            ],
        )
        self.task_builder_controller.fill_table_from_records(
            self.task_ui.tableWidget_constraints,
            [
                {
                    "Enable": "N",
                    "Name": "cons0",
                    "Lower": "",
                    "Upper": "1.0",
                    "Math": "mean",
                }
            ],
        )
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

    def _init_machine_tables(self) -> None:
        mapping_headers = ["Role", "Name", "PV Name", "Readback", "Group", "Note"]
        write_headers = ["Source Index", "Target PV", "Enabled"]
        objective_policy_headers = ["Enabled", "Policy Name", "Kwargs JSON"]
        constraint_policy_headers = ["Enabled", "Policy Name", "Kwargs JSON"]

        self._setup_table(self.machine_ui.tableWidget_mapping, mapping_headers, 3)
        self._setup_table(self.machine_ui.tableWidget_writeLinks, write_headers, 1)
        self._setup_table(self.machine_ui.tableWidget_objectivePolicies, objective_policy_headers, 1)
        self._setup_table(self.machine_ui.tableWidget_constraintPolicies, constraint_policy_headers, 1)
        self.machine_ui.tableWidget_writeLinks.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.machine_ui.tableWidget_objectivePolicies.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.machine_ui.tableWidget_constraintPolicies.setSelectionMode(QAbstractItemView.ExtendedSelection)

        self._set_table_row(self.machine_ui.tableWidget_mapping, 0, ["knob", "x0", "", "", "main", ""])
        self._set_table_row(self.machine_ui.tableWidget_mapping, 1, ["objective", "obj0", "", "", "metric", ""])
        self._set_table_row(self.machine_ui.tableWidget_mapping, 2, ["", "", "", "", "", ""])
        self._set_table_row(self.machine_ui.tableWidget_writeLinks, 0, ["x0", "TEST:K1:LINK", "False"])
        self._set_table_row(
            self.machine_ui.tableWidget_objectivePolicies,
            0,
            self.task_builder_controller.objective_policy_default_row("fel_energy_guard", enabled="False"),
        )
        self._set_table_row(
            self.machine_ui.tableWidget_constraintPolicies,
            0,
            self.task_builder_controller.constraint_policy_default_row("bpm_guard", enabled="False"),
        )
        self.task_builder_controller.refresh_write_link_editors()
        self.task_builder_controller.refresh_objective_policy_editors()
        self.task_builder_controller.refresh_constraint_policy_editors()

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
        self.ui.groupBox_dashboardSummary.setTitle("Current Task")
        self.ui.label_recentActivityHint.setVisible(False)
        self.ui.label_readinessHint.setVisible(False)
        self.ui.label_recentActivityEmpty.setText("No recent activity.")

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

        self.ui.pushButton_newOfflineTask.clicked.connect(self._create_new_task)
        self.ui.pushButton_openConfig.clicked.connect(self._open_config)
        self.ui.pushButton_saveProject.clicked.connect(self._save_project)
        self.ui.pushButton_validateTask.clicked.connect(self.validate_task)
        self.ui.pushButton_startRun.clicked.connect(self.start_run)
        self.ui.pushButton_pauseRun.clicked.connect(self.pause_run)
        self.ui.pushButton_stopRun.clicked.connect(self.stop_run)
        self.ui.pushButton_checkEnvironment.clicked.connect(self._check_environment)

        self.ui.actionNewTask.triggered.connect(self._create_new_task)
        self.ui.actionOpenConfig.triggered.connect(self._open_config)
        self.ui.actionSaveProject.triggered.connect(self._save_project)
        self.ui.actionExportResults.triggered.connect(self.export_results)
        self.ui.actionExit.triggered.connect(self.close)
        self.ui.actionValidate.triggered.connect(self.validate_task)
        self.ui.actionStart.triggered.connect(self.start_run)
        self.ui.actionPause.triggered.connect(self.pause_run)
        self.ui.actionStop.triggered.connect(self.stop_run)
        self.ui.actionRestoreMachine.triggered.connect(self.abort_and_restore)
        self.ui.actionEnvironmentCheck.triggered.connect(self._check_environment)
        self.ui.actionPVMonitor.triggered.connect(self._show_pv_monitor_stub)
        self.ui.actionPolicyEditor.triggered.connect(self._show_policy_editor_stub)
        self.ui.actionResetLayout.triggered.connect(self._reset_layout)
        self.ui.actionAboutGOTAcc.triggered.connect(self._show_about)

        self.task_ui.lineEdit_taskName.textChanged.connect(self._refresh_task_preview)
        self.task_ui.comboBox_mode.currentTextChanged.connect(self._refresh_task_preview)
        self.task_ui.comboBox_objectiveType.currentTextChanged.connect(self._on_objective_type_changed)
        self.task_ui.comboBox_algorithm.currentTextChanged.connect(self._on_algorithm_changed)
        self.task_ui.comboBox_testFunction.currentTextChanged.connect(self._refresh_task_preview)
        self.task_ui.spinBox_seed.valueChanged.connect(self._refresh_task_preview)
        self.task_ui.spinBox_maxEval.valueChanged.connect(self._refresh_task_preview)
        self.task_ui.lineEdit_workdir.textChanged.connect(self._refresh_task_preview)
        self.task_ui.pushButton_browseWorkdir.clicked.connect(self._browse_workdir)
        self.task_ui.pushButton_preview.clicked.connect(self._show_task_preview)
        self.task_ui.pushButton_validate.clicked.connect(self.validate_task)
        self.task_ui.pushButton_export.clicked.connect(self.export_config)
        self.task_ui.pushButton_openBoundsTools.clicked.connect(self._open_bounds_tools)
        self.task_ui.pushButton_openAlgorithmDetail.clicked.connect(self._open_algorithm_detail)
        self.task_ui.toolButton_toggleAlgorithmOverrides.toggled.connect(self._toggle_algorithm_overrides)

        # Refresh preview when table cells change.
        self.task_ui.tableWidget_variables.itemChanged.connect(lambda *_: self._refresh_task_preview())
        self.task_ui.tableWidget_objectives.itemChanged.connect(lambda *_: self._refresh_task_preview())
        self.task_ui.tableWidget_constraints.itemChanged.connect(lambda *_: self._refresh_task_preview())
        self.task_ui.tableWidget_dynamicParams.itemChanged.connect(self._on_dynamic_param_table_changed)
        self.machine_ui.tableWidget_mapping.itemChanged.connect(lambda *_: self._refresh_task_preview())
        self.machine_ui.tableWidget_mapping.itemChanged.connect(lambda *_: self.machine_controller.refresh_selected_library_tables())
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
        self.machine_ui.pushButton_addObjectivePolicy.clicked.connect(self._add_objective_policy_row)
        self.machine_ui.pushButton_removeObjectivePolicy.clicked.connect(self._remove_objective_policy_rows)
        self.machine_ui.pushButton_addConstraintPolicy.clicked.connect(self._add_constraint_policy_row)
        self.machine_ui.pushButton_removeConstraintPolicy.clicked.connect(self._remove_constraint_policy_rows)
        self.machine_ui.comboBox_policy.currentTextChanged.connect(self._log_machine_policy_change)
        self.machine_ui.checkBox_autoConnect.toggled.connect(self._refresh_task_preview)
        self.machine_ui.checkBox_confirm.toggled.connect(self._refresh_task_preview)
        self.machine_ui.checkBox_restore.toggled.connect(self._refresh_task_preview)
        self.machine_ui.checkBox_readbackCheck.toggled.connect(self._refresh_task_preview)
        self.machine_ui.doubleSpinBox_readbackTol.valueChanged.connect(self._refresh_task_preview)
        self.machine_ui.doubleSpinBox_setInterval.valueChanged.connect(self._refresh_task_preview)
        self.machine_ui.doubleSpinBox_sampleInterval.valueChanged.connect(self._refresh_task_preview)
        self.machine_ui.doubleSpinBox_timeout.valueChanged.connect(self._refresh_task_preview)
        self.machine_ui.lineEdit_caAddress.textChanged.connect(self._refresh_task_preview)
        self.machine_ui.tableWidget_objectivePolicies.itemChanged.connect(lambda *_: self._refresh_task_preview())
        self.machine_ui.tableWidget_constraintPolicies.itemChanged.connect(lambda *_: self._refresh_task_preview())

        self.run_ui.pushButton_start.clicked.connect(self.start_run)
        self.run_ui.pushButton_pause.clicked.connect(self.pause_run)
        self.run_ui.pushButton_resume.clicked.connect(self.resume_run)
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

    def _add_objective_policy_row(self) -> None:
        row = self._add_table_row(
            self.machine_ui.tableWidget_objectivePolicies,
            self.task_builder_controller.objective_policy_default_row("fel_energy_guard", enabled="True"),
        )
        self.task_builder_controller.refresh_objective_policy_editors()
        self.machine_ui.tableWidget_objectivePolicies.selectRow(row)
        self._refresh_task_preview()

    def _remove_objective_policy_rows(self) -> None:
        table = self.machine_ui.tableWidget_objectivePolicies
        rows = sorted({index.row() for index in table.selectionModel().selectedRows()}, reverse=True)
        if not rows:
            QMessageBox.information(self, "Remove Policy", "Please select one or more rows first.")
            return
        for row in rows:
            table.removeRow(row)
        if table.rowCount() == 0:
            self._add_table_row(
                table,
                self.task_builder_controller.objective_policy_default_row(
                    "fel_energy_guard",
                    enabled="False",
                ),
            )
        self.task_builder_controller.refresh_objective_policy_editors()
        self._refresh_task_preview()

    def _add_constraint_policy_row(self) -> None:
        row = self._add_table_row(
            self.machine_ui.tableWidget_constraintPolicies,
            self.task_builder_controller.constraint_policy_default_row("bpm_guard", enabled="True"),
        )
        self.task_builder_controller.refresh_constraint_policy_editors()
        self.machine_ui.tableWidget_constraintPolicies.selectRow(row)
        self._refresh_task_preview()

    def _remove_constraint_policy_rows(self) -> None:
        table = self.machine_ui.tableWidget_constraintPolicies
        rows = sorted({index.row() for index in table.selectionModel().selectedRows()}, reverse=True)
        if not rows:
            QMessageBox.information(self, "Remove Policy", "Please select one or more rows first.")
            return
        for row in rows:
            table.removeRow(row)
        if table.rowCount() == 0:
            self._add_table_row(
                table,
                self.task_builder_controller.constraint_policy_default_row(
                    "bpm_guard",
                    enabled="False",
                ),
            )
        self.task_builder_controller.refresh_constraint_policy_editors()
        self._refresh_task_preview()

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

    def _set_table_row(self, table, row: int, values) -> None:
        if table.rowCount() <= row:
            table.setRowCount(row + 1)
        for col, value in enumerate(values):
            item = QTableWidgetItem(str(value))
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
        self.task_builder_controller.create_new_online_task()

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

    def validate_task_silent(self) -> bool:
        return self.task_builder_controller.validate_task_silent()

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

    def pause_run(self) -> None:
        self.run_controller.pause_run()

    def resume_run(self) -> None:
        self.run_controller.resume_run()

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

    def _set_run_buttons_enabled(self, *, start: bool, pause: bool, resume: bool, stop: bool) -> None:
        self.runtime_status_controller.set_run_buttons_enabled(
            start=start,
            pause=pause,
            resume=resume,
            stop=stop,
        )

    def _set_run_phase(self, text: str) -> None:
        self.runtime_status_controller.set_run_phase(text)

    def _append_run_history(self, status: str) -> None:
        self.runtime_status_controller.append_run_history(status)

    def _sync_status_panels(self) -> None:
        self.runtime_status_controller.sync_status_panels()
        if hasattr(self, "label_workspace_mode"):
            self.label_workspace_task.setText(self.ui.label_statusTaskValue.text())
            self.label_workspace_mode.setText(self.ui.label_cardModeValue.text())
            self.label_workspace_algorithm.setText(self.ui.label_cardAlgorithmValue.text())
            self.label_workspace_backend.setText(self.ui.label_statusConnectionValue.text())
            self.label_workspace_best.setText(self.ui.label_statusBestValue.text())
            self._resize_workspace_status_items()

    def _resize_workspace_status_items(self) -> None:
        value_labels = (
            self.label_workspace_task,
            self.label_workspace_mode,
            self.label_workspace_algorithm,
            self.label_workspace_backend,
            self.label_workspace_best,
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

    def _show_policy_editor_stub(self) -> None:
        self.ui.tabWidget_configure.setCurrentIndex(self.CONFIGURE_TAB_MACHINE)
        if hasattr(self.machine_ui, "tab_advancedMachine"):
            self.machine_ui.tabWidget_machine.setCurrentWidget(self.machine_ui.tab_advancedMachine)
            self.machine_ui.tabWidget_machineAdvanced.setCurrentWidget(self.machine_ui.tab_objectivePolicy)
            location = "Machine Setup -> Advanced -> Objective Policy"
        else:
            self.machine_ui.tabWidget_machine.setCurrentWidget(self.machine_ui.tab_objectivePolicy)
            location = "Machine Setup -> Objective Policy"
        QMessageBox.information(
            self,
            "Policy Editor",
            f"Objective policies are now edited directly in {location}.",
        )
        self.go_to_page(self.PAGE_MACHINE)
        self._log_console(f"Opened {location}.")

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
            self.ui.splitter_resultsMain.setSizes([320, 1020])
        if hasattr(self.ui, "splitter_resultsRight"):
            self.ui.splitter_resultsRight.setSizes([480, 280])
        if hasattr(self.ui, "splitter_convergencePlots"):
            self.ui.splitter_convergencePlots.setSizes([320, 240])
        if hasattr(self.run_ui, "splitter_main"):
            self.run_ui.splitter_main.setSizes([790, 540])
        if hasattr(self.run_ui, "splitter_runRight"):
            self.run_ui.splitter_runRight.setSizes([130, 330])
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
