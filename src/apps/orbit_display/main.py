import os
import sys
from subprocess import Popen
from pathlib import Path

_REPO_BOOTSTRAP_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file()
)
if str(_REPO_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_BOOTSTRAP_ROOT))

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

import numpy as np
from epics import caget_many
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import half_linac.runtime_config as st
from half_linac.src.shared.machine_profile import (
    list_elements,
    load_app_context,
    resolve_channel,
)
from half_linac.src.shared.machine_profile.runtime_selector import (
    RuntimeSelectorWidget,
    request_runtime_restart,
)
from gui import Ui_MainWindow


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
    "plot_card_border": "#263640",
    "plot_bg": "#11181e",
    "plot_grid": "#2a3943",
    "plot_spine": "#445764",
    "plot_text": "#d7e2ea",
    "orbit_x": "#6cb6ff",
    "orbit_y": "#f4c46a",
    "status_bg": "#11191f",
    "status_fg": "#c9d5dc",
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
    "plot_card_border": "#ddd2c4",
    "plot_bg": "#fffdf8",
    "plot_grid": "#ddd4c7",
    "plot_spine": "#b5aa9a",
    "plot_text": "#304049",
    "orbit_x": "#2f7dc5",
    "orbit_y": "#b17a15",
    "status_bg": "#f3ede4",
    "status_fg": "#625b52",
    "status_strip_bg": "#f7f1e8",
    "status_strip_border": "#ddd2c4",
    "status_separator": "#ddd4c7",
    "status_item_idle_bar": "#c8bfb3",
    "status_title_fg": "#7c7368",
    "metric_active_fg": "#2d7f6d",
    "metric_warning_fg": "#a97118",
    "metric_idle_fg": "#4e5a62",
}


def build_orbit_display_theme(palette):
    theme_values = dict(palette, header_action_height=HEADER_ACTION_HEIGHT)
    return """
QMainWindow, QWidget#centralwidget {{
    background-color: {window_bg};
    color: {window_fg};
    font-family: "IBM Plex Sans", "Source Han Sans SC", "Segoe UI", sans-serif;
}}

QFrame#controlPanel, QFrame#plotCard {{
    background-color: {panel_bg};
    border: 1px solid {panel_border};
    border-radius: 14px;
}}

QFrame#summaryPanel {{
    background-color: {summary_bg};
    border: 1px solid {summary_border};
    border-radius: 14px;
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
}}

QPushButton {{
    background-color: {button_bg};
    border: 1px solid {button_border};
    border-radius: 12px;
    color: {button_fg};
    padding: 8px 12px;
    min-height: 38px;
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

QPushButton#headerButton {{
    min-height: {header_action_height}px;
    max-height: {header_action_height}px;
    padding: 0px 12px;
}}

QPushButton[compact="true"] {{
    padding: 5px 10px;
    min-height: 28px;
    font-size: 11px;
}}

QLineEdit {{
    background-color: {input_bg};
    border: 1px solid {input_border};
    border-radius: 10px;
    color: {input_fg};
    padding: 8px 10px;
    min-height: 18px;
    selection-background-color: {metric_active_fg};
}}

QLabel {{
    color: {window_fg};
    font-size: 12px;
    font-weight: 600;
    background: transparent;
    border: none;
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
    font-size: 11px;
    font-weight: 700;
}}

QToolButton#themeToggleButton:hover {{
    background-color: {button_hover_bg};
}}

QToolButton#themeToggleButton:pressed {{
    background-color: {button_pressed_bg};
}}

QStatusBar {{
    background-color: {status_bg};
    color: {status_fg};
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
    font-size: 14px;
    font-weight: 700;
}}
QLabel[role="value"][tone="success"] {{
    color: {status_tone_success_fg};
    background: transparent;
    border: none;
    font-size: 14px;
    font-weight: 700;
}}
QLabel[role="value"][tone="warning"] {{
    color: {status_tone_warning_fg};
    background: transparent;
    border: none;
    font-size: 14px;
    font-weight: 700;
}}
""".format_map(theme_values)


class OrbitStatusStrip(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = {}
        self.setObjectName("statusStrip")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
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
        container.setMinimumWidth(118)

        inner = QVBoxLayout(container)
        inner.setContentsMargins(10, 0, 8, 0)
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
        self.app_context = load_app_context("orbit_display")
        self.machine_profile = self.app_context.profile
        self.control_backend = self.app_context.control_backend.name
        self.bpm_elements = list_elements(self.app_context, kind="bpm")
        self.bpm_ids = [element.id for element in self.bpm_elements]
        self.bpm_x_pvs = [resolve_channel(self.app_context, bpm_id, "x") for bpm_id in self.bpm_ids]
        self.bpm_y_pvs = [resolve_channel(self.app_context, bpm_id, "y") for bpm_id in self.bpm_ids]

        self.current_theme = "dark"
        self.is_x_running = False
        self.is_y_running = False
        self._pv_available = False
        self._pv_error = None

        self._configure_window()
        self._configure_controls()
        self._build_plot_panel()

        self.init_pv()

        self.cxmin = None
        self.cxmax = None
        self.cymin = None
        self.cymax = None

        self.BPMxstart = None
        self.BPMxend = None
        self.BPMystart = None
        self.BPMyend = None

        self.timer_1 = QTimer(self)
        self.timer_1.timeout.connect(self.plotorbit_x)
        self.timer_2 = QTimer(self)
        self.timer_2.timeout.connect(self.plotorbit_y)

        self.start_1.clicked.connect(self.start1_btn)
        self.stop_1.clicked.connect(self.stop1_btn)
        self.start_2.clicked.connect(self.start2_btn)
        self.stop_2.clicked.connect(self.stop2_btn)
        self.hold_1.toggled.connect(self._refresh_status)
        self.hold_2.toggled.connect(self._refresh_status)
        self.bPMSLineEdit.textChanged.connect(self._refresh_status)
        self.bPMELineEdit.textChanged.connect(self._refresh_status)
        self.bPMSLineEdit_2.textChanged.connect(self._refresh_status)
        self.bPMYLineEdit.textChanged.connect(self._refresh_status)

        self._prepare_empty_plot(self.graphWidget_1.canvas.axes, self.graphWidget_1.canvas.figure, "Horizontal Orbit")
        self._prepare_empty_plot(self.graphWidget_2.canvas.axes, self.graphWidget_2.canvas.figure, "Vertical Orbit")
        self._refresh_status()

    def _configure_window(self):
        self.setWindowTitle(f"{self.machine_profile.machine.display_name} Orbit Display")
        self.resize(1320, 900)
        self._apply_theme()
        self.statusBar().showMessage("Orbit display ready.", 5000)

    def _configure_controls(self):
        self.horizontalLayout.setContentsMargins(10, 10, 10, 10)
        self.horizontalLayout.setSpacing(12)
        self.horizontalLayout_2.setSpacing(12)
        self.verticalLayout_3.setSpacing(12)

        self.frame_2.setObjectName("controlPanel")
        self.frame_3.setObjectName("controlPanel")
        self.frame_2.setMinimumWidth(210)
        self.frame_3.setMinimumWidth(210)
        self.frame_2.setMaximumWidth(220)
        self.frame_3.setMaximumWidth(220)

        self.verticalLayout.setContentsMargins(12, 12, 12, 12)
        self.verticalLayout_2.setContentsMargins(12, 12, 12, 12)
        self.verticalLayout_7.setSpacing(14)
        self.verticalLayout_9.setSpacing(14)

        self._insert_panel_title(self.verticalLayout, "Horizontal Controls")
        self._insert_panel_title(self.verticalLayout_2, "Vertical Controls")

        self.start_1.setText("Run X Orbit")
        self.stop_1.setText("Stop X Orbit")
        self.hold_1.setText("Hold Trace")
        self.start_2.setText("Run Y Orbit")
        self.stop_2.setText("Stop Y Orbit")
        self.hold_2.setText("Hold Trace")
        for button in (self.start_1, self.stop_1, self.start_2, self.stop_2):
            button.setProperty("compact", True)
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()
        self.stop_1.setEnabled(False)
        self.stop_2.setEnabled(False)

        self.label_5.setText("Y Min")
        self.label_6.setText("Y Max")
        self.label_7.setText("Y Min")
        self.label_8.setText("Y Max")
        self.bPMSLabel.setText("BPM Start")
        self.bPMELabel.setText("BPM End")
        self.bPMSLabel_2.setText("BPM Start")
        self.bPMYLabel.setText("BPM End")

        self.QL_cxmin.setPlaceholderText("Auto")
        self.QL_cxmax.setPlaceholderText("Auto")
        self.QL_cymin.setPlaceholderText("Auto")
        self.QL_cymax.setPlaceholderText("Auto")
        self.bPMSLineEdit.setPlaceholderText("1")
        self.bPMELineEdit.setPlaceholderText(str(len(self.bpm_ids)))
        self.bPMSLineEdit_2.setPlaceholderText("1")
        self.bPMYLineEdit.setPlaceholderText(str(len(self.bpm_ids)))

        for label in (
            self.label_5,
            self.label_6,
            self.label_7,
            self.label_8,
            self.bPMSLabel,
            self.bPMELabel,
            self.bPMSLabel_2,
            self.bPMYLabel,
        ):
            label.setProperty("role", "field")

    def _build_plot_panel(self):
        self.verticalLayout_4.setSizeConstraint(QLayout.SetDefaultConstraint)
        self.verticalLayout_4.setContentsMargins(0, 0, 0, 0)
        self.verticalLayout_4.setSpacing(12)

        self._build_summary_panel()

        self.verticalLayout_4.removeWidget(self.graphWidget_1)
        self.verticalLayout_4.removeWidget(self.graphWidget_2)
        self.verticalLayout_4.addWidget(self._build_plot_card("Horizontal Orbit", self.graphWidget_1))
        self.verticalLayout_4.addWidget(self._build_plot_card("Vertical Orbit", self.graphWidget_2))

        self.horizontalLayout_2.setStretch(0, 2)
        self.horizontalLayout_2.setStretch(1, 8)

    def _build_summary_panel(self):
        panel = QFrame(self.centralwidget)
        panel.setObjectName("summaryPanel")
        panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        outer_layout = QVBoxLayout(panel)
        outer_layout.setContentsMargins(14, 12, 14, 12)
        outer_layout.setSpacing(8)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(12)

        title = QLabel(f"{self.machine_profile.machine.display_name} Orbit Display", panel)
        title.setObjectName("summaryTitle")
        header_layout.addWidget(title)
        header_layout.addStretch(1)

        self.runtime_selector = RuntimeSelectorWidget(
            current_machine_id=self.machine_profile.machine.id,
            current_control_backend=self.control_backend,
            parent=panel,
        )
        self.runtime_selector.apply_requested.connect(self._apply_runtime_selection)
        header_layout.addWidget(self.runtime_selector)

        self.detail_button = QPushButton("BPM Detail", panel)
        self.detail_button.setObjectName("headerButton")
        self.detail_button.setFixedHeight(HEADER_ACTION_HEIGHT)
        self.detail_button.clicked.connect(self.start_bpmvalue_btn)
        header_layout.addWidget(self.detail_button)

        self.theme_toggle_button = QToolButton(panel)
        self.theme_toggle_button.setObjectName("themeToggleButton")
        self.theme_toggle_button.setFixedSize(HEADER_ACTION_HEIGHT, HEADER_ACTION_HEIGHT)
        self.theme_toggle_button.clicked.connect(self._toggle_theme)
        header_layout.addWidget(self.theme_toggle_button)

        outer_layout.addLayout(header_layout)

        self.status_panel = OrbitStatusStrip(panel)
        self.status_panel.add_item("machine", "MACHINE", self.machine_profile.machine.id)
        self.status_panel.add_item("backend", "BACKEND", self.control_backend.upper())
        self.status_panel.add_item("x", "X ORBIT", "Idle")
        self.status_panel.add_item("y", "Y ORBIT", "Idle")
        self.status_panel.add_item("hold", "HOLD", "Off")
        self.status_panel.add_item("view", "BPM VIEW", f"1-{len(self.bpm_ids)} default")
        self.status_panel.finish()
        self.status_panel.apply_theme(self._palette())
        self.status_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.status_panel.setFixedHeight(self.status_panel.sizeHint().height())
        self._update_theme_toggle_button()

        outer_layout.addWidget(self.status_panel)
        self.verticalLayout_4.addWidget(panel)

    def _build_plot_card(self, title_text, plot_widget):
        card = QFrame(self.centralwidget)
        card.setObjectName("plotCard")
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        title = QLabel(title_text, card)
        title.setObjectName("panelTitle")
        layout.addWidget(title)

        plot_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(plot_widget)
        return card

    def _insert_panel_title(self, layout, text):
        title = QLabel(text, self.centralwidget)
        title.setObjectName("panelTitle")
        layout.insertWidget(0, title)

    def _apply_theme(self):
        palette = self._palette()
        self.setStyleSheet(build_orbit_display_theme(palette))
        if hasattr(self, "status_panel"):
            self.status_panel.apply_theme(palette)
        self._update_theme_toggle_button()

    def _update_theme_toggle_button(self):
        if not hasattr(self, "theme_toggle_button"):
            return
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
        self._prepare_empty_plot(self.graphWidget_1.canvas.axes, self.graphWidget_1.canvas.figure, "Horizontal Orbit")
        self._prepare_empty_plot(self.graphWidget_2.canvas.axes, self.graphWidget_2.canvas.figure, "Vertical Orbit")

    def _palette(self):
        return DARK_THEME if self.current_theme == "dark" else LIGHT_THEME

    def _notify(self, message):
        self.statusBar().showMessage(message, 5000)

    def _refresh_status(self):
        self.status_panel.set_item("machine", self.machine_profile.machine.id, "subtle")
        self.status_panel.set_item(
            "backend",
            self.control_backend.upper(),
            "warning" if self.control_backend == "real" else "success",
        )
        self.status_panel.set_item("x", "Running" if self.is_x_running else "Idle", "success" if self.is_x_running else "subtle")
        self.status_panel.set_item("y", "Running" if self.is_y_running else "Idle", "success" if self.is_y_running else "subtle")

        hold_states = []
        if self.hold_1.isChecked():
            hold_states.append("X")
        if self.hold_2.isChecked():
            hold_states.append("Y")
        hold_text = " + ".join(hold_states) if hold_states else "Off"
        hold_tone = "warning" if hold_states else "subtle"
        self.status_panel.set_item("hold", hold_text, hold_tone)

        if not self._pv_available:
            self.status_panel.set_item("view", "Offline shell", "warning")
        else:
            self.status_panel.set_item("view", self._format_bpm_view(), "warning" if self._has_custom_view() else "subtle")

    def _has_custom_view(self):
        return any(
            field.text().strip()
            for field in (
                self.bPMSLineEdit,
                self.bPMELineEdit,
                self.bPMSLineEdit_2,
                self.bPMYLineEdit,
            )
        )

    def _format_bpm_view(self):
        ranges = []
        x_start = self.bPMSLineEdit.text().strip() or "1"
        x_end = self.bPMELineEdit.text().strip() or str(len(self.bpm_ids))
        y_start = self.bPMSLineEdit_2.text().strip() or "1"
        y_end = self.bPMYLineEdit.text().strip() or str(len(self.bpm_ids))

        if self.bPMSLineEdit.text().strip() or self.bPMELineEdit.text().strip():
            ranges.append(f"X {x_start}-{x_end}")
        if self.bPMSLineEdit_2.text().strip() or self.bPMYLineEdit.text().strip():
            ranges.append(f"Y {y_start}-{y_end}")

        return " / ".join(ranges) if ranges else f"1-{len(self.bpm_ids)} default"

    def init_pv(self):
        try:
            self.pvlx_val = caget_many(self.bpm_x_pvs)
            self.pvly_val = caget_many(self.bpm_y_pvs)
            self._pv_available = True
            self._pv_error = None
        except Exception as exc:
            self.pvlx_val = [None] * len(self.bpm_x_pvs)
            self.pvly_val = [None] * len(self.bpm_y_pvs)
            self._pv_available = False
            error_text = str(exc)
            if error_text != self._pv_error:
                self._pv_error = error_text
                self._notify("PV connection unavailable. Orbit Display is in offline shell mode.")

    def start1_btn(self):
        self.timer_1.start(1000)
        self.is_x_running = True
        self.start_1.setEnabled(False)
        self.stop_1.setEnabled(True)
        self._notify("Horizontal orbit refresh started.")
        self._refresh_status()

    def stop1_btn(self):
        self.timer_1.stop()
        self.is_x_running = False
        self.start_1.setEnabled(True)
        self.stop_1.setEnabled(False)
        self._notify("Horizontal orbit refresh stopped.")
        self._refresh_status()

    def start2_btn(self):
        self.timer_2.start(1000)
        self.is_y_running = True
        self.start_2.setEnabled(False)
        self.stop_2.setEnabled(True)
        self._notify("Vertical orbit refresh started.")
        self._refresh_status()

    def stop2_btn(self):
        self.timer_2.stop()
        self.is_y_running = False
        self.start_2.setEnabled(True)
        self.stop_2.setEnabled(False)
        self._notify("Vertical orbit refresh stopped.")
        self._refresh_status()

    @staticmethod
    def _scale_bpm_values(values):
        return [float(value) * 1000 if value is not None else np.nan for value in values]

    def _style_plot_axes(self, ax, fig):
        palette = self._palette()
        fig.patch.set_facecolor(palette["plot_card_bg"])
        ax.set_facecolor(palette["plot_bg"])
        ax.tick_params(colors=palette["plot_text"], which="both", labelsize=9)
        ax.xaxis.label.set_color(palette["plot_text"])
        ax.yaxis.label.set_color(palette["plot_text"])

        for spine in ax.spines.values():
            spine.set_edgecolor(palette["plot_spine"])

        ax.grid(True, color=palette["plot_grid"], linestyle="--", linewidth=0.6)

    def _prepare_empty_plot(self, ax, fig, title):
        palette = self._palette()
        ax.clear()
        self._style_plot_axes(ax, fig)
        ax.set_title(title, color=palette["plot_text"], fontsize=11, fontweight="bold", loc="left")
        ax.set_xlabel("BPM #", fontweight="bold")
        ax.set_ylabel("Position (mm)", fontweight="bold")
        fig.tight_layout()
        fig.canvas.draw()

    def plotorbit_x(self):
        self.init_pv()
        pvl_val = self._scale_bpm_values(self.pvlx_val)
        palette = self._palette()

        ax = self.graphWidget_1.canvas.axes
        fig = self.graphWidget_1.canvas.figure

        if not self.hold_1.isChecked():
            ax.clear()

        self._style_plot_axes(ax, fig)

        if not self._pv_available:
            ax.set_title("Horizontal Orbit / Offline", color=palette["plot_text"], fontsize=11, fontweight="bold", loc="left")
            ax.set_xlabel("BPM #", fontweight="bold")
            ax.set_ylabel("Cx (mm)", fontweight="bold")
            self.graphWidget_1.canvas.draw()
            self._refresh_status()
            return

        try:
            if self.QL_cxmin.text():
                self.cxmin = float(self.QL_cxmin.text())
                ax.set_ylim(bottom=self.cxmin)
            if self.QL_cxmax.text():
                self.cxmax = float(self.QL_cxmax.text())
                ax.set_ylim(top=self.cxmax)

            if self.bPMSLineEdit.text():
                self.BPMxstart = int(self.bPMSLineEdit.text())
                ax.set_xlim(left=self.BPMxstart)
            if self.bPMELineEdit.text():
                self.BPMxend = int(self.bPMELineEdit.text())
                ax.set_xlim(right=self.BPMxend)
        except ValueError:
            pass

        x = np.linspace(1, len(pvl_val), len(pvl_val))
        ax.plot(
            x,
            pvl_val,
            "-o",
            color=palette["orbit_x"],
            markerfacecolor=palette["orbit_x"],
            markeredgecolor=palette["plot_bg"],
            markersize=4,
            linewidth=1.5,
        )
        ax.set_xlabel("BPM #", fontweight="bold")
        ax.set_ylabel("Cx (mm)", fontweight="bold")
        self.graphWidget_1.canvas.draw()

    def plotorbit_y(self):
        self.init_pv()
        pvl_val = self._scale_bpm_values(self.pvly_val)
        palette = self._palette()

        ax = self.graphWidget_2.canvas.axes
        fig = self.graphWidget_2.canvas.figure

        if not self.hold_2.isChecked():
            ax.clear()

        self._style_plot_axes(ax, fig)

        if not self._pv_available:
            ax.set_title("Vertical Orbit / Offline", color=palette["plot_text"], fontsize=11, fontweight="bold", loc="left")
            ax.set_xlabel("BPM #", fontweight="bold")
            ax.set_ylabel("Cy (mm)", fontweight="bold")
            self.graphWidget_2.canvas.draw()
            self._refresh_status()
            return

        try:
            if self.QL_cymin.text():
                self.cymin = float(self.QL_cymin.text())
                ax.set_ylim(bottom=self.cymin)
            if self.QL_cymax.text():
                self.cymax = float(self.QL_cymax.text())
                ax.set_ylim(top=self.cymax)

            if self.bPMSLineEdit_2.text():
                self.BPMystart = int(self.bPMSLineEdit_2.text())
                ax.set_xlim(left=self.BPMystart)
            if self.bPMYLineEdit.text():
                self.BPMyend = int(self.bPMYLineEdit.text())
                ax.set_xlim(right=self.BPMyend)
        except ValueError:
            pass

        x = np.linspace(1, len(pvl_val), len(pvl_val))
        ax.plot(
            x,
            pvl_val,
            "-o",
            color=palette["orbit_y"],
            markerfacecolor=palette["orbit_y"],
            markeredgecolor=palette["plot_bg"],
            markersize=4,
            linewidth=1.5,
        )
        ax.set_xlabel("BPM #", fontweight="bold")
        ax.set_ylabel("Cy (mm)", fontweight="bold")
        self.graphWidget_2.canvas.draw()

    def start_bpmvalue_btn(self):
        self._notify("Opening BPM detail window.")
        Popen(
            ["python3", "submain.py"],
            cwd=st.rootpath + "/src/apps/orbit_display",
            shell=False,
        )

    def _apply_runtime_selection(self, machine_id, control_backend):
        request_runtime_restart(
            self,
            app_label="Orbit Display",
            current_machine_id=self.machine_profile.machine.id,
            current_control_backend=self.control_backend,
            machine_id=machine_id,
            control_backend=control_backend,
        )


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = myWindow()
    window.show()
    sys.exit(app.exec_())
