import logging
import sys
import re
import json
from pathlib import Path

_REPO_BOOTSTRAP_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file()
)
if str(_REPO_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_BOOTSTRAP_ROOT))

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

from PyQt5.QtWidgets import (
    QMainWindow,
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QMessageBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtCore import QRegExp, Qt, QTimer
from OrbCorgui import Ui_MainWindow

from half_linac.src.shared.machine_profile import (
    MachineProfileError,
    load_app_context,
    require_workflow_write_allowed,
)
from half_linac.src.shared.process_runtime import ManagedProcessGroup
from half_linac.src.shared.window_activation import install_qt_window_raise_handler
from half_linac.src.apps.orbit_correct.profile_runtime import (
    APP_DIR,
    display_unit,
    get_active_response_matrix_record,
    list_response_matrix_records,
    load_orbit_runtime_settings,
    set_active_response_matrix,
)

HEADER_ACTION_HEIGHT = 32
logger = logging.getLogger(__name__)

DARK_THEME = {
    "window_bg": "#0f1519",
    "window_fg": "#e6edf2",
    "panel_bg": "#172027",
    "panel_border": "#24333d",
    "summary_bg": "#1b262d",
    "summary_border": "#2b3a45",
    "summary_title_fg": "#f3efe3",
    "muted_fg": "#90a1ad",
    "button_bg": "#11191f",
    "button_border": "#2b3d48",
    "button_fg": "#edf3f7",
    "button_hover_bg": "#18242c",
    "button_pressed_bg": "#0c1217",
    "button_disabled_fg": "#6f7f89",
    "button_disabled_border": "#22313a",
    "button_disabled_bg": "#0f1519",
    "input_bg": "#10171c",
    "input_border": "#31424d",
    "input_fg": "#edf3f7",
    "status_strip_bg": "#131c22",
    "status_strip_border": "#2a3943",
    "status_separator": "#31424d",
    "status_item_idle_bar": "#4f6270",
    "status_title_fg": "#8ea0ad",
    "metric_active_fg": "#45d0bc",
    "metric_warning_fg": "#e4b86f",
    "metric_idle_fg": "#c8d2da",
    "progress_chunk": "#45d0bc",
    "scroll_bg": "#121a20",
}

LIGHT_THEME = {
    "window_bg": "#f2ede5",
    "window_fg": "#2c3942",
    "panel_bg": "#fffdf9",
    "panel_border": "#d7cec1",
    "summary_bg": "#fcf9f3",
    "summary_border": "#ddd4c8",
    "summary_title_fg": "#2d3940",
    "muted_fg": "#7c7368",
    "button_bg": "#f8f3eb",
    "button_border": "#d9d0c3",
    "button_fg": "#2c3942",
    "button_hover_bg": "#efe6d9",
    "button_pressed_bg": "#e3d8c8",
    "button_disabled_fg": "#91897e",
    "button_disabled_border": "#ddd4c8",
    "button_disabled_bg": "#f1ece4",
    "input_bg": "#fffdf9",
    "input_border": "#d9d0c3",
    "input_fg": "#2c3942",
    "status_strip_bg": "#f7f1e8",
    "status_strip_border": "#ddd2c4",
    "status_separator": "#ddd4c7",
    "status_item_idle_bar": "#c8bfb3",
    "status_title_fg": "#7c7368",
    "metric_active_fg": "#2d7f6d",
    "metric_warning_fg": "#a97118",
    "metric_idle_fg": "#4e5a62",
    "progress_chunk": "#2f9aad",
    "scroll_bg": "#f6f1e8",
}


def build_orbit_correct_theme(palette):
    theme_values = dict(palette, header_action_height=HEADER_ACTION_HEIGHT)
    return """
QWidget {{
    background-color: {window_bg};
    color: {window_fg};
    font-family: "IBM Plex Sans", "Source Han Sans SC", "Segoe UI", sans-serif;
}}

QFrame#summaryPanel {{
    background-color: {summary_bg};
    border: 1px solid {summary_border};
    border-radius: 14px;
}}

QFrame#sectionCard, QFrame#toolbarPanel, QFrame#commandPane {{
    background-color: {panel_bg};
    border: 1px solid {panel_border};
    border-radius: 14px;
}}

QFrame#subCard {{
    background-color: {status_strip_bg};
    border: 1px solid {status_strip_border};
    border-radius: 12px;
}}

QFrame#parameterGroup {{
    background-color: transparent;
    border: none;
}}

QFrame#targetToolbar {{
    background-color: {status_strip_bg};
    border: 1px solid {status_strip_border};
    border-radius: 12px;
}}

QTabWidget::pane {{
    border-left: 1px solid {panel_border};
    border-right: 1px solid {panel_border};
    border-bottom: 1px solid {panel_border};
    border-radius: 14px;
    background: {panel_bg};
    top: -1px;
}}

QTabBar::base {{
    border: none;
    background: transparent;
    height: 0px;
}}

QTabBar::tab {{
    background: {button_bg};
    border: 1px solid {button_border};
    color: {button_fg};
    min-width: 116px;
    padding: 8px 14px;
    margin-right: 6px;
    border-top-left-radius: 10px;
    border-top-right-radius: 10px;
    font-size: 12px;
    font-weight: 700;
}}

QTabBar::tab:selected {{
    background: {panel_bg};
    color: {summary_title_fg};
    border-bottom-color: {panel_bg};
}}

QTabBar::tab:hover:!selected {{
    background: {button_hover_bg};
}}

QLabel#summaryTitle {{
    color: {summary_title_fg};
    font-size: 22px;
    font-weight: 700;
    letter-spacing: 0.3px;
}}

QLabel#panelTitle {{
    color: {summary_title_fg};
    font-size: 15px;
    font-weight: 700;
}}

QLabel#subTitle {{
    color: {summary_title_fg};
    font-size: 13px;
    font-weight: 700;
    background: transparent;
    border: none;
}}

QLabel[role="field"] {{
    color: {muted_fg};
    font-size: 11px;
    font-weight: 600;
    background: transparent;
    border: none;
}}

QLabel {{
    color: {window_fg};
    font-size: 12px;
    font-weight: 600;
    background: transparent;
    border: none;
}}

QPushButton {{
    background-color: {button_bg};
    border: 1px solid {button_border};
    border-radius: 12px;
    color: {button_fg};
    padding: 6px 12px;
    min-height: 32px;
    font-size: 12px;
    font-weight: 700;
}}

QPushButton:hover {{
    background-color: {button_hover_bg};
}}

QPushButton:pressed {{
    background-color: {button_pressed_bg};
}}

QPushButton[compact="true"] {{
    padding: 3px 10px;
    min-height: 22px;
    font-size: 11px;
}}

QPushButton[primary="true"] {{
    background-color: {metric_active_fg};
    border-color: {metric_active_fg};
    color: {window_bg};
}}

QPushButton[primary="true"]:hover {{
    background-color: {metric_active_fg};
}}

QPushButton[danger="true"] {{
    color: {metric_warning_fg};
    border-color: {metric_warning_fg};
}}

QLineEdit, QComboBox, QDoubleSpinBox {{
    background-color: {input_bg};
    border: 1px solid {input_border};
    border-radius: 10px;
    color: {input_fg};
    padding: 5px 10px;
    min-height: 16px;
    selection-background-color: {metric_active_fg};
}}

QLineEdit:disabled, QComboBox:disabled, QDoubleSpinBox:disabled {{
    background-color: {button_disabled_bg};
    border-color: {button_disabled_border};
    color: {button_disabled_fg};
}}

QPushButton:disabled {{
    background-color: {button_disabled_bg};
    border-color: {button_disabled_border};
    color: {button_disabled_fg};
}}

QLabel:disabled {{
    color: {button_disabled_fg};
}}

QComboBox::drop-down, QDoubleSpinBox::drop-down {{
    border: none;
    width: 20px;
}}

QComboBox QAbstractItemView {{
    background-color: {input_bg};
    color: {input_fg};
    border: 1px solid {input_border};
    selection-background-color: {button_hover_bg};
}}

QCheckBox {{
    background: transparent;
    border: none;
    spacing: 8px;
    font-size: 12px;
    font-weight: 600;
}}

QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {panel_border};
    border-radius: 4px;
    background-color: {input_bg};
}}

QCheckBox::indicator:checked {{
    background-color: {metric_active_fg};
    border: 1px solid {metric_active_fg};
}}

QProgressBar {{
    background-color: {input_bg};
    border: 1px solid {input_border};
    border-radius: 10px;
    color: {summary_title_fg};
    text-align: center;
    min-height: 20px;
}}

QProgressBar::chunk {{
    background-color: {progress_chunk};
    border-radius: 8px;
}}

QScrollArea#targetsScroll {{
    border: 1px solid {panel_border};
    border-radius: 12px;
    background-color: {scroll_bg};
}}

QWidget#targetsContent {{
    background-color: {panel_bg};
}}

QToolButton#themeToggleButton {{
    background-color: {button_bg};
    border: 1px solid {button_border};
    border-radius: 11px;
    color: {button_fg};
    min-width: {header_action_height}px;
    max-width: {header_action_height}px;
    min-height: {header_action_height}px;
    max-height: {header_action_height}px;
    font-size: 14px;
    font-weight: 700;
}}

QToolButton#themeToggleButton:hover {{
    background-color: {button_hover_bg};
}}

QToolButton#themeToggleButton:pressed {{
    background-color: {button_pressed_bg};
}}
""".format_map(theme_values)


def build_status_strip_theme(palette):
    theme_values = dict(
        palette,
        status_tone_success_bar=palette["metric_active_fg"],
        status_tone_warning_bar=palette["metric_warning_fg"],
        status_tone_success_fg=palette["metric_active_fg"],
        status_tone_warning_fg=palette["metric_warning_fg"],
        status_tone_subtle_fg=palette["metric_idle_fg"],
    )
    return """
QWidget#statusStrip {{
    background: {status_strip_bg};
    border: 1px solid {status_strip_border};
    border-radius: 10px;
}}
QFrame#statusItem {{
    background: transparent;
    border: none;
    border-left: 4px solid {status_item_idle_bar};
    border-radius: 0px;
}}
QFrame#statusItem[tone="success"] {{
    border-left-color: {status_tone_success_bar};
}}
QFrame#statusItem[tone="warning"] {{
    border-left-color: {status_tone_warning_bar};
}}
QFrame#statusSeparator {{
    background: {status_separator};
    min-width: 1px;
    max-width: 1px;
    border: none;
}}
QLabel[role="title"] {{
    color: {status_title_fg};
    background: transparent;
    border: none;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.8px;
}}
QLabel[role="value"][tone="subtle"] {{
    color: {status_tone_subtle_fg};
    background: transparent;
    border: none;
    font-size: 13px;
    font-weight: 700;
}}
QLabel[role="value"][tone="success"] {{
    color: {status_tone_success_fg};
    background: transparent;
    border: none;
    font-size: 13px;
    font-weight: 700;
}}
QLabel[role="value"][tone="warning"] {{
    color: {status_tone_warning_fg};
    background: transparent;
    border: none;
    font-size: 13px;
    font-weight: 700;
}}
""".format_map(theme_values)


class OrbitStatusStrip(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = {}
        self.setObjectName("statusStrip")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(0)
        self._layout = layout

    def add_item(self, key, title, value):
        if self._items:
            separator = QFrame(self)
            separator.setObjectName("statusSeparator")
            separator.setFrameShape(QFrame.VLine)
            self._layout.addWidget(separator)

        container = QFrame(self)
        container.setObjectName("statusItem")
        container.setProperty("tone", "subtle")
        container.setMinimumWidth(112)

        inner = QVBoxLayout(container)
        inner.setContentsMargins(8, 0, 6, 0)
        inner.setSpacing(2)

        title_label = QLabel(title, container)
        title_label.setProperty("role", "title")
        value_label = QLabel(value, container)
        value_label.setProperty("role", "value")
        value_label.setProperty("tone", "subtle")
        value_label.setWordWrap(True)

        inner.addWidget(title_label)
        inner.addWidget(value_label)
        self._layout.addWidget(container)
        self._items[key] = (container, value_label)

    def finish(self):
        self._layout.addStretch(1)

    def apply_theme(self, palette):
        self.setStyleSheet(build_status_strip_theme(palette))
        for container, value_label in self._items.values():
            self._refresh_tone(container, value_label)

    def set_item(self, key, text, tone="subtle"):
        item = self._items.get(key)
        if item is None:
            return
        container, value_label = item
        container.setProperty("tone", tone)
        value_label.setProperty("tone", tone)
        value_label.setText(text)
        self._refresh_tone(container, value_label)

    @staticmethod
    def _refresh_tone(container, value_label):
        container.style().unpolish(container)
        container.style().polish(container)
        value_label.style().unpolish(value_label)
        value_label.style().polish(value_label)
        container.update()
        value_label.update()

class myWindow(QMainWindow, Ui_MainWindow):
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        install_qt_window_raise_handler(self)
        self.app_context = load_app_context("orbit_correct")
        self.machine_profile = self.app_context.profile
        self.orbit_workflow = self.app_context.orbit_workflow
        self.orbit_runtime = load_orbit_runtime_settings(self.app_context)
        self.runtime_defaults = self.orbit_runtime["runtime_defaults"]
        self.response_progress_path = Path(self.orbit_runtime["response_progress_path"])
        self.current_theme = "dark"
        self.last_notice = "Idle"
        self.process_manager = ManagedProcessGroup(notify=self._notify)
        self.process_manager.install_signal_handlers()
        self._response_scan_was_running = False

        self.all_checkboxes = []
        self._bpmx_spinboxes = []
        self._bpmy_spinboxes = []
        self.global_xcor_checkboxes = []
        self.global_ycor_checkboxes = []
        self._configure_window()
        self._configure_machine_profile()
        self._clear_inline_styles()
        self._build_shell()
        self._configure_form_content()

        # connect button
        self.pushButton.clicked.connect(self.measure_res)
        self.stop_response_button.clicked.connect(self.stop_measure_res)
        self.pushButton_4.clicked.connect(self.start_cor)
        self.pushButton_2.clicked.connect(self.cor_off)
        self.pushButton_3.clicked.connect(self.stop_cor)
        self.pushButton_7.clicked.connect(self.cor_recover)

        # other button
        self.pushButton_5.clicked.connect(self.selectall)
        self.pushButton_6.clicked.connect(self.cancelall)
        self.load_response_matrix_button.clicked.connect(self.load_response_matrix)

        self.comboBox.currentIndexChanged.connect(self._on_correction_method_changed)
        self.localResponseSourceComboBox.currentIndexChanged.connect(
            self._on_local_response_source_changed
        )
        self.tabWidget.currentChanged.connect(self._refresh_status)
        for cb in self.all_checkboxes:
            cb.stateChanged.connect(self._refresh_status)
        for cb in self.global_xcor_checkboxes + self.global_ycor_checkboxes:
            cb.stateChanged.connect(self._refresh_status)

        # initial parameters
        self._apply_default_method()
        self._apply_default_local_response_source()
        self.samplingIntervalSLineEdit.setText(f"{float(self.runtime_defaults['sampling_interval_s']):g}")
        self.correctionSettleSLineEdit.setText(
            f"{float(self.orbit_runtime['correction_settle_s']):g}"
        )
        self.correctorAccuracyUmLineEdit.setText(f"{float(self.runtime_defaults['accuracy_um']):g}")
        self.sampPerStepLineEdit.setText(str(int(self.runtime_defaults["samples_per_step"])))
        corrector_limit = float(self.orbit_runtime["corrector_upperlimit"])
        self.correctorLimitLineEdit.setText(f"{corrector_limit:.6g}")
        self.globalMaxIterLineEdit.setText(str(int(self.runtime_defaults["global_max_iter"])))
        self.oneToOneMaxIterLineEdit.setText(str(int(self.runtime_defaults["one_to_one_max_iter"])))
        self.correctionGainLineEdit.setText(f"{float(self.runtime_defaults['correction_gain']):g}")
        self.correctionMaxStepLineEdit.setText(f"{float(self.runtime_defaults['correction_max_step_pct']):g}")
        self.responseKickLineEdit.setText(
            f"{corrector_limit * float(self.runtime_defaults['local_response_kick_fraction']):.6g}"
        )
        self.matrixResponseKickLineEdit.setText(
            f"{corrector_limit * float(self.runtime_defaults['matrix_response_kick_fraction']):.6g}"
        )
        self.matrixWaitSLineEdit.setText(f"{float(self.orbit_runtime['response_wait_s']):.6g}")
        self.matrixSampleIntervalSLineEdit.setText(
            f"{float(self.orbit_runtime['response_sample_interval_s']):.6g}"
        )
        self.matrixSamplesLineEdit.setText(str(int(self.runtime_defaults["matrix_samples_per_step"])))

        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self._refresh_status)
        self.status_timer.start(700)

        self.refresh_response_matrices()
        self._apply_theme()
        self._update_method_parameter_state()
        self._refresh_status()

    def _configure_window(self):
        self.setWindowTitle(f"{self.machine_profile.machine.display_name} Orbit Correction")
        self.resize(1240, 1000)
        self.setMinimumSize(1100, 820)

    def _clear_inline_styles(self):
        widget_types = (
            QLabel,
            QPushButton,
            QLineEdit,
            QComboBox,
            QDoubleSpinBox,
            QTabWidget,
            QCheckBox,
            QProgressBar,
            QScrollArea,
        )
        for widget_type in widget_types:
            for widget in self.findChildren(widget_type):
                widget.setStyleSheet("")

    @staticmethod
    def _extract_widget_index(widget):
        match = re.search(r"(\d+)$", widget.objectName())
        return int(match.group(1)) if match else 0

    def _append_target_bpm_row(self, index):
        row = self.gridLayout_2.rowCount()
        checkbox = QCheckBox(self.scrollAreaWidgetContents_2)
        checkbox.setObjectName(f"checkBox_{index:02d}_dynamic")

        bpmx_widget = QDoubleSpinBox(self.scrollAreaWidgetContents_2)
        bpmx_widget.setObjectName(f"bpmx_doubleSpinBox_{index:02d}_dynamic")
        bpmx_widget.setDecimals(2)
        bpmx_widget.setMinimum(-99.99)
        bpmx_widget.setMaximum(99.99)
        bpmx_widget.setSingleStep(0.1)

        bpmy_widget = QDoubleSpinBox(self.scrollAreaWidgetContents_2)
        bpmy_widget.setObjectName(f"bpmy_doubleSpinBox_{index:02d}_dynamic")
        bpmy_widget.setDecimals(2)
        bpmy_widget.setMinimum(-99.99)
        bpmy_widget.setMaximum(99.99)
        bpmy_widget.setSingleStep(0.1)

        self.gridLayout_2.addWidget(checkbox, row, 0)
        self.gridLayout_2.addWidget(bpmx_widget, row, 1)
        self.gridLayout_2.addWidget(bpmy_widget, row, 2)

    def _configure_machine_profile(self):
        if self.orbit_workflow is None:
            raise ValueError("Orbit workflow is not available in the current app context.")
        orbit_bpms = self.orbit_workflow.bpms
        checkbox_widgets = sorted(
            self.findChildren(QCheckBox),
            key=self._extract_widget_index,
        )
        bpmx_widgets = sorted(
            self.findChildren(QDoubleSpinBox, QRegExp("bpmx_.*")),
            key=self._extract_widget_index,
        )
        bpmy_widgets = sorted(
            self.findChildren(QDoubleSpinBox, QRegExp("bpmy_.*")),
            key=self._extract_widget_index,
        )

        available = min(len(checkbox_widgets), len(bpmx_widgets), len(bpmy_widgets))
        while len(orbit_bpms) > available:
            self._append_target_bpm_row(available + 1)
            checkbox_widgets = sorted(
                self.findChildren(QCheckBox),
                key=self._extract_widget_index,
            )
            bpmx_widgets = sorted(
                self.findChildren(QDoubleSpinBox, QRegExp("bpmx_.*")),
                key=self._extract_widget_index,
            )
            bpmy_widgets = sorted(
                self.findChildren(QDoubleSpinBox, QRegExp("bpmy_.*")),
                key=self._extract_widget_index,
            )
            available = min(len(checkbox_widgets), len(bpmx_widgets), len(bpmy_widgets))

        self.all_checkboxes = checkbox_widgets[: len(orbit_bpms)]
        self._bpmx_spinboxes = bpmx_widgets[: len(orbit_bpms)]
        self._bpmy_spinboxes = bpmy_widgets[: len(orbit_bpms)]
        default_target_bpms = set(self.orbit_workflow.default_target_bpms)

        for bpm_name, checkbox in zip(orbit_bpms, self.all_checkboxes):
            checkbox.setText(bpm_name)
            if default_target_bpms:
                checkbox.setChecked(bpm_name in default_target_bpms)
            else:
                checkbox.setChecked(True)
            checkbox.show()
            checkbox.setEnabled(True)

        for extra_widget in checkbox_widgets[len(orbit_bpms) :]:
            extra_widget.setChecked(False)
            extra_widget.hide()
            extra_widget.setEnabled(False)

        for extra_widget in bpmx_widgets[len(orbit_bpms) :] + bpmy_widgets[len(orbit_bpms) :]:
            extra_widget.hide()
            extra_widget.setEnabled(False)

    def _build_shell(self):
        self.verticalLayout_2.setContentsMargins(10, 10, 10, 10)
        self.verticalLayout_2.setSpacing(12)
        self.horizontalLayout_9.setContentsMargins(0, 0, 0, 0)
        self.horizontalLayout_9.setSpacing(14)

        self._build_summary_panel()
        self._wrap_main_sections()
        self._build_tab_layouts()

    def _build_summary_panel(self):
        panel = QFrame(self.centralwidget)
        panel.setObjectName("summaryPanel")
        panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        outer_layout = QVBoxLayout(panel)
        outer_layout.setContentsMargins(12, 10, 12, 10)
        outer_layout.setSpacing(6)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(12)

        title = QLabel("Orbit Correction", panel)
        title.setObjectName("summaryTitle")
        header_layout.addWidget(title)
        header_layout.addStretch(1)

        for text in (
            f"Machine: {self.machine_profile.machine.display_name}",
            f"Backend: {self._format_header_backend_name()}",
        ):
            runtime_label = QLabel(text, panel)
            runtime_label.setProperty("role", "field")
            runtime_label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            header_layout.addWidget(runtime_label)

        self.theme_toggle_button = QToolButton(panel)
        self.theme_toggle_button.setObjectName("themeToggleButton")
        self.theme_toggle_button.setFixedSize(HEADER_ACTION_HEIGHT, HEADER_ACTION_HEIGHT)
        self.theme_toggle_button.clicked.connect(self._toggle_theme)
        header_layout.addWidget(self.theme_toggle_button)
        outer_layout.addLayout(header_layout)

        self.status_panel = OrbitStatusStrip(panel)
        self.status_panel.add_item("tab", "Tab", "Run Correct")
        self.status_panel.add_item("method", "Method", self.comboBox.currentText())
        self.status_panel.add_item("targets", "Targets", "0/0")
        self.status_panel.add_item("backend", "Backend", self.app_context.control_backend.name)
        self.status_panel.add_item("process", "Process", "Idle")
        self.status_panel.finish()
        self.status_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        outer_layout.addWidget(self.status_panel)

        self.verticalLayout_2.insertWidget(0, panel)

    def _format_header_backend_name(self):
        backend_name = self.app_context.control_backend.name
        return "VM" if backend_name == "vm" else backend_name

    def _wrap_main_sections(self):
        self.horizontalLayout_9.removeItem(self.verticalLayout_4)
        self.horizontalLayout_9.removeItem(self.verticalLayout_5)

        self.left_panel = QFrame(self.centralwidget)
        self.left_panel.setObjectName("sectionCard")
        self.left_panel.setLayout(self.verticalLayout_4)
        self.left_panel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.verticalLayout_4.setContentsMargins(12, 12, 12, 12)
        self.verticalLayout_4.setSpacing(12)
        self.verticalLayout_4.insertWidget(0, self._make_panel_title("Correction Setup", self.left_panel))

        self.right_panel = QFrame(self.centralwidget)
        self.right_panel.setObjectName("sectionCard")
        self.right_panel.setLayout(self.verticalLayout_5)
        self.right_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.verticalLayout_5.setContentsMargins(12, 12, 12, 12)
        self.verticalLayout_5.setSpacing(12)
        self.verticalLayout_5.insertWidget(0, self._make_panel_title("Selection", self.right_panel))

        self.horizontalLayout_9.addWidget(self.left_panel, 2)
        self.horizontalLayout_9.addWidget(self.right_panel, 3)

        self.tabWidget.setDocumentMode(False)
        self.tabWidget.tabBar().setDrawBase(False)
        self.tabWidget.setElideMode(False)

        self.scrollArea.setObjectName("targetsScroll")
        self.scrollAreaWidgetContents_2.setObjectName("targetsContent")
        self.gridLayout_3.setContentsMargins(0, 0, 0, 0)
        self.gridLayout_3.removeItem(self.gridLayout_2)
        self.gridLayout_3.addLayout(self.gridLayout_2, 0, 0, 1, 1, Qt.AlignTop)
        self.gridLayout_3.setRowStretch(0, 0)
        self.gridLayout_3.setRowStretch(1, 1)
        self._build_selection_tabs()

    def _build_tab_layouts(self):
        self.gridLayout_4.setHorizontalSpacing(10)
        self.gridLayout_4.setVerticalSpacing(8)
        self.gridLayout_5.setHorizontalSpacing(10)
        self.gridLayout_5.setVerticalSpacing(8)
        self.gridLayout_2.setHorizontalSpacing(10)
        self.gridLayout_2.setVerticalSpacing(6)
        self.gridLayout_2.setContentsMargins(8, 8, 8, 8)
        self.gridLayout.setHorizontalSpacing(0)
        self.gridLayout.setVerticalSpacing(10)

        self.horizontalLayout.setContentsMargins(0, 0, 0, 0)
        self.horizontalLayout.setSpacing(0)
        self.horizontalLayout.removeItem(self.gridLayout)
        self.command_pane = QFrame(self.tab)
        self.command_pane.setObjectName("commandPane")
        command_layout = QVBoxLayout(self.command_pane)
        command_layout.setContentsMargins(14, 14, 14, 14)
        command_layout.setSpacing(12)
        command_layout.addWidget(self._build_correction_parameters_card())
        command_layout.addWidget(self._build_correction_actions_card())
        command_layout.addStretch(1)
        self.horizontalLayout.addWidget(self.command_pane)

        self.response_pane = QFrame(self.tab_2)
        self.response_pane.setObjectName("commandPane")
        response_outer = QVBoxLayout(self.tab_2)
        response_outer.setContentsMargins(0, 0, 0, 0)
        response_outer.setSpacing(0)
        response_outer.addWidget(self.response_pane)
        response_layout = QVBoxLayout(self.response_pane)
        response_layout.setContentsMargins(14, 14, 14, 14)
        response_layout.setSpacing(12)
        response_layout.addWidget(self._build_matrix_library_card())
        response_layout.addWidget(self._build_matrix_measure_card())
        response_layout.addStretch(2)

    def _build_selection_tabs(self):
        self.verticalLayout_5.removeItem(self.gridLayout_5)
        for widget in (
            self.pushButton_5,
            self.pushButton_6,
            self.label_45,
            self.label_46,
            self.progressBar,
        ):
            self.gridLayout_5.removeWidget(widget)

        self.verticalLayout_5.removeWidget(self.scrollArea)

        self.selection_tabs = QTabWidget(self.right_panel)
        self.selection_tabs.setDocumentMode(False)
        self.selection_tabs.tabBar().setDrawBase(False)
        self.selection_tabs.setElideMode(False)
        self.selection_tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.target_bpm_tab = QWidget(self.selection_tabs)
        target_layout = QVBoxLayout(self.target_bpm_tab)
        target_layout.setContentsMargins(8, 8, 8, 8)
        target_layout.setSpacing(10)

        toolbar = QFrame(self.target_bpm_tab)
        toolbar.setObjectName("targetToolbar")
        toolbar_layout = QGridLayout(toolbar)
        toolbar_layout.setContentsMargins(12, 10, 12, 10)
        toolbar_layout.setHorizontalSpacing(10)
        toolbar_layout.setVerticalSpacing(8)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(8)
        action_row.addWidget(self.pushButton_5)
        action_row.addWidget(self.pushButton_6)
        action_row.addStretch(1)

        toolbar_layout.addLayout(action_row, 0, 0)
        toolbar_layout.addWidget(self.label_45, 0, 1)
        toolbar_layout.addWidget(self.label_46, 0, 2)
        toolbar_layout.addWidget(self.progressBar, 0, 3)
        toolbar_layout.setColumnStretch(0, 2)
        toolbar_layout.setColumnStretch(1, 2)
        toolbar_layout.setColumnStretch(2, 2)
        toolbar_layout.setColumnStretch(3, 1)

        self.target_toolbar = toolbar
        target_layout.addWidget(toolbar)
        target_layout.addWidget(self.scrollArea, 1)

        self.global_corrector_tab = QWidget(self.selection_tabs)
        corrector_layout = QVBoxLayout(self.global_corrector_tab)
        corrector_layout.setContentsMargins(8, 8, 8, 8)
        corrector_layout.setSpacing(10)
        corrector_layout.addWidget(
            self._build_global_corrector_selector(self.global_corrector_tab),
            0,
            Qt.AlignTop,
        )
        corrector_layout.addStretch(1)

        self.selection_tabs.addTab(self.target_bpm_tab, "Target BPMs")
        self.selection_tabs.addTab(self.global_corrector_tab, "Global Correctors")
        self.selection_tabs.currentChanged.connect(self._refresh_status)
        self.verticalLayout_5.addWidget(self.selection_tabs, 1)

    def _build_correction_parameters_card(self):
        card, layout = self._make_subcard(None, self.command_pane)
        self.correctorLimitLabel, self.correctorLimitLineEdit = self._make_parameter_field(card)
        self.correctionSettleSLabel, self.correctionSettleSLineEdit = self._make_parameter_field(card)
        self.globalMaxIterLabel, self.globalMaxIterLineEdit = self._make_parameter_field(card)
        self.oneToOneMaxIterLabel, self.oneToOneMaxIterLineEdit = self._make_parameter_field(card)
        self.correctionGainLabel, self.correctionGainLineEdit = self._make_parameter_field(card)
        self.correctionMaxStepLabel, self.correctionMaxStepLineEdit = self._make_parameter_field(card)
        self.responseKickLabel, self.responseKickLineEdit = self._make_parameter_field(card)
        self.localResponseSourceLabel = QLabel(card)
        self.localResponseSourceComboBox = QComboBox(card)
        self.localResponseSourceComboBox.addItem("Measure Live", "measure_live")
        self.localResponseSourceComboBox.addItem("Active Matrix", "active_matrix")
        self.activeMatrixLabel, self.activeMatrixValueLabel = self._make_parameter_display(card)
        self.matrixSetupLabel = QLabel(card)
        self.openResponseMatrixTabButton = QPushButton(card)
        self.globalCorrectorsLabel, self.globalCorrectorsValueLabel = self._make_parameter_display(card)
        self.correctorSetupLabel = QLabel(card)
        self.editCorrectorsButton = QPushButton(card)

        self.commonRuntimeGroup = self._build_parameter_group(
            "Common Runtime",
            (
                (self.label_6, self.comboBox, True),
                (self.samplingIntervalSLabel, self.samplingIntervalSLineEdit, True),
                (self.correctionSettleSLabel, self.correctionSettleSLineEdit, False),
                (self.correctorAccuracyUmLabel, self.correctorAccuracyUmLineEdit, True),
                (self.sampPerStepLabel, self.sampPerStepLineEdit, True),
                (self.correctorLimitLabel, self.correctorLimitLineEdit, False),
                (self.correctionGainLabel, self.correctionGainLineEdit, False),
                (self.correctionMaxStepLabel, self.correctionMaxStepLineEdit, False),
            ),
            card,
        )
        layout.addWidget(self.commonRuntimeGroup)

        self.globalCorrectionGroup = self._build_parameter_group(
            "Global Correction",
            (
                (self.globalMaxIterLabel, self.globalMaxIterLineEdit, False),
                (self.activeMatrixLabel, self.activeMatrixValueLabel, False),
                (self.matrixSetupLabel, self.openResponseMatrixTabButton, False),
                (self.globalCorrectorsLabel, self.globalCorrectorsValueLabel, False),
                (self.correctorSetupLabel, self.editCorrectorsButton, False),
            ),
            card,
        )
        layout.addWidget(self.globalCorrectionGroup)

        self.oneToOneCorrectionGroup = self._build_parameter_group(
            "One-to-One Correction",
            (
                (self.oneToOneMaxIterLabel, self.oneToOneMaxIterLineEdit, False),
                (self.localResponseSourceLabel, self.localResponseSourceComboBox, False),
                (self.responseKickLabel, self.responseKickLineEdit, False),
            ),
            card,
        )
        layout.addWidget(self.oneToOneCorrectionGroup)

        return card

    def _make_parameter_field(self, parent):
        label = QLabel(parent)
        editor = QLineEdit(parent)
        label.setProperty("role", "field")
        return label, editor

    def _make_parameter_display(self, parent):
        label = QLabel(parent)
        value = QLabel("--", parent)
        label.setProperty("role", "field")
        value.setProperty("role", "field")
        value.setWordWrap(True)
        return label, value

    def _build_parameter_group(self, title, rows, parent):
        group = QFrame(parent)
        group.setObjectName("parameterGroup")
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(10, 10, 10, 10)
        group_layout.setSpacing(8)

        if title:
            title_label = QLabel(title, group)
            title_label.setObjectName("subTitle")
            group_layout.addWidget(title_label)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        for row, (label, widget, from_legacy_grid) in enumerate(rows):
            if from_legacy_grid:
                self.gridLayout.removeWidget(label)
                self.gridLayout.removeWidget(widget)
            grid.addWidget(label, row, 0)
            grid.addWidget(widget, row, 1)
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 1)
        group_layout.addLayout(grid)
        return group

    def _build_global_corrector_selector(self, parent):
        container = QFrame(parent)
        container.setObjectName("correctorSelector")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        toolbar = QFrame(container)
        toolbar.setObjectName("targetToolbar")
        toolbar_layout = QGridLayout(toolbar)
        toolbar_layout.setContentsMargins(12, 10, 12, 10)
        toolbar_layout.setHorizontalSpacing(10)
        toolbar_layout.setVerticalSpacing(8)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(8)
        all_button = QPushButton("All Correctors", container)
        clear_button = QPushButton("Clear Correctors", container)
        for button in (all_button, clear_button):
            button.setProperty("compact", True)
            button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            action_row.addWidget(button)
        action_row.addStretch(1)

        x_header = QLabel("X Correctors", toolbar)
        y_header = QLabel("Y Correctors", toolbar)
        for header in (x_header, y_header):
            header.setProperty("role", "field")
            header.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.global_corrector_progress = QProgressBar(toolbar)
        self.global_corrector_progress.setTextVisible(True)
        self.global_corrector_progress.setFormat("%v/%m")

        toolbar_layout.addLayout(action_row, 0, 0)
        toolbar_layout.addWidget(x_header, 0, 1)
        toolbar_layout.addWidget(y_header, 0, 2)
        toolbar_layout.addWidget(self.global_corrector_progress, 0, 3)
        toolbar_layout.setColumnStretch(0, 2)
        toolbar_layout.setColumnStretch(1, 2)
        toolbar_layout.setColumnStretch(2, 2)
        toolbar_layout.setColumnStretch(3, 1)
        layout.addWidget(toolbar)

        scroll = QScrollArea(container)
        scroll.setObjectName("targetsScroll")
        scroll.setWidgetResizable(True)
        content = QWidget(scroll)
        content.setObjectName("targetsContent")
        grid = QGridLayout(content)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        grid.setAlignment(Qt.AlignTop)
        self._populate_corrector_columns(grid)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        all_button.clicked.connect(lambda: self._set_global_correctors_checked(True))
        clear_button.clicked.connect(lambda: self._set_global_correctors_checked(False))
        return container

    def _populate_corrector_columns(self, grid):
        row_count = max(len(self.orbit_workflow.xcors), len(self.orbit_workflow.ycors))
        for row in range(row_count):
            if row < len(self.orbit_workflow.xcors):
                checkbox = QCheckBox(self.orbit_workflow.xcors[row], grid.parentWidget())
                checkbox.setChecked(True)
                self.global_xcor_checkboxes.append(checkbox)
                grid.addWidget(checkbox, row, 0)

            if row < len(self.orbit_workflow.ycors):
                checkbox = QCheckBox(self.orbit_workflow.ycors[row], grid.parentWidget())
                checkbox.setChecked(True)
                self.global_ycor_checkboxes.append(checkbox)
                grid.addWidget(checkbox, row, 1)

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

    def _build_correction_actions_card(self):
        card, layout = self._make_subcard(None, self.command_pane)

        primary_row = QHBoxLayout()
        primary_row.setContentsMargins(0, 0, 0, 0)
        primary_row.setSpacing(10)
        for button in (self.pushButton_4, self.pushButton_3):
            self.gridLayout.removeWidget(button)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            primary_row.addWidget(button)
        layout.addLayout(primary_row)

        utility_row = QHBoxLayout()
        utility_row.setContentsMargins(0, 0, 0, 0)
        utility_row.setSpacing(10)
        for button in (self.pushButton_2, self.pushButton_7):
            self.gridLayout.removeWidget(button)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            utility_row.addWidget(button)
        layout.addLayout(utility_row)

        return card

    def _build_matrix_library_card(self):
        card, layout = self._make_subcard("Matrix Library", self.response_pane)
        self.response_matrix_combo = QComboBox(card)
        self.response_matrix_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(self.response_matrix_combo)

        matrix_action_row = QHBoxLayout()
        matrix_action_row.setContentsMargins(0, 0, 0, 0)
        matrix_action_row.setSpacing(10)
        self.load_response_matrix_button = QPushButton("Load Selected Matrix", card)
        for button in (self.load_response_matrix_button,):
            button.setProperty("compact", True)
            button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            matrix_action_row.addWidget(button)
        matrix_action_row.addStretch(1)
        layout.addLayout(matrix_action_row)
        return card

    def _build_matrix_measure_card(self):
        card, layout = self._make_subcard("Matrix Measurement", self.response_pane)
        self.matrixResponseKickLabel, self.matrixResponseKickLineEdit = self._make_parameter_field(card)
        self.matrixWaitSLabel, self.matrixWaitSLineEdit = self._make_parameter_field(card)
        self.matrixSampleIntervalSLabel, self.matrixSampleIntervalSLineEdit = self._make_parameter_field(card)
        self.matrixSamplesLabel, self.matrixSamplesLineEdit = self._make_parameter_field(card)
        layout.addWidget(
            self._build_parameter_group(
                None,
                (
                    (self.matrixResponseKickLabel, self.matrixResponseKickLineEdit, False),
                    (self.matrixWaitSLabel, self.matrixWaitSLineEdit, False),
                    (self.matrixSampleIntervalSLabel, self.matrixSampleIntervalSLineEdit, False),
                    (self.matrixSamplesLabel, self.matrixSamplesLineEdit, False),
                ),
                card,
            )
        )
        measure_row = QHBoxLayout()
        measure_row.setContentsMargins(0, 0, 0, 0)
        measure_row.setSpacing(10)
        self.pushButton.setParent(card)
        self.pushButton.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        measure_row.addWidget(self.pushButton, 0)
        self.stop_response_button = QPushButton(card)
        self.stop_response_button.setProperty("compact", True)
        self.stop_response_button.setProperty("danger", True)
        self.stop_response_button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        measure_row.addWidget(self.stop_response_button, 0)
        self.response_matrix_progress = QProgressBar(card)
        self.response_matrix_progress.setRange(0, 100)
        self.response_matrix_progress.setValue(0)
        self.response_matrix_progress.setTextVisible(True)
        self.response_matrix_progress.setFormat("Idle")
        self.response_matrix_progress.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        measure_row.addWidget(self.response_matrix_progress, 1)
        layout.addLayout(measure_row)
        return card

    def _configure_form_content(self):
        self.label_6.setProperty("role", "field")
        self.samplingIntervalSLabel.setProperty("role", "field")
        self.correctionSettleSLabel.setProperty("role", "field")
        self.correctorAccuracyUmLabel.setProperty("role", "field")
        self.sampPerStepLabel.setProperty("role", "field")
        self.correctorLimitLabel.setProperty("role", "field")
        self.globalMaxIterLabel.setProperty("role", "field")
        self.oneToOneMaxIterLabel.setProperty("role", "field")
        self.correctionGainLabel.setProperty("role", "field")
        self.correctionMaxStepLabel.setProperty("role", "field")
        self.responseKickLabel.setProperty("role", "field")
        self.localResponseSourceLabel.setProperty("role", "field")
        self.matrixResponseKickLabel.setProperty("role", "field")
        self.matrixWaitSLabel.setProperty("role", "field")
        self.matrixSampleIntervalSLabel.setProperty("role", "field")
        self.matrixSamplesLabel.setProperty("role", "field")
        self.activeMatrixLabel.setProperty("role", "field")
        self.activeMatrixValueLabel.setProperty("role", "field")
        self.matrixSetupLabel.setProperty("role", "field")
        self.globalCorrectorsLabel.setProperty("role", "field")
        self.globalCorrectorsValueLabel.setProperty("role", "field")
        self.correctorSetupLabel.setProperty("role", "field")
        self.label_45.setProperty("role", "field")
        self.label_46.setProperty("role", "field")

        limit_unit = display_unit(self.orbit_runtime["corrector_upperlimit_unit"])
        self.label_6.setText("Method")
        self.samplingIntervalSLabel.setText("Sampling Interval (s)")
        self.correctionSettleSLabel.setText("Settle Time (s)")
        self.correctorAccuracyUmLabel.setText("Accuracy (um)")
        self.sampPerStepLabel.setText("Samples / Step")
        self.correctorLimitLabel.setText(f"Corrector Limit ({limit_unit})")
        self.globalMaxIterLabel.setText("Global Max Iter")
        self.oneToOneMaxIterLabel.setText("1-to-1 Max Iter / BPM")
        self.correctionGainLabel.setText("Correction Gain")
        self.correctionMaxStepLabel.setText("Max Step (%)")
        self.responseKickLabel.setText(f"Local Response Kick ({limit_unit})")
        self.localResponseSourceLabel.setText("Local Response Source")
        self.localResponseSourceComboBox.setToolTip(
            "Measure Live applies a local test kick; Active Matrix uses the paired "
            "same-plane response coefficients from the active response matrix."
        )
        self.matrixResponseKickLabel.setText(f"Kick Step ({limit_unit})")
        self.matrixWaitSLabel.setText("Settle Time (s)")
        self.matrixSampleIntervalSLabel.setText("Sample Interval (s)")
        self.matrixSamplesLabel.setText("Samples/step")
        self.activeMatrixLabel.setText("Active Response Matrix")
        self.matrixSetupLabel.setText("Matrix Setup")
        self.globalCorrectorsLabel.setText("Global Correctors")
        self.correctorSetupLabel.setText("Corrector Setup")
        self.label_45.setText("BPM X (mm)")
        self.label_46.setText("BPM Y (mm)")
        self._hide_target_bpm_unit_labels()

        self.tabWidget.setTabText(self.tabWidget.indexOf(self.tab), "Run Correct")
        self.tabWidget.setTabText(self.tabWidget.indexOf(self.tab_2), "Response Matrix")

        for button in (
            self.pushButton,
            self.pushButton_2,
            self.pushButton_3,
            self.pushButton_4,
            self.pushButton_5,
            self.pushButton_6,
            self.pushButton_7,
            self.load_response_matrix_button,
            self.openResponseMatrixTabButton,
            self.editCorrectorsButton,
        ):
            button.setProperty("compact", True)

        self.pushButton.setText("Start Measurement")
        self.stop_response_button.setText("Stop Measurement")
        self.openResponseMatrixTabButton.setText("Open Response Matrix Tab")
        self.openResponseMatrixTabButton.clicked.connect(
            lambda: self.tabWidget.setCurrentWidget(self.tab_2)
        )
        self.editCorrectorsButton.setText("Edit Correctors")
        self.editCorrectorsButton.clicked.connect(self._open_global_correctors_tab)
        self.pushButton_4.setText("Start Correction")
        self.pushButton_3.setText("Stop Correction")
        self.pushButton_2.setText("Zero Correctors")
        self.pushButton_7.setText("Recover Correctors")
        self.pushButton_5.setText("All BPMs")
        self.pushButton_6.setText("Clear Selection")
        self.pushButton_4.setProperty("primary", True)
        self.pushButton_3.setProperty("danger", True)

        self.progressBar.setRange(0, len(self.all_checkboxes))
        self.progressBar.setTextVisible(True)
        self.progressBar.setFormat("%v/%m")
        self._set_response_progress(0, "Idle")

    def _hide_target_bpm_unit_labels(self):
        for label in self.scrollAreaWidgetContents_2.findChildren(QLabel):
            if label.text().strip().lower() == "mm":
                label.hide()
                label.setEnabled(False)

    def _make_panel_title(self, text, parent):
        label = QLabel(text, parent)
        label.setObjectName("panelTitle")
        return label

    def _make_subcard(self, title, parent):
        card = QFrame(parent)
        card.setObjectName("subCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        if title:
            title_label = QLabel(title, card)
            title_label.setObjectName("subTitle")
            layout.addWidget(title_label)
        return card, layout

    def _palette(self):
        return DARK_THEME if self.current_theme == "dark" else LIGHT_THEME

    def _apply_theme(self):
        palette = self._palette()
        self.centralwidget.setStyleSheet(build_orbit_correct_theme(palette))
        self.status_panel.apply_theme(palette)
        self.status_panel.setFixedHeight(self.status_panel.sizeHint().height())
        self._update_theme_toggle_button()

    def _update_theme_toggle_button(self):
        if self.current_theme == "dark":
            self.theme_toggle_button.setText("\u2600")
            self.theme_toggle_button.setToolTip("Switch to light theme.")
        else:
            self.theme_toggle_button.setText("\u263D")
            self.theme_toggle_button.setToolTip("Switch to dark theme.")

    def _toggle_theme(self):
        self.current_theme = "light" if self.current_theme == "dark" else "dark"
        self._apply_theme()
        self._refresh_status()

    def _notify(self, message):
        logger.info(message)
        self.last_notice = message
        self._refresh_status()

    def _open_global_correctors_tab(self):
        if hasattr(self, "selection_tabs") and hasattr(self, "global_corrector_tab"):
            if self._selected_correction_method() != "global":
                return
            self.selection_tabs.setCurrentWidget(self.global_corrector_tab)

    def _require_write_allowed(self, operation):
        try:
            require_workflow_write_allowed(self.app_context, "orbit", operation)
            return True
        except MachineProfileError as exc:
            message = str(exc)
            self._notify(message)
            QMessageBox.warning(self, "Orbit Correct", message)
            return False

    def _selected_bpm_count(self):
        return sum(1 for cb in self.all_checkboxes if cb.isChecked())

    def _global_corrector_summary(self):
        x_selected = sum(1 for checkbox in self.global_xcor_checkboxes if checkbox.isChecked())
        y_selected = sum(1 for checkbox in self.global_ycor_checkboxes if checkbox.isChecked())
        return (
            f"X {x_selected}/{len(self.global_xcor_checkboxes)}, "
            f"Y {y_selected}/{len(self.global_ycor_checkboxes)}"
        )

    def _current_process_status(self):
        if self.process_manager.is_running("orbit_correction"):
            return "Correction Running", "success"
        if self.process_manager.is_running("response_matrix"):
            return "Response Scan", "success"
        if self.process_manager.is_running("cor_off"):
            return "Zeroing", "warning"
        if self.process_manager.is_running("cor_recover"):
            return "Recovering", "warning"
        return "Idle", "subtle"

    def _selected_correction_method(self):
        return self.comboBox.currentText().strip().lower()

    def _selected_local_response_source(self):
        value = self.localResponseSourceComboBox.currentData()
        return str(value or "measure_live").strip().lower()

    def _apply_default_method(self):
        method = str(self.runtime_defaults.get("method", "")).strip()
        if not method:
            return
        index = self.comboBox.findText(method)
        if index >= 0:
            self.comboBox.setCurrentIndex(index)

    def _apply_default_local_response_source(self):
        source = str(
            self.runtime_defaults.get("local_response_source", "measure_live")
        ).strip().lower()
        index = self.localResponseSourceComboBox.findData(source)
        if index >= 0:
            self.localResponseSourceComboBox.setCurrentIndex(index)

    def _on_correction_method_changed(self, *_):
        self._update_method_parameter_state()
        self._refresh_status()

    def _on_local_response_source_changed(self, *_):
        self._update_local_response_source_state()
        self._refresh_status()

    def _update_method_parameter_state(self):
        if not hasattr(self, "globalCorrectionGroup"):
            return

        method = self._selected_correction_method()
        known_method = method in {"global", "one-to-one"}
        show_global = method == "global" or not known_method
        show_one_to_one = method == "one-to-one" or not known_method
        self.globalCorrectionGroup.setVisible(show_global)
        self.globalCorrectionGroup.setEnabled(show_global)
        self.oneToOneCorrectionGroup.setVisible(show_one_to_one)
        self.oneToOneCorrectionGroup.setEnabled(show_one_to_one)
        self._update_local_response_source_state()
        self._update_global_corrector_edit_state(method == "global")

    def _update_local_response_source_state(self):
        if not hasattr(self, "responseKickLineEdit"):
            return
        measure_live = self._selected_local_response_source() == "measure_live"
        self.responseKickLabel.setEnabled(measure_live)
        self.responseKickLineEdit.setEnabled(measure_live)

    def _update_global_corrector_edit_state(self, enabled):
        if hasattr(self, "editCorrectorsButton"):
            self.editCorrectorsButton.setEnabled(enabled)
        if hasattr(self, "selection_tabs") and hasattr(self, "global_corrector_tab"):
            self._set_global_correctors_tab_visible(enabled)

    def _set_global_correctors_tab_visible(self, visible):
        index = self.selection_tabs.indexOf(self.global_corrector_tab)
        if visible:
            if index < 0:
                self.selection_tabs.addTab(self.global_corrector_tab, "Global Correctors")
            return

        if index >= 0:
            if self.selection_tabs.currentIndex() == index:
                self.selection_tabs.setCurrentWidget(self.target_bpm_tab)
            self.selection_tabs.removeTab(index)

    def _refresh_status(self):
        if not hasattr(self, "status_panel"):
            return
        response_running = self.process_manager.is_running("response_matrix")
        correction_running = self.process_manager.is_running("orbit_correction")
        if self._response_scan_was_running and not response_running:
            self.refresh_response_matrices()
        self._response_scan_was_running = response_running

        total = len(self.all_checkboxes)
        selected = self._selected_bpm_count()
        process_text, process_tone = self._current_process_status()
        self.status_panel.set_item("tab", self.tabWidget.tabText(self.tabWidget.currentIndex()), "subtle")
        self.status_panel.set_item("method", self.comboBox.currentText(), "subtle")
        self.status_panel.set_item("targets", f"{selected}/{total}", "success" if selected else "warning")
        backend_name = self.app_context.control_backend.name
        backend_tone = "warning" if backend_name == "real" else "success"
        self.status_panel.set_item("backend", backend_name, backend_tone)
        self.status_panel.set_item("process", process_text, process_tone)
        self._refresh_response_progress(response_running)
        if hasattr(self, "stop_response_button"):
            self.pushButton.setEnabled(not response_running)
            self.stop_response_button.setEnabled(response_running)
        self.pushButton_3.setEnabled(correction_running)
        self.progressBar.setValue(selected)
        if hasattr(self, "globalCorrectorsValueLabel"):
            self.globalCorrectorsValueLabel.setText(self._global_corrector_summary())
        if hasattr(self, "global_corrector_progress"):
            corrector_total = len(self.global_xcor_checkboxes) + len(self.global_ycor_checkboxes)
            corrector_selected = (
                sum(1 for checkbox in self.global_xcor_checkboxes if checkbox.isChecked())
                + sum(1 for checkbox in self.global_ycor_checkboxes if checkbox.isChecked())
            )
            self.global_corrector_progress.setMaximum(corrector_total)
            self.global_corrector_progress.setValue(corrector_selected)

    def _format_response_matrix_record(self, record):
        created_at = str(record.get("created_at", "--"))
        matrix_file = Path(str(record.get("matrix_file", "--"))).name
        shape = record.get("shape", ("?", "?"))
        try:
            shape_text = f"{shape[0]}x{shape[1]}"
        except (TypeError, IndexError):
            shape_text = "?x?"
        return f"{created_at}  {shape_text}  {matrix_file}"

    def _set_active_response_matrix_text(self, value_text):
        if hasattr(self, "activeMatrixValueLabel"):
            self.activeMatrixValueLabel.setText(value_text)

    def refresh_response_matrices(self):
        if not hasattr(self, "response_matrix_combo"):
            return

        current_metadata = self.response_matrix_combo.currentData()
        self.response_matrix_combo.blockSignals(True)
        self.response_matrix_combo.clear()
        records = list_response_matrix_records(self.app_context)
        for record in records:
            self.response_matrix_combo.addItem(
                self._format_response_matrix_record(record),
                record.get("metadata_path"),
            )
        self.response_matrix_combo.blockSignals(False)

        if current_metadata:
            index = self.response_matrix_combo.findData(current_metadata)
            if index >= 0:
                self.response_matrix_combo.setCurrentIndex(index)

        try:
            active = get_active_response_matrix_record(self.app_context)
        except Exception as exc:
            self._set_active_response_matrix_text(f"invalid ({exc})")
            return

        if active is None:
            self._set_active_response_matrix_text("--")
            return

        self._set_active_response_matrix_text(self._format_response_matrix_record(active))
        active_metadata = active.get("metadata_path")
        if active_metadata:
            index = self.response_matrix_combo.findData(active_metadata)
            if index >= 0:
                self.response_matrix_combo.setCurrentIndex(index)

    def load_response_matrix(self):
        metadata_path = self.response_matrix_combo.currentData()
        if not metadata_path:
            QMessageBox.warning(
                self,
                "Orbit Correct",
                "No response matrix is available for the current machine/backend.",
            )
            return

        try:
            active = set_active_response_matrix(self.app_context, metadata_path)
        except Exception as exc:
            QMessageBox.warning(self, "Orbit Correct", str(exc))
            self.refresh_response_matrices()
            return

        self._notify(f"Loaded response matrix: {Path(active['matrix_file']).name}")
        self.refresh_response_matrices()

    def _extract_number(self, s):
        # 提取字符串中的第一个连续数字并转为整数
        match = re.search(r'\d+', s)
        return int(match.group()) if match else 0

    def selectall(self):
        for cb in self.all_checkboxes:
            cb.setChecked(True)

    def cancelall(self):
        for cb in self.all_checkboxes:
            cb.setChecked(False)

    def _set_global_correctors_checked(self, checked):
        for checkbox in self.global_xcor_checkboxes + self.global_ycor_checkboxes:
            checkbox.setChecked(checked)
        self._refresh_status()
    
    def all_BPM_target_value(self):
        all_bpmx_target_values = [spinbox.value() for spinbox in self._bpmx_spinboxes]
        all_bpmy_target_values = [spinbox.value() for spinbox in self._bpmy_spinboxes]
        return all_bpmx_target_values, all_bpmy_target_values

    def target_BPMs(self):
        bpm_target_list = []
        bpmx_target_values = []
        bpmy_target_values = []
        for checkbox, bpmx_spinbox, bpmy_spinbox in zip(
            self.all_checkboxes,
            self._bpmx_spinboxes,
            self._bpmy_spinboxes,
        ):
            if checkbox.isChecked():
                bpm_target_list.append(checkbox.text())
                bpmx_target_values.append(bpmx_spinbox.value())
                bpmy_target_values.append(bpmy_spinbox.value())
        return bpm_target_list, bpmx_target_values, bpmy_target_values

    def _selected_global_correctors(self):
        xcors = [checkbox.text() for checkbox in self.global_xcor_checkboxes if checkbox.isChecked()]
        ycors = [checkbox.text() for checkbox in self.global_ycor_checkboxes if checkbox.isChecked()]
        return xcors, ycors

    def _parse_positive_float(self, editor, label):
        try:
            value = float(editor.text())
        except ValueError as exc:
            raise ValueError(f"{label} must be numeric.") from exc
        if value <= 0:
            raise ValueError(f"{label} must be greater than 0.")
        return value

    def _parse_nonnegative_float(self, editor, label):
        try:
            value = float(editor.text())
        except ValueError as exc:
            raise ValueError(f"{label} must be numeric.") from exc
        if value < 0:
            raise ValueError(f"{label} must be >= 0.")
        return value

    def _parse_positive_int(self, editor, label):
        try:
            value = int(editor.text())
        except ValueError as exc:
            raise ValueError(f"{label} must be an integer.") from exc
        if value <= 0:
            raise ValueError(f"{label} must be greater than 0.")
        return value

    def _corrector_limit_value(self):
        profile_limit = float(self.orbit_runtime["corrector_upperlimit"])
        limit_unit = display_unit(self.orbit_runtime["corrector_upperlimit_unit"])
        corrector_limit = self._parse_positive_float(self.correctorLimitLineEdit, "Corrector Limit")
        if corrector_limit > profile_limit:
            raise ValueError(
                f"Corrector Limit cannot exceed profile limit {profile_limit:g} {limit_unit}."
            )
        return corrector_limit

    def _bounded_kick_value(self, editor, label, upper_limit, unit):
        response_kick = self._parse_positive_float(editor, label)
        if response_kick > upper_limit:
            raise ValueError(f"{label} cannot exceed {upper_limit:g} {unit}.")
        return response_kick

    def _local_response_kick_value(self, corrector_limit):
        unit = display_unit(self.orbit_runtime["corrector_upperlimit_unit"])
        return self._bounded_kick_value(
            self.responseKickLineEdit,
            "Local Response Kick",
            corrector_limit,
            unit,
        )

    def _matrix_measurement_args(self):
        profile_limit = float(self.orbit_runtime["corrector_upperlimit"])
        unit = display_unit(self.orbit_runtime["corrector_upperlimit_unit"])
        response_kick = self._bounded_kick_value(
            self.matrixResponseKickLineEdit,
            "Kick Step",
            profile_limit,
            unit,
        )
        wait_s = self._parse_nonnegative_float(self.matrixWaitSLineEdit, "Settle Time")
        sample_interval_s = self._parse_nonnegative_float(
            self.matrixSampleIntervalSLineEdit,
            "Sample Interval",
        )
        n_averages = self._parse_positive_int(self.matrixSamplesLineEdit, "Samples/step")
        return response_kick, wait_s, sample_interval_s, n_averages

    def _set_response_progress(self, percent, text):
        if not hasattr(self, "response_matrix_progress"):
            return
        self.response_matrix_progress.setRange(0, 100)
        self.response_matrix_progress.setValue(max(0, min(100, int(percent))))
        self.response_matrix_progress.setFormat(text)

    def _read_response_progress(self):
        try:
            return json.loads(self.response_progress_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError):
            return None

    def _refresh_response_progress(self, response_running):
        if not hasattr(self, "response_matrix_progress"):
            return

        progress = self._read_response_progress()
        if progress is None:
            if response_running:
                self._set_response_progress(0, "Starting...")
            return

        completed = int(progress.get("completed", 0))
        total = int(progress.get("total", 0))
        percent = int(progress.get("percent", 0))
        status = str(progress.get("status", "running"))
        current = str(progress.get("current", "")).strip()

        if total > 0:
            text = f"{completed}/{total} ({percent}%)"
        else:
            text = f"{percent}%"
        if current and status not in {"completed", "failed"}:
            text = f"{text} - {current}"
        elif status == "completed":
            text = "Completed (100%)"
        elif status == "failed":
            text = "Failed"

        self._set_response_progress(percent, text)

    def _correction_step_parameter_values(self):
        gain = self._parse_positive_float(self.correctionGainLineEdit, "Correction Gain")
        if gain > 1.0:
            raise ValueError("Correction Gain must be <= 1.0.")

        max_step_pct = self._parse_positive_float(
            self.correctionMaxStepLineEdit,
            "Max Step",
        )
        if max_step_pct > 100.0:
            raise ValueError("Max Step must be <= 100%.")
        return gain, max_step_pct / 100.0

    def _one_to_one_parameter_values(self):
        return self._parse_positive_int(
            self.oneToOneMaxIterLineEdit,
            "1-to-1 Max Iter / BPM",
        )

    def _correction_parameter_args(self):
        method = self._selected_correction_method()
        local_response_source = self._selected_local_response_source()
        corrector_limit = self._corrector_limit_value()
        correction_settle_s = self._parse_nonnegative_float(
            self.correctionSettleSLineEdit,
            "Settle Time",
        )
        correction_gain, correction_max_step_fraction = self._correction_step_parameter_values()
        global_xcors = []
        global_ycors = []

        if method == "global":
            global_max_iter = self._parse_positive_int(self.globalMaxIterLineEdit, "Global Max Iter")
            one_to_one_max_iter = int(self.runtime_defaults["one_to_one_max_iter"])
            response_kick = min(
                corrector_limit * float(self.runtime_defaults["local_response_kick_fraction"]),
                corrector_limit,
            )
            global_xcors, global_ycors = self._selected_global_correctors()
            if not global_xcors:
                raise ValueError("Select at least one X corrector for global correction.")
            if not global_ycors:
                raise ValueError("Select at least one Y corrector for global correction.")
        elif method == "one-to-one":
            global_max_iter = int(self.runtime_defaults["global_max_iter"])
            one_to_one_max_iter = self._one_to_one_parameter_values()
            if local_response_source == "active_matrix":
                try:
                    active_matrix = get_active_response_matrix_record(self.app_context)
                except Exception as exc:
                    raise ValueError(f"Active response matrix is invalid: {exc}") from exc
                if active_matrix is None:
                    raise ValueError(
                        "Active Matrix local response requires an active response matrix "
                        "for the current machine/backend."
                    )
                response_kick = min(
                    corrector_limit * float(
                        self.runtime_defaults["local_response_kick_fraction"]
                    ),
                    corrector_limit,
                )
            else:
                response_kick = self._local_response_kick_value(corrector_limit)
        else:
            global_max_iter = self._parse_positive_int(self.globalMaxIterLineEdit, "Global Max Iter")
            one_to_one_max_iter = self._one_to_one_parameter_values()
            response_kick = self._local_response_kick_value(corrector_limit)

        return [
            f"{corrector_limit:.12g}",
            str(global_max_iter),
            str(one_to_one_max_iter),
            f"{correction_gain:.12g}",
            f"{correction_max_step_fraction:.12g}",
            f"{response_kick:.12g}",
            ",".join(global_xcors),
            ",".join(global_ycors),
            f"{correction_settle_s:.12g}",
            local_response_source,
        ]
        
    def measure_res(self): #measure response matrix
        if not self._require_write_allowed("Response matrix measurement"):
            return
        if self.process_manager.is_running("orbit_correction"):
            QMessageBox.warning(
                self,
                "Orbit Correct",
                "Stop orbit correction before measuring the response matrix.",
            )
            return
        try:
            response_kick, wait_s, sample_interval_s, n_averages = self._matrix_measurement_args()
        except ValueError as exc:
            QMessageBox.warning(self, "Orbit Correct", str(exc))
            return

        try:
            self.response_progress_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
        self._set_response_progress(0, "Starting...")

        proc = self.process_manager.start_process(
            key="response_matrix",
            label="Response Matrix Measurement",
            cmd=[
                "python3",
                "findresponse.py",
                f"{response_kick:.12g}",
                str(n_averages),
                f"{wait_s:.12g}",
                f"{sample_interval_s:.12g}",
            ],
            cwd=str(APP_DIR),
        )
        if proc is None:
            self._set_response_progress(0, "Failed to start")

    def stop_measure_res(self):
        stopped = self.process_manager.stop_process("response_matrix", stop_timeout_s=5.0)
        if stopped:
            try:
                self.response_progress_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass
            self._set_response_progress(0, "Stopped")
        else:
            self._notify("Response Matrix Measurement is not running.")
        self._refresh_status()

    def start_cor(self):
        if not self._require_write_allowed("Orbit correction"):
            return
        if self.process_manager.is_running("response_matrix"):
            QMessageBox.warning(
                self,
                "Orbit Correct",
                "Stop response matrix measurement before starting orbit correction.",
            )
            return
        
        # prepare the target paras. 
        bpm_target_list, bpmx_target_values, bpmy_target_values = self.target_BPMs()
        if not bpm_target_list:
            QMessageBox.warning(
                self,
                "Orbit Correct",
                "Select at least one BPM before starting correction.",
            )
            return
        bpmx_target_values = [str(i) for i in bpmx_target_values]
        bpmy_target_values = [str(i) for i in bpmy_target_values]
        try:
            runtime_args = self._correction_parameter_args()
        except ValueError as exc:
            QMessageBox.warning(self, "Orbit Correct", str(exc))
            return
        
        cmd = [
            "python3", "correct.py",                  #0
            "start_cor",                              #1
            self.comboBox.currentText(),              #2   method
            self.samplingIntervalSLineEdit.text(),    #3   samp_interval
            self.correctorAccuracyUmLineEdit.text(),  #4   cor_accuracy
            self.sampPerStepLineEdit.text(),          #5   samples_perstep
            ",".join(bpm_target_list),                     #6   target_BPMlist
            ",".join(bpmx_target_values),                     #7   target_BPMlist
            ",".join(bpmy_target_values),                    #8   target_BPMlist
            *runtime_args,
        ]
        self.process_manager.start_process(
            key="orbit_correction",
            label="Orbit Correction",
            cmd=cmd,
            cwd=str(APP_DIR),
        )
 
    def cor_off(self):
        if not self._require_write_allowed("Corrector reset"):
            return
        if self.process_manager.is_running("response_matrix") or self.process_manager.is_running("orbit_correction"):
            QMessageBox.warning(
                self,
                "Orbit Correct",
                "Stop active measurement or correction before zeroing correctors.",
            )
            return
        bpm_target_list, bpmx_target_values, bpmy_target_values = self.target_BPMs()
        cmd = [
            "python3", "correct.py",                  #0
            "cor_off",                                 #1
            ",".join(bpm_target_list)                  #2
        ]
        self.process_manager.start_process(
            key="cor_off",
            label="Corrector Reset",
            cmd=cmd,
            cwd=str(APP_DIR),
            expect_running=False,
        )

    def cor_recover(self):
        if not self._require_write_allowed("Corrector recover"):
            return
        if self.process_manager.is_running("response_matrix") or self.process_manager.is_running("orbit_correction"):
            QMessageBox.warning(
                self,
                "Orbit Correct",
                "Stop active measurement or correction before recovering correctors.",
            )
            return
        # bpm_target_list, bpmx_target_values, bpmy_target_values = self.target_BPMs()
        cmd = [
            "python3", "correct.py",                  #0
            "cor_recover",                             #1
            # ",".join(bpm_target_list)                  #2
        ]
        self.process_manager.start_process(
            key="cor_recover",
            label="Corrector Recover",
            cmd=cmd,
            cwd=str(APP_DIR),
            expect_running=False,
        )


    # stop_cor
    def stop_cor(self):
        stopped = self.process_manager.stop_process("orbit_correction")
        if not stopped:
            self._notify("Orbit Correction is not running.")
        self._refresh_status()
    
    # 窗口关闭事件
    def closeEvent(self, event):
        self.process_manager.shutdown()
        event.accept()




if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = myWindow()
    window.show()
    sys.exit(app.exec_())
