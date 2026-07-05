
import sys
import epics
import time
import json
import numpy as np
import math
from pathlib import Path
from datetime import datetime
from collections.abc import Mapping

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
from mplwidget import MplWidget
from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from half_linac.src.shared.beam_diagnostics import fit_beam_image
from half_linac.src.shared.machine_profile import (
    METADATA_FILENAME,
    MachineProfileError,
    build_model_backend,
    build_model_snapshot,
    describe_app_model_support,
    get_emit_preset,
    get_workflow,
    load_app_context,
    load_profile,
    model_snapshot_lattice_overrides,
    require_workflow_write_allowed,
    resolve_app_runtime_paths,
    resolve_channel,
    resolve_flag_pixel_geometry,
)

nest_dict    = lambda: defaultdict(nest_dict)

ELECTRON_MASS_EV = 0.51099895000e6
SCAN_RESULTS_FILENAME = "scanResults.txt"
SCAN_RESULTS_META_FILENAME = "scanResults.meta.json"
TWISS_RESULTS_FILENAME = "twissResults.jsonl"
SCAN_ARCHIVE_ROOT = Path(__file__).resolve().parent / "runtime" / "scans"
APP_DIR = Path(__file__).resolve().parent
SCAN_DATA_SCHEMA_VERSION = "emit_scan_v1"
SCAN_POINT_COLUMNS = ("Use", "K1", "sigx (mm)", "sigy (mm)")
TWISS_TRANSPORT_TOOLTIP = (
    "Twiss transport assumes geometric emittance is conserved along the selected "
    "model path. Use it only for paths without acceleration or other processes "
    "that change geometric emittance."
)

HEADER_ACTION_HEIGHT = 32


def _image_extent_from_geometry(geometry):
    pixel_width = geometry.pixel_width_mm
    width = geometry.shape[0] * pixel_width
    height = geometry.shape[1] * pixel_width
    return (-0.5 * width, 0.5 * width, -0.5 * height, 0.5 * height)


def _read_flag_image_fit(image_pv, pixel_shape, extent):
    raw_image = epics.caget(image_pv)
    if raw_image is None:
        raise RuntimeError(f"Failed to read flag image PV: {image_pv}.")
    try:
        flat_image = np.asarray(raw_image, dtype=float)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Flag image PV is not numeric array data: {image_pv}.") from exc

    expected_size = pixel_shape[0] * pixel_shape[1]
    if flat_image.size != expected_size:
        raise RuntimeError(
            f"Flag image length mismatch for {image_pv}: got {flat_image.size}, expected {expected_size}."
        )

    image = np.reshape(flat_image, (pixel_shape[1], pixel_shape[0]))
    fit_result = fit_beam_image(image, extent=extent)
    return image, fit_result


def _load_beam_image_geometry_config(machine_id):
    profile = load_profile(machine_id)
    try:
        return get_workflow(profile, "beam_monitor")
    except MachineProfileError as exc:
        raise MachineProfileError(
            "emit_measure local image fitting requires beam_monitor flag_pixel_geometry "
            f"for machine {machine_id!r}."
        ) from exc


def _finite_float_or_none(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _status_from_plane_result(result):
    status = _read_result_field(result, "status")
    if status:
        return str(status)
    return "valid" if _finite_float_or_none(_read_result_field(result, "ex")) is not None else "unresolved"


def _read_result_field(result, key, default=None):
    if isinstance(result, Mapping):
        return result.get(key, default)
    return getattr(result, key, default)


def _plane_summary(result):
    status = _status_from_plane_result(result)
    summary = {
        "status": status,
        "message": str(_read_result_field(result, "message", "") or ""),
    }
    for source_key, target_key in (
        ("ex", "emittance"),
        ("exn", "normalized_emittance"),
        ("beta", "beta"),
        ("alpha", "alpha"),
        ("gamma", "gamma"),
        ("determinant", "determinant"),
        ("discriminant", "discriminant"),
    ):
        value = _finite_float_or_none(_read_result_field(result, source_key))
        if value is not None:
            summary[target_key] = value
    return summary


def _method_fit_summary(method, xplane, yplane):
    x_summary = _plane_summary(xplane)
    y_summary = _plane_summary(yplane)
    valid_count = sum(
        1
        for plane_summary in (x_summary, y_summary)
        if plane_summary["status"] == "valid"
    )
    if valid_count == 2:
        status = "valid"
    elif valid_count == 1:
        status = "partial"
    else:
        status = "unresolved"
    return {
        "method": method,
        "status": status,
        "xplane": x_summary,
        "yplane": y_summary,
    }


def _invalid_plane_result(status, message):
    return {
        "status": status,
        "message": str(message),
        "ex": None,
        "exn": None,
        "beta": None,
        "alpha": None,
        "gamma": None,
    }


def _compact_status_text(message, limit=120):
    text = str(message or "").strip()
    if not text:
        return "unresolved"
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _twiss_from_transfer_matrix(matrix, twiss0, plane="xplane"):
    if plane == "xplane":
        m11 = matrix[0, 0]
        m12 = matrix[0, 1]
        m21 = matrix[1, 0]
        m22 = matrix[1, 1]
    else:
        m11 = matrix[2, 2]
        m12 = matrix[2, 3]
        m21 = matrix[3, 2]
        m22 = matrix[3, 3]

    beta0 = twiss0["beta0"]
    alpha0 = twiss0["alpha0"]
    gamma0 = twiss0["gamma0"]
    beta = m11**2 * beta0 - 2 * m11 * m12 * alpha0 + m12**2 * gamma0
    alpha = (
        -m11 * m21 * beta0
        + (m11 * m22 + m12 * m21) * alpha0
        - m12 * m22 * gamma0
    )
    gamma = m21**2 * beta0 - 2 * m21 * m22 * alpha0 + m22**2 * gamma0
    return {
        "beta": beta,
        "alpha": alpha,
        "gamma": gamma,
    }


def _plane_transfer_matrix_summary(matrix, plane="xplane"):
    if plane == "xplane":
        entries = (matrix[0, 0], matrix[0, 1], matrix[1, 0], matrix[1, 1])
    else:
        entries = (matrix[2, 2], matrix[2, 3], matrix[3, 2], matrix[3, 3])
    return {
        key: float(value)
        for key, value in zip(("r11", "r12", "r21", "r22"), entries)
    }


def _format_matrix_summary(matrix_summary):
    if not matrix_summary:
        return ""
    return (
        f"[ {matrix_summary['r11']:.6g}  {matrix_summary['r12']:.6g} ]\n"
        f"[ {matrix_summary['r21']:.6g}  {matrix_summary['r22']:.6g} ]"
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
    border-bottom-color: {panel_bg};
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

QLabel[role="sectionTitle"] {{
    color: {summary_title_fg};
    font-size: 12px;
    font-weight: 700;
    background: transparent;
    border: none;
    padding: 2px 0px 4px 0px;
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

QLineEdit[readOnly="true"], QTextEdit[readOnly="true"] {{
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
    alternate-background-color: {panel_bg};
    border: 1px solid {input_border};
    border-radius: 10px;
    color: {input_fg};
    gridline-color: {panel_border};
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

QRadioButton, QCheckBox {{
    color: {window_fg};
    font-size: 12px;
    font-weight: 600;
    spacing: 8px;
}}

QRadioButton::indicator, QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {panel_border};
    border-radius: 8px;
    background-color: {input_bg};
}}

QRadioButton::indicator:checked, QCheckBox::indicator:checked {{
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
        self.beam_monitor_config = _load_beam_image_geometry_config(self.machine_profile.machine.id)

        self.current_theme = "dark"
        self.machine_type = self.app_context.control_backend.name
        self.scan_mode = None

        # default settings 
        # ----------------
        self.lineEdit_2.setText("2200") # energy=2200MeV
        self.lineEdit_24.setText("5") # settle time=5s
        self.lineEdit_7.setText("0")  # K1-start
        self.lineEdit_8.setText("5")  # K1-end 
        self.lineEdit_9.setText("15") # steps=15
        self.lineEdit_10.setText("5") # samples=5 

        self.scan = None
        self.twissCal = None
        self.clear = None
        self.sample_interval_edit = None
        self.sample_interval_label = None
        self.use_latest_fit_button = QPushButton("Use Latest Fit", self)
        self.twiss_initial_title = QLabel("Initial Twiss at From", self)
        self.twiss_result_title = QLabel("Computed Twiss at To", self)
        self.twiss_direction_label = QLabel("Direction", self)
        self.twiss_direction_combo = QComboBox(self)
        self.twiss_plane_label = QLabel("Plane", self)
        self.twiss_plane_combo = QComboBox(self)
        self.twiss_status_label = QLabel("Status", self)
        self.twiss_status_edit = QLineEdit(self)
        self.twiss_map_label = QLabel("Transfer Map", self)
        self.twiss_map_edit = QTextEdit(self)
        self.scan_points_table = None
        self.scan_points_summary_label = None
        self.loaded_scan_metadata = None
        self.loaded_scan_results_path = None
        self.pending_scan_metadata = None
        self.latest_emit_fit_summary = None
        self.latest_twiss_summary = None
        self.twiss_initial_source = {"kind": "manual"}
        self.latest_beam_image = None
        self.latest_beam_fit_result = None
        self.latest_beam_fit_flag = None
        self.latest_beam_fit_k1 = None
        self._beam_image_auto_refresh_ready = False
        self._model_backend_available, self._model_backend_error = describe_app_model_support(
            self.machine_profile.machine.id,
            "emit_measure",
        )
        self.beam_image_timer = QTimer(self)
        self.beam_image_timer.setInterval(2000)
        self.beam_image_timer.timeout.connect(self._auto_refresh_beam_image_fit)
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
        self.use_latest_fit_button.clicked.connect(self._use_latest_fit_for_twiss)

        # other function
        self.comboBox.currentIndexChanged.connect(self.updateComboBox4)
        self.comboBox_4.currentIndexChanged.connect(self._handle_emit_flag_changed)
        self.comboBox_2.currentIndexChanged.connect(self._update_twiss_path_status)
        self.comboBox_3.currentIndexChanged.connect(self._update_twiss_path_status)
        self.twiss_direction_combo.currentIndexChanged.connect(self._update_twiss_path_status)
        self.twiss_plane_combo.currentIndexChanged.connect(self._update_twiss_path_status)
        self.lineEdit.textEdited.connect(self._mark_twiss_initial_manual)
        self.lineEdit_3.textEdited.connect(self._mark_twiss_initial_manual)
        self.lineEdit_6.textEdited.connect(self._mark_twiss_initial_manual)
        self.tabWidget.currentChanged.connect(self._refresh_status)
        # self.pushButton_6.clicked.connect(self.simply_VM)
        # self.pushButton_7.clicked.connect(self.full_VM)

        self._configure_machine_profile()
        self._refresh_model_controls()
        self._apply_theme()
        self._draw_placeholder_plots()
        self._refresh_status()
        self._beam_image_auto_refresh_ready = True
        self._schedule_beam_image_refresh()
        self._update_beam_image_auto_refresh()

    def _configure_window(self):
        self.setWindowTitle(f"{self.machine_profile.machine.display_name} Emittance Measurement")
        self.resize(1600, 1020)
        self.setMinimumSize(1320, 880)

    def _build_shell(self):
        self.verticalLayout.setContentsMargins(10, 10, 10, 10)
        self.verticalLayout.setSpacing(12)
        self.tabWidget.setDocumentMode(False)
        self.tabWidget.tabBar().setDrawBase(False)
        self.tabWidget.setElideMode(Qt.ElideNone)
        self._attach_tab_roots()
        self.gridLayout_2.setRowStretch(0, 4)
        self.gridLayout_2.setRowStretch(1, 1)
        self.gridLayout_4.setRowStretch(0, 4)
        self.gridLayout_4.setRowStretch(1, 1)

        self._build_summary_panel()
        self._style_plot_cards()
        self._style_control_cards()
        self._arrange_main_tabs()

    def _attach_tab_roots(self):
        self.tabWidget.setTabText(self.tabWidget.indexOf(self.X_Plane), "Scan")
        self.tabWidget.setTabText(self.tabWidget.indexOf(self.tab_2), "Analysis")
        if self.X_Plane.layout() is self.gridLayout:
            self.gridLayout.setContentsMargins(0, 0, 0, 0)
        if self.tab_2.layout() is None:
            tab_layout = QVBoxLayout(self.tab_2)
            tab_layout.setContentsMargins(0, 0, 0, 0)
            tab_layout.setSpacing(0)
            tab_layout.addWidget(self.layoutWidget_2)
        self.twiss_tab = QWidget()
        self.twiss_tab.setObjectName("twissTab")
        self.twiss_tab_layout = QGridLayout(self.twiss_tab)
        self.twiss_tab_layout.setContentsMargins(0, 0, 6, 6)
        self.twiss_tab_layout.setSpacing(10)
        twiss_tab_index = self.tabWidget.addTab(self.twiss_tab, "Twiss")
        self.tabWidget.setTabToolTip(twiss_tab_index, TWISS_TRANSPORT_TOOLTIP)

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

        title = QLabel("Emittance Measurement", panel)
        title.setObjectName("summaryTitle")
        header_layout.addWidget(title)
        header_layout.addStretch(1)

        for text in (
            f"Machine: {self.machine_profile.machine.display_name}",
            f"Backend: {self.machine_type.upper()}",
        ):
            runtime_label = QLabel(text, panel)
            runtime_label.setProperty("role", "field")
            runtime_label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            header_layout.addWidget(runtime_label)

        self.theme_toggle_button = QToolButton(panel)
        self.theme_toggle_button.setObjectName("themeToggleButton")
        self.theme_toggle_button.setFixedSize(HEADER_ACTION_HEIGHT, HEADER_ACTION_HEIGHT)
        self.theme_toggle_button.clicked.connect(self._toggle_theme)
        header_layout.addWidget(self.theme_toggle_button)

        outer_layout.addLayout(header_layout)

        self.status_panel = EmitStatusStrip(panel)
        self.status_panel.add_item("mode", "MODE", self.machine_type.upper())
        self.status_panel.add_item("model", "MODEL", self._model_backend_status_text())
        self.status_panel.add_item("scan", "SCAN", "Idle")
        self.status_panel.add_item("twiss", "TWISS", "Idle")
        self.status_panel.add_item("fit", "PRF FIT", "No image")
        self.status_panel.add_item("emit", "EMIT", "No result")
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

        self._build_beam_image_card()
        for widget in (self.widget_6, self.widget_11, self.widget_12):
            widget.hide()

    def _build_beam_image_card(self):
        self.gridLayout_2.removeWidget(self.widget_3)
        self.widget_3.hide()

        card = QFrame(self.X_Plane)
        card.setObjectName("plotCard")
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)

        title = QLabel("Current PRF Image", card)
        title.setObjectName("panelTitle")
        title.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        header.addWidget(title)
        header.addStretch(1)

        colormap_label = QLabel("Colormap", card)
        colormap_label.setProperty("role", "field")
        self.beam_image_colormap_combo = QComboBox(card)
        self.beam_image_colormap_combo.addItems(["viridis", "plasma", "inferno", "magma", "gray", "jet"])
        self.beam_image_colormap_combo.setMaximumWidth(105)
        self.beam_image_colormap_combo.currentTextChanged.connect(self._redraw_latest_beam_image)

        vmin_label = QLabel("vmin", card)
        vmin_label.setProperty("role", "field")
        self.beam_image_vmin_edit = QLineEdit(card)
        self.beam_image_vmin_edit.setPlaceholderText("auto")
        self.beam_image_vmin_edit.setMaximumWidth(68)
        self.beam_image_vmin_edit.returnPressed.connect(self._redraw_latest_beam_image)

        vmax_label = QLabel("vmax", card)
        vmax_label.setProperty("role", "field")
        self.beam_image_vmax_edit = QLineEdit(card)
        self.beam_image_vmax_edit.setPlaceholderText("auto")
        self.beam_image_vmax_edit.setMaximumWidth(68)
        self.beam_image_vmax_edit.returnPressed.connect(self._redraw_latest_beam_image)

        self.beam_image_auto_refresh_checkbox = QCheckBox("Auto refresh", card)
        self.beam_image_auto_refresh_checkbox.setChecked(True)
        self.beam_image_auto_refresh_checkbox.stateChanged.connect(self._update_beam_image_auto_refresh)
        self.beam_image_projection_checkbox = QCheckBox("Projection", card)
        self.beam_image_projection_checkbox.setChecked(True)
        self.beam_image_projection_checkbox.stateChanged.connect(self._redraw_latest_beam_image)
        self.beam_image_fit_curve_checkbox = QCheckBox("Fit curve", card)
        self.beam_image_fit_curve_checkbox.setChecked(True)
        self.beam_image_fit_curve_checkbox.stateChanged.connect(self._redraw_latest_beam_image)

        self.preview_fit_button = QPushButton("Check PRF Fit", card)
        self.preview_fit_button.setProperty("compact", True)
        self.preview_fit_button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.preview_fit_button.clicked.connect(lambda: self.refresh_current_beam_image_fit())

        for widget in (
            colormap_label,
            self.beam_image_colormap_combo,
            vmin_label,
            self.beam_image_vmin_edit,
            vmax_label,
            self.beam_image_vmax_edit,
            self.beam_image_auto_refresh_checkbox,
            self.beam_image_projection_checkbox,
            self.beam_image_fit_curve_checkbox,
            self.preview_fit_button,
        ):
            header.addWidget(widget)
        layout.addLayout(header)

        self.beam_image_widget = MplWidget(card)
        layout.addWidget(self.beam_image_widget, 1)

        status_grid = QGridLayout()
        status_grid.setHorizontalSpacing(8)
        status_grid.setVerticalSpacing(4)
        self.beam_fit_flag_label = QLabel("--", card)
        self.beam_fit_sigx_label = QLabel("--", card)
        self.beam_fit_sigy_label = QLabel("--", card)
        self.beam_fit_status_label = QLabel("No image", card)
        for label in (
            self.beam_fit_flag_label,
            self.beam_fit_sigx_label,
            self.beam_fit_sigy_label,
            self.beam_fit_status_label,
        ):
            label.setWordWrap(True)
        for col, text in enumerate(("Flag", "sigx", "sigy", "Status")):
            label = QLabel(text, card)
            label.setProperty("role", "field")
            status_grid.addWidget(label, 0, col)
        status_grid.addWidget(self.beam_fit_flag_label, 1, 0)
        status_grid.addWidget(self.beam_fit_sigx_label, 1, 1)
        status_grid.addWidget(self.beam_fit_sigy_label, 1, 2)
        status_grid.addWidget(self.beam_fit_status_label, 1, 3)
        status_grid.setColumnStretch(3, 1)
        layout.addLayout(status_grid)

        self.gridLayout_2.addWidget(card, 0, 2, 1, 1)
        self.gridLayout_2.setColumnStretch(2, 2)
        self.beam_image_card = card
        self._plot_wrappers[self.beam_image_widget] = card

    @staticmethod
    def _clear_layout_positions(layout):
        while layout.count():
            layout.takeAt(0)

    def _arrange_main_tabs(self):
        self._clear_layout_positions(self.gridLayout_2)
        self._clear_layout_positions(self.gridLayout_4)
        self._clear_layout_positions(self.twiss_tab_layout)

        self.gridLayout_2.setHorizontalSpacing(10)
        self.gridLayout_2.setVerticalSpacing(10)
        self.gridLayout_2.addWidget(self.widget_4, 0, 0, 2, 1, Qt.AlignTop)
        self.gridLayout_2.addWidget(self.beam_image_card, 0, 1, 1, 2)
        self.gridLayout_2.addWidget(self._plot_wrappers[self.widget], 1, 1)
        self.gridLayout_2.addWidget(self._plot_wrappers[self.widget_8], 1, 2)
        self.gridLayout_2.setColumnStretch(0, 2)
        self.gridLayout_2.setColumnStretch(1, 3)
        self.gridLayout_2.setColumnStretch(2, 3)
        self.gridLayout_2.setRowStretch(0, 3)
        self.gridLayout_2.setRowStretch(1, 5)
        self.beam_image_widget.setMinimumHeight(240)
        for plot in (self.widget, self.widget_8):
            plot.setMinimumHeight(300)
            self._plot_wrappers[plot].setMinimumHeight(340)

        self.gridLayout_4.setHorizontalSpacing(10)
        self.gridLayout_4.setVerticalSpacing(10)
        self.gridLayout_4.addWidget(self._plot_wrappers[self.widget_2], 0, 0)
        self.gridLayout_4.addWidget(self._plot_wrappers[self.widget_9], 0, 1)
        self.gridLayout_4.addWidget(self.widget_5, 1, 0, Qt.AlignTop)
        self.gridLayout_4.addWidget(self.widget_10, 1, 1, Qt.AlignTop)
        self.gridLayout_4.setColumnStretch(0, 1)
        self.gridLayout_4.setColumnStretch(1, 1)
        self.gridLayout_4.setRowStretch(0, 4)
        self.gridLayout_4.setRowStretch(1, 1)

        self.twiss_tab_layout.addWidget(self.widget_13, 0, 0, Qt.AlignTop | Qt.AlignLeft)
        self.twiss_tab_layout.setColumnStretch(0, 1)

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
        self.widget_13.setMaximumWidth(900)

        self.label_9.setText("Scan Control")
        self.label_15.setText("X Plane Results")
        self.label_42.setText("Y Plane Results")
        self.label_8.setText("Twiss Transport")
        for title in (self.label_9, self.label_15, self.label_42, self.label_8):
            title.setObjectName("panelTitle")
        for title in (self.twiss_initial_title, self.twiss_result_title):
            title.setProperty("role", "sectionTitle")
        for label in (self.label_19, self.label_44, self.label_49, self.label_50):
            label.setText("gamma (1/m)")

        self.textEdit.hide()
        self.label_3.hide()
        self.gridLayout_2.setAlignment(self.widget_4, Qt.AlignTop)
        self.gridLayout_2.setAlignment(self.widget_5, Qt.AlignTop)
        self.gridLayout_4.setAlignment(self.widget_13, Qt.AlignTop)
        self.gridLayout_4.setAlignment(self.widget_10, Qt.AlignTop)

    def _configure_form_content(self):
        self.pushButton.setText("Start Scan")
        self.pushButton_2.setText("Recalculate")
        self.pushButton_3.setText("Clear View")
        self.pushButton_4.setText("Calculate Twiss")
        self.pushButton_5.setText("Stop Scan")
        self.use_latest_fit_button.setText("Use Latest Fit")
        self.label_32.setText("Settle time (s)")
        self.label_4.setText("From")
        self.label_7.setText("To")
        self.twiss_initial_title.setText("Initial Twiss at From")
        self.twiss_result_title.setText("Computed Twiss at To")
        self.twiss_direction_label.setText("Direction")
        self.twiss_plane_label.setText("Plane")
        self.twiss_status_label.setText("Status")
        self.twiss_map_label.setText("Transfer Map")
        self.radioButton.hide()
        self.radioButton_2.hide()
        self.twiss_direction_combo.clear()
        self.twiss_direction_combo.addItem("Forward", False)
        self.twiss_direction_combo.addItem("Backward", True)
        self.twiss_plane_combo.clear()
        self.twiss_plane_combo.addItem("X Plane", "xplane")
        self.twiss_plane_combo.addItem("Y Plane", "yplane")

        for button in (
            self.pushButton,
            self.pushButton_2,
            self.pushButton_3,
            self.pushButton_4,
            self.pushButton_5,
            self.use_latest_fit_button,
        ):
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
            self.twiss_direction_label,
            self.twiss_plane_label,
            self.twiss_status_label,
            self.twiss_map_label,
        ):
            label.setProperty("role", "field")

        self.twiss_status_edit.setReadOnly(True)
        self.twiss_status_edit.setText("Idle")
        self.twiss_map_edit.setReadOnly(True)
        self.twiss_map_edit.setPlainText("No Twiss calculation yet")
        self.twiss_map_edit.setFixedHeight(58)
        self.twiss_map_edit.setLineWrapMode(QTextEdit.NoWrap)

        self._result_fields = [
            self.lineEdit_11, self.lineEdit_12, self.lineEdit_13, self.lineEdit_14, self.lineEdit_15, self.lineEdit_16,
            self.lineEdit_39, self.lineEdit_35, self.lineEdit_40, self.lineEdit_36, self.lineEdit_38, self.lineEdit_37,
            self.lineEdit_4, self.lineEdit_5, self.lineEdit_20, self.lineEdit_19, self.lineEdit_18,
            self.lineEdit_41, self.lineEdit_42, self.lineEdit_43, self.lineEdit_44, self.lineEdit_45,
            self.lineEdit_17, self.lineEdit_21, self.lineEdit_22, self.twiss_status_edit,
        ]
        for widget in self._result_fields:
            widget.setReadOnly(True)
        for widget in (self.lineEdit_16, self.lineEdit_37):
            widget.setMinimumWidth(360)
            widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self._rebuild_panel_layouts()
        self._configure_tooltips()

    def _configure_tooltips(self):
        self.pushButton.setToolTip("Start a new emittance scan after validating the PRF image and write access.")
        self.pushButton_2.setToolTip("Recalculate emittance from the active scan points.")
        self.pushButton_3.setToolTip(
            "Clear plots, result fields, and the current scan point table. Saved archives are not deleted."
        )
        self.label_8.setToolTip(TWISS_TRANSPORT_TOOLTIP)
        self.twiss_tab.setToolTip(TWISS_TRANSPORT_TOOLTIP)
        self.pushButton_4.setToolTip(self._twiss_transport_tooltip())
        self.pushButton_5.setToolTip("Request the running scan to stop and restore the quadrupole setting.")
        self.use_latest_fit_button.setToolTip(
            "Copy beta, alpha and gamma from the latest valid emittance fit for the selected Twiss plane."
        )
        self.preview_fit_button.setToolTip("Read the selected PRF image PV and update the local beam-size fit.")
        self.load_points_button.setToolTip("Open an archived emittance scan for review or recalculation.")
        self.exclude_points_button.setToolTip("Disable the selected scan points without deleting the rows.")
        self.restore_points_button.setToolTip("Enable all scan points in the table.")
        self.lineEdit_24.setToolTip("Wait time after each K1 change before taking the first sample.")
        self.label_32.setToolTip(self.lineEdit_24.toolTip())
        self.sample_interval_edit.setToolTip("Wait time between repeated PRF image samples at the same K1.")
        self.sample_interval_label.setToolTip(self.sample_interval_edit.toolTip())
        self.lineEdit_10.setToolTip("Number of PRF image samples collected at each K1 value.")
        self.label_14.setToolTip(self.lineEdit_10.toolTip())
        self.comboBox_2.setToolTip("Start element for Twiss transport.")
        self.label_4.setToolTip(self.comboBox_2.toolTip())
        self.comboBox_3.setToolTip("End element for Twiss transport.")
        self.label_7.setToolTip(self.comboBox_3.toolTip())
        self.twiss_direction_combo.setToolTip("Choose forward transport or the inverse transfer map from To back to From.")
        self.twiss_direction_label.setToolTip(self.twiss_direction_combo.toolTip())
        self.twiss_plane_combo.setToolTip("Choose the Twiss plane to calculate.")
        self.twiss_plane_label.setToolTip(self.twiss_plane_combo.toolTip())
        self.twiss_status_edit.setToolTip("Latest Twiss calculation state or error message.")
        self.twiss_status_label.setToolTip(self.twiss_status_edit.toolTip())
        self.twiss_map_edit.setToolTip("2x2 transfer matrix block used by the latest Twiss calculation.")
        self.twiss_map_label.setToolTip(self.twiss_map_edit.toolTip())

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
        self.sample_interval_label = QLabel("Sample interval (s)", self.widget_4)
        self.sample_interval_label.setProperty("role", "field")
        self.sample_interval_edit = QLineEdit(self.widget_4)
        self.sample_interval_edit.setText("0.5")
        form.addWidget(self.sample_interval_label, 4, 0)
        form.addWidget(self.sample_interval_edit, 4, 1)
        form.setColumnStretch(1, 1)
        form.setColumnStretch(3, 1)
        layout.addLayout(form)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)

        for button in (self.pushButton, self.pushButton_5, self.pushButton_3):
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
        self.scan_points_table.setAlternatingRowColors(True)
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
        self.pushButton_2.setParent(self.widget_4)
        self.load_points_button = QPushButton("Load Points", self.widget_4)
        self.exclude_points_button = QPushButton("Exclude Selected", self.widget_4)
        self.restore_points_button = QPushButton("Use All Points", self.widget_4)
        for button in (
            self.pushButton_2,
            self.load_points_button,
            self.exclude_points_button,
            self.restore_points_button,
        ):
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
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(12)

        self.label_8.setParent(self.widget_13)
        layout.addWidget(self.label_8)

        top_row = QGridLayout()
        top_row.setHorizontalSpacing(10)
        top_row.setVerticalSpacing(5)
        self.twiss_direction_label.setParent(self.widget_13)
        self.twiss_direction_combo.setParent(self.widget_13)
        self.twiss_plane_label.setParent(self.widget_13)
        self.twiss_plane_combo.setParent(self.widget_13)
        top_row.addWidget(self.label_4, 0, 0)
        top_row.addWidget(self.label_7, 0, 1)
        top_row.addWidget(self.twiss_direction_label, 0, 2)
        top_row.addWidget(self.twiss_plane_label, 0, 3)
        top_row.addWidget(self.comboBox_2, 1, 0)
        top_row.addWidget(self.comboBox_3, 1, 1)
        top_row.addWidget(self.twiss_direction_combo, 1, 2)
        top_row.addWidget(self.twiss_plane_combo, 1, 3)
        top_row.setColumnStretch(0, 3)
        top_row.setColumnStretch(1, 3)
        top_row.setColumnStretch(2, 1)
        top_row.setColumnStretch(3, 1)
        self.twiss_direction_combo.setMinimumWidth(126)
        self.twiss_plane_combo.setMinimumWidth(126)
        layout.addLayout(top_row)

        grids_row = QHBoxLayout()
        grids_row.setContentsMargins(0, 0, 0, 0)
        grids_row.setSpacing(18)
        self.gridLayoutWidget.setParent(self.widget_13)
        self.gridLayoutWidget_2.setParent(self.widget_13)
        self.twiss_initial_title.setParent(self.widget_13)
        self.twiss_result_title.setParent(self.widget_13)
        self.gridLayoutWidget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.gridLayoutWidget_2.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.gridLayout_5.setVerticalSpacing(6)
        self.gridLayout_7.setVerticalSpacing(6)
        self.gridLayout_5.setHorizontalSpacing(8)
        self.gridLayout_7.setHorizontalSpacing(8)
        self.gridLayout_5.setColumnStretch(1, 1)
        self.gridLayout_7.setColumnStretch(1, 1)
        initial_layout = QVBoxLayout()
        initial_layout.setContentsMargins(0, 0, 0, 0)
        initial_layout.setSpacing(4)
        initial_layout.addWidget(self.twiss_initial_title)
        initial_layout.addWidget(self.gridLayoutWidget)
        result_layout = QVBoxLayout()
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.setSpacing(4)
        result_layout.addWidget(self.twiss_result_title)
        result_layout.addWidget(self.gridLayoutWidget_2)
        grids_row.addLayout(initial_layout, 1)
        grids_row.addLayout(result_layout, 1)
        layout.addLayout(grids_row)

        status_row = QGridLayout()
        status_row.setHorizontalSpacing(6)
        status_row.setVerticalSpacing(5)
        self.twiss_status_label.setParent(self.widget_13)
        self.twiss_status_edit.setParent(self.widget_13)
        status_row.addWidget(self.twiss_status_label, 0, 0)
        status_row.addWidget(self.twiss_status_edit, 0, 1)
        status_row.setColumnStretch(1, 1)
        layout.addLayout(status_row)

        detail_row = QGridLayout()
        detail_row.setHorizontalSpacing(6)
        detail_row.setVerticalSpacing(5)
        self.twiss_map_label.setParent(self.widget_13)
        self.twiss_map_edit.setParent(self.widget_13)
        detail_row.addWidget(self.twiss_map_label, 0, 0)
        detail_row.addWidget(self.twiss_map_edit, 0, 1)
        detail_row.setColumnStretch(1, 1)
        layout.addLayout(detail_row)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        self.radioButton.setParent(self.widget_13)
        self.radioButton_2.setParent(self.widget_13)
        self.use_latest_fit_button.setParent(self.widget_13)
        self.pushButton_4.setParent(self.widget_13)
        footer.addStretch(1)
        self.use_latest_fit_button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        footer.addWidget(self.use_latest_fit_button)
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
        self.gridLayout_6.addWidget(self.lineEdit_37, 6, 1, 1, 2)
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
        if hasattr(self, "beam_image_widget"):
            self._style_axes(self.beam_image_widget, "x (mm)", "y (mm)")
        for plot in (self.widget, self.widget_2, self.widget_8, self.widget_9):
            plot.canvas.draw_idle()
        if hasattr(self, "beam_image_widget"):
            self.beam_image_widget.canvas.draw_idle()

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
        self._draw_scan_fit_placeholder_plots()
        self._draw_beam_image_placeholder()

    def _draw_scan_fit_placeholder_plots(self):
        self._draw_placeholder(self.widget, "$K_1 (m^{-2})$", "sigx (mm)", "Waiting for scan points")
        self._draw_placeholder(self.widget_2, "$-K= K_1 L_q (m^{-1})$", "$sigx^2 (mm^2)$", "Waiting for fit")
        self._draw_placeholder(self.widget_8, "$K_1 (m^{-2})$", "sigy (mm)", "Waiting for scan points")
        self._draw_placeholder(self.widget_9, "$K= K_1 L_q (m^{-1})$", "$sigy^2 (mm^2)$", "Waiting for fit")

    def _draw_beam_image_placeholder(self, note="Update PRF image before scan"):
        if not hasattr(self, "beam_image_widget"):
            return
        self.latest_beam_image = None
        self.latest_beam_fit_result = None
        self.latest_beam_fit_flag = None
        self.latest_beam_fit_k1 = None
        self._draw_placeholder(self.beam_image_widget, "x (mm)", "y (mm)", note)
        if hasattr(self, "beam_fit_flag_label"):
            self.beam_fit_flag_label.setText(self.comboBox_4.currentText() or "--")
            self.beam_fit_sigx_label.setText("--")
            self.beam_fit_sigy_label.setText("--")
            self.beam_fit_status_label.setText("No image")

    def _optional_beam_image_limit(self, edit):
        text = edit.text().strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            edit.setText("")
            return None

    def _beam_image_display_limits(self):
        if not hasattr(self, "beam_image_vmin_edit"):
            return None, None
        vmin = self._optional_beam_image_limit(self.beam_image_vmin_edit)
        vmax = self._optional_beam_image_limit(self.beam_image_vmax_edit)
        if vmin is not None and vmax is not None and vmax <= vmin:
            self.beam_image_vmax_edit.setText("")
            vmax = None
        return vmin, vmax

    def _redraw_latest_beam_image(self, *args):
        del args
        if self.latest_beam_image is None or self.latest_beam_fit_result is None:
            return
        self._display_beam_image_fit(
            self.latest_beam_fit_flag or self.comboBox_4.currentText(),
            self.latest_beam_image,
            self.latest_beam_fit_result,
            k1=self.latest_beam_fit_k1,
        )

    def _display_beam_image_fit(self, flag_name, image, fit_result, *, k1=None, extent=None):
        if not hasattr(self, "beam_image_widget"):
            return
        if extent is None:
            extent = self._current_flag_image_extent(flag_name)
        palette = self._palette()
        widget = self.beam_image_widget
        widget.axes.clear()
        self._style_axes(widget, "x (mm)", "y (mm)")
        vmin, vmax = self._beam_image_display_limits()
        widget.axes.imshow(
            image,
            cmap=self.beam_image_colormap_combo.currentText() if hasattr(self, "beam_image_colormap_combo") else "viridis",
            origin="lower",
            extent=extent,
            aspect="auto",
            vmin=vmin,
            vmax=vmax,
        )

        height = abs(extent[3] - extent[2])
        width = abs(extent[1] - extent[0])
        x_projection = fit_result.x_projection.normalized_projection
        y_projection = fit_result.y_projection.normalized_projection
        show_projection = (
            not hasattr(self, "beam_image_projection_checkbox")
            or self.beam_image_projection_checkbox.isChecked()
        )
        show_fit_curve = (
            not hasattr(self, "beam_image_fit_curve_checkbox")
            or self.beam_image_fit_curve_checkbox.isChecked()
        )
        if show_projection and x_projection is not None and y_projection is not None:
            denx = x_projection * height * 0.3 + extent[2] * 0.98
            deny = y_projection * width * 0.3 + extent[0] * 0.98
            widget.axes.plot(fit_result.x_axis, denx, "--c")
            widget.axes.plot(deny, fit_result.y_axis, "--c")
        if show_fit_curve and fit_result.valid:
            fit_denx = fit_result.x_projection.fitted_projection * height * 0.3 + extent[2] * 0.98
            fit_deny = fit_result.y_projection.fitted_projection * width * 0.3 + extent[0] * 0.98
            widget.axes.plot(fit_result.x_axis, fit_denx, "--", color=palette["plot_fit"])
            widget.axes.plot(fit_deny, fit_result.y_axis, "--", color=palette["plot_fit"])

        title = flag_name
        if k1 is not None:
            title = f"{flag_name} / K1 {float(k1):.6g}"
        widget.axes.set_title(title, color=palette["plot_text"], fontsize=11, fontweight="bold", loc="left")
        widget.canvas.draw()

        self.latest_beam_image = image
        self.latest_beam_fit_result = fit_result
        self.latest_beam_fit_flag = flag_name
        self.latest_beam_fit_k1 = k1
        self.beam_fit_flag_label.setText(flag_name)
        self.beam_fit_sigx_label.setText(f"{fit_result.sigx_mm:.3f}" if fit_result.sigx_mm is not None else "--")
        self.beam_fit_sigy_label.setText(f"{fit_result.sigy_mm:.3f}" if fit_result.sigy_mm is not None else "--")
        if fit_result.valid:
            self.beam_fit_status_label.setText("valid")
        else:
            self.beam_fit_status_label.setText(fit_result.status)
        self._refresh_status()

    def _refresh_status(self):
        if not hasattr(self, "status_panel"):
            return
        mode_tone = "warning" if self.machine_type == "real" else "success"
        self.status_panel.set_item("mode", self.machine_type.upper(), mode_tone)
        self.status_panel.set_item(
            "model",
            self._model_backend_status_text(),
            self._model_backend_status_tone(),
            self._model_backend_status_tooltip(),
        )

        if self._scan_is_running():
            if self.scan_mode == "recalculate":
                scan_text = "Recalculate"
                scan_tone = "success"
            elif self.scan_mode == "stopping":
                scan_text = "Stopping"
                scan_tone = "warning"
            else:
                scan_text = "Running"
                scan_tone = "success"
            self.status_panel.set_item("scan", scan_text, scan_tone)
        else:
            self.status_panel.set_item("scan", "Idle", "subtle")

        if self._twiss_is_running():
            twiss_summary = self.latest_twiss_summary or {}
            text = self._format_twiss_status_text(twiss_summary, default="Running")
            tooltip = self._format_twiss_status_tooltip(twiss_summary)
            self.status_panel.set_item("twiss", text, "success", tooltip)
        elif self.latest_twiss_summary is None:
            self.status_panel.set_item("twiss", "Idle", "subtle")
        elif self.latest_twiss_summary.get("status") == "error":
            self.status_panel.set_item(
                "twiss",
                "Error",
                "warning",
                self._format_twiss_status_tooltip(self.latest_twiss_summary),
            )
        else:
            self.status_panel.set_item(
                "twiss",
                self._format_twiss_status_text(self.latest_twiss_summary),
                "success",
                self._format_twiss_status_tooltip(self.latest_twiss_summary),
            )
        if self.latest_beam_fit_result is None:
            self.status_panel.set_item("fit", "No image", "subtle")
        elif self.latest_beam_fit_result.valid:
            self.status_panel.set_item(
                "fit",
                f"{self.latest_beam_fit_flag} sx {self.latest_beam_fit_result.sigx_mm:.3f} sy {self.latest_beam_fit_result.sigy_mm:.3f}",
                "success",
            )
        else:
            self.status_panel.set_item(
                "fit",
                f"{self.latest_beam_fit_flag} {self.latest_beam_fit_result.status}",
                "warning",
            )
        self._refresh_emit_fit_status()
        scan_results_path = self._latest_scan_results_path()
        if scan_results_path.exists():
            active, total = self._scan_points_counts()
            if total:
                self.status_panel.set_item("data", f"{active}/{total} points", "success")
            else:
                self.status_panel.set_item("data", "runtime latest", "success")
        else:
            self.status_panel.set_item("data", "No scan file", "warning")

    def _refresh_emit_fit_status(self):
        summary = self.latest_emit_fit_summary
        if not summary:
            self.status_panel.set_item("emit", "No result", "subtle")
            return
        status = summary.get("status", "unresolved")
        method = summary.get("method", "fit")
        if status == "valid":
            tone = "success"
        elif status == "error":
            tone = "warning"
        else:
            tone = "warning"

        x_status = summary.get("xplane", {}).get("status", "unknown")
        y_status = summary.get("yplane", {}).get("status", "unknown")
        if status == "error":
            text = _compact_status_text(summary.get("message", "error"), limit=90)
        else:
            text = f"{method}: {status} (x {x_status}, y {y_status})"
        self.status_panel.set_item("emit", text, tone)

    @staticmethod
    def _format_twiss_plane_label(plane):
        return "Y" if plane == "yplane" else "X"

    def _format_twiss_status_text(self, summary, default="Idle"):
        if not summary:
            return default
        plane_label = self._format_twiss_plane_label(summary.get("plane"))
        direction = summary.get("direction")
        if direction:
            return f"{plane_label} {direction}"
        return plane_label

    def _format_twiss_status_tooltip(self, summary):
        if not summary:
            return ""
        plane_label = self._format_twiss_plane_label(summary.get("plane"))
        direction = summary.get("direction", "")
        from_element = summary.get("from_element", "")
        to_element = summary.get("to_element", "")
        status = summary.get("status", "")
        parts = [f"{plane_label} plane"]
        if direction:
            parts.append(direction)
        if from_element and to_element:
            parts.append(f"{from_element} -> {to_element}")
        if status == "error":
            parts.append(_compact_status_text(summary.get("message", "error"), limit=100))
        return ", ".join(parts)

    def _model_backend_status_text(self):
        return "Ready" if self._model_backend_available else "Unavailable"

    def _model_backend_status_tone(self):
        return "success" if self._model_backend_available else "warning"

    def _model_backend_status_tooltip(self):
        if self._model_backend_available:
            return "Model backend is available for emittance and Twiss calculations."
        return f"Model backend unavailable: {self._model_backend_error}"

    @staticmethod
    def _twiss_transport_tooltip(prefix=None):
        base = "Calculate Twiss transport from the selected From element to the selected To element."
        if prefix:
            base = f"{prefix} {base}"
        return f"{base} {TWISS_TRANSPORT_TOOLTIP}"

    @staticmethod
    def _refresh_widget_style(widget):
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()

    def _refresh_model_controls(self):
        if self._model_backend_available:
            self.pushButton.setEnabled(True)
            self.pushButton.setToolTip("Start a new emittance scan after validating the PRF image and write access.")
            self.pushButton_2.setEnabled(True)
            self.pushButton_2.setToolTip("Recalculate emittance from the active scan points.")
            self.pushButton_4.setEnabled(True)
            self.pushButton_4.setToolTip(self._twiss_transport_tooltip())
        else:
            message = self._model_backend_status_tooltip()
            self.pushButton.setEnabled(False)
            self.pushButton.setToolTip(message)
            self.pushButton_2.setEnabled(False)
            self.pushButton_2.setToolTip(message)
            self.pushButton_4.setEnabled(False)
            self.pushButton_4.setToolTip(self._twiss_transport_tooltip(prefix=message))

        for button in (self.pushButton, self.pushButton_2, self.pushButton_4):
            self._refresh_widget_style(button)

    def _require_model_backend_available(self, operation, *, title="Emittance Measurement"):
        if self._model_backend_available:
            return True
        message = f"{operation} requires the model backend. {self._model_backend_status_tooltip()}"
        print(message)
        QMessageBox.warning(self, title, message)
        return False

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
            self.loaded_scan_results_path = self._latest_scan_results_path()
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
        return resolve_app_runtime_paths(APP_DIR, self.app_context)["runs_dir"]

    def _legacy_scan_archive_dir(self):
        return SCAN_ARCHIVE_ROOT / self.machine_profile.machine.id / self.machine_type

    def _scan_latest_dir(self):
        return resolve_app_runtime_paths(APP_DIR, self.app_context)["latest_dir"]

    def _latest_scan_results_path(self):
        return self._scan_latest_dir() / SCAN_RESULTS_FILENAME

    def _twiss_results_path(self):
        if self.loaded_scan_results_path is not None:
            return Path(self.loaded_scan_results_path).parent / TWISS_RESULTS_FILENAME
        return self._scan_latest_dir() / TWISS_RESULTS_FILENAME

    def _emit_model_snapshot_fields_for_path(self, elem1, elem2, *, model_line=None):
        backend = build_model_backend(self.app_context, line_name=model_line)
        fields = {
            (str(element["NAME"]), "K1")
            for element in backend.get_line_elements(elem1, elem2)
            if str(element.get("TYPE", "")).upper() == "QUAD" and "K1" in element
        }
        return tuple(sorted(fields))

    def _build_emit_model_snapshot_metadata_for_path(self, elem1, elem2, *, model_line=None):
        fields = self._emit_model_snapshot_fields_for_path(elem1, elem2, model_line=model_line)
        if not fields:
            return None
        snapshot = build_model_snapshot(self.app_context, fields)
        return snapshot.as_metadata()

    def _prepare_emit_model_snapshot(self, paras):
        metadata = self._build_emit_model_snapshot_metadata_for_path(
            paras.quad_name,
            paras.flag_name,
            model_line=paras.model_line,
        )
        paras.model_snapshot_metadata = metadata
        paras.model_lattice_overrides = model_snapshot_lattice_overrides(metadata)

    def _scan_metadata_from_paras(self, paras):
        metadata = {
            "schema_version": SCAN_DATA_SCHEMA_VERSION,
            "machine_id": self.machine_profile.machine.id,
            "machine_display_name": self.machine_profile.machine.display_name,
            "backend": self.machine_type,
            "quad": paras.quad_name,
            "flag": paras.flag_name,
            "model_line": paras.model_line,
            "beam_size_source": "local_fit",
            "flag_image_pv": paras.flagImagePV,
            "energy_mev": paras.EnergyMeV,
            "k1_from": paras.k1_from,
            "k1_end": paras.k1_end,
            "k1_steps": paras.k1_steps,
            "samples": paras.samples,
            "settle_time": paras.settle_time,
            "sample_interval": paras.sample_interval,
        }
        model_snapshot = getattr(paras, "model_snapshot_metadata", None)
        if isinstance(model_snapshot, Mapping):
            metadata["model_snapshot"] = dict(model_snapshot)
        return metadata

    def _metadata_path_for_results(self, results_path):
        results_path = Path(results_path)
        if results_path.name == SCAN_RESULTS_FILENAME:
            metadata_path = results_path.parent / METADATA_FILENAME
            if metadata_path.exists():
                return metadata_path
            return results_path.parent / SCAN_RESULTS_META_FILENAME
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

    def _load_scan_results_into_table(self, results_path=None, *, expected_metadata=None):
        if results_path is None:
            results_path = self._latest_scan_results_path()
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
        if isinstance(metadata, Mapping) and isinstance(metadata.get("fit_summary"), Mapping):
            fit_summary = metadata["fit_summary"]
            preferred_method = "leastSquares" if "leastSquares" in fit_summary else "parabolic"
            self.latest_emit_fit_summary = fit_summary.get(preferred_method)
        self._redraw_scan_points_from_table()

    def _load_scan_archive(self):
        paras = self.get_setting()
        if paras is None:
            return
        archive_dir = self._scan_archive_dir()
        legacy_archive_dir = self._legacy_scan_archive_dir()
        if not archive_dir.exists() and legacy_archive_dir.exists():
            archive_dir = legacy_archive_dir
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

    def _latest_twiss_initial_source(self):
        if isinstance(self.twiss_initial_source, Mapping):
            source = dict(self.twiss_initial_source)
            source.setdefault("source_quad", self.comboBox_2.currentText() or None)
            return source
        return {
            "kind": "manual",
            "source_quad": self.comboBox_2.currentText() or None,
        }

    def _append_twiss_result_log(self, result):
        path = self._twiss_results_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "schema_version": "twiss_result_v1",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "machine_id": self.machine_profile.machine.id,
            "machine_display_name": self.machine_profile.machine.display_name,
            "backend": self.machine_type,
            "plane": result.get("plane"),
            "direction": result.get("direction"),
            "from_element": result.get("from_element"),
            "to_element": result.get("to_element"),
            "energy_mev": _finite_float_or_none(result.get("energy_mev")),
            "initial_source": self._latest_twiss_initial_source(),
            "initial_twiss": {
                "beta": _finite_float_or_none(result.get("beta0")),
                "alpha": _finite_float_or_none(result.get("alpha0")),
                "gamma": _finite_float_or_none(result.get("gamma0")),
            },
            "result_twiss": {
                "beta": _finite_float_or_none(result.get("beta")),
                "alpha": _finite_float_or_none(result.get("alpha")),
                "gamma": _finite_float_or_none(result.get("gamma")),
            },
            "transfer_matrix": result.get("matrix"),
        }
        if self.loaded_scan_results_path is not None:
            record["scan_results_path"] = str(Path(self.loaded_scan_results_path))
        elif self.loaded_scan_metadata or self.pending_scan_metadata:
            record["scan_results_path"] = str(self._latest_scan_results_path())
        model_snapshot = result.get("model_snapshot")
        if isinstance(model_snapshot, Mapping):
            record["model_snapshot"] = dict(model_snapshot)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        return path

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
        QMessageBox.warning(self, "Emittance Measurement", message)

    def _warn_twiss(self, message):
        print(message)
        QMessageBox.warning(self, "Twiss Calculation", message)

    def _scan_is_running(self):
        return self.scan is not None and self.scan.isRunning()

    def _twiss_is_running(self):
        return self.twissCal is not None and self.twissCal.isRunning()

    def _on_scan_finished(self):
        self.scan = None
        self.scan_mode = None
        self.pending_scan_metadata = None
        self._refresh_status()
        if self._beam_image_auto_refresh_ready:
            self._schedule_beam_image_refresh()

    def _on_twiss_finished(self):
        self.twissCal = None
        self._refresh_status()

    def _selected_twiss_inverse_map(self):
        return bool(self.twiss_direction_combo.currentData())

    def _selected_twiss_plane(self):
        return self.twiss_plane_combo.currentData() or "xplane"

    def _twiss_element_index(self, element_id):
        index = self.comboBox_3.findText(element_id)
        return index if index >= 0 else None

    def _twiss_path_validation_message(self):
        from_element = self.comboBox_2.currentText()
        to_element = self.comboBox_3.currentText()
        if not from_element or not to_element:
            return "Select From and To elements."
        if from_element == to_element:
            return "From and To must be different."

        from_index = self._twiss_element_index(from_element)
        to_index = self._twiss_element_index(to_element)
        if from_index is None or to_index is None:
            return "Path order is unavailable for the selected elements."

        if self._selected_twiss_inverse_map():
            if from_index <= to_index:
                return "Backward expects To to be upstream of From. Swap elements or choose Forward."
        elif to_index <= from_index:
            return "Forward expects To to be downstream of From. Swap elements or choose Backward."
        return None

    def _format_twiss_path_status(self):
        from_element = self.comboBox_2.currentText() or "--"
        to_element = self.comboBox_3.currentText() or "--"
        direction = "backward" if self._selected_twiss_inverse_map() else "forward"
        plane = self._format_twiss_plane_label(self._selected_twiss_plane())
        return f"Ready: {plane} plane, {direction}, {from_element} -> {to_element}"

    def _update_twiss_path_status(self):
        if not hasattr(self, "twiss_status_edit") or self._twiss_is_running():
            return
        message = self._twiss_path_validation_message()
        self.twiss_status_edit.setText(message or self._format_twiss_path_status())

    def _mark_twiss_initial_manual(self):
        self.twiss_initial_source = {
            "kind": "manual",
            "source_quad": self.comboBox_2.currentText() or None,
        }

    def _latest_fit_source_quad(self):
        for source in (
            self.latest_emit_fit_summary,
            self.loaded_scan_metadata,
            self.pending_scan_metadata,
        ):
            if not isinstance(source, Mapping):
                continue
            quad = source.get("source_quad") or source.get("quad")
            if quad:
                return str(quad)
        current_quad = self.comboBox.currentText()
        return current_quad or None

    def _use_latest_fit_for_twiss(self):
        if not self.latest_emit_fit_summary:
            self._warn_twiss("No emittance fit is available. Run a scan or recalculate first.")
            return

        plane = self._selected_twiss_plane()
        plane_summary = self.latest_emit_fit_summary.get(plane)
        if not isinstance(plane_summary, Mapping):
            self._warn_twiss(f"No latest fit summary is available for {self._format_twiss_plane_label(plane)} plane.")
            return
        if plane_summary.get("status") != "valid":
            message = plane_summary.get("message") or plane_summary.get("status") or "unresolved"
            self._warn_twiss(
                f"Latest {self._format_twiss_plane_label(plane)} plane fit is not valid: "
                f"{_compact_status_text(message)}"
            )
            return

        beta = _finite_float_or_none(plane_summary.get("beta"))
        alpha = _finite_float_or_none(plane_summary.get("alpha"))
        gamma = _finite_float_or_none(plane_summary.get("gamma"))
        if beta is None or alpha is None or gamma is None:
            self._warn_twiss(f"Latest {self._format_twiss_plane_label(plane)} plane fit has incomplete Twiss values.")
            return

        self.lineEdit.setText(f"{beta:.8g}")
        self.lineEdit_3.setText(f"{alpha:.8g}")
        self.lineEdit_6.setText(f"{gamma:.8g}")
        source_quad = self._latest_fit_source_quad()
        if source_quad and not self._set_combo_current_text(self.comboBox_2, source_quad):
            self._warn_twiss(f"Latest fit source element {source_quad} is not available in the Twiss From list.")
            return
        self.twiss_initial_source = {
            "kind": "latest_fit",
            "method": self.latest_emit_fit_summary.get("method"),
            "plane": plane,
            "source_quad": source_quad,
        }
        source_text = f" from {source_quad}" if source_quad else ""
        self.twiss_status_edit.setText(
            f"Loaded latest {self._format_twiss_plane_label(plane)} plane fit{source_text}."
        )

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
            return True
        return False

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

    def _current_flag_pixel_geometry(self, flag_name=None):
        flag_name = flag_name or self.comboBox_4.currentText()
        return resolve_flag_pixel_geometry(
            self.beam_monitor_config,
            "workflows.beam_monitor",
            self.machine_type,
            flag_name,
        )

    def _current_flag_image_extent(self, flag_name=None):
        return _image_extent_from_geometry(self._current_flag_pixel_geometry(flag_name))

    def _twiss_from_choices(self):
        if self.emit_workflow.twiss_quads:
            return list(self.emit_workflow.twiss_quads)

        choices = []
        for preset in self.emit_workflow.presets:
            if preset.quad not in choices:
                choices.append(preset.quad)
        return choices

    def _twiss_to_choices(self):
        choices = [
            element.id
            for element in self.machine_profile.elements
            if element.kind == "quad"
        ]
        return choices or self._twiss_from_choices()

    def _configure_machine_profile(self):
        presets_by_quad = self._emit_presets_by_quad()
        quad_items = list(presets_by_quad)
        self._set_combo_items(self.comboBox, quad_items)

        twiss_from_quads = self._twiss_from_choices()
        twiss_to_quads = self._twiss_to_choices()
        self._set_combo_items(self.comboBox_2, twiss_from_quads)
        self._set_combo_items(self.comboBox_3, twiss_to_quads)

        default_preset = self._find_emit_preset(self.emit_workflow.default_preset)
        self._set_combo_current_text(self.comboBox, default_preset.quad)
        self.updateComboBox4(self.comboBox.currentIndex())
        self._set_combo_current_text(self.comboBox_4, default_preset.flag)
        default_from = twiss_from_quads[0] if twiss_from_quads else None
        if twiss_from_quads:
            self._set_combo_current_text(self.comboBox_2, default_from)
        if twiss_to_quads:
            from_index = (
                twiss_to_quads.index(default_from)
                if default_from in twiss_to_quads
                else -1
            )
            default_to = next(
                (
                    quad
                    for index, quad in enumerate(twiss_to_quads)
                    if index > from_index and quad != default_from
                ),
                next((quad for quad in twiss_to_quads if quad != default_from), twiss_to_quads[0]),
            )
            self._set_combo_current_text(self.comboBox_3, default_to)
        self._sync_emit_preset_defaults()
        self._update_twiss_path_status()

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
        if scan.settle_time is not None:
            self.lineEdit_24.setText(str(scan.settle_time))
        if scan.sample_interval is not None and self.sample_interval_edit is not None:
            self.sample_interval_edit.setText(str(scan.sample_interval))

    def _handle_emit_flag_changed(self, index):
        del index
        self._sync_emit_preset_defaults()
        self._draw_beam_image_placeholder()
        if self._beam_image_auto_refresh_ready:
            self._schedule_beam_image_refresh()

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
        self._draw_beam_image_placeholder()
        if self._beam_image_auto_refresh_ready:
            self._schedule_beam_image_refresh()


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
            para.flagImagePV = resolve_channel(self.machine_profile, para.flag_name, "image", self.machine_type)
            geometry = self._current_flag_pixel_geometry(para.flag_name)
            para.flag_pixel_shape = geometry.shape
            para.flag_image_extent = _image_extent_from_geometry(geometry)
            para.model_line = preset.model_line
            para.app_context = self.app_context

            para.k1_from  = float(self.lineEdit_7.text())
            para.k1_end   = float(self.lineEdit_8.text())
            para.k1_steps = self._parse_positive_int(self.lineEdit_9.text(), "K1 steps")
            para.samples  = self._parse_positive_int(self.lineEdit_10.text(), "Samples per step")
            para.EnergyMeV = float(self.lineEdit_2.text())
            para.settle_time = self._parse_non_negative_float(self.lineEdit_24.text(), "Settle time")
            para.sample_interval = self._parse_non_negative_float(
                self.sample_interval_edit.text(),
                "Sample interval",
            )
            if para.EnergyMeV <= 0:
                raise ValueError("Energy must be positive.")
            return para
        except (MachineProfileError, ValueError) as exc:
            self._warn(str(exc))
            return None

    def refresh_current_beam_image_fit(self, paras=None, *, show_warning=True):
        if paras is None:
            paras = self.get_setting()
            if paras is None:
                return False
        try:
            image, fit_result = _read_flag_image_fit(
                paras.flagImagePV,
                paras.flag_pixel_shape,
                paras.flag_image_extent,
            )
        except RuntimeError as exc:
            self._draw_beam_image_placeholder("PRF image unavailable")
            if show_warning:
                self._warn(str(exc))
            return False

        self._display_beam_image_fit(
            paras.flag_name,
            image,
            fit_result,
            extent=paras.flag_image_extent,
        )
        if not fit_result.valid:
            if show_warning:
                detail = f": {fit_result.message}" if fit_result.message else ""
                self._warn(f"Current PRF image fit is not valid ({fit_result.status}){detail}.")
            return False
        return True

    def _auto_refresh_beam_image_fit(self):
        if not self._beam_image_auto_refresh_ready:
            return
        if self._scan_is_running():
            return
        if not hasattr(self, "beam_image_auto_refresh_checkbox"):
            return
        if not self.beam_image_auto_refresh_checkbox.isChecked():
            return
        self.refresh_current_beam_image_fit(show_warning=False)

    def _update_beam_image_auto_refresh(self, *args):
        del args
        if not hasattr(self, "beam_image_auto_refresh_checkbox"):
            return
        if self.beam_image_auto_refresh_checkbox.isChecked():
            if not self.beam_image_timer.isActive():
                self.beam_image_timer.start()
        else:
            self.beam_image_timer.stop()

    def _schedule_beam_image_refresh(self):
        QTimer.singleShot(
            250,
            lambda: self.refresh_current_beam_image_fit(show_warning=False),
        )
 
    def startScan(self):
        if not self._require_model_backend_available("Emit measurement scan"):
            return
        if self._scan_is_running():
            print("Scan is already running. Stop it before starting a new scan.")
            return

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
        if not self.refresh_current_beam_image_fit(self.paras):
            return
        try:
            self._prepare_emit_model_snapshot(self.paras)
        except MachineProfileError as exc:
            self._warn(str(exc))
            return
        self.display({"clear": True, "preserve_beam_image": True})
        self.paras.recal = False
        self.paras.clear = False 
        self.paras.scan_metadata = self._scan_metadata_from_paras(self.paras)
        self.paras.scan_latest_dir = self._scan_latest_dir()
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
            self.scan_mode = "stopping"
            self._refresh_status()
            self.scan.stop()
            if not self.scan.wait(3000):
                print("Timed out waiting for scan thread to stop; scan is still stopping.")
                self._refresh_status()
                return
            print("Scan thread stopped.")
        self.scan_mode = None
        self._refresh_status()

    def recalculate(self):
        if not self._require_model_backend_available("Emit recalculation"):
            return
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

        model_snapshot = None
        if isinstance(self.loaded_scan_metadata, Mapping):
            candidate = self.loaded_scan_metadata.get("model_snapshot")
            if isinstance(candidate, Mapping):
                model_snapshot = candidate
        if model_snapshot is None:
            self._warn(
                "Scan metadata has no model_snapshot; recalculation will use the current model snapshot."
            )
            try:
                self._prepare_emit_model_snapshot(self.paras)
            except MachineProfileError as exc:
                self._warn(str(exc))
                return
        else:
            archived_overrides = model_snapshot_lattice_overrides(model_snapshot)
            self.paras.model_snapshot_metadata = dict(model_snapshot)
            self.paras.model_lattice_overrides = archived_overrides
            if archived_overrides is None:
                self._warn(
                    "Scan metadata model_snapshot has no usable lattice overrides; "
                    "recalculation will use the current model snapshot."
                )
                try:
                    self._prepare_emit_model_snapshot(self.paras)
                except MachineProfileError as exc:
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
        self.paras.scan_latest_dir = self._scan_latest_dir()
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
        if not self._require_model_backend_available("Twiss calculation", title="Twiss Calculation"):
            return
        if self._twiss_is_running():
            self._warn_twiss("Twiss calculation is already running.")
            return

        path_error = self._twiss_path_validation_message()
        if path_error is not None:
            self.twiss_status_edit.setText("Invalid path")
            self._warn_twiss(path_error)
            return

        try:
            beta0 = float(self.lineEdit.text())
            alpha0 = float(self.lineEdit_3.text())
            gamma0 = float(self.lineEdit_6.text())
            energy = float(self.lineEdit_2.text())
        except ValueError:
            self.twiss_status_edit.setText("Invalid input")
            self._warn_twiss("Twiss input values must be numeric.")
            return
        if energy <= 0:
            self.twiss_status_edit.setText("Invalid input")
            self._warn_twiss("Energy must be positive.")
            return
        if beta0 <= 0:
            self.twiss_status_edit.setText("Invalid input")
            self._warn_twiss("Initial beta must be positive.")
            return
        if gamma0 <= 0:
            self.twiss_status_edit.setText("Invalid input")
            self._warn_twiss("Initial gamma must be positive.")
            return

        para = {}
        para["quad1"] = self.comboBox_2.currentText()
        para["quad2"] = self.comboBox_3.currentText()

        expected_gamma = (1.0 + alpha0**2) / beta0
        gamma_delta = abs(gamma0 - expected_gamma)
        gamma_scale = max(1.0, abs(expected_gamma))
        if gamma_delta / gamma_scale > 0.05:
            QMessageBox.warning(
                self,
                "Twiss Calculation",
                (
                    "Initial gamma is not consistent with beta and alpha. "
                    f"Expected gamma is about {expected_gamma:.6g} 1/m."
                ),
            )
        
        para["inverse_map"] = self._selected_twiss_inverse_map()
        para["direction"] = "backward" if para["inverse_map"] else "forward"

        para["plane"] = self._selected_twiss_plane()
        
        para["beta0"] = beta0
        para["alpha0"] = alpha0
        para["gamma0"] = gamma0
        para["EnergyMeV"] = energy
        para["app_context"] = self.app_context
        para["from_element"] = para["quad1"]
        para["to_element"] = para["quad2"]
        try:
            model_snapshot = self._build_emit_model_snapshot_metadata_for_path(
                para["quad1"],
                para["quad2"],
            )
        except MachineProfileError as exc:
            self.twiss_status_edit.setText("Model snapshot failed")
            self._warn_twiss(str(exc))
            return
        para["model_snapshot_metadata"] = model_snapshot
        para["model_lattice_overrides"] = model_snapshot_lattice_overrides(model_snapshot)

        self.latest_twiss_summary = {
            "status": "running",
            "plane": para["plane"],
            "direction": para["direction"],
            "from_element": para["from_element"],
            "to_element": para["to_element"],
            "energy_mev": energy,
            "beta0": beta0,
            "alpha0": alpha0,
            "gamma0": gamma0,
        }
        for field in (self.lineEdit_17, self.lineEdit_21, self.lineEdit_22):
            field.setText("")
        self.twiss_map_edit.setPlainText("Calculating transfer map...")
        self.twiss_status_edit.setText(
            f"Running: {self._format_twiss_status_tooltip(self.latest_twiss_summary)}"
        )
        self.twissCal = twissCalThread(para)
        self.twissCal.trigger.connect(self.showTwiss)
        self.twissCal.finished.connect(self._on_twiss_finished)
        self.twissCal.start()
        self._refresh_status()

    def display(self,dict):
        if "error" in dict:
            self.latest_emit_fit_summary = {
                "method": "scan",
                "status": "error",
                "message": str(dict["error"]),
            }
            self._refresh_status()
            self._warn(dict["error"])
            return
        if "clear" in dict:
            # clear all the results
            if dict.get("preserve_beam_image"):
                self._draw_scan_fit_placeholder_plots()
            else:
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

            self.lineEdit_17.setText("")
            self.lineEdit_21.setText("")
            self.lineEdit_22.setText("")
            self.twiss_status_edit.setText("Idle")
            self.twiss_map_edit.setPlainText("No Twiss calculation yet")

            self._clear_scan_points()
            self.loaded_scan_metadata = None
            self.loaded_scan_results_path = None
            self.latest_emit_fit_summary = None
            self.latest_twiss_summary = None
            self.twiss_initial_source = {"kind": "manual"}
            self._refresh_status()
            
            return

        if "beam_image" in dict and "beam_fit" in dict:
            self._display_beam_image_fit(
                dict.get("flag", self.comboBox_4.currentText()),
                dict["beam_image"],
                dict["beam_fit"],
                k1=dict.get("k1"),
                extent=dict.get("beam_extent"),
            )

        if dict["method"] == None:
            if "sigx" not in dict or "sigy" not in dict:
                return
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
            self.latest_emit_fit_summary = dict.get("fit_summary")
            self._display_parabolic_plane("xplane", dict["xplane"])
            self._display_parabolic_plane("yplane", dict["yplane"])

        elif dict["method"] == "leastSquares":
            self.latest_emit_fit_summary = dict.get("fit_summary")
            self._display_least_square_plane("xplane", dict["xplane"])
            self._display_least_square_plane("yplane", dict["yplane"])

        else:
            print(f"Error, unexpected result method: {dict.get('method')}")
            return
        self._refresh_status()

    def _display_parabolic_plane(self, plane, result):
        palette = self._palette()
        if plane == "xplane":
            widget = self.widget_2
            xlabel = "$-K= K_1 L_q (m^{-1})$"
            ylabel = "$sigx^2 (mm^2)$"
            plot_sign = -1
            fields = (self.lineEdit_11, self.lineEdit_12, self.lineEdit_13, self.lineEdit_14, self.lineEdit_15)
            text_field = self.lineEdit_16
            curve_name = "sigx^2"
        else:
            widget = self.widget_9
            xlabel = "$K= K_1 L_q (m^{-1})$"
            ylabel = "$sigy^2 (mm^2)$"
            plot_sign = 1
            fields = (self.lineEdit_39, self.lineEdit_35, self.lineEdit_40, self.lineEdit_36, self.lineEdit_38)
            text_field = self.lineEdit_37
            curve_name = "sigy^2"

        widget.axes.clear()
        self._style_axes(widget, xlabel, ylabel)
        if all(key in result for key in ("xx", "yy", "err")):
            xx = result["xx"]
            yy = result["yy"]
            err = result["err"]
            widget.axes.errorbar(
                plot_sign * xx,
                yy,
                err,
                fmt=".",
                color=palette["plot_point"],
                ecolor=palette["plot_error"],
                capsize=3,
            )
            if result.get("fit_yy") is not None:
                widget.axes.plot(
                    plot_sign * xx,
                    result["fit_yy"],
                    "--",
                    color=palette["plot_fit"],
                    label="fitting curve",
                )
                legend = widget.axes.legend(frameon=False)
                if legend is not None:
                    for text in legend.get_texts():
                        text.set_color(palette["plot_text"])
        else:
            widget.axes.text(
                0.5,
                0.5,
                _compact_status_text(result.get("message", "fit failed"), limit=60),
                transform=widget.axes.transAxes,
                ha="center",
                va="center",
                color=palette["muted_fg"],
                fontsize=10,
            )
        widget.canvas.draw()

        if result.get("status") == "valid":
            for field, key in zip(fields, ("ex", "beta", "alpha", "gamma", "exn")):
                field.setText(str(result.get(key)))
            text_field.setText(
                f"{curve_name}={result.get('a')}K^2+{result.get('b')}K+{result.get('c')}"
            )
            return

        fields[0].setText("unresolved")
        for field in fields[1:]:
            field.setText("--")
        text_field.setText(_compact_status_text(result.get("message", result.get("status"))))

    def _display_least_square_plane(self, plane, result):
        if plane == "xplane":
            fields = (self.lineEdit_4, self.lineEdit_5, self.lineEdit_20, self.lineEdit_19, self.lineEdit_18)
        else:
            fields = (self.lineEdit_41, self.lineEdit_42, self.lineEdit_43, self.lineEdit_44, self.lineEdit_45)

        if result.get("status") == "valid":
            for field, key in zip(fields, ("ex", "exn", "beta", "alpha", "gamma")):
                field.setText(str(result.get(key)))
            return

        fields[0].setText("unresolved")
        for field in fields[1:-1]:
            field.setText("--")
        fields[-1].setText(_compact_status_text(result.get("message", result.get("status"))))

    def showTwiss(self, dict):
        if "error" in dict:
            message = str(dict["error"])
            self.latest_twiss_summary = {
                "status": "error",
                "plane": dict.get("plane"),
                "direction": dict.get("direction"),
                "from_element": dict.get("from_element"),
                "to_element": dict.get("to_element"),
                "message": message,
            }
            self.lineEdit_17.setText("--")
            self.lineEdit_21.setText("--")
            self.lineEdit_22.setText("--")
            self.twiss_map_edit.setPlainText("--")
            self.twiss_status_edit.setText("Error")
            self._refresh_status()
            self._warn_twiss(message)
            return
        beta = round(dict["beta"], 2)
        alpha = round(dict["alpha"], 2)
        gamma = round(dict["gamma"], 2)
        matrix_summary = dict.get("matrix")

        self.latest_twiss_summary = {
            "status": "valid",
            "plane": dict.get("plane"),
            "direction": dict.get("direction"),
            "from_element": dict.get("from_element"),
            "to_element": dict.get("to_element"),
            "energy_mev": dict.get("energy_mev"),
            "beta0": dict.get("beta0"),
            "alpha0": dict.get("alpha0"),
            "gamma0": dict.get("gamma0"),
            "beta": beta,
            "alpha": alpha,
            "gamma": gamma,
            "matrix": matrix_summary,
        }
        self.lineEdit_17.setText(str(beta))
        self.lineEdit_21.setText(str(alpha))
        self.lineEdit_22.setText(str(gamma))
        self.twiss_map_edit.setPlainText(_format_matrix_summary(matrix_summary) or "No transfer map returned")
        status_text = self._format_twiss_status_tooltip(self.latest_twiss_summary)
        try:
            log_path = self._append_twiss_result_log(dict)
        except Exception as exc:
            print(f"Warning: failed to write Twiss result log: {exc}")
        else:
            status_text = f"{status_text}; logged to {log_path.name}"
        self.twiss_status_edit.setText(status_text)
        self._refresh_status()

    def closeEvent(self, event):
        self.beam_image_timer.stop()
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
            context = {
                "plane": self.input.get("plane"),
                "direction": self.input.get("direction"),
                "from_element": self.input.get("from_element", quad1),
                "to_element": self.input.get("to_element", quad2),
                "energy_mev": self.input.get("EnergyMeV"),
                "model_snapshot": self.input.get("model_snapshot_metadata"),
            }

            twiss0={}
            twiss0["beta0"]  = self.input["beta0"]
            twiss0["alpha0"] = self.input["alpha0"]
            twiss0["gamma0"] = self.input["gamma0"]

            plane = self.input["plane"]
            inverse = self.input["inverse_map"]

            trans = transfer(
                self.input["EnergyMeV"],
                app_context=self.input["app_context"],
                lattice_overrides=self.input.get("model_lattice_overrides"),
            )
            if inverse:
                matrix = np.linalg.inv(trans.get_map(quad2, quad1, seq="ent2exit"))
            else:
                matrix = trans.get_map(quad1, quad2, seq="ent2exit")
            twiss1 = _twiss_from_transfer_matrix(matrix, twiss0, plane=plane)
            twiss1.update(context)
            twiss1["beta0"] = twiss0["beta0"]
            twiss1["alpha0"] = twiss0["alpha0"]
            twiss1["gamma0"] = twiss0["gamma0"]
            twiss1["matrix"] = _plane_transfer_matrix_summary(matrix, plane=plane)

            self.trigger.emit(twiss1)
        except Exception as exc:
            error_payload = {
                "error": str(exc),
                "plane": self.input.get("plane"),
                "direction": self.input.get("direction"),
                "from_element": self.input.get("from_element"),
                "to_element": self.input.get("to_element"),
            }
            self.trigger.emit(error_payload)

class scanThread(QThread):

    trigger = pyqtSignal(dict)

    def __init__(self,paras):
        super().__init__()
        self.quad_name  = paras.quad_name.upper() 
        self.flag_name  = paras.flag_name.upper() 
        self.quadPV     = paras.quadPV    
        self.flagImagePV = paras.flagImagePV
        self.flag_pixel_shape = paras.flag_pixel_shape
        self.flag_image_extent = paras.flag_image_extent
        self.k1_from    = paras.k1_from   
        self.k1_end     = paras.k1_end    
        self.k1_steps   = paras.k1_steps  
        self.samples    = paras.samples   
        self.EnergyMeV  = paras.EnergyMeV
        self.settle_time = paras.settle_time
        self.sample_interval = paras.sample_interval
        self.model_line = paras.model_line
        self.app_context = paras.app_context
        self.model_snapshot_metadata = getattr(paras, "model_snapshot_metadata", None)
        self.model_lattice_overrides = getattr(paras, "model_lattice_overrides", None)
        self.quad_length = None

        self.recal      = paras.recal 
        self.recal_points = getattr(paras, "recal_points", None)
        self.scan_metadata = getattr(paras, "scan_metadata", None)
        self.scan_archive_dir = Path(
            getattr(paras, "scan_archive_dir", SCAN_ARCHIVE_ROOT / "unknown" / "unknown")
        )
        self.scan_latest_dir = Path(
            getattr(paras, "scan_latest_dir", self.scan_archive_dir / "latest")
        )
        self.scan_results_path = self.scan_latest_dir / SCAN_RESULTS_FILENAME
        self.scan_results_meta_path = self.scan_latest_dir / METADATA_FILENAME
        self.legacy_scan_results_meta_path = self.scan_latest_dir / SCAN_RESULTS_META_FILENAME
        self.scan_metadata_paths = []
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
                    if not self._sleep_or_stop(self.settle_time):
                        print("Stop scan, quad is back to initial values, K1=",iniK1)
                        return
                    for j in range(self.samples):
                        if self.is_running == True:
                            print("Quad K1=",k1)
                            if j > 0 and not self._sleep_or_stop(self.sample_interval):
                                print("Stop scan, quad is back to initial values, K1=",iniK1)
                                return
                            
                            image, fit_result = _read_flag_image_fit(
                                self.flagImagePV,
                                self.flag_pixel_shape,
                                self.flag_image_extent,
                            )
                            point = {
                                "method": None,
                                "k1": k1,
                                "flag": self.flag_name,
                                "beam_image": image,
                                "beam_fit": fit_result,
                                "beam_extent": self.flag_image_extent,
                            }
                            if not fit_result.valid:
                                self.trigger.emit(point)
                                detail = f": {fit_result.message}" if fit_result.message else ""
                                raise RuntimeError(
                                    f"Flag image fit failed for {self.flag_name} ({fit_result.status}){detail}."
                                )
                            tmp2 = float(fit_result.sigx_mm)
                            tmp3 = float(fit_result.sigy_mm)
                            print("sigmax=",tmp2,"sigamy=",tmp3)

                            point["sigx"] = tmp2
                            point["sigy"] = tmp3

                            self.k1l.append(k1)
                            self.sigxl.append(tmp2)
                            self.sigyl.append(tmp3)

                            self.trigger.emit(point)
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
                    source_path = self.scan_results_path
                    print(f"Loading {source_path} ...")
                    if not source_path.exists():
                        raise RuntimeError(f"{source_path} not found. Run a scan before recalculating.")
                    with open(source_path,"r") as f:
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
                lattice_overrides=self.model_lattice_overrides,
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
                fit_summary = {
                    "parabolic": {
                        "method": "parabolic",
                        "status": "skipped",
                        "message": "scan points cannot be reshaped into equal samples per K1",
                    }
                }
            else:
                tmp["method"] = "parabolic"
                tmp["xplane"] = self._parabolic_plane_result(k1l, sigxl, m11, m12, "xplane")
                tmp["yplane"] = self._parabolic_plane_result(-k1l, sigyl, m33, m34, "yplane")
                parabolic_summary = _method_fit_summary("parabolic", tmp["xplane"], tmp["yplane"])
                fit_summary = {"parabolic": parabolic_summary}
                tmp["fit_summary"] = parabolic_summary
                self.trigger.emit(tmp)
                if not self._sleep_or_stop(2):
                    return
                print(f"Parabolic fitting finished: {parabolic_summary['status']}")

            # Least squares method
            # ========================
            tmpx, tmpy = self.leastSquare()
            
            # X-plane
            tmp["method"]    = "leastSquares"

            tmpxx = self._plane_result_payload(tmpx)
            tmp["xplane"] = tmpxx

            # Y-plane
            tmpyy = self._plane_result_payload(tmpy)
            tmp["yplane"] = tmpyy
            least_squares_summary = _method_fit_summary("leastSquares", tmpxx, tmpyy)
            fit_summary["leastSquares"] = least_squares_summary
            tmp["fit_summary"] = least_squares_summary
            tmp["all_fit_summary"] = fit_summary

            self.trigger.emit(tmp)
            self._write_scan_fit_summary(fit_summary)
            print(f"leastSquare finished: {least_squares_summary['status']}")
           
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

        self.scan_latest_dir.mkdir(parents=True, exist_ok=True)
        np.savetxt(self.scan_results_path, data, fmt="%.6e")
        metadata_text = json.dumps(metadata, indent=2, sort_keys=True)
        self.scan_results_meta_path.write_text(metadata_text, encoding="utf-8")
        self.legacy_scan_results_meta_path.write_text(metadata_text, encoding="utf-8")

        self.scan_archive_dir.mkdir(parents=True, exist_ok=True)
        archive_dir = self.scan_archive_dir / self._scan_archive_stem()
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = archive_dir / SCAN_RESULTS_FILENAME
        archive_meta_path = archive_dir / METADATA_FILENAME
        np.savetxt(archive_path, data, fmt="%.6e")
        archive_meta_path.write_text(metadata_text, encoding="utf-8")
        self.scan_metadata_paths = [
            self.scan_results_meta_path,
            self.legacy_scan_results_meta_path,
            archive_meta_path,
        ]
        print(f"Saved latest scan results: {self.scan_results_path}")
        print(f"Saved scan archive: {archive_path}")

    def _write_scan_fit_summary(self, fit_summary):
        if not self.scan_metadata_paths:
            return
        for metadata_path in self.scan_metadata_paths:
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                metadata = {}
            metadata["fit_summary"] = fit_summary
            metadata["fit_summary_updated_at"] = datetime.now().isoformat(timespec="seconds")
            metadata_path.write_text(
                json.dumps(metadata, indent=2, sort_keys=True),
                encoding="utf-8",
            )

    def _plane_result_payload(self, result):
        payload = {
            "status": getattr(result, "status", _status_from_plane_result(result)),
            "message": getattr(result, "message", ""),
            "ex": result.ex,
            "exn": result.exn,
            "beta": result.beta,
            "alpha": result.alpha,
            "gamma": result.gamma,
        }
        determinant = getattr(result, "determinant", None)
        if determinant is not None:
            payload["determinant"] = determinant
        return payload

    def _parabolic_plane_result(self, k1l, sigxl, m11, m12, plane):
        try:
            return self.parabolicfitting(k1l, sigxl, m11, m12)
        except Exception as exc:
            print(f"Warning: parabolic fitting failed for {plane}: {exc}")
            return _invalid_plane_result("fit_failed", str(exc))

    def leastSquare(self):
        k1l  = np.array(self.k1l)
        sigx = np.array(self.sigxl)    #[mm]
        sigy = np.array(self.sigyl)    #[mm]
        
        sigxx = sigx**2
        sigyy = sigy**2
        
        A0_x = []
        A0_y = []
        trans = transfer(
            self.EnergyMeV,
            app_context=self.app_context,
            model_line=self.model_line,
            lattice_overrides=self.model_lattice_overrides,
        )
        for k1 in k1l:
            # get the transfer map 
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
        determinant = None
        try:
            A = np.asmatrix( np.reshape(A0,(len(k1l),3)) )
            b = np.asmatrix(sigxx).transpose()

            AA = A.transpose()*A
            bb = A.transpose()*b

            xx = np.linalg.solve(AA,bb)

            sig11 = xx[0,0]
            sig12 = xx[1,0]
            sig22 = xx[2,0]

            determinant = float(sig11 * sig22 - sig12**2)
            if not math.isfinite(determinant) or determinant <= 0:
                raise ValueError(f"non-physical beam matrix determinant={determinant:.6g}")
            ex = math.sqrt(determinant)
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
            tmp.status = "valid"
            tmp.message = ""
            tmp.determinant = determinant

        except (ValueError, ZeroDivisionError, np.linalg.LinAlgError) as exc:
            print(f"Warning: least-squares emittance solve failed: {exc}")
            tmp = structData()
            tmp.ex    = None 
            tmp.exn   = None 
            tmp.beta  = None 
            tmp.alpha = None 
            tmp.gamma = None 
            tmp.status = "non_physical" if "non-physical" in str(exc) else "failed"
            tmp.message = str(exc)
            tmp.determinant = determinant

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

        tmp["a"] = round(a,2)
        tmp["b"] = round(b,2)
        tmp["c"] = round(c,2)

        discriminant = float(4 * a * c - b**2)
        tmp["discriminant"] = discriminant
        if not math.isfinite(discriminant) or discriminant <= 0:
            tmp.update(
                _invalid_plane_result(
                    "non_physical",
                    "non-physical emittance discriminant "
                    f"4ac-b^2={discriminant:.6g}",
                )
            )
            return tmp
        if m12 == 0:
            tmp.update(
                _invalid_plane_result(
                    "failed",
                    "cannot solve emittance because transfer matrix m12 is zero",
                )
            )
            return tmp

        fac = math.sqrt(discriminant)
        ex    = fac/(2*m12**2)
        alpha = (-b+2*a*m11/m12)/fac
        beta  = 2*a/fac
        if not math.isfinite(beta) or beta == 0:
            tmp.update(_invalid_plane_result("failed", f"invalid beta={beta}"))
            return tmp
        gamma = (1+alpha**2)/beta
        
        gam0 = self.EnergyMeV*1e6/ELECTRON_MASS_EV
        exn = ex*gam0
        
        #print("exn,beta,alpha,gamma",exn,beta,alpha,gamma)

        tmp["ex"]    = round(ex   ,4) 
        tmp["exn"]   = round(exn  ,2) 
        tmp["beta"]  = round(beta ,2)
        tmp["alpha"] = round(alpha,2)
        tmp["gamma"] = round(gamma,2)
        tmp["status"] = "valid"
        tmp["message"] = ""

        return tmp
    
    def stop(self):
        self.is_running = False

class transfer:
    def __init__(self,EnergyMeV=None, app_context=None, model_line=None, lattice_overrides=None):
        self.energy = EnergyMeV
        self.app_context = app_context or load_app_context("emit_measure")
        self.model_line = model_line
        self.lattice_overrides = lattice_overrides
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
            lattice_overrides=self.lattice_overrides,
        )

    def get_map(self, elem1, elem2, k1=None, seq="exit2exit"):
        return self.model_backend.get_map(
            elem1,
            elem2,
            k1=k1,
            lattice_overrides=self.lattice_overrides,
            seq=seq,
        )

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
