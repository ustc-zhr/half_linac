
import sys
import time
import numpy as np
import os
import sdds
import math
import json
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
from scipy.interpolate import UnivariateSpline
from scipy.optimize import curve_fit
from epics import caget, caget_many, caput, caput_many, PV

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gui import Ui_MainWindow
from half_linac.src.apps.energy_spectrum.get_energy0 import get_energy0
from half_linac.src.apps.energy_spectrum.esa_auto_tuner import ESA_AutoTuner
from half_linac.src.shared.elegant_backend import ElegantParser
from half_linac.src.shared.elegant_runtime import run_elegant_input
from half_linac.src.shared.machine_profile import (
    MachineProfileError,
    get_workflow,
    list_elements,
    load_app_context,
    require_workflow_write_allowed,
    resolve_channel,
    workflow_writes_allowed,
)
# 会使用到VM计算η和twiss (不具有一般性)


HEADER_ACTION_HEIGHT = 32
DEFAULT_DESIGN_ETA = 0.7484210850804714  # [m]

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
    "plot_trace": "#78d5e3",
    "plot_fit": "red",
    "plot_energy": "#6cb6ff",
    "status_strip_bg": "#131c22",
    "status_strip_border": "#2a3943",
    "status_separator": "#31424d",
    "status_item_idle_bar": "#4f6270",
    "status_title_fg": "#8ea0ad",
    "metric_active_fg": "#45d0bc",
    "metric_warning_fg": "#e4b86f",
    "metric_idle_fg": "#c8d2da",
    "metric_readout_fg": "#86e8f2",
    "metric_label_fg": "#f3efe3",
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
    "plot_trace": "#2f9aad",
    "plot_fit": "red",
    "plot_energy": "#2f7dc5",
    "status_strip_bg": "#f7f1e8",
    "status_strip_border": "#ddd2c4",
    "status_separator": "#ddd4c7",
    "status_item_idle_bar": "#c8bfb3",
    "status_title_fg": "#7c7368",
    "metric_active_fg": "#2d7f6d",
    "metric_warning_fg": "#a97118",
    "metric_idle_fg": "#4e5a62",
    "metric_readout_fg": "#1f73c9",
    "metric_label_fg": "#2d3940",
}


def build_energy_spectrum_theme(palette):
    theme_values = dict(palette, header_action_height=HEADER_ACTION_HEIGHT)
    return """
QMainWindow, QWidget#centralwidget {{
    background-color: {window_bg};
    color: {window_fg};
    font-family: "IBM Plex Sans", "Source Han Sans SC", "Segoe UI", sans-serif;
}}

QFrame#summaryPanel {{
    background-color: {summary_bg};
    border: 1px solid {summary_border};
    border-radius: 14px;
}}

QFrame#plotCard, QGroupBox#workspaceCard {{
    background-color: {panel_bg};
    border: 1px solid {panel_border};
    border-radius: 14px;
}}

QGroupBox#workspaceCard {{
    margin-top: 22px;
    padding-top: 8px;
    font-weight: 700;
}}

QGroupBox#workspaceCard::title {{
    subcontrol-origin: margin;
    left: 14px;
    top: 5px;
    padding: 0;
    color: {summary_title_fg};
    font-size: 14px;
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

QLabel#metricValue {{
    color: {metric_readout_fg};
    font-size: 18px;
    font-weight: 700;
}}

QLabel[role="metricLabel"] {{
    color: {metric_label_fg};
    font-size: 12px;
    font-weight: 700;
    background: transparent;
    border: none;
}}

QLabel[role="focusField"] {{
    color: {metric_label_fg};
    font-size: 11px;
    font-weight: 700;
    background: transparent;
    border: none;
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

QPushButton[tight="true"] {{
    padding: 4px 10px;
    min-height: 22px;
    font-size: 11px;
}}

QLineEdit, QComboBox, QDoubleSpinBox {{
    background-color: {input_bg};
    border: 1px solid {input_border};
    border-radius: 10px;
    color: {input_fg};
    padding: 7px 10px;
    min-height: 18px;
    selection-background-color: {metric_active_fg};
}}

QLineEdit[dense="true"], QComboBox[dense="true"], QDoubleSpinBox[dense="true"] {{
    padding: 5px 8px;
    min-height: 14px;
    font-size: 11px;
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
    color: {window_fg};
    font-size: 12px;
    font-weight: 600;
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

QSlider::groove:horizontal {{
    height: 5px;
    border-radius: 2px;
    background: {input_border};
}}

QSlider::handle:horizontal {{
    width: 16px;
    margin: -6px 0;
    border-radius: 8px;
    background: {metric_active_fg};
    border: 1px solid {button_border};
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


class SpectrumStatusStrip(QWidget):
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


class NullPV:
    def __init__(self, name):
        self.pvname = name

    def get(self, *args, **kwargs):
        return None


class ESAAutoTuneThread(QThread):
    progress = pyqtSignal(dict)
    trigger = pyqtSignal(dict)

    def __init__(
        self,
        *,
        flag_pv_obj,
        flag_pixel,
        bend_pv,
        remove_bg,
        bg_image,
        bend_scan,
        app_context,
        parent=None,
    ):
        super().__init__(parent)
        self.flag_pv_obj = flag_pv_obj
        self.flag_pixel = tuple(flag_pixel)
        self.bend_pv = bend_pv
        self.remove_bg = remove_bg
        self.bg_image = bg_image
        self.bend_scan = dict(bend_scan)
        self.app_context = app_context

    def run(self):
        try:
            require_workflow_write_allowed(
                self.app_context,
                "energy_spectrum",
                "ESA auto tune",
            )
            tuner = ESA_AutoTuner(
                flag_pv_obj=self.flag_pv_obj,
                flag_pixel=self.flag_pixel,
                bend_pv=self.bend_pv,
                progress_callback=self.progress.emit,
                remove_bg=self.remove_bg,
                bg_image=self.bg_image,
            )
            best_current = tuner.run(
                B_min=float(self.bend_scan.get("min", 0)),
                B_max=float(self.bend_scan.get("max", 200)),
                coarse_steps=int(self.bend_scan.get("coarse_steps", 40)),
                fine_steps=int(self.bend_scan.get("fine_steps", 15)),
            )
            self.trigger.emit(
                {
                    "ok": best_current is not None,
                    "best_current": best_current,
                    "status": tuner.get_last_status(),
                }
            )
        except Exception as exc:
            self.trigger.emit(
                {
                    "ok": False,
                    "status": "FAILED",
                    "error": str(exc),
                }
            )

class EnergySpectrumApp(QMainWindow,Ui_MainWindow):
    """
    a gui window for energ spectrum analysis
    """

    def __init__(self):
        super().__init__()
        self.setupUi(self)
        self.app_context = load_app_context("energy_spectrum")
        self.machine_profile = self.app_context.profile
        self.control_backend = self.app_context.control_backend.name
        self.energy_config = self._load_energy_spectrum_config()
        self._pv_available = False
        self._pv_error = None
        self._model_error = None
        self._model_text = "Waiting"
        self._model_tone = "subtle"
        self._model_tooltip = None
        try:
            self.energy_model_config = self._load_energy_model_config()
        except MachineProfileError as exc:
            self.energy_model_config = None
            self._model_error = str(exc)
            self._model_text = "Unavailable"
            self._model_tone = "warning"
            self._model_tooltip = f"Model backend unavailable: {self._model_error}"
        self.start_elements = self._build_start_elements()
        self.energy_set_pv = self._load_energy_set_pv()
        self.esa_quad_ids = tuple(self.energy_config["esa_quads"])
        self.esa_quad_pvs = {
            element_id: resolve_channel(self.app_context, element_id, "k1")
            for element_id in self.esa_quad_ids
        }
        self.bend_pv = resolve_channel(
            self.app_context,
            self.energy_config["bend_element"],
            self.energy_config["bend_channel"],
        )

        self.current_theme = "dark"
        self._auto_tune_text = "Idle"
        self._auto_tune_tone = "subtle"

        self.colorbar = None
        self.sigx = None
        self.sigy = None
        self.bg_image = None
        self.auto_tune_thread = None

        self._configure_window()

        # initialize flag PV according to real machine or VM
        self.init_ESAflag()
        self._build_shell()

        # refresh plot with timer (the default frequency: 1Hz)
        self.setup_timer()

        # ESA 入口处束团参数
        self.with_emit = False # 默认不考虑发射度
        self.remove_bg = False # 默认不去背景

        self.beta_flag = 0
        self.emi_flag = 0
        self.eta_flag = 0

        self._connect_signals()
        self._refresh_model_controls()
        self._draw_placeholder_views()
        if self._model_available():
            self.cal_disp()
        else:
            self._use_design_eta(tooltip=self._model_tooltip)
            self._refresh_status()
        self.fit_method = self.comboBox_fitmethod.currentText()
        self.ESA_running()

    def _load_energy_spectrum_config(self):
        workflow = dict(get_workflow(self.machine_profile, "energy_spectrum"))
        required_keys = (
            "flag_element",
            "flag_image_channel",
            "bend_element",
            "bend_channel",
            "esa_quads",
            "flag_pixel_shape",
            "flag_pixel_width_mm",
        )
        for key in required_keys:
            if key not in workflow:
                raise MachineProfileError(
                    f"workflows.energy_spectrum is missing required key {key!r}."
                )
        if not isinstance(workflow["esa_quads"], list) or not workflow["esa_quads"]:
            raise MachineProfileError("workflows.energy_spectrum.esa_quads must be a non-empty list.")
        return workflow

    def _load_energy_model_config(self):
        if self.app_context.model_backend is None:
            raise MachineProfileError("energy_spectrum requires a configured model backend.")

        config = dict(self.app_context.model_backend.config)
        required_keys = (
            "working_dir",
            "source_lattice",
            "energy_ini_ele_file",
            "energy_json_path",
            "energy_lte_file",
            "energy_ele_file",
            "energy_mat_file",
            "energy_twi_file",
            "energy_log",
            "energy_dispersion_line_name",
            "energy_twiss_line_name",
        )
        for key in required_keys:
            value = config.get(key)
            if not isinstance(value, str) or not value.strip():
                raise MachineProfileError(
                    f"energy_spectrum model backend is missing required key {key!r}."
                )
        return config

    def _build_start_elements(self):
        explicit = self.energy_config.get("start_elements")
        if isinstance(explicit, list) and explicit:
            return [str(item) for item in explicit]

        tag_name = str(self.energy_config.get("start_element_tag", "")).strip()
        if tag_name:
            tagged = [
                element.id
                for element in list_elements(self.app_context, kind="quad")
                if tag_name in element.tags
            ]
            if tagged:
                return tagged

        return [element.id for element in list_elements(self.app_context, kind="quad")]

    def _load_energy_set_pv(self):
        raw_value = self.energy_config.get("energy_set_pv")
        if raw_value is None:
            return None
        if isinstance(raw_value, dict):
            value = raw_value.get(self.control_backend)
            if value is None:
                return None
            text = str(value).strip()
            return text or None
        text = str(raw_value).strip()
        return text or None

    @staticmethod
    def _resolve_mode_mapping(mapping, mode, location):
        if not isinstance(mapping, dict):
            raise MachineProfileError(f"{location} must be a mapping keyed by backend.")
        try:
            return mapping[mode]
        except KeyError as exc:
            raise MachineProfileError(f"{location} is missing backend {mode!r}.") from exc

    def _energy_model_path(self, key):
        if self.energy_model_config is None:
            raise MachineProfileError(self._model_error or "energy_spectrum model backend is unavailable.")
        return Path(self.energy_model_config[key])

    def _model_available(self):
        return self.energy_model_config is not None

    def _model_unavailable_message(self):
        return self._model_error or "energy_spectrum model backend is unavailable."

    def _refresh_model_controls(self):
        available = self._model_available()
        if available:
            self.pushButton_cal_disp.setEnabled(True)
            self.pushButton_cal_disp.setToolTip("Calculate ESA dispersion with the configured model backend.")
            self.pushButton_cal_twiss_disp.setEnabled(True)
            self.pushButton_cal_twiss_disp.setToolTip("Calculate ESA optics with the configured model backend.")
        else:
            message = f"Model backend unavailable: {self._model_unavailable_message()}"
            self.pushButton_cal_disp.setEnabled(False)
            self.pushButton_cal_disp.setToolTip(message)
            self.pushButton_cal_twiss_disp.setEnabled(False)
            self.pushButton_cal_twiss_disp.setToolTip(message)
            self._update_model_status("Unavailable", "warning", message)

        for button in (self.pushButton_cal_disp, self.pushButton_cal_twiss_disp):
            self._refresh_widget_style(button)

    def _use_design_eta(self, status_text=None, tooltip=None):
        self.eta_flag = DEFAULT_DESIGN_ETA
        self.lineEdit_eta_ESAflag.setText(str(round(self.eta_flag, 5)))
        self._update_model_status(
            status_text or f"design eta {self.eta_flag:.4f} m",
            "warning",
            tooltip,
        )

    def _get_esa_quad_values(self):
        values = {}
        for element_id, pv_name in self.esa_quad_pvs.items():
            value = caget(pv_name)
            if value is None:
                raise RuntimeError(f"{pv_name} returned no value for {element_id}.")
            values[element_id] = value
        return values

    def _configure_window(self):
        self.setWindowTitle(f"{self.machine_profile.machine.display_name} Energy Spectrum")
        self.resize(1260, 960)
        self.setMinimumSize(1024, 780)
        self.menuBar().hide()
        self.statusBar().hide()

    def _build_shell(self):
        self.centralwidget.setObjectName("centralwidget")
        self.verticalLayout_2.setContentsMargins(10, 10, 10, 10)
        self.verticalLayout_2.setSpacing(12)
        self.horizontalLayout_2.setContentsMargins(0, 0, 0, 0)
        self.horizontalLayout_2.setSpacing(12)
        self.horizontalLayout_2.setStretch(0, 5)
        self.horizontalLayout_2.setStretch(1, 3)

        self.frame.setFrameShape(QFrame.NoFrame)
        self.frame_2.setFrameShape(QFrame.NoFrame)
        self.verticalLayout_4.setContentsMargins(0, 0, 0, 0)
        self.verticalLayout_4.setSpacing(0)
        self.verticalLayout_3.setSpacing(12)
        self.verticalLayout_7.setContentsMargins(0, 0, 0, 0)
        self.verticalLayout_7.setSpacing(12)
        self.verticalLayout.setSpacing(12)

        self._build_summary_panel()
        self._configure_plot_card()
        self._configure_workspace_cards()
        self._configure_workspace_content()
        self._apply_theme()

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

        title = QLabel(f"{self.machine_profile.machine.display_name} Energy Spectrum", panel)
        title.setObjectName("summaryTitle")
        header_layout.addWidget(title)
        header_layout.addStretch(1)

        for text in (
            f"Machine: {self.machine_profile.machine.display_name}",
            f"Backend: {self.control_backend.upper()}",
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

        self.status_panel = SpectrumStatusStrip(panel)
        self.status_panel.add_item("machine", "MACHINE", self.machine_profile.machine.id)
        self.status_panel.add_item("backend", "BACKEND", self.control_backend.upper())
        self.status_panel.add_item("connection", "CONNECTION", "Waiting")
        self.status_panel.add_item("fit", "FIT", self.comboBox_fitmethod.currentText())
        self.status_panel.add_item("model", "MODEL", "Waiting")
        self.status_panel.add_item("tune", "AUTO FIND", "Idle")
        self.status_panel.add_item("readout", "READOUT", "Waiting")
        self.status_panel.finish()
        self.status_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        outer_layout.addWidget(self.status_panel)

        self.verticalLayout_2.insertWidget(0, panel)

    def _configure_plot_card(self):
        self.frame_3.setObjectName("plotCard")
        self.frame_3.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.verticalLayout_6.setContentsMargins(12, 12, 12, 12)
        self.verticalLayout_6.setSpacing(0)
        self.verticalLayout_5.setSpacing(10)

        self.flag_plot_title = QLabel("Flag Image", self.frame_3)
        self.flag_plot_title.setObjectName("panelTitle")
        self.spectrum_plot_title = QLabel("Energy Spectrum", self.frame_3)
        self.spectrum_plot_title.setObjectName("panelTitle")

        self.verticalLayout_5.insertWidget(0, self.flag_plot_title)
        self.verticalLayout_5.insertWidget(2, self.spectrum_plot_title)
        self.verticalLayout_5.setStretch(1, 1)
        self.verticalLayout_5.setStretch(3, 1)

        self.verticalLayout_3.removeWidget(self.groupBox_4)
        self.verticalLayout_3.removeWidget(self.groupBox_5)
        self.left_detail_stack = QVBoxLayout()
        self.left_detail_stack.setContentsMargins(0, 0, 0, 0)
        self.left_detail_stack.setSpacing(10)
        self.left_detail_stack.addWidget(self.groupBox_4)
        self.left_detail_stack.addWidget(self.groupBox_5)
        self.verticalLayout_3.addLayout(self.left_detail_stack)

    def _configure_workspace_cards(self):
        for group_box in (self.groupBox_4, self.groupBox_5, self.groupBox_6, self.groupBox_7, self.groupBox_8):
            group_box.setObjectName("workspaceCard")
            group_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        self.groupBox_7.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.groupBox_4.setTitle("Acquisition")
        self.groupBox_5.setTitle("Readout")
        self.groupBox_6.setTitle("Transport Model")
        self.groupBox_8.setTitle("Energy Tuning")
        self.groupBox_7.setTitle("Background Reference")

    def _configure_workspace_content(self):
        self.label.setText("Exposure (s)")
        self.label_2.setText("Colormap")
        self.label_3.setText("Refresh (s)")
        self.label_10.setText("Fit Method")
        self.label_4.setText("Energy (MeV)")
        self.label_6.setText("Spread (%)")
        self.label_9.setText("Start @")
        self.label_11.setText("ESA flag")
        self.label_14.setText("Energy (MeV)")
        self.pushButton_cal_disp.setText("Update eta")
        self.pushButton_cal_twiss_disp.setText("Update optics")
        self.pushButton_autoFind.setText("Auto Find")
        self.pushButton_sapmles.setText("Sample Background")
        self.pushButton_save.setText("Save Background")
        self.pushButton_load.setText("Load Background")
        self.checkBox_emit.setText("Include emit")
        self.checkBox_bg.setText("Remove background")

        for label in (
            self.label,
            self.label_2,
            self.label_3,
            self.label_10,
            self.label_4,
            self.label_6,
            self.label_9,
            self.label_11,
            self.label_14,
            self.label_5,
            self.label_7,
            self.label_8,
            self.label_eta,
        ):
            label.setProperty("role", "field")

        self.label_energy.setObjectName("metricValue")
        self.label_energyspread.setObjectName("metricValue")
        self.label_energy.setMinimumWidth(96)
        self.label_energyspread.setMinimumWidth(96)
        for label in (self.label_4, self.label_6):
            label.setStyleSheet("")
            label.setProperty("role", "metricLabel")
        for label in (self.label_9, self.label_11):
            label.setStyleSheet("")
            label.setProperty("role", "focusField")
        for label in (self.label_energy, self.label_energyspread):
            label.setStyleSheet("")

        self.horizontalLayout_7.setContentsMargins(10, 12, 10, 8)
        self.horizontalLayout_7.setSpacing(10)
        self.horizontalLayout_6.setSpacing(8)
        self.verticalLayout_13.setSpacing(6)

        self.lineEdit_expotime.setReadOnly(self.control_backend != "real" or not self._writes_allowed())
        self.lineEdit_alpha_ESAflag.setReadOnly(True)
        self.lineEdit_beta_ESAflag.setReadOnly(True)
        self.lineEdit_eta_ESAflag.setReadOnly(True)

        self.verticalLayout_8.setContentsMargins(10, 12, 10, 8)
        self.verticalLayout_8.setSpacing(6)
        self.verticalLayout_9.setContentsMargins(10, 12, 10, 8)
        self.verticalLayout_9.setSpacing(6)
        self.verticalLayout_10.setContentsMargins(10, 12, 10, 8)
        self.verticalLayout_10.setSpacing(6)
        self.verticalLayout_14.setContentsMargins(10, 12, 10, 8)
        self.verticalLayout_14.setSpacing(6)
        self.verticalLayout_11.setSpacing(6)
        self.verticalLayout_12.setContentsMargins(0, 0, 0, 0)
        self.verticalLayout_12.setSpacing(6)

        self.gridLayout.setHorizontalSpacing(7)
        self.gridLayout.setVerticalSpacing(6)
        self.gridLayout.removeWidget(self.label)
        self.gridLayout.removeWidget(self.lineEdit_expotime)
        self.gridLayout.removeWidget(self.label_3)
        self.gridLayout.removeWidget(self.lineEdit_refresh)
        self.gridLayout.removeWidget(self.label_2)
        self.gridLayout.removeWidget(self.comboBox_colormap)
        self.gridLayout.removeWidget(self.label_10)
        self.gridLayout.removeWidget(self.comboBox_fitmethod)
        self.gridLayout.addWidget(self.label, 0, 0)
        self.gridLayout.addWidget(self.lineEdit_expotime, 0, 1)
        self.gridLayout.addWidget(self.label_3, 0, 2)
        self.gridLayout.addWidget(self.lineEdit_refresh, 0, 3)
        self.gridLayout.addWidget(self.label_2, 1, 0)
        self.gridLayout.addWidget(self.comboBox_colormap, 1, 1)
        self.gridLayout.addWidget(self.label_10, 1, 2)
        self.gridLayout.addWidget(self.comboBox_fitmethod, 1, 3)
        self.gridLayout.setColumnStretch(0, 0)
        self.gridLayout.setColumnStretch(1, 1)
        self.gridLayout.setColumnStretch(2, 0)
        self.gridLayout.setColumnStretch(3, 1)
        self.gridLayout_2.setHorizontalSpacing(7)
        self.gridLayout_2.setVerticalSpacing(5)
        self.gridLayout_3.setHorizontalSpacing(7)
        self.gridLayout_3.setVerticalSpacing(5)
        self.gridLayout_6.setHorizontalSpacing(7)
        self.gridLayout_6.setVerticalSpacing(5)

        while self.verticalLayout_9.count():
            item = self.verticalLayout_9.itemAt(self.verticalLayout_9.count() - 1)
            if item.spacerItem() is None:
                break
            self.verticalLayout_9.takeAt(self.verticalLayout_9.count() - 1)

        dense_inputs = (
            self.lineEdit_expotime,
            self.lineEdit_refresh,
            self.comboBox_colormap,
            self.comboBox_fitmethod,
            self.comboBox_start_element,
            self.doubleSpinBox_alpha_in,
            self.doubleSpinBox_beta_in,
            self.doubleSpinBox_emi_in,
            self.lineEdit_alpha_ESAflag,
            self.lineEdit_beta_ESAflag,
            self.lineEdit_eta_ESAflag,
            self.lineEdit_samples,
        )
        for widget in dense_inputs:
            widget.setProperty("dense", True)
            self._refresh_widget_style(widget)

        self.comboBox_start_element.clear()
        self.comboBox_start_element.addItems(self.start_elements)
        default_start = str(self.energy_config.get("default_start_element", "")).strip()
        if default_start:
            index = self.comboBox_start_element.findText(default_start)
            if index >= 0:
                self.comboBox_start_element.setCurrentIndex(index)

        default_energy = int(round(float(self.energy_config.get("energy0_default_mev", 2200))))
        self.slider_energy.setValue(default_energy)
        self._update_energy_slider_label(default_energy)
        self._sync_energy_control_state()

        self.pushButton_cal_disp.setProperty("compact", True)
        self.pushButton_cal_twiss_disp.setProperty("compact", True)
        self.pushButton_autoFind.setProperty("compact", True)
        for button in (
            self.pushButton_cal_disp,
            self.pushButton_cal_twiss_disp,
            self.pushButton_autoFind,
            self.pushButton_sapmles,
            self.pushButton_save,
            self.pushButton_load,
        ):
            button.setProperty("tight", True)
            self._refresh_widget_style(button)

        self.gridLayout_4.removeWidget(self.pushButton_sapmles)
        self.gridLayout_4.removeWidget(self.lineEdit_samples)
        self.gridLayout_4.removeWidget(self.pushButton_save)
        self.gridLayout_4.removeWidget(self.pushButton_load)
        self.gridLayout_4.removeWidget(self.lineEdit)
        self.gridLayout_4.removeWidget(self.lineEdit_3)
        self.lineEdit.hide()
        self.lineEdit_3.hide()
        self.gridLayout_4.setHorizontalSpacing(7)
        self.gridLayout_4.setVerticalSpacing(5)
        self.gridLayout_4.addWidget(self.pushButton_sapmles, 0, 0)
        self.gridLayout_4.addWidget(self.lineEdit_samples, 0, 1)
        self.gridLayout_4.addWidget(self.pushButton_save, 1, 0, 1, 2)
        self.gridLayout_4.addWidget(self.pushButton_load, 2, 0, 1, 2)
        self.gridLayout_4.setColumnStretch(1, 1)

        self.background_plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.background_plot.setMinimumHeight(272)
        self.verticalLayout_12.setStretch(0, 1)
        self.verticalLayout_12.setStretch(1, 0)

    def _connect_signals(self):
        self.lineEdit_expotime.returnPressed.connect(self.set_expotime)
        self.lineEdit_refresh.returnPressed.connect(self.set_refresh)
        self.comboBox_fitmethod.currentTextChanged.connect(self._refresh_status)
        self.comboBox_colormap.currentTextChanged.connect(self._handle_colormap_change)

        self.checkBox_emit.clicked.connect(lambda: self.emit_withornot(self.checkBox_emit.isChecked()))
        self.pushButton_cal_disp.clicked.connect(self.cal_disp)
        self.pushButton_cal_twiss_disp.clicked.connect(self.cal_twiss_disp)

        self.pushButton_sapmles.clicked.connect(self.background_samples)
        self.pushButton_save.clicked.connect(self.save_bgfile)
        self.pushButton_load.clicked.connect(self.load_bgfile)
        self.checkBox_bg.clicked.connect(lambda: self.bg_removeornot(self.checkBox_bg.isChecked()))

        self.slider_energy.valueChanged.connect(self._update_energy_slider_label)
        self.slider_energy.sliderReleased.connect(self.set_bend_quad)
        self.pushButton_autoFind.clicked.connect(self.run_esa_auto_tune)

    def _warn(self, message):
        print(message)
        QMessageBox.warning(self, "Energy Spectrum", message)

    def _auto_tune_is_running(self):
        return self.auto_tune_thread is not None and self.auto_tune_thread.isRunning()

    def _writes_allowed(self):
        return workflow_writes_allowed(self.app_context, "energy_spectrum")

    def _require_write_allowed(self, operation):
        require_workflow_write_allowed(self.app_context, "energy_spectrum", operation)

    def _sync_energy_control_state(self):
        writes_allowed = self._writes_allowed()
        slider_enabled = (
            self.control_backend == "real"
            and self.energy_set_pv is not None
            and not self._auto_tune_is_running()
            and writes_allowed
        )
        self.slider_energy.setEnabled(slider_enabled)
        if self._auto_tune_is_running():
            self.slider_energy.setToolTip("Disabled while Auto Find is scanning.")
        elif slider_enabled:
            self.slider_energy.setToolTip(f"Release to write target energy to {self.energy_set_pv}.")
        elif self.control_backend == "real" and not writes_allowed:
            self.slider_energy.setToolTip("Real-machine energy writes are blocked by machine profile.")
        elif self.control_backend == "vm":
            self.slider_energy.setToolTip("VM backend does not support direct energy setpoint control.")
        else:
            self.slider_energy.setToolTip("No energy_set_pv configured for the real backend.")

        auto_tune_enabled = self.control_backend == "real" and not self._auto_tune_is_running() and writes_allowed
        self.pushButton_autoFind.setEnabled(auto_tune_enabled)
        if self._auto_tune_is_running():
            self.pushButton_autoFind.setText("Scanning...")
            self.pushButton_autoFind.setToolTip("Auto Find is currently scanning the ESA bend current.")
        else:
            self.pushButton_autoFind.setText("Auto Find")
            if self.control_backend == "real" and writes_allowed:
                self.pushButton_autoFind.setToolTip(
                    f"Scan {self.bend_pv} to locate the ESA beam on {self.flag_pv}."
                )
            elif self.control_backend == "real":
                self.pushButton_autoFind.setToolTip("Real-machine ESA auto tune is blocked by machine profile.")
            else:
                self.pushButton_autoFind.setToolTip(
                    "VM backend does not provide a coupled ESA response, so Auto Find is disabled."
                )

    def _apply_theme(self):
        palette = self._palette()
        self.setStyleSheet(build_energy_spectrum_theme(palette))
        if hasattr(self, "status_panel"):
            self.status_panel.apply_theme(palette)
            self.status_panel.setFixedHeight(self.status_panel.sizeHint().height())
        self._update_theme_toggle_button()
        self._style_all_plots()

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
        if self._pv_available:
            self.ESA_running()
        else:
            self._draw_placeholder_views()
        self._refresh_background_preview()
        self._refresh_status()

    def _handle_colormap_change(self):
        if self._pv_available:
            self.ESA_running()
        else:
            self._draw_placeholder_views()
        self._refresh_background_preview()

    @staticmethod
    def _refresh_widget_style(widget):
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()

    def _style_axes(self, widget, xlabel, ylabel, title=None):
        palette = self._palette()
        widget.fig.patch.set_facecolor(palette["plot_card_bg"])
        widget.axes.set_facecolor(palette["plot_bg"])
        widget.axes.tick_params(colors=palette["plot_text"], which="both", labelsize=9)
        widget.axes.xaxis.label.set_color(palette["plot_text"])
        widget.axes.yaxis.label.set_color(palette["plot_text"])
        if title:
            widget.axes.set_title(title, color=palette["plot_text"], fontsize=11, fontweight="bold", loc="left")
        for spine in widget.axes.spines.values():
            spine.set_edgecolor(palette["plot_spine"])
        widget.axes.set_xlabel(xlabel)
        widget.axes.set_ylabel(ylabel)
        widget.axes.grid(alpha=0.8, linestyle="--", color=palette["plot_grid"])

    def _style_all_plots(self):
        for widget, xlabel, ylabel in (
            (self.ESAflag_image, "x (mm)", "y (mm)"),
            (self.energy_plot, "E (MeV)", "Spectrum (arb. units)"),
            (self.background_plot, "x (mm)", "y (mm)"),
        ):
            self._style_axes(widget, xlabel, ylabel)
            widget.canvas.draw_idle()

    def _draw_placeholder_plot(self, widget, title, xlabel, ylabel, note=None):
        palette = self._palette()
        widget.axes.clear()
        self._style_axes(widget, xlabel, ylabel, title=title)
        if note:
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

    def _draw_placeholder_views(self):
        note = "Waiting for image PV" if not self._pv_available else None
        self._draw_placeholder_plot(self.ESAflag_image, "Flag Image", "x (mm)", "y (mm)", note=note)
        self._draw_placeholder_plot(self.energy_plot, "Energy Spectrum", "E (MeV)", "Spectrum (arb. units)", note=note)
        self._refresh_background_preview()

    def _refresh_background_preview(self):
        if self.bg_image is None:
            self._draw_placeholder_plot(self.background_plot, "Background Preview", "x (mm)", "y (mm)")
            return
        self.background_plot.axes.clear()
        self._style_axes(self.background_plot, "x (mm)", "y (mm)", title="Background Preview")
        self.background_plot.axes.imshow(
            self.bg_image,
            cmap=self.comboBox_colormap.currentText(),
            origin="lower",
            extent=self.extent,
            aspect="auto",
        )
        self.background_plot.axes.set_xlim(self.xlim)
        self.background_plot.axes.set_ylim(self.ylim)
        self.background_plot.canvas.draw()

    def _mark_pv_available(self):
        self._pv_available = True
        self._pv_error = None

    def _mark_pv_unavailable(self, exc):
        self._pv_available = False
        error_text = str(exc)
        if error_text != self._pv_error:
            self._pv_error = error_text
            print("PV connection unavailable. Energy Spectrum is in offline shell mode.")

    def _update_model_status(self, text, tone, tooltip=None):
        self._model_text = text
        self._model_tone = tone
        self._model_tooltip = tooltip

    def _refresh_status(self):
        if not hasattr(self, "status_panel"):
            return

        machine_tone = "subtle"
        backend_tone = "warning" if self.control_backend == "vm" else "success"
        self.status_panel.set_item("machine", self.machine_profile.machine.id, machine_tone)
        self.status_panel.set_item("backend", self.control_backend.upper(), backend_tone)
        if self._pv_available:
            self.status_panel.set_item("connection", "Live PV", "success")
        else:
            self.status_panel.set_item("connection", "Offline shell", "warning")

        self.status_panel.set_item("fit", self.comboBox_fitmethod.currentText(), "subtle")
        self.status_panel.set_item("model", self._model_text, self._model_tone, self._model_tooltip)
        self.status_panel.set_item("tune", self._auto_tune_text, self._auto_tune_tone)

        energy_text = self.label_energy.text().strip()
        spread_text = self.label_energyspread.text().strip()
        if energy_text and energy_text != "N/A" and spread_text and spread_text != "N/A":
            self.status_panel.set_item("readout", f"{energy_text} MeV / {spread_text}%", "success")
        elif self._pv_available:
            self.status_panel.set_item("readout", "Waiting", "subtle")
        else:
            self.status_panel.set_item("readout", "Unavailable", "warning")

    def _get_positive_int(self, widget, field_name):
        try:
            value = int(widget.text())
        except ValueError:
            print(f"{field_name} must be an integer.")
            return None
        if value <= 0:
            print(f"{field_name} must be positive.")
            return None
        return value

    def _get_positive_interval_ms(self):
        try:
            interval_s = float(self.lineEdit_refresh.text())
        except ValueError:
            print("refresh interval must be numeric")
            return None
        if interval_s <= 0:
            print("refresh interval must be positive")
            return None
        return round(interval_s * 1000)

    def _set_energy_outputs(self, energy_center, energy_spread):
        self.label_energy.setText("{:.4f}".format(energy_center))
        self.label_energyspread.setText("{:.4f}".format(energy_spread * 1e2))
        self._refresh_status()

    def _set_energy_unavailable(self):
        self.label_energy.setText("N/A")
        self.label_energyspread.setText("N/A")
        self._refresh_status()


    def background_samples(self):
        """sample background image and subtract later"""
        n_samples = self._get_positive_int(self.lineEdit_samples, "background sample count")
        if n_samples is None:
            return
        print(f"sampling {n_samples} background images...")
        bg_images = []
        for i in range(n_samples):
            time.sleep(1) # wait for PV update
            tmp = self.flag_pv_obj.get()
            if tmp is None:
                self._mark_pv_unavailable(RuntimeError(f"{self.flag_pv} returned no background data"))
                print("background sampling failed: flag image PV returned no data")
                self._refresh_status()
                return
            data_ini = list(map(float, tmp))
            data = np.reshape(data_ini,(self.flag_pixel[1],self.flag_pixel[0])) # 注意shape顺序，先y后x
            bg_images.append(data)
        self._mark_pv_available()
        self.bg_image = np.mean(bg_images, axis=0)
        print("background sampling done.")
        self._refresh_background_preview()
        self._refresh_status()
    
    def save_bgfile(self):
        """save the background image to a file"""
        options = QFileDialog.Options()
        filePath, _ = QFileDialog.getSaveFileName(self,"Save Background Image","","NumPy Files (*.npy);;All Files (*)", options=options)
        if self.bg_image is None:
            print("No background image to save!")
            return
        if filePath:
            np.save(filePath, self.bg_image)
            print(f"background image saved to {filePath}")
    
    def load_bgfile(self):
        """load the background image from a file"""
        options = QFileDialog.Options()
        filePath, _ = QFileDialog.getOpenFileName(self,"Load Background Image","","NumPy Files (*.npy);;All Files (*)", options=options)
        if filePath:
            self.bg_image = np.load(filePath)
            print(f"background image loaded from {filePath}")
            self._refresh_background_preview()

    def bg_removeornot(self, state):
        """decide whether to remove background or not"""
        if state:
            if self.bg_image is None:
                self.remove_bg = False
                self.checkBox_bg.blockSignals(True)
                self.checkBox_bg.setChecked(False)
                self.checkBox_bg.blockSignals(False)
                print("background removal requires a sampled or loaded background image")
                return
            self.remove_bg = True
            print("background removal is ON")
        else:
            self.remove_bg = False
            print("background removal is OFF")
        if self._pv_available:
            self.ESA_running()


    def init_ESAflag(self):
        """init the flag PV and pixel size according to real machine or VM"""
        flag_element = self.energy_config["flag_element"]
        image_channel = self.energy_config["flag_image_channel"]
        self.flag_pv = resolve_channel(self.app_context, flag_element, image_channel)

        pixel_shape = self._resolve_mode_mapping(
            self.energy_config["flag_pixel_shape"],
            self.control_backend,
            "workflows.energy_spectrum.flag_pixel_shape",
        )
        if not isinstance(pixel_shape, list) or len(pixel_shape) != 2:
            raise MachineProfileError(
                "workflows.energy_spectrum.flag_pixel_shape must provide [nx, ny] per backend."
            )
        self.flag_pixel = (int(pixel_shape[0]), int(pixel_shape[1]))
        flag_pixel_width = float(
            self._resolve_mode_mapping(
                self.energy_config["flag_pixel_width_mm"],
                self.control_backend,
                "workflows.energy_spectrum.flag_pixel_width_mm",
            )
        )

        self.flag_expotime_pv = None
        if self.control_backend == "real":
            exposure_channel = str(self.energy_config.get("flag_exposure_channel", "")).strip()
            if exposure_channel:
                self.flag_expotime_pv = resolve_channel(
                    self.app_context,
                    flag_element,
                    exposure_channel,
                )

            try:
                expotime = caget(self.flag_expotime_pv) if self.flag_expotime_pv else None
            except Exception as exc:
                expotime = None
                self._mark_pv_unavailable(exc)
            self.lineEdit_expotime.setText(str(expotime) if expotime is not None else "--")
        else:
            self.lineEdit_expotime.setText("VM")

        try:
            self.flag_pv_obj = PV(self.flag_pv)
        except Exception as exc:
            self.flag_pv_obj = NullPV(self.flag_pv)
            self._mark_pv_unavailable(exc)

        self.width  = self.flag_pixel[0]*flag_pixel_width # mm
        self.height = self.flag_pixel[1]*flag_pixel_width # mm
        self.xlim = (-0.5*self.width , 0.5*self.width  )
        self.ylim = (-0.5*self.height, 0.5*self.height ) 
        self.extent = self.xlim +self.ylim
  
    def setup_timer(self):
        # refreah the figure at 1 Hz
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.ESA_running)
        self.is_timer_running = True  # 定时器状态
        self.timer.start(1000)# 每过1s timer.timeout触发一次
        self._refresh_status()

    def set_refresh(self):  
        # 停止并重新启动定时器以更改时间间隔  
        interval = self._get_positive_interval_ms()
        if interval is None:
            return
        self.timer.stop()  
        self.timer.start(interval) 
        self.is_timer_running = True
        self._refresh_status()

    def set_expotime(self):
        if self.control_backend == "real" and self.flag_expotime_pv:
            try:
                self._require_write_allowed("Flag exposure write")
            except MachineProfileError as exc:
                self._warn(str(exc))
                return
            try:
                expoTime = float(self.lineEdit_expotime.text())
            except ValueError:
                print("exposure time must be numeric")
                return
            try:
                caput(self.flag_expotime_pv,expoTime)
            except Exception as exc:
                self._mark_pv_unavailable(exc)
                self._refresh_status()

        else:
            self.lineEdit_expotime.setText("VM")


    def ESA_running(self):
        palette = self._palette()
        self.fit_method = self.comboBox_fitmethod.currentText()

        # clear previous image
        self.ESAflag_image.axes.clear()
        self._style_axes(self.ESAflag_image, "x (mm)", "y (mm)")
        # get colormap
        colormap = self.comboBox_colormap.currentText()  
        fit_method = self.comboBox_fitmethod.currentText()  
        # get flag image data from PV
        tmp = self.flag_pv_obj.get()
        if tmp is None:
            self.sigx = None
            self.sigy = None
            self._mark_pv_unavailable(RuntimeError(f"{self.flag_pv} returned no image data"))
            print(f"Warning: {self.flag_pv} returned no image data.")
            self._draw_placeholder_views()
            self._set_energy_unavailable()
            self._refresh_status()
            return
        self._mark_pv_available()
        data_ini = list(map(float, tmp))
        try:
            data = np.reshape(data_ini,(self.flag_pixel[1],self.flag_pixel[0])) # 注意shape顺序，先y后x
        except ValueError as exc:
            self.sigx = None
            self.sigy = None
            self._mark_pv_unavailable(exc)
            print(f"Warning: flag image reshape failed: {exc}")
            self._draw_placeholder_views()
            self._set_energy_unavailable()
            self._refresh_status()
            return
        # subtract background if needed
        if self.remove_bg and self.bg_image is not None:
            data = data - self.bg_image
            data[data<0] = 0  # 防止负值出现

        # plot the image
        self.ESAflag_image.axes.imshow(data,cmap=colormap,origin="lower",extent=self.extent,aspect="auto")
        self.ESAflag_image.axes.set_xlim(self.xlim)
        self.ESAflag_image.axes.set_ylim(self.ylim)

        #  density stat 
        #------------------------

        # sample out only the selected region data
        x = np.linspace(self.extent[0],self.extent[1],self.flag_pixel[0])
        y = np.linspace(self.extent[2],self.extent[3],self.flag_pixel[1])
        idx = np.logical_and(x>self.xlim[0], x<self.xlim[1])
        idy = np.logical_and(y>self.ylim[0], y<self.ylim[1])
        x = x[idx] #numpy布尔索引
        y = y[idy]
        data = data[idy,:][:,idx]    
        
        # projection density
        denx0 = np.sum(data,axis=0) #-2e4
        deny0 = np.sum(data,axis=1) #-6e4
        if np.max(denx0) == 0 or np.max(deny0) == 0:
            self.sigx = None
            self.sigy = None
            print("Warning: ESA projection is empty; skipping spectrum update.")
            self.ESAflag_image.canvas.draw()
            self._draw_placeholder_plot(self.energy_plot, "Energy Spectrum", "E (MeV)", "Spectrum (arb. units)")
            self._set_energy_unavailable()
            self._refresh_status()
            return

        # add density profile line
        #-------------------------
        norm_denx = denx0/np.max(denx0)
        norm_deny = deny0/np.max(deny0)
        denx = norm_denx *self.height *0.3  +self.ylim[0]*0.98
        deny = norm_deny *self.width  *0.3  +self.xlim[0]*0.98
        self.ESAflag_image.axes.plot(x, denx, "--", color=palette["plot_trace"], linewidth=1.4, label="projection")
        # self.ESAflag_image.axes.plot(deny,y,'--c')

        
        
        # add gauss-fitting lines
        #------------------------
        def Gauss_func(x,a,x0,sigma):    
                return a*np.exp(-(x-x0)**2/(2*sigma**2))
        def gauss_fit(x,amp):
            max_amp = np.max(amp)  # 最大值
            max_index = np.argmax(amp)  # 最大值对应的索引
            x0_initial = x[max_index]  # 对应的x坐标作为x0初始值
            initial_guess = [max_amp, x0_initial, 1.0] # 对应高斯函数参数 [A, μ, σ, C] 的初始值
            popt,pcov = curve_fit(Gauss_func, x, amp, p0=initial_guess) 
            return popt
        fit_norm_denx = norm_denx
        if fit_method in ("Gauss", "Gauss fit"):
            try:
                popt = gauss_fit(x, norm_denx)
                fit_norm_denx = Gauss_func(x,popt[0],popt[1],popt[2])
                fit_denx = fit_norm_denx *self.height*0.3 +self.ylim[0]*0.98
                self.ESAflag_image.axes.plot(x, fit_denx, "--", color=palette["plot_fit"], linewidth=1.4)
                self.meanx = round(popt[1], 3)
                self.sigx = abs(round(popt[2],3))
                self.sigy = None
            except (RuntimeError, ValueError, ZeroDivisionError, FloatingPointError) as exc:
                print(f"Gauss fit failed, falling back to direct moments: {exc}")
                fit_method = "direct"

        # 不拟合直接计算投影分布的方差
        if fit_method == "direct":
            # 直接计算投影分布的方差
            total_denx = np.sum(denx0)
            probabilities = denx0 / total_denx
            mean_direct = np.sum(x * probabilities)
            variance_direct = np.sum(probabilities * (x - mean_direct)**2)
            std_direct = np.sqrt(variance_direct)
            # gauss_direct = Gauss_func(x, np.max(norm_denx), mean_direct, std_direct)
            # fit_denx_direct = gauss_direct * self.height * 0.3 + self.ylim[0] * 0.98
            # self.ESAflag_image.axes.plot(x, fit_denx_direct, '--g', label="direct")
            self.meanx = mean_direct
            self.sigx = std_direct

            # 使用样条插值
            try:
                spline = UnivariateSpline(x, norm_denx, s=0.1)  # s是平滑参数
                # x_dense = np.linspace(x[0], x[-1], 200)  # 更密集的点
                fit_norm_denx = spline(x)
                fit_denx = fit_norm_denx * self.height * 0.3 + self.ylim[0] * 0.98
                self.ESAflag_image.axes.plot(x, fit_denx, "--", color=palette["plot_fit"], linewidth=1.4, alpha=0.8)
            except (ValueError, RuntimeError, FloatingPointError) as exc:
                print(f"spline fit failed: {exc}")
        self.ESAflag_image.canvas.draw()


        # -----------------
        # energy0 calculation (coresponding to the x=0)
        energy0 = float(self.energy_config.get("energy0_default_mev", 2200))
        if self.control_backend != "vm":
            # 1. 若提供了相关的能量物理量在ioc中 可以直接caget获取
            # 2. 根据磁铁(电流)强度给出energy0
            try:
                current_ES_Bend = caget(self.bend_pv) # A
            except Exception as exc:
                current_ES_Bend = None
                self._mark_pv_unavailable(exc)
            if current_ES_Bend is not None:
                try:
                    energy0 = get_energy0(
                        current_ES_Bend,
                        self.energy_config.get("energy_from_bend_current"),
                    ) # MeV
                except Exception as exc:
                    print(f"Energy0 fallback to default because conversion failed: {exc}")

        # dispersion calculation and display 
        # self.cal_disp()
        if np.isclose(self.eta_flag, 0.0):
            print("Warning: eta_flag is zero; skipping energy calculation.")
            self._draw_placeholder_plot(self.energy_plot, "Energy Spectrum", "E (MeV)", "Spectrum (arb. units)")
            self._set_energy_unavailable()
            self._refresh_status()
            return

        # energy_center and energy_spread calculation and display 
        energy_center = energy0 * (self.meanx - 0)*1e-3 / self.eta_flag + energy0 # MeV

        if self.with_emit == True: # 不考虑发射度贡献
            spread_term = (((self.sigx*1e-3)**2 - self.beta_flag * self.emi_flag) / self.eta_flag ** 2)
        elif self.with_emit == False: # 考虑发射度贡献 
            spread_term = (((self.sigx*1e-3)**2 - 0 * 0) / self.eta_flag ** 2)
        if spread_term < 0:
            print(f"Warning: negative energy spread term {spread_term}; clamping to zero.")
            spread_term = 0.0
        energy_spread = math.sqrt(spread_term) * energy0 / energy_center
        
        self._set_energy_outputs(energy_center, energy_spread)

        # plot energy profile in another figure
        enregy_all = [energy0 * (xi - 0)*1e-3 / self.eta_flag + energy0 for xi in x]
        self.energy_plot.axes.clear()
        self._style_axes(self.energy_plot, "E (MeV)", "Spectrum (arb. units)")
        self.energy_plot.axes.plot(enregy_all, norm_denx, "--", color=palette["plot_energy"], linewidth=1.4, label="projection")
        if fit_method == "direct":
            self.energy_plot.axes.plot(enregy_all, fit_norm_denx, "--", color=palette["plot_fit"], linewidth=1.4, label="spline fit")
        elif fit_method in ("Gauss", "Gauss fit"):
            self.energy_plot.axes.plot(enregy_all, fit_norm_denx, "--", color=palette["plot_fit"], linewidth=1.4, label="Gauss fit")
        legend = self.energy_plot.axes.legend(frameon=False)
        if legend is not None:
            for text in legend.get_texts():
                text.set_color(palette["plot_text"])
        self.energy_plot.canvas.draw()  # 强制刷新
        self._refresh_status()

    def cal_disp(self):
        if not self._model_available():
            message = f"Model backend unavailable: {self._model_unavailable_message()}"
            print(message)
            self._use_design_eta(tooltip=message)
            self._refresh_status()
            return

        try:
            # 根据ESA的弯铁SM(L, angle)和Q铁QE01 QE02 QE03(k,L) 漂移段(L)参数计算eta    变量仅为Q_k
            # 采用elegant计算

            # 获取当前ESA三块Q铁强度 这里假设获得的是强度k
            quad_values = self._get_esa_quad_values()

            #
            lattice_file = self._energy_model_path("source_lattice")
            esa_ini_ele_file = self._energy_model_path("energy_ini_ele_file")
            line_name = self.energy_model_config["energy_dispersion_line_name"]
            working_dir = self._energy_model_path("working_dir")

            esajson_path = self._energy_model_path("energy_json_path")
            esa_lte_file = self._energy_model_path("energy_lte_file")
            esa_ele_file = self._energy_model_path("energy_ele_file")
            esa_mat_file = self._energy_model_path("energy_mat_file")

            lte1 = ElegantParser(
                lattice_file,
                esa_ini_ele_file,
                line_name,
                runtime_json_path=esajson_path,
                elegant_dir=working_dir,
            )
            lte1.dump_runtime_state()
            with open(esajson_path, "r", encoding="utf-8") as f:
                lte = json.load(f)
            contl = lte["control"]
            lattice  = lte["lattice"]

            contl['run_setup']['lattice'] = esa_lte_file.name
            for element_id, k_value in quad_values.items():
                lattice[element_id]['K1'] = str(k_value)

            lte["control"]  = contl
            lte["lattice"]  = lattice

            with open(esajson_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(lte, indent=4))
            
            lte1.json_to_lte_ele(esa_lte_file, esa_ele_file)

            # run elegant
            # ==========================
            run_elegant_input(
                esa_ele_file.name,
                self.energy_model_config["energy_log"],
                workdir=working_dir,
            )
            
            tmp = sdds.SDDS(0)
            tmp.load(str(esa_mat_file))
            list_R = [tmp.columnData[i][0][0] for i in range(12, 48)]
            Rj = np.array(list_R).reshape(6,6)

            self.eta_flag = Rj[0, -1] 
            print('dispersion of ESA updates: ',self.eta_flag, 'm')
            self._update_model_status(f"eta {self.eta_flag:.4f} m", "success")
        
        except Exception as e:
            print(f"Error in cal_disp: {e}")
            self.eta_flag = DEFAULT_DESIGN_ETA  # 理论设计值
            print('default dispersion: ',self.eta_flag, 'm')
            self._update_model_status(f"design eta {self.eta_flag:.4f} m", "warning")
            
        self.lineEdit_eta_ESAflag.setText(str(round(self.eta_flag,5)))
        self._refresh_status()

    def cal_twiss_disp(self):
        """calculate the twiss @ ESA flag according the twiss @ in"""
        if not self._model_available():
            message = f"Model backend unavailable: {self._model_unavailable_message()}"
            print(message)
            self._update_model_status("Unavailable", "warning", message)
            self._refresh_status()
            return

        # get twiss @ in
        alpha_in = self.doubleSpinBox_alpha_in.value() #    -16.2@QT02
        beta_in = self.doubleSpinBox_beta_in.value() # m     88.6@QT02
        emi_in = self.doubleSpinBox_emi_in.value()*1e-9 # m  ~43nm@QT02
        start_element = self.comboBox_start_element.currentText() 
        quad_values = self._get_esa_quad_values()

        if beta_in <= 0:
            print("wrong beta in")
            return
        if not start_element:
            print("missing start element")
            return

        # run ESA lattice 
        #
        lattice_file = self._energy_model_path("source_lattice")
        esa_ini_ele_file = self._energy_model_path("energy_ini_ele_file")
        line_name = self.energy_model_config["energy_twiss_line_name"]
        working_dir = self._energy_model_path("working_dir")

        esajson_path = self._energy_model_path("energy_json_path")
        esa_lte_file = self._energy_model_path("energy_lte_file")
        esa_ele_file = self._energy_model_path("energy_ele_file")
        esa_twi_file = self._energy_model_path("energy_twi_file")

        try:
            lte1 = ElegantParser(
                lattice_file,
                esa_ini_ele_file,
                line_name,
                runtime_json_path=esajson_path,
                elegant_dir=working_dir,
            )
            lte1.dump_runtime_state()
            with open(esajson_path, "r", encoding="utf-8") as f:
                lte = json.load(f)
            contl = lte["control"]
            lattice  = lte["lattice"]
            usedline = lte["usedline"]

            for element_id, k_value in quad_values.items():
                lattice[element_id]['K1'] = str(k_value)

            contl['run_setup']['lattice'] = esa_lte_file.name
            contl['twiss_output']['beta_x'] = str(beta_in)
            contl['twiss_output']['alpha_x'] = str(alpha_in)

            # map of entrance of elem1 => end
            id1 = usedline.index(start_element)
            scanline = usedline[id1:-1]

            # update json with new lte and new control
            lte["control"]  = contl
            lte["lattice"]  = lattice
            lte["usedline"] = scanline

            with open(esajson_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(lte, indent=4))
            
            lte1.json_to_lte_ele(esa_lte_file, esa_ele_file)

            # run elegant
            # ==========================
            run_elegant_input(
                esa_ele_file.name,
                self.energy_model_config["energy_log"],
                workdir=working_dir,
            )
            
            tmp = sdds.SDDS(0)
            tmp.load(str(esa_twi_file))
            betax   = tmp.columnData[1][0][-1]
            alphax  = tmp.columnData[2][0][-1]
            eta     = tmp.columnData[4][0][-1]
        except Exception as e:
            print(f"Error in cal_twiss_disp: {e}")
            self._update_model_status("Model update failed", "warning")
            self._refresh_status()
            return

        # results
        self.alpha_flag = alphax
        self.beta_flag = betax
        self.emi_flag = emi_in # m

        self.eta_flag = eta # m.

        print('cal results: beta=',self.beta_flag, 'm, alpha=',self.alpha_flag, 'eta=',self.eta_flag, ' m')

        self.lineEdit_alpha_ESAflag.setText(str(round(self.alpha_flag,5)))
        self.lineEdit_beta_ESAflag.setText(str(round(self.beta_flag,5)))
        # self.lineEdit_emi_ESAflag.setText(str(self.emi_flag*1e9))
        self.lineEdit_eta_ESAflag.setText(str(round(self.eta_flag,5)))
        self._update_model_status(f"eta {self.eta_flag:.4f} m", "success")
        self._refresh_status()

    def emit_withornot(self, state):
        if state:
            self.with_emit = True
            
        if not state:
            self.with_emit = False
        if self._pv_available:
            self.ESA_running()

    def _update_energy_slider_label(self, value):
        self.label_sliderenergy.setText(str(int(value)))

    def set_bend_quad(self):
        """
        update the energy0 value according to slider position
        这里energy0是由ESA的弯铁强度决定的
        """
        slider_value = self.slider_energy.value()
        self._update_energy_slider_label(slider_value)
        if self.control_backend != "real":
            return
        if not self.energy_set_pv:
            print("No energy setpoint PV is configured for the real backend.")
            return
        try:
            self._require_write_allowed("ESA target energy write")
        except MachineProfileError as exc:
            self._warn(str(exc))
            self._sync_energy_control_state()
            return

        try:
            caput(self.energy_set_pv, float(slider_value))
            print(f"ESA target energy set to {slider_value} MeV via {self.energy_set_pv}")
        except Exception as exc:
            self._mark_pv_unavailable(exc)
            self._refresh_status()
            print(f"Failed to write ESA target energy: {exc}")

    def run_esa_auto_tune(self):
        if self.control_backend != "real":
            print("ESA auto tune is only enabled for the real backend.")
            return
        try:
            self._require_write_allowed("ESA auto tune")
        except MachineProfileError as exc:
            self._warn(str(exc))
            self._sync_energy_control_state()
            return
        if self._auto_tune_is_running():
            print("ESA auto tune is already running.")
            return

        # 暂停定时刷新，防止抢 PV
        self.timer.stop()
        self.is_timer_running = False
        self._auto_tune_text = "Running"
        self._auto_tune_tone = "warning"
        self._sync_energy_control_state()
        self._refresh_status()

        try:
            preview = self.flag_pv_obj.get()
            if preview is None:
                self._mark_pv_unavailable(RuntimeError(f"{self.flag_pv} returned no image data"))
                print("ESA auto tune requires a live flag image PV.")
                self._auto_tune_text = "No image"
                self._auto_tune_tone = "warning"
                self._sync_energy_control_state()
                self.timer.start()
                self.is_timer_running = True
                self._refresh_status()
                return
            self._mark_pv_available()

            bend_scan = dict(self.energy_config.get("bend_scan", {}))
            self.auto_tune_thread = ESAAutoTuneThread(
                flag_pv_obj=self.flag_pv_obj,
                flag_pixel=self.flag_pixel,
                bend_pv=self.bend_pv,
                remove_bg=self.remove_bg,
                bg_image=self.bg_image,
                bend_scan=bend_scan,
                app_context=self.app_context,
                parent=self,
            )
            self.auto_tune_thread.progress.connect(self._handle_auto_tune_progress)
            self.auto_tune_thread.trigger.connect(self._handle_auto_tune_result)
            self.auto_tune_thread.finished.connect(self._on_auto_tune_finished)
            self.auto_tune_thread.start()
        except Exception as exc:
            print(f"ESA auto tune failed: {exc}")
            self._mark_pv_unavailable(exc)
            self._auto_tune_text = "Failed"
            self._auto_tune_tone = "warning"
            self.timer.start()
            self.is_timer_running = True
            self._sync_energy_control_state()
            self._refresh_status()
            if self._pv_available:
                self.ESA_running()

    def _handle_auto_tune_progress(self, payload):
        stage = str(payload.get("stage", "")).strip().lower()
        current = payload.get("current")
        has_beam = bool(payload.get("has_beam"))

        if stage == "coarse":
            prefix = "Coarse"
        elif stage == "fine":
            prefix = "Fine"
        elif stage == "final":
            prefix = "Final"
        else:
            prefix = "Scan"

        current_text = f"{float(current):.1f} A" if current is not None else "--"
        suffix = " beam" if has_beam else " ..."
        self._auto_tune_text = f"{prefix} {current_text}{suffix}"
        self._auto_tune_tone = "success" if has_beam else "warning"
        self._refresh_status()
        if self._pv_available:
            self.ESA_running()

    def _handle_auto_tune_result(self, payload):
        if payload.get("ok"):
            best_current = payload.get("best_current")
            if best_current is not None:
                self._auto_tune_text = f"{best_current:.1f} A"
                self._auto_tune_tone = "success"
                print(f"[GUI] ESA auto-tuned to {best_current:.3f} A")
            else:
                self._auto_tune_text = "Done"
                self._auto_tune_tone = "success"
        else:
            error_text = payload.get("error")
            status_text = payload.get("status", "FAILED")
            self._auto_tune_text = "Failed"
            self._auto_tune_tone = "warning"
            if error_text:
                print(f"ESA auto tune failed: {error_text}")
            else:
                print(f"[GUI] ESA auto tune failed ({status_text}).")
        self._refresh_status()

    def _on_auto_tune_finished(self):
        self.auto_tune_thread = None
        self.timer.start()
        self.is_timer_running = True
        self._sync_energy_control_state()
        self._refresh_status()
        if self._pv_available:
            self.ESA_running()




if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = EnergySpectrumApp()
    window.show()
    sys.exit(app.exec_())
    
    
    
