
import sys
import epics
import time
import numpy as np
import json
import math
from pathlib import Path

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

import half_linac.runtime_config as st
from half_linac.src.shared.machine_profile import (
    build_model_backend,
    get_emit_preset,
    load_app_context,
    resolve_channel,
)
from half_linac.src.shared.machine_profile.runtime_selector import (
    RuntimeSelectorWidget,
    request_runtime_restart,
)

nest_dict    = lambda: defaultdict(nest_dict)

#
jsonpath     = st.rootpath+"/src/virtual_machine/half_elegant/halflinac.json"
SCAN_RESULTS_PATH    = Path(__file__).resolve().parent / "scanResults.txt"

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
            raise ValueError("emit_measure workflow is not available in the current app context.")

        self.current_theme = "dark"
        self.control_backend = self.app_context.control_backend.name
        self.scan_mode = None

        self.scan = None
        self.twissCal = None
        self.clear = None
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

        self.status_panel = EmitStatusStrip(panel)
        self.status_panel.add_item("backend", "BACKEND", self.control_backend.upper())
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
        backend_tone = "warning" if self.control_backend == "real" else "success"
        self.status_panel.set_item("backend", self.control_backend.upper(), backend_tone)
        self.status_panel.set_item("tab", self.tabWidget.tabText(self.tabWidget.currentIndex()), "subtle")

        if self._scan_is_running():
            scan_text = "Recalculate" if self.scan_mode == "recalculate" else "Running"
            self.status_panel.set_item("scan", scan_text, "success")
        else:
            self.status_panel.set_item("scan", "Idle", "subtle")

        self.status_panel.set_item("twiss", "Running" if self._twiss_is_running() else "Idle", "success" if self._twiss_is_running() else "subtle")
        if SCAN_RESULTS_PATH.exists():
            self.status_panel.set_item("data", SCAN_RESULTS_PATH.name, "success")
        else:
            self.status_panel.set_item("data", "No scan file", "warning")

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

    def _apply_runtime_selection(self, machine_id, control_backend):
        if self._scan_is_running() or self._twiss_is_running():
            self._warn("Stop the current scan or Twiss calculation before switching machine or backend.")
            return

        request_runtime_restart(
            self,
            app_label="Emit Measure",
            current_machine_id=self.machine_profile.machine.id,
            current_control_backend=self.app_context.control_backend.name,
            machine_id=machine_id,
            control_backend=control_backend,
        )

    def _scan_is_running(self):
        return self.scan is not None and self.scan.isRunning()

    def _twiss_is_running(self):
        return self.twissCal is not None and self.twissCal.isRunning()

    def _on_scan_finished(self):
        self.scan = None
        self.scan_mode = None
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

    # def simply_VM(self):
    #     """to simplify the lattice in VM for accelerate the testing process (only considering from Q to flag )"""
        
    #     quad = self.comboBox.currentText()
    #     flag = self.comboBox_4.currentText()

    #     with open(jsonpath,"r") as f:
    #         lte  = json.load(f)
    #     contl    = lte  ["control"]
    #     lattice  = lte  ["lattice"]
    #     # usedline = lte  ["usedline"]
    #     usedline = lte["lattice"]["ALL"]["LINE"]

    #     # pre the input beam before entrance of quad
    #     # ------------------------------------------
    #     # add a watch
    #     prewatch = {}
    #     prewatch["NAME"] = "PREW"
    #     prewatch["TYPE"] = "WATCH"
    #     prewatch["FILENAME"] = "pre.bun"
    #     prewatch["MODE"] = "COORD"
    #     prewatch["DISABLE"] = "0"

    #     lattice["PREW"]={}
    #     lattice["PREW"]=prewatch
        
    #     id = usedline.index(quad)
    #     preline = usedline[0:id]
    #     preline.append(prewatch["NAME"])

    #     ltepre = {}
    #     ltepre["control"]  = contl
    #     ltepre["lattice"]  = lattice
    #     ltepre["usedline"] = preline
    #     with open(jsonpath,"w") as f:
    #         f.write(json.dumps(ltepre,indent=4))
        
    #     # wait the vm run
    #     time.sleep(st.runtime_vmmachine)
    #     print("pre.bun before ",quad," is ready")

    #     # get the energy before quad
    #     tmp = sdds.SDDS(0)
    #     tmp.load(st.rootpath+"/src/virtual_machine/half_elegant/elegant/one.cen")
    #     tmppCentral = tmp.columnData[11][0][-1]

    #     # simply VM
    #     # ---------
    #     contl    = lte  ["control"]
    #     lattice  = lte  ["lattice"]
    #     usedline = lte  ["usedline"]

    #     id1 = usedline.index(quad)
    #     id2 = usedline.index(flag)
    #     scanline = usedline[id1:id2+1]

    #     del contl["bunched_beam"]

    #     contl["run_setup"]["p_central"]= str(tmppCentral)

    #     contl["sdds_beam"] = {}
    #     contl["sdds_beam"]["input"]="pre.bun"
    #     contl["sdds_beam"]["center_arrival_time"]="1"
    #     contl["sdds_beam"]["reuse_bunch"]="1"

    #     lte["control"]  = contl
    #     lte["lattice"]  = lattice
    #     lte["usedline"] = scanline

    #     with open(jsonpath,"w") as f:
    #         f.write(json.dumps(lte,indent=4))
        
    #     time.sleep(5)
    #     print("simply VM: (",quad,"-to-",flag,") is ready")

    #     return
    
    # def full_VM(self):
    #     # back to initial
    #     # print(lattice_file)
    #     # ltet = elegant_parser(lattice_file, ele_file, line_name)
    #     # ltet.dump2json(jsonpath)
        
    #     # back to the state before simply
    #     with open(jsonpath,"r") as f:
    #         lte  = json.load(f)
    #     contl    = lte  ["control"]
    #     lattice  = lte  ["lattice"]
    #     line = lte["lattice"]["ALL"]["LINE"]

    #     lte["usedline"] = lte["lattice"]["ALL"]["LINE"]

    #     if "PREW" in lattice:
    #         del lattice["PREW"]
        
    #     if "sdds_beam" in contl:
    #         del contl["sdds_beam"]

    #         bunched_beam = {
    #             "n_particles_per_bunch": "10000",
    #             "emit_nx": "10e-6",
    #             "emit_ny": "10e-6",
    #             "use_twiss_command_values": "10000",
    #             "distribution_type[0]": "\"gaussian\"",
    #             "distribution_type[1]": "\"gaussian\"",
    #             "distribution_type[2]": "\"gaussian\"",
    #             "distribution_cutoff[0]": "5",
    #             "distribution_cutoff[1]": "5",
    #             "distribution_cutoff[2]": "5",
    #             "sigma_s": "1.11e-3",
    #             "sigma_dp": "4e-3"
    #         }
    #         contl["bunched_beam"] = bunched_beam
        
    #     contl["run_setup"]["p_central"]= "223.4028"

    #     lte["control"]  = contl
    #     lte["lattice"]  = lattice
    #     lte["usedline"] = line
    #     with open(jsonpath,"w") as f:   
    #         f.write(json.dumps(lte,indent=4))

    #     print("full VM is back")
    #     return

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

    def _emit_presets_by_quad(self):
        grouped = defaultdict(list)
        for preset in self.emit_workflow.presets:
            grouped[preset.quad].append(preset)
        return grouped

    def _find_emit_preset(self, preset_id):
        return get_emit_preset(self.app_context, preset_id)

    def _configure_machine_profile(self):
        presets_by_quad = self._emit_presets_by_quad()
        quad_items = list(presets_by_quad)
        self._set_combo_items(self.comboBox, quad_items)

        twiss_quads = self.emit_workflow.twiss_quads
        self._set_combo_items(self.comboBox_2, twiss_quads)
        self._set_combo_items(self.comboBox_3, twiss_quads)

        default_preset = self._find_emit_preset(self.emit_workflow.default_preset)
        self._set_combo_current_text(self.comboBox, default_preset.quad)
        self.updateComboBox4(self.comboBox.currentIndex())
        self._set_combo_current_text(self.comboBox_4, default_preset.flag)
        self._set_combo_current_text(self.comboBox_2, twiss_quads[0])
        self._set_combo_current_text(self.comboBox_3, twiss_quads[0])
        self._apply_emit_preset_defaults(default_preset)

    def _apply_emit_preset_defaults(self, preset):
        default_preset = self._find_emit_preset(self.emit_workflow.default_preset)
        scan_source = preset.scan if preset.scan.as_dict() else default_preset.scan
        self._apply_typed_defaults(
            scan_source,
            {
                "k1_from": self.lineEdit_7,
                "k1_end": self.lineEdit_8,
                "k1_steps": self.lineEdit_9,
                "samples": self.lineEdit_10,
                "sleeptime": self.lineEdit_24,
            },
        )
        self._apply_typed_defaults(
            preset.analysis,
            {
                "energy_mev": self.lineEdit_2,
            },
        )

    def _sync_emit_preset_defaults(self):
        quad_name = self.comboBox.currentText()
        flag_name = self.comboBox_4.currentText()
        for preset in self.emit_workflow.presets:
            if preset.quad == quad_name and preset.flag == flag_name:
                self._apply_emit_preset_defaults(preset)
                return

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
            para.quadPV = resolve_channel(self.app_context, para.quad_name, "k1", self.control_backend)
            para.flagSigxPV = resolve_channel(self.app_context, para.flag_name, "sigx", self.control_backend)
            para.flagSigyPV = resolve_channel(self.app_context, para.flag_name, "sigy", self.control_backend)
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
        except ValueError as exc:
            self._warn(str(exc))
            return None
 
    def startScan(self):
        if self._scan_is_running():
            print("Scan is already running. Stop it before starting a new scan.")
            return

        self.clearPlot()

        self.paras = self.get_setting()
        if self.paras is None:
            return
        self.paras.recal = False
        self.paras.clear = False 
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
        self.paras.recal = True 
        self.paras.clear = False 
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

            self._refresh_status()
            
            return

        if dict["method"] == None:
            k1 = dict["k1"]
            sigx = dict["sigx"]
            sigy = dict["sigy"]

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
        self.app_context = para["app_context"]

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

            model_backend = build_model_backend(self.app_context, energy_mev=self.input["EnergyMeV"])
            twiss1 = model_backend.get_twiss1(quad1,quad2,twiss0,plane=plane,inverse=inverse)

            self.trigger.emit(twiss1)
        except Exception as exc:
            self.trigger.emit({"error": str(exc)})

class scanThread(QThread):

    trigger = pyqtSignal(dict)

    def __init__(self,paras):
        super().__init__()
        self.app_context = paras.app_context
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
        self.model_backend = build_model_backend(self.app_context, energy_mev=self.EnergyMeV)

        self.recal      = paras.recal 
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
                np.savetxt(SCAN_RESULTS_PATH,txt,fmt="%.6e")
            
            elif self.recal == True:
                print(f"Loading {SCAN_RESULTS_PATH.name} ...")
                if not SCAN_RESULTS_PATH.exists():
                    raise RuntimeError(f"{SCAN_RESULTS_PATH} not found. Run a scan before recalculating.")
                with open(SCAN_RESULTS_PATH,"r") as f:
                    data = np.loadtxt(f, ndmin=2)
                self.k1l   = data[:,0]
                self.sigxl = data[:,1]   #[mm]
                self.sigyl = data[:,2]   #[mm]

            else:
                raise RuntimeError("self.recal should be True or False.")

            # Parabolic fitting method
            # ========================
            # get the transfer matrix of (exit of quad-to-flag) 
            mat = self.model_backend.get_map(self.quad_name,self.flag_name)
            
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
            mat = self.model_backend.get_map(self.quad_name,self.flag_name,k1=k1,seq="ent2exit")
            
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
            
            gam0 = self.EnergyMeV*1e6/st.electron_mass
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

        with open(jsonpath,"r") as f:
            lte = json.load(f)

        lattice = lte["lattice"]
        Lq = lattice[self.quad_name]["L"]

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
        
        gam0 = self.EnergyMeV*1e6/st.electron_mass
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

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = myWindow()
    window.show()
    sys.exit(app.exec_())
    
    # window.plot_beamprofile()
