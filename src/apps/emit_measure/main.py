
import sys
import epics
import time
import json
import numpy as np
import math
from pathlib import Path
from datetime import datetime

_REPO_BOOTSTRAP_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file()
)
if str(_REPO_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_BOOTSTRAP_ROOT))

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

import matplotlib as mpl
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit
from collections import defaultdict
from scipy.stats import truncnorm

from gui import Ui_Form
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QFileDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from half_linac.src.shared.machine_profile import (
    MachineProfileError,
    build_model_backend,
    get_emit_preset,
    load_app_context,
    require_workflow_write_allowed,
    resolve_channel,
)

nest_dict    = lambda: defaultdict(nest_dict)

ELECTRON_MASS_EV = 0.51099895000e6
SCAN_RESULTS_PATH    = Path(__file__).resolve().parent / "scanResults.txt"
SCAN_RESULTS_META_PATH = Path(__file__).resolve().parent / "scanResults.meta.json"
SCAN_ARCHIVE_ROOT = Path(__file__).resolve().parent / "runtime" / "scans"
SCAN_DATA_SCHEMA_VERSION = "emit_scan_v1"
SCAN_POINT_COLUMNS = ("Use", "K1", "sigx", "sigy")

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
    "plot_error": "#8ac9a2",
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
    "plot_error": "#6aa17a",
    "status_strip_bg": "#f7f1e8",
    "status_strip_border": "#ddd2c4",
    "status_separator": "#ddd4c7",
    "status_item_idle_bar": "#c8bfb3",
    "status_title_fg": "#7c7368",
    "metric_active_fg": "#2d7f6d",
    "metric_warning_fg": "#a97118",
    "metric_idle_fg": "#4e5a62",
}


def build_emit_measure_theme(palette):
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

QFrame#plotCard, QWidget#controlCard, QWidget#resultCard {{
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

QLineEdit, QComboBox, QTextEdit {{
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

QTableWidget {{
    background-color: {input_bg};
    border: 1px solid {input_border};
    border-radius: 10px;
    color: {input_fg};
    gridline-color: {panel_border};
    selection-background-color: {button_hover_bg};
    selection-color: {input_fg};
}}

QTableWidget::item {{
    padding: 3px 6px;
}}

QHeaderView::section {{
    background-color: {button_bg};
    border: none;
    border-right: 1px solid {button_border};
    border-bottom: 1px solid {button_border};
    color: {button_fg};
    padding: 4px 6px;
    font-size: 11px;
    font-weight: 700;
}}

QRadioButton {{
    color: {window_fg};
    font-size: 12px;
    font-weight: 600;
    spacing: 8px;
}}

QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {panel_border};
    border-radius: 8px;
    background-color: {input_bg};
}}

QRadioButton::indicator:checked {{
    background-color: {metric_active_fg};
    border: 2px solid {window_fg};
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


class EmitStatusStrip(QWidget):
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


class structData:
    def __init__(self):
        pass
        
class myWindow(QWidget,Ui_Form):
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        self.app_context = load_app_context("emit_measure")
        self.machine_profile = self.app_context.profile
        self.emit_workflow = self.app_context.emit_measure_workflow
        if self.emit_workflow is None:
            raise ValueError("Emit measure workflow is not available in the current app context.")

        self.current_theme = "dark"
        self.machine_type = self.app_context.control_backend.name
        self.scan_mode = None

        # default settings 
        # ----------------
        self.lineEdit_2.setText("2200") # energy=2200MeV
        self.lineEdit_24.setText("5") # freq time=5s
        self.lineEdit_7.setText("0")  # K1-start
        self.lineEdit_8.setText("5")  # K1-end 
        self.lineEdit_9.setText("15") # steps=15
        self.lineEdit_10.setText("5") # samples=5 

        self.scan = None
        self.twissCal = None
        self.clear = None
        self.scan_points_table = None
        self.scan_points_summary_label = None
        self.loaded_scan_metadata = None
        self.loaded_scan_results_path = None
        self.pending_scan_metadata = None
        self._plot_wrappers = {}
        self._result_fields = []
        self._configure_window()
        self._build_shell()
        self._configure_form_content()

        # basic function
        self.pushButton.clicked.connect(self.startScan)
        self.pushButton_2.clicked.connect(self.recalculate)
        self.pushButton_3.clicked.connect(self.clearPlot)
        self.pushButton_4.clicked.connect(self.start_twissCalc)
        self.pushButton_5.clicked.connect(self.stopScan)

        # other function
        self.comboBox.currentIndexChanged.connect(self.updateComboBox4)
        self.comboBox_4.currentIndexChanged.connect(self._sync_emit_preset_defaults)
        self.tabWidget.currentChanged.connect(self._refresh_status)
        # self.pushButton_6.clicked.connect(self.simply_VM)
        # self.pushButton_7.clicked.connect(self.full_VM)

        self._configure_machine_profile()
        self._apply_theme()
        self._draw_placeholder_plots()
        self._refresh_status()

    def _configure_window(self):
        self.setWindowTitle(f"{self.machine_profile.machine.display_name} Emit Measure")
        self.resize(1600, 940)
        self.setMinimumSize(1320, 820)

    def _build_shell(self):
        self.verticalLayout.setContentsMargins(10, 10, 10, 10)
        self.verticalLayout.setSpacing(12)
        self.tabWidget.setDocumentMode(True)
        self.tabWidget.setElideMode(Qt.ElideNone)
        self._attach_tab_roots()
        self.gridLayout_2.setRowStretch(0, 4)
        self.gridLayout_2.setRowStretch(1, 1)
        self.gridLayout_4.setRowStretch(0, 4)
        self.gridLayout_4.setRowStretch(1, 1)

        self._build_summary_panel()
        self._style_plot_cards()
        self._style_control_cards()

    def _attach_tab_roots(self):
        if self.X_Plane.layout() is self.gridLayout:
            self.gridLayout.setContentsMargins(0, 0, 0, 0)
        if self.tab_2.layout() is None:
            tab_layout = QVBoxLayout(self.tab_2)
            tab_layout.setContentsMargins(0, 0, 0, 0)
            tab_layout.setSpacing(0)
            tab_layout.addWidget(self.layoutWidget_2)

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

        title = QLabel("Emit Measure", panel)
        title.setObjectName("summaryTitle")
        header_layout.addWidget(title)
        header_layout.addStretch(1)

        self.theme_toggle_button = QToolButton(panel)
        self.theme_toggle_button.setObjectName("themeToggleButton")
        self.theme_toggle_button.setFixedSize(HEADER_ACTION_HEIGHT, HEADER_ACTION_HEIGHT)
        self.theme_toggle_button.clicked.connect(self._toggle_theme)
        header_layout.addWidget(self.theme_toggle_button)

        outer_layout.addLayout(header_layout)

        self.status_panel = EmitStatusStrip(panel)
        self.status_panel.add_item("mode", "MODE", self.machine_type.upper())
        self.status_panel.add_item("tab", "TAB", self.tabWidget.tabText(self.tabWidget.currentIndex()))
        self.status_panel.add_item("scan", "SCAN", "Idle")
        self.status_panel.add_item("twiss", "TWISS", "Idle")
        self.status_panel.add_item("data", "DATA", "No scan file")
        self.status_panel.finish()
        self.status_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        outer_layout.addWidget(self.status_panel)

        self.verticalLayout.insertWidget(0, panel)

    def _style_plot_cards(self):
        self._wrap_plot_card(
            self.gridLayout_2,
            self.widget,
            "X Sigma Scan",
            0,
            0,
            self.X_Plane,
        )
        self._wrap_plot_card(
            self.gridLayout_2,
            self.widget_2,
            "X Parabolic Fit",
            0,
            1,
            self.X_Plane,
        )
        self._wrap_plot_card(
            self.gridLayout_4,
            self.widget_8,
            "Y Sigma Scan",
            0,
            0,
            self.tab_2,
        )
        self._wrap_plot_card(
            self.gridLayout_4,
            self.widget_9,
            "Y Parabolic Fit",
            0,
            1,
            self.tab_2,
        )

        for widget in (self.widget_3, self.widget_6, self.widget_11, self.widget_12):
            widget.hide()

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
        self.widget_4.setObjectName("controlCard")
        self.widget_5.setObjectName("resultCard")
        self.widget_13.setObjectName("controlCard")
        self.widget_10.setObjectName("resultCard")
        for widget in (self.widget_4, self.widget_5, self.widget_10, self.widget_13):
            widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        self.label_9.setText("Scan Control")
        self.label_15.setText("X Plane Results")
        self.label_42.setText("Y Plane Results")
        self.label_8.setText("Twiss Transport")
        for title in (self.label_9, self.label_15, self.label_42, self.label_8):
            title.setObjectName("panelTitle")

        self.textEdit.hide()
        self.label_3.hide()
        self.gridLayout_2.setAlignment(self.widget_4, Qt.AlignTop)
        self.gridLayout_2.setAlignment(self.widget_5, Qt.AlignTop)
        self.gridLayout_4.setAlignment(self.widget_13, Qt.AlignTop)
        self.gridLayout_4.setAlignment(self.widget_10, Qt.AlignTop)

    def _configure_form_content(self):
        self.pushButton.setText("Start Scan")
        self.pushButton_2.setText("Recalculate")
        self.pushButton_3.setText("Clear")
        self.pushButton_4.setText("Calculate")
        self.pushButton_5.setText("Stop Scan")
        self.radioButton.setText("Inverse Map")
        self.radioButton_2.setText("Y Plane")

        for button in (self.pushButton, self.pushButton_2, self.pushButton_3, self.pushButton_4, self.pushButton_5):
            button.setProperty("compact", True)

        for label in (
            self.label_10,
            self.label_45,
            self.label_22,
            self.label_32,
            self.label_12,
            self.label_20,
            self.label_11,
            self.label_13,
            self.label_14,
            self.label_16,
            self.label_17,
            self.label_18,
            self.label_21,
            self.label_19,
            self.label_23,
            self.label_39,
            self.label_40,
            self.label_41,
            self.label_43,
            self.label_44,
            self.label_47,
            self.label_4,
            self.label_7,
            self.label_49,
            self.label_48,
            self.label_46,
            self.label_50,
            self.label_51,
            self.label_52,
        ):
            label.setProperty("role", "field")

        self._result_fields = [
            self.lineEdit_11, self.lineEdit_12, self.lineEdit_13, self.lineEdit_14, self.lineEdit_15, self.lineEdit_16,
            self.lineEdit_39, self.lineEdit_35, self.lineEdit_40, self.lineEdit_36, self.lineEdit_38, self.lineEdit_37,
            self.lineEdit_4, self.lineEdit_5, self.lineEdit_20, self.lineEdit_19, self.lineEdit_18,
            self.lineEdit_41, self.lineEdit_42, self.lineEdit_43, self.lineEdit_44, self.lineEdit_45,
            self.lineEdit_17, self.lineEdit_21, self.lineEdit_22,
        ]
        for widget in self._result_fields:
            widget.setReadOnly(True)

        self._rebuild_panel_layouts()

    def _rebuild_panel_layouts(self):
        self._rebuild_scan_control_panel()
        self._rebuild_x_results_panel()
        self._rebuild_twiss_control_panel()
        self._rebuild_y_results_panel()

    def _rebuild_scan_control_panel(self):
        layout = QVBoxLayout(self.widget_4)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.label_9.setParent(self.widget_4)
        layout.addWidget(self.label_9)
        self.label_20.hide()

        form = QGridLayout()
        form.setHorizontalSpacing(6)
        form.setVerticalSpacing(5)

        form.addWidget(self.label_10, 0, 0)
        form.addWidget(self.comboBox, 0, 1)
        form.addWidget(self.label_45, 0, 2)
        form.addWidget(self.comboBox_4, 0, 3)
        form.addWidget(self.label_22, 1, 0)
        form.addWidget(self.lineEdit_2, 1, 1)
        form.addWidget(self.label_32, 1, 2)
        form.addWidget(self.lineEdit_24, 1, 3)
        form.addWidget(self.label_11, 2, 0)
        form.addWidget(self.lineEdit_7, 2, 1)
        form.addWidget(self.label_12, 2, 2)
        form.addWidget(self.lineEdit_8, 2, 3)
        form.addWidget(self.label_13, 3, 0)
        form.addWidget(self.lineEdit_9, 3, 1)
        form.addWidget(self.label_14, 3, 2)
        form.addWidget(self.lineEdit_10, 3, 3)
        form.setColumnStretch(1, 1)
        form.setColumnStretch(3, 1)
        layout.addLayout(form)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)

        self.pushButton_2.setParent(self.widget_4)
        for button in (self.pushButton, self.pushButton_5, self.pushButton_3, self.pushButton_2):
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            actions.addWidget(button)

        layout.addLayout(actions)

        points_header = QHBoxLayout()
        points_header.setContentsMargins(0, 4, 0, 0)
        points_header.setSpacing(6)

        points_title = QLabel("Scan Points", self.widget_4)
        points_title.setObjectName("panelTitle")
        points_header.addWidget(points_title)
        points_header.addStretch(1)

        self.scan_points_summary_label = QLabel("0 active / 0 total", self.widget_4)
        self.scan_points_summary_label.setProperty("role", "field")
        points_header.addWidget(self.scan_points_summary_label)
        layout.addLayout(points_header)

        self.scan_points_table = QTableWidget(0, len(SCAN_POINT_COLUMNS), self.widget_4)
        self.scan_points_table.setHorizontalHeaderLabels(SCAN_POINT_COLUMNS)
        self.scan_points_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.scan_points_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.scan_points_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.scan_points_table.setAlternatingRowColors(False)
        self.scan_points_table.setMaximumHeight(170)
        self.scan_points_table.verticalHeader().setVisible(False)
        header = self.scan_points_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for column in range(1, len(SCAN_POINT_COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.Stretch)
        self.scan_points_table.itemChanged.connect(self._on_scan_point_item_changed)
        layout.addWidget(self.scan_points_table)

        point_actions = QHBoxLayout()
        point_actions.setContentsMargins(0, 0, 0, 0)
        point_actions.setSpacing(6)
        self.load_points_button = QPushButton("Load Archive", self.widget_4)
        self.exclude_points_button = QPushButton("Exclude Selected", self.widget_4)
        self.restore_points_button = QPushButton("Restore All", self.widget_4)
        for button in (self.load_points_button, self.exclude_points_button, self.restore_points_button):
            button.setProperty("compact", True)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            point_actions.addWidget(button)
        self.load_points_button.clicked.connect(self._load_scan_archive)
        self.exclude_points_button.clicked.connect(self._exclude_selected_scan_points)
        self.restore_points_button.clicked.connect(self._restore_all_scan_points)
        layout.addLayout(point_actions)

    def _rebuild_x_results_panel(self):
        layout = QVBoxLayout(self.widget_5)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)

        self.layoutWidget.setParent(self.widget_5)
        self.layoutWidget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.gridLayout_3.setVerticalSpacing(6)
        layout.addWidget(self.layoutWidget)

    def _rebuild_twiss_control_panel(self):
        layout = QVBoxLayout(self.widget_13)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.label_8.setParent(self.widget_13)
        layout.addWidget(self.label_8)

        top_row = QGridLayout()
        top_row.setHorizontalSpacing(6)
        top_row.setVerticalSpacing(5)
        top_row.addWidget(self.label_4, 0, 0)
        top_row.addWidget(self.comboBox_2, 0, 1)
        top_row.addWidget(self.label_7, 0, 2)
        top_row.addWidget(self.comboBox_3, 0, 3)
        top_row.setColumnStretch(1, 1)
        top_row.setColumnStretch(3, 1)
        layout.addLayout(top_row)

        grids_row = QHBoxLayout()
        grids_row.setContentsMargins(0, 0, 0, 0)
        grids_row.setSpacing(8)
        self.gridLayoutWidget.setParent(self.widget_13)
        self.gridLayoutWidget_2.setParent(self.widget_13)
        self.gridLayoutWidget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.gridLayoutWidget_2.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.gridLayout_5.setVerticalSpacing(6)
        self.gridLayout_7.setVerticalSpacing(6)
        grids_row.addWidget(self.gridLayoutWidget, 1)
        grids_row.addWidget(self.gridLayoutWidget_2, 1)
        layout.addLayout(grids_row)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        self.radioButton.setParent(self.widget_13)
        self.radioButton_2.setParent(self.widget_13)
        self.pushButton_4.setParent(self.widget_13)
        footer.addWidget(self.radioButton)
        footer.addWidget(self.radioButton_2)
        footer.addStretch(1)
        self.pushButton_4.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        footer.addWidget(self.pushButton_4)
        layout.addLayout(footer)

    def _rebuild_y_results_panel(self):
        layout = QVBoxLayout(self.widget_10)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)

        self.layoutWidget_4.setParent(self.widget_10)
        self.layoutWidget_4.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.gridLayout_6.setVerticalSpacing(6)
        layout.addWidget(self.layoutWidget_4)
        self.widget_7.hide()

    def _palette(self):
        return DARK_THEME if self.current_theme == "dark" else LIGHT_THEME

    def _apply_theme(self):
        palette = self._palette()
        self.setStyleSheet(build_emit_measure_theme(palette))
        if hasattr(self, "status_panel"):
            self.status_panel.apply_theme(palette)
            self.status_panel.setFixedHeight(self.status_panel.sizeHint().height())
        self._update_theme_toggle_button()
        self._style_all_plots()

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
        self._redraw_current_results()
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

    def _style_all_plots(self):
        self._style_axes(self.widget, "$K_1 (m^{-2})$", "sigx (mm)")
        self._style_axes(self.widget_2, "$-K= K_1 L_q (m^{-1})$", "$sigx^2 (mm^2)$")
        self._style_axes(self.widget_8, "$K_1 (m^{-2})$", "sigy (mm)")
        self._style_axes(self.widget_9, "$K= K_1 L_q (m^{-1})$", "$sigy^2 (mm^2)$")
        for plot in (self.widget, self.widget_2, self.widget_8, self.widget_9):
            plot.canvas.draw_idle()

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
        self._draw_placeholder(self.widget, "$K_1 (m^{-2})$", "sigx (mm)", "Waiting for scan points")
        self._draw_placeholder(self.widget_2, "$-K= K_1 L_q (m^{-1})$", "$sigx^2 (mm^2)$", "Waiting for fit")
        self._draw_placeholder(self.widget_8, "$K_1 (m^{-2})$", "sigy (mm)", "Waiting for scan points")
        self._draw_placeholder(self.widget_9, "$K= K_1 L_q (m^{-1})$", "$sigy^2 (mm^2)$", "Waiting for fit")

    def _refresh_status(self):
        if not hasattr(self, "status_panel"):
            return
        mode_tone = "warning" if self.machine_type == "real" else "success"
        self.status_panel.set_item("mode", self.machine_type.upper(), mode_tone)
        self.status_panel.set_item("tab", self.tabWidget.tabText(self.tabWidget.currentIndex()), "subtle")

        if self._scan_is_running():
            scan_text = "Recalculate" if self.scan_mode == "recalculate" else "Running"
            self.status_panel.set_item("scan", scan_text, "success")
        else:
            self.status_panel.set_item("scan", "Idle", "subtle")

        self.status_panel.set_item("twiss", "Running" if self._twiss_is_running() else "Idle", "success" if self._twiss_is_running() else "subtle")
        if SCAN_RESULTS_PATH.exists():
            active, total = self._scan_points_counts()
            if total:
                self.status_panel.set_item("data", f"{active}/{total} points", "success")
            else:
                self.status_panel.set_item("data", SCAN_RESULTS_PATH.name, "success")
        else:
            self.status_panel.set_item("data", "No scan file", "warning")

    def _scan_points_counts(self):
        table = self.scan_points_table
        if table is None:
            return 0, 0
        total = table.rowCount()
        active = 0
        for row in range(total):
            item = table.item(row, 0)
            if item is not None and item.checkState() == Qt.Checked:
                active += 1
        return active, total

    def _update_scan_points_summary(self):
        if self.scan_points_summary_label is None:
            return
        active, total = self._scan_points_counts()
        self.scan_points_summary_label.setText(f"{active} active / {total} total")
        self._refresh_status()

    def _on_scan_point_item_changed(self, item):
        if item.column() != 0:
            return
        self._update_scan_points_summary()
        self._redraw_scan_points_from_table()

    def _clear_scan_points(self):
        if self.scan_points_table is None:
            return
        self.scan_points_table.blockSignals(True)
        self.scan_points_table.setRowCount(0)
        self.scan_points_table.blockSignals(False)
        self._update_scan_points_summary()

    def _append_scan_point(self, k1, sigx, sigy, *, enabled=True):
        table = self.scan_points_table
        if table is None:
            return
        table.blockSignals(True)
        row = table.rowCount()
        table.insertRow(row)

        use_item = QTableWidgetItem("")
        use_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
        use_item.setCheckState(Qt.Checked if enabled else Qt.Unchecked)
        use_item.setTextAlignment(Qt.AlignCenter)
        table.setItem(row, 0, use_item)

        for column, value in enumerate((k1, sigx, sigy), start=1):
            numeric_value = float(value)
            data_item = QTableWidgetItem(f"{numeric_value:.6g}")
            data_item.setData(Qt.UserRole, numeric_value)
            data_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            data_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            table.setItem(row, column, data_item)

        table.blockSignals(False)
        if self.loaded_scan_metadata is None and self.pending_scan_metadata is not None:
            self.loaded_scan_metadata = dict(self.pending_scan_metadata)
            self.loaded_scan_results_path = SCAN_RESULTS_PATH
        self._update_scan_points_summary()

    def _scan_point_value(self, row, column):
        item = self.scan_points_table.item(row, column)
        if item is None:
            raise ValueError(f"Scan point row {row + 1} is incomplete.")
        value = item.data(Qt.UserRole)
        if value is None:
            value = item.text()
        return float(value)

    def _enabled_scan_points(self):
        table = self.scan_points_table
        if table is None:
            return []
        points = []
        for row in range(table.rowCount()):
            use_item = table.item(row, 0)
            if use_item is None or use_item.checkState() != Qt.Checked:
                continue
            points.append((
                self._scan_point_value(row, 1),
                self._scan_point_value(row, 2),
                self._scan_point_value(row, 3),
            ))
        return points

    def _exclude_selected_scan_points(self):
        table = self.scan_points_table
        if table is None:
            return
        rows = sorted({index.row() for index in table.selectedIndexes()})
        if not rows:
            return
        table.blockSignals(True)
        for row in rows:
            item = table.item(row, 0)
            if item is not None:
                item.setCheckState(Qt.Unchecked)
        table.blockSignals(False)
        self._update_scan_points_summary()
        self._redraw_scan_points_from_table()

    def _restore_all_scan_points(self):
        table = self.scan_points_table
        if table is None:
            return
        table.blockSignals(True)
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item is not None:
                item.setCheckState(Qt.Checked)
        table.blockSignals(False)
        self._update_scan_points_summary()
        self._redraw_scan_points_from_table()

    def _scan_archive_dir(self):
        return SCAN_ARCHIVE_ROOT / self.machine_profile.machine.id / self.machine_type

    def _scan_metadata_from_paras(self, paras):
        return {
            "schema_version": SCAN_DATA_SCHEMA_VERSION,
            "machine_id": self.machine_profile.machine.id,
            "machine_display_name": self.machine_profile.machine.display_name,
            "backend": self.machine_type,
            "quad": paras.quad_name,
            "flag": paras.flag_name,
            "model_line": paras.model_line,
            "energy_mev": paras.EnergyMeV,
            "k1_from": paras.k1_from,
            "k1_end": paras.k1_end,
            "k1_steps": paras.k1_steps,
            "samples": paras.samples,
        }

    def _metadata_path_for_results(self, results_path):
        results_path = Path(results_path)
        if results_path.resolve() == SCAN_RESULTS_PATH.resolve():
            return SCAN_RESULTS_META_PATH
        return results_path.with_suffix(".json")

    def _read_scan_metadata(self, results_path):
        metadata_path = self._metadata_path_for_results(results_path)
        if not metadata_path.exists():
            return None
        try:
            return json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid scan metadata file: {metadata_path}") from exc

    def _validate_scan_metadata(self, metadata, expected, source_label):
        if metadata is None:
            print(f"Warning: {source_label} has no metadata; context compatibility cannot be verified.")
            return
        if metadata.get("schema_version") != SCAN_DATA_SCHEMA_VERSION:
            raise RuntimeError(
                f"{source_label} has unsupported metadata schema: {metadata.get('schema_version')!r}."
            )

        mismatches = []
        for key in ("machine_id", "backend", "quad", "flag", "model_line"):
            if metadata.get(key) != expected.get(key):
                mismatches.append(f"{key}: file={metadata.get(key)!r}, current={expected.get(key)!r}")

        try:
            file_energy = float(metadata.get("energy_mev"))
            current_energy = float(expected.get("energy_mev"))
        except (TypeError, ValueError):
            mismatches.append(
                f"energy_mev: file={metadata.get('energy_mev')!r}, current={expected.get('energy_mev')!r}"
            )
        else:
            if abs(file_energy - current_energy) > 1e-9:
                mismatches.append(f"energy_mev: file={file_energy:g}, current={current_energy:g}")

        if mismatches:
            detail = "; ".join(mismatches)
            raise RuntimeError(f"{source_label} does not match current emit settings: {detail}.")

    def _load_scan_results_into_table(self, results_path=SCAN_RESULTS_PATH, *, expected_metadata=None):
        results_path = Path(results_path)
        if not results_path.exists():
            raise RuntimeError(f"{results_path} not found. Run a scan or load an archive before recalculating.")
        metadata = self._read_scan_metadata(results_path)
        if expected_metadata is not None:
            self._validate_scan_metadata(metadata, expected_metadata, str(results_path))
        data = np.loadtxt(results_path, ndmin=2)
        if data.ndim != 2 or data.shape[1] < 3:
            raise RuntimeError(f"{results_path.name} must contain K1, sigx and sigy columns.")
        self._clear_scan_points()
        for k1, sigx, sigy in data[:, :3]:
            self._append_scan_point(k1, sigx, sigy)
        self.loaded_scan_metadata = metadata
        self.loaded_scan_results_path = results_path
        self._redraw_scan_points_from_table()

    def _load_scan_archive(self):
        paras = self.get_setting()
        if paras is None:
            return
        archive_dir = self._scan_archive_dir()
        archive_dir.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Emit Scan Archive",
            str(archive_dir),
            "Emit scan data (*.txt);;All files (*)",
        )
        if not path:
            return
        try:
            self._load_scan_results_into_table(
                Path(path),
                expected_metadata=self._scan_metadata_from_paras(paras),
            )
        except RuntimeError as exc:
            self._warn(str(exc))

    def _redraw_scan_points_from_table(self):
        table = self.scan_points_table
        if table is None:
            return

        active = []
        excluded = []
        for row in range(table.rowCount()):
            point = (
                self._scan_point_value(row, 1),
                self._scan_point_value(row, 2),
                self._scan_point_value(row, 3),
            )
            use_item = table.item(row, 0)
            if use_item is not None and use_item.checkState() == Qt.Checked:
                active.append(point)
            else:
                excluded.append(point)

        palette = self._palette()
        self.widget.axes.clear()
        self._style_axes(self.widget, "$K_1 (m^{-2})$", "sigx (mm)")
        self.widget_8.axes.clear()
        self._style_axes(self.widget_8, "$K_1 (m^{-2})$", "sigy (mm)")

        if excluded:
            k1, sigx, sigy = np.array(excluded).T
            self.widget.axes.plot(k1, sigx, marker="x", linestyle="None", color=palette["muted_fg"], alpha=0.45)
            self.widget_8.axes.plot(k1, sigy, marker="x", linestyle="None", color=palette["muted_fg"], alpha=0.45)
        if active:
            k1, sigx, sigy = np.array(active).T
            self.widget.axes.plot(k1, sigx, marker="x", linestyle="None", color=palette["plot_point"])
            self.widget_8.axes.plot(k1, sigy, marker="x", linestyle="None", color=palette["plot_point"])
        else:
            self.widget.axes.text(
                0.5,
                0.5,
                "No active scan points",
                transform=self.widget.axes.transAxes,
                ha="center",
                va="center",
                color=palette["muted_fg"],
                fontsize=10,
            )
            self.widget_8.axes.text(
                0.5,
                0.5,
                "No active scan points",
                transform=self.widget_8.axes.transAxes,
                ha="center",
                va="center",
                color=palette["muted_fg"],
                fontsize=10,
            )

        self.widget.canvas.draw()
        self.widget_8.canvas.draw()

    def _redraw_current_results(self):
        palette = self._palette()
        has_points = bool(self.widget.axes.lines or self.widget_8.axes.lines)
        has_fits = bool(self.widget_2.axes.lines or self.widget_9.axes.lines)
        if not has_points and not has_fits:
            self._draw_placeholder_plots()
            return
        for plot, xlabel, ylabel in (
            (self.widget, "$K_1 (m^{-2})$", "sigx (mm)"),
            (self.widget_2, "$-K= K_1 L_q (m^{-1})$", "$sigx^2 (mm^2)$"),
            (self.widget_8, "$K_1 (m^{-2})$", "sigy (mm)"),
            (self.widget_9, "$K= K_1 L_q (m^{-1})$", "$sigy^2 (mm^2)$"),
        ):
            self._style_axes(plot, xlabel, ylabel)
            for line in plot.axes.lines:
                if line.get_marker() and line.get_marker() != "None":
                    line.set_color(palette["plot_point"])
                elif line.get_linestyle() and line.get_linestyle() != "None":
                    line.set_color(palette["plot_fit"])
            for collection in plot.axes.collections:
                try:
                    collection.set_color(palette["plot_error"])
                except Exception:
                    pass
            legend = plot.axes.get_legend()
            if legend is not None:
                for text in legend.get_texts():
                    text.set_color(palette["plot_text"])
            plot.canvas.draw()

    def _warn(self, message):
        print(message)
        QMessageBox.warning(self, "Emit Measure", message)

    def _scan_is_running(self):
        return self.scan is not None and self.scan.isRunning()

    def _twiss_is_running(self):
        return self.twissCal is not None and self.twissCal.isRunning()

    def _on_scan_finished(self):
        self.scan = None
        self.scan_mode = None
        self.pending_scan_metadata = None
        self._refresh_status()

    def _on_twiss_finished(self):
        self.twissCal = None
        self._refresh_status()

    def _parse_positive_int(self, text, field_name):
        try:
            value = int(text)
        except ValueError:
            raise ValueError(f"{field_name} must be an integer.")
        if value <= 0:
            raise ValueError(f"{field_name} must be positive.")
        return value

    def _parse_non_negative_float(self, text, field_name):
        try:
            value = float(text)
        except ValueError:
            raise ValueError(f"{field_name} must be numeric.")
        if value < 0:
            raise ValueError(f"{field_name} must be non-negative.")
        return value

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

    def _emit_presets_by_quad(self):
        grouped = defaultdict(list)
        for preset in self.emit_workflow.presets:
            grouped[preset.quad].append(preset)
        return grouped

    def _find_emit_preset(self, preset_id):
        return get_emit_preset(self.app_context, preset_id)

    def _current_emit_preset(self):
        quad_name = self.comboBox.currentText()
        flag_name = self.comboBox_4.currentText()
        for preset in self.emit_workflow.presets:
            if preset.quad == quad_name and preset.flag == flag_name:
                return preset
        return None

    def _twiss_quad_choices(self):
        if self.emit_workflow.twiss_quads:
            return list(self.emit_workflow.twiss_quads)

        choices = []
        for preset in self.emit_workflow.presets:
            if preset.quad not in choices:
                choices.append(preset.quad)
        return choices

    def _configure_machine_profile(self):
        presets_by_quad = self._emit_presets_by_quad()
        quad_items = list(presets_by_quad)
        self._set_combo_items(self.comboBox, quad_items)

        twiss_quads = self._twiss_quad_choices()
        self._set_combo_items(self.comboBox_2, twiss_quads)
        self._set_combo_items(self.comboBox_3, twiss_quads)

        default_preset = self._find_emit_preset(self.emit_workflow.default_preset)
        self._set_combo_current_text(self.comboBox, default_preset.quad)
        self.updateComboBox4(self.comboBox.currentIndex())
        self._set_combo_current_text(self.comboBox_4, default_preset.flag)
        if twiss_quads:
            self._set_combo_current_text(self.comboBox_2, twiss_quads[0])
            self._set_combo_current_text(self.comboBox_3, twiss_quads[0])
        self._sync_emit_preset_defaults()

    def _sync_emit_preset_defaults(self):
        preset = self._current_emit_preset()
        if preset is None:
            return
        if preset.energy_mev is not None:
            self.lineEdit_2.setText(str(preset.energy_mev))
        scan = preset.scan
        if scan.k1_from is not None:
            self.lineEdit_7.setText(str(scan.k1_from))
        if scan.k1_end is not None:
            self.lineEdit_8.setText(str(scan.k1_end))
        if scan.k1_steps is not None:
            self.lineEdit_9.setText(str(scan.k1_steps))
        if scan.samples is not None:
            self.lineEdit_10.setText(str(scan.samples))
        if scan.sleeptime is not None:
            self.lineEdit_24.setText(str(scan.sleeptime))

    def updateComboBox4(self, index):
        del index
        quad_name = self.comboBox.currentText()
        presets = self._emit_presets_by_quad().get(quad_name, [])
        flag_items = [preset.flag for preset in presets]
        current_flag = self.comboBox_4.currentText()
        self._set_combo_items(self.comboBox_4, flag_items)
        if current_flag in flag_items:
            self._set_combo_current_text(self.comboBox_4, current_flag)
        self._sync_emit_preset_defaults()


    def get_setting(self):
        try:
            para = structData()
            # get scan parameters
            para.quad_name = self.comboBox.currentText()
            para.flag_name = self.comboBox_4.currentText()
            preset = self._current_emit_preset()
            if preset is None:
                raise ValueError(
                    f"No emit_measure preset is defined for {para.quad_name} -> {para.flag_name}."
                )
            para.quadPV = resolve_channel(self.machine_profile, para.quad_name, "k1", self.machine_type)
            para.flagSigxPV = resolve_channel(self.machine_profile, para.flag_name, "sigx", self.machine_type)
            para.flagSigyPV = resolve_channel(self.machine_profile, para.flag_name, "sigy", self.machine_type)
            para.model_line = preset.model_line
            para.app_context = self.app_context

            para.k1_from  = float(self.lineEdit_7.text())
            para.k1_end   = float(self.lineEdit_8.text())
            para.k1_steps = self._parse_positive_int(self.lineEdit_9.text(), "K1 steps")
            para.samples  = self._parse_positive_int(self.lineEdit_10.text(), "Samples per step")
            para.EnergyMeV = float(self.lineEdit_2.text())
            para.sleeptime = self._parse_non_negative_float(self.lineEdit_24.text(), "Sleep time")
            if para.EnergyMeV <= 0:
                raise ValueError("Energy must be positive.")
            return para
        except (MachineProfileError, ValueError) as exc:
            self._warn(str(exc))
            return None
 
    def startScan(self):
        if self._scan_is_running():
            print("Scan is already running. Stop it before starting a new scan.")
            return

        self.display({"clear": True})

        self.paras = self.get_setting()
        if self.paras is None:
            return
        try:
            require_workflow_write_allowed(
                self.app_context,
                "emit_measure",
                "Emit measurement scan",
            )
        except MachineProfileError as exc:
            self._warn(str(exc))
            return
        self.paras.recal = False
        self.paras.clear = False 
        self.paras.scan_metadata = self._scan_metadata_from_paras(self.paras)
        self.paras.scan_archive_dir = self._scan_archive_dir()
        self.pending_scan_metadata = dict(self.paras.scan_metadata)
        self.scan_mode = "scan"
        self.scan = scanThread(self.paras)
        self.scan.trigger.connect(self.display)
        self.scan.finished.connect(self._on_scan_finished)
        self.scan.start()
        self._refresh_status()

    def stopScan(self):
        if self._scan_is_running():
            self.scan.stop()
            if not self.scan.wait(3000):
                print("Timed out waiting for scan thread to stop.")
            print("Scan thread is stopped.")
        self.scan_mode = None
        self._refresh_status()

    def recalculate(self):
        if self._scan_is_running():
            print("Scan is already running. Stop it before recalculating.")
            return

        self.paras = self.get_setting()
        if self.paras is None:
            return
        expected_metadata = self._scan_metadata_from_paras(self.paras)

        try:
            if self.scan_points_table is not None and self.scan_points_table.rowCount() == 0:
                self._load_scan_results_into_table(expected_metadata=expected_metadata)
            else:
                self._validate_scan_metadata(
                    self.loaded_scan_metadata,
                    expected_metadata,
                    "current scan table",
                )
        except RuntimeError as exc:
            self._warn(str(exc))
            return

        recal_points = self._enabled_scan_points()
        if self.scan_points_table is not None and self.scan_points_table.rowCount() > 0:
            if len(recal_points) < 3:
                self._warn("At least 3 active scan points are required for recalculation.")
                return
            self._redraw_scan_points_from_table()

        self.paras.recal = True 
        self.paras.clear = False 
        self.paras.recal_points = recal_points if recal_points else None
        self.scan_mode = "recalculate"
        self.scan = scanThread(self.paras)
        self.scan.trigger.connect(self.display)
        self.scan.finished.connect(self._on_scan_finished)
        self.scan.start()
        self._refresh_status()

    def clearPlot(self):
        self.clear = clearThread()
        self.clear.trigger.connect(self.display)
        self.clear.start()

    def start_twissCalc(self):
        if self._twiss_is_running():
            self._warn("Twiss calculation is already running.")
            return

        try:
            beta0 = float(self.lineEdit.text())
            alpha0 = float(self.lineEdit_3.text())
            gamma0 = float(self.lineEdit_6.text())
            energy = float(self.lineEdit_2.text())
        except ValueError:
            self._warn("Twiss input values must be numeric.")
            return
        if energy <= 0:
            self._warn("Energy must be positive.")
            return

        para = {}
        para["quad1"] = self.comboBox_2.currentText()
        para["quad2"] = self.comboBox_3.currentText()
        
        para["inverse_map"] = self.radioButton.isChecked()

        if self.radioButton_2.isChecked() == True:
            para["plane"] = "yplane"
        else:
            para["plane"] = "xplane"
        
        para["beta0"] = beta0
        para["alpha0"] = alpha0
        para["gamma0"] = gamma0
        para["EnergyMeV"] = energy
        para["app_context"] = self.app_context

        self.twissCal = twissCalThread(para)
        self.twissCal.trigger.connect(self.showTwiss)
        self.twissCal.finished.connect(self._on_twiss_finished)
        self.twissCal.start()
        self._refresh_status()

    def display(self,dict):
        if "error" in dict:
            self._warn(dict["error"])
            return
        if "clear" in dict:
            # clear all the results
            self._draw_placeholder_plots()

            self.lineEdit_11.setText("")
            self.lineEdit_12.setText("")
            self.lineEdit_13.setText("")
            self.lineEdit_14.setText("")
            self.lineEdit_15.setText("")
            self.lineEdit_16.setText("")

            self.lineEdit_39.setText("")
            self.lineEdit_35.setText("")
            self.lineEdit_40.setText("")
            self.lineEdit_36.setText("")
            self.lineEdit_38.setText("")
            self.lineEdit_37.setText("")

            self.lineEdit_4.setText( "")
            self.lineEdit_5.setText( "")
            self.lineEdit_20.setText("")
            self.lineEdit_19.setText("")
            self.lineEdit_18.setText("")
            
            self.lineEdit_41.setText("")
            self.lineEdit_42.setText("")
            self.lineEdit_43.setText("")
            self.lineEdit_44.setText("")
            self.lineEdit_45.setText("")

            self._clear_scan_points()
            self.loaded_scan_metadata = None
            self.loaded_scan_results_path = None
            self._refresh_status()
            
            return

        if dict["method"] == None:
            k1 = dict["k1"]
            sigx = dict["sigx"]
            sigy = dict["sigy"]
            self._append_scan_point(k1, sigx, sigy)

            palette = self._palette()
            if not self.widget.axes.lines:
                self.widget.axes.clear()
                self._style_axes(self.widget, "$K_1 (m^{-2})$", "sigx (mm)")
            self.widget.axes.plot(k1, sigx, marker="x", linestyle="None", color=palette["plot_point"])
            self.widget.canvas.draw()

            if not self.widget_8.axes.lines:
                self.widget_8.axes.clear()
                self._style_axes(self.widget_8, "$K_1 (m^{-2})$", "sigy (mm)")
            self.widget_8.axes.plot(k1, sigy, marker="x", linestyle="None", color=palette["plot_point"])
            self.widget_8.canvas.draw()

        elif dict["method"] == "parabolic":
            palette = self._palette()
            xx     = dict["xplane"]["xx"]
            yy     = dict["xplane"]["yy"]
            err    = dict["xplane"]["err"]
            fit_yy = dict["xplane"]["fit_yy"]
            a      = dict["xplane"]["a"]
            b      = dict["xplane"]["b"]
            c      = dict["xplane"]["c"]

            self.widget_2.axes.clear()
            self._style_axes(self.widget_2, "$-K= K_1 L_q (m^{-1})$", "$sigx^2 (mm^2)$")
            self.widget_2.axes.errorbar(-xx, yy, err, fmt=".", color=palette["plot_point"], ecolor=palette["plot_error"], capsize=3)
            self.widget_2.axes.plot(-xx, fit_yy, "--", color=palette["plot_fit"], label="fitting curve")
            legend = self.widget_2.axes.legend(frameon=False)
            if legend is not None:
                for text in legend.get_texts():
                    text.set_color(palette["plot_text"])
            self.widget_2.canvas.draw()

            self.lineEdit_11.setText(str(dict["xplane"]["ex"]))
            self.lineEdit_12.setText(str(dict["xplane"]["beta"]))
            self.lineEdit_13.setText(str(dict["xplane"]["alpha"]))
            self.lineEdit_14.setText(str(dict["xplane"]["gamma"]))
            self.lineEdit_15.setText(str(dict["xplane"]["exn"]))

            curve = "sigx^2=" +str(a) +"K^2+" +str(b) +"K+" +str(c)
            self.lineEdit_16.setText(curve)

            #y-plane
            xx     = dict["yplane"]["xx"]
            yy     = dict["yplane"]["yy"]
            err    = dict["yplane"]["err"]
            fit_yy = dict["yplane"]["fit_yy"]
            a      = dict["yplane"]["a"]
            b      = dict["yplane"]["b"]
            c      = dict["yplane"]["c"]

            self.widget_9.axes.clear()
            self._style_axes(self.widget_9, "$K= K_1 L_q (m^{-1})$", "$sigy^2 (mm^2)$")
            self.widget_9.axes.errorbar(xx, yy, err, fmt=".", color=palette["plot_point"], ecolor=palette["plot_error"], capsize=3)
            self.widget_9.axes.plot(xx, fit_yy, "--", color=palette["plot_fit"], label="fitting curve")
            legend = self.widget_9.axes.legend(frameon=False)
            if legend is not None:
                for text in legend.get_texts():
                    text.set_color(palette["plot_text"])
            self.widget_9.canvas.draw()

            self.lineEdit_39.setText(str(dict["yplane"]["ex"]))
            self.lineEdit_35.setText(str(dict["yplane"]["beta"]))
            self.lineEdit_40.setText(str(dict["yplane"]["alpha"]))
            self.lineEdit_36.setText(str(dict["yplane"]["gamma"]))
            self.lineEdit_38.setText(str(dict["yplane"]["exn"]))

            curve = "sigy^2=" +str(a) +"K^2+" +str(b) +"K+" +str(c)
            self.lineEdit_37.setText(curve)

        elif dict["method"] == "leastSquares":
            self.lineEdit_4.setText(str(dict["xplane"]["ex"]))
            self.lineEdit_5.setText(str(dict["xplane"]["exn"]))
            self.lineEdit_20.setText(str(dict["xplane"]["beta"]))
            self.lineEdit_19.setText(str(dict["xplane"]["alpha"]))
            self.lineEdit_18.setText(str(dict["xplane"]["gamma"]))
            
            self.lineEdit_41.setText(str(dict["yplane"]["ex"]))
            self.lineEdit_42.setText(str(dict["yplane"]["exn"]))
            self.lineEdit_43.setText(str(dict["yplane"]["beta"]))
            self.lineEdit_44.setText(str(dict["yplane"]["alpha"]))
            self.lineEdit_45.setText(str(dict["yplane"]["gamma"]))

        else:
            print(f"Error, unexpected result method: {dict.get('method')}")
            return
        self._refresh_status()

    def showTwiss(self, dict):
        if "error" in dict:
            self._warn(dict["error"])
            return
        beta  = round(dict["beta"], 2)
        alpha = round(dict["alpha"],2)
        gamma = round(dict["gamma"],2)

        self.lineEdit_17.setText(str(beta))
        self.lineEdit_21.setText(str(alpha))
        self.lineEdit_22.setText(str(gamma))
        self._refresh_status()

    def closeEvent(self, event):
        self.stopScan()
        if self._twiss_is_running():
            if not self.twissCal.wait(3000):
                print("Timed out waiting for twiss thread to finish.")
        event.accept()

class clearThread(QThread):
    trigger = pyqtSignal(dict)
    def __init__(self):
        super().__init__()

    def run(self):
        todisp = {}
        todisp["clear"] = True
        self.trigger.emit(todisp)
 
class twissCalThread(QThread):
    trigger = pyqtSignal(dict)
    def __init__(self, para):
        super().__init__()

        self.input = para

    def run(self):
        try:
            quad1 = self.input["quad1"]
            quad2 = self.input["quad2"]

            twiss0={}
            twiss0["beta0"]  = self.input["beta0"]
            twiss0["alpha0"] = self.input["alpha0"]
            twiss0["gamma0"] = self.input["gamma0"]

            plane = self.input["plane"]
            inverse = self.input["inverse_map"]

            trans = transfer(self.input["EnergyMeV"], app_context=self.input["app_context"])
            twiss1 = trans.getTwiss1(quad1,quad2,twiss0,plane=plane,inverse=inverse)

            self.trigger.emit(twiss1)
        except Exception as exc:
            self.trigger.emit({"error": str(exc)})

class scanThread(QThread):

    trigger = pyqtSignal(dict)

    def __init__(self,paras):
        super().__init__()
        self.quad_name  = paras.quad_name.upper() 
        self.flag_name  = paras.flag_name.upper() 
        self.quadPV     = paras.quadPV    
        self.flagSigxPV = paras.flagSigxPV
        self.flagSigyPV = paras.flagSigyPV
        self.k1_from    = paras.k1_from   
        self.k1_end     = paras.k1_end    
        self.k1_steps   = paras.k1_steps  
        self.samples    = paras.samples   
        self.EnergyMeV  = paras.EnergyMeV
        self.sleeptime  = paras.sleeptime
        self.model_line = paras.model_line
        self.app_context = paras.app_context
        self.quad_length = None

        self.recal      = paras.recal 
        self.recal_points = getattr(paras, "recal_points", None)
        self.scan_metadata = getattr(paras, "scan_metadata", None)
        self.scan_archive_dir = Path(
            getattr(paras, "scan_archive_dir", SCAN_ARCHIVE_ROOT / "unknown" / "unknown")
        )
        self.is_running = True

    def _sleep_or_stop(self, seconds):
        end_time = time.time() + seconds
        while self.is_running and time.time() < end_time:
            time.sleep(min(0.1, end_time - time.time()))
        return self.is_running

    def _restore_quad(self, value):
        if value is not None:
            epics.caput(self.quadPV, value)

    def run(self):
        tmp = {"method": None}
        iniK1 = None
        try:
            if self.recal == False:
                require_workflow_write_allowed(
                    self.app_context,
                    "emit_measure",
                    "Emit measurement scan",
                )
                k1_list = np.linspace(self.k1_from,self.k1_end,self.k1_steps)

                self.k1l =[]
                self.sigxl = []
                self.sigyl = []

                iniK1 = epics.caget(self.quadPV)
                if iniK1 is None:
                    raise RuntimeError(f"Failed to read initial quad value from {self.quadPV}.")
                for k1 in k1_list:
                    if not self.is_running:
                        print("Stop scan, quad is back to initial values, K1=",iniK1)
                        return
                    epics.caput(self.quadPV,k1)
                    if not self._sleep_or_stop(self.sleeptime):
                        print("Stop scan, quad is back to initial values, K1=",iniK1)
                        return
                    for j in range(self.samples):
                        if self.is_running == True:
                            print("Quad K1=",k1)
                            if not self._sleep_or_stop(self.sleeptime):
                                print("Stop scan, quad is back to initial values, K1=",iniK1)
                                return
                            
                            [tmp2, tmp3] = epics.caget_many([self.flagSigxPV,self.flagSigyPV])
                            if tmp2 is None or tmp3 is None:
                                raise RuntimeError(
                                    f"Failed to read flag sigma PVs: {self.flagSigxPV}, {self.flagSigyPV}."
                                )
                            print("sigmax=",tmp2,"sigamy=",tmp3)

                            tmp["k1"]   = k1
                            tmp["sigx"] = tmp2
                            tmp["sigy"] = tmp3

                            self.k1l.append(k1)
                            self.sigxl.append(tmp2)
                            self.sigyl.append(tmp3)

                            self.trigger.emit(tmp)
                        else:
                            print("Stop scan, quad is back to initial values, K1=",iniK1)
                            return
                           
                print("Scan finished, quad is back to initial values, K1=",iniK1)

                txt = np.matrix([self.k1l,self.sigxl,self.sigyl]).transpose()
                self._write_scan_results(txt)
            
            elif self.recal == True:
                if self.recal_points is not None:
                    print(f"Loading {len(self.recal_points)} enabled scan points from table ...")
                    data = np.asarray(self.recal_points, dtype=float)
                else:
                    print(f"Loading {SCAN_RESULTS_PATH.name} ...")
                    if not SCAN_RESULTS_PATH.exists():
                        raise RuntimeError(f"{SCAN_RESULTS_PATH} not found. Run a scan before recalculating.")
                    with open(SCAN_RESULTS_PATH,"r") as f:
                        data = np.loadtxt(f, ndmin=2)
                if data.ndim != 2 or data.shape[1] < 3:
                    raise RuntimeError("Scan data must contain K1, sigx and sigy columns.")
                self.k1l   = data[:,0]
                self.sigxl = data[:,1]   #[mm]
                self.sigyl = data[:,2]   #[mm]
                if len(self.k1l) < 3:
                    raise RuntimeError("At least 3 scan points are required for emit recalculation.")

            else:
                raise RuntimeError("self.recal should be True or False.")

            # Parabolic fitting method
            # ========================
            # get the transfer matrix of (exit of quad-to-flag) 
            trans = transfer(
                self.EnergyMeV,
                app_context=self.app_context,
                model_line=self.model_line,
            )
            mat = trans.get_map(self.quad_name,self.flag_name)
            self.quad_length = trans.get_lattice_float(self.quad_name, "L")
            
            m11 = mat[0,0]
            m12 = mat[0,1]
            m33 = mat[2,2]
            m34 = mat[2,3]
            try:
                dim0 = len(self.k1l)/self.samples
                k1l   = np.reshape(self.k1l,  (int(dim0),self.samples))
                sigxl = np.reshape(self.sigxl,(int(dim0),self.samples))
                sigyl = np.reshape(self.sigyl,(int(dim0),self.samples))
            except ValueError:
                print("Warning: Please delete all points for a K1 value to make every step has the same samples.")
                print("However, this would not affect least squares method.")
            else:
                try:
                    tmp["method"] = "parabolic"
                    tmp["xplane"] = self.parabolicfitting(k1l,sigxl,m11,m12)
                    tmp["yplane"] = self.parabolicfitting(-k1l,sigyl,m33,m34)
                    self.trigger.emit(tmp)
                    if not self._sleep_or_stop(2):
                        return
                    print("Parabolic fitting finished")
                except Exception as exc:
                    print(f"Warning: parabolic fitting failed: {exc}")

            # Least squares method
            # ========================
            tmpx, tmpy = self.leastSquare()
            
            # X-plane
            tmp["method"]    = "leastSquares"

            tmpxx = {}
            tmpxx["ex"]    = tmpx.ex
            tmpxx["exn"]   = tmpx.exn
            tmpxx["beta"]  = tmpx.beta
            tmpxx["alpha"] = tmpx.alpha
            tmpxx["gamma"] = tmpx.gamma
            tmp["xplane"] = tmpxx

            # Y-plane
            tmpyy = {}
            tmpyy["ex"]    = tmpy.ex
            tmpyy["exn"]   = tmpy.exn
            tmpyy["beta"]  = tmpy.beta
            tmpyy["alpha"] = tmpy.alpha
            tmpyy["gamma"] = tmpy.gamma
            tmp["yplane"] = tmpyy

            self.trigger.emit(tmp)
            print("leastSquare finished")
           
            print("Program finished !")
        except Exception as exc:
            self.trigger.emit({"error": str(exc)})
        finally:
            self._restore_quad(iniK1)

    # Method-1, least squares method
    # --------------------
    def _scan_archive_stem(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        quad = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in self.quad_name)
        flag = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in self.flag_name)
        return f"scan_{timestamp}_{quad}_{flag}"

    def _write_scan_results(self, data):
        data = np.asarray(data, dtype=float)
        metadata = dict(self.scan_metadata or {})
        metadata.update(
            {
                "schema_version": SCAN_DATA_SCHEMA_VERSION,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "points": int(data.shape[0]),
                "columns": ["k1", "sigx", "sigy"],
            }
        )

        np.savetxt(SCAN_RESULTS_PATH, data, fmt="%.6e")
        SCAN_RESULTS_META_PATH.write_text(
            json.dumps(metadata, indent=2, sort_keys=True),
            encoding="utf-8",
        )

        self.scan_archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = self.scan_archive_dir / f"{self._scan_archive_stem()}.txt"
        archive_meta_path = archive_path.with_suffix(".json")
        np.savetxt(archive_path, data, fmt="%.6e")
        archive_meta_path.write_text(
            json.dumps(metadata, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(f"Saved scan results: {SCAN_RESULTS_PATH}")
        print(f"Saved scan archive: {archive_path}")

    def leastSquare(self):
        k1l  = np.array(self.k1l)
        sigx = np.array(self.sigxl)    #[mm]
        sigy = np.array(self.sigyl)    #[mm]
        
        sigxx = sigx**2
        sigyy = sigy**2
        
        A0_x = []
        A0_y = []
        for k1 in k1l:
            # get the transfer map 
            trans = transfer(
                self.EnergyMeV,
                app_context=self.app_context,
                model_line=self.model_line,
            )
            mat = trans.get_map(self.quad_name,self.flag_name,k1=k1,seq="ent2exit")
            
            # X-plane
            A11 = mat[0,0]**2
            A12 = 2*mat[0,0]*mat[0,1]
            A13 = mat[0,1]**2
            A0_x = A0_x + [A11,A12,A13]

            # Y-plane
            A11 = mat[2,2]**2
            A12 = 2*mat[2,2]*mat[2,3]
            A13 = mat[2,3]**2
            A0_y = A0_y + [A11,A12,A13]

        # for x-plane
        tmpx = self._solveMat(A0_x, k1l, sigxx)
        # for y-plane
        tmpy = self._solveMat(A0_y, k1l, sigyy)

        return tmpx,tmpy

    def _solveMat(self,A0,k1l,sigxx):
        A = np.asmatrix( np.reshape(A0,(len(k1l),3)) )
        b = np.asmatrix(sigxx).transpose()
        
        AA = A.transpose()*A
        bb = A.transpose()*b
        
        xx = np.linalg.solve(AA,bb)
        
        sig11 = xx[0,0]
        sig12 = xx[1,0]
        sig22 = xx[2,0]
        
        try:
            ex = math.sqrt(sig11*sig22-sig12**2)
            beta  = sig11/ex
            alpha = -sig12/ex
            gamma = sig22/ex
            
            gam0 = self.EnergyMeV*1e6/ELECTRON_MASS_EV
            exn = ex*gam0
            
            #print("exn,beta,alpha,gamma",exn,beta,alpha,gamma)
            tmp = structData()
            tmp.ex    = round(ex   ,4)
            tmp.exn   = round(exn  ,2)
            tmp.beta  = round(beta ,2)
            tmp.alpha = round(alpha,2)
            tmp.gamma = round(gamma,2)

        except (ValueError, ZeroDivisionError, np.linalg.LinAlgError):
            tmp = structData()
            tmp.ex    = None 
            tmp.exn   = None 
            tmp.beta  = None 
            tmp.alpha = None 
            tmp.gamma = None 

        return tmp

    # Method-2, parabolic fitting 
    #----------------------------------------
    def parabolicfitting(self,k1l,sigxl,m11,m12):
        # print('parabolicfitting~')
        k1_ave   = np.mean(k1l,1)
        sigx_ave = np.mean(sigxl,1)
        
        # get the error, for error bar plot
        # err_sigx = np.max(sigxl,1)**2 - sigx_ave**2
        err_sigx = np.std(sigxl, axis=1)

        if self.quad_length is None:
            trans = transfer(self.EnergyMeV, app_context=self.app_context)
            self.quad_length = trans.get_lattice_float(self.quad_name, "L")
        Lq = self.quad_length

        # s_quad = lattice[self.quad_name]["S"]
        # s_flag = lattice[self.flag_name]["S"]
        # distance = float(s_flag) - float(s_quad) 
     
        xx = -k1_ave*float(Lq)
        yy = sigx_ave**2
        
        ## resample the data
        #id1 = 0
        #id2 = steps
        #xxx =       xx[id1:id2]
        #yyy =       yy[id1:id2]
        #err = err_sigx[id1:id2]
        #plt.figure()
        #plt.errorbar(xxx,yyy,err,fmt='.r',ecolor='g',capsize=3)
        
        # fitting with 2-nd curve
        # -------------------------
        def paraFunc(x,a,b,c):
            y = a*x**2+b*x+c
            return y
        popt,pcov = curve_fit(paraFunc,xx,yy)
        fit_yy = paraFunc(xx,popt[0],popt[1],popt[2])

        tmp = {}
        tmp["xx"]     = xx
        tmp["yy"]     = yy
        tmp["err"]    = err_sigx
        tmp["fit_yy"] = fit_yy
        
        #plt.plot(xx,fit_yy,'--b',label='fitting-curve')
        #plt.legend()
        #plt.xlabel("-K1*Lq")
        #plt.ylabel("$sigx^2$")
        #plt.show()
        
        # calc to get twiss and emit 
        #=====================================
        a = popt[0]
        b = popt[1]
        c = popt[2]

        fac = np.sqrt(4*a*c-b**2)
        ex    = fac/(2*m12**2)
        alpha = (-b+2*a*m11/m12)/fac
        beta  = 2*a/fac
        gamma = (1+alpha**2)/beta
        
        gam0 = self.EnergyMeV*1e6/ELECTRON_MASS_EV
        exn = ex*gam0
        
        #print("exn,beta,alpha,gamma",exn,beta,alpha,gamma)

        tmp["ex"]    = round(ex   ,4) 
        tmp["exn"]   = round(exn  ,2) 
        tmp["beta"]  = round(beta ,2)
        tmp["alpha"] = round(alpha,2)
        tmp["gamma"] = round(gamma,2)

        tmp["a"] = round(a,2)
        tmp["b"] = round(b,2)
        tmp["c"] = round(c,2)

        return tmp
    
    def stop(self):
        self.is_running = False

class transfer:
    def __init__(self,EnergyMeV=None, app_context=None, model_line=None):
        self.energy = EnergyMeV
        self.app_context = app_context or load_app_context("emit_measure")
        self.model_line = model_line
        self.model_backend = build_model_backend(
            self.app_context,
            energy_mev=EnergyMeV,
            line_name=model_line,
        )

    def getTwiss1(self, quad1, quad2, twiss0, plane="xplane", inverse=False):
        return self.model_backend.get_twiss1(
            quad1,
            quad2,
            twiss0,
            plane=plane,
            inverse=inverse,
        )

    def get_map(self, elem1, elem2, k1=None, seq="exit2exit"):
        return self.model_backend.get_map(elem1, elem2, k1=k1, seq=seq)

    def get_lattice_float(self, element_id, field_name):
        element = self.model_backend.get_lattice_element(element_id)
        try:
            return float(element[field_name])
        except KeyError as exc:
            raise RuntimeError(
                f"Model backend lattice element {element_id!r} is missing {field_name!r}."
            ) from exc
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Model backend lattice element {element_id!r}.{field_name} is not numeric."
            ) from exc

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = myWindow()
    window.show()
    sys.exit(app.exec_())
    
    # window.plot_beamprofile()
