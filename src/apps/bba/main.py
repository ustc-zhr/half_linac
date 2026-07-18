import json
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
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
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gui import Ui_Form

from half_linac.src.shared.machine_profile import (
    RuntimeContextWidget,
    build_model_backend,
    build_model_snapshot,
    describe_app_model_support,
    get_bba_preset,
    list_elements,
    load_app_context,
    MachineProfileError,
    model_snapshot_lattice_overrides,
    require_workflow_write_allowed,
    resolve_channel,
    resolve_corrector_write_channel,
)
from half_linac.src.shared.app_theme import resolve_initial_theme
from half_linac.src.apps.bba.profile_runtime import (
    new_bba_scan_archive_dir,
    resolve_bba_runtime_paths,
)
from half_linac.src.shared.window_activation import install_qt_window_raise_handler

K1LQ_FACTOR = 0.15
HEADER_ACTION_HEIGHT = 32
BBA1_QUAD_X_LABEL = "$K_1 (m^{-2})$"
BBA1_SLOPE_LABEL = "dBPM2/dK1"
BBA2_QUAD_X_LABEL = "$K_1L_q (m^{-1})$"
BBA1_SCAN_POINT_COLUMNS = ("Use", "Corrector", "K1", "BPM1 (mm)", "BPM2 (mm)")
BBA2_SCAN_POINT_COLUMNS = {
    "quad": ("Use", "K1Leff", "BPM2 (mm)"),
    "bpm1": ("Use", "BPM1 (mm)"),
    "corrector": ("Use", "COR", "theta", "BPM2 (mm)"),
}
BBA2_SCAN_POINT_LABELS = {
    "quad": "Quad Scan",
    "bpm1": "BPM1 Samples",
    "corrector": "COR Scan",
}
BBA1_TOOLTIP = (
    "BBA-1 scans quad K1 at several COR settings, fits dBPM2/dK1 versus BPM1, "
    "and reports the BPM1 reading at the quad center."
)
BBA2_TOOLTIP = (
    "BBA-2 fits BPM2 versus quad K1Leff and corrector kick, then reports "
    "the BPM1 reading at the quad center."
)


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

    def set_item(self, key, text, tone="subtle", tooltip=None):
        item = self._items.get(key)
        if item is None:
            return
        container, value_label = item
        container.setProperty("tone", tone)
        value_label.setProperty("tone", tone)
        value_label.setText(text)
        container.setToolTip(tooltip or "")
        value_label.setToolTip(tooltip or "")
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
    settle_time: float = 0.0
    sample_interval: float = 0.0
    recal: bool = False
    energy_mev: float = 0.0
    bpm1_samples: int = 0
    by_formula: str = ""
    bx_formula: str = ""
    leff_by: float = 0.0
    leff_bx: float = 0.0
    quad_leff: float = K1LQ_FACTOR
    control_backend: str = ""
    app_context: object | None = None
    bba1_data_path: Path | None = None
    bba1_quad_scan_path: Path | None = None
    bba1_metadata_path: Path | None = None
    bba1_source_dir: Path | None = None
    bba1_recal_points: list[tuple[float, float, float, float]] | None = None
    bba2_quad_scan_path: Path | None = None
    bba2_bpm1_path: Path | None = None
    bba2_corrector_scan_path: Path | None = None
    bba2_metadata_path: Path | None = None
    latest_metadata_path: Path | None = None
    bba2_recal_quad_points: list[tuple[float, float]] | None = None
    bba2_recal_bpm1_points: list[float] | None = None
    bba2_recal_corrector_points: list[tuple[float, float]] | None = None
    model_snapshot_metadata: dict | None = None
    model_lattice_overrides: dict | None = None
    model_snapshot_error: str | None = None
    archive_dir: Path | None = None


class myWindow(QWidget, Ui_Form):
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        install_qt_window_raise_handler(self)
        self.app_context = load_app_context("bba")
        self.machine_profile = self.app_context.profile
        self.bba_workflow = self.app_context.bba_workflow
        if self.bba_workflow is None:
            raise ValueError("BBA workflow is not available in the current app context.")
        self.current_theme = resolve_initial_theme()
        self.scan = None
        self.clear = None
        self.scan_mode = None
        self.scan_family = None
        self._plot_wrappers = {}
        self._result_fields = []
        self.bba2_scan_points = {key: [] for key in BBA2_SCAN_POINT_COLUMNS}
        self.bba2_loaded_source_dir = None
        self.bba2_loaded_metadata = None
        self._model_backend_available, self._model_backend_error = describe_app_model_support(
            self.machine_profile.machine.id,
            "bba",
        )

        self._configure_window()
        self._setup_defaults()
        self._connect_buttons()
        self._build_shell()
        self._configure_form_content()
        self._configure_machine_profile()
        self._normalize_display_units()
        self._apply_theme()
        self._draw_placeholder_plots()
        self._refresh_status()

    def _configure_window(self):
        self.setWindowTitle(f"{self.machine_profile.machine.display_name} BBA")
        self.resize(1600, 960)
        self.setMinimumSize(1320, 860)
        self.tabWidget.setCurrentIndex(0)

    def _normalize_display_units(self):
        def normalize(text):
            return (
                text.replace("MEV", "MeV")
                .replace("Mev", "MeV")
                .replace("MM", "mm")
                .replace("(M)", "(m)")
                .replace("/M", "/m")
            )

        for label in self.findChildren(QLabel):
            label.setText(normalize(label.text()))

        for button_type in (QPushButton, QToolButton):
            for button in self.findChildren(button_type):
                button.setText(normalize(button.text()))

        for combo in self.findChildren(QComboBox):
            for index in range(combo.count()):
                combo.setItemText(index, normalize(combo.itemText(index)))
        bba1_tab_index = self.tabWidget.indexOf(self.tab)
        if bba1_tab_index >= 0:
            self.tabWidget.setTabToolTip(bba1_tab_index, BBA1_TOOLTIP)
        bba2_tab_index = self.tabWidget.indexOf(self.tab_2)
        if bba2_tab_index >= 0:
            self.tabWidget.setTabToolTip(bba2_tab_index, BBA2_TOOLTIP)

    def _setup_defaults(self):
        self.lineEdit_10.clear()
        self.lineEdit_18.clear()
        self.lineEdit_19.clear()
        self.lineEdit_19.setToolTip("")
        self.lineEdit_21.clear()
        if hasattr(self, "bba2_model_r12_edit"):
            self.bba2_model_r12_edit.clear()

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
        self.gridLayout_3.setVerticalSpacing(14)

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

        header_layout.addWidget(
            RuntimeContextWidget(
                machine_id=self.machine_profile.machine.id,
                machine_display_name=self.machine_profile.machine.display_name,
                control_backend=self.app_context.control_backend.name,
                parent=panel,
            )
        )

        self.theme_toggle_button = QToolButton(panel)
        self.theme_toggle_button.setObjectName("themeToggleButton")
        self.theme_toggle_button.setFixedSize(HEADER_ACTION_HEIGHT, HEADER_ACTION_HEIGHT)
        self.theme_toggle_button.clicked.connect(self._toggle_theme)
        header_layout.addWidget(self.theme_toggle_button)
        outer_layout.addLayout(header_layout)

        self.status_panel = BBAStatusStrip(panel)
        self.status_panel.add_item("tab", "Tab", self.tabWidget.tabText(self.tabWidget.currentIndex()))
        self.status_panel.add_item("plane", "Plane", self.comboBox_5.currentText())
        self.status_panel.add_item("scan", "Scan", "Idle")
        self.status_panel.add_item("model", "Model", self._model_backend_status_text())
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
        self.pushButton_2.setText("Clear View")
        self.pushButton_3.setText("Recalculate")
        self.pushButton_4.setText("Stop Scan")
        self.pushButton_5.setText("Start Scan")
        self.pushButton_6.setText("Stop Scan")
        self.pushButton_7.setText("Recalculate")
        self.pushButton_8.setText("Clear View")
        clear_view_tooltip = (
            "Clear plots and displayed results. "
            "Does not restore PVs or delete scan points."
        )
        self.pushButton_2.setToolTip(clear_view_tooltip)
        self.pushButton_8.setToolTip(clear_view_tooltip)

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

        self.bba1_preset_combo = QComboBox(self.frame)
        self.bba1_preset_combo.currentIndexChanged.connect(self._apply_selected_bba1_preset)
        form.addWidget(self._make_field_label("Preset", self.frame), 0, 0)
        form.addWidget(self.bba1_preset_combo, 0, 1, 1, 7)

        form.addWidget(self._make_field_label("Plane", self.frame), 1, 0)
        self.comboBox_5.setParent(self.frame)
        self.comboBox_5.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        form.addWidget(self.comboBox_5, 1, 1, 1, 3)

        self.bba1_sample_interval_edit = QLineEdit(self.frame)
        setup_inputs = (
            self.lineEdit,
            self.lineEdit_2,
            self.lineEdit_3,
            self.lineEdit_4,
            self.lineEdit_5,
            self.lineEdit_6,
            self.lineEdit_7,
            self.bba1_sample_interval_edit,
            self.lineEdit_8,
        )
        for widget in setup_inputs:
            widget.setFixedWidth(96)

        rows = [
            ("COR", self.comboBox, "From", self.lineEdit, "To", self.lineEdit_2, "Steps", self.lineEdit_3),
            ("Quad", self.comboBox_2, "From", self.lineEdit_6, "To", self.lineEdit_4, "Steps", self.lineEdit_5),
            ("BPM1", self.comboBox_3, "BPM2", self.comboBox_4, "Settle Time (s)", self.lineEdit_7, "Samples/step", self.lineEdit_8),
            ("Sample Interval (s)", self.bba1_sample_interval_edit, "", None, "", None, "", None),
        ]
        for row, items in enumerate(rows, start=2):
            for col in range(0, len(items), 2):
                text = items[col]
                widget = items[col + 1]
                if not text or widget is None:
                    continue
                form.addWidget(self._make_field_label(text, self.frame), row, col * 2)
                widget.setParent(self.frame)
                widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                form.addWidget(widget, row, col * 2 + 1)

        for column in (1, 3, 5, 7, 9):
            form.setColumnStretch(column, 1)

        layout.addLayout(form)

    def _rebuild_bba1_run_panel(self):
        layout = QVBoxLayout(self.frame_2)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        self.label_13.hide()

        points_header = QHBoxLayout()
        points_header.setContentsMargins(0, 4, 0, 0)
        points_header.setSpacing(6)
        points_title = QLabel("Scan Points", self.frame_2)
        points_title.setObjectName("panelTitle")
        points_header.addWidget(points_title)
        points_header.addStretch(1)
        self.bba1_scan_points_summary_label = QLabel("0 active / 0 total", self.frame_2)
        self.bba1_scan_points_summary_label.setProperty("role", "field")
        points_header.addWidget(self.bba1_scan_points_summary_label)
        layout.addLayout(points_header)

        self.bba1_scan_points_table = QTableWidget(0, len(BBA1_SCAN_POINT_COLUMNS), self.frame_2)
        self.bba1_scan_points_table.setHorizontalHeaderLabels(BBA1_SCAN_POINT_COLUMNS)
        self.bba1_scan_points_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.bba1_scan_points_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.bba1_scan_points_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.bba1_scan_points_table.setMaximumHeight(170)
        self.bba1_scan_points_table.verticalHeader().setVisible(False)
        header = self.bba1_scan_points_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for column in range(1, len(BBA1_SCAN_POINT_COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.Stretch)
        self.bba1_scan_points_table.itemChanged.connect(self._on_bba1_scan_point_item_changed)
        layout.addWidget(self.bba1_scan_points_table)

        point_actions = QHBoxLayout()
        point_actions.setContentsMargins(0, 0, 0, 0)
        point_actions.setSpacing(6)
        self.pushButton_3.setParent(self.frame_2)
        self.bba1_load_points_button = QPushButton("Load Points", self.frame_2)
        self.bba1_exclude_points_button = QPushButton("Exclude Selected", self.frame_2)
        self.bba1_restore_points_button = QPushButton("Use All Points", self.frame_2)
        for button in (
            self.pushButton_3,
            self.bba1_load_points_button,
            self.bba1_exclude_points_button,
            self.bba1_restore_points_button,
        ):
            button.setProperty("compact", True)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            point_actions.addWidget(button)
        self.bba1_load_points_button.clicked.connect(self._load_bba1_scan_archive)
        self.bba1_exclude_points_button.clicked.connect(self._exclude_selected_bba1_scan_points)
        self.bba1_restore_points_button.clicked.connect(self._restore_all_bba1_scan_points)
        layout.addLayout(point_actions)

        layout.addWidget(self._make_panel_title("Run & Readout", self.frame_2))

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(8)
        for button in (self.pushButton, self.pushButton_2, self.pushButton_4):
            button.setParent(self.frame_2)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            controls.addWidget(button)
        layout.addLayout(controls)

        result_row = QGridLayout()
        result_row.setHorizontalSpacing(8)
        result_row.setVerticalSpacing(6)
        result_row.addWidget(self._make_field_label("BPM1 Reading at Quad Center (mm)", self.frame_2), 0, 0)
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
        self.bba2_quad_leff_edit = QLineEdit(self.frame_3)

        form.addWidget(self._make_field_label("Plane", self.frame_3), 0, 0)
        self.comboBox_10.setParent(self.frame_3)
        self.comboBox_10.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        form.addWidget(self.comboBox_10, 0, 1, 1, 3)

        self.bba2_sample_interval_edit = QLineEdit(self.frame_3)
        rows = [
            ("Quad", self.comboBox_7, "From", self.lineEdit_14, "To", self.lineEdit_17, "Steps", self.lineEdit_16),
            ("COR", self.comboBox_9, "From", self.lineEdit_11, "To", self.lineEdit_13, "Steps", self.lineEdit_12),
            ("1st BPM", self.comboBox_8, "2nd BPM", self.comboBox_6, "BPM1 samples", self.lineEdit_22, "", None),
            (
                "Settle Time (s)",
                self.lineEdit_15,
                "Samples/step",
                self.lineEdit_9,
                "Sample Interval (s)",
                self.bba2_sample_interval_edit,
                "",
                None,
            ),
            (
                "Quad Leff (m)",
                self.bba2_quad_leff_edit,
                "",
                None,
                "",
                None,
                "",
                None,
            ),
        ]
        setup_inputs = (
            self.lineEdit_14,
            self.lineEdit_17,
            self.lineEdit_16,
            self.lineEdit_11,
            self.lineEdit_13,
            self.lineEdit_12,
            self.lineEdit_22,
            self.lineEdit_15,
            self.lineEdit_9,
            self.bba2_sample_interval_edit,
            self.bba2_quad_leff_edit,
        )
        for widget in setup_inputs:
            widget.setFixedWidth(96)

        for row, items in enumerate(rows, start=1):
            for col in range(0, len(items), 2):
                text = items[col]
                widget = items[col + 1]
                if not text or widget is None:
                    continue
                form.addWidget(self._make_field_label(text, self.frame_3), row, col * 2)
                widget.setParent(self.frame_3)
                widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                form.addWidget(widget, row, col * 2 + 1)

        for column in (1, 3, 5, 7):
            form.setColumnStretch(column, 1)

        layout.addLayout(form)

        self.bba2_corrector_model_widget = QWidget(self.frame_3)
        model_layout = QHBoxLayout(self.bba2_corrector_model_widget)
        model_layout.setContentsMargins(0, 0, 0, 0)
        model_layout.setSpacing(8)

        model_title = QLabel("Corrector model", self.bba2_corrector_model_widget)
        model_title.setObjectName("panelTitle")
        model_layout.addWidget(model_title)
        self.bba2_corrector_model_summary_label = QLabel("", self.bba2_corrector_model_widget)
        self.bba2_corrector_model_summary_label.setProperty("role", "field")
        self.bba2_corrector_model_summary_label.setWordWrap(True)
        self.bba2_corrector_model_summary_label.setMinimumWidth(0)
        self.bba2_corrector_model_summary_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        model_layout.addWidget(self.bba2_corrector_model_summary_label, 1)
        self.bba2_corrector_model_edit_button = QPushButton("Edit...", self.bba2_corrector_model_widget)
        self.bba2_corrector_model_edit_button.setProperty("compact", True)
        self.bba2_corrector_model_edit_button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.bba2_corrector_model_edit_button.clicked.connect(self._open_bba2_corrector_model_dialog)
        model_layout.addWidget(self.bba2_corrector_model_edit_button)
        layout.addWidget(self.bba2_corrector_model_widget)

        for widget in (
            self.lineEdit_20,
            self.lineEdit_23,
            self.lineEdit_24,
            self.lineEdit_25,
            self.lineEdit_26,
        ):
            widget.setParent(self.frame_3)
            widget.hide()

    def _rebuild_bba2_run_panel(self):
        layout = QVBoxLayout(self.frame_4)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        for label in (self.label_20, self.label_21, self.label_23):
            label.hide()

        points_header = QHBoxLayout()
        points_header.setContentsMargins(0, 4, 0, 0)
        points_header.setSpacing(6)
        points_title = QLabel("Scan Points", self.frame_4)
        points_title.setObjectName("panelTitle")
        points_header.addWidget(points_title)
        self.bba2_scan_points_type_combo = QComboBox(self.frame_4)
        for key, label in BBA2_SCAN_POINT_LABELS.items():
            self.bba2_scan_points_type_combo.addItem(label, key)
        self.bba2_scan_points_type_combo.currentIndexChanged.connect(self._render_bba2_scan_points_table)
        points_header.addWidget(self.bba2_scan_points_type_combo)
        points_header.addStretch(1)
        self.bba2_scan_points_summary_label = QLabel("0 active / 0 total", self.frame_4)
        self.bba2_scan_points_summary_label.setProperty("role", "field")
        points_header.addWidget(self.bba2_scan_points_summary_label)
        layout.addLayout(points_header)

        self.bba2_scan_points_table = QTableWidget(0, 0, self.frame_4)
        self.bba2_scan_points_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.bba2_scan_points_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.bba2_scan_points_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.bba2_scan_points_table.setMaximumHeight(150)
        self.bba2_scan_points_table.verticalHeader().setVisible(False)
        self.bba2_scan_points_table.itemChanged.connect(self._on_bba2_scan_point_item_changed)
        layout.addWidget(self.bba2_scan_points_table)

        point_actions = QHBoxLayout()
        point_actions.setContentsMargins(0, 0, 0, 0)
        point_actions.setSpacing(6)
        self.bba2_load_points_button = QPushButton("Load Points", self.frame_4)
        self.bba2_exclude_points_button = QPushButton("Exclude Selected", self.frame_4)
        self.bba2_restore_points_button = QPushButton("Use All Points", self.frame_4)
        self.pushButton_7.setParent(self.frame_4)
        for button in (
            self.pushButton_7,
            self.bba2_load_points_button,
            self.bba2_exclude_points_button,
            self.bba2_restore_points_button,
        ):
            button.setProperty("compact", True)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            point_actions.addWidget(button)
        self.bba2_load_points_button.clicked.connect(self._load_bba2_scan_archive)
        self.bba2_exclude_points_button.clicked.connect(self._exclude_selected_bba2_scan_points)
        self.bba2_restore_points_button.clicked.connect(self._restore_all_bba2_scan_points)
        layout.addLayout(point_actions)
        self._render_bba2_scan_points_table()

        layout.addWidget(self._make_panel_title("Run & Readout", self.frame_4))

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(8)
        self.comboBox_11.hide()
        for button in (self.pushButton_5, self.pushButton_8, self.pushButton_6):
            button.setParent(self.frame_4)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            controls.addWidget(button)
        layout.addLayout(controls)

        r12_results = QGridLayout()
        r12_results.setHorizontalSpacing(8)
        r12_results.setVerticalSpacing(6)
        self.bba2_model_r12_edit = QLineEdit(self.frame_4)
        self.bba2_model_r12_edit.setReadOnly(True)
        for widget in (self.lineEdit_19, self.bba2_model_r12_edit, self.lineEdit_18):
            widget.setParent(self.frame_4)
            widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        r12_results.addWidget(self._make_field_label("Measured R12 (m)", self.frame_4), 0, 0)
        r12_results.addWidget(self.lineEdit_19, 0, 1)
        r12_results.addWidget(self._make_field_label("Model R12 (m)", self.frame_4), 0, 2)
        r12_results.addWidget(self.bba2_model_r12_edit, 0, 3)
        r12_results.setColumnStretch(1, 1)
        r12_results.setColumnStretch(3, 1)
        layout.addLayout(r12_results)

        bpm_readout = QGridLayout()
        bpm_readout.setHorizontalSpacing(8)
        bpm_readout.setVerticalSpacing(6)
        bpm_readout.addWidget(self._make_field_label("BPM1 Reading at Quad Center (mm)", self.frame_4), 0, 0)
        bpm_readout.addWidget(self.lineEdit_18, 0, 1)
        bpm_readout.setColumnStretch(1, 1)
        layout.addLayout(bpm_readout)
        for hidden_widget in (self.lineEdit_21,):
            hidden_widget.setParent(self.frame_4)
            hidden_widget.hide()
        self._reset_bba2_model_r12_readout()

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
        self._draw_placeholder(self.widget, BBA1_QUAD_X_LABEL, "BPM2 (mm)", "Waiting for BBA-1 scan points")
        self._draw_placeholder(self.widget_2, "BPM1 (mm)", BBA1_SLOPE_LABEL, "Waiting for BBA-1 fit")
        self._draw_placeholder(self.widget_3, BBA2_QUAD_X_LABEL, "BPM2 (mm)", "Waiting for BBA-2 quad scan")
        self._draw_placeholder(self.widget_4, "corrector kick (mrad)", "BPM2 (mm)", "Waiting for BBA-2 corrector scan")

    def _restyle_current_plots(self):
        palette = self._palette()
        plot_specs = (
            (self.widget, BBA1_QUAD_X_LABEL, "BPM2 (mm)"),
            (self.widget_2, "BPM1 (mm)", BBA1_SLOPE_LABEL),
            (self.widget_3, BBA2_QUAD_X_LABEL, "BPM2 (mm)"),
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
    def _scan_sample_interval_default(scan):
        if scan.sample_interval is not None:
            return scan.sample_interval
        if scan.settle_time is not None:
            return scan.settle_time
        return 0.5

    def _refresh_bba2_corrector_model_summary(self):
        label = getattr(self, "bba2_corrector_model_summary_label", None)
        if label is None:
            return
        label.setText(
            "E={energy} MeV | By={by} | Bx={bx} | Leff(By/Bx)={leff_by}/{leff_bx} m".format(
                energy=self.lineEdit_20.text().strip() or "-",
                by=self.lineEdit_23.text().strip() or "-",
                bx=self.lineEdit_24.text().strip() or "-",
                leff_by=self.lineEdit_25.text().strip() or "-",
                leff_bx=self.lineEdit_26.text().strip() or "-",
            )
        )

    def _model_backend_status_text(self):
        return "Ready" if self._model_backend_available else "Unavailable"

    def _model_backend_status_tone(self):
        return "success" if self._model_backend_available else "warning"

    def _model_backend_status_tooltip(self):
        if self._model_backend_available:
            return "Model backend is available for optional BBA-2 Model R12 calculation."
        return f"Model backend unavailable: {self._model_backend_error}"

    def _reset_bba2_model_r12_readout(self):
        if not hasattr(self, "bba2_model_r12_edit"):
            return
        self.bba2_model_r12_edit.setText("")
        if self._model_backend_available:
            self.bba2_model_r12_edit.setToolTip(
                "BBA-2 Model R12 is calculated after the corrector scan."
            )
        else:
            self.bba2_model_r12_edit.setToolTip(self._model_backend_status_tooltip())

    def _open_bba2_corrector_model_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Corrector Model")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        fields = {
            "energy": QLineEdit(self.lineEdit_20.text(), dialog),
            "by": QLineEdit(self.lineEdit_23.text(), dialog),
            "leff_by": QLineEdit(self.lineEdit_25.text(), dialog),
            "bx": QLineEdit(self.lineEdit_24.text(), dialog),
            "leff_bx": QLineEdit(self.lineEdit_26.text(), dialog),
        }

        grid.addWidget(self._make_field_label("Energy@COR (MeV)", dialog), 0, 0)
        grid.addWidget(fields["energy"], 0, 1, 1, 3)
        x_title = QLabel("X Plane", dialog)
        x_title.setObjectName("panelTitle")
        grid.addWidget(x_title, 1, 0, 1, 4)
        grid.addWidget(self._make_field_label("By formula (Gauss)", dialog), 2, 0)
        grid.addWidget(fields["by"], 2, 1)
        grid.addWidget(self._make_field_label("Leff By (m)", dialog), 2, 2)
        grid.addWidget(fields["leff_by"], 2, 3)
        y_title = QLabel("Y Plane", dialog)
        y_title.setObjectName("panelTitle")
        grid.addWidget(y_title, 3, 0, 1, 4)
        grid.addWidget(self._make_field_label("Bx formula (Gauss)", dialog), 4, 0)
        grid.addWidget(fields["bx"], 4, 1)
        grid.addWidget(self._make_field_label("Leff Bx (m)", dialog), 4, 2)
        grid.addWidget(fields["leff_bx"], 4, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        layout.addLayout(grid)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec_() != QDialog.Accepted:
            return

        self.lineEdit_20.setText(fields["energy"].text())
        self.lineEdit_23.setText(fields["by"].text())
        self.lineEdit_25.setText(fields["leff_by"].text())
        self.lineEdit_24.setText(fields["bx"].text())
        self.lineEdit_26.setText(fields["leff_bx"].text())
        self._refresh_bba2_corrector_model_summary()

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

    def _find_bba_preset(self, preset_id):
        return get_bba_preset(self.app_context, preset_id)

    def _bba_presets_for_family(self, family_name):
        return tuple(
            preset for preset in self.bba_workflow.presets
            if preset.family == family_name
        )

    @staticmethod
    def _bba_preset_label(preset):
        return f"{preset.plane}: {preset.quad} + {preset.corr} / {preset.bpm1}->{preset.bpm2}"

    def _set_bba_preset_combo_items(self, combo, presets):
        combo.blockSignals(True)
        combo.clear()
        for preset in presets:
            combo.addItem(self._bba_preset_label(preset), preset.id)
        combo.blockSignals(False)

    @staticmethod
    def _set_bba_preset_combo_current(combo, preset_id):
        for index in range(combo.count()):
            if combo.itemData(index) == preset_id:
                combo.setCurrentIndex(index)
                return

    def _apply_bba1_preset(self, preset):
        self._set_combo_current_plane(self.comboBox_5, preset.plane)
        self._refresh_corrector_combo(
            self.comboBox,
            self._standard_corrector_items(preset.plane),
            preferred=preset.corr,
        )
        self._set_combo_current_text(self.comboBox_2, preset.quad)
        self._set_combo_current_text(self.comboBox_3, preset.bpm1)
        self._set_combo_current_text(self.comboBox_4, preset.bpm2)
        self._apply_typed_defaults(
            preset.scan,
            {
                "corr_from": self.lineEdit,
                "corr_end": self.lineEdit_2,
                "corr_steps": self.lineEdit_3,
                "quad_end": self.lineEdit_4,
                "quad_steps": self.lineEdit_5,
                "quad_from": self.lineEdit_6,
                "settle_time": self.lineEdit_7,
                "samples": self.lineEdit_8,
            },
        )
        self._set_line_edit_value(
            self.bba1_sample_interval_edit,
            self._scan_sample_interval_default(preset.scan),
        )
        self._refresh_status()

    def _apply_selected_bba1_preset(self, *_):
        if not hasattr(self, "bba1_preset_combo") or self.bba1_preset_combo.count() == 0:
            return
        preset_id = self.bba1_preset_combo.currentData()
        if not preset_id:
            return
        try:
            preset = self._find_bba_preset(preset_id)
        except MachineProfileError as exc:
            self._warn(str(exc))
            return
        self._apply_bba1_preset(preset)

    def _family_control_backends(self, family):
        return family.control_backends

    def _require_family_control_backend(self, family, backend, label):
        allowed = self._family_control_backends(family)
        if allowed and backend not in allowed:
            allowed_text = ", ".join(allowed)
            raise ValueError(f"{label} does not allow {backend!r} backend. Allowed backend(s): {allowed_text}.")

    def _configure_family_backend_availability(self):
        backend = self.app_context.control_backend.name
        for family, label, start_button in (
            (self.bba_workflow.bba1, "BBA-1", self.pushButton),
            (self.bba_workflow.bba2, "BBA-2", self.pushButton_5),
        ):
            try:
                self._require_family_control_backend(family, backend, label)
            except ValueError as exc:
                start_button.setEnabled(False)
                start_button.setToolTip(str(exc))

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
        return self._family_element_ids(self.bba_workflow.bba1.quads, kind="quad")

    def _standard_corrector_items(self, plane):
        return self._family_element_ids(
            self.bba_workflow.bba1.correctors,
            kind="corr",
            plane=self._normalize_plane_value(plane).lower(),
        )

    def _standard_bpm1_items(self):
        return self._family_element_ids(self.bba_workflow.bba1.bpm1, kind="bpm")

    def _standard_bpm2_items(self):
        return self._family_element_ids(self.bba_workflow.bba1.bpm2, kind="bpm")

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
        bba1 = self.bba_workflow.bba1
        bba2 = self.bba_workflow.bba2

        self._set_combo_items(self.comboBox_2, self._standard_quad_items())
        self._set_combo_items(self.comboBox_3, self._standard_bpm1_items())
        self._set_combo_items(self.comboBox_4, self._standard_bpm2_items())

        self._set_combo_items(self.comboBox_7, self._bba2_quad_items())
        self._set_combo_items(self.comboBox_8, self._bba2_bpm1_items())
        self._set_combo_items(self.comboBox_6, self._bba2_bpm2_items())
        bba1_default = self._find_bba_preset(bba1.default_preset)
        self._set_bba_preset_combo_items(
            self.bba1_preset_combo,
            self._bba_presets_for_family("bba1"),
        )
        self._set_bba_preset_combo_current(self.bba1_preset_combo, bba1_default.id)
        self._apply_bba1_preset(bba1_default)

        bba2_default = self._find_bba_preset(bba2.default_preset)
        self.bba2_quad_leff = bba2_default.analysis.quad_leff or K1LQ_FACTOR
        self._set_line_edit_value(self.bba2_quad_leff_edit, self.bba2_quad_leff)
        self._set_combo_current_plane(self.comboBox_10, bba2_default.plane)
        self._set_combo_current_text(self.comboBox_7, bba2_default.quad)
        self._refresh_corrector_combo(
            self.comboBox_9,
            self._bba2_corrector_items(bba2_default.plane),
            preferred=bba2_default.corr,
        )
        self._set_combo_current_text(self.comboBox_8, bba2_default.bpm1)
        self._set_combo_current_text(self.comboBox_6, bba2_default.bpm2)
        self._apply_typed_defaults(
            bba2_default.scan,
            {
                "corr_steps": self.lineEdit_12,
                "corr_from": self.lineEdit_11,
                "corr_end": self.lineEdit_13,
                "quad_from": self.lineEdit_14,
                "settle_time": self.lineEdit_15,
                "quad_steps": self.lineEdit_16,
                "quad_end": self.lineEdit_17,
                "samples": self.lineEdit_9,
            },
        )
        self._set_line_edit_value(
            self.bba2_sample_interval_edit,
            self._scan_sample_interval_default(bba2_default.scan),
        )
        self._configure_family_backend_availability()
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
        self._refresh_bba2_corrector_model_summary()
        self._load_latest_bba1_data_into_table()
        self._load_latest_bba2_data_into_table()

    def _profile_default_control_backend(self):
        return self.app_context.control_backend.name

    @staticmethod
    def _bpm_logical_channel(plane):
        return "x" if plane == "X" else "y"

    def _current_plane_text(self):
        return self.comboBox_5.currentText() if self.tabWidget.currentIndex() == 0 else self.comboBox_10.currentText()

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

        self.status_panel.set_item(
            "model",
            self._model_backend_status_text(),
            self._model_backend_status_tone(),
            self._model_backend_status_tooltip(),
        )

    def _warn(self, message):
        print(message)
        QMessageBox.warning(self, "BBA", message)

    def _require_write_allowed(self, operation, mode):
        try:
            require_workflow_write_allowed(self.app_context, "bba", operation, mode=mode)
            return True
        except MachineProfileError as exc:
            self._warn(str(exc))
            return False

    def _bba_runtime_paths(self, mode):
        context = load_app_context(
            "bba",
            machine_id=self.machine_profile.machine.id,
            control_backend=mode,
        )
        return resolve_bba_runtime_paths(context)

    @staticmethod
    def _load_bba_metadata(path):
        try:
            with Path(path).open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _latest_bba1_data_dir(self):
        return self._bba_runtime_paths(self._profile_default_control_backend())["latest_dir"]

    def _latest_bba2_data_dir(self):
        return self._bba_runtime_paths(self._profile_default_control_backend())["latest_dir"]

    def _bba1_scan_points_counts(self):
        table = getattr(self, "bba1_scan_points_table", None)
        if table is None:
            return 0, 0
        total = table.rowCount()
        active = 0
        for row in range(total):
            item = table.item(row, 0)
            if item is not None and item.checkState() == Qt.Checked:
                active += 1
        return active, total

    def _update_bba1_scan_points_summary(self):
        label = getattr(self, "bba1_scan_points_summary_label", None)
        if label is None:
            return
        active, total = self._bba1_scan_points_counts()
        label.setText(f"{active} active / {total} total")

    def _on_bba1_scan_point_item_changed(self, item):
        if item.column() != 0:
            return
        self._update_bba1_scan_points_summary()
        self._redraw_bba1_scan_points_from_table()

    def _clear_bba1_scan_points(self):
        table = getattr(self, "bba1_scan_points_table", None)
        if table is None:
            return
        table.blockSignals(True)
        table.setRowCount(0)
        table.blockSignals(False)
        self._update_bba1_scan_points_summary()

    def _append_bba1_scan_point(self, corr, quad_k1, bpm1, bpm2, *, enabled=True):
        table = getattr(self, "bba1_scan_points_table", None)
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

        values = (corr, quad_k1, bpm1, bpm2)
        display_values = (corr, quad_k1, bpm1 * 1e3, bpm2 * 1e3)
        for column, (value, display_value) in enumerate(zip(values, display_values), start=1):
            numeric_value = float(value)
            data_item = QTableWidgetItem(f"{float(display_value):.6g}")
            data_item.setData(Qt.UserRole, numeric_value)
            data_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            data_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            table.setItem(row, column, data_item)

        table.blockSignals(False)
        self._update_bba1_scan_points_summary()

    def _bba1_scan_point_value(self, row, column):
        item = self.bba1_scan_points_table.item(row, column)
        if item is None:
            raise ValueError(f"BBA-1 scan point row {row + 1} is incomplete.")
        value = item.data(Qt.UserRole)
        if value is None:
            value = item.text()
        return float(value)

    def _enabled_bba1_scan_points(self):
        table = getattr(self, "bba1_scan_points_table", None)
        if table is None:
            return []
        points = []
        for row in range(table.rowCount()):
            use_item = table.item(row, 0)
            if use_item is None or use_item.checkState() != Qt.Checked:
                continue
            points.append((
                self._bba1_scan_point_value(row, 1),
                self._bba1_scan_point_value(row, 2),
                self._bba1_scan_point_value(row, 3),
                self._bba1_scan_point_value(row, 4),
            ))
        return points

    def _load_bba1_scan_points_from_path(self, raw_path):
        raw_path = Path(raw_path)
        if not raw_path.exists():
            raise RuntimeError(f"{raw_path} not found.")
        data = np.loadtxt(raw_path, ndmin=2)
        if data.ndim != 2 or data.shape[1] < 4:
            raise RuntimeError(f"{raw_path.name} must contain corrector, K1, BPM1 and BPM2 columns.")
        self._clear_bba1_scan_points()
        for corr, quad_k1, bpm1, bpm2 in data[:, :4]:
            self._append_bba1_scan_point(corr, quad_k1, bpm1, bpm2)
        self._redraw_bba1_scan_points_from_table()

    def _load_latest_bba1_data_into_table(self):
        if not hasattr(self, "bba1_scan_points_table"):
            return
        source_dir = self._latest_bba1_data_dir()
        raw_path = source_dir / "bba1_quad_scan.txt"
        if not raw_path.exists():
            self._clear_bba1_scan_points()
            return
        try:
            self._load_bba1_scan_points_from_path(raw_path)
        except RuntimeError as exc:
            self._warn(str(exc))

    def _load_bba1_scan_archive(self):
        if self._scan_is_running():
            self._warn("Stop the current BBA scan before loading archived data.")
            return
        archive_dir = self._bba_runtime_paths(self._profile_default_control_backend())["archive_dir"]
        archive_dir.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load BBA-1 Scan Archive",
            str(archive_dir),
            "BBA-1 raw scan data (bba1_quad_scan.txt);;Text files (*.txt);;All files (*)",
        )
        if not path:
            return
        try:
            self._load_bba1_scan_points_from_path(Path(path))
        except RuntimeError as exc:
            self._warn(str(exc))

    def _exclude_selected_bba1_scan_points(self):
        table = getattr(self, "bba1_scan_points_table", None)
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
        self._update_bba1_scan_points_summary()
        self._redraw_bba1_scan_points_from_table()

    def _restore_all_bba1_scan_points(self):
        table = getattr(self, "bba1_scan_points_table", None)
        if table is None:
            return
        table.blockSignals(True)
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item is not None:
                item.setCheckState(Qt.Checked)
        table.blockSignals(False)
        self._update_bba1_scan_points_summary()
        self._redraw_bba1_scan_points_from_table()

    def _redraw_bba1_scan_points_from_table(self):
        table = getattr(self, "bba1_scan_points_table", None)
        if table is None or table.rowCount() == 0:
            return

        active = []
        excluded = []
        for row in range(table.rowCount()):
            point = (
                self._bba1_scan_point_value(row, 1),
                self._bba1_scan_point_value(row, 2),
                self._bba1_scan_point_value(row, 3),
                self._bba1_scan_point_value(row, 4),
            )
            use_item = table.item(row, 0)
            if use_item is not None and use_item.checkState() == Qt.Checked:
                active.append(point)
            else:
                excluded.append(point)

        self._draw_placeholder(self.widget, BBA1_QUAD_X_LABEL, "BPM2 (mm)", "Waiting for BBA-1 scan points")
        palette = self._palette()
        if active:
            data = np.asarray(active, dtype=float)
            self.widget.axes.plot(
                data[:, 1],
                data[:, 3] * 1e3,
                marker="x",
                linestyle="None",
                color=palette["plot_point"],
            )
        if excluded:
            data = np.asarray(excluded, dtype=float)
            self.widget.axes.plot(
                data[:, 1],
                data[:, 3] * 1e3,
                marker="x",
                linestyle="None",
                color=palette["muted_fg"],
                alpha=0.45,
            )
        self.widget.canvas.draw()

    def _current_bba2_point_type(self):
        combo = getattr(self, "bba2_scan_points_type_combo", None)
        if combo is None:
            return "quad"
        return combo.currentData() or "quad"

    def _bba2_point_counts(self, point_type=None):
        point_type = point_type or self._current_bba2_point_type()
        rows = self.bba2_scan_points.get(point_type, [])
        total = len(rows)
        active = sum(1 for row in rows if row.get("enabled", True))
        return active, total

    def _update_bba2_scan_points_summary(self):
        label = getattr(self, "bba2_scan_points_summary_label", None)
        if label is None:
            return
        active, total = self._bba2_point_counts()
        label.setText(f"{active} active / {total} total")

    def _bba2_display_values(self, point_type, values):
        if point_type == "quad":
            return (values[0], values[1] * 1e3)
        if point_type == "bpm1":
            return (values[0] * 1e3,)
        if point_type == "corrector":
            return (values[0], values[1], values[2] * 1e3)
        raise ValueError(f"Unknown BBA-2 point type: {point_type}")

    def _append_bba2_scan_point(self, point_type, values, *, enabled=True, render=True):
        if point_type not in BBA2_SCAN_POINT_COLUMNS:
            return
        self.bba2_scan_points.setdefault(point_type, []).append({
            "enabled": bool(enabled),
            "values": tuple(float(value) for value in values),
        })
        if render and self._current_bba2_point_type() == point_type:
            self._render_bba2_scan_points_table()
        else:
            self._update_bba2_scan_points_summary()

    def _set_bba2_scan_points(self, point_type, rows):
        self.bba2_scan_points[point_type] = [
            {"enabled": bool(enabled), "values": tuple(float(value) for value in values)}
            for values, enabled in rows
        ]
        if self._current_bba2_point_type() == point_type:
            self._render_bba2_scan_points_table()
        else:
            self._update_bba2_scan_points_summary()

    def _clear_bba2_scan_points(self):
        self.bba2_scan_points = {key: [] for key in BBA2_SCAN_POINT_COLUMNS}
        self.bba2_loaded_source_dir = None
        self.bba2_loaded_metadata = None
        self._render_bba2_scan_points_table()

    def _on_bba2_scan_point_item_changed(self, item):
        if item.column() != 0:
            return
        point_type = self._current_bba2_point_type()
        rows = self.bba2_scan_points.get(point_type, [])
        if 0 <= item.row() < len(rows):
            rows[item.row()]["enabled"] = item.checkState() == Qt.Checked
        self._update_bba2_scan_points_summary()
        self._redraw_bba2_scan_points_from_table(point_type)

    def _render_bba2_scan_points_table(self, *_):
        table = getattr(self, "bba2_scan_points_table", None)
        if table is None:
            return
        point_type = self._current_bba2_point_type()
        columns = BBA2_SCAN_POINT_COLUMNS[point_type]
        rows = self.bba2_scan_points.get(point_type, [])

        table.blockSignals(True)
        table.clear()
        table.setColumnCount(len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.setRowCount(0)
        for row_index, row_data in enumerate(rows):
            table.insertRow(row_index)
            use_item = QTableWidgetItem("")
            use_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            use_item.setCheckState(Qt.Checked if row_data.get("enabled", True) else Qt.Unchecked)
            use_item.setTextAlignment(Qt.AlignCenter)
            table.setItem(row_index, 0, use_item)

            values = row_data["values"]
            display_values = self._bba2_display_values(point_type, values)
            for column, (value, display_value) in enumerate(zip(values, display_values), start=1):
                item = QTableWidgetItem(f"{float(display_value):.6g}")
                item.setData(Qt.UserRole, float(value))
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                table.setItem(row_index, column, item)

        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for column in range(1, len(columns)):
            header.setSectionResizeMode(column, QHeaderView.Stretch)
        table.blockSignals(False)
        self._update_bba2_scan_points_summary()
        self._redraw_bba2_scan_points_from_table(point_type)

    def _enabled_bba2_scan_points(self, point_type):
        return [
            tuple(row["values"])
            for row in self.bba2_scan_points.get(point_type, [])
            if row.get("enabled", True)
        ]

    def _load_bba2_scan_points_from_dir(self, source_dir):
        source_dir = Path(source_dir)
        if not source_dir.exists():
            raise RuntimeError(f"{source_dir} not found.")

        metadata = self._load_bba_metadata(source_dir / "metadata.json")
        scan = metadata.get("scan") if isinstance(metadata.get("scan"), dict) else {}

        quad_data = np.loadtxt(source_dir / "bba2_k1Lqm2.txt", ndmin=2)
        if quad_data.ndim != 2 or quad_data.shape[1] < 2:
            raise RuntimeError("bba2_k1Lqm2.txt must contain K1Leff and BPM2 columns.")
        self.bba2_scan_points["quad"] = [
            {"enabled": True, "values": (float(k1_lq), float(bpm2))}
            for k1_lq, bpm2 in quad_data[:, :2]
        ]

        bpm1_data = np.loadtxt(source_dir / "bba2_m1.txt", ndmin=1)
        self.bba2_scan_points["bpm1"] = [
            {"enabled": True, "values": (float(value),)}
            for value in np.asarray(bpm1_data, dtype=float).reshape(-1)
        ]

        corrector_data = np.loadtxt(source_dir / "bba2_thetam2.txt", ndmin=2)
        if corrector_data.ndim != 2 or corrector_data.shape[1] < 2:
            raise RuntimeError("bba2_thetam2.txt must contain theta and BPM2 columns.")
        corr_values = None
        try:
            corr_steps = int(scan.get("corr_steps") or 0)
            samples = int(scan.get("samples") or 1)
            if corr_steps > 0:
                corr_base = np.linspace(float(scan["corr_from"]), float(scan["corr_end"]), corr_steps)
                corr_values = np.repeat(corr_base, samples)
        except (KeyError, TypeError, ValueError):
            corr_values = None
        if corr_values is None or len(corr_values) != len(corrector_data):
            corr_values = np.full(len(corrector_data), np.nan)
        self.bba2_scan_points["corrector"] = [
            {"enabled": True, "values": (float(corr), float(theta), float(bpm2))}
            for corr, (theta, bpm2) in zip(corr_values, corrector_data[:, :2])
        ]

        self.bba2_loaded_source_dir = source_dir
        self.bba2_loaded_metadata = metadata
        self._render_bba2_scan_points_table()
        self._redraw_bba2_scan_points_from_table("quad")
        self._redraw_bba2_scan_points_from_table("corrector")

    def _load_latest_bba2_data_into_table(self):
        if not hasattr(self, "bba2_scan_points_table"):
            return
        source_dir = self._latest_bba2_data_dir()
        required = ("bba2_k1Lqm2.txt", "bba2_m1.txt", "bba2_thetam2.txt")
        if not all((source_dir / name).exists() for name in required):
            self._clear_bba2_scan_points()
            return
        try:
            self._load_bba2_scan_points_from_dir(source_dir)
        except RuntimeError as exc:
            self._warn(str(exc))

    def _load_bba2_scan_archive(self):
        if self._scan_is_running():
            self._warn("Stop the current BBA scan before loading archived data.")
            return
        archive_dir = self._bba_runtime_paths(self._profile_default_control_backend())["archive_dir"]
        archive_dir.mkdir(parents=True, exist_ok=True)
        path = QFileDialog.getExistingDirectory(
            self,
            "Load BBA-2 Scan Archive",
            str(archive_dir),
        )
        if not path:
            return
        try:
            self._load_bba2_scan_points_from_dir(Path(path))
        except RuntimeError as exc:
            self._warn(str(exc))

    def _exclude_selected_bba2_scan_points(self):
        table = getattr(self, "bba2_scan_points_table", None)
        if table is None:
            return
        point_type = self._current_bba2_point_type()
        rows = self.bba2_scan_points.get(point_type, [])
        selected_rows = sorted({index.row() for index in table.selectedIndexes()})
        if not selected_rows:
            return
        for row in selected_rows:
            if 0 <= row < len(rows):
                rows[row]["enabled"] = False
        self._render_bba2_scan_points_table()

    def _restore_all_bba2_scan_points(self):
        point_type = self._current_bba2_point_type()
        for row in self.bba2_scan_points.get(point_type, []):
            row["enabled"] = True
        self._render_bba2_scan_points_table()

    def _redraw_bba2_scan_points_from_table(self, point_type=None):
        point_type = point_type or self._current_bba2_point_type()
        if point_type == "quad":
            self._draw_placeholder(self.widget_3, BBA2_QUAD_X_LABEL, "BPM2 (mm)", "Waiting for BBA-2 quad scan")
            axes = self.widget_3.axes
            x_index, y_index = 0, 1
            canvas = self.widget_3.canvas
        elif point_type == "corrector":
            self._draw_placeholder(self.widget_4, "corrector kick (mrad)", "BPM2 (mm)", "Waiting for BBA-2 corrector scan")
            axes = self.widget_4.axes
            x_index, y_index = 1, 2
            canvas = self.widget_4.canvas
        else:
            return

        palette = self._palette()
        active = []
        excluded = []
        for row in self.bba2_scan_points.get(point_type, []):
            if row.get("enabled", True):
                active.append(row["values"])
            else:
                excluded.append(row["values"])

        if active:
            data = np.asarray(active, dtype=float)
            x_values = data[:, x_index]
            if point_type == "corrector":
                x_values = x_values * 1e3
            axes.plot(
                x_values,
                data[:, y_index] * 1e3,
                marker="x",
                linestyle="None",
                color=palette["plot_point"],
            )
        if excluded:
            data = np.asarray(excluded, dtype=float)
            x_values = data[:, x_index]
            if point_type == "corrector":
                x_values = x_values * 1e3
            axes.plot(
                x_values,
                data[:, y_index] * 1e3,
                marker="x",
                linestyle="None",
                color=palette["muted_fg"],
                alpha=0.45,
            )
        canvas.draw()

    def _apply_runtime_paths(self, params, family):
        paths = self._bba_runtime_paths(params.control_backend)
        params.bba1_data_path = paths["bba1_data_path"]
        params.bba1_quad_scan_path = paths["bba1_quad_scan_path"]
        params.bba1_metadata_path = paths["bba1_metadata_path"]
        params.bba2_quad_scan_path = paths["bba2_quad_scan_path"]
        params.bba2_bpm1_path = paths["bba2_bpm1_path"]
        params.bba2_corrector_scan_path = paths["bba2_corrector_scan_path"]
        params.bba2_metadata_path = paths["bba2_metadata_path"]
        params.latest_metadata_path = paths["latest_metadata_path"]
        params.bba1_source_dir = paths["latest_dir"]
        if not params.recal:
            context = load_app_context(
                "bba",
                machine_id=self.machine_profile.machine.id,
                control_backend=params.control_backend,
            )
            params.archive_dir = new_bba_scan_archive_dir(context, family)

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

    def _build_bba2_model_snapshot_metadata(self, params):
        snapshot = build_model_snapshot(
            self.app_context,
            ((params.quad, "K1"),),
        )
        return snapshot.as_metadata()

    def _prepare_bba2_model_snapshot(self, params):
        try:
            metadata = self._build_bba2_model_snapshot_metadata(params)
        except MachineProfileError as exc:
            params.model_snapshot_metadata = None
            params.model_lattice_overrides = None
            params.model_snapshot_error = str(exc)
            return
        params.model_snapshot_metadata = metadata
        params.model_lattice_overrides = model_snapshot_lattice_overrides(metadata)
        params.model_snapshot_error = None

    def get_setting(self):
        try:
            params = ScanParameters()
            params.corr = self.comboBox.currentText()
            params.quad = self.comboBox_2.currentText()
            params.bpm1 = self.comboBox_3.currentText()
            params.bpm2 = self.comboBox_4.currentText()
            params.plane = self._normalize_plane_value(self.comboBox_5.currentText())

            mode = self._profile_default_control_backend()
            self._require_family_control_backend(self.bba_workflow.bba1, mode, "BBA-1")
            bpm_channel = self._bpm_logical_channel(params.plane)
            params.corrPV = resolve_corrector_write_channel(self.app_context, params.corr, mode)
            params.quadPV = resolve_channel(self.app_context, params.quad, "k1", mode)
            params.bpm1PV = resolve_channel(self.app_context, params.bpm1, bpm_channel, mode)
            params.bpm2PV = resolve_channel(self.app_context, params.bpm2, bpm_channel, mode)
            params.control_backend = mode
            params.app_context = self.app_context

            params.corr_from = float(self.lineEdit.text())
            params.corr_end = float(self.lineEdit_2.text())
            params.corr_steps = int(self.lineEdit_3.text())
            params.quad_from = float(self.lineEdit_6.text())
            params.quad_end = float(self.lineEdit_4.text())
            params.quad_steps = int(self.lineEdit_5.text())
            params.samples = int(self.lineEdit_8.text())
            params.settle_time = float(self.lineEdit_7.text())
            params.sample_interval = float(self.bba1_sample_interval_edit.text())

            self._validate_positive_int(params.corr_steps, "Corrector steps")
            self._validate_positive_int(params.quad_steps, "Quad steps")
            self._validate_positive_int(params.samples, "Samples per step")
            self._validate_non_negative_float(params.settle_time, "Settle time")
            self._validate_non_negative_float(params.sample_interval, "Sample interval")
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
            params.control_backend = self._profile_default_control_backend()
            self._require_family_control_backend(self.bba_workflow.bba2, params.control_backend, "BBA-2")
            bpm_channel = self._bpm_logical_channel(params.plane)
            params.quadPV = resolve_channel(self.app_context, params.quad, "k1", params.control_backend)
            params.corrPV = resolve_corrector_write_channel(
                self.app_context,
                params.corr,
                params.control_backend,
            )
            params.bpm1PV = resolve_channel(self.app_context, params.bpm1, bpm_channel, params.control_backend)
            params.bpm2PV = resolve_channel(self.app_context, params.bpm2, bpm_channel, params.control_backend)

            params.quad_from = float(self.lineEdit_14.text())
            params.quad_end = float(self.lineEdit_17.text())
            params.quad_steps = int(self.lineEdit_16.text())
            params.corr_from = float(self.lineEdit_11.text())
            params.corr_end = float(self.lineEdit_13.text())
            params.corr_steps = int(self.lineEdit_12.text())
            params.samples = int(self.lineEdit_9.text())
            params.settle_time = float(self.lineEdit_15.text())
            params.sample_interval = float(self.bba2_sample_interval_edit.text())
            params.energy_mev = float(self.lineEdit_20.text())
            params.bpm1_samples = int(self.lineEdit_22.text())
            params.by_formula = self.lineEdit_23.text()
            params.bx_formula = self.lineEdit_24.text()
            params.leff_by = float(self.lineEdit_25.text())
            params.leff_bx = float(self.lineEdit_26.text())
            params.quad_leff = float(self.bba2_quad_leff_edit.text())
            params.app_context = self.app_context

            self._validate_positive_int(params.quad_steps, "Quad steps")
            self._validate_positive_int(params.corr_steps, "Corrector steps")
            self._validate_positive_int(params.samples, "Samples per step")
            self._validate_positive_int(params.bpm1_samples, "BPM1 sample count")
            self._validate_non_negative_float(params.settle_time, "Settle time")
            self._validate_non_negative_float(params.sample_interval, "Sample interval")
            if params.energy_mev <= 0:
                raise ValueError("Energy must be positive.")
            if params.quad_leff <= 0:
                raise ValueError("BBA-2 quad effective length must be positive.")
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

        if not self._require_write_allowed("BBA-1 scan", params.control_backend):
            return

        self.clearPlot()
        self._clear_bba1_scan_points()
        params.recal = False
        self._apply_runtime_paths(params, "bba1")
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

        if not self._require_write_allowed("BBA-2 scan", params.control_backend):
            return

        self.clearPlot_bba2()
        self._clear_bba2_scan_points()
        params.recal = False
        self._apply_runtime_paths(params, "bba2")
        self._prepare_bba2_model_snapshot(params)
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
        self._apply_runtime_paths(params, "bba1")
        source_dir = self._latest_bba1_data_dir()
        params.bba1_source_dir = source_dir
        params.bba1_data_path = source_dir / "m1S.txt"
        params.bba1_quad_scan_path = source_dir / "bba1_quad_scan.txt"
        params.bba1_metadata_path = source_dir / "metadata.json"
        if hasattr(self, "bba1_scan_points_table"):
            if self.bba1_scan_points_table.rowCount() == 0:
                self._load_latest_bba1_data_into_table()
            if self.bba1_scan_points_table.rowCount() > 0:
                points = self._enabled_bba1_scan_points()
                if len(points) < 2:
                    self._warn("At least 2 active BBA-1 scan points are required for recalculation.")
                    return
                params.bba1_recal_points = points
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
        self._apply_runtime_paths(params, "bba2")
        if hasattr(self, "bba2_scan_points_table"):
            if all(len(rows) == 0 for rows in self.bba2_scan_points.values()):
                self._load_latest_bba2_data_into_table()
            source_dir = self.bba2_loaded_source_dir
            if source_dir is not None:
                params.bba2_metadata_path = Path(source_dir) / "metadata.json"
            quad_points = self._enabled_bba2_scan_points("quad")
            bpm1_points = self._enabled_bba2_scan_points("bpm1")
            corrector_points = self._enabled_bba2_scan_points("corrector")
            if len(quad_points) < 2:
                self._warn("At least 2 active BBA-2 quad scan points are required for recalculation.")
                return
            if len(bpm1_points) < 1:
                self._warn("At least 1 active BBA-2 BPM1 sample is required for recalculation.")
                return
            if len(corrector_points) < 2:
                self._warn("At least 2 active BBA-2 COR scan points are required for recalculation.")
                return
            params.bba2_recal_quad_points = [(k1_lq, bpm2) for k1_lq, bpm2 in quad_points]
            params.bba2_recal_bpm1_points = [bpm1 for (bpm1,) in bpm1_points]
            params.bba2_recal_corrector_points = [
                (theta, bpm2)
                for _corr, theta, bpm2 in corrector_points
            ]
            params.samples = 1
        metadata = self.bba2_loaded_metadata
        if not isinstance(metadata, Mapping):
            metadata = self._load_bba_metadata(params.bba2_metadata_path)
        archived_snapshot = metadata.get("model_snapshot") if isinstance(metadata, Mapping) else None
        if isinstance(archived_snapshot, Mapping):
            archived_overrides = model_snapshot_lattice_overrides(archived_snapshot)
            params.model_snapshot_metadata = dict(archived_snapshot)
            params.model_lattice_overrides = archived_overrides
            params.model_snapshot_error = None
            if archived_overrides is None:
                self._warn(
                    "BBA-2 archive model_snapshot has no usable lattice overrides; "
                    "recalculation will use the current model snapshot."
                )
                self._prepare_bba2_model_snapshot(params)
        else:
            self._warn(
                "BBA-2 archive has no model_snapshot; recalculation will use the current model snapshot."
            )
            self._prepare_bba2_model_snapshot(params)
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
            self._draw_placeholder(self.widget, BBA1_QUAD_X_LABEL, "BPM2 (mm)", "Waiting for BBA-1 scan points")
            self._draw_placeholder(self.widget_2, "BPM1 (mm)", BBA1_SLOPE_LABEL, "Waiting for BBA-1 fit")
            self.lineEdit_10.setText("")
            if data.get("clear_points"):
                self._clear_bba1_scan_points()
            self._refresh_status()
            return

        palette = self._palette()
        show_type = data.get("show")
        if show_type == "scan_point":
            self._append_bba1_scan_point(
                data["corr"],
                data["quad_k1"],
                data["bpm1"],
                data["bpm2"],
            )
            self._refresh_status()
            return
        if show_type == "k1m2":
            if not self.widget.axes.lines:
                self.widget.axes.clear()
                self._style_axes(self.widget, BBA1_QUAD_X_LABEL, "BPM2 (mm)")
            self.widget.axes.plot(
                data["quad_k1"],
                np.asarray(data["m2"]) * 1e3,
                marker="x",
                linestyle="None",
                color=palette["plot_point"],
            )
            self.widget.canvas.draw()
        elif show_type == "fit_k1m2":
            self.widget.axes.plot(data["x"], np.asarray(data["y"]) * 1e3, linestyle="--", color=palette["plot_fit"])
            self.widget.canvas.draw()

            m1 = data["m1"]
            slope = data["slope_k1"]
            mm1 = data["mm1"]
            self.widget_2.axes.clear()
            self._style_axes(self.widget_2, "BPM1 (mm)", BBA1_SLOPE_LABEL)
            self.widget_2.axes.plot(
                np.asarray(mm1) * 1e3,
                np.ones(len(mm1)) * slope,
                marker="x",
                linestyle="None",
                color=palette["plot_point"],
            )
            self.widget_2.axes.plot(m1 * 1e3, slope, marker="o", linestyle="None", color=palette["plot_fit"])
            self.widget_2.canvas.draw()
        elif show_type == "m1S":
            self.widget_2.axes.clear()
            self._style_axes(self.widget_2, "BPM1 (mm)", BBA1_SLOPE_LABEL)
            m1_mm = np.asarray(data["m1"]) * 1e3
            self.widget_2.axes.plot(m1_mm, data["slope_k1"], marker="o", linestyle="None", color=palette["plot_point"])
            self.widget_2.axes.plot(m1_mm, data["yvals"], linestyle="-", color=palette["plot_fit"])
            self.widget_2.canvas.draw()
            self.lineEdit_10.setText(f"{data['offset'] * 1e3:.4f}")
        self._refresh_status()

    def display_bba2(self, data):
        if "error" in data:
            self._warn(data["error"])
            return

        if "clear" in data:
            self._draw_placeholder(self.widget_3, BBA2_QUAD_X_LABEL, "BPM2 (mm)", "Waiting for BBA-2 quad scan")
            self._draw_placeholder(self.widget_4, "corrector kick (mrad)", "BPM2 (mm)", "Waiting for BBA-2 corrector scan")
            self.lineEdit_18.setText("")
            self.lineEdit_19.setText("")
            self.lineEdit_19.setToolTip("")
            self.lineEdit_21.setText("")
            self._reset_bba2_model_r12_readout()
            if data.get("clear_points"):
                self._clear_bba2_scan_points()
            self._refresh_status()
            return

        palette = self._palette()
        show_type = data.get("show")
        if show_type == "k1m2":
            if self.scan_mode == "scan":
                for k1_lq, bpm2 in zip(np.atleast_1d(data["K1Lq"]), np.atleast_1d(data["m2"])):
                    self._append_bba2_scan_point("quad", (float(k1_lq), float(bpm2)), render=False)
                self._update_bba2_scan_points_summary()
            if not self.widget_3.axes.lines:
                self.widget_3.axes.clear()
                self._style_axes(self.widget_3, BBA2_QUAD_X_LABEL, "BPM2 (mm)")
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
        elif show_type == "bpm1_sample":
            if self.scan_mode == "scan":
                for bpm1 in np.atleast_1d(data["bpm1"]):
                    self._append_bba2_scan_point("bpm1", (float(bpm1),), render=False)
                self._update_bba2_scan_points_summary()
        elif show_type == "thetam2":
            if self.scan_mode == "scan":
                corr_values = np.atleast_1d(data.get("corr", np.nan))
                theta_values = np.atleast_1d(data["theta"])
                bpm2_values = np.atleast_1d(data["m2"])
                if len(corr_values) == 1 and len(theta_values) > 1:
                    corr_values = np.repeat(corr_values[0], len(theta_values))
                for corr, theta, bpm2 in zip(corr_values, theta_values, bpm2_values):
                    self._append_bba2_scan_point(
                        "corrector",
                        (float(corr), float(theta), float(bpm2)),
                        render=False,
                    )
                self._update_bba2_scan_points_summary()
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
            self.lineEdit_19.setToolTip(f"Measured R12: {data['R12']:.6g} m")
            model_r12 = data.get("model_R12")
            if model_r12 is not None:
                self.bba2_model_r12_edit.setText(str(model_r12))
                self.bba2_model_r12_edit.setToolTip(f"Model R12: {model_r12:.6g} m")
            else:
                self._reset_bba2_model_r12_readout()
            model_r12_error = data.get("model_R12_error")
            if model_r12_error:
                self.bba2_model_r12_edit.setToolTip(f"Model R12 unavailable: {model_r12_error}")
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

    def _require_path(self, path, label):
        if path is None:
            raise RuntimeError(f"{label} runtime path is not configured.")
        return Path(path)

    def _save_array(self, path, data, *, archive_dir=None, archive_name=None, header=""):
        target = self._require_path(path, "BBA data")
        target.parent.mkdir(parents=True, exist_ok=True)
        np.savetxt(target, data, fmt="%.6e", header=header)
        if archive_dir is None:
            return
        archive_path = Path(archive_dir) / (archive_name or target.name)
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        np.savetxt(archive_path, data, fmt="%.6e", header=header)

    def _save_json(self, path, data, *, archive_dir=None, archive_name=None):
        target = self._require_path(path, "BBA metadata")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        if archive_dir is None:
            return
        archive_path = Path(archive_dir) / (archive_name or target.name)
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with archive_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")

    def _load_json(self, path):
        target = self._require_path(path, "BBA metadata")
        if not target.exists():
            return {}
        with target.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}


class BBAScanThread(BBABaseThread):
    def __init__(self, params):
        super().__init__()
        self.params = params

    def run(self):
        try:
            if self.params.recal:
                self._emit({"clear": True})
                if self.params.bba1_recal_points is not None:
                    recalculated = self._recalculate_from_points(self.params.bba1_recal_points)
                else:
                    recalculated = self._recalculate_from_quad_scan()
                if recalculated is None:
                    data_path = self._require_path(self.params.bba1_data_path, "BBA-1 recalculation data")
                    x, y = self._load_two_column(data_path, "BBA-1 recalculation data")
                else:
                    x, y = recalculated
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
                "slope_k1": y,
                "yvals": fit(x),
                "offset": -coeff[1] / coeff[0],
            })
        except Exception as exc:
            self._emit({"error": str(exc)})

    def _recalculate_from_quad_scan(self):
        quad_scan_path = self._require_path(self.params.bba1_quad_scan_path, "BBA-1 quad scan data")
        if not quad_scan_path.exists():
            return None

        data = np.loadtxt(quad_scan_path, ndmin=2)
        if data.shape[1] < 4:
            raise RuntimeError(f"BBA-1 quad scan data is malformed: {quad_scan_path}")
        return self._recalculate_from_points(data[:, :4])

    def _recalculate_from_points(self, points):
        data = np.asarray(points, dtype=float)
        if data.ndim != 2 or data.shape[1] < 4:
            raise RuntimeError("BBA-1 scan points must contain corrector, K1, BPM1 and BPM2 columns.")
        kick_values = np.asarray(data[:, 0], dtype=float)
        quad_k1_values = np.asarray(data[:, 1], dtype=float)
        bpm1_values = np.asarray(data[:, 2], dtype=float)
        bpm2_values = np.asarray(data[:, 3], dtype=float)
        self._emit({
            "show": "k1m2",
            "quad_k1": quad_k1_values,
            "m2": bpm2_values,
        })

        m1_results = []
        slope_results = []
        for kick in self._ordered_unique(kick_values):
            kick_mask = kick_values == kick
            group_quad_k1 = quad_k1_values[kick_mask]
            group_bpm1 = bpm1_values[kick_mask]
            group_bpm2 = bpm2_values[kick_mask]

            quad_means = []
            bpm2_means = []
            for quad_k1 in self._ordered_unique(group_quad_k1):
                quad_mask = group_quad_k1 == quad_k1
                quad_means.append(float(np.mean(group_quad_k1[quad_mask])))
                bpm2_means.append(float(np.mean(group_bpm2[quad_mask])))

            if len(quad_means) < 2:
                raise RuntimeError("Need at least two K1 points per corrector setting to recalculate BBA-1.")

            quad_means = np.asarray(quad_means, dtype=float)
            bpm2_means = np.asarray(bpm2_means, dtype=float)
            coeff = np.polyfit(quad_means, bpm2_means, deg=1)
            fit = np.poly1d(coeff)

            bpm1_mean = float(np.mean(group_bpm1))
            slope = float(coeff[0])
            m1_results.append(bpm1_mean)
            slope_results.append(slope)
            self._emit({
                "show": "fit_k1m2",
                "x": quad_means,
                "y": fit(quad_means),
                "m1": bpm1_mean,
                "slope_k1": slope,
                "mm1": group_bpm1,
            })

        return np.asarray(m1_results, dtype=float), np.asarray(slope_results, dtype=float)

    @staticmethod
    def _ordered_unique(values):
        unique = []
        for value in values:
            item = float(value)
            if item not in unique:
                unique.append(item)
        return unique

    def _metadata(self):
        return {
            "family": "bba1",
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "machine": getattr(getattr(self.params.app_context, "machine", None), "id", None),
            "backend": self.params.control_backend,
            "plane": self.params.plane,
            "corr": self.params.corr,
            "quad": self.params.quad,
            "bpm1": self.params.bpm1,
            "bpm2": self.params.bpm2,
            "scan": {
                "corr_from": self.params.corr_from,
                "corr_end": self.params.corr_end,
                "corr_steps": self.params.corr_steps,
                "quad_from": self.params.quad_from,
                "quad_end": self.params.quad_end,
                "quad_steps": self.params.quad_steps,
                "samples": self.params.samples,
                "settle_time": self.params.settle_time,
                "sample_interval": self.params.sample_interval,
            },
            "files": {
                "raw": "bba1_quad_scan.txt",
                "summary": "m1S.txt",
            },
        }

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
        quad_scan_rows = []

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
                    if not self._sleep_or_stop(self.params.settle_time):
                        return None

                    for sample_index in range(self.params.samples):
                        print("cor-kick,K1=", kick, k1)
                        if sample_index > 0 and not self._sleep_or_stop(self.params.sample_interval):
                            return None

                        bpm2_value = self._safe_get(bpm2, self.params.bpm2PV)
                        bpm1_value = self._safe_get(bpm1, self.params.bpm1PV)
                        bpm2_samples.append(bpm2_value)
                        bpm1_samples.append(bpm1_value)
                        quad_k1 = k1 * sign
                        quad_scan_rows.append((kick, quad_k1, bpm1_value, bpm2_value))

                        self._emit({
                            "show": "scan_point",
                            "corr": kick,
                            "quad_k1": quad_k1,
                            "bpm1": bpm1_value,
                            "bpm2": bpm2_value,
                        })
                        self._emit({
                            "show": "k1m2",
                            "quad_k1": quad_k1,
                            "m2": bpm2_value,
                        })

                bpm2_matrix = np.asarray(bpm2_samples, dtype=float).reshape(self.params.quad_steps, self.params.samples)
                bpm2_mean = np.mean(bpm2_matrix, axis=1)
                bpm1_mean = float(np.mean(np.asarray(bpm1_samples, dtype=float)))

                x = sign * k1_values
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
                    "slope_k1": slope,
                    "mm1": np.asarray(bpm1_samples, dtype=float),
                })
                if not self._sleep_or_stop(1):
                    return None

            self._save_array(
                self.params.bba1_data_path,
                np.column_stack((m1_results, slope_results)),
                archive_dir=self.params.archive_dir,
                header="bpm1_mean_m slope_dBPM2_dK1",
            )
            self._save_array(
                self.params.bba1_quad_scan_path,
                np.asarray(quad_scan_rows, dtype=float),
                archive_dir=self.params.archive_dir,
                header="corrector_setpoint quad_k1 bpm1_m bpm2_m",
            )
            self._save_json(
                self.params.bba1_metadata_path,
                self._metadata(),
                archive_dir=self.params.archive_dir,
            )
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
            if self.params.recal:
                self._apply_recal_metadata()
                if (
                    self.params.bba2_recal_quad_points is not None
                    or self.params.bba2_recal_corrector_points is not None
                ):
                    self.params.samples = 1

            cor = epics.PV(self.params.corrPV)
            quad = epics.PV(self.params.quadPV)
            bpm1 = epics.PV(self.params.bpm1PV)
            bpm2 = epics.PV(self.params.bpm2PV)
            print(cor, quad, bpm1, bpm2)
            self._try_build_model_backend()

            sign = -1 if self.params.plane == "X" else 1
            kick_values = np.linspace(self.params.corr_from, self.params.corr_end, self.params.corr_steps)
            angle_values = self._calculate_kick_angles(kick_values)
            baseline_bpm1_values = None

            if not self.params.recal:
                baseline_bpm1_values = self._measure_bpm1(bpm1)
                if baseline_bpm1_values is None:
                    return

            if self.params.recal:
                if self.params.bba2_recal_quad_points is not None:
                    recal_quad = np.asarray(self.params.bba2_recal_quad_points, dtype=float)
                    k1_lq, quad_m2 = recal_quad[:, 0], recal_quad[:, 1]
                else:
                    quad_scan_path = self._require_path(
                        self.params.bba2_quad_scan_path,
                        "BBA-2 quad scan data",
                    )
                    k1_lq, quad_m2 = self._load_two_column(quad_scan_path, "BBA-2 quad scan data")
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
            # _perform_quad_scan restores the quad in its finally block; wait for the
            # backend readbacks to reflect the restored lattice before the corrector scan.
            post_quad_wait = self.params.settle_time if not self.params.recal else 1
            if not self._sleep_or_stop(post_quad_wait):
                return

            if self.params.recal:
                if self.params.bba2_recal_bpm1_points is not None:
                    bpm1_values = np.asarray(self.params.bba2_recal_bpm1_points, dtype=float)
                else:
                    bpm1_path = self._require_path(self.params.bba2_bpm1_path, "BBA-2 BPM1 data")
                    bpm1_values = self._load_one_column(bpm1_path, "BBA-2 BPM1 data")
            else:
                bpm1_values = baseline_bpm1_values
            self.m1_ave = float(np.mean(bpm1_values))
            print("m1_ave=", self.m1_ave * 1e3, "mm")

            if self.params.recal:
                if self.params.bba2_recal_corrector_points is not None:
                    recal_corrector = np.asarray(self.params.bba2_recal_corrector_points, dtype=float)
                    theta, corr_m2 = recal_corrector[:, 0], recal_corrector[:, 1]
                else:
                    corrector_scan_path = self._require_path(
                        self.params.bba2_corrector_scan_path,
                        "BBA-2 corrector scan data",
                    )
                    theta, corr_m2 = self._load_two_column(corrector_scan_path, "BBA-2 corrector scan data")
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
            if not self.params.recal:
                metadata = self._metadata()
                self._save_json(
                    self.params.bba2_metadata_path,
                    metadata,
                    archive_dir=self.params.archive_dir,
                    archive_name="metadata.json",
                )
                self._save_json(self.params.latest_metadata_path, metadata)
        except Exception as exc:
            self._emit({"error": str(exc)})

    def _apply_recal_metadata(self):
        metadata = self._load_json(self.params.bba2_metadata_path)
        if metadata.get("family") != "bba2":
            return

        self.params.plane = str(metadata.get("plane") or self.params.plane)
        self.params.quad = str(metadata.get("quad") or self.params.quad)
        self.params.corr = str(metadata.get("corr") or self.params.corr)
        self.params.bpm1 = str(metadata.get("bpm1") or self.params.bpm1)
        self.params.bpm2 = str(metadata.get("bpm2") or self.params.bpm2)

        scan = metadata.get("scan") if isinstance(metadata.get("scan"), dict) else {}
        analysis = metadata.get("analysis") if isinstance(metadata.get("analysis"), dict) else {}
        initial = metadata.get("initial") if isinstance(metadata.get("initial"), dict) else {}

        self.params.samples = int(scan.get("samples") or self.params.samples)
        self.params.settle_time = float(scan.get("settle_time") or self.params.settle_time)
        self.params.sample_interval = float(
            scan.get("sample_interval")
            if scan.get("sample_interval") is not None
            else self.params.sample_interval
        )
        self.params.bpm1_samples = int(analysis.get("bpm1_samples") or self.params.bpm1_samples)
        self.params.energy_mev = float(analysis.get("energy_mev") or self.params.energy_mev)
        self.params.by_formula = str(analysis.get("by_formula") or self.params.by_formula)
        self.params.bx_formula = str(analysis.get("bx_formula") or self.params.bx_formula)
        self.params.leff_by = float(analysis.get("leff_by") or self.params.leff_by)
        self.params.leff_bx = float(analysis.get("leff_bx") or self.params.leff_bx)
        self.params.quad_leff = float(analysis.get("quad_leff") or self.params.quad_leff)
        if initial.get("quad_k1") is not None:
            self.initial_quad_k1 = float(initial["quad_k1"])
        model_snapshot = metadata.get("model_snapshot")
        if isinstance(model_snapshot, Mapping):
            self.params.model_snapshot_metadata = dict(model_snapshot)
            self.params.model_lattice_overrides = model_snapshot_lattice_overrides(model_snapshot)
            self.params.model_snapshot_error = None
        elif not self.params.model_snapshot_metadata:
            model_snapshot_error = metadata.get("model_snapshot_error")
            if isinstance(model_snapshot_error, str) and model_snapshot_error:
                self.params.model_snapshot_error = model_snapshot_error

    def _metadata(self):
        metadata = {
            "family": "bba2",
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "machine": getattr(getattr(self.params.app_context, "machine", None), "id", None),
            "backend": self.params.control_backend,
            "plane": self.params.plane,
            "quad": self.params.quad,
            "corr": self.params.corr,
            "bpm1": self.params.bpm1,
            "bpm2": self.params.bpm2,
            "scan": {
                "quad_from": self.params.quad_from,
                "quad_end": self.params.quad_end,
                "quad_steps": self.params.quad_steps,
                "corr_from": self.params.corr_from,
                "corr_end": self.params.corr_end,
                "corr_steps": self.params.corr_steps,
                "samples": self.params.samples,
                "settle_time": self.params.settle_time,
                "sample_interval": self.params.sample_interval,
            },
            "analysis": {
                "energy_mev": self.params.energy_mev,
                "bpm1_samples": self.params.bpm1_samples,
                "by_formula": self.params.by_formula,
                "bx_formula": self.params.bx_formula,
                "leff_by": self.params.leff_by,
                "leff_bx": self.params.leff_bx,
                "quad_leff": self.params.quad_leff,
                "S": self.S,
                "R12": self.R12,
                "model_R12": self.model_r12,
                "model_R12_error": self.model_r12_error,
                "bpm1_reading_at_quad_center_m": (
                    self.m1_ave - self.S / self.R12
                    if self.S is not None and self.R12 is not None and self.m1_ave is not None
                    else None
                ),
                "offset_quad_center_minus_bpm1_m": (
                    self.S / self.R12 - self.m1_ave
                    if self.S is not None and self.R12 is not None and self.m1_ave is not None
                    else None
                ),
            },
            "initial": {
                "quad_k1": self.initial_quad_k1,
            },
            "files": {
                "quad_scan": "bba2_k1Lqm2.txt",
                "bpm1": "bba2_m1.txt",
                "corrector_scan": "bba2_thetam2.txt",
            },
        }
        if isinstance(self.params.model_snapshot_metadata, Mapping):
            metadata["model_snapshot"] = dict(self.params.model_snapshot_metadata)
        if self.params.model_snapshot_error:
            metadata["model_snapshot_error"] = self.params.model_snapshot_error
        return metadata

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
                if not self._sleep_or_stop(self.params.settle_time):
                    return None

                for sample_index in range(self.params.samples):
                    if sample_index > 0 and not self._sleep_or_stop(self.params.sample_interval):
                        return None
                    bpm2_value = self._safe_get(bpm2, self.params.bpm2PV)
                    print("K1=", k1, "bpm2=", bpm2_value)
                    bpm2_samples.append(bpm2_value)
                    k1_samples.append(k1)

                    self._emit({
                        "show": "k1m2",
                        "K1Lq": k1 * sign * self.params.quad_leff,
                        "m2": bpm2_value,
                    })

            k1_lq = sign * np.asarray(k1_samples, dtype=float) * self.params.quad_leff
            m2 = np.asarray(bpm2_samples, dtype=float)
            self._save_array(
                self.params.bba2_quad_scan_path,
                np.column_stack((k1_lq, m2)),
                archive_dir=self.params.archive_dir,
            )
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
        for sample_index in range(self.params.bpm1_samples):
            if not self.is_running:
                print("BPM1 scan stop.")
                return None
            if sample_index > 0 and not self._sleep_or_stop(self.params.sample_interval):
                return None
            value = self._safe_get(bpm1, self.params.bpm1PV)
            samples.append(value)
            print("BPM1 m1=", value * 1e3, "mm")
            self._emit({
                "show": "bpm1_sample",
                "bpm1": value,
            })
        self._save_array(
            self.params.bba2_bpm1_path,
            np.asarray(samples, dtype=float),
            archive_dir=self.params.archive_dir,
        )
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
                if not self._sleep_or_stop(self.params.settle_time):
                    return None
                for sample_index in range(self.params.samples):
                    if sample_index > 0 and not self._sleep_or_stop(self.params.sample_interval):
                        return None
                    bpm2_value = self._safe_get(bpm2, self.params.bpm2PV)
                    print("corrector=", kick, "bpm2=", bpm2_value)
                    bpm2_samples.append(bpm2_value)
                    theta_samples.append(angle_values[idx])

                    self._emit({
                        "show": "thetam2",
                        "corr": kick,
                        "theta": angle_values[idx],
                        "m2": bpm2_value,
                    })

            theta = np.asarray(theta_samples, dtype=float)
            m2 = np.asarray(bpm2_samples, dtype=float)
            self._save_array(
                self.params.bba2_corrector_scan_path,
                np.column_stack((theta, m2)),
                archive_dir=self.params.archive_dir,
            )
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
            raise RuntimeError("BBA-2 corrector fit slope is zero; cannot compute BPM1 reading at quad center.")
        if self.S is None or self.m1_ave is None:
            raise RuntimeError("BBA-2 fit inputs are incomplete.")

        self.model_r12 = self._calculate_model_r12()
        b1q1 = self.m1_ave - self.S / self.R12
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
            if not self.model_r12_error:
                self.model_r12_error = "model backend is not configured"
            return None

        if self.params.plane == "X":
            row, col = 0, 1
        elif self.params.plane == "Y":
            row, col = 2, 3
        else:
            raise RuntimeError("Plane should be X or Y.")

        lattice_overrides = self.params.model_lattice_overrides
        overrides = None
        if lattice_overrides is None and self.initial_quad_k1 is not None:
            overrides = {self.params.quad: self.initial_quad_k1}

        try:
            return self.model_backend.get_matrix_element(
                self.params.corr,
                self.params.bpm2,
                row,
                col,
                element_overrides=overrides,
                lattice_overrides=lattice_overrides,
            )
        except Exception as exc:
            self.model_r12_error = str(exc)
            return None

    def _try_build_model_backend(self):
        if self.params.app_context is None:
            self.model_r12_error = "app context is not configured"
            return
        try:
            self.model_backend = build_model_backend(
                self.params.app_context,
                energy_mev=self.params.energy_mev,
            )
            self.model_r12_error = None
        except Exception as exc:
            self.model_backend = None
            self.model_r12_error = str(exc)

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
