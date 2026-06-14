import sys
import time
from dataclasses import dataclass
from pathlib import Path

_REPO_BOOTSTRAP_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file()
)
if str(_REPO_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_BOOTSTRAP_ROOT))

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

import epics
import numpy as np
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gui import Ui_Form

from half_linac.src.shared.machine_profile import (
    build_model_backend,
    get_bba_preset,
    list_elements,
    load_app_context,
    resolve_channel,
)
from half_linac.src.shared.machine_profile.runtime_selector import (
    RuntimeSelectorWidget,
    default_control_backend_choices,
    request_runtime_restart,
)


BBA_DIR = Path(__file__).resolve().parent
M1S_PATH = BBA_DIR / "m1S.txt"
BBA2_K1LQM2_PATH = BBA_DIR / "bba2_k1Lqm2.txt"
BBA2_M1_PATH = BBA_DIR / "bba2_m1.txt"
BBA2_THETAM2_PATH = BBA_DIR / "bba2_thetam2.txt"
K1LQ_FACTOR = 0.15
HEADER_ACTION_HEIGHT = 32

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
    "plot_card_bg": "#121a20",
    "plot_bg": "#11181e",
    "plot_grid": "#2a3943",
    "plot_spine": "#445764",
    "plot_text": "#d7e2ea",
    "plot_point": "#78d5e3",
    "plot_fit": "#ff6b6b",
    "status_strip_bg": "#131c22",
    "status_strip_border": "#2a3943",
    "status_separator": "#31424d",
    "status_item_idle_bar": "#4f6270",
    "status_title_fg": "#8ea0ad",
    "metric_active_fg": "#45d0bc",
    "metric_warning_fg": "#e4b86f",
    "metric_idle_fg": "#c8d2da",
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
    "plot_card_bg": "#f6f1e8",
    "plot_bg": "#fffdf8",
    "plot_grid": "#ddd4c7",
    "plot_spine": "#b5aa9a",
    "plot_text": "#304049",
    "plot_point": "#2f9aad",
    "plot_fit": "#d9534f",
    "status_strip_bg": "#f7f1e8",
    "status_strip_border": "#ddd2c4",
    "status_separator": "#ddd4c7",
    "status_item_idle_bar": "#c8bfb3",
    "status_title_fg": "#7c7368",
    "metric_active_fg": "#2d7f6d",
    "metric_warning_fg": "#a97118",
    "metric_idle_fg": "#4e5a62",
}


def build_bba_theme(palette):
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

QFrame#plotCard, QFrame#controlCard, QFrame#resultCard {{
    background-color: {panel_bg};
    border: 1px solid {panel_border};
    border-radius: 14px;
}}

QTabWidget::pane {{
    border: 1px solid {panel_border};
    border-radius: 14px;
    background: {panel_bg};
    top: -1px;
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

QLabel[role="field"] {{
    color: {muted_fg};
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
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

QPushButton:disabled {{
    color: {button_disabled_fg};
    border-color: {button_disabled_border};
    background-color: {button_disabled_bg};
}}

QPushButton[compact="true"] {{
    padding: 3px 10px;
    min-height: 22px;
    font-size: 11px;
}}

QLineEdit, QComboBox {{
    background-color: {input_bg};
    border: 1px solid {input_border};
    border-radius: 10px;
    color: {input_fg};
    padding: 5px 10px;
    min-height: 16px;
    selection-background-color: {metric_active_fg};
}}

QLineEdit[readOnly="true"] {{
    color: {summary_title_fg};
    font-weight: 600;
}}

QComboBox::drop-down {{
    border: none;
    width: 20px;
}}

QComboBox QAbstractItemView {{
    background-color: {input_bg};
    color: {input_fg};
    border: 1px solid {input_border};
    selection-background-color: {button_hover_bg};
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


class BBAStatusStrip(QWidget):
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
            separator.setFrameShadow(QFrame.Plain)
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


@dataclass
class ScanParameters:
    corr: str = ""
    quad: str = ""
    bpm1: str = ""
    bpm2: str = ""
    plane: str = "X"
    corrPV: str = ""
    quadPV: str = ""
    bpm1PV: str = ""
    bpm2PV: str = ""
    corr_from: float = 0.0
    corr_end: float = 0.0
    corr_steps: int = 0
    quad_from: float = 0.0
    quad_end: float = 0.0
    quad_steps: int = 0
    samples: int = 0
    sleeptime: float = 0.0
    recal: bool = False
    energy_mev: float = 0.0
    bpm1_samples: int = 0
    by_formula: str = ""
    bx_formula: str = ""
    leff_by: float = 0.0
    leff_bx: float = 0.0
    control_backend: str = ""
    app_context: object | None = None


class myWindow(QWidget, Ui_Form):
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        self.app_context = load_app_context("bba")
        self.machine_profile = self.app_context.profile
        self.bba_workflow = self.app_context.bba_workflow
        if self.bba_workflow is None:
            raise ValueError("BBA workflow is not available in the current app context.")
        self.current_theme = "dark"
        self.scan = None
        self.clear = None
        self.scan_mode = None
        self.scan_family = None
        self._plot_wrappers = {}
        self._result_fields = []

        self._configure_window()
        self._setup_defaults()
        self._connect_buttons()
        self._build_shell()
        self._configure_form_content()
        self._configure_machine_profile()
        self._apply_theme()
        self._draw_placeholder_plots()
        self._refresh_status()

    def _configure_window(self):
        self.setWindowTitle(f"{self.machine_profile.machine.display_name} BBA")
        self.resize(1600, 960)
        self.setMinimumSize(1320, 860)
        self.tabWidget.setCurrentIndex(0)

    def _setup_defaults(self):
        self.lineEdit_10.clear()
        self.lineEdit_18.clear()
        self.lineEdit_19.clear()
        self.lineEdit_19.setToolTip("")
        self.lineEdit_21.clear()

    def _connect_buttons(self):
        self.pushButton.clicked.connect(self.startScan)
        self.pushButton_2.clicked.connect(self.clearPlot)
        self.pushButton_3.clicked.connect(self.recalculate)
        self.pushButton_4.clicked.connect(self.stopScan)

        self.pushButton_5.clicked.connect(self.startScan_bba2)
        self.pushButton_6.clicked.connect(self.stopScan)
        self.pushButton_8.clicked.connect(self.clearPlot_bba2)
        self.pushButton_7.clicked.connect(self.recalculate_bba2)
        self.tabWidget.currentChanged.connect(self._refresh_status)
        self.comboBox_5.currentIndexChanged.connect(self._refresh_status)
        self.comboBox_5.currentIndexChanged.connect(self._refresh_standard_correctors)
        self.comboBox_10.currentIndexChanged.connect(self._refresh_status)
        self.comboBox_10.currentIndexChanged.connect(self._refresh_bba2_correctors)
        self.comboBox_11.currentIndexChanged.connect(self._refresh_status)

    def _build_shell(self):
        self.gridLayout.setContentsMargins(10, 10, 10, 10)
        self.gridLayout.setSpacing(12)
        self.horizontalLayout.setContentsMargins(0, 0, 0, 0)
        self.horizontalLayout.setSpacing(0)
        self.horizontalLayout_2.setContentsMargins(0, 0, 0, 0)
        self.horizontalLayout_2.setSpacing(0)

        self.gridLayout.removeWidget(self.tabWidget)
        self._build_summary_panel()
        self.gridLayout.addWidget(self.tabWidget, 1, 0, 1, 1)

        self.gridLayout_2.setRowStretch(0, 4)
        self.gridLayout_2.setRowStretch(1, 2)
        self.gridLayout_2.setColumnStretch(0, 1)
        self.gridLayout_2.setColumnStretch(1, 1)
        self.gridLayout_2.setHorizontalSpacing(14)
        self.gridLayout_3.setRowStretch(0, 4)
        self.gridLayout_3.setRowStretch(1, 2)
        self.gridLayout_3.setColumnStretch(0, 1)
        self.gridLayout_3.setColumnStretch(1, 1)
        self.gridLayout_3.setHorizontalSpacing(14)

        self._style_plot_cards()
        self._style_control_cards()

    def _build_summary_panel(self):
        panel = QFrame(self)
        panel.setObjectName("summaryPanel")
        panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        outer_layout = QVBoxLayout(panel)
        outer_layout.setContentsMargins(12, 10, 12, 10)
        outer_layout.setSpacing(6)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(12)

        title = QLabel("Beam-Based Alignment", panel)
        title.setObjectName("summaryTitle")
        header_layout.addWidget(title)
        header_layout.addStretch(1)

        self.runtime_selector = RuntimeSelectorWidget(
            current_machine_id=self.machine_profile.machine.id,
            current_control_backend=self.app_context.control_backend.name,
            parent=panel,
        )
        self.runtime_selector.apply_requested.connect(self._apply_runtime_selection)
        header_layout.addWidget(self.runtime_selector)

        self.theme_toggle_button = QToolButton(panel)
        self.theme_toggle_button.setObjectName("themeToggleButton")
        self.theme_toggle_button.setFixedSize(HEADER_ACTION_HEIGHT, HEADER_ACTION_HEIGHT)
        self.theme_toggle_button.clicked.connect(self._toggle_theme)
        header_layout.addWidget(self.theme_toggle_button)
        outer_layout.addLayout(header_layout)

        self.status_panel = BBAStatusStrip(panel)
        self.status_panel.add_item("tab", "TAB", self.tabWidget.tabText(self.tabWidget.currentIndex()))
        self.status_panel.add_item("plane", "PLANE", self.comboBox_5.currentText())
        self.status_panel.add_item("scan", "SCAN", "Idle")
        self.status_panel.add_item("backend", "BACKEND", "Standard")
        self.status_panel.finish()
        self.status_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        outer_layout.addWidget(self.status_panel)

        self.gridLayout.addWidget(panel, 0, 0, 1, 1)

    def _style_plot_cards(self):
        self._wrap_plot_card(self.gridLayout_2, self.widget, "BBA-1 Quad Sweep", 0, 0, self.tab)
        self._wrap_plot_card(self.gridLayout_2, self.widget_2, "BBA-1 Offset Fit", 0, 1, self.tab)
        self._wrap_plot_card(self.gridLayout_3, self.widget_3, "BBA-2 Quad Sweep", 0, 0, self.tab_2)
        self._wrap_plot_card(self.gridLayout_3, self.widget_4, "BBA-2 Corrector Sweep", 0, 1, self.tab_2)

    def _wrap_plot_card(self, layout, widget, title_text, row, col, parent):
        layout.removeWidget(widget)

        card = QFrame(parent)
        card.setObjectName("plotCard")
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 10, 10, 10)
        card_layout.setSpacing(8)

        title = QLabel(title_text, card)
        title.setObjectName("panelTitle")
        card_layout.addWidget(title)
        card_layout.addWidget(widget)

        layout.addWidget(card, row, col)
        self._plot_wrappers[widget] = card

    def _style_control_cards(self):
        self.frame.setObjectName("controlCard")
        self.frame_2.setObjectName("resultCard")
        self.frame_3.setObjectName("controlCard")
        self.frame_4.setObjectName("resultCard")

        for widget in (self.frame, self.frame_2, self.frame_3, self.frame_4):
            widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        self.gridLayout_2.setAlignment(self.frame, Qt.AlignTop)
        self.gridLayout_2.setAlignment(self.frame_2, Qt.AlignTop)
        self.gridLayout_3.setAlignment(self.frame_3, Qt.AlignTop)
        self.gridLayout_3.setAlignment(self.frame_4, Qt.AlignTop)

        self._rebuild_bba1_setup_panel()
        self._rebuild_bba1_run_panel()
        self._rebuild_bba2_setup_panel()
        self._rebuild_bba2_run_panel()

    def _configure_form_content(self):
        for button in (
            self.pushButton,
            self.pushButton_2,
            self.pushButton_3,
            self.pushButton_4,
            self.pushButton_5,
            self.pushButton_6,
            self.pushButton_7,
            self.pushButton_8,
        ):
            button.setProperty("compact", True)

        self.pushButton.setText("Start Scan")
        self.pushButton_2.setText("Clear")
        self.pushButton_3.setText("Recalculate")
        self.pushButton_4.setText("Stop Scan")
        self.pushButton_5.setText("Start Scan")
        self.pushButton_6.setText("Stop Scan")
        self.pushButton_7.setText("Recalculate")
        self.pushButton_8.setText("Clear")

        self._result_fields = [self.lineEdit_10, self.lineEdit_18, self.lineEdit_19, self.lineEdit_21]
        for field in self._result_fields:
            field.setReadOnly(True)

    def _make_panel_title(self, text, parent):
        label = QLabel(text, parent)
        label.setObjectName("panelTitle")
        return label

    def _make_field_label(self, text, parent):
        label = QLabel(text, parent)
        label.setProperty("role", "field")
        return label

    def _rebuild_bba1_setup_panel(self):
        layout = QVBoxLayout(self.frame)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        layout.addWidget(self._make_panel_title("Scan Setup", self.frame))

        for label in (
            self.label,
            self.label_2,
            self.label_3,
            self.label_4,
            self.label_5,
            self.label_6,
            self.label_7,
            self.label_8,
            self.label_9,
        ):
            label.hide()

        form = QGridLayout()
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)

        rows = [
            ("Corrector", self.comboBox, "From", self.lineEdit, "To", self.lineEdit_2, "Steps", self.lineEdit_3),
            ("Quad", self.comboBox_2, "From", self.lineEdit_6, "To", self.lineEdit_4, "Steps", self.lineEdit_5),
            ("BPM1", self.comboBox_3, "BPM2", self.comboBox_4, "Scan freq", self.lineEdit_7, "Samples/step", self.lineEdit_8),
        ]
        for row, items in enumerate(rows):
            for col in range(0, len(items), 2):
                form.addWidget(self._make_field_label(items[col], self.frame), row, col * 2)
                widget = items[col + 1]
                widget.setParent(self.frame)
                widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                form.addWidget(widget, row, col * 2 + 1)

        for column in (1, 3, 5, 7):
            form.setColumnStretch(column, 1)

        layout.addLayout(form)

    def _rebuild_bba1_run_panel(self):
        layout = QVBoxLayout(self.frame_2)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        layout.addWidget(self._make_panel_title("Run & Readout", self.frame_2))

        self.label_13.hide()

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(8)
        self.comboBox_5.setParent(self.frame_2)
        self.comboBox_5.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        controls.addWidget(self.comboBox_5)
        for button in (self.pushButton, self.pushButton_2, self.pushButton_4, self.pushButton_3):
            button.setParent(self.frame_2)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            controls.addWidget(button)
        layout.addLayout(controls)

        result_row = QGridLayout()
        result_row.setHorizontalSpacing(8)
        result_row.setVerticalSpacing(6)
        result_row.addWidget(self._make_field_label("Offset of BPM1-Quad (mm)", self.frame_2), 0, 0)
        self.lineEdit_10.setParent(self.frame_2)
        self.lineEdit_10.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        result_row.addWidget(self.lineEdit_10, 0, 1)
        result_row.setColumnStretch(1, 1)
        layout.addLayout(result_row)

    def _rebuild_bba2_setup_panel(self):
        layout = QVBoxLayout(self.frame_3)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        layout.addWidget(self._make_panel_title("Scan Setup", self.frame_3))

        for label in (
            self.label_10,
            self.label_11,
            self.label_12,
            self.label_14,
            self.label_15,
            self.label_16,
            self.label_17,
            self.label_18,
            self.label_19,
            self.label_22,
            self.label_24,
            self.label_25,
            self.label_26,
            self.label_27,
            self.label_28,
            self.label_29,
        ):
            label.hide()

        form = QGridLayout()
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)

        rows = [
            ("Quad", self.comboBox_7, "From", self.lineEdit_14, "To", self.lineEdit_17, "Steps", self.lineEdit_16),
            ("Corrector", self.comboBox_9, "From", self.lineEdit_11, "To", self.lineEdit_13, "Steps", self.lineEdit_12),
            ("1st BPM", self.comboBox_8, "2nd BPM", self.comboBox_6, "BPM1 samples", self.lineEdit_22, "", None),
            ("Energy@corrector", self.lineEdit_20, "Scan freq", self.lineEdit_15, "Samples/step", self.lineEdit_9, "", None),
        ]
        for row, items in enumerate(rows):
            for col in range(0, len(items), 2):
                text = items[col]
                widget = items[col + 1]
                if not text or widget is None:
                    continue
                form.addWidget(self._make_field_label(text, self.frame_3), row, col * 2)
                widget.setParent(self.frame_3)
                widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                form.addWidget(widget, row, col * 2 + 1)

        for column in (1, 3, 5):
            form.setColumnStretch(column, 1)

        layout.addLayout(form)

        model_title = QLabel("Corrector model", self.frame_3)
        layout.addWidget(model_title)

        model_grid = QGridLayout()
        model_grid.setHorizontalSpacing(8)
        model_grid.setVerticalSpacing(6)
        self.lineEdit_23.setParent(self.frame_3)
        self.lineEdit_24.setParent(self.frame_3)
        self.lineEdit_25.setParent(self.frame_3)
        self.lineEdit_26.setParent(self.frame_3)
        for widget in (self.lineEdit_23, self.lineEdit_24, self.lineEdit_25, self.lineEdit_26):
            widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        model_grid.addWidget(self._make_field_label("By (Gauss)", self.frame_3), 0, 0)
        model_grid.addWidget(self.lineEdit_23, 0, 1, 1, 3)
        model_grid.addWidget(self._make_field_label("Bx (Gauss)", self.frame_3), 1, 0)
        model_grid.addWidget(self.lineEdit_24, 1, 1, 1, 3)
        model_grid.addWidget(self._make_field_label("Leff By (m)", self.frame_3), 2, 0)
        model_grid.addWidget(self.lineEdit_25, 2, 1)
        model_grid.addWidget(self._make_field_label("Leff Bx (m)", self.frame_3), 2, 2)
        model_grid.addWidget(self.lineEdit_26, 2, 3)
        model_grid.setColumnStretch(1, 1)
        model_grid.setColumnStretch(3, 1)
        layout.addLayout(model_grid)

    def _rebuild_bba2_run_panel(self):
        layout = QVBoxLayout(self.frame_4)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        layout.addWidget(self._make_panel_title("Run & Readout", self.frame_4))

        for label in (self.label_20, self.label_21, self.label_23):
            label.hide()

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(8)
        for widget in (self.comboBox_10, self.comboBox_11):
            widget.setParent(self.frame_4)
            widget.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            controls.addWidget(widget)
        controls.addStretch(1)
        for button in (self.pushButton_5, self.pushButton_8, self.pushButton_6, self.pushButton_7):
            button.setParent(self.frame_4)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            controls.addWidget(button)
        layout.addLayout(controls)

        results = QGridLayout()
        results.setHorizontalSpacing(8)
        results.setVerticalSpacing(6)
        items = [
            ("Average of BPM1 (mm)", self.lineEdit_21),
            ("Response matrix R12 (m)", self.lineEdit_19),
            ("Offset of BPM1-Quad (mm)", self.lineEdit_18),
        ]
        for row, (text, widget) in enumerate(items):
            results.addWidget(self._make_field_label(text, self.frame_4), row, 0)
            widget.setParent(self.frame_4)
            widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            results.addWidget(widget, row, 1)
        results.setColumnStretch(1, 1)
        layout.addLayout(results)

    def _palette(self):
        return DARK_THEME if self.current_theme == "dark" else LIGHT_THEME

    def _apply_theme(self):
        palette = self._palette()
        self.setStyleSheet(build_bba_theme(palette))
        self.status_panel.apply_theme(palette)
        self.status_panel.setFixedHeight(self.status_panel.sizeHint().height())
        self._update_theme_toggle_button()
        self._restyle_current_plots()

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

    def _style_axes(self, widget, xlabel=None, ylabel=None):
        palette = self._palette()
        widget.fig.patch.set_facecolor(palette["plot_card_bg"])
        widget.axes.set_facecolor(palette["plot_bg"])
        widget.axes.tick_params(colors=palette["plot_text"], which="both", labelsize=9)
        widget.axes.xaxis.label.set_color(palette["plot_text"])
        widget.axes.yaxis.label.set_color(palette["plot_text"])
        for spine in widget.axes.spines.values():
            spine.set_edgecolor(palette["plot_spine"])
        if xlabel:
            widget.axes.set_xlabel(xlabel)
        if ylabel:
            widget.axes.set_ylabel(ylabel)
        widget.axes.grid(alpha=0.75, linestyle="--", color=palette["plot_grid"])

    def _draw_placeholder(self, widget, xlabel, ylabel, note):
        palette = self._palette()
        widget.axes.clear()
        self._style_axes(widget, xlabel, ylabel)
        widget.axes.text(
            0.5,
            0.5,
            note,
            transform=widget.axes.transAxes,
            ha="center",
            va="center",
            color=palette["muted_fg"],
            fontsize=10,
        )
        widget.canvas.draw()

    def _draw_placeholder_plots(self):
        self._draw_placeholder(self.widget, "$K_1L_q$", "BPM2 (mm)", "Waiting for BBA-1 scan points")
        self._draw_placeholder(self.widget_2, "BPM1 (mm)", "S", "Waiting for BBA-1 fit")
        self._draw_placeholder(self.widget_3, "$K_1L_q$", "BPM2 (mm)", "Waiting for BBA-2 quad scan")
        self._draw_placeholder(self.widget_4, "corrector kick (mrad)", "BPM2 (mm)", "Waiting for BBA-2 corrector scan")

    def _restyle_current_plots(self):
        palette = self._palette()
        plot_specs = (
            (self.widget, "$K_1L_q$", "BPM2 (mm)"),
            (self.widget_2, "BPM1 (mm)", "S"),
            (self.widget_3, "$K_1L_q$", "BPM2 (mm)"),
            (self.widget_4, "corrector kick (mrad)", "BPM2 (mm)"),
        )
        if not any(plot.axes.lines for plot, _, _ in plot_specs):
            self._draw_placeholder_plots()
            return

        for plot, xlabel, ylabel in plot_specs:
            self._style_axes(plot, xlabel, ylabel)
            for line in plot.axes.lines:
                if line.get_linestyle() and line.get_linestyle() != "None":
                    line.set_color(palette["plot_fit"])
                else:
                    line.set_color(palette["plot_point"])
            plot.canvas.draw()

    @staticmethod
    def _set_combo_items(combo, items):
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(items)
        combo.blockSignals(False)

    @staticmethod
    def _set_combo_current_text(combo, value):
        index = combo.findText(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    @staticmethod
    def _set_line_edit_value(line_edit, value):
        line_edit.setText(str(value))

    def _apply_typed_defaults(self, config, field_map):
        for field_name, widget in field_map.items():
            value = getattr(config, field_name, None)
            if value is not None:
                self._set_line_edit_value(widget, value)

    @staticmethod
    def _normalize_plane_value(value):
        text = str(value).strip().lower()
        if text.startswith("x"):
            return "X"
        if text.startswith("y"):
            return "Y"
        raise ValueError(f"Unsupported plane value: {value!r}")

    @staticmethod
    def _normalize_control_backend_value(value):
        text = str(value).strip().lower().replace("_", " ")
        if text in {"vm", "virtual machine"}:
            return "vm"
        if text in {"real", "real machine"}:
            return "real"
        raise ValueError(f"Unsupported control backend: {value!r}")

    def _set_combo_current_plane(self, combo, plane):
        target = self._normalize_plane_value(plane)
        for index in range(combo.count()):
            try:
                if self._normalize_plane_value(combo.itemText(index)) == target:
                    combo.setCurrentIndex(index)
                    return
            except ValueError:
                continue

    def _set_combo_current_control_backend(self, combo, backend):
        target = self._normalize_control_backend_value(backend)
        for index in range(combo.count()):
            try:
                if self._normalize_control_backend_value(combo.itemText(index)) == target:
                    combo.setCurrentIndex(index)
                    return
            except ValueError:
                continue

    def _find_bba_preset(self, preset_id):
        return get_bba_preset(self.app_context, preset_id)

    def _family_control_backends(self, family):
        return family.control_backends or default_control_backend_choices(self.app_context.machine.id)

    def _selected_family_control_backend(self, family):
        control_backends = self._family_control_backends(family)
        current = self.app_context.control_backend.name
        if current in control_backends:
            return current
        if control_backends:
            return control_backends[0]
        return current

    def _require_family_control_backend(self, family, backend, label):
        allowed = self._family_control_backends(family)
        if allowed and backend not in allowed:
            allowed_text = ", ".join(allowed)
            raise ValueError(f"{label} does not allow {backend!r} backend. Allowed backend(s): {allowed_text}.")

    def _profile_element_ids(self, *, kind, role=None, plane=None):
        return [element.id for element in list_elements(self.app_context, kind=kind, role=role, plane=plane)]

    def _family_element_ids(self, configured_ids, *, kind, plane=None):
        if not configured_ids:
            return self._profile_element_ids(kind=kind, plane=plane)

        items = []
        for element_id in configured_ids:
            element = self.machine_profile.get_element(element_id)
            if element.kind != kind:
                continue
            if plane is not None and element.plane != plane:
                continue
            items.append(element.id)
        return items

    def _standard_quad_items(self):
        return self._family_element_ids(self.bba_workflow.standard.quads, kind="quad")

    def _standard_corrector_items(self, plane):
        return self._family_element_ids(
            self.bba_workflow.standard.correctors,
            kind="corr",
            plane=self._normalize_plane_value(plane).lower(),
        )

    def _standard_bpm1_items(self):
        return self._family_element_ids(self.bba_workflow.standard.bpm1, kind="bpm")

    def _standard_bpm2_items(self):
        return self._family_element_ids(self.bba_workflow.standard.bpm2, kind="bpm")

    def _bba2_quad_items(self):
        return self._family_element_ids(self.bba_workflow.bba2.quads, kind="quad")

    def _bba2_corrector_items(self, plane):
        return self._family_element_ids(
            self.bba_workflow.bba2.correctors,
            kind="corr",
            plane=self._normalize_plane_value(plane).lower(),
        )

    def _bba2_bpm1_items(self):
        return self._family_element_ids(self.bba_workflow.bba2.bpm1, kind="bpm")

    def _bba2_bpm2_items(self):
        return self._family_element_ids(self.bba_workflow.bba2.bpm2, kind="bpm")

    def _refresh_corrector_combo(self, combo, items, preferred=None):
        current = preferred or combo.currentText()
        self._set_combo_items(combo, items)
        if current in items:
            self._set_combo_current_text(combo, current)
        elif items:
            self._set_combo_current_text(combo, items[0])

    def _refresh_standard_correctors(self):
        if not hasattr(self, "comboBox"):
            return
        items = self._standard_corrector_items(self.comboBox_5.currentText())
        self._refresh_corrector_combo(self.comboBox, items)

    def _refresh_bba2_correctors(self):
        if not hasattr(self, "comboBox_9"):
            return
        items = self._bba2_corrector_items(self.comboBox_10.currentText())
        self._refresh_corrector_combo(self.comboBox_9, items)

    def _configure_machine_profile(self):
        standard = self.bba_workflow.standard
        bba2 = self.bba_workflow.bba2
        self.standard_control_backend = self._selected_family_control_backend(standard)
        bba2_control_backends = self._family_control_backends(bba2)
        bba2_control_backend = self._selected_family_control_backend(bba2)

        self._set_combo_items(self.comboBox_2, self._standard_quad_items())
        self._set_combo_items(self.comboBox_3, self._standard_bpm1_items())
        self._set_combo_items(self.comboBox_4, self._standard_bpm2_items())

        self._set_combo_items(self.comboBox_7, self._bba2_quad_items())
        self._set_combo_items(self.comboBox_8, self._bba2_bpm1_items())
        self._set_combo_items(self.comboBox_6, self._bba2_bpm2_items())
        self._set_combo_items(self.comboBox_11, bba2_control_backends)

        standard_default = self._find_bba_preset(standard.default_preset)
        self._set_combo_current_plane(self.comboBox_5, standard_default.plane)
        self._refresh_corrector_combo(
            self.comboBox,
            self._standard_corrector_items(standard_default.plane),
            preferred=standard_default.corr,
        )
        self._set_combo_current_text(self.comboBox_2, standard_default.quad)
        self._set_combo_current_text(self.comboBox_3, standard_default.bpm1)
        self._set_combo_current_text(self.comboBox_4, standard_default.bpm2)
        self._apply_typed_defaults(
            standard_default.scan,
            {
                "corr_from": self.lineEdit,
                "corr_end": self.lineEdit_2,
                "corr_steps": self.lineEdit_3,
                "quad_end": self.lineEdit_4,
                "quad_steps": self.lineEdit_5,
                "quad_from": self.lineEdit_6,
                "sleeptime": self.lineEdit_7,
                "samples": self.lineEdit_8,
            },
        )

        bba2_default = self._find_bba_preset(bba2.default_preset)
        self._set_combo_current_plane(self.comboBox_10, bba2_default.plane)
        self._set_combo_current_text(self.comboBox_7, bba2_default.quad)
        self._refresh_corrector_combo(
            self.comboBox_9,
            self._bba2_corrector_items(bba2_default.plane),
            preferred=bba2_default.corr,
        )
        self._set_combo_current_text(self.comboBox_8, bba2_default.bpm1)
        self._set_combo_current_text(self.comboBox_6, bba2_default.bpm2)
        self._set_combo_current_control_backend(
            self.comboBox_11,
            bba2_control_backend,
        )
        self._apply_typed_defaults(
            bba2_default.scan,
            {
                "corr_steps": self.lineEdit_12,
                "corr_from": self.lineEdit_11,
                "corr_end": self.lineEdit_13,
                "quad_from": self.lineEdit_14,
                "sleeptime": self.lineEdit_15,
                "quad_steps": self.lineEdit_16,
                "quad_end": self.lineEdit_17,
                "samples": self.lineEdit_9,
            },
        )
        self._apply_typed_defaults(
            bba2_default.analysis,
            {
                "energy_mev": self.lineEdit_20,
                "bpm1_samples": self.lineEdit_22,
                "by_formula": self.lineEdit_23,
                "bx_formula": self.lineEdit_24,
                "leff_by": self.lineEdit_25,
                "leff_bx": self.lineEdit_26,
            },
        )

    def _profile_default_control_backend(self):
        return getattr(self, "standard_control_backend", self.app_context.control_backend.name)

    @staticmethod
    def _bpm_logical_channel(plane):
        return "x" if plane == "X" else "y"

    def _current_plane_text(self):
        return self.comboBox_5.currentText() if self.tabWidget.currentIndex() == 0 else self.comboBox_10.currentText()

    def _current_control_backend_text(self):
        return self._profile_default_control_backend() if self.tabWidget.currentIndex() == 0 else self.comboBox_11.currentText()

    def _refresh_status(self):
        if not hasattr(self, "status_panel"):
            return

        self.status_panel.set_item("tab", self.tabWidget.tabText(self.tabWidget.currentIndex()), "subtle")
        self.status_panel.set_item("plane", self._current_plane_text(), "subtle")

        if self._scan_is_running():
            scan_text = "Recalculate" if self.scan_mode == "recalculate" else "Running"
            self.status_panel.set_item("scan", scan_text, "success")
        else:
            self.status_panel.set_item("scan", "Idle", "subtle")

        backend_text = self._current_control_backend_text()
        try:
            normalized_backend = self._normalize_control_backend_value(backend_text)
        except ValueError:
            normalized_backend = None
        backend_tone = (
            "warning" if normalized_backend == "real" else "success" if normalized_backend == "vm" else "subtle"
        )
        self.status_panel.set_item("backend", backend_text, backend_tone)

    def _warn(self, message):
        print(message)
        QMessageBox.warning(self, "BBA", message)

    def _apply_runtime_selection(self, machine_id, control_backend):
        if self._scan_is_running():
            self._warn("Stop the current BBA scan before switching machine or backend.")
            return

        request_runtime_restart(
            self,
            app_label="BBA",
            current_machine_id=self.machine_profile.machine.id,
            current_control_backend=self.app_context.control_backend.name,
            machine_id=machine_id,
            control_backend=control_backend,
        )

    def _scan_is_running(self):
        return self.scan is not None and self.scan.isRunning()

    def _attach_scan(self, thread, display_handler):
        self.scan = thread
        thread.trigger.connect(display_handler)
        thread.finished.connect(self._on_scan_finished)
        thread.start()
        self._refresh_status()

    def _on_scan_finished(self):
        self.scan = None
        self.scan_mode = None
        self.scan_family = None
        self._refresh_status()

    def _start_clear(self, display_handler):
        self.clear = ClearThread()
        self.clear.trigger.connect(display_handler)
        self.clear.start()

    def _validate_positive_int(self, value, field_name):
        if value <= 0:
            raise ValueError(f"{field_name} must be a positive integer.")

    def _validate_non_negative_float(self, value, field_name):
        if value < 0:
            raise ValueError(f"{field_name} must be non-negative.")

    def get_setting(self):
        try:
            params = ScanParameters()
            params.corr = self.comboBox.currentText()
            params.quad = self.comboBox_2.currentText()
            params.bpm1 = self.comboBox_3.currentText()
            params.bpm2 = self.comboBox_4.currentText()
            params.plane = self._normalize_plane_value(self.comboBox_5.currentText())

            mode = self._profile_default_control_backend()
            self._require_family_control_backend(self.bba_workflow.standard, mode, "BBA-1")
            bpm_channel = self._bpm_logical_channel(params.plane)
            params.corrPV = resolve_channel(self.app_context, params.corr, "setpoint", mode)
            params.quadPV = resolve_channel(self.app_context, params.quad, "k1", mode)
            params.bpm1PV = resolve_channel(self.app_context, params.bpm1, bpm_channel, mode)
            params.bpm2PV = resolve_channel(self.app_context, params.bpm2, bpm_channel, mode)
            params.control_backend = mode

            params.corr_from = float(self.lineEdit.text())
            params.corr_end = float(self.lineEdit_2.text())
            params.corr_steps = int(self.lineEdit_3.text())
            params.quad_from = float(self.lineEdit_6.text())
            params.quad_end = float(self.lineEdit_4.text())
            params.quad_steps = int(self.lineEdit_5.text())
            params.samples = int(self.lineEdit_8.text())
            params.sleeptime = float(self.lineEdit_7.text())

            self._validate_positive_int(params.corr_steps, "Corrector steps")
            self._validate_positive_int(params.quad_steps, "Quad steps")
            self._validate_positive_int(params.samples, "Samples per step")
            self._validate_non_negative_float(params.sleeptime, "Sleep time")
            return params
        except ValueError as exc:
            self._warn(str(exc))
            return None

    def get_setting_bba2(self):
        try:
            params = ScanParameters()
            params.quad = self.comboBox_7.currentText()
            params.corr = self.comboBox_9.currentText()
            params.bpm1 = self.comboBox_8.currentText()
            params.bpm2 = self.comboBox_6.currentText()
            params.plane = self._normalize_plane_value(self.comboBox_10.currentText())
            params.control_backend = self._normalize_control_backend_value(self.comboBox_11.currentText())
            self._require_family_control_backend(self.bba_workflow.bba2, params.control_backend, "BBA-2")
            bpm_channel = self._bpm_logical_channel(params.plane)
            params.quadPV = resolve_channel(self.app_context, params.quad, "k1", params.control_backend)
            params.corrPV = resolve_channel(self.app_context, params.corr, "setpoint", params.control_backend)
            params.bpm1PV = resolve_channel(self.app_context, params.bpm1, bpm_channel, params.control_backend)
            params.bpm2PV = resolve_channel(self.app_context, params.bpm2, bpm_channel, params.control_backend)

            params.quad_from = float(self.lineEdit_14.text())
            params.quad_end = float(self.lineEdit_17.text())
            params.quad_steps = int(self.lineEdit_16.text())
            params.corr_from = float(self.lineEdit_11.text())
            params.corr_end = float(self.lineEdit_13.text())
            params.corr_steps = int(self.lineEdit_12.text())
            params.samples = int(self.lineEdit_9.text())
            params.sleeptime = float(self.lineEdit_15.text())
            params.energy_mev = float(self.lineEdit_20.text())
            params.bpm1_samples = int(self.lineEdit_22.text())
            params.by_formula = self.lineEdit_23.text()
            params.bx_formula = self.lineEdit_24.text()
            params.leff_by = float(self.lineEdit_25.text())
            params.leff_bx = float(self.lineEdit_26.text())
            params.app_context = self.app_context

            self._validate_positive_int(params.quad_steps, "Quad steps")
            self._validate_positive_int(params.corr_steps, "Corrector steps")
            self._validate_positive_int(params.samples, "Samples per step")
            self._validate_positive_int(params.bpm1_samples, "BPM1 sample count")
            self._validate_non_negative_float(params.sleeptime, "Sleep time")
            if params.energy_mev <= 0:
                raise ValueError("Energy must be positive.")
            return params
        except ValueError as exc:
            self._warn(str(exc))
            return None

    def startScan(self):
        if self._scan_is_running():
            self._warn("A BBA scan is already running. Stop it before starting another one.")
            return

        params = self.get_setting()
        if params is None:
            return

        self.clearPlot()
        params.recal = False
        self.scan_mode = "scan"
        self.scan_family = "bba1"
        self._attach_scan(BBAScanThread(params), self.display)

    def startScan_bba2(self):
        if self._scan_is_running():
            self._warn("A BBA scan is already running. Stop it before starting another one.")
            return

        params = self.get_setting_bba2()
        if params is None:
            return

        self.clearPlot_bba2()
        params.recal = False
        self.scan_mode = "scan"
        self.scan_family = "bba2"
        self._attach_scan(BBAScanThreadBBA2(params), self.display_bba2)

    def stopScan(self):
        if self._scan_is_running():
            self.scan.stop()
            if not self.scan.wait(3000):
                print("Timed out waiting for BBA scan thread to stop.")
            print("Scan thread is stopped.")
        self.scan = None
        self.scan_mode = None
        self.scan_family = None
        self._refresh_status()

    def recalculate(self):
        if self._scan_is_running():
            self._warn("A BBA scan is already running. Stop it before recalculating.")
            return

        params = self.get_setting()
        if params is None:
            return

        params.recal = True
        self.scan_mode = "recalculate"
        self.scan_family = "bba1"
        self._attach_scan(BBAScanThread(params), self.display)

    def recalculate_bba2(self):
        if self._scan_is_running():
            self._warn("A BBA scan is already running. Stop it before recalculating.")
            return

        params = self.get_setting_bba2()
        if params is None:
            return

        self.clearPlot_bba2()
        params.recal = True
        self.scan_mode = "recalculate"
        self.scan_family = "bba2"
        self._attach_scan(BBAScanThreadBBA2(params), self.display_bba2)

    def clearPlot(self):
        self._start_clear(self.display)

    def clearPlot_bba2(self):
        self._start_clear(self.display_bba2)

    def display(self, data):
        if "error" in data:
            self._warn(data["error"])
            return

        if "clear" in data:
            self._draw_placeholder(self.widget, "$K_1L_q$", "BPM2 (mm)", "Waiting for BBA-1 scan points")
            self._draw_placeholder(self.widget_2, "BPM1 (mm)", "S", "Waiting for BBA-1 fit")
            self.lineEdit_10.setText("")
            self._refresh_status()
            return

        palette = self._palette()
        show_type = data.get("show")
        if show_type == "k1m2":
            if not self.widget.axes.lines:
                self.widget.axes.clear()
                self._style_axes(self.widget, "$K_1L_q$", "BPM2 (mm)")
            self.widget.axes.plot(
                data["K1Lq"],
                data["m2"],
                marker="x",
                linestyle="None",
                color=palette["plot_point"],
            )
            self.widget.canvas.draw()
        elif show_type == "fit_k1m2":
            self.widget.axes.plot(data["x"], data["y"], linestyle="--", color=palette["plot_fit"])
            self.widget.canvas.draw()

            m1 = data["m1"]
            slope = data["S"]
            mm1 = data["mm1"]
            self.widget_2.axes.clear()
            self._style_axes(self.widget_2, "BPM1 (mm)", "S")
            self.widget_2.axes.plot(mm1, np.ones(len(mm1)) * slope, marker="x", linestyle="None", color=palette["plot_point"])
            self.widget_2.axes.plot(m1, slope, marker="o", linestyle="None", color=palette["plot_fit"])
            self.widget_2.canvas.draw()
        elif show_type == "m1S":
            self.widget_2.axes.clear()
            self._style_axes(self.widget_2, "BPM1 (mm)", "S")
            self.widget_2.axes.plot(data["m1"], data["S"], marker="o", linestyle="None", color=palette["plot_point"])
            self.widget_2.axes.plot(data["m1"], data["yvals"], linestyle="-", color=palette["plot_fit"])
            self.widget_2.canvas.draw()
            self.lineEdit_10.setText(str(round(data["offset"], 1)))
        self._refresh_status()

    def display_bba2(self, data):
        if "error" in data:
            self._warn(data["error"])
            return

        if "clear" in data:
            self._draw_placeholder(self.widget_3, "$K_1L_q$", "BPM2 (mm)", "Waiting for BBA-2 quad scan")
            self._draw_placeholder(self.widget_4, "corrector kick (mrad)", "BPM2 (mm)", "Waiting for BBA-2 corrector scan")
            self.lineEdit_18.setText("")
            self.lineEdit_19.setText("")
            self.lineEdit_19.setToolTip("")
            self.lineEdit_21.setText("")
            self._refresh_status()
            return

        palette = self._palette()
        show_type = data.get("show")
        if show_type == "k1m2":
            if not self.widget_3.axes.lines:
                self.widget_3.axes.clear()
                self._style_axes(self.widget_3, "$K_1L_q$", "BPM2 (mm)")
            self.widget_3.axes.plot(
                data["K1Lq"],
                np.asarray(data["m2"]) * 1e3,
                marker="x",
                linestyle="None",
                color=palette["plot_point"],
            )
            self.widget_3.canvas.draw()
        elif show_type == "fit_k1m2":
            self.widget_3.axes.plot(data["x"], np.asarray(data["y"]) * 1e3, linestyle="--", color=palette["plot_fit"])
            self.widget_3.canvas.draw()
        elif show_type == "thetam2":
            if not self.widget_4.axes.lines:
                self.widget_4.axes.clear()
                self._style_axes(self.widget_4, "corrector kick (mrad)", "BPM2 (mm)")
            self.widget_4.axes.plot(
                np.asarray(data["theta"]) * 1e3,
                np.asarray(data["m2"]) * 1e3,
                marker="x",
                linestyle="None",
                color=palette["plot_point"],
            )
            self.widget_4.canvas.draw()
        elif show_type == "fit_thetam2":
            self.widget_4.axes.plot(
                np.asarray(data["x"]) * 1e3,
                np.asarray(data["y"]) * 1e3,
                linestyle="--",
                color=palette["plot_fit"],
            )
            self.widget_4.canvas.draw()
            self.lineEdit_21.setText(str(data["m1_ave"] * 1e3))
            self.lineEdit_19.setText(str(data["R12"]))
            tooltip_lines = [f"Measured R12: {data['R12']:.6g} m"]
            model_r12 = data.get("model_R12")
            if model_r12 is not None:
                tooltip_lines.append(f"Model R12: {model_r12:.6g} m")
            model_r12_error = data.get("model_R12_error")
            if model_r12_error:
                tooltip_lines.append(f"Model R12 unavailable: {model_r12_error}")
            self.lineEdit_19.setToolTip("\n".join(tooltip_lines))
            self.lineEdit_18.setText(str(data["b1q1"] * 1e3))
        self._refresh_status()

    def closeEvent(self, event):
        self.stopScan()
        event.accept()


class BBABaseThread(QThread):
    trigger = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.is_running = True

    def stop(self):
        self.is_running = False

    def _emit(self, payload):
        self.trigger.emit(dict(payload))

    def _safe_get(self, pv, label):
        value = pv.get()
        if value is None:
            raise RuntimeError(f"Failed to read {label}.")
        return value

    def _safe_put(self, pv, value):
        if value is not None:
            pv.put(value)

    def _sleep_or_stop(self, seconds):
        if seconds <= 0:
            return self.is_running
        end_time = time.time() + seconds
        while self.is_running and time.time() < end_time:
            time.sleep(min(0.1, end_time - time.time()))
        return self.is_running

    def _load_two_column(self, path, label):
        if not path.exists():
            raise RuntimeError(f"{label} not found: {path}")
        data = np.loadtxt(path, ndmin=2)
        if data.shape[1] < 2:
            raise RuntimeError(f"{label} is malformed: {path}")
        return np.asarray(data[:, 0], dtype=float), np.asarray(data[:, 1], dtype=float)

    def _load_one_column(self, path, label):
        if not path.exists():
            raise RuntimeError(f"{label} not found: {path}")
        data = np.atleast_1d(np.loadtxt(path))
        return np.asarray(data, dtype=float)


class BBAScanThread(BBABaseThread):
    def __init__(self, params):
        super().__init__()
        self.params = params

    def run(self):
        try:
            if self.params.recal:
                x, y = self._load_two_column(M1S_PATH, "BBA-1 recalculation data")
            else:
                scan_result = self._perform_scan()
                if scan_result is None:
                    return
                x, y = scan_result

            if len(x) < 2:
                raise RuntimeError("Need at least two BBA-1 points to fit the offset.")

            coeff = np.polyfit(x, y, deg=1)
            if np.isclose(coeff[0], 0.0):
                raise RuntimeError("BBA-1 slope is zero; cannot compute offset.")

            fit = np.poly1d(coeff)
            self._emit({
                "show": "m1S",
                "m1": x,
                "S": y,
                "yvals": fit(x),
                "offset": coeff[1] / coeff[0],
            })
        except Exception as exc:
            self._emit({"error": str(exc)})

    def _perform_scan(self):
        cor = epics.PV(self.params.corrPV)
        quad = epics.PV(self.params.quadPV)
        bpm1 = epics.PV(self.params.bpm1PV)
        bpm2 = epics.PV(self.params.bpm2PV)
        sign = 1 if self.params.plane == "X" else -1

        k1_values = np.linspace(self.params.quad_from, self.params.quad_end, self.params.quad_steps)
        kick_values = np.linspace(self.params.corr_from, self.params.corr_end, self.params.corr_steps)

        initial_quad = self._safe_get(quad, self.params.quadPV)
        initial_kick = self._safe_get(cor, self.params.corrPV)
        print("ini values of the quad and corrector=", initial_quad, initial_kick)

        m1_results = []
        slope_results = []

        try:
            for kick in kick_values:
                if not self.is_running:
                    return None
                self._safe_put(cor, kick)

                bpm2_samples = []
                bpm1_samples = []
                for k1 in k1_values:
                    if not self.is_running:
                        return None
                    self._safe_put(quad, k1)

                    for _ in range(self.params.samples):
                        print("cor-kick,K1=", kick, k1)
                        if not self._sleep_or_stop(self.params.sleeptime):
                            return None

                        bpm2_value = self._safe_get(bpm2, self.params.bpm2PV)
                        bpm1_value = self._safe_get(bpm1, self.params.bpm1PV)
                        bpm2_samples.append(bpm2_value)
                        bpm1_samples.append(bpm1_value)

                        self._emit({
                            "show": "k1m2",
                            "K1Lq": k1 * sign * K1LQ_FACTOR,
                            "m2": bpm2_value,
                        })
                        if not self._sleep_or_stop(1):
                            return None

                bpm2_matrix = np.asarray(bpm2_samples, dtype=float).reshape(self.params.quad_steps, self.params.samples)
                bpm2_mean = np.mean(bpm2_matrix, axis=1)
                bpm1_mean = float(np.mean(np.asarray(bpm1_samples, dtype=float)))

                x = sign * k1_values * K1LQ_FACTOR
                coeff = np.polyfit(x, bpm2_mean, deg=1)
                fit = np.poly1d(coeff)

                slope = float(coeff[0])
                m1_results.append(bpm1_mean)
                slope_results.append(slope)

                self._emit({
                    "show": "fit_k1m2",
                    "x": x,
                    "y": fit(x),
                    "m1": bpm1_mean,
                    "S": slope,
                    "mm1": np.asarray(bpm1_samples, dtype=float),
                })
                if not self._sleep_or_stop(1):
                    return None

            np.savetxt(M1S_PATH, np.column_stack((m1_results, slope_results)), fmt="%.6e")
            print("Scan finished, corrector and quad are back to initial values.")
            return np.asarray(m1_results, dtype=float), np.asarray(slope_results, dtype=float)
        finally:
            self._safe_put(quad, initial_quad)
            self._safe_put(cor, initial_kick)


class BBAScanThreadBBA2(BBABaseThread):
    def __init__(self, params):
        super().__init__()
        self.params = params
        self.S = None
        self.m1_ave = None
        self.R12 = None
        self.model_backend = None
        self.model_r12 = None
        self.model_r12_error = None
        self.initial_quad_k1 = None

    def run(self):
        try:
            cor = epics.PV(self.params.corrPV)
            quad = epics.PV(self.params.quadPV)
            bpm1 = epics.PV(self.params.bpm1PV)
            bpm2 = epics.PV(self.params.bpm2PV)
            print(cor, quad, bpm1, bpm2)
            if self.params.app_context is not None:
                self.model_backend = build_model_backend(
                    self.params.app_context,
                    energy_mev=self.params.energy_mev,
                )

            sign = -1 if self.params.plane == "X" else 1
            kick_values = np.linspace(self.params.corr_from, self.params.corr_end, self.params.corr_steps)
            angle_values = self._calculate_kick_angles(kick_values)

            if self.params.recal:
                k1_lq, quad_m2 = self._load_two_column(BBA2_K1LQM2_PATH, "BBA-2 quad scan data")
                self._emit({"show": "k1m2", "K1Lq": k1_lq, "m2": quad_m2})
                if not self._sleep_or_stop(1):
                    return
            else:
                quad_scan = self._perform_quad_scan(quad, bpm2, sign)
                if quad_scan is None:
                    return
                k1_lq, quad_m2 = quad_scan

            theta_scan = self._fit_quad_scan(k1_lq, quad_m2)
            self._emit(theta_scan)
            if not self._sleep_or_stop(1):
                return

            if self.params.recal:
                bpm1_values = self._load_one_column(BBA2_M1_PATH, "BBA-2 BPM1 data")
            else:
                bpm1_values = self._measure_bpm1(bpm1)
                if bpm1_values is None:
                    return
            self.m1_ave = float(np.mean(bpm1_values))
            print("m1_ave=", self.m1_ave * 1e3, "mm")

            if self.params.recal:
                theta, corr_m2 = self._load_two_column(BBA2_THETAM2_PATH, "BBA-2 corrector scan data")
                self._emit({"show": "thetam2", "theta": theta, "m2": corr_m2})
                if not self._sleep_or_stop(1):
                    return
            else:
                corrector_scan = self._perform_corrector_scan(cor, bpm2, kick_values, angle_values)
                if corrector_scan is None:
                    return
                theta, corr_m2 = corrector_scan

            self._emit(self._fit_corrector_scan(theta, corr_m2))
            if not self._sleep_or_stop(1):
                return
        except Exception as exc:
            self._emit({"error": str(exc)})

    def _perform_quad_scan(self, quad, bpm2, sign):
        k1_values = np.linspace(self.params.quad_from, self.params.quad_end, self.params.quad_steps)
        initial_quad = self._safe_get(quad, self.params.quadPV)
        try:
            self.initial_quad_k1 = float(initial_quad)
        except (TypeError, ValueError):
            self.initial_quad_k1 = None
        print("ini values of the quad=", initial_quad)

        k1_samples = []
        bpm2_samples = []
        try:
            for k1 in k1_values:
                if not self.is_running:
                    return None
                self._safe_put(quad, k1)

                for _ in range(self.params.samples):
                    if not self._sleep_or_stop(self.params.sleeptime):
                        return None
                    bpm2_value = self._safe_get(bpm2, self.params.bpm2PV)
                    print("K1=", k1, "bpm2=", bpm2_value)
                    bpm2_samples.append(bpm2_value)
                    k1_samples.append(k1)

                    self._emit({
                        "show": "k1m2",
                        "K1Lq": k1 * sign * K1LQ_FACTOR,
                        "m2": bpm2_value,
                    })
                    if not self._sleep_or_stop(1):
                        return None

            k1_lq = sign * np.asarray(k1_samples, dtype=float) * K1LQ_FACTOR
            m2 = np.asarray(bpm2_samples, dtype=float)
            np.savetxt(BBA2_K1LQM2_PATH, np.column_stack((k1_lq, m2)), fmt="%.6e")
            print("First scan finished, quad is back to initial values,", initial_quad)
            return k1_lq, m2
        finally:
            self._safe_put(quad, initial_quad)

    def _fit_quad_scan(self, k1_lq, m2):
        k1_matrix = np.asarray(k1_lq, dtype=float).reshape(int(len(k1_lq) / self.params.samples), self.params.samples)
        k1_mean = np.mean(k1_matrix, axis=1)
        m2_matrix = np.asarray(m2, dtype=float).reshape(int(len(m2) / self.params.samples), self.params.samples)
        m2_mean = np.mean(m2_matrix, axis=1)

        coeff = np.polyfit(k1_mean, m2_mean, deg=1)
        self.S = float(coeff[0])
        print("S=", self.S)
        return {
            "show": "fit_k1m2",
            "x": k1_mean,
            "y": np.poly1d(coeff)(k1_mean),
        }

    def _measure_bpm1(self, bpm1):
        samples = []
        print("get average BPM1 <m1> now:")
        for _ in range(self.params.bpm1_samples):
            if not self.is_running:
                print("BPM1 scan stop.")
                return None
            value = self._safe_get(bpm1, self.params.bpm1PV)
            samples.append(value)
            print("BPM1 m1=", value * 1e3, "mm")
            if not self._sleep_or_stop(2):
                return None
        np.savetxt(BBA2_M1_PATH, np.asarray(samples, dtype=float), fmt="%.6e")
        return np.asarray(samples, dtype=float)

    def _perform_corrector_scan(self, cor, bpm2, kick_values, angle_values):
        initial_kick = self._safe_get(cor, self.params.corrPV)
        print("ini values of the corrector=", initial_kick)

        theta_samples = []
        bpm2_samples = []
        try:
            for idx, kick in enumerate(kick_values):
                if not self.is_running:
                    return None
                self._safe_put(cor, kick)
                for _ in range(self.params.samples):
                    if not self._sleep_or_stop(self.params.sleeptime):
                        return None
                    bpm2_value = self._safe_get(bpm2, self.params.bpm2PV)
                    print("corrector=", kick, "bpm2=", bpm2_value)
                    bpm2_samples.append(bpm2_value)
                    theta_samples.append(angle_values[idx])

                    self._emit({
                        "show": "thetam2",
                        "theta": angle_values[idx],
                        "m2": bpm2_value,
                    })
                    if not self._sleep_or_stop(1):
                        return None

            theta = np.asarray(theta_samples, dtype=float)
            m2 = np.asarray(bpm2_samples, dtype=float)
            np.savetxt(BBA2_THETAM2_PATH, np.column_stack((theta, m2)), fmt="%.6e")
            print("Second scan finished, corrector is back to initial values.")
            return theta, m2
        finally:
            self._safe_put(cor, initial_kick)

    def _fit_corrector_scan(self, theta, m2):
        theta_matrix = np.asarray(theta, dtype=float).reshape(int(len(theta) / self.params.samples), self.params.samples)
        theta_mean = np.mean(theta_matrix, axis=1)
        m2_matrix = np.asarray(m2, dtype=float).reshape(int(len(m2) / self.params.samples), self.params.samples)
        m2_mean = np.mean(m2_matrix, axis=1)

        coeff = np.polyfit(theta_mean, m2_mean, deg=1)
        self.R12 = float(coeff[0])
        if np.isclose(self.R12, 0.0):
            raise RuntimeError("BBA-2 corrector fit slope is zero; cannot compute offset.")
        if self.S is None or self.m1_ave is None:
            raise RuntimeError("BBA-2 fit inputs are incomplete.")

        self.model_r12 = self._calculate_model_r12()
        b1q1 = self.S / self.R12 - self.m1_ave
        print(self.S, self.R12, self.m1_ave)
        result = {
            "show": "fit_thetam2",
            "x": theta_mean,
            "y": np.poly1d(coeff)(theta_mean),
            "m1_ave": self.m1_ave,
            "R12": self.R12,
            "b1q1": b1q1,
        }
        if self.model_r12 is not None:
            result["model_R12"] = self.model_r12
        if self.model_r12_error:
            result["model_R12_error"] = self.model_r12_error
        return result

    def _calculate_kick_angles(self, kick_values):
        if self.params.control_backend == "vm":
            print("Virtual Machine.")
            return np.asarray(kick_values, dtype=float)

        if self.params.plane == "X":
            field = self._evaluate_formula(self.params.by_formula, kick_values) * 1e-4
            effective_length = self.params.leff_by
        elif self.params.plane == "Y":
            field = self._evaluate_formula(self.params.bx_formula, kick_values) * 1e-4
            effective_length = self.params.leff_bx
        else:
            raise RuntimeError("Plane should be X or Y.")

        return 299.8 / self.params.energy_mev * field * effective_length

    def _calculate_model_r12(self):
        if self.model_backend is None:
            self.model_r12_error = "model backend is not configured"
            return None

        if self.params.plane == "X":
            row, col = 0, 1
        elif self.params.plane == "Y":
            row, col = 2, 3
        else:
            raise RuntimeError("Plane should be X or Y.")

        overrides = None
        if self.initial_quad_k1 is not None:
            overrides = {self.params.quad: self.initial_quad_k1}

        try:
            return self.model_backend.get_matrix_element(
                self.params.corr,
                self.params.bpm2,
                row,
                col,
                element_overrides=overrides,
            )
        except Exception as exc:
            self.model_r12_error = str(exc)
            return None

    def _evaluate_formula(self, formula, current_values):
        try:
            result = eval(
                formula,
                {"__builtins__": {}},
                {"current": np.asarray(current_values, dtype=float), "np": np},
            )
        except Exception as exc:
            raise RuntimeError(f"Invalid corrector formula '{formula}': {exc}") from exc
        return np.asarray(result, dtype=float)


class ClearThread(QThread):
    trigger = pyqtSignal(dict)

    def run(self):
        self.trigger.emit({"clear": True})


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = myWindow()
    window.show()
    sys.exit(app.exec_())
