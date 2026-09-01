import sys
import time
import math
from datetime import datetime
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
from epics import PV, caget, caput
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gui import Ui_Form
from mplwidget import MplWidget
from profile_runtime import resolve_beam_monitor_background_paths
from half_linac.src.shared.beam_diagnostics import (
    BEAM_IMAGE_COLORMAPS,
    DEFAULT_BEAM_IMAGE_COLORMAP,
    BackgroundStoreError,
    analyze_beam_image,
    load_background,
    save_background,
    ROIControl,
    configured_roi,
    roi_extent,
)
from half_linac.src.shared.app_theme import resolve_initial_theme
from half_linac.src.shared.machine_profile import (
    MachineProfileError,
    RuntimeContextWidget,
    get_workflow,
    list_elements,
    load_app_context,
    resolve_app_runtime_paths,
    require_workflow_write_allowed,
    resolve_channel,
    resolve_element_image_geometry,
    resolve_write_target,
    workflow_writes_allowed,
)
from half_linac.src.shared.window_activation import install_qt_window_raise_handler


HEADER_ACTION_HEIGHT = 32
APP_DIR = Path(__file__).resolve().parent
IMAGE_PV_GET_TIMEOUT_S = 3.0

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

QWidget[role="controlSection"] {{
    background-color: transparent;
}}

QLabel#summaryTitle {{
    background-color: transparent;
    color: {summary_title_fg};
    font-size: 22px;
    font-weight: 700;
    letter-spacing: 0.3px;
}}

QLabel#panelTitle {{
    background-color: transparent;
    border: none;
    color: {summary_title_fg};
    font-size: 15px;
    font-weight: 700;
}}

QLabel[role="sectionTitle"] {{
    background-color: transparent;
    color: {summary_title_fg};
    font-size: 13px;
    font-weight: 700;
}}

QLabel[role="field"] {{
    background-color: transparent;
    color: {muted_fg};
    font-size: 11px;
    font-weight: 600;
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

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
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

QCheckBox {{
    background-color: transparent;
    color: {window_fg};
    spacing: 8px;
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
    border: 2px solid {window_fg};
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
        install_qt_window_raise_handler(self)
        self.app_context = load_app_context("beam_monitor")
        self.machine_profile = self.app_context.profile
        self.control_backend = self.app_context.control_backend.name
        self.beam_monitor_config = get_workflow(self.machine_profile, "beam_monitor")
        self.flag_elements = list_elements(
            self.app_context,
            kind="flag",
            logical_channel="image",
            control_backend=self.control_backend,
        )
        self.flag_ids = [element.id for element in self.flag_elements]

        self.current_theme = resolve_initial_theme()
        self.is_timer_running = True
        self._pv_available = False
        self._pv_error = None
        self._profile_warning = None
        self._profile_status_text = "Waiting"
        self._profile_status_tone = "subtle"
        self._write_block_notice = None
        self.tmppv = self.flag_ids[0] if self.flag_ids else ""
        self._pixel_geometry_flag_id = None
        self._image_pv = None
        self._image_pv_name = None
        self._size_pvs = (None, None)
        self._size_pv_names = (None, None)
        self.size_pv_sigx = None
        self.size_pv_sigy = None
        self.background_image = None
        self.background_metadata = {}
        self.background_image_path = None
        self.background_flag_id = None
        self.subtract_background_enabled = False
        self.background_dialog = None
        self.background_preview = None
        self.display_settings_dialog = None
        self.roi_dialog = None
        self.roi_control = None
        self.roi_status_label = None

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
        self.verticalLayout_2.setStretch(0, 7)
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

        title = QLabel("Beam Monitor", panel)
        title.setObjectName("summaryTitle")
        header_layout.addWidget(title)
        header_layout.addStretch(1)

        header_layout.addWidget(
            RuntimeContextWidget(
                machine_id=self.machine_profile.machine.id,
                machine_display_name=self.machine_profile.machine.display_name,
                control_backend=self.control_backend,
                parent=panel,
            )
        )

        self.theme_toggle_button = QToolButton(panel)
        self.theme_toggle_button.setObjectName("themeToggleButton")
        self.theme_toggle_button.setFixedSize(HEADER_ACTION_HEIGHT, HEADER_ACTION_HEIGHT)
        self.theme_toggle_button.clicked.connect(self._toggle_theme)
        header_layout.addWidget(self.theme_toggle_button)

        outer_layout.addLayout(header_layout)

        self.status_panel = BeamStatusStrip(panel)
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
        self.widget.setMinimumHeight(380)
        layout.addWidget(self.widget)

        self.verticalLayout_2.insertWidget(0, card, 2)
        self.plot_card = card

    def _build_control_workspace(self):
        self.widget_2.setObjectName("workspacePanel")
        self.control_grid = QGridLayout(self.widget_2)
        self.control_grid.setContentsMargins(0, 0, 0, 0)
        self.control_grid.setSpacing(0)

        self.controls_card = QFrame(self.widget_2)
        self.controls_card.setObjectName("controlCard")
        self.controls_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        controls_layout = QGridLayout(self.controls_card)
        controls_layout.setContentsMargins(12, 10, 12, 10)
        controls_layout.setHorizontalSpacing(18)
        controls_layout.setVerticalSpacing(10)
        self.compact_control_grid = controls_layout
        self.control_grid.addWidget(self.controls_card, 0, 0)

        self.acquisition_card = self._build_control_section("Acquisition")
        self.profile_card = self._build_control_section("Analysis")
        self.view_card = self._build_control_section("Display / Background")

        self._populate_acquisition_card()
        self._populate_profile_card()
        self._populate_view_card()
        self._update_control_workspace_layout()

    def _build_control_section(self, title_text):
        card = QWidget(self.controls_card)
        card.setProperty("role", "controlSection")
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        title = QLabel(title_text, card)
        title.setProperty("role", "sectionTitle")
        layout.addWidget(title)

        return card

    def _populate_acquisition_card(self):
        layout = self.acquisition_card.layout()

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)

        self.label_10.setText("Flag")
        self.label.setText("Exposure (s)")
        self.label_9.setText("Refresh (s)")
        for label in (self.label_10, self.label, self.label_9):
            label.setProperty("role", "field")

        grid.addWidget(self.label_10, 0, 0)
        grid.addWidget(self.flag_selec, 0, 1)
        grid.addWidget(self.label, 1, 0)
        grid.addWidget(self.lineEdit, 1, 1)
        grid.addWidget(self.label_9, 2, 0)
        grid.addWidget(self.lineEdit_9, 2, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        self.flag_selec.clear()
        self.flag_selec.addItems(self.flag_ids)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(6)

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

        self.pushButton_3.hide()
        self.lineEdit_7.hide()
        self.lineEdit_8.hide()

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        self.label_2.setText("Colormap")
        self.comboBox_2.clear()
        self.comboBox_2.addItems(BEAM_IMAGE_COLORMAPS)
        self.comboBox_2.setCurrentText(DEFAULT_BEAM_IMAGE_COLORMAP)
        self.label_2.setProperty("role", "field")
        for widget in (self.label_3, self.label_4, self.lineEdit_3, self.lineEdit_4):
            widget.hide()

        grid.addWidget(self.label_2, 0, 0)
        grid.addWidget(self.comboBox_2, 0, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        self.display_settings_button = QPushButton("Display…", self.view_card)
        self.display_settings_button.setProperty("compact", True)
        self.display_settings_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.display_settings_button.setToolTip(
            "Set optional fixed image intensity limits; blank fields use automatic scaling."
        )
        self._refresh_widget_style(self.display_settings_button)
        self.reset_view_button = QPushButton("Fit to Image", self.view_card)
        self.reset_view_button.setProperty("compact", True)
        self.reset_view_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.reset_view_button.setToolTip(
            "Restore the full physical image extent for the selected flag."
        )
        self._refresh_widget_style(self.reset_view_button)
        display_actions = QHBoxLayout()
        display_actions.setContentsMargins(0, 0, 0, 0)
        display_actions.setSpacing(6)
        display_actions.addWidget(self.display_settings_button)
        display_actions.addWidget(self.reset_view_button)
        layout.addLayout(display_actions)

        self.background_subtract_checkbox.setParent(self.view_card)
        self.background_status_label.setParent(self.view_card)
        self.background_button.setParent(self.view_card)
        background_row = QHBoxLayout()
        background_row.setContentsMargins(0, 0, 0, 0)
        background_row.setSpacing(6)
        background_row.addWidget(self.background_subtract_checkbox)
        background_row.addWidget(self.background_status_label, 1)
        background_row.addWidget(self.background_button)
        layout.addLayout(background_row)

    def _populate_profile_card(self):
        layout = self.profile_card.layout()

        self.label_5.hide()
        self.label_8.hide()
        self.textEdit.hide()

        method_label = QLabel("Profile method", self.profile_card)
        method_label.setProperty("role", "field")
        self.profile_method_combo = QComboBox(self.profile_card)
        self.profile_method_combo.setObjectName("profileMethodComboBox")
        self.profile_method_combo.addItems(("Gaussian fit", "RMS moments"))
        configured_method = str(
            self.beam_monitor_config.get("profile_method", "Gaussian fit")
        ).strip()
        method_index = self.profile_method_combo.findText(configured_method)
        if method_index < 0:
            raise MachineProfileError(
                f"Unsupported beam monitor profile_method: {configured_method!r}."
            )
        self.profile_method_combo.setCurrentIndex(method_index)
        self.profile_method_combo.setToolTip(
            "Gaussian fit reports the fitted core width. RMS moments reports the "
            "intensity-weighted second moment and is more sensitive to background."
        )

        self.background_subtract_checkbox = QCheckBox(
            "Apply",
            self.profile_card,
        )
        self.background_subtract_checkbox.setObjectName("subtractBackgroundCheckBox")
        self.background_subtract_checkbox.setToolTip(
            "Subtract the current flag's saved background before display and profile analysis."
        )

        self.background_status_label = QLabel("BG: None", self.profile_card)
        self.background_status_label.setProperty("role", "field")
        self.background_status_label.setWordWrap(False)

        self.background_button = QPushButton("Manage…", self.profile_card)
        self.background_button.setObjectName("backgroundButton")
        self.background_button.setProperty("compact", True)
        self.background_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._refresh_widget_style(self.background_button)

        self.roi_status_label = QLabel("ROI: Off", self.profile_card)
        self.roi_status_label.setProperty("role", "field")
        self.roi_button = QPushButton("ROI…", self.profile_card)
        self.roi_button.setProperty("compact", True)
        self.roi_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._refresh_widget_style(self.roi_button)
        self.roi_button.hide()

        analysis_grid = QGridLayout()
        analysis_grid.setHorizontalSpacing(8)
        analysis_grid.setVerticalSpacing(6)
        analysis_grid.addWidget(method_label, 0, 0)
        analysis_grid.addWidget(self.profile_method_combo, 0, 1)
        analysis_grid.setColumnStretch(1, 1)
        layout.addLayout(analysis_grid)
        roi_row = QHBoxLayout()
        roi_row.setContentsMargins(0, 0, 0, 0)
        roi_row.addWidget(self.roi_status_label, 1)
        roi_row.addWidget(self.roi_button)
        layout.addLayout(roi_row)

        self.label_6.hide()
        self.label_7.hide()

        self.lineEdit_5.setReadOnly(True)
        self.lineEdit_6.setReadOnly(True)
        self.lineEdit_5.setMinimumWidth(72)
        self.lineEdit_6.setMinimumWidth(72)
        self.lineEdit_5.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.lineEdit_6.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        sigma_grid = QGridLayout()
        sigma_grid.setHorizontalSpacing(6)
        sigma_grid.setVerticalSpacing(4)

        for column, text in enumerate(("Source", "σx (mm)", "σy (mm)")):
            header = QLabel(text, self.profile_card)
            header.setProperty("role", "field")
            sigma_grid.addWidget(header, 0, column)

        local_fit_title = QLabel("Local fit", self.profile_card)
        local_fit_title.setProperty("role", "field")
        published_size_tooltip = (
            "Optional cross-check values read from the configured sigx/sigy channels. "
            "Their calculation and background treatment are determined by the data provider."
        )
        size_pv_title = QLabel("Published size", self.profile_card)
        size_pv_title.setProperty("role", "field")
        size_pv_title.setToolTip(published_size_tooltip)

        self.size_pv_sigx_label = QLineEdit("--", self.profile_card)
        self.size_pv_sigy_label = QLineEdit("--", self.profile_card)
        for field in (self.size_pv_sigx_label, self.size_pv_sigy_label):
            field.setReadOnly(True)
            field.setMinimumWidth(72)
            field.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            field.setToolTip(published_size_tooltip)

        sigma_grid.addWidget(local_fit_title, 1, 0)
        sigma_grid.addWidget(self.lineEdit_5, 1, 1)
        sigma_grid.addWidget(self.lineEdit_6, 1, 2)
        sigma_grid.addWidget(size_pv_title, 2, 0)
        sigma_grid.addWidget(self.size_pv_sigx_label, 2, 1)
        sigma_grid.addWidget(self.size_pv_sigy_label, 2, 2)
        sigma_grid.setColumnStretch(0, 0)
        sigma_grid.setColumnStretch(1, 1)
        sigma_grid.setColumnStretch(2, 1)
        layout.addLayout(sigma_grid)

    def _show_display_settings_dialog(self):
        if self.display_settings_dialog is None:
            dialog = QDialog(self)
            dialog.setWindowTitle("Image Display")
            dialog.setMinimumWidth(360)
            layout = QVBoxLayout(dialog)

            note = QLabel(
                "Leave either limit blank to use the current image minimum or maximum.",
                dialog,
            )
            note.setWordWrap(True)
            note.setProperty("role", "field")
            layout.addWidget(note)

            form = QGridLayout()
            self.label_3.setParent(dialog)
            self.label_4.setParent(dialog)
            self.lineEdit_4.setParent(dialog)
            self.lineEdit_3.setParent(dialog)
            self.label_3.setText("vmin")
            self.label_4.setText("vmax")
            self.label_3.setProperty("role", "field")
            self.label_4.setProperty("role", "field")
            self.lineEdit_4.setPlaceholderText("auto")
            self.lineEdit_3.setPlaceholderText("auto")
            for widget in (self.label_3, self.label_4, self.lineEdit_4, self.lineEdit_3):
                widget.show()
            form.addWidget(self.label_3, 0, 0)
            form.addWidget(self.lineEdit_4, 0, 1)
            form.addWidget(self.label_4, 1, 0)
            form.addWidget(self.lineEdit_3, 1, 1)
            form.setColumnStretch(1, 1)
            layout.addLayout(form)

            actions = QHBoxLayout()
            reset_button = QPushButton("Reset Auto", dialog)
            apply_button = QPushButton("Apply", dialog)
            close_button = QPushButton("Close", dialog)
            for button in (reset_button, apply_button, close_button):
                button.setProperty("compact", True)
            reset_button.clicked.connect(self._reset_intensity_limits)
            apply_button.clicked.connect(self.plot_beamprofile)
            close_button.clicked.connect(dialog.hide)
            self.lineEdit_4.returnPressed.connect(self.plot_beamprofile)
            self.lineEdit_3.returnPressed.connect(self.plot_beamprofile)
            actions.addWidget(reset_button)
            actions.addStretch(1)
            actions.addWidget(apply_button)
            actions.addWidget(close_button)
            layout.addLayout(actions)
            self.display_settings_dialog = dialog

        self.display_settings_dialog.show()
        self.display_settings_dialog.raise_()
        self.display_settings_dialog.activateWindow()

    def _reset_intensity_limits(self):
        self.lineEdit_4.clear()
        self.lineEdit_3.clear()
        self.plot_beamprofile()

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
        self.lineEdit_4.clear()
        self.lineEdit_3.clear()
        self.lineEdit_9.setText("1")
        self.lineEdit_5.setText("--")
        self.lineEdit_6.setText("--")
        self._update_exposure_edit_hint()
        self._read_exposure_time()
        self._load_latest_background(silent=True)
        self._reconfigure_roi(self.tmppv)

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

    def _resolve_optional_write_target(self, element_id, logical_channel, unit):
        try:
            return resolve_write_target(
                self.app_context,
                element_id,
                logical_channel=logical_channel,
                unit=unit,
            )
        except MachineProfileError:
            return None

    def _connect_signals(self):
        self.pushButton.clicked.connect(self.start1_btn)
        self.pushButton_2.clicked.connect(self.stop1_btn)
        self.reset_view_button.clicked.connect(self.reset_view)
        self.display_settings_button.clicked.connect(self._show_display_settings_dialog)
        self.background_button.clicked.connect(self._show_background_dialog)
        self.background_subtract_checkbox.toggled.connect(
            self._set_background_subtraction
        )
        self.profile_method_combo.currentTextChanged.connect(
            self._handle_profile_method_changed
        )
        self.lineEdit.returnPressed.connect(self.setExpoTime)
        self.lineEdit_9.textChanged.connect(self.change_interval)
        self.flag_selec.currentTextChanged.connect(self._handle_flag_changed)

    def _handle_flag_changed(self, flag_id):
        self.tmppv = flag_id
        self._configure_pixel_geometry(flag_id)
        self._clear_background()
        self._update_exposure_edit_hint()
        self._read_exposure_time()
        self._load_latest_background(silent=True)
        self._reconfigure_roi(flag_id)
        if self.background_dialog is not None:
            self.background_dialog.setWindowTitle(f"Background Reference — {flag_id}")
        self._draw_placeholder_plot("Beam Profile")
        self._refresh_status()

    def _roi_runtime_path(self, flag_id=None):
        paths = resolve_app_runtime_paths(APP_DIR, self.app_context)
        return paths["latest_dir"] / "roi" / f"{flag_id or self.tmppv}.json"

    def _reconfigure_roi(self, flag_id=None):
        geometry = resolve_element_image_geometry(
            self.app_context,
            flag_id or self.tmppv,
            self.control_backend,
        )
        configured = configured_roi(
            self.beam_monitor_config.get("roi"),
            self.control_backend,
            flag_id or self.tmppv,
        )
        if self.roi_control is None:
            self.roi_control = ROIControl(
                image_shape=(geometry.shape[1], geometry.shape[0]),
                runtime_path=self._roi_runtime_path(flag_id),
                configured=configured,
            )
            self.roi_control.warningRaised.connect(
                lambda message: self._set_profile_status(message, "warning")
            )
            self.roi_control.roiChanged.connect(self._update_roi_status)
            self.profile_card.layout().addWidget(self.roi_control)
        else:
            self.roi_control.reconfigure(
                image_shape=(geometry.shape[1], geometry.shape[0]),
                runtime_path=self._roi_runtime_path(flag_id),
                configured=configured,
            )
        self._update_roi_status()

    def _update_roi_status(self, *_args):
        if self.roi_status_label is None or self.roi_control is None:
            return
        roi = self.roi_control.roi()
        text = f"ROI: {roi.width} × {roi.height} px" if self.roi_control.use_roi.isChecked() else "ROI: Off"
        self.roi_status_label.setText(text)

    def _show_roi_dialog(self):
        if self.roi_control is None:
            self._reconfigure_roi(self.tmppv)
        if self.roi_dialog is None:
            self.roi_dialog = QDialog(self)
            self.roi_dialog.setWindowTitle("Software ROI")
            self.roi_dialog.setMinimumWidth(340)
            layout = QVBoxLayout(self.roi_dialog)
            note = QLabel("Configure the image region used by the monitor. Changes are ready for the next ROI processing step.", self.roi_dialog)
            note.setWordWrap(True)
            note.setProperty("role", "field")
            layout.addWidget(note)
            layout.addWidget(self.roi_control)
            close_button = QPushButton("Close", self.roi_dialog)
            close_button.setProperty("compact", True)
            close_button.clicked.connect(self.roi_dialog.hide)
            layout.addWidget(close_button)
        self.roi_dialog.show()
        self.roi_dialog.raise_()
        self.roi_dialog.activateWindow()

    def _handle_profile_method_changed(self, _method):
        self._set_profile_status(self.profile_method_combo.currentText(), "subtle")
        if self._pv_available:
            self.plot_beamprofile()

    def _background_paths(self, flag_id=None):
        return resolve_beam_monitor_background_paths(
            self.app_context,
            flag_id or self.tmppv,
        )

    def _current_exposure_value(self):
        try:
            value = float(self.lineEdit.text().strip())
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None

    def _background_metadata(self, *, sample_count=None, sample_interval_s=None, source):
        metadata = {
            "machine_id": self.machine_profile.machine.id,
            "control_backend": self.control_backend,
            "flag_id": self.tmppv,
            "image_pv": getattr(self, "pv", None),
            "pixel_shape": [int(self.pixel[0]), int(self.pixel[1])],
            "exposure_s": self._current_exposure_value(),
            "source": source,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        if sample_count is not None:
            metadata["sample_count"] = int(sample_count)
        if sample_interval_s is not None:
            metadata["sample_interval_s"] = float(sample_interval_s)
        return metadata

    def _validate_background_metadata(self, metadata):
        expected = {
            "machine_id": self.machine_profile.machine.id,
            "control_backend": self.control_backend,
            "flag_id": self.tmppv,
        }
        for key, expected_value in expected.items():
            actual = metadata.get(key)
            if actual is not None and str(actual) != str(expected_value):
                raise BackgroundStoreError(
                    f"Background {key} {actual!r} does not match current {expected_value!r}."
                )

    def _background_exposure_mismatch(self):
        current = self._current_exposure_value()
        saved = self.background_metadata.get("exposure_s")
        try:
            saved_value = float(saved)
        except (TypeError, ValueError):
            return False
        return current is not None and not math.isclose(
            current,
            saved_value,
            rel_tol=1e-6,
            abs_tol=1e-9,
        )

    def _update_background_status(self):
        if self.background_image is None:
            text = "BG: None"
        else:
            sample_count = self.background_metadata.get("sample_count")
            sample_text = f" · {sample_count} frame" if sample_count == 1 else (
                f" · {sample_count} frames" if sample_count else ""
            )
            mismatch_text = " • exposure mismatch" if self._background_exposure_mismatch() else ""
            text = f"BG: {self.background_flag_id}{sample_text}{mismatch_text}"
        self.background_status_label.setText(text)
        if hasattr(self, "background_dialog_status_label"):
            self.background_dialog_status_label.setText(text)

    def _set_background_image(self, image, metadata, image_path):
        self._validate_background_metadata(metadata)
        self.background_image = np.asarray(image, dtype=float)
        self.background_metadata = dict(metadata)
        self.background_image_path = Path(image_path) if image_path is not None else None
        self.background_flag_id = self.tmppv
        self._update_background_status()
        self._refresh_background_preview()
        if self._background_exposure_mismatch():
            blocked = self.background_subtract_checkbox.blockSignals(True)
            self.background_subtract_checkbox.setChecked(False)
            self.background_subtract_checkbox.blockSignals(blocked)
            self.subtract_background_enabled = False
            self._notify(
                "Loaded background exposure differs from the current camera exposure; "
                "background subtraction remains disabled."
            )

    def _clear_background(self):
        self.background_image = None
        self.background_metadata = {}
        self.background_image_path = None
        self.background_flag_id = None
        self.subtract_background_enabled = False
        if hasattr(self, "background_subtract_checkbox"):
            blocked = self.background_subtract_checkbox.blockSignals(True)
            self.background_subtract_checkbox.setChecked(False)
            self.background_subtract_checkbox.blockSignals(blocked)
        if hasattr(self, "background_status_label"):
            self._update_background_status()
        self._refresh_background_preview()

    def _load_latest_background(self, *, silent=False):
        if not self.tmppv:
            return False
        paths = self._background_paths()
        image_path = paths["background_image_path"]
        if not image_path.is_file():
            if not silent:
                self._notify(f"No saved background is available for {self.tmppv}.")
            return False
        try:
            image, metadata = load_background(
                image_path,
                paths["background_metadata_path"],
                expected_shape=(self.pixel[1], self.pixel[0]),
            )
            self._set_background_image(image, metadata, image_path)
        except (BackgroundStoreError, OSError, ValueError) as exc:
            if not silent:
                self._notify(f"Could not load background: {exc}")
            else:
                print(f"Could not auto-load background for {self.tmppv}: {exc}")
            return False
        print(f"Background loaded for {self.tmppv} from {image_path}")
        return True

    def _set_background_subtraction(self, checked):
        if checked and (
            self.background_image is None or self._background_exposure_mismatch()
        ):
            blocked = self.background_subtract_checkbox.blockSignals(True)
            self.background_subtract_checkbox.setChecked(False)
            self.background_subtract_checkbox.blockSignals(blocked)
            self.subtract_background_enabled = False
            self._notify(
                "Load or sample a matching background before enabling subtraction."
            )
            return
        self.subtract_background_enabled = bool(checked)
        if self._pv_available:
            self.plot_beamprofile()

    def _build_background_dialog(self):
        dialog = QDialog(self)
        dialog.setObjectName("backgroundDialog")
        dialog.setWindowTitle("Background Reference")
        dialog.resize(720, 620)

        outer = QVBoxLayout(dialog)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(10)

        warning = QLabel(
            "Sample only with beam absent and with the same camera exposure used for measurement.",
            dialog,
        )
        warning.setWordWrap(True)
        warning.setProperty("role", "field")
        outer.addWidget(warning)

        self.background_preview = MplWidget(dialog)
        self.background_preview.setMinimumHeight(300)
        outer.addWidget(self.background_preview, 1)

        controls = QGridLayout()
        controls.setHorizontalSpacing(10)
        controls.setVerticalSpacing(8)
        samples_label = QLabel("Samples", dialog)
        samples_label.setProperty("role", "field")
        interval_label = QLabel("Interval", dialog)
        interval_label.setProperty("role", "field")
        self.background_samples_spin = QSpinBox(dialog)
        self.background_samples_spin.setRange(1, 100)
        self.background_samples_spin.setValue(
            int(self.beam_monitor_config.get("background_sample_count", 5))
        )
        self.background_interval_spin = QDoubleSpinBox(dialog)
        self.background_interval_spin.setDecimals(2)
        self.background_interval_spin.setSingleStep(0.05)
        self.background_interval_spin.setRange(0.0, 60.0)
        self.background_interval_spin.setSuffix(" s")
        self.background_interval_spin.setValue(
            float(self.beam_monitor_config.get("background_sample_interval_s", 1.0))
        )
        controls.addWidget(samples_label, 0, 0)
        controls.addWidget(self.background_samples_spin, 0, 1)
        controls.addWidget(interval_label, 0, 2)
        controls.addWidget(self.background_interval_spin, 0, 3)
        controls.setColumnStretch(1, 1)
        controls.setColumnStretch(3, 1)
        outer.addLayout(controls)

        self.background_dialog_status_label = QLabel(dialog)
        self.background_dialog_status_label.setProperty("role", "field")
        self.background_dialog_status_label.setWordWrap(True)
        outer.addWidget(self.background_dialog_status_label)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        sample_button = QPushButton("Sample Background", dialog)
        load_latest_button = QPushButton("Load Latest", dialog)
        load_file_button = QPushButton("Load File", dialog)
        save_as_button = QPushButton("Save As", dialog)
        close_button = QPushButton("Close", dialog)
        for button in (
            sample_button,
            load_latest_button,
            load_file_button,
            save_as_button,
            close_button,
        ):
            button.setProperty("compact", True)
        sample_button.clicked.connect(self._sample_background)
        load_latest_button.clicked.connect(
            lambda: self._load_latest_background(silent=False)
        )
        load_file_button.clicked.connect(self._load_background_file)
        save_as_button.clicked.connect(self._save_background_as)
        close_button.clicked.connect(dialog.hide)
        action_row.addWidget(sample_button)
        action_row.addWidget(load_latest_button)
        action_row.addWidget(load_file_button)
        action_row.addWidget(save_as_button)
        action_row.addStretch(1)
        action_row.addWidget(close_button)
        outer.addLayout(action_row)

        self.background_dialog = dialog
        self._update_background_status()
        self._refresh_background_preview()

    def _show_background_dialog(self):
        if self.background_dialog is None:
            self._build_background_dialog()
        self.background_dialog.setWindowTitle(f"Background Reference — {self.tmppv}")
        self._update_background_status()
        self._refresh_background_preview()
        self.background_dialog.show()
        self.background_dialog.raise_()
        self.background_dialog.activateWindow()

    def _refresh_background_preview(self):
        if self.background_preview is None:
            return
        palette = self._palette()
        axes = self.background_preview.axes
        axes.clear()
        self.background_preview.fig.patch.set_facecolor(palette["plot_card_bg"])
        axes.set_facecolor(palette["plot_bg"])
        axes.tick_params(colors=palette["plot_text"], which="both", labelsize=8)
        for spine in axes.spines.values():
            spine.set_edgecolor(palette["plot_spine"])
        if self.background_image is None:
            axes.text(
                0.5,
                0.5,
                "No background loaded",
                ha="center",
                va="center",
                color=palette["muted_fg"],
                transform=axes.transAxes,
            )
            axes.set_xticks([])
            axes.set_yticks([])
        else:
            axes.imshow(
                self.background_image,
                cmap=self.comboBox_2.currentText(),
                origin="lower",
                aspect="auto",
            )
            axes.set_title(
                f"{self.background_flag_id} background",
                color=palette["plot_text"],
                fontsize=10,
                loc="left",
            )
            axes.set_xlabel("x pixel", color=palette["plot_text"])
            axes.set_ylabel("y pixel", color=palette["plot_text"])
        self.background_preview.canvas.draw_idle()

    def _sample_background(self):
        if not self.tmppv or not self._configure_active_channels():
            return
        if QMessageBox.question(
            self,
            "Sample Beam Background",
            "Confirm that the beam is absent. Sample and replace the saved background now?",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        ) != QMessageBox.Yes:
            return
        sample_count = self.background_samples_spin.value()
        interval_s = self.background_interval_spin.value()
        expected_shape = (self.pixel[1], self.pixel[0])
        timer_was_active = self.timer.isActive()
        timer_interval = self.timer.interval()
        self.timer.stop()
        self._set_profile_status("Sampling background", "subtle")
        self._refresh_status()
        images = []
        try:
            for index in range(sample_count):
                if index > 0 and interval_s > 0:
                    time.sleep(interval_s)
                raw = self._image_pv.get(
                    timeout=IMAGE_PV_GET_TIMEOUT_S,
                    use_monitor=False,
                )
                if raw is None:
                    raise BackgroundStoreError(
                        f"{self.pv} returned no image data during background sampling."
                    )
                image = np.asarray(raw, dtype=float).reshape(expected_shape)
                if not np.all(np.isfinite(image)):
                    raise BackgroundStoreError("Sampled background contains non-finite values.")
                images.append(image)
        except (BackgroundStoreError, TypeError, ValueError) as exc:
            self._notify(f"Background sampling failed: {exc}")
            self._set_profile_status("Background failed", "warning")
            self._refresh_status()
            return
        finally:
            if timer_was_active:
                self.timer.start(max(timer_interval, 1))

        background = np.mean(images, axis=0)
        metadata = self._background_metadata(
            sample_count=sample_count,
            sample_interval_s=interval_s,
            source="sampled",
        )
        paths = self._background_paths()
        try:
            image_path, _metadata_path = save_background(
                background,
                paths["background_image_path"],
                paths["background_metadata_path"],
                metadata,
            )
            self._set_background_image(background, metadata, image_path)
        except (BackgroundStoreError, OSError, ValueError) as exc:
            self._notify(f"Could not save sampled background: {exc}")
            self._set_profile_status("Background save failed", "warning")
            return
        self._set_profile_status("Background sampled", "success")
        self._refresh_status()
        print(f"Background sampled for {self.tmppv} and saved to {image_path}")

    def _choose_background_file(self, *, save):
        paths = self._background_paths()
        dialog = QFileDialog(self.background_dialog or self)
        dialog.setOption(QFileDialog.DontUseNativeDialog, True)
        dialog.setNameFilter("NumPy files (*.npy)")
        if save:
            paths["runs_dir"].mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
            dialog.setWindowTitle("Save Beam Monitor Background")
            dialog.setAcceptMode(QFileDialog.AcceptSave)
            dialog.setDefaultSuffix("npy")
            dialog.setDirectory(str(paths["runs_dir"]))
            dialog.selectFile(f"{self.tmppv}_background_{timestamp}.npy")
        else:
            dialog.setWindowTitle("Load Beam Monitor Background")
            dialog.setFileMode(QFileDialog.ExistingFile)
            initial = paths["background_image_path"]
            dialog.setDirectory(str(initial.parent if initial.parent.is_dir() else paths["latest_dir"]))
        if dialog.exec_() != QDialog.Accepted:
            return None
        selected = dialog.selectedFiles()
        return Path(selected[0]) if selected else None

    def _load_background_file(self):
        image_path = self._choose_background_file(save=False)
        if image_path is None:
            return
        try:
            image, metadata = load_background(
                image_path,
                image_path.with_suffix(".json"),
                expected_shape=(self.pixel[1], self.pixel[0]),
            )
            self._set_background_image(image, metadata, image_path)
        except (BackgroundStoreError, OSError, ValueError) as exc:
            self._notify(f"Could not load background: {exc}")
            return
        print(f"Background loaded for {self.tmppv} from {image_path}")

    def _save_background_as(self):
        if self.background_image is None:
            self._notify("No background is available to save.")
            return
        image_path = self._choose_background_file(save=True)
        if image_path is None:
            return
        metadata = dict(self.background_metadata)
        metadata.update(self._background_metadata(source="save_as"))
        try:
            save_background(
                self.background_image,
                image_path,
                image_path.with_suffix(".json"),
                metadata,
            )
        except (BackgroundStoreError, OSError, ValueError) as exc:
            self._notify(f"Could not save background: {exc}")
            return
        print(f"Background saved to {image_path}")

    def _apply_theme(self):
        palette = self._palette()
        self.setStyleSheet(build_beam_monitor_theme(palette))
        if hasattr(self, "status_panel"):
            self.status_panel.apply_theme(palette)
        if self.background_preview is not None:
            self._refresh_background_preview()
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
        self.status_panel.set_item(
            "flag",
            self.flag_selec.currentText() or "--",
            "subtle" if self._pv_available else "warning",
        )
        if not self._pv_available:
            self.status_panel.set_item("acq", "Running shell" if self.is_timer_running else "Stopped shell", "warning")
            profile_text = self._profile_status_text if self._profile_status_text != "Waiting" else "Waiting for PV"
            self.status_panel.set_item("profile", profile_text, "warning")
            return

        self.status_panel.set_item("acq", "Running" if self.is_timer_running else "Stopped", "success" if self.is_timer_running else "subtle")

        if self.sigx is not None and self.sigy is not None:
            self.status_panel.set_item("profile", f"\u03c3x {self.sigx:.3f} / \u03c3y {self.sigy:.3f}", "success")
        else:
            self.status_panel.set_item("profile", self._profile_status_text, self._profile_status_tone)

    def _set_profile_status(self, text, tone="subtle"):
        self._profile_status_text = text
        self._profile_status_tone = tone

    def _update_control_workspace_layout(self):
        if not hasattr(self, "compact_control_grid"):
            return

        grid = self.compact_control_grid
        while grid.count():
            grid.takeAt(0)

        width = self.widget_2.width() or self.width()
        if width < 820:
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 0)
            grid.setColumnStretch(2, 0)
            grid.addWidget(self.acquisition_card, 0, 0, Qt.AlignTop)
            grid.addWidget(self.profile_card, 1, 0, Qt.AlignTop)
            grid.addWidget(self.view_card, 2, 0, Qt.AlignTop)
            return

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        grid.addWidget(self.acquisition_card, 0, 0, Qt.AlignTop)
        grid.addWidget(self.profile_card, 0, 1, Qt.AlignTop)
        grid.addWidget(self.view_card, 0, 2, Qt.AlignTop)

    @staticmethod
    def _refresh_widget_style(widget):
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()

    def _notify(self, message):
        print(message)

    def _warn_once(self, message):
        if message and message != self._profile_warning:
            print(message)
            self._profile_warning = message

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

    def _update_exposure_edit_hint(self):
        if self._current_mode() == "vm":
            self.lineEdit.setReadOnly(True)
            self.lineEdit.setToolTip("Exposure time is not writable in VM mode.")
            return

        if self._resolve_optional_channel(self.tmppv, "exposure_time") is None:
            self.lineEdit.setReadOnly(True)
            self.lineEdit.setToolTip("The selected flag does not provide an exposure time PV.")
            return

        if not workflow_writes_allowed(self.app_context, "beam_monitor"):
            self.lineEdit.setReadOnly(True)
            self.lineEdit.setToolTip("Exposure time writes are blocked by the beam monitor workflow policy.")
            return

        self.lineEdit.setReadOnly(False)
        self.lineEdit.setToolTip("Press Enter to write exposure time to the selected flag camera.")

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
        self._set_profile_status("Waiting")
        if hasattr(self, "lineEdit_5"):
            self.lineEdit_5.setText("--")
        if hasattr(self, "lineEdit_6"):
            self.lineEdit_6.setText("--")

    def _clear_size_pv_stats(self):
        self.size_pv_sigx = None
        self.size_pv_sigy = None
        if hasattr(self, "size_pv_sigx_label"):
            self.size_pv_sigx_label.setText("--")
            self.size_pv_sigy_label.setText("--")

    def _active_image_axes(self):
        if self.h is None:
            return None
        axes = getattr(self.h, "axes", None)
        if axes is None:
            self.h = None
        return axes

    def _show_profile_placeholder(self, title, warning=None, status_text=None, status_tone="warning"):
        self._warn_once(warning)
        self._draw_placeholder_plot(title)
        if status_text:
            self._set_profile_status(status_text, status_tone)
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
        geometry = resolve_element_image_geometry(
            self.app_context,
            flag_id,
            self.control_backend,
        )
        self._pixel_geometry_flag_id = flag_id
        self.pixel = geometry.shape
        pixel_width = geometry.pixel_width_mm
        self.width = self.pixel[0] * pixel_width
        self.height = self.pixel[1] * pixel_width
        self._reset_view_limits()
        self._image_pv = None
        self._image_pv_name = None

    def _reset_view_limits(self):
        self.xlim = (-0.5 * self.width, 0.5 * self.width)
        self.ylim = (-0.5 * self.height, 0.5 * self.height)
        self.extent = self.xlim + self.ylim

    def _get_refresh_interval_ms(self):
        text = self.lineEdit_9.text().strip()
        if not text:
            return None
        try:
            interval_ms = round(float(text) * 1000)
        except ValueError:
            return None
        return interval_ms if interval_ms > 0 else None

    def _resolve_intensity_limits(self, data):
        data_min = float(np.min(data))
        data_max = float(np.max(data))

        def parse_limit(line_edit, fallback):
            text = line_edit.text().strip()
            if not text:
                return fallback
            try:
                value = float(text)
            except ValueError:
                line_edit.clear()
                return fallback
            if not math.isfinite(value):
                line_edit.clear()
                return fallback
            return value

        vmin = parse_limit(self.lineEdit_4, data_min)
        vmax = parse_limit(self.lineEdit_3, data_max)
        if not vmin < vmax:
            self._warn_once(
                f"Warning: invalid intensity range for {self.tmppv}: vmin {vmin} must be smaller than vmax {vmax}."
            )
            self._set_profile_status("Bad intensity range", "warning")
            vmin = data_min
            vmax = data_max
            if not vmin < vmax:
                vmax = vmin + 1.0
            self.lineEdit_4.clear()
            self.lineEdit_3.clear()
        return vmin, vmax

    def reset_view(self):
        self._reset_view_limits()
        self._set_profile_status("View reset", "subtle")

        axes = self._active_image_axes()
        if axes is not None:
            axes.set_xlim(self.xlim)
            axes.set_ylim(self.ylim)
            self.widget.canvas.draw()
        self._refresh_status()

    def _configure_active_channels(self):
        mode = self._current_mode()
        self.pv = resolve_channel(self.app_context, self.tmppv, "image")
        self.exposure_target = self._resolve_optional_write_target(
            self.tmppv,
            "exposure_time",
            "s",
        )
        self.expoTimePV = (
            self.exposure_target.pv_name
            if self.exposure_target is not None
            else None
        )

        if mode not in ("real", "vm"):
            print("Error, usage: python main.py [real|vm]")
            return False
        if self.pv != self._image_pv_name:
            self._image_pv = PV(self.pv)
            self._image_pv_name = self.pv
        size_names = (
            self._resolve_optional_channel(self.tmppv, "sigx"),
            self._resolve_optional_channel(self.tmppv, "sigy"),
        )
        if size_names != self._size_pv_names:
            self._size_pvs = tuple(PV(name) if name else None for name in size_names)
            self._size_pv_names = size_names
            self._clear_size_pv_stats()
        return True

    def _read_exposure_time(self):
        if not hasattr(self, "expoTimePV"):
            self._configure_active_channels()

        mode = self._current_mode()
        if mode == "vm":
            self.lineEdit.setText("VM")
            return

        if self.expoTimePV is None:
            self.lineEdit.setText("--")
            return

        if self.lineEdit.hasFocus():
            return

        try:
            expoTime = caget(self.expoTimePV)
            if expoTime is not None:
                self.lineEdit.setText(str(expoTime))
        except Exception as exc:
            self._mark_pv_unavailable(exc)

    def _read_size_pv(self):
        values = []
        for pv in self._size_pvs:
            if pv is None:
                values.append(None)
                continue
            try:
                value = float(pv.get(timeout=0.05))
            except Exception:
                value = None
            values.append(
                value if value is not None and math.isfinite(value) and value > 0 else None
            )
        self.size_pv_sigx, self.size_pv_sigy = values
        self.size_pv_sigx_label.setText(
            f"{self.size_pv_sigx:.3f}" if self.size_pv_sigx is not None else "--"
        )
        self.size_pv_sigy_label.setText(
            f"{self.size_pv_sigy:.3f}" if self.size_pv_sigy is not None else "--"
        )

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
            if (
                self.exposure_target is not None
                and not self.exposure_target.machine_limit.contains(expoTime)
            ):
                print(
                    f"Exposure time {expoTime:g} s is outside machine limit "
                    f"{self.exposure_target.machine_limit.describe()}."
                )
                return
            try:
                caput(self.expoTimePV, expoTime)
                self._mark_pv_available()
                self.lineEdit.clearFocus()
                self._read_exposure_time()
            except Exception as exc:
                self._mark_pv_unavailable(exc)

        elif mode == "vm":
            self.lineEdit.setText("VM")

    def plot_beamprofile(self):
        self.tmppv = self.flag_selec.currentText()
        if self.tmppv != self._pixel_geometry_flag_id:
            self._configure_pixel_geometry(self.tmppv)
        if not self._configure_active_channels():
            return
        self._read_size_pv()

        try:
            tmp = self._image_pv.get(
                timeout=IMAGE_PV_GET_TIMEOUT_S,
                use_monitor=False,
            )
            self._mark_pv_available()
        except Exception as exc:
            self._mark_pv_unavailable(exc)
            self._show_profile_placeholder("Beam Profile / Offline", status_text="PV offline")
            return

        if tmp is None:
            self._show_profile_placeholder(
                f"{self.tmppv} / No Data",
                warning=f"Warning: {self.pv} has no image data.",
                status_text="No data",
            )
            return

        try:
            data_ini = list(map(float, tmp))
        except (TypeError, ValueError) as exc:
            self._show_profile_placeholder(
                f"{self.tmppv} / Invalid Data",
                warning=f"Warning: beam profile data is not numeric array-like data: {exc}",
                status_text="Invalid data",
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
            status_text = "No VM data" if len(data_ini) == 0 else "Invalid shape"
            self._show_profile_placeholder(title, warning=warning, status_text=status_text)
            return

        raw_data = np.reshape(data_ini, (self.pixel[1], self.pixel[0]))
        self._profile_warning = None
        self._clear_profile_stats()

        axes = self._active_image_axes()
        if axes is not None:
            self.xlim = axes.get_xlim()
            self.ylim = axes.get_ylim()

        self._clear_image_plot_state()
        self.widget.axes.clear()
        self._style_plot_axes()

        if self.subtract_background_enabled and self._background_exposure_mismatch():
            self.subtract_background_enabled = False
            blocked = self.background_subtract_checkbox.blockSignals(True)
            self.background_subtract_checkbox.setChecked(False)
            self.background_subtract_checkbox.blockSignals(blocked)
            self._update_background_status()
            self._notify(
                "Camera exposure changed; background subtraction has been disabled."
            )

        try:
            active_roi = self.roi_control.active_roi()
            analysis_extent = (
                roi_extent(self.extent, active_roi, raw_data.shape)
                if active_roi is not None
                else self.extent
            )
            analysis_xlim = analysis_extent[:2] if active_roi is not None else self.xlim
            analysis_ylim = analysis_extent[2:] if active_roi is not None else self.ylim
            data, fit_result = analyze_beam_image(
                raw_data,
                extent=self.extent,
                background=self.background_image if self.subtract_background_enabled else None,
                xlim=analysis_xlim,
                ylim=analysis_ylim,
                method=self.profile_method_combo.currentText(),
                roi=active_roi,
            )
        except BackgroundStoreError as exc:
            self.subtract_background_enabled = False
            blocked = self.background_subtract_checkbox.blockSignals(True)
            self.background_subtract_checkbox.setChecked(False)
            self.background_subtract_checkbox.blockSignals(blocked)
            self._show_profile_placeholder(
                f"{self.tmppv} / Background Error",
                warning=f"Warning: background subtraction failed: {exc}",
                status_text="Background error",
            )
            return

        vmin, vmax = self._resolve_intensity_limits(data)

        vnorm = mpl.colors.Normalize(vmin=vmin, vmax=vmax)
        colormap = self.comboBox_2.currentText()

        display_data = raw_data if active_roi is not None else data
        display_extent = self.extent if active_roi is not None else analysis_extent
        self.h = self.widget.axes.imshow(
            display_data,
            cmap=colormap,
            norm=vnorm,
            origin="lower",
            extent=display_extent,
            aspect="auto",
        )
        self.colorbar = self.widget.fig.colorbar(self.h)
        self.colorbar.ax.yaxis.set_tick_params(color=self._palette()["plot_text"])
        for tick in self.colorbar.ax.get_yticklabels():
            tick.set_color(self._palette()["plot_text"])

        profile_method = self.profile_method_combo.currentText()
        background_marker = " • BG" if self.subtract_background_enabled else ""
        self.widget.axes.set_title(
            f"{self.tmppv} • {profile_method}{background_marker}",
            color=self._palette()["plot_text"],
            fontsize=11,
            fontweight="bold",
            loc="left",
        )
        self.widget.axes.set_xlabel("x (mm)")
        self.widget.axes.set_ylabel("y (mm)")
        self.widget.axes.set_xlim(self.xlim if active_roi is not None else analysis_xlim)
        self.widget.axes.set_ylim(self.ylim if active_roi is not None else analysis_ylim)
        self.roi_control.attach_axes(self.widget.axes, extent=self.extent)

        height = abs(analysis_ylim[1] - analysis_ylim[0])
        width = abs(analysis_xlim[1] - analysis_xlim[0])

        if not fit_result.has_signal:
            self._set_profile_status("Low signal", "warning")
            self.widget.canvas.draw()
            self._refresh_status()
            return

        norm_denx = fit_result.x_projection.normalized_projection
        norm_deny = fit_result.y_projection.normalized_projection
        if norm_denx is not None and norm_deny is not None:
            denx = norm_denx * height * 0.3 + analysis_ylim[0] * 0.98
            deny = norm_deny * width * 0.3 + analysis_xlim[0] * 0.98
            self.widget.axes.plot(fit_result.x_axis, denx, "--c")
            self.widget.axes.plot(deny, fit_result.y_axis, "--c")

        if fit_result.valid:
            if fit_result.x_projection.fitted_projection is not None:
                fit_denx = (
                    fit_result.x_projection.fitted_projection * height * 0.3
                    + analysis_ylim[0] * 0.98
                )
                self.widget.axes.plot(fit_result.x_axis, fit_denx, "--r")
            self.sigx = round(fit_result.sigx_mm, 3)
            self.lineEdit_5.setText(str(self.sigx))

            if fit_result.y_projection.fitted_projection is not None:
                fit_deny = (
                    fit_result.y_projection.fitted_projection * width * 0.3
                    + analysis_xlim[0] * 0.98
                )
                self.widget.axes.plot(
                    fit_deny,
                    fit_result.y_axis,
                    "--r",
                    label="Gaussian fit",
                )
                self.widget.axes.legend()

            self.sigy = round(fit_result.sigy_mm, 3)
            self.lineEdit_6.setText(str(self.sigy))
        elif fit_result.status == "fit_failed":
            self._warn_once(
                f"Warning: beam profile {fit_result.method} analysis failed: "
                f"{fit_result.message}"
            )
            self._set_profile_status(f"{fit_result.method} failed", "warning")
            self.lineEdit_5.setText("--")
            self.lineEdit_6.setText("--")

        self.widget.canvas.draw()
        self._refresh_status()

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
