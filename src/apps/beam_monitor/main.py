import sys
import time
from pathlib import Path

_REPO_BOOTSTRAP_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file()
)
if str(_REPO_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_BOOTSTRAP_ROOT))

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

import matplotlib as mpl
import numpy as np
from epics import PV, caget, caput, caput_many
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gui import Ui_Form
from half_linac.src.shared.beam_diagnostics import fit_beam_image
from half_linac.src.shared.machine_profile import (
    MachineProfileError,
    get_workflow,
    list_elements,
    load_app_context,
    require_workflow_write_allowed,
    resolve_channel,
    resolve_flag_pixel_geometry,
)
from half_linac.src.shared.machine_profile.runtime_selector import (
    RuntimeSelectorWidget,
    request_runtime_restart,
)


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
    "textedit_bg": "#10171c",
    "textedit_border": "#24343f",
    "textedit_fg": "#d7e2ea",
    "plot_card_bg": "#121a20",
    "plot_card_border": "#263640",
    "plot_bg": "#11181e",
    "plot_grid": "#2a3943",
    "plot_spine": "#445764",
    "plot_text": "#d7e2ea",
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
    "textedit_bg": "#fffdf9",
    "textedit_border": "#ddd4c8",
    "textedit_fg": "#304049",
    "plot_card_bg": "#f6f1e8",
    "plot_card_border": "#ddd2c4",
    "plot_bg": "#fffdf8",
    "plot_grid": "#ddd4c7",
    "plot_spine": "#b5aa9a",
    "plot_text": "#304049",
    "status_strip_bg": "#f7f1e8",
    "status_strip_border": "#ddd2c4",
    "status_separator": "#ddd4c7",
    "status_item_idle_bar": "#c8bfb3",
    "status_title_fg": "#7c7368",
    "metric_active_fg": "#2d7f6d",
    "metric_warning_fg": "#a97118",
    "metric_idle_fg": "#4e5a62",
}


def build_beam_monitor_theme(palette):
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

QFrame#plotCard, QFrame#controlCard {{
    background-color: {panel_bg};
    border: 1px solid {panel_border};
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

QPushButton[compact="true"] {{
    padding: 5px 10px;
    min-height: 28px;
    font-size: 11px;
}}

QLineEdit, QComboBox {{
    background-color: {input_bg};
    border: 1px solid {input_border};
    border-radius: 10px;
    color: {input_fg};
    padding: 7px 10px;
    min-height: 18px;
    selection-background-color: {metric_active_fg};
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

QTextEdit {{
    background-color: {textedit_bg};
    border: 1px solid {textedit_border};
    border-radius: 12px;
    color: {textedit_fg};
    padding: 10px;
    font-size: 12px;
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


class BeamStatusStrip(QWidget):
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
        container.setMinimumWidth(120)

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


class myWindow(QWidget, Ui_Form):
    """
    Beam monitor with VM-safe shell and offline-friendly status handling.
    """

    def __init__(self):
        super().__init__()
        self.setupUi(self)
        self.app_context = load_app_context("beam_monitor")
        self.machine_profile = self.app_context.profile
        self.control_backend = self.app_context.control_backend.name
        self.beam_monitor_config = get_workflow(self.machine_profile, "beam_monitor")
        self.flag_elements = list_elements(self.app_context, kind="flag", logical_channel="image")
        self.flag_ids = [element.id for element in self.flag_elements]

        self.current_theme = "dark"
        self.is_timer_running = True
        self._pv_available = False
        self._pv_error = None
        self._profile_warning = None
        self._write_block_notice = None
        self.tmppv = self.flag_ids[0] if self.flag_ids else ""
        self._pixel_geometry_flag_id = None

        self.h = None
        self.colorbar = None
        self.sigx = None
        self.sigy = None

        self._configure_pixel_geometry(self.tmppv)

        self._configure_window()
        self._build_shell()
        self._configure_default_state()
        self._connect_signals()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.plot_beamprofile)
        self.timer.start(1000)

        self._draw_placeholder_plot("Beam Profile")
        self._refresh_status()
        self.plot_beamprofile()

    def _configure_window(self):
        self.setWindowTitle(f"{self.machine_profile.machine.display_name} Beam Monitor")
        self.resize(1120, 920)
        self.setMinimumSize(900, 760)
        self._apply_theme()

    def _build_shell(self):
        self.verticalLayout_3.setContentsMargins(10, 10, 10, 10)
        self.verticalLayout_3.setSpacing(12)
        self.verticalLayout_2.setContentsMargins(0, 0, 0, 0)
        self.verticalLayout_2.setSpacing(12)
        self.verticalLayout_2.setStretch(0, 5)
        self.verticalLayout_2.setStretch(1, 0)
        self.widget_2.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        self._build_summary_panel()
        self._build_plot_card()
        self._build_control_workspace()

    def _build_summary_panel(self):
        panel = QFrame(self)
        panel.setObjectName("summaryPanel")
        panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        outer_layout = QVBoxLayout(panel)
        outer_layout.setContentsMargins(14, 12, 14, 12)
        outer_layout.setSpacing(8)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(12)

        title = QLabel(f"{self.machine_profile.machine.display_name} Beam Monitor", panel)
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

        self.theme_toggle_button = QToolButton(panel)
        self.theme_toggle_button.setObjectName("themeToggleButton")
        self.theme_toggle_button.setFixedSize(HEADER_ACTION_HEIGHT, HEADER_ACTION_HEIGHT)
        self.theme_toggle_button.clicked.connect(self._toggle_theme)
        header_layout.addWidget(self.theme_toggle_button)

        outer_layout.addLayout(header_layout)

        self.status_panel = BeamStatusStrip(panel)
        self.status_panel.add_item("machine", "MACHINE", self.machine_profile.machine.id)
        self.status_panel.add_item("backend", "BACKEND", self.control_backend.upper())
        self.status_panel.add_item("flag", "FLAG", self.tmppv or "--")
        self.status_panel.add_item("acq", "ACQ", "Running")
        self.status_panel.add_item("profile", "PROFILE", "Waiting")
        self.status_panel.finish()
        self.status_panel.apply_theme(self._palette())
        self.status_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.status_panel.setFixedHeight(self.status_panel.sizeHint().height())
        self._update_theme_toggle_button()

        outer_layout.addWidget(self.status_panel)
        self.verticalLayout_3.insertWidget(0, panel)

    def _build_plot_card(self):
        self.verticalLayout_2.removeWidget(self.widget)

        card = QFrame(self)
        card.setObjectName("plotCard")
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        title = QLabel("Beam Profile", card)
        title.setObjectName("panelTitle")
        layout.addWidget(title)
        layout.addWidget(self.widget)

        self.verticalLayout_2.insertWidget(0, card, 2)
        self.plot_card = card

    def _build_control_workspace(self):
        self.widget_2.setObjectName("workspacePanel")
        self.control_grid = QGridLayout(self.widget_2)
        self.control_grid.setContentsMargins(0, 0, 0, 0)
        self.control_grid.setHorizontalSpacing(12)
        self.control_grid.setVerticalSpacing(10)

        self.acquisition_card = self._build_control_card("Acquisition")
        self.view_card = self._build_control_card("View Controls")
        self.profile_card = self._build_control_card("Profile Stats")

        self._populate_acquisition_card()
        self._populate_view_card()
        self._populate_profile_card()
        self._update_control_workspace_layout()

    def _build_control_card(self, title_text):
        card = QFrame(self.widget_2)
        card.setObjectName("controlCard")
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        title = QLabel(title_text, card)
        title.setObjectName("panelTitle")
        layout.addWidget(title)

        return card

    def _populate_acquisition_card(self):
        layout = self.acquisition_card.layout()

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        self.label_10.setText("Flag")
        self.label.setText("Exposure (s)")
        self.label_9.setText("Refresh (s)")
        self.label_2.setText("Colormap")
        for label in (self.label_10, self.label, self.label_9, self.label_2):
            label.setProperty("role", "field")

        grid.addWidget(self.label_10, 0, 0)
        grid.addWidget(self.flag_selec, 0, 1)
        grid.addWidget(self.label, 1, 0)
        grid.addWidget(self.lineEdit, 1, 1)
        grid.addWidget(self.label_9, 2, 0)
        grid.addWidget(self.lineEdit_9, 2, 1)
        grid.addWidget(self.label_2, 3, 0)
        grid.addWidget(self.comboBox_2, 3, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        self.flag_selec.clear()
        self.flag_selec.addItems(self.flag_ids)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(10)

        self.pushButton.setText("Run Monitor")
        self.pushButton_2.setText("Pause Monitor")
        for button in (self.pushButton, self.pushButton_2):
            button.setProperty("compact", True)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self._refresh_widget_style(button)
        action_row.addWidget(self.pushButton)
        action_row.addWidget(self.pushButton_2)
        layout.addLayout(action_row)

    def _populate_view_card(self):
        layout = self.view_card.layout()

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        self.label_3.setText("vmin")
        self.label_4.setText("vmax")
        for label in (self.label_3, self.label_4):
            label.setProperty("role", "field")

        move_x_label = QLabel("Move X", self.view_card)
        move_x_label.setProperty("role", "field")
        move_y_label = QLabel("Move Y", self.view_card)
        move_y_label.setProperty("role", "field")

        grid.addWidget(self.label_3, 0, 0)
        grid.addWidget(self.lineEdit_4, 0, 1)
        grid.addWidget(self.label_4, 1, 0)
        grid.addWidget(self.lineEdit_3, 1, 1)
        grid.addWidget(move_x_label, 2, 0)
        grid.addWidget(self.lineEdit_7, 2, 1)
        grid.addWidget(move_y_label, 3, 0)
        grid.addWidget(self.lineEdit_8, 3, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        self.pushButton_3.setText("Apply Offset")
        self.pushButton_3.setProperty("compact", True)
        self.pushButton_3.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._refresh_widget_style(self.pushButton_3)
        layout.addWidget(self.pushButton_3)

    def _populate_profile_card(self):
        layout = self.profile_card.layout()

        self.label_5.hide()
        self.label_8.hide()
        self.textEdit.hide()
        self.label_6.setText("Sigma X (mm)")
        self.label_7.setText("Sigma Y (mm)")
        for label in (self.label_6, self.label_7):
            label.setProperty("role", "field")
            label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)

        self.lineEdit_5.setReadOnly(True)
        self.lineEdit_6.setReadOnly(True)
        self.lineEdit_5.setMinimumWidth(120)
        self.lineEdit_6.setMinimumWidth(120)
        self.lineEdit_5.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.lineEdit_6.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        sigma_grid = QGridLayout()
        sigma_grid.setHorizontalSpacing(12)
        sigma_grid.setVerticalSpacing(0)
        sigma_grid.addWidget(self.label_6, 0, 0)
        sigma_grid.addWidget(self.lineEdit_5, 0, 1)
        sigma_grid.addWidget(self.label_7, 0, 2)
        sigma_grid.addWidget(self.lineEdit_6, 0, 3)
        sigma_grid.setColumnStretch(0, 0)
        sigma_grid.setColumnStretch(1, 1)
        sigma_grid.setColumnStretch(2, 0)
        sigma_grid.setColumnStretch(3, 1)
        layout.addLayout(sigma_grid)

    def _configure_default_state(self):
        self.pushButton.setEnabled(False)
        self.pushButton_2.setEnabled(True)
        default_flag = self._pick_default_flag_id()
        if default_flag:
            self.flag_selec.setCurrentText(default_flag)
        elif self.flag_ids:
            self.flag_selec.setCurrentIndex(0)
        self.tmppv = self.flag_selec.currentText()
        self._configure_pixel_geometry(self.tmppv)
        self.lineEdit_7.setText("0")
        self.lineEdit_8.setText("0")
        self.lineEdit_4.setText("0")
        self.lineEdit_9.setText("1")
        self.lineEdit_5.setText("--")
        self.lineEdit_6.setText("--")

    def _pick_default_flag_id(self):
        default_flag = str(self.beam_monitor_config.get("default_flag", "")).strip()
        if default_flag and default_flag in self.flag_ids:
            return default_flag
        return self.flag_ids[0] if self.flag_ids else ""

    def _resolve_optional_channel(self, element_id, logical_channel):
        try:
            return resolve_channel(self.app_context, element_id, logical_channel)
        except MachineProfileError:
            return None

    def _connect_signals(self):
        self.pushButton.clicked.connect(self.start1_btn)
        self.pushButton_2.clicked.connect(self.stop1_btn)
        self.pushButton_3.clicked.connect(self.moveaxis)
        self.lineEdit.returnPressed.connect(self.setExpoTime)
        self.lineEdit_9.textChanged.connect(self.change_interval)
        self.flag_selec.currentTextChanged.connect(self._handle_flag_changed)

    def _handle_flag_changed(self, flag_id):
        self.tmppv = flag_id
        self._configure_pixel_geometry(flag_id)
        self._draw_placeholder_plot("Beam Profile")
        self._refresh_status()

    def _apply_theme(self):
        palette = self._palette()
        self.setStyleSheet(build_beam_monitor_theme(palette))
        if hasattr(self, "status_panel"):
            self.status_panel.apply_theme(palette)
        self._update_theme_toggle_button()

    def _palette(self):
        return DARK_THEME if self.current_theme == "dark" else LIGHT_THEME

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
        if self.h is None:
            self._draw_placeholder_plot("Beam Profile")
        else:
            self.plot_beamprofile()
        self._refresh_status()

    def _refresh_status(self):
        self.status_panel.set_item("machine", self.machine_profile.machine.id, "subtle")
        self.status_panel.set_item(
            "backend",
            self._current_mode().upper(),
            "warning" if self._current_mode() == "real" else "success",
        )
        self.status_panel.set_item(
            "flag",
            self.flag_selec.currentText() or "--",
            "subtle" if self._pv_available else "warning",
        )
        if not self._pv_available:
            self.status_panel.set_item("acq", "Running shell" if self.is_timer_running else "Stopped shell", "warning")
            self.status_panel.set_item("profile", "Waiting for PV", "warning")
            return

        self.status_panel.set_item("acq", "Running" if self.is_timer_running else "Stopped", "success" if self.is_timer_running else "subtle")

        if self.sigx is not None and self.sigy is not None:
            self.status_panel.set_item("profile", f"\u03c3x {self.sigx:.3f} / \u03c3y {self.sigy:.3f}", "success")
        else:
            self.status_panel.set_item("profile", "Waiting", "subtle")

    def _update_control_workspace_layout(self):
        if not hasattr(self, "control_grid"):
            return

        while self.control_grid.count():
            self.control_grid.takeAt(0)

        width = self.widget_2.width() or self.width()
        if width < 960:
            self.control_grid.setColumnStretch(0, 1)
            self.control_grid.setColumnStretch(1, 0)
            self.control_grid.addWidget(self.acquisition_card, 0, 0, Qt.AlignTop)
            self.control_grid.addWidget(self.view_card, 1, 0, Qt.AlignTop)
            self.control_grid.addWidget(self.profile_card, 2, 0, Qt.AlignTop)
            return

        self.control_grid.setColumnStretch(0, 1)
        self.control_grid.setColumnStretch(1, 1)
        self.control_grid.addWidget(self.acquisition_card, 0, 0, Qt.AlignTop)
        self.control_grid.addWidget(self.view_card, 0, 1, Qt.AlignTop)
        self.control_grid.addWidget(self.profile_card, 1, 0, 1, 2, Qt.AlignTop)

    @staticmethod
    def _refresh_widget_style(widget):
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()

    def _notify(self, message):
        print(message)

    def _writes_allowed(self, operation):
        try:
            require_workflow_write_allowed(self.app_context, "beam_monitor", operation)
        except MachineProfileError as exc:
            message = str(exc)
            if message != self._write_block_notice:
                self._write_block_notice = message
                self._notify(message)
            return False
        return True

    def _apply_runtime_selection(self, machine_id, control_backend):
        request_runtime_restart(
            self,
            app_label="Beam Monitor",
            current_machine_id=self.machine_profile.machine.id,
            current_control_backend=self.control_backend,
            machine_id=machine_id,
            control_backend=control_backend,
        )

    def _draw_placeholder_plot(self, title):
        palette = self._palette()
        self._clear_image_plot_state()
        self._clear_profile_stats()
        self.widget.axes.clear()
        self._style_plot_axes()
        self.widget.axes.set_title(title, color=palette["plot_text"], fontsize=11, fontweight="bold", loc="left")
        self.widget.axes.set_xlabel("x (mm)")
        self.widget.axes.set_ylabel("y (mm)")
        self.widget.axes.set_xlim(self.xlim)
        self.widget.axes.set_ylim(self.ylim)
        self.widget.canvas.draw()

    def _clear_image_plot_state(self):
        if self.colorbar is not None:
            try:
                self.colorbar.remove()
            except (AttributeError, KeyError, ValueError):
                pass
            self.colorbar = None
        self.h = None

    def _clear_profile_stats(self):
        self.sigx = None
        self.sigy = None
        if hasattr(self, "lineEdit_5"):
            self.lineEdit_5.setText("--")
        if hasattr(self, "lineEdit_6"):
            self.lineEdit_6.setText("--")

    def _active_image_axes(self):
        if self.h is None:
            return None
        axes = getattr(self.h, "axes", None)
        if axes is None:
            self.h = None
        return axes

    def _show_profile_placeholder(self, title, warning=None):
        if warning and warning != self._profile_warning:
            print(warning)
            self._profile_warning = warning
        self._draw_placeholder_plot(title)
        self._refresh_status()

    def _style_plot_axes(self):
        palette = self._palette()
        self.widget.fig.patch.set_facecolor(palette["plot_card_bg"])
        self.widget.axes.set_facecolor(palette["plot_bg"])
        self.widget.axes.tick_params(colors=palette["plot_text"], which="both", labelsize=9)
        self.widget.axes.xaxis.label.set_color(palette["plot_text"])
        self.widget.axes.yaxis.label.set_color(palette["plot_text"])
        for spine in self.widget.axes.spines.values():
            spine.set_edgecolor(palette["plot_spine"])
        self.widget.axes.grid(alpha=0.8, linestyle="--", color=palette["plot_grid"])

    def _mark_pv_available(self):
        self._pv_available = True
        self._pv_error = None

    def _mark_pv_unavailable(self, exc):
        self._pv_available = False
        error_text = str(exc)
        if error_text != self._pv_error:
            self._pv_error = error_text
            self._notify("PV connection unavailable. Beam Monitor is in offline shell mode.")

    def _current_mode(self):
        return self.control_backend

    def _configure_pixel_geometry(self, flag_id):
        geometry = resolve_flag_pixel_geometry(
            self.beam_monitor_config,
            "workflows.beam_monitor",
            self.control_backend,
            flag_id,
        )
        self._pixel_geometry_flag_id = flag_id
        self.pixel = geometry.shape
        pixel_width = geometry.pixel_width_mm
        self.width = self.pixel[0] * pixel_width
        self.height = self.pixel[1] * pixel_width
        self.xlim = (-0.5 * self.width, 0.5 * self.width)
        self.ylim = (-0.5 * self.height, 0.5 * self.height)
        self.extent = self.xlim + self.ylim

    def _get_refresh_interval_ms(self):
        text = self.lineEdit_9.text().strip()
        if not text:
            return None
        try:
            interval_ms = round(float(text)) * 1000
        except ValueError:
            return None
        return interval_ms if interval_ms > 0 else None

    def moveaxis(self):
        if self.lineEdit_7.text() != "":
            offx = float(self.lineEdit_7.text())
        else:
            offx = 0

        if self.lineEdit_8.text() != "":
            offy = float(self.lineEdit_8.text())
        else:
            offy = 0

        tmp1 = tuple(np.array(self.extent)[0:2] - offx)
        tmp2 = tuple(np.array(self.extent)[2:4] - offy)
        self.extent = tmp1 + tmp2

        axes = self._active_image_axes()
        if axes is not None:
            axes.set_xlim(tmp1)
            axes.set_ylim(tmp2)
            self.widget.canvas.draw()

    def init_realOrVM(self):
        mode = self._current_mode()
        self.pv = resolve_channel(self.app_context, self.tmppv, "image")
        self.expoTimePV = self._resolve_optional_channel(self.tmppv, "exposure_time")

        if mode == "real":
            if self.expoTimePV is not None:
                try:
                    expoTime = caget(self.expoTimePV)
                    if expoTime is not None:
                        self.lineEdit.setText(str(expoTime))
                except Exception as exc:
                    self._mark_pv_unavailable(exc)
            else:
                self.lineEdit.setText("--")
        elif mode == "vm":
            self.lineEdit.setText("VM")
        else:
            print("Error, usage: python main.py [real|vm]")
            return False
        return True

    def init_sigxy_pv(self):
        sigx_pv = self._resolve_optional_channel(self.tmppv, "sigx")
        sigy_pv = self._resolve_optional_channel(self.tmppv, "sigy")
        if sigx_pv is None or sigy_pv is None:
            self.sigPV = None
            return
        self.sigPV = [sigx_pv, sigy_pv]

    def start1_btn(self):
        freq = self._get_refresh_interval_ms()
        if freq is None:
            print("Refresh rate must be a positive number.")
            return

        self.timer.start(freq)
        self.is_timer_running = True
        self.pushButton.setEnabled(False)
        self.pushButton_2.setEnabled(True)
        self._refresh_status()

    def stop1_btn(self):
        self.timer.stop()
        self.is_timer_running = False
        self.pushButton.setEnabled(True)
        self.pushButton_2.setEnabled(False)
        self._refresh_status()

    def change_interval(self):
        interval_ms = self._get_refresh_interval_ms()
        if interval_ms is None:
            return
        self.timer.stop()
        self.timer.start(interval_ms)
        self.is_timer_running = True
        self.pushButton.setEnabled(False)
        self.pushButton_2.setEnabled(True)
        self._refresh_status()

    def setExpoTime(self):
        mode = self._current_mode()
        if mode == "real" and self.expoTimePV is not None:
            if not self._writes_allowed("set beam monitor exposure time"):
                return
            try:
                expoTime = float(self.lineEdit.text())
            except ValueError:
                print("Exposure time must be numeric.")
                return
            try:
                caput(self.expoTimePV, expoTime)
                self._mark_pv_available()
            except Exception as exc:
                self._mark_pv_unavailable(exc)

        elif mode == "vm":
            self.lineEdit.setText("VM")

    def plot_beamprofile(self):
        self.tmppv = self.flag_selec.currentText()
        if self.tmppv != self._pixel_geometry_flag_id:
            self._configure_pixel_geometry(self.tmppv)
        if not self.init_realOrVM():
            return
        self.init_sigxy_pv()

        try:
            tmppv1 = PV(self.pv)
            tmp = tmppv1.get()
            self._mark_pv_available()
        except Exception as exc:
            self._mark_pv_unavailable(exc)
            self._show_profile_placeholder("Beam Profile / Offline")
            return

        if tmp is None:
            self._show_profile_placeholder(
                f"{self.tmppv} / No Data",
                warning=f"Warning: {self.pv} has no image data.",
            )
            return

        try:
            data_ini = list(map(float, tmp))
        except TypeError as exc:
            self._show_profile_placeholder(
                f"{self.tmppv} / Invalid Data",
                warning=f"Warning: beam profile data is not array-like: {exc}",
            )
            return

        expected_size = self.pixel[0] * self.pixel[1]
        if len(data_ini) != expected_size:
            if len(data_ini) == 0:
                title = f"{self.tmppv} / Not In Active Usedline"
                warning = (
                    f"Warning: {self.tmppv} has no VM image data in the active usedline. "
                    "Switch to a usedline containing this flag, or select a published flag."
                )
            else:
                title = f"{self.tmppv} / Invalid Data"
                warning = (
                    f"Warning: beam profile data length mismatch for {self.tmppv}: "
                    f"got {len(data_ini)}, expected {expected_size}."
                )
            self._show_profile_placeholder(title, warning=warning)
            return

        data = np.reshape(data_ini, (self.pixel[1], self.pixel[0]))
        self._profile_warning = None
        self._clear_profile_stats()

        axes = self._active_image_axes()
        if axes is not None:
            self.xlim = axes.get_xlim()
            self.ylim = axes.get_ylim()

        self._clear_image_plot_state()
        self.widget.axes.clear()
        self._style_plot_axes()

        if self.lineEdit_4.text() != "":
            try:
                vmin = float(self.lineEdit_4.text())
            except ValueError:
                vmin = float(np.min(data))
                self.lineEdit_4.setText(str(vmin))
        else:
            vmin = float(np.min(data))
            self.lineEdit_4.setText(str(vmin))

        if self.lineEdit_3.text() != "":
            try:
                vmax = float(self.lineEdit_3.text())
            except ValueError:
                vmax = float(np.max(data))
                self.lineEdit_3.setText(str(vmax))
        else:
            vmax = float(np.max(data))
            self.lineEdit_3.setText(str(vmax))

        vnorm = mpl.colors.Normalize(vmin=vmin, vmax=vmax)
        colormap = self.comboBox_2.currentText()

        self.h = self.widget.axes.imshow(
            data,
            cmap=colormap,
            norm=vnorm,
            origin="lower",
            extent=self.extent,
            aspect="auto",
        )
        self.colorbar = self.widget.fig.colorbar(self.h)
        self.colorbar.ax.yaxis.set_tick_params(color=self._palette()["plot_text"])
        for tick in self.colorbar.ax.get_yticklabels():
            tick.set_color(self._palette()["plot_text"])

        self.widget.axes.set_title(self.tmppv, color=self._palette()["plot_text"], fontsize=11, fontweight="bold", loc="left")
        self.widget.axes.set_xlabel("x (mm)")
        self.widget.axes.set_ylabel("y (mm)")
        self.widget.axes.set_xlim(self.xlim)
        self.widget.axes.set_ylim(self.ylim)

        height = abs(self.ylim[1] - self.ylim[0])
        width = abs(self.xlim[1] - self.xlim[0])

        fit_result = fit_beam_image(
            data,
            extent=self.extent,
            xlim=self.xlim,
            ylim=self.ylim,
        )

        if not fit_result.has_signal:
            self.widget.canvas.draw()
            self._refresh_status()
            return

        norm_denx = fit_result.x_projection.normalized_projection
        norm_deny = fit_result.y_projection.normalized_projection
        if norm_denx is not None and norm_deny is not None:
            denx = norm_denx * height * 0.3 + self.ylim[0] * 0.98
            deny = norm_deny * width * 0.3 + self.xlim[0] * 0.98
            self.widget.axes.plot(fit_result.x_axis, denx, "--c")
            self.widget.axes.plot(deny, fit_result.y_axis, "--c")

        if fit_result.valid:
            fit_denx = fit_result.x_projection.fitted_projection * height * 0.3 + self.ylim[0] * 0.98
            self.widget.axes.plot(fit_result.x_axis, fit_denx, "--r")
            self.sigx = round(fit_result.sigx_mm, 3)
            self.lineEdit_5.setText(str(self.sigx))

            fit_deny = fit_result.y_projection.fitted_projection * width * 0.3 + self.xlim[0] * 0.98
            self.widget.axes.plot(fit_deny, fit_result.y_axis, "--r", label="fitting curve")

            self.widget.axes.legend()
            self.sigy = round(fit_result.sigy_mm, 3)
            self.lineEdit_6.setText(str(self.sigy))
        elif fit_result.status == "fit_failed":
            print(f"Warning: beam profile Gaussian fitting skipped: {fit_result.message}")
            self.lineEdit_5.setText("--")
            self.lineEdit_6.setText("--")

        self.widget.canvas.draw()
        self._refresh_status()

        if self.sigx is not None and self.sigy is not None and self.sigPV is not None:
            if not self._writes_allowed("publish beam monitor fitted sigma"):
                return
            try:
                caput_many(self.sigPV, [self.sigx, self.sigy])
            except Exception as exc:
                self._mark_pv_unavailable(exc)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_control_workspace_layout()

    def closeEvent(self, event):
        self.timer.stop()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = myWindow()
    window.show()
    sys.exit(app.exec_())
