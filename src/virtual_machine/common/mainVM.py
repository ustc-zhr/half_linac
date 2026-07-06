import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from subprocess import Popen, TimeoutExpired

_REPO_BOOTSTRAP_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file()
)
if str(_REPO_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_BOOTSTRAP_ROOT))

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from half_linac.src.virtual_machine.half_elegant.VMgui import Ui_MainWindow

from half_linac.src.shared.machine_profile import (
    MachineProfileError,
    resolve_machine_runtime,
    resolve_virtual_machine_usedline_workflow,
)
from half_linac.src.virtual_machine.lattice_usedline import describe_runtime_usedline


PROCESS_START_TIMEOUT_S = 0.3
PROCESS_STOP_TIMEOUT_S = 3.0
PROCESS_REFRESH_INTERVAL_MS = 1000
PROCESS_READY_GRACE_S = 2.0
HEADER_ACTION_HEIGHT = 32

DARK_THEME = {
    "window_bg": "#0f1519",
    "window_fg": "#e6edf2",
    "frame_bg": "#172027",
    "frame_border": "#22303a",
    "summary_panel_bg": "#1b262d",
    "summary_panel_border": "#2b3a45",
    "status_strip_bg": "#131c22",
    "status_strip_border": "#2a3943",
    "status_separator": "#31424d",
    "status_item_idle_bar": "#4f6270",
    "status_title_fg": "#8ea0ad",
    "title_fg": "#f3efe3",
    "metric_label_fg": "#8ea0ad",
    "metric_value_fg": "#f3efe3",
    "metric_active_fg": "#45d0bc",
    "metric_warning_fg": "#e4b86f",
    "metric_idle_fg": "#c8d2da",
    "textedit_bg": "#10171c",
    "textedit_border": "#24343f",
    "textedit_fg": "#d7e2ea",
    "group_bg": "#172027",
    "group_border": "#24333d",
    "group_title_fg": "#e7edf1",
    "button_bg": "#11191f",
    "button_border": "#2b3d48",
    "button_fg": "#edf3f7",
    "button_hover_bg": "#18242c",
    "button_pressed_bg": "#0c1217",
    "button_running_bg": "#193238",
    "button_running_border": "#45d0bc",
    "button_running_fg": "#f3fbf8",
    "button_disabled_fg": "#6f7f89",
    "button_disabled_border": "#22313a",
    "button_disabled_bg": "#0f1519",
    "input_bg": "#11191f",
    "input_border": "#2b3d48",
    "input_fg": "#edf3f7",
    "input_selection_bg": "#2f6c63",
    "field_fg": "#9bb0bc",
    "status_bg": "#11191f",
    "status_fg": "#c9d5dc",
    "toggle_bg": "#11191f",
    "toggle_border": "#2b3d48",
    "toggle_fg": "#edf3f7",
    "toggle_hover_bg": "#18242c",
    "toggle_pressed_bg": "#0c1217",
}

LIGHT_THEME = {
    "window_bg": "#f2ede5",
    "window_fg": "#2c3942",
    "frame_bg": "#faf7f1",
    "frame_border": "#d7cec1",
    "summary_panel_bg": "#fcf9f3",
    "summary_panel_border": "#ddd4c8",
    "status_strip_bg": "#f7f1e8",
    "status_strip_border": "#ddd2c4",
    "status_separator": "#ddd4c7",
    "status_item_idle_bar": "#c8bfb3",
    "status_title_fg": "#7c7368",
    "title_fg": "#2d3940",
    "metric_label_fg": "#7c7368",
    "metric_value_fg": "#2d3940",
    "metric_active_fg": "#2d7f6d",
    "metric_warning_fg": "#a97118",
    "metric_idle_fg": "#4e5a62",
    "textedit_bg": "#fffdf9",
    "textedit_border": "#ddd4c8",
    "textedit_fg": "#314049",
    "group_bg": "#fffdf9",
    "group_border": "#d7cec1",
    "group_title_fg": "#2d3940",
    "button_bg": "#f8f3eb",
    "button_border": "#d9d0c3",
    "button_fg": "#2c3942",
    "button_hover_bg": "#efe6d9",
    "button_pressed_bg": "#e3d8c8",
    "button_running_bg": "#dcede3",
    "button_running_border": "#3f8a72",
    "button_running_fg": "#28483e",
    "button_disabled_fg": "#91897e",
    "button_disabled_border": "#ddd4c8",
    "button_disabled_bg": "#f1ece4",
    "input_bg": "#fffdf9",
    "input_border": "#d9d0c3",
    "input_fg": "#2c3942",
    "input_selection_bg": "#dcede3",
    "field_fg": "#70685d",
    "status_bg": "#f3ede4",
    "status_fg": "#625b52",
    "toggle_bg": "#f8f3eb",
    "toggle_border": "#d9d0c3",
    "toggle_fg": "#2c3942",
    "toggle_hover_bg": "#efe6d9",
    "toggle_pressed_bg": "#e3d8c8",
}


def build_mainvm_theme(palette):
    theme_values = dict(palette, header_action_height=HEADER_ACTION_HEIGHT)
    return """
QMainWindow, QWidget {{
    background-color: {window_bg};
    color: {window_fg};
    font-family: "IBM Plex Sans", "Source Han Sans SC", "Segoe UI", sans-serif;
}}

QFrame {{
    background-color: {frame_bg};
    border: 1px solid {frame_border};
    border-radius: 14px;
}}

QFrame#summaryPanel {{
    background-color: {summary_panel_bg};
    border: 1px solid {summary_panel_border};
}}

QLabel#summaryTitle {{
    color: {title_fg};
    font-size: 24px;
    font-weight: 700;
    letter-spacing: 0.3px;
}}

QTextEdit#textEdit {{
    background-color: {textedit_bg};
    border: 1px solid {textedit_border};
    border-radius: 12px;
    padding: 12px;
    color: {textedit_fg};
    font-family: "JetBrains Mono", "Cascadia Mono", "Consolas", monospace;
    font-size: 12px;
}}

QGroupBox {{
    background-color: {group_bg};
    border: 1px solid {group_border};
    border-radius: 14px;
    margin-top: 0px;
    padding-top: 26px;
    font-size: 14px;
    font-weight: 700;
}}

QGroupBox::title {{
    subcontrol-origin: padding;
    subcontrol-position: top left;
    left: 16px;
    top: 6px;
    padding: 0px;
    background-color: transparent;
    color: {group_title_fg};
    border: none;
}}

QPushButton {{
    background-color: {button_bg};
    border: 1px solid {button_border};
    border-radius: 12px;
    color: {button_fg};
    padding: 8px 12px;
    min-height: 40px;
    font-size: 12px;
    font-weight: 700;
    text-align: center;
}}

QPushButton:hover {{
    background-color: {button_hover_bg};
}}

QPushButton:pressed {{
    background-color: {button_pressed_bg};
}}

QPushButton[running="true"] {{
    background-color: {button_running_bg};
    border-color: {button_running_border};
    color: {button_running_fg};
}}

QPushButton:disabled {{
    color: {button_disabled_fg};
    border-color: {button_disabled_border};
    background-color: {button_disabled_bg};
}}

QPushButton#shutdownButton {{
    padding: 0px 12px;
    min-height: {header_action_height}px;
    max-height: {header_action_height}px;
    border-radius: 11px;
    font-size: 11px;
}}

QToolButton#themeToggleButton {{
    background-color: {toggle_bg};
    border: 1px solid {toggle_border};
    border-radius: 11px;
    color: {toggle_fg};
    padding: 0px;
    min-width: {header_action_height}px;
    max-width: {header_action_height}px;
    min-height: {header_action_height}px;
    max-height: {header_action_height}px;
    font-size: 14px;
    font-weight: 700;
}}

QToolButton#themeToggleButton:hover {{
    background-color: {toggle_hover_bg};
}}

QToolButton#themeToggleButton:pressed {{
    background-color: {toggle_pressed_bg};
}}

QLineEdit, QComboBox {{
    background-color: {input_bg};
    border: 1px solid {input_border};
    border-radius: 10px;
    padding: 6px 10px;
    min-height: 24px;
    color: {input_fg};
    selection-background-color: {input_selection_bg};
}}

QComboBox::drop-down {{
    border: none;
    width: 24px;
}}

QLabel[role="field"] {{
    color: {field_fg};
    font-size: 11px;
    font-weight: 600;
}}

QStatusBar {{
    background-color: {status_bg};
    color: {status_fg};
}}
""".format_map(theme_values)


def build_status_strip_theme(palette):
    theme_values = dict(
        palette,
        status_tone_info_bar="#60a5fa",
        status_tone_success_bar=palette["metric_active_fg"],
        status_tone_warning_bar=palette["metric_warning_fg"],
        status_tone_danger_bar="#f87171" if palette["window_bg"] != "#0f1519" else "#ef8a7e",
        status_tone_info_fg="#1d4ed8" if palette["window_bg"] != "#0f1519" else "#8bc5ff",
        status_tone_success_fg="#166534" if palette["window_bg"] != "#0f1519" else palette["metric_active_fg"],
        status_tone_warning_fg="#b45309" if palette["window_bg"] != "#0f1519" else palette["metric_warning_fg"],
        status_tone_danger_fg="#b91c1c" if palette["window_bg"] != "#0f1519" else "#ef8a7e",
        status_tone_subtle_fg="#475569" if palette["window_bg"] != "#0f1519" else palette["metric_idle_fg"],
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
QFrame#statusItem[tone="info"] {{
    border-left-color: {status_tone_info_bar};
}}
QFrame#statusItem[tone="success"] {{
    border-left-color: {status_tone_success_bar};
}}
QFrame#statusItem[tone="warning"] {{
    border-left-color: {status_tone_warning_bar};
}}
QFrame#statusItem[tone="danger"] {{
    border-left-color: {status_tone_danger_bar};
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
QLabel[role="value"][tone="info"] {{
    color: {status_tone_info_fg};
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
QLabel[role="value"][tone="danger"] {{
    color: {status_tone_danger_fg};
    background: transparent;
    border: none;
    font-size: 14px;
    font-weight: 700;
}}
""".format_map(theme_values)


class VMStatusStrip(QWidget):
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
        container.setMinimumWidth(102)

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
        return value_label

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

    def set_connection(self, text, tone="subtle"):
        self.set_item("connection", text, tone=tone)

    def set_mode(self, text, tone="subtle"):
        self.set_item("mode", text, tone=tone)

    def set_config(self, text, tone="subtle"):
        self.set_item("config", text, tone=tone)

    def set_current(self, text, tone="subtle"):
        self.set_item("current", text, tone=tone)

    @staticmethod
    def _refresh_tone(container, value_label):
        container.style().unpolish(container)
        container.style().polish(container)
        value_label.style().unpolish(value_label)
        value_label.style().polish(value_label)
        container.update()
        value_label.update()

BUTTON_CONFIG = {
    "start_ioc": {
        "text": "Start softIOC",
        "category": "runtime",
        "tooltip": "Start the softIOC supervisor before launching the VM runtime.",
    },
    "start_vm": {
        "text": "Start VM Runtime",
        "category": "runtime",
        "tooltip": "Start the elegant-based VM watcher after softIOC is online.",
    },
    "sim2real": {
        "text": "sim2real",
        "category": "runtime",
        "tooltip": "Placeholder button. No action is implemented yet.",
    },
    "real2sim": {
        "text": "real2sim",
        "category": "runtime",
        "tooltip": "Placeholder button. No action is implemented yet.",
    },
    "shutdown_VM": {
        "text": "Stop VM Session",
        "category": "session",
        "tooltip": "Stop every process started from this VM control window.",
    },
    "pushButton_ESAline": {
        "text": "Apply Usedline",
        "category": "routing",
        "tooltip": "Switch the VM lattice to the selected predefined usedline.",
    },
    "pushButton_FULLline": {
        "text": "Reload Initial Lattice",
        "category": "routing",
        "tooltip": "Reload the VM runtime JSON from the configured lattice_ini.lte and one_ini.ele files.",
    },
    "pushButton_simply_VM": {
        "text": "Simplify Segment",
        "category": "routing",
        "tooltip": "Build a temporary usedline between the selected start and end elements.",
    },
    "static_err": {
        "text": "Apply Error",
        "category": "error",
        "tooltip": "Apply the requested static offset and quadrupole jitter settings.",
    },
    "err_off": {
        "text": "Reset Error",
        "category": "error",
        "tooltip": "Reset VM error inputs and request an error-free lattice state.",
    },
}


class myWindow(QMainWindow, Ui_MainWindow):
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        self.runtime = resolve_machine_runtime()
        self.machine_profile = self.runtime.profile
        self.usedline_workflow = resolve_virtual_machine_usedline_workflow(self.machine_profile)
        self.comboBox_predefined_usedline = QComboBox(self.groupBox_2)
        self.comboBox_segment = QComboBox(self.groupBox_2)
        self.processes = {}
        self.process_start_times = {}
        self.current_theme = "dark"
        self._is_shutting_down = False
        self._install_signal_handlers()

        self._connect_signals()
        self._configure_window()
        self._build_summary_panel()
        self._configure_group_titles()
        self._configure_inputs()
        self._configure_action_buttons()
        self._configure_group_panel()
        self._schedule_layout_refresh()
        self._reset_activity_log()

        self.QDXDYvalue.setText("0")
        self.QK1JITTER.setText("0")

        self.process_timer = QTimer(self)
        self.process_timer.timeout.connect(self._refresh_process_state)
        self.process_timer.start(PROCESS_REFRESH_INTERVAL_MS)
        self._refresh_process_state()

    def _connect_signals(self):
        self.start_ioc.clicked.connect(self.startioc)
        self.start_vm.clicked.connect(self.startvm)
        self.shutdown_VM.clicked.connect(self.stopvm)
        self.static_err.clicked.connect(self.staticerr)
        self.err_off.clicked.connect(self.erroff)
        self.pushButton_ESAline.clicked.connect(self.ESAline)
        self.pushButton_simply_VM.clicked.connect(self.simply_VM)
        self.pushButton_FULLline.clicked.connect(self.back_FULL)
        self.comboBox_segment.currentIndexChanged.connect(self._refresh_segment_choices)

    def _configure_window(self):
        self.setWindowTitle(f"{self.machine_profile.machine.display_name} VM Control")
        self.resize(1080, 820)
        self.setMinimumSize(940, 760)
        self._apply_theme()
        self.frame_2.hide()
        self.textEdit.setReadOnly(True)
        self.textEdit.setAcceptRichText(False)
        self.textEdit.setUndoRedoEnabled(False)
        self.statusBar().showMessage("VM control ready in VM-safe mode.", 5000)

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

        title = QLabel(f"{self.machine_profile.machine.display_name} VM Control", panel)
        title.setObjectName("summaryTitle")
        header_layout.addWidget(title)
        header_layout.addStretch(1)

        self.shutdown_VM.setObjectName("shutdownButton")
        self.shutdown_VM.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.shutdown_VM.setMinimumWidth(180)
        self.shutdown_VM.setFixedHeight(HEADER_ACTION_HEIGHT)
        header_layout.addWidget(self.shutdown_VM)

        self.theme_toggle_button = QToolButton(panel)
        self.theme_toggle_button.setObjectName("themeToggleButton")
        self.theme_toggle_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.theme_toggle_button.setFixedSize(HEADER_ACTION_HEIGHT, HEADER_ACTION_HEIGHT)
        self.theme_toggle_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.theme_toggle_button.clicked.connect(self._toggle_theme)
        header_layout.addWidget(self.theme_toggle_button)

        outer_layout.addLayout(header_layout)

        self.status_panel = VMStatusStrip(panel)
        self.status_panel.add_item("connection", "CONNECTION", "Offline")
        self.status_panel.add_item("mode", "MODE", "Idle")
        self.status_panel.add_item("config", "CONFIG", "Idle")
        self.status_panel.add_item("current", "CURRENT", "Start softIOC")
        self.status_panel.finish()
        self.status_panel.apply_theme(DARK_THEME if self.current_theme == "dark" else LIGHT_THEME)
        self.status_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.status_panel.setFixedHeight(self.status_panel.sizeHint().height())
        self._update_theme_toggle_button()

        outer_layout.addWidget(self.status_panel)
        self.verticalLayout_2.insertWidget(1, panel)

    def _configure_group_titles(self):
        self.groupBox.setTitle("Startup Sequence")
        self.groupBox_2.setTitle("Lattice Usedline")
        self.groupBox_3.setTitle("Error Settings")
        self.groupBox_4.setTitle("Static Offset")
        self.groupBox_5.setTitle("Quadrupole Jitter")

    def _configure_inputs(self):
        self.label_4.setText("Quad DX/DY")
        self.label_8.setText("Quad K1")
        self.label_9.setText("um rms")
        self.label_10.setText("ppm rms")

        for label in (self.label_4, self.label_8, self.label_9, self.label_10):
            label.setProperty("role", "field")
            self._refresh_widget_style(label)

        self.comboBox_simply_start.setToolTip("Start element for the simplified VM segment.")
        self.comboBox_simply_end.setToolTip("End element for the simplified VM segment.")
        self.comboBox_predefined_usedline.setToolTip(
            "Predefined full usedline from the VM lattice, such as ALL_MAIN or ALL_ESA."
        )
        self.comboBox_segment.setToolTip(
            "Local segment definition. Start/end candidates are scoped to its parent usedline."
        )
        self.QDXDYvalue.setToolTip("Quadrupole static offset in micrometers rms.")
        self.QK1JITTER.setToolTip("Quadrupole K1 jitter in ppm rms.")

        self._populate_predefined_usedline_combo()
        self._populate_segment_combo()
        self._refresh_segment_choices()

        self.QDXDYvalue.setMaximumWidth(110)
        self.QK1JITTER.setMaximumWidth(110)
        self.QDXDYvalue.setAlignment(Qt.AlignCenter)
        self.QK1JITTER.setAlignment(Qt.AlignCenter)

    @staticmethod
    def _set_combo_items(combo, items):
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(list(items))
        combo.blockSignals(False)

    @staticmethod
    def _set_combo_current_text(combo, value):
        index = combo.findText(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    @staticmethod
    def _set_combo_current_data(combo, value):
        for index in range(combo.count()):
            if combo.itemData(index) == value:
                combo.setCurrentIndex(index)
                return

    def _populate_predefined_usedline_combo(self):
        self.comboBox_predefined_usedline.blockSignals(True)
        self.comboBox_predefined_usedline.clear()
        for choice in self.usedline_workflow.predefined_usedlines:
            label = choice.label if choice.label == choice.id else f"{choice.label} ({choice.id})"
            self.comboBox_predefined_usedline.addItem(label, choice.id)
        self._set_combo_current_data(
            self.comboBox_predefined_usedline,
            self.usedline_workflow.default_usedline,
        )
        self.comboBox_predefined_usedline.blockSignals(False)

    def _populate_segment_combo(self):
        self.comboBox_segment.blockSignals(True)
        self.comboBox_segment.clear()
        for segment in self.usedline_workflow.local_segments:
            label = f"{segment.label} ({segment.parent_usedline})"
            self.comboBox_segment.addItem(label, segment.id)
        self._set_combo_current_data(
            self.comboBox_segment,
            self.usedline_workflow.default_segment_id,
        )
        self.comboBox_segment.blockSignals(False)

    def _current_local_segment(self):
        segment_id = self.comboBox_segment.currentData()
        for segment in self.usedline_workflow.local_segments:
            if segment.id == segment_id:
                return segment
        if self.usedline_workflow.local_segments:
            return self.usedline_workflow.local_segments[0]
        return None

    def _refresh_segment_choices(self):
        segment = self._current_local_segment()
        if segment is None:
            self._set_combo_items(self.comboBox_simply_start, ())
            self._set_combo_items(self.comboBox_simply_end, ())
            return

        self._set_combo_items(self.comboBox_simply_start, segment.start_ids)
        self._set_combo_items(self.comboBox_simply_end, segment.end_ids)
        self._set_combo_current_text(self.comboBox_simply_start, segment.default_start_id)
        self._set_combo_current_text(self.comboBox_simply_end, segment.default_end_id)

    def _configure_action_buttons(self):
        self.action_buttons = {}
        for name, spec in BUTTON_CONFIG.items():
            button = getattr(self, name)
            button.setText(spec["text"])
            button.setToolTip(spec["tooltip"])
            button.setProperty("category", spec["category"])
            button.setProperty("running", False)
            if name == "shutdown_VM":
                button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
                button.setFixedHeight(HEADER_ACTION_HEIGHT)
            else:
                button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.action_buttons[name] = button
            self._refresh_widget_style(button)

    def _apply_theme(self):
        palette = DARK_THEME if self.current_theme == "dark" else LIGHT_THEME
        self.setStyleSheet(build_mainvm_theme(palette))
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

        self._refresh_widget_style(self.theme_toggle_button)

    def _toggle_theme(self):
        self.current_theme = "light" if self.current_theme == "dark" else "dark"
        self._apply_theme()
        self._refresh_process_state()

    def _configure_group_panel(self):
        self.verticalLayout_2.removeWidget(self.groupBox)
        self.verticalLayout_2.removeWidget(self.groupBox_2)
        self.verticalLayout_2.removeWidget(self.groupBox_3)

        self.group_panel_layout = QGridLayout()
        self.group_panel_layout.setContentsMargins(0, 2, 0, 0)
        self.group_panel_layout.setHorizontalSpacing(12)
        self.group_panel_layout.setVerticalSpacing(12)
        self.group_panel_layout.setColumnStretch(0, 1)
        self.group_panel_layout.setColumnStretch(1, 1)
        self.verticalLayout_2.addLayout(self.group_panel_layout)

        for group_box in (
            self.groupBox,
            self.groupBox_2,
            self.groupBox_3,
            self.groupBox_4,
            self.groupBox_5,
        ):
            group_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        self.verticalLayout_5.setContentsMargins(10, 8, 10, 10)
        self.verticalLayout_6.setContentsMargins(10, 8, 10, 10)
        self.verticalLayout_7.setContentsMargins(10, 8, 10, 10)
        self.verticalLayout.setContentsMargins(10, 8, 10, 10)
        self.verticalLayout_8.setContentsMargins(10, 8, 10, 10)

    def _install_signal_handlers(self):
        signal.signal(signal.SIGTERM, self._handle_shutdown_signal)
        signal.signal(signal.SIGINT, self._handle_shutdown_signal)

    def _handle_shutdown_signal(self, signum, frame):
        self._shutdown()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _append_log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        print(line)
        self.textEdit.append(line)

    def _notify(self, message):
        self._append_log(message)
        self.statusBar().showMessage(message, 5000)

    def _reset_activity_log(self):
        self.textEdit.clear()
        self._append_log("VM control ready.")
        self._append_log("Start softIOC, then start the VM runtime.")
        self._append_log("Routing and error tools unlock after the VM is online.")
        self._append_log(f"Current VM usedline: {self._current_usedline_summary()}.")

    def _prune_finished_processes(self):
        for key, proc in list(self.processes.items()):
            if proc.poll() is not None:
                self.processes.pop(key, None)
                self.process_start_times.pop(key, None)

    def _is_running(self, key):
        proc = self.processes.get(key)
        return proc is not None and proc.poll() is None

    def _is_ready(self, key, grace_period=PROCESS_READY_GRACE_S):
        if not self._is_running(key):
            return False

        started_at = self.process_start_times.get(key)
        if started_at is None:
            return False

        return (time.monotonic() - started_at) >= grace_period

    def _start_process(self, key, label, cmd, cwd, expect_running):
        self._prune_finished_processes()
        if self._is_running(key):
            self._notify(f"{label} is already running.")
            return None

        proc = Popen(
            cmd,
            cwd=cwd,
            shell=False,
            start_new_session=True,
        )

        if expect_running:
            try:
                proc.wait(timeout=PROCESS_START_TIMEOUT_S)
            except TimeoutExpired:
                self.processes[key] = proc
                self.process_start_times[key] = time.monotonic()
                self._refresh_process_state()
                return proc

            self._notify(f"Failed to start {label} (exit code {proc.returncode}).")
            self._refresh_process_state()
            return None

        self.processes[key] = proc
        self.process_start_times[key] = time.monotonic()
        self._refresh_process_state()
        return proc

    def _signal_process_group(self, proc, sig):
        if proc.poll() is not None:
            return

        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        except Exception:
            if sig == signal.SIGKILL:
                proc.kill()
            else:
                proc.terminate()

    def _stop_subpro(self):
        self._prune_finished_processes()
        procs = list(self.processes.values())

        for proc in procs:
            self._signal_process_group(proc, signal.SIGTERM)

        deadline = time.time() + PROCESS_STOP_TIMEOUT_S
        while time.time() < deadline:
            if all(proc.poll() is not None for proc in procs):
                break
            time.sleep(0.1)

        for proc in procs:
            if proc.poll() is None:
                self._signal_process_group(proc, signal.SIGKILL)

        self.processes.clear()
        self.process_start_times.clear()
        self._refresh_process_state()

    def _shutdown(self):
        if self._is_shutting_down:
            return
        self._is_shutting_down = True
        self._stop_subpro()

    def _refresh_process_state(self):
        self._prune_finished_processes()

        ioc_running = self._is_running("softioc")
        ioc_ready = self._is_ready("softioc")
        vm_running = self._is_running("vm")
        vm_ready = self._is_ready("vm")
        config_running = self._is_running("vm_config")
        any_running = any(proc.poll() is None for proc in self.processes.values())

        self.start_ioc.setEnabled(not ioc_running)
        self.start_vm.setEnabled(ioc_ready and not vm_running)
        self.shutdown_VM.setEnabled(any_running)

        vm_controls_enabled = vm_ready and not config_running
        self.pushButton_ESAline.setEnabled(vm_controls_enabled)
        self.pushButton_simply_VM.setEnabled(vm_controls_enabled)
        self.pushButton_FULLline.setEnabled(vm_controls_enabled)
        self.static_err.setEnabled(vm_controls_enabled)
        self.err_off.setEnabled(vm_controls_enabled)

        self._update_button_state(self.start_ioc, ioc_running)
        self._update_button_state(self.start_vm, vm_running)
        self._update_button_state(self.shutdown_VM, any_running)
        for button in (
            self.pushButton_ESAline,
            self.pushButton_simply_VM,
            self.pushButton_FULLline,
            self.static_err,
            self.err_off,
        ):
            self._update_button_state(button, config_running)

        if config_running:
            self._set_summary_value("config", "Applying", "warning")
        else:
            self._set_summary_value("config", self._current_usedline_summary(), "active")

        if not ioc_running and not vm_running:
            self._set_summary_value("connection", "Offline", "idle")
        elif not ioc_ready:
            self._set_summary_value("connection", "softIOC starting", "warning")
        elif not vm_running:
            self._set_summary_value("connection", "softIOC only", "warning")
        elif not vm_ready:
            self._set_summary_value("connection", "VM booting", "warning")
        else:
            self._set_summary_value("connection", "softIOC + VM", "active")

        if config_running:
            self._set_summary_value("mode", "Config Busy", "warning")
        elif vm_ready:
            self._set_summary_value("mode", "Runtime Ready", "active")
        elif vm_running:
            self._set_summary_value("mode", "Runtime Boot", "warning")
        elif ioc_ready:
            self._set_summary_value("mode", "Start VM", "warning")
        elif ioc_running:
            self._set_summary_value("mode", "Startup", "warning")
        else:
            self._set_summary_value("mode", "Idle", "idle")

        if config_running:
            self._set_summary_value("current", "Controls locked", "warning")
        elif vm_ready:
            self._set_summary_value("current", "Controls Ready", "active")
        elif vm_running:
            self._set_summary_value("current", "Waiting for VM", "warning")
        elif ioc_ready:
            self._set_summary_value("current", "Ready to Start VM", "warning")
        else:
            self._set_summary_value("current", "Start softIOC", "idle")

    def _update_button_state(self, button, is_running):
        button.setProperty("running", is_running)
        self._refresh_widget_style(button)

    def _start_vm_config(self, label, cmd):
        if not self._is_running("vm"):
            self._notify(f"Start VM before running {label}.")
            return None

        return self._start_process(
            key="vm_config",
            label=label,
            cmd=cmd,
            cwd=str(self.runtime.vm.root),
            expect_running=False,
        )

    def startioc(self):
        self._notify("Starting softIOC.")
        softioc_manager = self.runtime.softioc.root.parent / "mainIOC.py"
        self._start_process(
            key="softioc",
            label="softIOC",
            cmd=["python3", softioc_manager.name],
            cwd=str(softioc_manager.parent),
            expect_running=True,
        )

    def startvm(self):
        if not self._is_running("softioc"):
            self._notify("Start softIOC before starting the VM.")
            return

        self._notify("Starting VM runtime.")
        self._start_process(
            key="vm",
            label="VM runtime",
            cmd=["python3", self.runtime.vm.manager_entrypoint.name],
            cwd=str(self.runtime.vm.root),
            expect_running=True,
        )

    def ESAline(self):
        line_id = self.comboBox_predefined_usedline.currentData()
        if not line_id:
            self._notify("No predefined usedline is selected.")
            return

        self._notify(f"Requesting predefined usedline: {line_id}.")
        self._start_vm_config(
            label="predefined usedline transfer",
            cmd=["python3", "transfer_ESAline.py", line_id],
        )

    def simply_VM(self):
        segment = self._current_local_segment()
        if segment is None:
            self._notify("No local VM segment is configured.")
            return

        ele_start = self.comboBox_simply_start.currentText()
        ele_end = self.comboBox_simply_end.currentText()
        self._notify(
            f"Requesting simplified usedline from {segment.parent_usedline}: "
            f"{ele_start} -> {ele_end}."
        )
        self._start_vm_config(
            label="VM usedline simplification",
            cmd=["python3", "simply_VM.py", segment.parent_usedline, ele_start, ele_end],
        )

    def back_FULL(self):
        self._notify("Requesting full VM reload from initial lattice files.")
        self._start_vm_config(
            label="Initial lattice reload",
            cmd=["python3", "full_VM.py"],
        )

    def staticerr(self):
        self._notify(
            "Applying error model: "
            f"Quad DX/DY {self.QDXDYvalue.text()} um rms, "
            f"Quad K1 {self.QK1JITTER.text()} ppm rms."
        )
        self._start_vm_config(
            label="VM error update",
            cmd=[
                "python3",
                "err_gene_VM.py",
                "gene_err",
                self.QDXDYvalue.text(),
                self.QK1JITTER.text(),
            ],
        )

    def erroff(self):
        self._notify("Resetting VM error model.")
        self.QDXDYvalue.setText("0")
        self.QK1JITTER.setText("0")
        self._start_vm_config(
            label="VM error reset",
            cmd=["python3", "err_gene_VM.py", "err_off"],
        )

    def stopvm(self):
        if not self.processes:
            self._notify("No VM session processes are running.")
            return

        self._notify("Stopping VM session.")
        self._stop_subpro()

    def _set_summary_value(self, key, text, state):
        tone = {
            "idle": "subtle",
            "active": "success",
            "warning": "warning",
        }.get(state, "subtle")
        self.status_panel.set_item(key, text, tone=tone)

    def _current_usedline_summary(self):
        try:
            return describe_runtime_usedline(self.runtime)
        except Exception as exc:
            return f"Usedline unknown: {exc}"

    @staticmethod
    def _refresh_widget_style(widget):
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()

    def _schedule_layout_refresh(self):
        QTimer.singleShot(0, self._refresh_dynamic_layouts)

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule_layout_refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._schedule_layout_refresh()

    def _refresh_dynamic_layouts(self):
        self._update_summary_layout()
        self._update_group_panel_layout()
        QTimer.singleShot(0, self._refresh_inner_group_layouts)

    def _refresh_inner_group_layouts(self):
        self._update_runtime_layout()
        self._update_routing_layout()
        self._update_error_action_layout()

    def _update_summary_layout(self):
        return

    def _update_group_panel_layout(self):
        if not hasattr(self, "group_panel_layout"):
            return

        while self.group_panel_layout.count():
            self.group_panel_layout.takeAt(0)

        if self.width() < 940:
            self.group_panel_layout.setColumnStretch(0, 1)
            self.group_panel_layout.setColumnStretch(1, 0)
            self.group_panel_layout.setRowStretch(0, 0)
            self.group_panel_layout.setRowStretch(1, 0)
            self.group_panel_layout.setRowStretch(2, 0)
            self.group_panel_layout.addWidget(self.groupBox, 0, 0, Qt.AlignTop)
            self.group_panel_layout.addWidget(self.groupBox_2, 1, 0, Qt.AlignTop)
            self.group_panel_layout.addWidget(self.groupBox_3, 2, 0, Qt.AlignTop)
            return

        self.group_panel_layout.setColumnStretch(0, 3)
        self.group_panel_layout.setColumnStretch(1, 5)
        self.group_panel_layout.setRowStretch(0, 0)
        self.group_panel_layout.setRowStretch(1, 0)

        right_column = QVBoxLayout()
        right_column.setContentsMargins(0, 0, 0, 0)
        right_column.setSpacing(12)
        right_column.addWidget(self.groupBox_2)
        right_column.addWidget(self.groupBox_3)

        self.group_panel_layout.addWidget(self.groupBox, 0, 0, Qt.AlignTop)
        self.group_panel_layout.addLayout(right_column, 0, 1, Qt.AlignTop)

    def _clear_grid_layout(self, layout):
        while layout.count():
            layout.takeAt(0)

    def _update_runtime_layout(self):
        self._clear_grid_layout(self.gridLayout_3)
        width = max(self.groupBox.width(), self.groupBox.sizeHint().width())

        self.gridLayout_3.setContentsMargins(0, 0, 0, 0)
        self.gridLayout_3.setHorizontalSpacing(10)
        self.gridLayout_3.setVerticalSpacing(10)

        if width < 340:
            self.groupBox.setMinimumHeight(250)
            self.gridLayout_3.addWidget(self.start_ioc, 0, 0)
            self.gridLayout_3.addWidget(self.start_vm, 1, 0)
            self.gridLayout_3.addWidget(self.sim2real, 2, 0)
            self.gridLayout_3.addWidget(self.real2sim, 3, 0)
            return

        self.groupBox.setMinimumHeight(164)
        self.gridLayout_3.addWidget(self.start_ioc, 0, 0)
        self.gridLayout_3.addWidget(self.start_vm, 0, 1)
        self.gridLayout_3.addWidget(self.sim2real, 1, 0)
        self.gridLayout_3.addWidget(self.real2sim, 1, 1)
        self.gridLayout_3.setColumnStretch(0, 1)
        self.gridLayout_3.setColumnStretch(1, 1)

    def _update_routing_layout(self):
        self._clear_grid_layout(self.gridLayout_5)
        width = max(self.groupBox_2.width(), self.groupBox_2.sizeHint().width())

        self.gridLayout_5.setContentsMargins(0, 0, 0, 0)
        self.gridLayout_5.setHorizontalSpacing(10)
        self.gridLayout_5.setVerticalSpacing(10)

        if width < 420:
            self.groupBox_2.setMinimumHeight(360)
            self.gridLayout_5.addWidget(self.comboBox_predefined_usedline, 0, 0)
            self.gridLayout_5.addWidget(self.pushButton_ESAline, 1, 0)
            self.gridLayout_5.addWidget(self.pushButton_FULLline, 2, 0)
            self.gridLayout_5.addWidget(self.comboBox_segment, 3, 0)
            self.gridLayout_5.addWidget(self.comboBox_simply_start, 4, 0)
            self.gridLayout_5.addWidget(self.comboBox_simply_end, 5, 0)
            self.gridLayout_5.addWidget(self.pushButton_simply_VM, 6, 0)
            return

        self.groupBox_2.setMinimumHeight(260)
        self.gridLayout_5.addWidget(self.comboBox_predefined_usedline, 0, 0)
        self.gridLayout_5.addWidget(self.pushButton_ESAline, 0, 1)
        self.gridLayout_5.addWidget(self.pushButton_FULLline, 1, 0, 1, 2)
        self.gridLayout_5.addWidget(self.comboBox_segment, 2, 0, 1, 2)
        self.gridLayout_5.addWidget(self.comboBox_simply_start, 3, 0)
        self.gridLayout_5.addWidget(self.comboBox_simply_end, 3, 1)
        self.gridLayout_5.addWidget(self.pushButton_simply_VM, 4, 0, 1, 2)
        self.gridLayout_5.setColumnStretch(0, 1)
        self.gridLayout_5.setColumnStretch(1, 1)

    def _update_error_action_layout(self):
        self._clear_grid_layout(self.gridLayout_2)
        width = max(self.groupBox_3.width(), self.groupBox_3.sizeHint().width())

        self.gridLayout_2.setContentsMargins(0, 0, 0, 0)
        self.gridLayout_2.setHorizontalSpacing(10)
        self.gridLayout_2.setVerticalSpacing(10)

        if width < 420:
            self.groupBox_3.setMinimumHeight(260)
            self.gridLayout_2.addWidget(self.static_err, 0, 0)
            self.gridLayout_2.addWidget(self.err_off, 1, 0)
            return

        self.groupBox_3.setMinimumHeight(220)
        self.gridLayout_2.addWidget(self.static_err, 0, 0)
        self.gridLayout_2.addWidget(self.err_off, 0, 1)
        self.gridLayout_2.setColumnStretch(0, 1)
        self.gridLayout_2.setColumnStretch(1, 1)

    def closeEvent(self, event):
        self._shutdown()
        event.accept()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    try:
        window = myWindow()
    except MachineProfileError as exc:
        print(f"failed to initialize VM control: {exc}")
        raise SystemExit(1) from exc
    window.show()
    sys.exit(app.exec_())
