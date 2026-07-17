import sys
import colorsys
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
from PyQt5.QtGui import QDoubleValidator, QIntValidator
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from half_linac.src.shared.machine_profile import (
    get_workflow,
    list_elements,
    load_app_context,
    resolve_channel,
)
from half_linac.src.shared.window_activation import install_qt_window_raise_handler
from gui import Ui_MainWindow


HEADER_ACTION_HEIGHT = 32
TRACE_COLOR_GOLDEN_RATIO = 0.618033988749895
MAX_HOLD_TRACES = 150

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
    background-color: transparent;
    border: none;
    color: {summary_title_fg};
    font-size: 15px;
    font-weight: 700;
}}

QLabel[role="field"] {{
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

QPushButton:checked {{
    background-color: {metric_active_fg};
    border-color: {metric_active_fg};
    color: {window_bg};
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
    max-height: 28px;
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

QLineEdit#refreshIntervalEdit {{
    min-height: {header_action_height}px;
    max-height: {header_action_height}px;
    padding: 0px 10px;
}}

QLineEdit[compact="true"] {{
    min-height: 28px;
    max-height: 28px;
    padding: 0px 8px;
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
        install_qt_window_raise_handler(self)
        self.app_context = load_app_context("orbit_display")
        self.machine_profile = self.app_context.profile
        self.control_backend = self.app_context.control_backend.name
        self.bpm_position_scale_to_mm = self._resolve_bpm_position_scale_to_mm()
        self.bpm_elements = list_elements(self.app_context, kind="bpm")
        self.bpm_ids = [element.id for element in self.bpm_elements]
        self.bpm_x_pvs = [resolve_channel(self.app_context, bpm_id, "x") for bpm_id in self.bpm_ids]
        self.bpm_y_pvs = [resolve_channel(self.app_context, bpm_id, "y") for bpm_id in self.bpm_ids]

        self.current_theme = "dark"
        self.is_x_running = False
        self.is_y_running = False
        self._pv_available = False
        self._pv_error = None
        self._x_trace_count = 0
        self._y_trace_count = 0
        self._bpm_range_start = 1
        self._bpm_range_end = len(self.bpm_ids)
        self._bpm_range_is_custom = False
        self._axis_ranges = {"x": (None, None), "y": (None, None)}
        self._bpm_detail_window = None
        self.refresh_interval_ms = 1000

        self._configure_window()
        self._configure_controls()
        self._build_plot_panel()

        self.init_pv()

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._refresh_running_orbits)

        self.x_live_button.clicked.connect(lambda: self._toggle_plane_live("x"))
        self.y_live_button.clicked.connect(lambda: self._toggle_plane_live("y"))
        self.hold_1.toggled.connect(lambda checked: self._handle_hold_toggled("x", checked))
        self.hold_2.toggled.connect(lambda checked: self._handle_hold_toggled("y", checked))

        self._prepare_empty_plot(self.graphWidget_1.canvas.axes, self.graphWidget_1.canvas.figure, "Horizontal Orbit")
        self._prepare_empty_plot(self.graphWidget_2.canvas.axes, self.graphWidget_2.canvas.figure, "Vertical Orbit")
        self._start_default_refresh()
        self._refresh_status()

    def _configure_window(self):
        self.setWindowTitle(f"{self.machine_profile.machine.display_name} Orbit Display")
        self.resize(1320, 900)
        self._apply_theme()
        self.statusBar().showMessage("Orbit display ready.", 5000)

    def _configure_controls(self):
        self.horizontalLayout.setContentsMargins(10, 10, 10, 10)
        self.horizontalLayout.setSpacing(12)

    def _build_plot_panel(self):
        self.verticalLayout_3.removeWidget(self.frame_2)
        self.verticalLayout_3.removeWidget(self.frame_3)
        self.verticalLayout_4.removeWidget(self.graphWidget_1)
        self.verticalLayout_4.removeWidget(self.graphWidget_2)

        while self.horizontalLayout.count():
            self.horizontalLayout.takeAt(0)

        self.page_layout = QVBoxLayout()
        self.page_layout.setContentsMargins(0, 0, 0, 0)
        self.page_layout.setSpacing(12)
        self.horizontalLayout.addLayout(self.page_layout)

        self.page_layout.addWidget(self._build_summary_panel())

        self.frame_2.hide()
        self.frame_3.hide()

        self.page_layout.addWidget(
            self._build_plot_card("Horizontal Orbit", self.graphWidget_1, "x"),
            1,
        )
        self.page_layout.addWidget(
            self._build_plot_card("Vertical Orbit", self.graphWidget_2, "y"),
            1,
        )

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

        self.live_button = QPushButton("Pause Live", panel)
        self.live_button.setObjectName("headerButton")
        self.live_button.setFixedHeight(HEADER_ACTION_HEIGHT)
        self.live_button.setToolTip("Pause or resume both orbit plots.")
        self.live_button.clicked.connect(self._toggle_all_live)
        header_layout.addWidget(self.live_button)

        refresh_label = QLabel("Refresh (s)", panel)
        refresh_label.setProperty("role", "field")
        header_layout.addWidget(refresh_label)

        self.refresh_interval_edit = QLineEdit(panel)
        self.refresh_interval_edit.setObjectName("refreshIntervalEdit")
        self.refresh_interval_edit.setText("1.0")
        self.refresh_interval_edit.setFixedWidth(72)
        self.refresh_interval_edit.setFixedHeight(HEADER_ACTION_HEIGHT)
        self.refresh_interval_edit.setToolTip("Refresh interval in seconds.")
        self.refresh_interval_edit.returnPressed.connect(self._apply_refresh_interval)
        self.refresh_interval_edit.editingFinished.connect(self._apply_refresh_interval)
        header_layout.addWidget(self.refresh_interval_edit)

        bpm_range_label = QLabel("BPM Range", panel)
        bpm_range_label.setProperty("role", "field")
        header_layout.addWidget(bpm_range_label)

        bpm_validator = QIntValidator(1, max(1, len(self.bpm_ids)), panel)
        self.bpm_start_edit = QLineEdit(panel)
        self.bpm_start_edit.setProperty("compact", True)
        self.bpm_start_edit.setFixedWidth(54)
        self.bpm_start_edit.setPlaceholderText("1")
        self.bpm_start_edit.setValidator(bpm_validator)
        self.bpm_start_edit.setToolTip("First BPM index. Empty uses the first BPM.")
        header_layout.addWidget(self.bpm_start_edit)

        bpm_range_separator = QLabel("to", panel)
        bpm_range_separator.setProperty("role", "field")
        header_layout.addWidget(bpm_range_separator)

        self.bpm_end_edit = QLineEdit(panel)
        self.bpm_end_edit.setProperty("compact", True)
        self.bpm_end_edit.setFixedWidth(54)
        self.bpm_end_edit.setPlaceholderText(str(len(self.bpm_ids)))
        self.bpm_end_edit.setValidator(bpm_validator)
        self.bpm_end_edit.setToolTip("Last BPM index. Empty uses the last BPM.")
        header_layout.addWidget(self.bpm_end_edit)
        for field in (self.bpm_start_edit, self.bpm_end_edit):
            field.returnPressed.connect(self._apply_shared_bpm_range)
            field.editingFinished.connect(self._apply_shared_bpm_range)

        self.detail_button = QPushButton("BPM Detail", panel)
        self.detail_button.setObjectName("headerButton")
        self.detail_button.setFixedHeight(HEADER_ACTION_HEIGHT)
        self.detail_button.clicked.connect(self.start_bpmvalue_btn)
        header_layout.addWidget(self.detail_button)

        for widget in (
            self.live_button,
            self.refresh_interval_edit,
            self.detail_button,
        ):
            widget.setFixedHeight(HEADER_ACTION_HEIGHT + 2)
            header_layout.setAlignment(widget, Qt.AlignVCenter)

        self.theme_toggle_button = QToolButton(panel)
        self.theme_toggle_button.setObjectName("themeToggleButton")
        self.theme_toggle_button.setFixedSize(HEADER_ACTION_HEIGHT, HEADER_ACTION_HEIGHT)
        self.theme_toggle_button.clicked.connect(self._toggle_theme)
        header_layout.addWidget(self.theme_toggle_button)

        outer_layout.addLayout(header_layout)

        self.status_panel = OrbitStatusStrip(panel)
        self.status_panel.add_item("machine", "Machine", self.machine_profile.machine.id)
        self.status_panel.add_item("backend", "Backend", self._format_backend_name())
        self.status_panel.add_item("x", "X Orbit", "Idle")
        self.status_panel.add_item("y", "Y Orbit", "Idle")
        self.status_panel.add_item("hold", "History", "Off")
        self.status_panel.add_item("refresh", "Refresh", "1.0 s")
        self.status_panel.add_item("view", "BPM View", f"1-{len(self.bpm_ids)} default")
        self.status_panel.finish()
        self.status_panel.apply_theme(self._palette())
        self.status_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.status_panel.setFixedHeight(self.status_panel.sizeHint().height())
        self._update_theme_toggle_button()

        outer_layout.addWidget(self.status_panel)
        return panel

    def _build_plot_card(self, title_text, plot_widget, plane):
        card = QFrame(self.centralwidget)
        card.setObjectName("plotCard")
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        title = QLabel(title_text, card)
        title.setObjectName("panelTitle")
        header_layout.addWidget(title)
        header_layout.addStretch(1)

        live_button = QPushButton(card)
        live_button.setProperty("compact", True)
        live_button.setFixedWidth(78)
        live_button.setToolTip(f"Pause or resume the {plane.upper()} orbit plot only.")
        header_layout.addWidget(live_button)

        range_label = QLabel("Y Range (mm)", card)
        range_label.setProperty("role", "field")
        header_layout.addWidget(range_label)

        range_validator = QDoubleValidator(card)
        range_validator.setNotation(QDoubleValidator.ScientificNotation)
        minimum_edit = QLineEdit(card)
        minimum_edit.setProperty("compact", True)
        minimum_edit.setFixedWidth(80)
        minimum_edit.setPlaceholderText("Min")
        minimum_edit.setValidator(range_validator)
        minimum_edit.setToolTip(
            f"Optional minimum {plane.upper()} orbit Y-axis value in mm."
        )
        header_layout.addWidget(minimum_edit)

        range_separator = QLabel("to", card)
        range_separator.setProperty("role", "field")
        header_layout.addWidget(range_separator)

        maximum_edit = QLineEdit(card)
        maximum_edit.setProperty("compact", True)
        maximum_edit.setFixedWidth(80)
        maximum_edit.setPlaceholderText("Max")
        maximum_edit.setValidator(range_validator)
        maximum_edit.setToolTip(
            f"Optional maximum {plane.upper()} orbit Y-axis value in mm."
        )
        header_layout.addWidget(maximum_edit)
        for field in (minimum_edit, maximum_edit):
            field.returnPressed.connect(lambda plane=plane: self._apply_axis_range(plane))
            field.editingFinished.connect(
                lambda plane=plane: self._apply_axis_range(plane)
            )

        auto_button = QPushButton("Auto", card)
        auto_button.setProperty("compact", True)
        auto_button.setToolTip(f"Restore automatic {plane.upper()}-plot Y-axis limits.")
        auto_button.clicked.connect(lambda: self._set_axis_auto(plane))
        header_layout.addWidget(auto_button)

        overlay_button = QPushButton("Trace History", card)
        overlay_button.setProperty("compact", True)
        overlay_button.setCheckable(True)
        overlay_button.setToolTip(
            f"Overlay up to the latest {MAX_HOLD_TRACES} {plane.upper()} orbit traces."
        )
        header_layout.addWidget(overlay_button)

        clear_button = QPushButton("Clear", card)
        clear_button.setProperty("compact", True)
        clear_button.setToolTip(f"Clear accumulated {plane.upper()} orbit traces.")
        clear_button.clicked.connect(lambda: self._clear_trace_history(plane))
        header_layout.addWidget(clear_button)

        if plane == "x":
            self.x_live_button = live_button
            self.QL_cxmin = minimum_edit
            self.QL_cxmax = maximum_edit
            self.hold_1 = overlay_button
        else:
            self.y_live_button = live_button
            self.QL_cymin = minimum_edit
            self.QL_cymax = maximum_edit
            self.hold_2 = overlay_button

        layout.addLayout(header_layout)

        plot_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(plot_widget)
        return card

    def _apply_theme(self):
        palette = self._palette()
        self.setStyleSheet(build_orbit_display_theme(palette))
        if hasattr(self, "status_panel"):
            self.status_panel.apply_theme(palette)
        if self._bpm_detail_window is not None:
            self._bpm_detail_window.apply_theme(palette)
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
        self._reset_trace_sequence("x")
        self._reset_trace_sequence("y")
        self._refresh_status()
        self._prepare_empty_plot(self.graphWidget_1.canvas.axes, self.graphWidget_1.canvas.figure, "Horizontal Orbit")
        self._prepare_empty_plot(self.graphWidget_2.canvas.axes, self.graphWidget_2.canvas.figure, "Vertical Orbit")

    def _palette(self):
        return DARK_THEME if self.current_theme == "dark" else LIGHT_THEME

    def _notify(self, message):
        self.statusBar().showMessage(message, 5000)

    def _format_backend_name(self):
        return self.control_backend.upper()

    def _apply_refresh_interval(self):
        if not hasattr(self, "refresh_interval_edit"):
            return
        raw_text = self.refresh_interval_edit.text().strip()
        try:
            interval_s = float(raw_text)
        except ValueError:
            self._restore_refresh_interval_text()
            self._notify("Refresh interval must be numeric seconds.")
            return
        if interval_s <= 0:
            self._restore_refresh_interval_text()
            self._notify("Refresh interval must be greater than 0 seconds.")
            return

        new_interval_ms = max(100, int(round(interval_s * 1000)))
        if new_interval_ms == self.refresh_interval_ms:
            self._restore_refresh_interval_text()
            return

        self.refresh_interval_ms = new_interval_ms
        self._restore_refresh_interval_text()
        self._sync_bpm_detail_refresh_interval()
        if self.is_x_running:
            self._start_refresh_timer()
        if self.is_y_running:
            self._start_refresh_timer()
        self._notify(f"Orbit refresh interval set to {self._format_refresh_interval()}.")
        self._refresh_status()

    def _restore_refresh_interval_text(self):
        if hasattr(self, "refresh_interval_edit"):
            self.refresh_interval_edit.setText(f"{self.refresh_interval_ms / 1000:.1f}")

    def _format_refresh_interval(self):
        interval_s = self.refresh_interval_ms / 1000
        if interval_s.is_integer():
            return f"{int(interval_s)} s"
        return f"{interval_s:.1f} s"

    def _sync_bpm_detail_refresh_interval(self):
        if self._bpm_detail_window is not None:
            self._bpm_detail_window.set_refresh_interval_ms(self.refresh_interval_ms)

    def _handle_hold_toggled(self, plane, checked):
        del checked
        self._reset_trace_sequence(plane)
        self._clear_orbit_plot(plane)
        if (plane == "x" and self.is_x_running) or (
            plane == "y" and self.is_y_running
        ):
            self._plot_plane(plane, read_pv=False)
        self._refresh_status()

    def _set_axis_auto(self, plane):
        if plane == "x":
            fields = (self.QL_cxmin, self.QL_cxmax)
        else:
            fields = (self.QL_cymin, self.QL_cymax)
        for field in fields:
            field.clear()
        self._axis_ranges[plane] = (None, None)
        self._plot_plane(plane, read_pv=False)
        self._notify(f"{plane.upper()} orbit Y-axis range set to Auto.")

    def _apply_axis_range(self, plane):
        if plane == "x":
            minimum_edit, maximum_edit = self.QL_cxmin, self.QL_cxmax
        else:
            minimum_edit, maximum_edit = self.QL_cymin, self.QL_cymax
        minimum_text = minimum_edit.text().strip()
        maximum_text = maximum_edit.text().strip()
        minimum = float(minimum_text) if minimum_text else None
        maximum = float(maximum_text) if maximum_text else None
        if minimum is not None and maximum is not None and minimum >= maximum:
            self._notify(
                f"{plane.upper()} plot minimum must be smaller than maximum."
            )
            self._restore_axis_range_fields(plane)
            return
        self._axis_ranges[plane] = (minimum, maximum)
        self._plot_plane(plane, read_pv=False)

    def _restore_axis_range_fields(self, plane):
        if plane == "x":
            fields = (self.QL_cxmin, self.QL_cxmax)
        else:
            fields = (self.QL_cymin, self.QL_cymax)
        values = self._axis_ranges[plane]
        for field in fields:
            field.blockSignals(True)
        try:
            for field, value in zip(fields, values):
                field.setText("" if value is None else f"{value:g}")
        finally:
            for field in fields:
                field.blockSignals(False)

    def _clear_trace_history(self, plane):
        self._reset_trace_sequence(plane)
        self._clear_orbit_plot(plane)
        if (plane == "x" and self.is_x_running) or (
            plane == "y" and self.is_y_running
        ):
            self._plot_plane(plane, read_pv=False)
        self._notify(f"{plane.upper()} orbit trace history cleared.")
        self._refresh_status()

    def _plot_plane(self, plane, *, read_pv):
        if plane == "x":
            self.plotorbit_x(read_pv=read_pv)
        else:
            self.plotorbit_y(read_pv=read_pv)

    def _reset_trace_sequence(self, plane):
        if plane == "x":
            self._x_trace_count = 0
        elif plane == "y":
            self._y_trace_count = 0

    def _clear_orbit_plot(self, plane):
        if plane == "x":
            self._prepare_empty_plot(
                self.graphWidget_1.canvas.axes,
                self.graphWidget_1.canvas.figure,
                "Horizontal Orbit",
            )
        elif plane == "y":
            self._prepare_empty_plot(
                self.graphWidget_2.canvas.axes,
                self.graphWidget_2.canvas.figure,
                "Vertical Orbit",
            )

    def _next_trace_color(self, plane):
        if plane == "x":
            shot_index = self._x_trace_count
            self._x_trace_count += 1
        else:
            shot_index = self._y_trace_count
            self._y_trace_count += 1
        return self._trace_color(shot_index)

    def _trace_color(self, shot_index):
        hue = (shot_index * TRACE_COLOR_GOLDEN_RATIO) % 1.0
        if self.current_theme == "dark":
            lightness = 0.64
            saturation = 0.78
        else:
            lightness = 0.42
            saturation = 0.76
        red, green, blue = colorsys.hls_to_rgb(hue, lightness, saturation)
        return "#{:02x}{:02x}{:02x}".format(
            int(red * 255),
            int(green * 255),
            int(blue * 255),
        )

    def _refresh_status(self):
        self.status_panel.set_item("machine", self.machine_profile.machine.id, "subtle")
        self.status_panel.set_item(
            "backend",
            self._format_backend_name(),
            "warning" if self.control_backend == "real" else "success",
        )
        self.status_panel.set_item("x", "Running" if self.is_x_running else "Idle", "success" if self.is_x_running else "subtle")
        self.status_panel.set_item("y", "Running" if self.is_y_running else "Idle", "success" if self.is_y_running else "subtle")

        hold_states = []
        if self.hold_1.isChecked():
            hold_states.append(f"X {min(self._x_trace_count, MAX_HOLD_TRACES)}")
        if self.hold_2.isChecked():
            hold_states.append(f"Y {min(self._y_trace_count, MAX_HOLD_TRACES)}")
        hold_text = " + ".join(hold_states) if hold_states else "Off"
        hold_tone = "warning" if hold_states else "subtle"
        self.status_panel.set_item("hold", hold_text, hold_tone)
        self.status_panel.set_item("refresh", self._format_refresh_interval(), "success")

        if not self._pv_available:
            self.status_panel.set_item("view", "Offline shell", "warning")
        else:
            self.status_panel.set_item("view", self._format_bpm_view(), "warning" if self._has_custom_view() else "subtle")
        self._update_live_controls()

    def _start_refresh_timer(self):
        if self.is_x_running or self.is_y_running:
            self.refresh_timer.start(self.refresh_interval_ms)

    def _start_default_refresh(self):
        self.is_x_running = True
        self.is_y_running = True
        self._update_live_controls()
        self._start_refresh_timer()
        self._refresh_running_orbits()
        self._notify("Orbit refresh started.")

    def _stop_refresh_timer_if_idle(self):
        if not self.is_x_running and not self.is_y_running:
            self.refresh_timer.stop()

    def _refresh_running_orbits(self):
        self.init_pv()
        if self.is_x_running:
            self.plotorbit_x(read_pv=False)
        if self.is_y_running:
            self.plotorbit_y(read_pv=False)

    def _has_custom_view(self):
        return self._bpm_range_is_custom

    def _format_bpm_view(self):
        suffix = " custom" if self._has_custom_view() else " default"
        return f"{self._bpm_range_start}-{self._bpm_range_end}{suffix}"

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

    def _update_live_controls(self):
        if not hasattr(self, "live_button"):
            return
        if self.is_x_running or self.is_y_running:
            self.live_button.setText("Pause Live")
        else:
            self.live_button.setText("Resume Live")
        self.x_live_button.setText("Pause X" if self.is_x_running else "Resume X")
        self.y_live_button.setText("Pause Y" if self.is_y_running else "Resume Y")

    def _toggle_all_live(self):
        if self.is_x_running or self.is_y_running:
            self.is_x_running = False
            self.is_y_running = False
            self.refresh_timer.stop()
            self._notify("Orbit refresh paused for both planes.")
        else:
            self._apply_refresh_interval()
            self.is_x_running = True
            self.is_y_running = True
            self._start_refresh_timer()
            self._refresh_running_orbits()
            self._notify("Orbit refresh resumed for both planes.")
        self._update_live_controls()
        self._refresh_status()

    def _toggle_plane_live(self, plane):
        if plane == "x":
            if self.is_x_running:
                self.stop1_btn()
            else:
                self.start1_btn()
        elif self.is_y_running:
            self.stop2_btn()
        else:
            self.start2_btn()

    def _apply_shared_bpm_range(self):
        start_text = self.bpm_start_edit.text().strip()
        end_text = self.bpm_end_edit.text().strip()
        start = int(start_text) if start_text else 1
        end = int(end_text) if end_text else len(self.bpm_ids)
        if start >= end:
            self._notify("BPM range start must be smaller than end.")
            self._restore_bpm_range_fields()
            return
        self._bpm_range_start = start
        self._bpm_range_end = end
        self._bpm_range_is_custom = bool(start_text or end_text)
        self._refresh_status()
        if self.is_x_running or self.is_y_running:
            self._refresh_running_orbits()

    def _restore_bpm_range_fields(self):
        for field in (self.bpm_start_edit, self.bpm_end_edit):
            field.blockSignals(True)
        try:
            if self._bpm_range_is_custom:
                self.bpm_start_edit.setText(str(self._bpm_range_start))
                self.bpm_end_edit.setText(str(self._bpm_range_end))
            else:
                self.bpm_start_edit.clear()
                self.bpm_end_edit.clear()
        finally:
            for field in (self.bpm_start_edit, self.bpm_end_edit):
                field.blockSignals(False)

    def start1_btn(self):
        if self.hold_1.isChecked():
            self._reset_trace_sequence("x")
            self._clear_orbit_plot("x")
        self._apply_refresh_interval()
        self.is_x_running = True
        self._start_refresh_timer()
        self._update_live_controls()
        self._notify("Horizontal orbit refresh started.")
        self._refresh_status()

    def stop1_btn(self):
        self.is_x_running = False
        self._stop_refresh_timer_if_idle()
        self._update_live_controls()
        self._notify("Horizontal orbit refresh stopped.")
        self._refresh_status()

    def start2_btn(self):
        if self.hold_2.isChecked():
            self._reset_trace_sequence("y")
            self._clear_orbit_plot("y")
        self._apply_refresh_interval()
        self.is_y_running = True
        self._start_refresh_timer()
        self._update_live_controls()
        self._notify("Vertical orbit refresh started.")
        self._refresh_status()

    def stop2_btn(self):
        self.is_y_running = False
        self._stop_refresh_timer_if_idle()
        self._update_live_controls()
        self._notify("Vertical orbit refresh stopped.")
        self._refresh_status()

    def _resolve_bpm_position_scale_to_mm(self):
        workflow = get_workflow(self.machine_profile, "orbit")
        scale_by_backend = workflow.get("bpm_position_scale_to_mm", {})
        if isinstance(scale_by_backend, dict):
            try:
                return float(scale_by_backend.get(self.control_backend, 1000.0))
            except (TypeError, ValueError):
                pass
        return 1000.0

    def _scale_bpm_values(self, values):
        return [
            float(value) * self.bpm_position_scale_to_mm
            if value is not None
            else np.nan
            for value in values
        ]

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

    def _apply_plot_limits(self, ax, *, plane):
        y_min, y_max = self._axis_ranges[plane]
        bpm_start = self._bpm_range_start
        bpm_end = self._bpm_range_end

        ax.set_autoscaley_on(True)
        ax.relim()
        ax.autoscale_view(scalex=False, scaley=True)
        if y_min is not None:
            ax.set_ylim(bottom=y_min)
        if y_max is not None:
            ax.set_ylim(top=y_max)

        ax.set_xlim(left=bpm_start, right=bpm_end)

    @staticmethod
    def _trim_hold_traces(ax):
        while len(ax.lines) > MAX_HOLD_TRACES:
            ax.lines[0].remove()

    def plotorbit_x(self, read_pv=True):
        if read_pv:
            self.init_pv()
        pvl_val = self._scale_bpm_values(self.pvlx_val)
        palette = self._palette()

        ax = self.graphWidget_1.canvas.axes
        fig = self.graphWidget_1.canvas.figure

        hold_trace = self.hold_1.isChecked()
        if not hold_trace:
            ax.clear()
            self._reset_trace_sequence("x")

        self._style_plot_axes(ax, fig)

        if not self._pv_available:
            ax.set_title("Horizontal Orbit / Offline", color=palette["plot_text"], fontsize=11, fontweight="bold", loc="left")
            ax.set_xlabel("BPM #", fontweight="bold")
            ax.set_ylabel("Cx (mm)", fontweight="bold")
            self.graphWidget_1.canvas.draw()
            self._refresh_status()
            return

        x = np.linspace(1, len(pvl_val), len(pvl_val))
        trace_color = self._next_trace_color("x") if hold_trace else palette["orbit_x"]
        ax.plot(
            x,
            pvl_val,
            "-o",
            color=trace_color,
            markerfacecolor=trace_color,
            markeredgecolor=palette["plot_bg"],
            markersize=4,
            linewidth=1.5,
        )
        self._apply_plot_limits(ax, plane="x")
        ax.set_xlabel("BPM #", fontweight="bold")
        ax.set_ylabel("Cx (mm)", fontweight="bold")
        if hold_trace:
            self._trim_hold_traces(ax)
        self.graphWidget_1.canvas.draw()
        self._refresh_status()

    def plotorbit_y(self, read_pv=True):
        if read_pv:
            self.init_pv()
        pvl_val = self._scale_bpm_values(self.pvly_val)
        palette = self._palette()

        ax = self.graphWidget_2.canvas.axes
        fig = self.graphWidget_2.canvas.figure

        hold_trace = self.hold_2.isChecked()
        if not hold_trace:
            ax.clear()
            self._reset_trace_sequence("y")

        self._style_plot_axes(ax, fig)

        if not self._pv_available:
            ax.set_title("Vertical Orbit / Offline", color=palette["plot_text"], fontsize=11, fontweight="bold", loc="left")
            ax.set_xlabel("BPM #", fontweight="bold")
            ax.set_ylabel("Cy (mm)", fontweight="bold")
            self.graphWidget_2.canvas.draw()
            self._refresh_status()
            return

        x = np.linspace(1, len(pvl_val), len(pvl_val))
        trace_color = self._next_trace_color("y") if hold_trace else palette["orbit_y"]
        ax.plot(
            x,
            pvl_val,
            "-o",
            color=trace_color,
            markerfacecolor=trace_color,
            markeredgecolor=palette["plot_bg"],
            markersize=4,
            linewidth=1.5,
        )
        self._apply_plot_limits(ax, plane="y")
        ax.set_xlabel("BPM #", fontweight="bold")
        ax.set_ylabel("Cy (mm)", fontweight="bold")
        if hold_trace:
            self._trim_hold_traces(ax)
        self.graphWidget_2.canvas.draw()
        self._refresh_status()

    def start_bpmvalue_btn(self):
        if self._bpm_detail_window is None:
            from submain import myWindow as BpmDetailWindow

            self._bpm_detail_window = BpmDetailWindow(
                refresh_interval_ms=self.refresh_interval_ms,
                palette=self._palette(),
                parent=self,
            )
            self._bpm_detail_window.setAttribute(Qt.WA_DeleteOnClose, True)
            self._bpm_detail_window.destroyed.connect(
                lambda *_: setattr(self, "_bpm_detail_window", None)
            )
        else:
            self._sync_bpm_detail_refresh_interval()
        self._bpm_detail_window.show()
        self._bpm_detail_window.raise_()
        self._bpm_detail_window.activateWindow()
        self._notify("BPM detail window opened.")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = myWindow()
    window.show()
    sys.exit(app.exec_())
