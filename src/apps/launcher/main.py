import sys
import os
from datetime import datetime
from pathlib import Path

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
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from half_linac.src.shared.machine_profile import (
    CONTROL_BACKEND_ENV,
    LEGACY_CONTROL_BACKEND_ENV,
    LEGACY_MACHINE_ID_ENV,
    MACHINE_ID_ENV,
    MachineProfileError,
    REAL_STATUS_NOT_SUPPORTED,
    REAL_STATUS_READ_ONLY,
    REAL_STATUS_WRITE_BLOCKED,
    describe_app_model_support,
    describe_app_support,
    load_profile,
    normalize_mode,
    real_commissioning_status,
    real_commissioning_status_label,
    resolve_machine_runtime,
)
from half_linac.src.shared.machine_profile.runtime_selector import (
    RuntimeSelectorWidget,
)
from half_linac.src.shared.process_runtime import ManagedProcessGroup
from gui import Ui_MainWindow

ROOT = _REPO_BOOTSTRAP_ROOT
HEADER_ACTION_HEIGHT = 32

DARK_THEME = {
    "window_bg": "#0f1519",
    "window_fg": "#e6edf2",
    "frame_bg": "#172027",
    "frame_border": "#22303a",
    "summary_panel_bg": "#1b262d",
    "summary_panel_border": "#2b3a45",
    "summary_card_bg": "#152028",
    "summary_card_border": "transparent",
    "status_strip_bg": "#131c22",
    "status_strip_border": "#2a3943",
    "status_separator": "#31424d",
    "status_item_idle_bar": "#4f6270",
    "status_title_fg": "#8ea0ad",
    "title_fg": "#f3efe3",
    "subtitle_fg": "#99a9b5",
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
    "group_title_accent_fg": "#7dd7c5",
    "button_bg": "#22313a",
    "button_border": "#48606e",
    "button_fg": "#edf3f7",
    "button_hover_bg": "#2b3f4b",
    "button_pressed_bg": "#19262e",
    "button_running_bg": "#193238",
    "button_running_border": "#45d0bc",
    "button_running_fg": "#f3fbf8",
    "button_disabled_fg": "#6f7f89",
    "button_disabled_border": "#22313a",
    "button_disabled_bg": "#0f1519",
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
    "summary_card_bg": "#f1eadf",
    "summary_card_border": "transparent",
    "status_strip_bg": "#f7f1e8",
    "status_strip_border": "#ddd2c4",
    "status_separator": "#ddd4c7",
    "status_item_idle_bar": "#c8bfb3",
    "status_title_fg": "#7c7368",
    "title_fg": "#2d3940",
    "subtitle_fg": "#746c62",
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
    "group_title_accent_fg": "#2d7f6d",
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
    "status_bg": "#f3ede4",
    "status_fg": "#625b52",
    "toggle_bg": "#f8f3eb",
    "toggle_border": "#d9d0c3",
    "toggle_fg": "#2c3942",
    "toggle_hover_bg": "#efe6d9",
    "toggle_pressed_bg": "#e3d8c8",
}


def build_launcher_theme(palette):
    theme_values = dict(palette, header_action_height=HEADER_ACTION_HEIGHT)
    return """
QMainWindow, QWidget#centralwidget {{
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

QFrame#summaryCard {{
    background-color: {summary_card_bg};
    border: 1px solid {summary_card_border};
    border-radius: 12px;
}}

QLabel#summaryTitle {{
    color: {title_fg};
    font-size: 23px;
    font-weight: 700;
    letter-spacing: 0.3px;
}}

QLabel#summarySubtitle {{
    color: {subtitle_fg};
    font-size: 12px;
    line-height: 1.4;
}}

QLabel#summaryMetricLabel {{
    color: {metric_label_fg};
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
}}

QLabel#summaryMetricValue {{
    color: {metric_value_fg};
    font-size: 16px;
    font-weight: 700;
}}

QLabel#summaryMetricValue[state="active"] {{
    color: {metric_active_fg};
}}

QLabel#summaryMetricValue[state="warning"] {{
    color: {metric_warning_fg};
}}

QLabel#summaryMetricValue[state="idle"] {{
    color: {metric_idle_fg};
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
    padding-top: 30px;
    font-size: 14px;
    font-weight: 700;
}}

QGroupBox::title {{
    subcontrol-origin: padding;
    subcontrol-position: top left;
    left: 16px;
    top: 7px;
    padding: 0px;
    background-color: transparent;
    color: {group_title_accent_fg};
    border: none;
    font-size: 15px;
    font-weight: 800;
}}

QPushButton {{
    background-color: {button_bg};
    border: 1px solid {button_border};
    border-radius: 12px;
    color: {button_fg};
    padding: 10px 12px;
    min-height: 52px;
    font-size: 12px;
    font-weight: 700;
    text-align: center;
}}

QPushButton[compact="true"] {{
    padding: 0px 12px;
    min-height: {header_action_height}px;
    max-height: {header_action_height}px;
    border-radius: 11px;
    font-size: 11px;
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

QPushButton[realStatus="read_only"] {{
    border-color: {status_item_idle_bar};
}}

QPushButton[realStatus="write_blocked"] {{
    border-color: {metric_warning_fg};
}}

QPushButton[realStatus="write_smoke_passed"],
QPushButton[realStatus="commissioned"] {{
    border-color: {metric_active_fg};
}}

QPushButton#shutdownButton {{
    padding: 0px 12px;
    height: {header_action_height}px;
    min-height: {header_action_height}px;
    max-height: {header_action_height}px;
    border-radius: 11px;
    font-size: 11px;
}}

QComboBox {{
    background-color: {button_bg};
    border: 1px solid {button_border};
    border-radius: 11px;
    color: {button_fg};
    padding: 0px 10px;
    min-height: {header_action_height}px;
    max-height: {header_action_height}px;
    font-size: 11px;
    font-weight: 700;
}}

QComboBox:hover {{
    background-color: {button_hover_bg};
}}

QComboBox::drop-down {{
    border: none;
    width: 22px;
}}

QLabel[role="field"] {{
    color: {metric_label_fg};
    background: transparent;
    border: none;
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
}}

QMessageBox {{
    background-color: {frame_bg};
    color: {window_fg};
}}

QMessageBox QLabel {{
    color: {window_fg};
    background: transparent;
    border: none;
    font-size: 12px;
    font-weight: 600;
}}

QMessageBox QPushButton {{
    background-color: {button_bg};
    border: 1px solid {button_border};
    border-radius: 8px;
    color: {button_fg};
    min-width: 72px;
    min-height: 28px;
    padding: 4px 12px;
    font-weight: 700;
}}

QMessageBox QPushButton:hover {{
    background-color: {button_hover_bg};
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


class LauncherStatusStrip(QWidget):
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

    def set_item(self, key, text, tone="subtle", tooltip=None):
        item = self._items.get(key)
        if item is None:
            return
        container, value_label = item
        container.setProperty("tone", tone)
        value_label.setProperty("tone", tone)
        value_label.setText(text)
        if tooltip is not None:
            container.setToolTip(tooltip)
            value_label.setToolTip(tooltip)
        self._refresh_tone(container, value_label)

    @staticmethod
    def _refresh_tone(container, value_label):
        container.style().unpolish(container)
        container.style().polish(container)
        value_label.style().unpolish(value_label)
        value_label.style().polish(value_label)
        container.update()
        value_label.update()

APP_DEFINITIONS = {
    "vm_manager": {
        "button_name": "vmbtn",
        "category": "core",
        "button_text": "Virtual Accelerator",
        "label": "Virtual Accelerator",
        "description": "Manage the virtual machine and softIOC workflow.",
        "cmd": ["python3", "mainVM.py"],
        "cwd": ROOT / "src/virtual_machine/half_elegant",
    },
    "optimization": {
        "button_name": "online_opt",
        "category": "core",
        "button_text": "Optimization",
        "label": "Optimization",
        "description": "Open the GOTAcc optimization workflow.",
        "cmd": ["python3", "mainOPT.py"],
        "cwd": ROOT / "src/optimization",
    },
    "orbitdisplay": {
        "button_name": "orbitdisplay",
        "category": "diagnostic",
        "button_text": "Orbit Display",
        "label": "Orbit Display",
        "description": "View BPM orbit readings for the selected machine/backend.",
        "cmd": ["python3", "main.py"],
        "cwd": ROOT / "src/apps/orbit_display",
    },
    "beammonitor": {
        "button_name": "beammonitor",
        "category": "diagnostic",
        "button_text": "Beam Monitor",
        "label": "Beam Monitor",
        "description": "View beam images and monitor profile diagnostics.",
        "cmd": ["python3", "main.py"],
        "cwd": ROOT / "src/apps/beam_monitor",
    },
    "jitter": {
        "button_name": "jitter_plot",
        "category": "diagnostic",
        "button_text": "Jitter Analysis",
        "label": "Jitter Analysis",
        "description": "Analyze shot-to-shot beam and signal jitter.",
        "cmd": ["python3", "main.py"],
        "cwd": ROOT / "src/apps/jitter",
    },
    "energy_spectrum": {
        "button_name": "energy_spectrum",
        "category": "diagnostic",
        "button_text": "Energy Spectrum",
        "label": "Energy Spectrum",
        "description": "Analyze the ESA energy spectrum workflow.",
        "cmd": ["python3", "main.py"],
        "cwd": ROOT / "src/apps/energy_spectrum",
    },
    "bba": {
        "button_name": "BBA",
        "category": "control",
        "button_text": "BBA",
        "label": "BBA",
        "description": "Run beam-based alignment scans and recalculation.",
        "cmd": ["python3", "main.py"],
        "cwd": ROOT / "src/apps/bba",
    },
    "orbit_correct": {
        "button_name": "orbit_correct",
        "category": "control",
        "button_text": "Orbit Correct",
        "label": "Orbit Correct",
        "description": "Measure response matrices and apply orbit correction.",
        "cmd": ["python3", "mainOrbCor.py"],
        "cwd": ROOT / "src/apps/orbit_correct",
    },
    "solenoid_centering": {
        "button_name": "solenoid_centering_button",
        "category": "control",
        "button_text": "Solenoid Centering",
        "label": "Solenoid Centering",
        "description": "Find corrector settings that minimize BPM sensitivity to solenoid scans.",
        "cmd": ["python3", "main.py"],
        "cwd": ROOT / "src/apps/solenoid_centering",
    },
    "emitmeasure": {
        "button_name": "emitmeasure",
        "category": "control",
        "button_text": "Emittance",
        "label": "Emittance",
        "description": "Run quadrupole-scan emittance measurement and Twiss analysis.",
        "cmd": ["python3", "main.py"],
        "cwd": ROOT / "src/apps/emit_measure",
    },
    "energy_feedback": {
        "button_name": "energy_feedback_button",
        "category": "control",
        "button_text": "Energy Feedback",
        "label": "Energy Feedback",
        "description": "Reserved launcher entry for energy feedback control.",
        "reserved": True,
        "reserved_reason": "Energy feedback GUI is not connected yet.",
        "cmd": ["python3", "main.py"],
        "cwd": ROOT / "src/apps/energy_feedback",
    },
    "hv_feedback": {
        "button_name": "hv_feedback_button",
        "category": "control",
        "button_text": "HV Feedback",
        "label": "HV Feedback",
        "description": "Reserved launcher entry for high-voltage feedback control.",
        "reserved": True,
        "reserved_reason": "High-voltage feedback GUI is not connected yet.",
        "cmd": ["python3", "main.py"],
        "cwd": ROOT / "src/apps/hv_feedback",
    },
}

PROFILE_MANAGED_APP_KEYS = {
    "orbitdisplay": "orbit_display",
    "beammonitor": "beam_monitor",
    "energy_spectrum": "energy_spectrum",
    "bba": "bba",
    "orbit_correct": "orbit_correct",
    "solenoid_centering": "solenoid_centering",
    "emitmeasure": "emit_measure",
}


class myWindow(QMainWindow, Ui_MainWindow):
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        self.machine_profile = load_profile()
        self.control_backend = normalize_mode(
            os.environ.get(CONTROL_BACKEND_ENV, "")
            or os.environ.get(LEGACY_CONTROL_BACKEND_ENV, "")
            or self.machine_profile.machine.default_mode,
            "control_backend",
        )
        self.process_manager = ManagedProcessGroup(notify=self._notify)
        self.process_manager.install_signal_handlers()

        self.current_theme = "dark"
        self.managed_buttons = {}
        self.app_support_status = {}
        self.app_real_status = {}
        self.group_button_specs = []

        self._configure_window()
        self._build_summary_panel()
        self._configure_groups()
        self._configure_group_panel()
        self._configure_group_layouts()
        self._configure_app_buttons()
        self._refresh_machine_capabilities()
        self._configure_session_buttons()
        self._schedule_group_button_layout_update()
        self._reset_activity_log()

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._refresh_process_state)
        self.refresh_timer.start(1000)
        self._refresh_process_state()

    def _configure_window(self):
        self.setWindowTitle(f"{self.machine_profile.machine.display_name} Control Room")
        self.resize(1240, 800)
        self.setMinimumSize(940, 720)
        self._apply_theme()
        self.textEdit.hide()
        self.textEdit.setMaximumHeight(160)
        self.textEdit.setReadOnly(True)
        self.textEdit.setAcceptRichText(False)
        self.textEdit.setUndoRedoEnabled(False)
        self.statusBar().showMessage(
            f"Control Room ready for {self.machine_profile.machine.display_name} ({self.control_backend}).",
            5000,
        )

    def _refresh_window_identity(self):
        self.setWindowTitle(f"{self.machine_profile.machine.display_name} Control Room")
        if hasattr(self, "summary_title"):
            self.summary_title.setText(f"{self.machine_profile.machine.display_name} Control Room")
        self.statusBar().showMessage(
            f"Control Room ready for {self.machine_profile.machine.display_name} ({self.control_backend}).",
            5000,
        )

    def _build_summary_panel(self):
        panel = QFrame(self.frame)
        panel.setObjectName("summaryPanel")
        panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        outer_layout = QVBoxLayout(panel)
        outer_layout.setContentsMargins(14, 12, 14, 12)
        outer_layout.setSpacing(8)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(12)

        self.summary_title = QLabel(f"{self.machine_profile.machine.display_name} Control Room", panel)
        self.summary_title.setObjectName("summaryTitle")
        header_layout.addWidget(self.summary_title)
        header_layout.addStretch(1)

        self.runtime_selector = RuntimeSelectorWidget(
            current_machine_id=self.machine_profile.machine.id,
            current_control_backend=self.control_backend,
            control_height=HEADER_ACTION_HEIGHT,
            machine_width=132,
            backend_width=118,
            parent=panel,
        )
        self.runtime_selector.apply_requested.connect(self._apply_runtime_selection)
        header_layout.addWidget(self.runtime_selector)

        self.theme_toggle_button = QToolButton(panel)
        self.theme_toggle_button.setObjectName("themeToggleButton")
        self.theme_toggle_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.theme_toggle_button.setFixedSize(HEADER_ACTION_HEIGHT, HEADER_ACTION_HEIGHT)
        self.theme_toggle_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.theme_toggle_button.clicked.connect(self._toggle_theme)

        self.logs_button = QPushButton("Logs", panel)
        self.logs_button.setObjectName("logToggleButton")
        self.logs_button.setCheckable(True)
        self.logs_button.setProperty("compact", True)
        self.logs_button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.logs_button.setMinimumWidth(72)
        self.logs_button.setFixedHeight(HEADER_ACTION_HEIGHT)
        self.logs_button.setToolTip("Show or hide the current launcher activity log.")
        self.logs_button.toggled.connect(self._toggle_activity_log)

        self.shutdown_button = QPushButton("Shutdown Apps", panel)
        self.shutdown_button.setObjectName("shutdownButton")
        self.shutdown_button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.shutdown_button.setMinimumWidth(132)
        self.shutdown_button.setFixedHeight(HEADER_ACTION_HEIGHT)
        header_layout.addWidget(self.logs_button)
        header_layout.addWidget(self.shutdown_button)
        header_layout.addWidget(self.theme_toggle_button)

        outer_layout.addLayout(header_layout)

        self.status_panel = LauncherStatusStrip(panel)
        self.status_panel.add_item("machine", "MACHINE", self.machine_profile.machine.id)
        self.status_panel.add_item("backend", "BACKEND", self.control_backend.upper())
        self.status_panel.add_item("real_access", "REAL ACCESS", "--")
        self.status_panel.add_item("running", "RUNNING", "0 apps")
        self.status_panel.finish()
        self.status_panel.apply_theme(DARK_THEME if self.current_theme == "dark" else LIGHT_THEME)
        self.status_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self._update_theme_toggle_button()

        outer_layout.addWidget(self.status_panel)
        self.verticalLayout.insertWidget(1, panel)
        self.verticalLayout.removeWidget(self.textEdit)
        self.verticalLayout.insertWidget(1, self.textEdit)

    def _configure_groups(self):
        self.groupBox_4.setTitle("Diagnostics")
        self.groupBox_5.setTitle("Beam Tuning")
        self.groupBox_3.setTitle("Core Systems")

    def _configure_group_panel(self):
        self.frame_2.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.verticalLayout_5.setContentsMargins(0, 6, 0, 0)
        self.verticalLayout_5.setSpacing(12)

        while self.verticalLayout_5.count():
            self.verticalLayout_5.takeAt(0)

        self.group_panel_grid = QGridLayout()
        self.group_panel_grid.setContentsMargins(0, 4, 0, 0)
        self.group_panel_grid.setHorizontalSpacing(12)
        self.group_panel_grid.setVerticalSpacing(12)
        self.group_panel_grid.setColumnStretch(0, 1)
        self.group_panel_grid.setColumnStretch(1, 1)

        self.verticalLayout_5.addLayout(self.group_panel_grid)
        self.verticalLayout_5.addStretch(1)
        self._update_group_panel_layout()

    def _configure_group_layouts(self):
        for group_box in (self.groupBox_3, self.groupBox_4, self.groupBox_5):
            group_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
            group_box.layout().setContentsMargins(10, 12, 10, 10)

        self.energy_feedback_button = QPushButton(self.groupBox_5)
        self.energy_feedback_button.setObjectName("energy_feedback_button")
        self.hv_feedback_button = QPushButton(self.groupBox_5)
        self.hv_feedback_button.setObjectName("hv_feedback_button")
        self.solenoid_centering_button = QPushButton(self.groupBox_5)
        self.solenoid_centering_button.setObjectName("solenoid_centering_button")

        self.group_button_specs = [
            (self.gridLayout_3, self.groupBox_3, [self.vmbtn, self.online_opt], 1),
            (self.gridLayout_2, self.groupBox_4, [self.beammonitor, self.orbitdisplay, self.energy_spectrum, self.jitter_plot], 1),
            (
                self.gridLayout_4,
                self.groupBox_5,
                [
                    self.orbit_correct,
                    self.solenoid_centering_button,
                    self.BBA,
                    self.emitmeasure,
                    self.energy_feedback_button,
                    self.hv_feedback_button,
                ],
                1,
            ),
        ]
        self._schedule_group_button_layout_update()

    def _configure_app_buttons(self):
        for key, spec in APP_DEFINITIONS.items():
            button = getattr(self, spec["button_name"])
            button.setText(self._button_display_text(key))
            button.setToolTip(spec["description"])
            button.setProperty("category", spec["category"])
            button.setProperty("running", False)
            button.clicked.connect(lambda _checked=False, app_key=key: self._launch_app(app_key))
            self.managed_buttons[key] = button
            self._refresh_widget_style(button)

    def _refresh_machine_capabilities(self):
        self.app_support_status = {}
        self.app_real_status = {}
        for key, spec in APP_DEFINITIONS.items():
            button = self.managed_buttons.get(key)
            if button is None:
                continue

            tooltip = spec["description"]
            supported = True
            reason = None
            if spec.get("reserved"):
                supported = False
                reason = spec.get("reserved_reason", "This launcher entry is reserved for future implementation.")
                tooltip = f"{tooltip}\n\nReserved: {reason}"
            if key == "vm_manager":
                supported, reason = self._refresh_vm_manager_launch_spec(spec)
                if not supported and reason:
                    tooltip = (
                        f"{spec['description']}\n\n"
                        f"Unavailable for machine '{self.machine_profile.machine.id}': {reason}"
                    )
            profile_app_name = PROFILE_MANAGED_APP_KEYS.get(key)
            if supported and profile_app_name is not None:
                supported, reason = describe_app_support(
                    self.machine_profile.machine.id,
                    profile_app_name,
                )
                if not supported and reason:
                    tooltip = (
                        f"{spec['description']}\n\n"
                        f"Unavailable for machine '{self.machine_profile.machine.id}': {reason}"
                    )

            if supported and profile_app_name is not None:
                model_supported, model_reason = describe_app_model_support(
                    self.machine_profile.machine.id,
                    profile_app_name,
                )
                if not model_supported and model_reason:
                    tooltip = (
                        f"{tooltip}\n\n"
                        f"Model backend unavailable for machine '{self.machine_profile.machine.id}': {model_reason}"
                    )

            if supported and profile_app_name is not None:
                tooltip = self._append_real_commissioning_tooltip(tooltip, profile_app_name)
                if self.machine_profile.machine.id == "irfel" and self.control_backend == "real":
                    try:
                        real_status = real_commissioning_status(self.machine_profile, profile_app_name)
                        self.app_real_status[key] = real_status
                    except MachineProfileError as exc:
                        supported = False
                        reason = f"IRFEL real commissioning status is invalid: {exc}"
                    else:
                        if real_status == REAL_STATUS_NOT_SUPPORTED:
                            supported = False
                            reason = f"{spec['label']} is not supported for IRFEL real mode."

            self.app_support_status[key] = (supported, reason)
            button.setEnabled(supported)
            button.setText(self._button_display_text(key))
            button.setProperty("realStatus", self.app_real_status.get(key, "none"))
            button.setToolTip(tooltip)
            self._refresh_widget_style(button)

    def _append_real_commissioning_tooltip(self, tooltip, app_name):
        if self.machine_profile.machine.id != "irfel" or self.control_backend != "real":
            return tooltip

        try:
            status = real_commissioning_status(self.machine_profile, app_name)
            status_text = real_commissioning_status_label(status)
        except MachineProfileError as exc:
            status_text = f"CONFIG ERROR: {exc}"
        return f"{tooltip}\n\nIRFEL real status: {status_text}"

    def _refresh_vm_manager_launch_spec(self, spec):
        if "vm" not in self.machine_profile.control_backends:
            return False, "VM backend is not configured for this machine."
        try:
            runtime = resolve_machine_runtime(self.machine_profile)
        except MachineProfileError as exc:
            return False, str(exc)
        spec["cmd"] = ["python3", runtime.vm.ui_entrypoint.name]
        spec["cwd"] = runtime.vm.root
        return True, None

    def _button_display_text(self, key, running=False):
        spec = APP_DEFINITIONS[key]
        if running:
            return f"{spec['button_text']}\nrunning"
        return spec["button_text"]

    def _configure_session_buttons(self):
        self.shutdown_button.setText("Shutdown Apps")
        self.shutdown_button.setToolTip("Terminate all subprocesses started from this Control Room window.")
        self.shutdown_button.setProperty("category", "session")
        self.shutdown_button.clicked.connect(self._shutdown_all)
        self._refresh_widget_style(self.shutdown_button)

    def _apply_theme(self):
        palette = DARK_THEME if self.current_theme == "dark" else LIGHT_THEME
        self.setStyleSheet(build_launcher_theme(palette))
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

    def _toggle_activity_log(self, visible):
        self.textEdit.setVisible(visible)
        self.logs_button.setText("Hide Logs" if visible else "Logs")
        if visible:
            scrollbar = self.textEdit.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def _append_log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        print(line)
        self.textEdit.append(line)
        if self.textEdit.isVisible():
            scrollbar = self.textEdit.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def _notify(self, message):
        self._append_log(message)
        self.statusBar().showMessage(message, 5000)

    def _reset_activity_log(self):
        self.textEdit.clear()
        self._append_log("Control Room ready.")
        self._append_log(
            f"Runtime: machine={self.machine_profile.machine.id}, backend={self.control_backend}."
        )
        self._append_log("Start with Virtual Accelerator before running analysis or control apps.")

    def _launch_app(self, key):
        spec = APP_DEFINITIONS[key]
        if spec.get("reserved"):
            self._notify(f"{spec['label']} is reserved but not connected yet.")
            return
        supported, reason = self.app_support_status.get(key, (True, None))
        if not supported:
            message = reason or (
                f"{spec['label']} is not configured for machine {self.machine_profile.machine.id!r}."
            )
            self._notify(message)
            QMessageBox.warning(self, spec["label"], message)
            self._refresh_process_state()
            return
        if self.process_manager.is_running(key):
            self._handle_running_app(key)
            self._refresh_process_state()
            return

        self._notify(f"Launching {spec['label']}.")
        proc = self.process_manager.start_process(
            key=key,
            label=spec["label"],
            cmd=spec["cmd"],
            cwd=str(spec["cwd"]),
        )
        if proc is not None:
            self._notify(f"{spec['label']} started.")
        self._refresh_process_state()

    def _handle_running_app(self, key):
        spec = APP_DEFINITIONS[key]
        prompt = QMessageBox(self)
        prompt.setIcon(QMessageBox.Question)
        prompt.setWindowTitle(spec["label"])
        prompt.setText(f"{spec['label']} is already running.")
        stop_button = prompt.addButton("Stop", QMessageBox.AcceptRole)
        cancel_button = prompt.addButton("Cancel", QMessageBox.RejectRole)
        prompt.setDefaultButton(cancel_button)
        prompt.exec_()

        if prompt.clickedButton() is not stop_button:
            self._notify(f"{spec['label']} is already running.")
            return

        self._notify(f"Stopping {spec['label']}.")
        stopped = self.process_manager.stop_process(key)
        if stopped:
            self._notify(f"{spec['label']} stopped.")
        else:
            self._notify(f"{spec['label']} was not running.")

    def _apply_runtime_selection(self, machine_id, control_backend):
        self.process_manager.prune_finished_processes()
        if self.process_manager.processes:
            message = "Stop all managed subprocesses before switching machine or backend."
            self._notify(message)
            QMessageBox.warning(self, "Control Room", message)
            return

        normalized_backend = normalize_mode(control_backend, "control_backend")
        if machine_id == self.machine_profile.machine.id and normalized_backend == self.control_backend:
            self._notify("Runtime selection is already active.")
            return

        try:
            next_profile = load_profile(machine_id)
        except MachineProfileError as exc:
            message = f"Failed to switch runtime: {exc}"
            self._notify(message)
            QMessageBox.warning(self, "Control Room", message)
            return

        if normalized_backend not in next_profile.control_backends:
            message = (
                f"Backend {normalized_backend!r} is not configured for "
                f"machine {next_profile.machine.id!r}."
            )
            self._notify(message)
            QMessageBox.warning(self, "Control Room", message)
            return

        os.environ[MACHINE_ID_ENV] = next_profile.machine.id
        os.environ[CONTROL_BACKEND_ENV] = normalized_backend
        os.environ[LEGACY_MACHINE_ID_ENV] = next_profile.machine.id
        os.environ[LEGACY_CONTROL_BACKEND_ENV] = normalized_backend
        self.machine_profile = next_profile
        self.control_backend = normalized_backend

        self._refresh_window_identity()
        self._refresh_machine_capabilities()
        self._reset_activity_log()
        self._refresh_process_state()
        self._notify(
            f"Runtime switched to machine={self.machine_profile.machine.id}, "
            f"backend={self.control_backend}."
        )

    def _shutdown_all(self):
        self.process_manager.prune_finished_processes()
        if not self.process_manager.processes:
            self._notify("No managed subprocesses are running.")
            self._refresh_process_state()
            return

        self._notify("Stopping all managed subprocesses.")
        self.process_manager.stop_all()
        self._refresh_process_state()
        self._notify("All managed subprocesses have been stopped.")

    def _set_summary_value(self, key, text, state, tooltip=None):
        tone = {
            "idle": "subtle",
            "active": "success",
            "warning": "warning",
        }.get(state, "subtle")
        self.status_panel.set_item(key, text, tone=tone, tooltip=tooltip)

    def _real_commissioning_summary(self):
        if self.control_backend != "real":
            return "--", "idle"
        if self.machine_profile.machine.id != "irfel":
            return "Untracked", "idle"

        statuses = []
        for app_name in dict.fromkeys(PROFILE_MANAGED_APP_KEYS.values()):
            try:
                statuses.append(real_commissioning_status(self.machine_profile, app_name))
            except MachineProfileError:
                return "Config error", "warning"

        blocked = sum(status == REAL_STATUS_WRITE_BLOCKED for status in statuses)
        readonly = sum(status == REAL_STATUS_READ_ONLY for status in statuses)
        unsupported = sum(status == REAL_STATUS_NOT_SUPPORTED for status in statuses)
        if blocked or unsupported:
            parts = []
            if blocked:
                parts.append(f"{blocked} blocked")
            if readonly:
                parts.append(f"{readonly} read")
            if unsupported:
                parts.append(f"{unsupported} off")
            return " / ".join(parts), "warning"
        if readonly:
            return f"{readonly} read", "idle"
        return "Commissioned", "active"

    def _refresh_process_state(self):
        self.process_manager.prune_finished_processes()
        running_keys = [
            key for key in APP_DEFINITIONS
            if self.process_manager.is_running(key)
        ]

        for key, button in self.managed_buttons.items():
            running = self.process_manager.is_running(key)
            button.setProperty("running", running)
            button.setProperty("realStatus", self.app_real_status.get(key, "none"))
            button.setText(self._button_display_text(key, running=running))
            self._refresh_widget_style(button)

        active_count = len(running_keys)
        self._set_summary_value(
            "running",
            f"{active_count} apps" if active_count else "0 apps",
            "active" if active_count else "idle",
        )

        backend_tone = "warning" if self.control_backend == "real" else "active"
        self._set_summary_value("machine", self.machine_profile.machine.id, "active")
        self._set_summary_value("backend", self.control_backend.upper(), backend_tone)
        real_status_text, real_status_state = self._real_commissioning_summary()
        self._set_summary_value("real_access", real_status_text, real_status_state)

        has_running_processes = active_count > 0
        self.shutdown_button.setEnabled(has_running_processes)
        self.shutdown_button.setProperty("running", has_running_processes)
        self._refresh_widget_style(self.shutdown_button)

    @staticmethod
    def _refresh_widget_style(widget):
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()

    def _schedule_group_button_layout_update(self):
        QTimer.singleShot(0, self._update_group_button_layouts)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_summary_layout()
        self._update_group_panel_layout()
        self._schedule_group_button_layout_update()

    def _update_summary_layout(self):
        return

    def _update_group_panel_layout(self):
        if not hasattr(self, "group_panel_grid"):
            return

        groups = [
            self.groupBox_3,
            self.groupBox_4,
            self.groupBox_5,
        ]

        while self.group_panel_grid.count():
            self.group_panel_grid.takeAt(0)

        if self.width() < 860:
            columns = 1
        elif self.width() < 1020:
            columns = 2
        else:
            columns = 3

        if columns == 3:
            self.group_panel_grid.setColumnStretch(0, 1)
            self.group_panel_grid.setColumnStretch(1, 1)
            self.group_panel_grid.setColumnStretch(2, 1)
            for index, group_box in enumerate(groups):
                self.group_panel_grid.addWidget(group_box, 0, index, Qt.AlignTop)
            return

        if columns == 2:
            self.group_panel_grid.setColumnStretch(0, 1)
            self.group_panel_grid.setColumnStretch(1, 1)
            self.group_panel_grid.setColumnStretch(2, 0)
            self.group_panel_grid.addWidget(self.groupBox_3, 0, 0, Qt.AlignTop)
            self.group_panel_grid.addWidget(self.groupBox_4, 0, 1, Qt.AlignTop)
            self.group_panel_grid.addWidget(self.groupBox_5, 1, 0, 1, 2, Qt.AlignTop)
            return

        self.group_panel_grid.setColumnStretch(0, 1)
        self.group_panel_grid.setColumnStretch(1, 0)
        self.group_panel_grid.setColumnStretch(2, 0)
        for index, group_box in enumerate(groups):
            self.group_panel_grid.addWidget(group_box, index, 0, Qt.AlignTop)

    def _update_group_button_layouts(self):
        if not self.group_button_specs:
            return

        for layout, container, buttons, preferred_columns in self.group_button_specs:
            while layout.count():
                layout.takeAt(0)

            columns = preferred_columns

            layout.setContentsMargins(0, 0, 0, 0)
            layout.setHorizontalSpacing(10)
            layout.setVerticalSpacing(8)

            for column in range(columns):
                layout.setColumnStretch(column, 1)

            for index, button in enumerate(buttons):
                row = index // columns
                column = index % columns
                button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                button.setMinimumHeight(52)
                button.setMaximumWidth(16777215)
                layout.addWidget(button, row, column)

    def closeEvent(self, event):
        self.process_manager.shutdown()
        event.accept()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = myWindow()
    window.show()
    sys.exit(app.exec_())
