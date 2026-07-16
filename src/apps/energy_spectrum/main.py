
import sys
import time
import numpy as np
import os
import sdds
import math
import json
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
import matplotlib.pyplot as plt
from scipy.interpolate import UnivariateSpline
from epics import caget, caget_many, caput, caput_many, PV

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListView,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gui import Ui_MainWindow
from half_linac.src.apps.energy_spectrum.auto_tune_run_log import ESAAutoTuneRunLog
from half_linac.src.apps.energy_spectrum.background_store import (
    BackgroundStoreError,
    load_background,
    save_background,
)
from half_linac.src.apps.energy_spectrum.get_energy0 import select_reference_energy_mev
from half_linac.src.apps.energy_spectrum.esa_auto_tuner import (
    ESA_AutoTuner,
    reference_x_pixel,
)
from half_linac.src.apps.energy_spectrum.profile_runtime import (
    resolve_energy_spectrum_runtime_paths,
)
from half_linac.src.apps.energy_spectrum.spectrum_profile import (
    SpectrumProfileError,
    fit_projection_profile,
    project_image_profiles,
)
from half_linac.src.shared.elegant_backend import ElegantParser
from half_linac.src.shared.elegant_runtime import run_elegant_input
from half_linac.src.shared.machine_profile import (
    MachineProfileError,
    build_model_snapshot,
    get_workflow,
    list_elements,
    load_app_context,
    prepare_elegant_model_workdir,
    require_workflow_write_allowed,
    resolve_bend_write_channel,
    resolve_channel,
    save_model_snapshot,
    workflow_writes_allowed,
)
from half_linac.src.shared.window_activation import install_qt_window_raise_handler
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
QMainWindow, QWidget#centralwidget, QDialog#energySpectrumDialog {{
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

QWidget#ESAflag_image, QWidget#energy_plot, QWidget#background_plot {{
    background-color: {plot_card_bg};
    border: none;
}}

QGroupBox#workspaceCard {{
    margin-top: 0;
    padding-top: 0;
    font-weight: 700;
}}

QGroupBox#dialogCard {{
    background-color: {panel_bg};
    border: 1px solid {panel_border};
    border-radius: 12px;
    margin-top: 0;
    padding: 0;
}}

QDialog#energySpectrumDialog QToolBar {{
    background-color: {plot_card_bg};
    border: none;
    spacing: 2px;
}}

QDialog#energySpectrumDialog QToolBar QToolButton {{
    background: transparent;
    border: none;
    border-radius: 4px;
    padding: 3px;
}}

QDialog#energySpectrumDialog QToolBar QToolButton:hover {{
    background-color: {button_hover_bg};
}}

QLabel#cardTitle, QLabel#dialogCardTitle {{
    color: {summary_title_fg};
    font-size: 13px;
    font-weight: 700;
    background: transparent;
    border: none;
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

QDialog#energySpectrumDialog QPushButton[dialogAction="true"] {{
    padding: 3px 10px;
    min-height: 20px;
    max-height: 22px;
    border-radius: 8px;
}}

QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {{
    background-color: {input_bg};
    border: 1px solid {input_border};
    border-radius: 10px;
    color: {input_fg};
    padding: 7px 10px;
    min-height: 18px;
    selection-background-color: {metric_active_fg};
}}

QLineEdit[dense="true"], QComboBox[dense="true"], QDoubleSpinBox[dense="true"], QSpinBox[dense="true"] {{
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
        selection-background-color: {metric_active_fg};
        selection-color: {window_bg};
        outline: none;
    }}

    QComboBox QAbstractItemView::item {{
        color: {input_fg};
        background-color: {input_bg};
        min-height: 22px;
    }}

    QComboBox QAbstractItemView::item:selected {{
        color: {window_bg};
        background-color: {metric_active_fg};
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


def build_background_file_dialog_theme(palette):
    return """
QFileDialog#backgroundFileDialog {{
    background-color: {window_bg};
    color: {window_fg};
}}

QFileDialog#backgroundFileDialog QAbstractItemView {{
    background-color: {input_bg};
    alternate-background-color: {panel_bg};
    color: {input_fg};
    border: 1px solid {input_border};
    selection-background-color: {metric_active_fg};
    selection-color: {window_bg};
    outline: none;
}}

QFileDialog#backgroundFileDialog QAbstractItemView::item {{
    padding: 3px 5px;
}}

QFileDialog#backgroundFileDialog QHeaderView::section {{
    background-color: {panel_bg};
    color: {window_fg};
    border: none;
    border-right: 1px solid {panel_border};
    border-bottom: 1px solid {panel_border};
    padding: 5px 7px;
    font-weight: 700;
}}

QFileDialog#backgroundFileDialog QToolButton {{
    background-color: {button_bg};
    color: {button_fg};
    border: 1px solid {button_border};
    border-radius: 6px;
    min-width: 26px;
    min-height: 26px;
    padding: 2px;
}}

QFileDialog#backgroundFileDialog QToolButton:hover {{
    background-color: {button_hover_bg};
}}

QFileDialog#backgroundFileDialog QPushButton {{
    padding: 4px 10px;
    min-height: 24px;
    max-height: 28px;
    border-radius: 8px;
}}

QFileDialog#backgroundFileDialog QSplitter::handle {{
    background-color: {panel_border};
}}

QFileDialog#backgroundFileDialog QScrollBar {{
    background-color: {window_bg};
}}

QFileDialog#backgroundFileDialog QSizeGrip {{
    background-color: {window_bg};
}}
""".format_map(palette)


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
                settle_time_s=float(self.bend_scan.get("settle_time_s", 0.5)),
                restore_initial_on_failure=bool(self.bend_scan.get("restore_initial_on_failure", True)),
                cancel_requested=self.isInterruptionRequested,
                restore_initial_on_cancel=True,
                mode=str(self.bend_scan.get("objective", "find_beam")),
                target_x_pixel=float(
                    self.bend_scan.get(
                        "target_x_pixel",
                        (self.flag_pixel[0] - 1) / 2.0,
                    )
                ),
                frame_samples=int(self.bend_scan.get("frame_samples", 3)),
                min_valid_frames=int(self.bend_scan.get("min_valid_frames", 2)),
                verification_frame_samples=int(
                    self.bend_scan.get("verification_frame_samples", 5)
                ),
                verification_min_valid_frames=int(
                    self.bend_scan.get("verification_min_valid_frames", 3)
                ),
                frame_interval_s=float(self.bend_scan.get("frame_interval_s", 0.2)),
                max_center_spread_pixel=float(
                    self.bend_scan.get("max_center_spread_pixel", np.inf)
                ),
                target_tolerance_pixel=float(
                    self.bend_scan.get("target_tolerance_pixel", np.inf)
                ),
                min_fit_correlation=float(
                    self.bend_scan.get("min_fit_correlation", 0.7)
                ),
                pixel_width_mm=float(self.bend_scan["pixel_width_mm"]),
                profile_fit_method=str(
                    self.bend_scan.get("profile_fit_method", "Gauss fit")
                ),
                x_reference_mm=float(self.bend_scan.get("x_reference_mm", 0.0)),
                center_step=float(
                    self.bend_scan.get("center_step", 0.05)
                ),
                center_max_total_offset=float(
                    self.bend_scan.get("center_max_total_offset", 1.0)
                ),
                center_tolerance_mm=float(
                    self.bend_scan.get("center_tolerance_mm", 0.2)
                ),
            )
            best_current = tuner.run(
                B_min=float(self.bend_scan.get("min", 0)),
                B_max=float(self.bend_scan.get("max", 200)),
                coarse_steps=int(self.bend_scan.get("coarse_steps", 40)),
                fine_steps=int(self.bend_scan.get("fine_steps", 81)),
            )
            self.trigger.emit(
                {
                    "ok": best_current is not None,
                    "best_current": best_current,
                    "status": tuner.get_last_status(),
                    "initial_value": tuner.initial_current,
                    "best_center_offset_pixel": tuner.best_center_offset_px,
                    "message": tuner.get_last_message(),
                    "hybrid_fit": tuner.hybrid_fit,
                    "center_lock_result": tuner.center_lock_result,
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
        install_qt_window_raise_handler(self)
        self.app_context = load_app_context("energy_spectrum")
        self.machine_profile = self.app_context.profile
        self.control_backend = self.app_context.control_backend.name
        self.energy_config = self._load_energy_spectrum_config()
        self.x_reference_mm = self._load_x_reference_mm()
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
        self.energy_reference_pv = self._load_energy_reference_pv()
        self.energy_set_limits = self._load_energy_set_limits()
        self.esa_quad_ids = tuple(self.energy_config["esa_quads"])
        self.pushButton_sample_bg = self.pushButton_sapmles
        self.bend_pv = resolve_bend_write_channel(
            self.app_context,
            self.energy_config["bend_element"],
        )
        self.bend_readback_pv = self._resolve_bend_readback_pv()
        self.auto_tune_pv, self.auto_tune_unit = self._load_auto_tune_actuator()

        self.current_theme = "dark"
        self._auto_tune_text = "Idle"
        self._auto_tune_tone = "subtle"
        self._fit_text = "Waiting"
        self._fit_tone = "subtle"
        self._fit_tooltip = None
        self._readout_text = None
        self._readout_tone = None
        self._readout_tooltip = None

        self.colorbar = None
        self.sigx = None
        self.sigy = None
        self.bg_image = None
        self.bg_metadata = {}
        self.bg_image_path = None
        self.remove_bg = False
        self.auto_tune_thread = None
        self._auto_tune_run_log = None
        self._auto_tune_log_path = None
        self.latest_model_snapshot_metadata = None
        self.latest_model_snapshot_path = None
        self._archive_next_energy_result = False

        self._configure_window()

        # initialize flag PV according to real machine or VM
        self.init_ESAflag()
        self._build_shell()

        # refresh plot with timer (the default frequency: 1Hz)
        self.setup_timer()

        # ESA 入口处束团参数
        self.with_emit = False # 默认不考虑发射度
        self._load_latest_background(silent=True)

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
        self.ESA_running(write_latest=False)

    def _load_energy_spectrum_config(self):
        workflow = dict(get_workflow(self.machine_profile, "energy_spectrum"))
        required_keys = (
            "flag_element",
            "flag_image_channel",
            "bend_element",
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

    def _resolve_bend_readback_pv(self):
        if self.control_backend == "real":
            try:
                return resolve_channel(
                    self.app_context,
                    self.energy_config["bend_element"],
                    "current_readback",
                )
            except MachineProfileError:
                pass

        legacy_channel = self.energy_config.get("bend_channel")
        if legacy_channel:
            try:
                return resolve_channel(
                    self.app_context,
                    self.energy_config["bend_element"],
                    str(legacy_channel),
                )
            except MachineProfileError:
                pass

        return self.bend_pv

    def _load_energy_model_config(self):
        if self.app_context.model_backend is None:
            raise MachineProfileError("energy_spectrum requires a configured model backend.")

        config = dict(self.app_context.model_backend.config)
        required_keys = (
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
        working_dir = config.get("energy_working_dir") or config.get("working_dir")
        if not isinstance(working_dir, str) or not working_dir.strip():
            raise MachineProfileError(
                "energy_spectrum model backend is missing required key "
                "'energy_working_dir' or fallback 'working_dir'."
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
        return self._load_energy_element_pv("energy_set_channel") or self._load_backend_pv(
            "energy_set_pv"
        )

    def _load_energy_reference_pv(self):
        return self._load_energy_element_pv(
            "energy_reference_channel"
        ) or self._load_backend_pv("energy_reference_pv")

    def _load_energy_element_pv(self, channel_key):
        element_id = str(self.energy_config.get("energy_element", "")).strip()
        channel = str(self.energy_config.get(channel_key, "")).strip()
        if not element_id or not channel:
            return None
        try:
            return resolve_channel(self.app_context, element_id, channel)
        except MachineProfileError:
            if self.control_backend == "vm":
                return None
            raise

    def _load_energy_set_limits(self):
        element_id = str(self.energy_config.get("energy_element", "")).strip()
        if not element_id:
            return None
        limits = self.machine_profile.get_element(element_id).limits
        if "low" not in limits or "high" not in limits:
            return None
        low = float(limits["low"])
        high = float(limits["high"])
        if not np.isfinite(low) or not np.isfinite(high) or low >= high:
            raise MachineProfileError(
                f"Energy element {element_id} must define finite low/high limits."
            )
        return low, high

    def _load_backend_pv(self, config_key):
        raw_value = self.energy_config.get(config_key)
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

    def _read_reference_energy_mev(self):
        default_energy = float(self.energy_config.get("energy0_default_mev", 2200))
        if self.control_backend == "vm":
            return default_energy, "workflow_default", None

        reference_energy = None
        if self.energy_reference_pv:
            try:
                reference_energy = caget(self.energy_reference_pv)
            except Exception as exc:
                print(
                    f"Energy reference PV {self.energy_reference_pv} could not be read: {exc}"
                )

        bend_conversion = self.energy_config.get("energy_from_bend_current")
        bend_current = None
        if bend_conversion is not None:
            try:
                bend_current = caget(self.bend_readback_pv)
            except Exception as exc:
                print(f"Bend readback PV {self.bend_readback_pv} could not be read: {exc}")

        energy, source = select_reference_energy_mev(
            default_energy,
            reference_energy_mev=reference_energy,
            bend_current=bend_current,
            bend_conversion=bend_conversion,
        )
        source_pv = None
        if source == "reference_pv":
            source_pv = self.energy_reference_pv
        elif source == "bend_current_conversion":
            source_pv = self.bend_readback_pv
        elif self.energy_reference_pv or bend_conversion is not None:
            print(f"Using configured default reference energy {default_energy:g} MeV.")
        if (
            source == "reference_pv"
            and hasattr(self, "target_energy_spin")
            and not self._auto_tune_is_running()
            and not self.slider_energy.isSliderDown()
            and not self.target_energy_spin.hasFocus()
        ):
            self._set_target_energy_control(energy)
        return energy, source, source_pv

    def _auto_tune_configured_for_backend(self):
        configured_backends = self.energy_config.get("auto_tune_control_backends")
        if configured_backends is None:
            return True
        return self.control_backend in configured_backends

    def _load_auto_tune_actuator(self):
        actuator = self.energy_config.get("auto_tune_actuator")
        if not isinstance(actuator, dict):
            return self.bend_pv, "A"

        element_id = str(actuator.get("element", "")).strip()
        channel = str(actuator.get("channel", "")).strip()
        unit = str(actuator.get("unit", "")).strip() or "a.u."
        if not element_id or not channel:
            raise MachineProfileError(
                "workflows.energy_spectrum.auto_tune_actuator requires element and channel."
            )
        try:
            actuator_pv = resolve_channel(self.app_context, element_id, channel)
        except MachineProfileError:
            if not self._auto_tune_configured_for_backend():
                return self.bend_pv, "A"
            raise
        return actuator_pv, unit

    def _load_x_reference_mm(self):
        raw_value = self.energy_config.get("x_reference_mm", 0.0)
        if isinstance(raw_value, dict):
            raw_value = self._resolve_mode_mapping(
                raw_value,
                self.control_backend,
                "workflows.energy_spectrum.x_reference_mm",
            )
        try:
            return float(raw_value)
        except (TypeError, ValueError) as exc:
            raise MachineProfileError(
                "workflows.energy_spectrum.x_reference_mm must be numeric."
            ) from exc

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

    def _energy_model_working_dir(self):
        if self.energy_model_config is None:
            raise MachineProfileError(self._model_error or "energy_spectrum model backend is unavailable.")
        working_dir = self.energy_model_config.get("energy_working_dir") or self.energy_model_config["working_dir"]
        return Path(working_dir)

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
        self.latest_model_snapshot_metadata = None
        self.latest_model_snapshot_path = None
        self.lineEdit_eta_ESAflag.setText(str(round(self.eta_flag, 5)))
        self._update_model_status(
            status_text or f"design eta {self.eta_flag:.4f} m",
            "warning",
            tooltip,
        )

    def _esa_quad_model_fields(self):
        return tuple((element_id, "K1") for element_id in self.esa_quad_ids)

    def _build_esa_quad_model_snapshot(self):
        snapshot = build_model_snapshot(self.app_context, self._esa_quad_model_fields())
        self.latest_model_snapshot_metadata = snapshot.as_metadata()
        self._save_latest_model_snapshot(snapshot)
        return snapshot

    def _save_latest_model_snapshot(self, snapshot):
        paths = resolve_energy_spectrum_runtime_paths(self.app_context)
        try:
            self.latest_model_snapshot_path = save_model_snapshot(
                snapshot,
                paths["model_snapshot_path"],
                extra_metadata={
                    "app": self.app_context.app_name,
                    "calculation": "energy_spectrum_esa",
                    "x_reference_mm": self.x_reference_mm,
                },
            )
        except MachineProfileError as exc:
            if isinstance(self.latest_model_snapshot_metadata, dict):
                self.latest_model_snapshot_metadata["save_error"] = str(exc)
            print(f"Warning: {exc}")

    def _snapshot_status_label(self, snapshot):
        labels = {
            "live_from_vm": "VM snap",
            "live_from_real": "Real snap",
            "saved": "Saved snap",
            "design": "Design",
        }
        return labels.get(snapshot.source, str(snapshot.source))

    def _snapshot_status_tooltip(self, snapshot):
        lines = [f"Model snapshot source: {snapshot.source}"]
        if snapshot.origin_source:
            lines.append(f"Origin source: {snapshot.origin_source}")
        if self.latest_model_snapshot_path is not None:
            lines.append(f"Saved snapshot: {self.latest_model_snapshot_path}")
        for field in snapshot.fields:
            pv_text = f" from {field.source_pv}" if field.source_pv else ""
            lines.append(f"{field.element_id}.{field.field_name} = {field.value:g}{pv_text}")
        return "\n".join(lines)

    @staticmethod
    def _apply_lattice_overrides(lattice, lattice_overrides):
        for element_id, field_overrides in lattice_overrides.items():
            if element_id not in lattice:
                raise MachineProfileError(f"Model lattice does not define element {element_id!r}.")
            element = lattice[element_id]
            for field_name, value in field_overrides.items():
                if field_name not in element:
                    raise MachineProfileError(
                        f"Model lattice element {element_id!r} does not define field {field_name!r}."
                    )
                element[field_name] = str(value)

    def _twiss_target_element(self):
        """Return the model element where ESA optics/readout values are reported."""
        for key in ("twiss_target_element", "vm_watch_element", "flag_element"):
            value = str(self.energy_config.get(key, "")).strip()
            if value:
                return value
        raise MachineProfileError("energy_spectrum requires a Twiss target element.")

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
        self.verticalLayout_7.setAlignment(Qt.AlignTop)
        self.verticalLayout.setSpacing(12)
        self.verticalLayout.setAlignment(Qt.AlignTop)

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
        self.ESAflag_image.layout().setSpacing(0)

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
        self.verticalLayout.insertWidget(0, self.groupBox_4)
        self.verticalLayout.removeWidget(self.groupBox_8)
        self.verticalLayout.insertWidget(1, self.groupBox_8)
        self.left_detail_stack = QVBoxLayout()
        self.left_detail_stack.setContentsMargins(0, 0, 0, 0)
        self.left_detail_stack.setSpacing(10)
        self.left_detail_stack.addWidget(self.groupBox_5)
        self.verticalLayout_3.addLayout(self.left_detail_stack)

    def _configure_workspace_cards(self):
        card_titles = (
            (self.groupBox_4, "Acquisition"),
            (self.groupBox_5, None),
            (self.groupBox_6, "Optics Model"),
            (self.groupBox_8, "Energy Tuning"),
            (self.groupBox_7, "Background Reference"),
        )
        self.workspace_card_title_labels = []
        for group_box, title in card_titles:
            group_box.setObjectName("workspaceCard")
            group_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
            group_box.setTitle("")

            if title is None:
                continue
            title_label = QLabel(title, group_box)
            title_label.setObjectName("cardTitle")
            title_label.move(14, 8)
            title_label.raise_()
            self.workspace_card_title_labels.append(title_label)

        self.groupBox_7.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def _configure_energy_tuning_controls(self):
        self._energy_slider_scale = 100  # 0.01 MeV per slider count.

        if self.energy_set_limits is not None:
            energy_low, energy_high = self.energy_set_limits
        else:
            energy_low = self.slider_energy.minimum()
            energy_high = self.slider_energy.maximum()

        self.slider_energy.setRange(
            math.ceil(energy_low * self._energy_slider_scale),
            math.floor(energy_high * self._energy_slider_scale),
        )
        self.slider_energy.setSingleStep(1)
        self.slider_energy.setPageStep(100)

        self.target_energy_spin = QDoubleSpinBox(self.groupBox_8)
        self.target_energy_spin.setObjectName("targetEnergySpinBox")
        self.target_energy_spin.setDecimals(2)
        self.target_energy_spin.setSingleStep(0.01)
        self.target_energy_spin.setRange(energy_low, energy_high)
        self.target_energy_spin.setSuffix(" MeV")
        self.target_energy_spin.setKeyboardTracking(False)
        self.target_energy_spin.setProperty("dense", True)

        self.gridLayout_6.removeWidget(self.label_sliderenergy)
        self.label_sliderenergy.hide()
        self.gridLayout_6.addWidget(self.target_energy_spin, 0, 2)

        scan_config = dict(
            self.energy_config.get(
                "auto_tune_scan",
                self.energy_config.get("bend_scan", {}),
            )
        )
        self.auto_tune_min_spin = QDoubleSpinBox(self.groupBox_8)
        self.auto_tune_max_spin = QDoubleSpinBox(self.groupBox_8)
        for spin in (self.auto_tune_min_spin, self.auto_tune_max_spin):
            spin.setDecimals(2)
            spin.setSingleStep(0.1)
            spin.setRange(energy_low, energy_high)
            spin.setSuffix(f" {self.auto_tune_unit}")
            spin.setKeyboardTracking(False)
            spin.setProperty("dense", True)
        self.auto_tune_min_spin.setObjectName("autoTuneMinimumSpinBox")
        self.auto_tune_max_spin.setObjectName("autoTuneMaximumSpinBox")
        self.auto_tune_min_spin.setValue(float(scan_config.get("min", energy_low)))
        self.auto_tune_max_spin.setValue(float(scan_config.get("max", energy_high)))

        self.auto_tune_coarse_steps_spin = QSpinBox(self.groupBox_8)
        self.auto_tune_fine_steps_spin = QSpinBox(self.groupBox_8)
        for spin in (self.auto_tune_coarse_steps_spin, self.auto_tune_fine_steps_spin):
            spin.setRange(2, 2000)
            spin.setKeyboardTracking(False)
            spin.setProperty("dense", True)
        self.auto_tune_coarse_steps_spin.setObjectName("autoTuneCoarseStepsSpinBox")
        self.auto_tune_fine_steps_spin.setObjectName("autoTuneFineStepsSpinBox")
        self.auto_tune_coarse_steps_spin.setValue(int(scan_config.get("coarse_steps", 40)))
        self.auto_tune_fine_steps_spin.setValue(int(scan_config.get("fine_steps", 81)))

        self.auto_tune_objective_combo = QComboBox(self.groupBox_8)
        self.auto_tune_objective_combo.setObjectName("autoTuneObjectiveComboBox")
        self.auto_tune_objective_combo.addItem(
            f"Closest to x reference ({self.x_reference_mm:g} mm)",
            "center_x_reference",
        )
        self.auto_tune_objective_combo.addItem("Highest brightness", "find_beam")
        self.auto_tune_objective_combo.addItem(
            "Peak brightness + fitted center",
            "brightness_then_profile_lock",
        )
        configured_objective = str(
            self.energy_config.get("auto_tune_objective", "find_beam")
        ).strip()
        objective_index = self.auto_tune_objective_combo.findData(configured_objective)
        if objective_index < 0:
            raise MachineProfileError(
                f"Unsupported energy-spectrum auto_tune_objective: {configured_objective!r}."
            )
        self.auto_tune_objective_combo.setCurrentIndex(objective_index)
        self.auto_tune_objective_combo.setProperty("dense", True)

        self.auto_tune_settle_spin = QDoubleSpinBox(self.groupBox_8)
        self.auto_tune_settle_spin.setObjectName("autoTuneSettleTimeSpinBox")
        self.auto_tune_settle_spin.setDecimals(2)
        self.auto_tune_settle_spin.setSingleStep(0.05)
        self.auto_tune_settle_spin.setRange(0.0, 60.0)
        self.auto_tune_settle_spin.setSuffix(" s")
        self.auto_tune_settle_spin.setKeyboardTracking(False)
        self.auto_tune_settle_spin.setProperty("dense", True)
        self.auto_tune_settle_spin.setValue(float(scan_config.get("settle_time_s", 0.5)))

        center_lock_config = dict(self.energy_config.get("auto_tune_center_lock", {}))
        self.auto_tune_frame_interval_spin = QDoubleSpinBox(self.groupBox_8)
        self.auto_tune_frame_interval_spin.setObjectName("autoTuneFrameIntervalSpinBox")
        self.auto_tune_frame_interval_spin.setDecimals(2)
        self.auto_tune_frame_interval_spin.setSingleStep(0.05)
        self.auto_tune_frame_interval_spin.setRange(0.0, 10.0)
        self.auto_tune_frame_interval_spin.setSuffix(" s")
        self.auto_tune_frame_interval_spin.setKeyboardTracking(False)
        self.auto_tune_frame_interval_spin.setProperty("dense", True)
        self.auto_tune_frame_interval_spin.setValue(
            float(center_lock_config.get("frame_interval_s", 0.2))
        )

        self.auto_tune_probe_step_spin = QDoubleSpinBox(self.groupBox_8)
        self.auto_tune_probe_step_spin.setObjectName("autoTuneCenterStepSpinBox")
        self.auto_tune_probe_step_spin.setDecimals(2)
        self.auto_tune_probe_step_spin.setSingleStep(0.01)
        self.auto_tune_probe_step_spin.setRange(0.01, 2.0)
        self.auto_tune_probe_step_spin.setSuffix(f" {self.auto_tune_unit}")
        self.auto_tune_probe_step_spin.setKeyboardTracking(False)
        self.auto_tune_probe_step_spin.setProperty("dense", True)
        self.auto_tune_probe_step_spin.setValue(
            float(center_lock_config.get("center_step", 0.05))
        )

        self.auto_tune_center_tolerance_spin = QDoubleSpinBox(self.groupBox_8)
        self.auto_tune_center_tolerance_spin.setObjectName(
            "autoTuneCenterToleranceSpinBox"
        )
        self.auto_tune_center_tolerance_spin.setDecimals(2)
        self.auto_tune_center_tolerance_spin.setSingleStep(0.05)
        self.auto_tune_center_tolerance_spin.setRange(0.01, 5.0)
        self.auto_tune_center_tolerance_spin.setSuffix(" mm")
        self.auto_tune_center_tolerance_spin.setKeyboardTracking(False)
        self.auto_tune_center_tolerance_spin.setProperty("dense", True)
        self.auto_tune_center_tolerance_spin.setValue(
            float(center_lock_config.get("center_tolerance_mm", 0.2))
        )

        self.auto_tune_frame_samples_spin = QSpinBox(self.groupBox_8)
        self.auto_tune_min_valid_frames_spin = QSpinBox(self.groupBox_8)
        self.auto_tune_verification_frames_spin = QSpinBox(self.groupBox_8)
        self.auto_tune_verification_min_valid_spin = QSpinBox(self.groupBox_8)
        for spin in (
            self.auto_tune_frame_samples_spin,
            self.auto_tune_min_valid_frames_spin,
            self.auto_tune_verification_frames_spin,
            self.auto_tune_verification_min_valid_spin,
        ):
            spin.setRange(1, 100)
            spin.setKeyboardTracking(False)
            spin.setProperty("dense", True)
        self.auto_tune_frame_samples_spin.setValue(
            int(center_lock_config.get("frame_samples", 3))
        )
        self.auto_tune_min_valid_frames_spin.setValue(
            int(center_lock_config.get("min_valid_frames", 2))
        )
        self.auto_tune_verification_frames_spin.setValue(
            int(center_lock_config.get("verification_frame_samples", 5))
        )
        self.auto_tune_verification_min_valid_spin.setValue(
            int(center_lock_config.get("verification_min_valid_frames", 3))
        )

        self.auto_tune_max_offset_spin = QDoubleSpinBox(self.groupBox_8)
        self.auto_tune_max_offset_spin.setDecimals(2)
        self.auto_tune_max_offset_spin.setSingleStep(0.1)
        self.auto_tune_max_offset_spin.setRange(0.01, 10.0)
        self.auto_tune_max_offset_spin.setSuffix(f" {self.auto_tune_unit}")
        self.auto_tune_max_offset_spin.setKeyboardTracking(False)
        self.auto_tune_max_offset_spin.setProperty("dense", True)
        self.auto_tune_max_offset_spin.setValue(
            float(center_lock_config.get("max_total_offset", 1.0))
        )

        self.auto_tune_settings_dialog = QDialog(self)
        self.auto_tune_settings_dialog.setObjectName("energySpectrumDialog")
        self.auto_tune_settings_dialog.setWindowTitle("Auto Find Settings")
        self.auto_tune_settings_dialog.setModal(True)
        self.auto_tune_settings_dialog.resize(560, 520)
        settings_layout = QVBoxLayout(self.auto_tune_settings_dialog)
        settings_layout.setContentsMargins(14, 14, 14, 14)
        settings_layout.setSpacing(10)

        def add_settings_group(title, fields):
            group = QGroupBox("", self.auto_tune_settings_dialog)
            group.setObjectName("dialogCard")
            grid = QGridLayout(group)
            grid.setContentsMargins(10, 9, 10, 10)
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(7)

            title_label = QLabel(title, group)
            title_label.setObjectName("dialogCardTitle")
            grid.addWidget(title_label, 0, 0, 1, 2)
            for row, (text, widget) in enumerate(fields):
                label = QLabel(text, group)
                label.setProperty("role", "field")
                grid.addWidget(label, row + 1, 0)
                grid.addWidget(widget, row + 1, 1)
            grid.setColumnStretch(1, 1)
            settings_layout.addWidget(group)

        add_settings_group(
            "Scan",
            (
                ("Minimum", self.auto_tune_min_spin),
                ("Maximum", self.auto_tune_max_spin),
                ("Coarse points", self.auto_tune_coarse_steps_spin),
                ("Fine points", self.auto_tune_fine_steps_spin),
                ("Settle time", self.auto_tune_settle_spin),
            ),
        )
        add_settings_group(
            "Sampling",
            (
                ("Fine/center frames", self.auto_tune_frame_samples_spin),
                ("Minimum valid frames", self.auto_tune_min_valid_frames_spin),
                ("Verification frames", self.auto_tune_verification_frames_spin),
                ("Verification minimum", self.auto_tune_verification_min_valid_spin),
                ("Frame gap", self.auto_tune_frame_interval_spin),
            ),
        )
        add_settings_group(
            "Center Lock",
            (
                ("Center step", self.auto_tune_probe_step_spin),
                ("Center tolerance", self.auto_tune_center_tolerance_spin),
                ("Maximum offset", self.auto_tune_max_offset_spin),
            ),
        )
        self.auto_tune_settings_buttons = QDialogButtonBox(
            QDialogButtonBox.Ok
            | QDialogButtonBox.Cancel
            | QDialogButtonBox.RestoreDefaults,
            parent=self.auto_tune_settings_dialog,
        )
        self.auto_tune_settings_buttons.accepted.connect(
            self.auto_tune_settings_dialog.accept
        )
        self.auto_tune_settings_buttons.rejected.connect(
            self.auto_tune_settings_dialog.reject
        )
        self.auto_tune_settings_buttons.button(
            QDialogButtonBox.RestoreDefaults
        ).clicked.connect(self._reset_auto_tune_settings)
        self.auto_tune_settings_buttons.button(QDialogButtonBox.Ok).setText("Apply")
        for button in self.auto_tune_settings_buttons.buttons():
            button.setProperty("dialogAction", True)
        settings_layout.addWidget(self.auto_tune_settings_buttons)

        self.auto_tune_settings_button = QPushButton("Settings...", self.groupBox_8)
        self.auto_tune_settings_button.setObjectName("pushButton_autoTuneSettings")
        self.auto_tune_settings_summary = QLabel(self.groupBox_8)
        self.auto_tune_settings_summary.setWordWrap(True)
        self.auto_tune_settings_summary.setProperty("role", "field")

        objective_layout = QGridLayout()
        objective_layout.setContentsMargins(0, 0, 0, 0)
        objective_layout.setHorizontalSpacing(7)
        objective_label = QLabel("Method", self.groupBox_8)
        objective_label.setProperty("role", "field")
        objective_layout.addWidget(objective_label, 0, 0)
        objective_layout.addWidget(self.auto_tune_objective_combo, 0, 1)
        objective_layout.addWidget(self.auto_tune_settings_button, 0, 2)
        objective_layout.setColumnStretch(1, 1)

        self.pushButton_stopAutoFind = QPushButton("Stop", self.groupBox_8)
        self.pushButton_stopAutoFind.setObjectName("pushButton_stopAutoFind")
        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(7)
        self.verticalLayout_14.removeWidget(self.pushButton_autoFind)
        button_layout.addWidget(self.pushButton_autoFind, 1)
        button_layout.addWidget(self.pushButton_stopAutoFind, 1)
        self.verticalLayout_14.addLayout(objective_layout)
        self.verticalLayout_14.addWidget(self.auto_tune_settings_summary)
        self.verticalLayout_14.addLayout(button_layout)

        self.auto_tune_parameter_widgets = (
            self.auto_tune_min_spin,
            self.auto_tune_max_spin,
            self.auto_tune_coarse_steps_spin,
            self.auto_tune_fine_steps_spin,
            self.auto_tune_settle_spin,
            self.auto_tune_objective_combo,
            self.auto_tune_frame_samples_spin,
            self.auto_tune_min_valid_frames_spin,
            self.auto_tune_verification_frames_spin,
            self.auto_tune_verification_min_valid_spin,
            self.auto_tune_frame_interval_spin,
            self.auto_tune_probe_step_spin,
            self.auto_tune_center_tolerance_spin,
            self.auto_tune_max_offset_spin,
        )
        self._auto_tune_default_values = self._auto_tune_settings_values()
        self._update_auto_tune_settings_summary()

    def _auto_tune_settings_values(self):
        return {
            "minimum": self.auto_tune_min_spin.value(),
            "maximum": self.auto_tune_max_spin.value(),
            "coarse_steps": self.auto_tune_coarse_steps_spin.value(),
            "fine_steps": self.auto_tune_fine_steps_spin.value(),
            "settle_time_s": self.auto_tune_settle_spin.value(),
            "frame_samples": self.auto_tune_frame_samples_spin.value(),
            "min_valid_frames": self.auto_tune_min_valid_frames_spin.value(),
            "verification_frame_samples": self.auto_tune_verification_frames_spin.value(),
            "verification_min_valid_frames": self.auto_tune_verification_min_valid_spin.value(),
            "frame_interval_s": self.auto_tune_frame_interval_spin.value(),
            "center_step": self.auto_tune_probe_step_spin.value(),
            "center_tolerance_mm": self.auto_tune_center_tolerance_spin.value(),
            "max_total_offset": self.auto_tune_max_offset_spin.value(),
        }

    def _set_auto_tune_settings_values(self, values):
        widget_values = (
            (self.auto_tune_min_spin, "minimum"),
            (self.auto_tune_max_spin, "maximum"),
            (self.auto_tune_coarse_steps_spin, "coarse_steps"),
            (self.auto_tune_fine_steps_spin, "fine_steps"),
            (self.auto_tune_settle_spin, "settle_time_s"),
            (self.auto_tune_frame_samples_spin, "frame_samples"),
            (self.auto_tune_min_valid_frames_spin, "min_valid_frames"),
            (self.auto_tune_verification_frames_spin, "verification_frame_samples"),
            (self.auto_tune_verification_min_valid_spin, "verification_min_valid_frames"),
            (self.auto_tune_frame_interval_spin, "frame_interval_s"),
            (self.auto_tune_probe_step_spin, "center_step"),
            (self.auto_tune_center_tolerance_spin, "center_tolerance_mm"),
            (self.auto_tune_max_offset_spin, "max_total_offset"),
        )
        for widget, key in widget_values:
            widget.setValue(values[key])

    def _reset_auto_tune_settings(self):
        self._set_auto_tune_settings_values(self._auto_tune_default_values)

    def _validate_auto_tune_dialog_values(self):
        values = self._auto_tune_settings_values()
        if values["minimum"] >= values["maximum"]:
            raise ValueError("Auto Find minimum energy must be less than maximum energy.")
        if values["min_valid_frames"] > values["frame_samples"]:
            raise ValueError("Minimum valid frames cannot exceed Fine/center frames.")
        if (
            values["verification_min_valid_frames"]
            > values["verification_frame_samples"]
        ):
            raise ValueError(
                "Verification minimum cannot exceed verification frames."
            )

    def _show_auto_tune_settings(self):
        if self._auto_tune_is_running():
            return
        previous = self._auto_tune_settings_values()
        if self.auto_tune_settings_dialog.exec_() != QDialog.Accepted:
            self._set_auto_tune_settings_values(previous)
            return
        try:
            self._validate_auto_tune_dialog_values()
        except ValueError as exc:
            self._warn(str(exc))
            self._set_auto_tune_settings_values(previous)
            return
        self._update_auto_tune_settings_summary()

    def _update_auto_tune_settings_summary(self):
        self.auto_tune_settings_summary.setText(
            f"Range {self.auto_tune_min_spin.value():g}–"
            f"{self.auto_tune_max_spin.value():g} {self.auto_tune_unit} · "
            f"{self.auto_tune_coarse_steps_spin.value()}/"
            f"{self.auto_tune_fine_steps_spin.value()} pts · "
            f"step {self.auto_tune_probe_step_spin.value():g} {self.auto_tune_unit} · "
            f"tol {self.auto_tune_center_tolerance_spin.value():g} mm"
        )

    def _initial_target_energy_mev(self):
        fallback = float(self.energy_config.get("energy0_default_mev", 2200))
        if self.control_backend != "real":
            return fallback

        source_pv = self.energy_set_pv or self.energy_reference_pv
        if not source_pv:
            return fallback
        try:
            value = caget(source_pv)
            energy = float(value)
        except Exception as exc:
            print(f"Could not initialize Target from {source_pv}: {exc}")
            return fallback
        if not np.isfinite(energy):
            print(f"Could not initialize Target from non-finite value on {source_pv}.")
            return fallback
        if self.energy_set_limits is not None:
            low, high = self.energy_set_limits
            if not low <= energy <= high:
                print(
                    f"Ignoring out-of-range Target value {energy:g} MeV from {source_pv}; "
                    f"expected [{low:g}, {high:g}] MeV."
                )
                return fallback
        print(f"Initialized Target to {energy:.2f} MeV from {source_pv}.")
        return energy

    def _set_target_energy_control(self, energy_mev):
        energy = float(energy_mev)
        energy = min(max(energy, self.target_energy_spin.minimum()), self.target_energy_spin.maximum())
        slider_value = int(round(energy * self._energy_slider_scale))
        slider_value = min(max(slider_value, self.slider_energy.minimum()), self.slider_energy.maximum())
        slider_was_blocked = self.slider_energy.blockSignals(True)
        spin_was_blocked = self.target_energy_spin.blockSignals(True)
        try:
            self.slider_energy.setValue(slider_value)
            self.target_energy_spin.setValue(energy)
        finally:
            self.slider_energy.blockSignals(slider_was_blocked)
            self.target_energy_spin.blockSignals(spin_was_blocked)

    def _current_auto_tune_scan(self):
        minimum = self.auto_tune_min_spin.value()
        maximum = self.auto_tune_max_spin.value()
        if minimum >= maximum:
            raise ValueError("Auto Find minimum energy must be less than maximum energy.")
        if self.energy_set_limits is not None:
            low, high = self.energy_set_limits
            if minimum < low or maximum > high:
                raise ValueError(
                    f"Auto Find range must stay within [{low:g}, {high:g}] MeV."
                )
        configured = self.energy_config.get(
            "auto_tune_scan",
            self.energy_config.get("bend_scan", {}),
        )
        objective = str(self.auto_tune_objective_combo.currentData())
        target_x_pixel = reference_x_pixel(
            self.x_reference_mm,
            self.flag_pixel[0],
            self.flag_pixel_width_mm,
        )
        return {
            "min": minimum,
            "max": maximum,
            "coarse_steps": self.auto_tune_coarse_steps_spin.value(),
            "fine_steps": self.auto_tune_fine_steps_spin.value(),
            "settle_time_s": self.auto_tune_settle_spin.value(),
            "objective": objective,
            "target_x_pixel": target_x_pixel,
            "frame_samples": self.auto_tune_frame_samples_spin.value(),
            "min_valid_frames": self.auto_tune_min_valid_frames_spin.value(),
            "verification_frame_samples": self.auto_tune_verification_frames_spin.value(),
            "verification_min_valid_frames": self.auto_tune_verification_min_valid_spin.value(),
            "frame_interval_s": self.auto_tune_frame_interval_spin.value(),
            "pixel_width_mm": self.flag_pixel_width_mm,
            "profile_fit_method": self.comboBox_fitmethod.currentText(),
            "x_reference_mm": self.x_reference_mm,
            "center_step": self.auto_tune_probe_step_spin.value(),
            "center_max_total_offset": self.auto_tune_max_offset_spin.value(),
            "center_tolerance_mm": self.auto_tune_center_tolerance_spin.value(),
            "restore_initial_on_failure": bool(
                configured.get("restore_initial_on_failure", True)
            ),
        }

    def _configure_workspace_content(self):
        self.label.setText("Exposure (s)")
        self.label_2.setText("Colormap")
        self.label_3.setText("Refresh (s)")
        self.label_10.setText("Fit Method")
        self.label_4.setText("Energy (MeV)")
        self.label_6.setText("Spread (%)")
        self.label_9.setText("Input @")
        self.label_11.setText("Target")
        self.label_14.setText("Energy setpoint")
        self.pushButton_cal_disp.setText("Update eta")
        self.pushButton_cal_twiss_disp.setText("Update optics")
        self.pushButton_autoFind.setText("Auto Find")
        self.pushButton_sample_bg.setText("Sample BG")
        self.pushButton_save.setText("Save As")
        self.pushButton_load.setText("Load File")
        self.checkBox_emit.setText("Subtract emittance contribution")
        self.checkBox_emit.setToolTip(
            "Remove the beam-size contribution calculated from the current optics "
            "and emittance when reporting energy spread."
        )
        self.checkBox_bg.setText("Subtract background")
        self._configure_energy_tuning_controls()
        self._name_operator_controls()

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

        self.lineEdit_expotime.setReadOnly(self.control_backend != "real" or not self._writes_allowed())
        self.lineEdit_alpha_ESAflag.setReadOnly(True)
        self.lineEdit_beta_ESAflag.setReadOnly(True)
        self.lineEdit_eta_ESAflag.setReadOnly(True)

        self.horizontalLayout_7.setContentsMargins(10, 8, 10, 8)
        self.horizontalLayout_7.setSpacing(10)
        self.horizontalLayout_6.setSpacing(8)
        self.verticalLayout_13.setSpacing(6)

        self.verticalLayout_8.setContentsMargins(10, 34, 10, 8)
        self.verticalLayout_8.setSpacing(6)
        self.verticalLayout_9.setContentsMargins(10, 34, 10, 8)
        self.verticalLayout_9.setSpacing(6)
        self.verticalLayout_10.setContentsMargins(10, 34, 10, 8)
        self.verticalLayout_10.setSpacing(6)
        self.verticalLayout_14.setContentsMargins(10, 34, 10, 8)
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
        self.gridLayout.removeWidget(self.checkBox_emit)
        self.verticalLayout_13.removeWidget(self.checkBox_emit)
        self.verticalLayout_9.insertWidget(1, self.checkBox_emit)
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
        self._apply_optics_input_preset(self.comboBox_start_element.currentText())

        self._set_target_energy_control(self._initial_target_energy_mev())

        self.pushButton_cal_disp.setProperty("compact", True)
        self.pushButton_cal_twiss_disp.setProperty("compact", True)
        self.pushButton_autoFind.setProperty("compact", True)
        self.pushButton_stopAutoFind.setProperty("compact", True)
        for button in (
            self.pushButton_cal_disp,
            self.pushButton_cal_twiss_disp,
            self.pushButton_autoFind,
            self.pushButton_stopAutoFind,
            self.auto_tune_settings_button,
            self.pushButton_sample_bg,
            self.pushButton_save,
            self.pushButton_load,
        ):
            button.setProperty("tight", True)
            self._refresh_widget_style(button)

        self.background_dialog = QDialog(self)
        self.background_dialog.setObjectName("energySpectrumDialog")
        self.background_dialog.setWindowTitle("Background Reference")
        self.background_dialog.setModal(True)
        self.background_dialog.resize(760, 620)
        background_dialog_layout = QVBoxLayout(self.background_dialog)
        background_dialog_layout.setContentsMargins(14, 14, 14, 14)
        background_dialog_layout.setSpacing(10)

        self.verticalLayout_12.removeWidget(self.background_plot)
        self.background_plot.setParent(self.background_dialog)
        self.background_plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.background_plot.setMinimumHeight(400)
        background_dialog_layout.addWidget(self.background_plot, 1)

        controls_group = QGroupBox("", self.background_dialog)
        controls_group.setObjectName("dialogCard")
        controls_layout = QGridLayout(controls_group)
        controls_layout.setContentsMargins(10, 9, 10, 10)
        controls_layout.setHorizontalSpacing(7)
        controls_layout.setVerticalSpacing(7)
        for widget in (
            self.pushButton_sample_bg,
            self.lineEdit_samples,
            self.pushButton_save,
            self.pushButton_load,
            self.lineEdit,
            self.lineEdit_3,
        ):
            self.gridLayout_4.removeWidget(widget)
        controls_title = QLabel("Background Controls", controls_group)
        controls_title.setObjectName("dialogCardTitle")
        controls_layout.addWidget(controls_title, 0, 0, 1, 5)
        sample_label = QLabel("Samples", controls_group)
        sample_label.setProperty("role", "field")
        interval_label = QLabel("Interval", controls_group)
        interval_label.setProperty("role", "field")
        self.background_sample_interval_spin = QDoubleSpinBox(controls_group)
        self.background_sample_interval_spin.setObjectName(
            "backgroundSampleIntervalSpinBox"
        )
        self.background_sample_interval_spin.setDecimals(2)
        self.background_sample_interval_spin.setSingleStep(0.05)
        self.background_sample_interval_spin.setRange(0.0, 60.0)
        self.background_sample_interval_spin.setSuffix(" s")
        self.background_sample_interval_spin.setKeyboardTracking(False)
        self.background_sample_interval_spin.setProperty("dense", True)
        self.background_sample_interval_spin.setValue(
            float(self.energy_config.get("background_sample_interval_s", 1.0))
        )
        self.background_sample_interval_spin.setAccessibleName(
            "Background sample interval"
        )
        self.background_sample_interval_spin.setToolTip(
            "Delay between consecutive background frames."
        )
        controls_layout.addWidget(sample_label, 1, 0)
        controls_layout.addWidget(self.lineEdit_samples, 1, 1)
        controls_layout.addWidget(interval_label, 1, 2)
        controls_layout.addWidget(self.background_sample_interval_spin, 1, 3)
        controls_layout.addWidget(self.pushButton_sample_bg, 1, 4)
        self.pushButton_load_latest_bg = QPushButton("Load Latest", controls_group)
        background_action_layout = QHBoxLayout()
        background_action_layout.setContentsMargins(0, 0, 0, 0)
        background_action_layout.setSpacing(7)
        background_action_layout.addWidget(self.pushButton_save, 1)
        background_action_layout.addWidget(self.pushButton_load, 1)
        background_action_layout.addWidget(self.pushButton_load_latest_bg, 1)
        controls_layout.addLayout(background_action_layout, 2, 0, 1, 5)
        controls_layout.setColumnStretch(1, 1)
        controls_layout.setColumnStretch(3, 1)
        background_dialog_layout.addWidget(controls_group)

        self.background_path_label = QLabel("No background loaded", self.background_dialog)
        self.background_path_label.setWordWrap(True)
        self.background_path_label.setProperty("role", "field")
        background_dialog_layout.addWidget(self.background_path_label)
        background_close_buttons = QDialogButtonBox(
            QDialogButtonBox.Close,
            parent=self.background_dialog,
        )
        for button in background_close_buttons.buttons():
            button.setProperty("dialogAction", True)
        background_close_buttons.rejected.connect(self.background_dialog.reject)
        background_dialog_layout.addWidget(background_close_buttons)

        self.lineEdit.hide()
        self.lineEdit_3.hide()
        while self.verticalLayout_12.count():
            self.verticalLayout_12.takeAt(0)
        self.background_status_label = QLabel("Background: None", self.groupBox_7)
        self.background_status_label.setWordWrap(True)
        self.background_status_label.setProperty("role", "field")
        self.background_settings_button = QPushButton("Background...", self.groupBox_7)
        self.background_settings_button.setObjectName("pushButton_backgroundSettings")
        self.background_settings_button.setAccessibleName("Open background settings")
        self.pushButton_load_latest_bg.setAccessibleName("Load latest background")
        for button in (
            self.background_settings_button,
            self.pushButton_load_latest_bg,
        ):
            button.setProperty("tight", True)
            self._refresh_widget_style(button)
        self.verticalLayout_12.addWidget(self.checkBox_bg)
        self.verticalLayout_12.addWidget(self.background_status_label)
        self.verticalLayout_12.addWidget(self.background_settings_button)
        self.groupBox_7.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self._sync_energy_control_state()

    def _apply_optics_input_preset(self, element_id):
        presets = self.energy_config.get("optics_input_presets", {})
        if not isinstance(presets, dict):
            return
        preset = presets.get(str(element_id).strip())
        if not isinstance(preset, dict):
            return

        fields = (
            (self.doubleSpinBox_alpha_in, "alpha_x"),
            (self.doubleSpinBox_beta_in, "beta_x_m"),
            (self.doubleSpinBox_emi_in, "emittance_x_nm"),
        )
        try:
            values = tuple(float(preset[key]) for _widget, key in fields)
        except (KeyError, TypeError, ValueError) as exc:
            print(f"Invalid optics input preset for {element_id}: {exc}")
            return
        if not all(np.isfinite(value) for value in values) or values[1] <= 0 or values[2] <= 0:
            print(f"Invalid optics input preset for {element_id}: values must be finite and beta/emittance positive.")
            return

        for (widget, _key), value in zip(fields, values):
            widget.setValue(value)
        print(
            f"Loaded optics preset at {element_id}: alpha_x={values[0]:g}, "
            f"beta_x={values[1]:g} m, emittance_x={values[2]:g} nm."
        )

    def _connect_signals(self):
        self.lineEdit_expotime.returnPressed.connect(self.set_expotime)
        self.lineEdit_refresh.returnPressed.connect(self.set_refresh)
        self.comboBox_fitmethod.currentTextChanged.connect(self._handle_fit_method_change)
        self.comboBox_colormap.currentTextChanged.connect(self._handle_colormap_change)
        self.comboBox_start_element.currentTextChanged.connect(
            self._apply_optics_input_preset
        )

        self.checkBox_emit.clicked.connect(lambda: self.emit_withornot(self.checkBox_emit.isChecked()))
        self.pushButton_cal_disp.clicked.connect(lambda: self.cal_disp(archive_result=True))
        self.pushButton_cal_twiss_disp.clicked.connect(lambda: self.cal_twiss_disp(archive_result=True))

        self.pushButton_sample_bg.clicked.connect(self.background_samples)
        self.pushButton_save.clicked.connect(self.save_bgfile)
        self.pushButton_load.clicked.connect(self.load_bgfile)
        self.pushButton_load_latest_bg.clicked.connect(self._load_latest_background)
        self.background_settings_button.clicked.connect(self._show_background_dialog)
        self.checkBox_bg.clicked.connect(lambda: self.bg_removeornot(self.checkBox_bg.isChecked()))

        self.slider_energy.valueChanged.connect(self._update_energy_slider_label)
        self.slider_energy.sliderReleased.connect(self.set_bend_quad)
        self.target_energy_spin.valueChanged.connect(self._update_energy_slider_from_spin)
        self.target_energy_spin.editingFinished.connect(self.set_bend_quad)
        self.auto_tune_objective_combo.currentIndexChanged.connect(
            self._sync_energy_control_state
        )
        self.auto_tune_objective_combo.currentIndexChanged.connect(
            self._update_auto_tune_settings_summary
        )
        self.auto_tune_settings_button.clicked.connect(self._show_auto_tune_settings)
        self.pushButton_autoFind.clicked.connect(self.run_esa_auto_tune)
        self.pushButton_stopAutoFind.clicked.connect(self.stop_esa_auto_tune)

    def _name_operator_controls(self):
        self.pushButton_sample_bg.setObjectName("pushButton_sample_bg")
        self.lineEdit_samples.setObjectName("lineEdit_bg_samples")
        self.lineEdit.setObjectName("lineEdit_bg_save_path_legacy")
        self.lineEdit_3.setObjectName("lineEdit_bg_load_path_legacy")

        names = {
            self.lineEdit_expotime: "Exposure time input",
            self.lineEdit_refresh: "Refresh interval input",
            self.comboBox_colormap: "Image color map selector",
            self.comboBox_fitmethod: "Spectrum fit method selector",
            self.label_energy: "Energy center readout",
            self.label_energyspread: "Energy spread readout",
            self.checkBox_emit: "Subtract emittance contribution toggle",
            self.checkBox_bg: "Subtract background image toggle",
            self.comboBox_start_element: "Optics input element selector",
            self.doubleSpinBox_alpha_in: "Input alpha x",
            self.doubleSpinBox_beta_in: "Input beta x",
            self.doubleSpinBox_emi_in: "Input emittance x",
            self.lineEdit_alpha_ESAflag: "Target alpha x readout",
            self.lineEdit_beta_ESAflag: "Target beta x readout",
            self.lineEdit_eta_ESAflag: "Target eta x readout",
            self.pushButton_cal_disp: "Update dispersion button",
            self.pushButton_cal_twiss_disp: "Update optics button",
            self.slider_energy: "Target energy slider",
            self.target_energy_spin: "Energy setpoint precise input",
            self.label_sliderenergy: "Target energy value",
            self.auto_tune_min_spin: "Auto Find minimum energy",
            self.auto_tune_max_spin: "Auto Find maximum energy",
            self.auto_tune_coarse_steps_spin: "Auto Find coarse scan points",
            self.auto_tune_fine_steps_spin: "Auto Find fine scan points",
            self.auto_tune_settle_spin: "Auto Find settle time",
            self.auto_tune_objective_combo: "Auto Find search method",
            self.auto_tune_frame_samples_spin: "Auto Find Fine and center frame count",
            self.auto_tune_min_valid_frames_spin: "Auto Find minimum valid frame count",
            self.auto_tune_verification_frames_spin: "Auto Find verification frame count",
            self.auto_tune_verification_min_valid_spin: "Auto Find verification minimum valid frames",
            self.auto_tune_frame_interval_spin: "Interval between fine-scan camera frames",
            self.auto_tune_probe_step_spin: "Fixed A3 step used by fitted-center search",
            self.auto_tune_center_tolerance_spin: "Final fitted-center tolerance",
            self.auto_tune_max_offset_spin: "Maximum fitted-center energy offset",
            self.auto_tune_settings_button: "Open Auto Find settings",
            self.pushButton_autoFind: "Auto Find start button",
            self.pushButton_stopAutoFind: "Auto Find stop button",
            self.background_plot: "Background preview plot",
            self.lineEdit_samples: "Background sample count input",
            self.pushButton_sample_bg: "Sample background button",
            self.pushButton_save: "Save background button",
            self.pushButton_load: "Load background button",
        }
        for widget, name in names.items():
            widget.setAccessibleName(name)

    def _apply_combo_palette(self):
        palette = self._palette()
        qt_palette = QPalette()
        qt_palette.setColor(QPalette.Base, QColor(palette["input_bg"]))
        qt_palette.setColor(QPalette.Text, QColor(palette["input_fg"]))
        qt_palette.setColor(QPalette.Button, QColor(palette["input_bg"]))
        qt_palette.setColor(QPalette.ButtonText, QColor(palette["input_fg"]))
        qt_palette.setColor(QPalette.Highlight, QColor(palette["metric_active_fg"]))
        qt_palette.setColor(QPalette.HighlightedText, QColor(palette["window_bg"]))
        combo_style = f"""
            QComboBox {{
                background-color: {palette["input_bg"]};
                color: {palette["input_fg"]};
                border: 1px solid {palette["input_border"]};
                border-radius: 10px;
                padding: 5px 8px;
                min-height: 14px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {palette["input_bg"]};
                color: {palette["input_fg"]};
                selection-background-color: {palette["metric_active_fg"]};
                selection-color: {palette["window_bg"]};
            }}
        """
        view_style = f"""
            QListView {{
                background-color: {palette["input_bg"]};
                color: {palette["input_fg"]};
                border: 1px solid {palette["input_border"]};
                outline: 0;
            }}
            QListView::item {{
                background-color: {palette["input_bg"]};
                color: {palette["input_fg"]};
                min-height: 24px;
                padding: 3px 8px;
            }}
            QListView::item:selected {{
                background-color: {palette["metric_active_fg"]};
                color: {palette["window_bg"]};
            }}
            QListView::item:hover {{
                background-color: {palette["button_hover_bg"]};
                color: {palette["input_fg"]};
            }}
        """
        for combo in (
            self.comboBox_colormap,
            self.comboBox_fitmethod,
            self.comboBox_start_element,
            self.auto_tune_objective_combo,
        ):
            if not isinstance(combo.view(), QListView):
                combo.setView(QListView(combo))
            combo.setPalette(qt_palette)
            combo.setStyleSheet(combo_style)
            view = combo.view()
            if view is not None:
                view.setPalette(qt_palette)
                view.setStyleSheet(view_style)

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
        self.target_energy_spin.setEnabled(slider_enabled)
        if self._auto_tune_is_running():
            self.slider_energy.setToolTip("Disabled while bend scan is running.")
        elif slider_enabled:
            self.slider_energy.setToolTip(f"Release to write target energy to {self.energy_set_pv}.")
        elif self.control_backend == "real" and not writes_allowed:
            self.slider_energy.setToolTip("Real-machine energy writes are blocked by machine profile.")
        elif self.control_backend == "vm":
            self.slider_energy.setToolTip("VM backend does not support direct energy setpoint control.")
        else:
            self.slider_energy.setToolTip("No energy_set_pv configured for the real backend.")
        self.target_energy_spin.setToolTip(self.slider_energy.toolTip())

        auto_tune_enabled = (
            self.control_backend == "real"
            and not self._auto_tune_is_running()
            and writes_allowed
            and self._auto_tune_configured_for_backend()
        )
        self.pushButton_autoFind.setEnabled(auto_tune_enabled)
        scan_running = self._auto_tune_is_running()
        self.auto_tune_settings_button.setEnabled(not scan_running)
        self.background_settings_button.setEnabled(not scan_running)
        self.checkBox_bg.setEnabled(not scan_running)
        self.comboBox_fitmethod.setEnabled(not scan_running)
        for widget in self.auto_tune_parameter_widgets:
            widget.setEnabled(not scan_running)
        center_lock_controls_enabled = (
            not scan_running
            and self.auto_tune_objective_combo.currentData()
            == "brightness_then_profile_lock"
        )
        for widget in (
            self.auto_tune_frame_interval_spin,
            self.auto_tune_probe_step_spin,
            self.auto_tune_center_tolerance_spin,
            self.auto_tune_verification_frames_spin,
            self.auto_tune_verification_min_valid_spin,
            self.auto_tune_max_offset_spin,
        ):
            widget.setEnabled(center_lock_controls_enabled)
        stop_requested = (
            scan_running
            and self.auto_tune_thread is not None
            and self.auto_tune_thread.isInterruptionRequested()
        )
        self.pushButton_stopAutoFind.setEnabled(scan_running and not stop_requested)
        self.pushButton_stopAutoFind.setText("Stopping..." if stop_requested else "Stop")
        self.pushButton_stopAutoFind.setToolTip(
            "Stop scanning and restore the energy that was active before Auto Find."
        )
        if self._auto_tune_is_running():
            self.pushButton_autoFind.setText("Scanning...")
            self.pushButton_autoFind.setToolTip("Auto Find is scanning the configured ESA actuator.")
        else:
            self.pushButton_autoFind.setText("Auto Find")
            if (
                self.control_backend == "real"
                and writes_allowed
                and self._auto_tune_configured_for_backend()
            ):
                self.pushButton_autoFind.setToolTip(
                    f"Scan {self.auto_tune_pv} to locate the ESA beam on {self.flag_pv}."
                )
            elif self.control_backend == "real" and not self._auto_tune_configured_for_backend():
                self.pushButton_autoFind.setToolTip(
                    "Direct bend scan is disabled for this backend; use the coordinated energy control."
                )
            elif self.control_backend == "real":
                self.pushButton_autoFind.setToolTip("Real-machine bend scan is blocked by machine profile.")
            else:
                self.pushButton_autoFind.setToolTip(
                    "VM backend does not provide a coupled ESA response, so bend scan is disabled."
                )

    def _apply_theme(self):
        palette = self._palette()
        theme = build_energy_spectrum_theme(palette)
        self.setStyleSheet(theme)
        for dialog_name in ("auto_tune_settings_dialog", "background_dialog"):
            dialog = getattr(self, dialog_name, None)
            if dialog is not None:
                dialog.setStyleSheet(theme)
                for button in dialog.findChildren(QPushButton):
                    self._refresh_widget_style(button)
        for title_label in getattr(self, "workspace_card_title_labels", ()):
            title_label.setStyleSheet(
                "color: {color}; font-size: 13px; font-weight: 700; "
                "background: transparent; border: none;".format(
                    color=palette["summary_title_fg"]
                )
            )
            title_label.adjustSize()
        if hasattr(self, "status_panel"):
            self.status_panel.apply_theme(palette)
            self.status_panel.setFixedHeight(self.status_panel.sizeHint().height())
        self._update_theme_toggle_button()
        self._apply_combo_palette()
        self._style_all_plots()

    def _palette(self):
        return DARK_THEME if self.current_theme == "dark" else LIGHT_THEME

    def _update_theme_toggle_button(self):
        if not hasattr(self, "theme_toggle_button"):
            return
        if self.current_theme == "dark":
            self.theme_toggle_button.setText("\u2600")
            self.theme_toggle_button.setToolTip("switch to light theme.")
        else:
            self.theme_toggle_button.setText("\u263D")
            self.theme_toggle_button.setToolTip("switch to dark theme.")

    def _toggle_theme(self):
        self.current_theme = "light" if self.current_theme == "dark" else "dark"
        self._apply_theme()
        if self._pv_available:
            self.ESA_running(write_latest=False)
        else:
            self._draw_placeholder_views()
        self._refresh_background_preview()
        self._refresh_status()

    def _handle_colormap_change(self):
        if self._pv_available:
            self.ESA_running(write_latest=False)
        else:
            self._draw_placeholder_views()
        self._refresh_background_preview()

    def _handle_fit_method_change(self):
        self._update_fit_status(self.comboBox_fitmethod.currentText())
        if self._pv_available:
            self.ESA_running()
        self._refresh_status()

    @staticmethod
    def _refresh_widget_style(widget):
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()

    def _style_axes(self, widget, xlabel, ylabel, title=None):
        palette = self._palette()
        widget.fig.patch.set_facecolor(palette["plot_card_bg"])
        widget.fig.patch.set_edgecolor(palette["plot_card_bg"])
        widget.fig.patch.set_linewidth(0.0)
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
        self._draw_placeholder_plot(self.ESAflag_image, None, "x (mm)", "y (mm)", note=note)
        self._draw_placeholder_plot(
            self.energy_plot,
            None,
            "E (MeV)",
            "Spectrum (arb. units)",
            note=note,
        )
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

    def _update_fit_status(self, text, tone="subtle", tooltip=None):
        self._fit_text = text
        self._fit_tone = tone
        self._fit_tooltip = tooltip

    def _set_readout_status(self, text, tone, tooltip=None):
        self._readout_text = text
        self._readout_tone = tone
        self._readout_tooltip = tooltip

    def _clear_readout_status(self):
        self._readout_text = None
        self._readout_tone = None
        self._readout_tooltip = None

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

        self.status_panel.set_item("fit", self._fit_text, self._fit_tone, self._fit_tooltip)
        self.status_panel.set_item("model", self._model_text, self._model_tone, self._model_tooltip)
        self.status_panel.set_item("tune", self._auto_tune_text, self._auto_tune_tone)

        energy_text = self.label_energy.text().strip()
        spread_text = self.label_energyspread.text().strip()
        if self._readout_text is not None:
            self.status_panel.set_item(
                "readout",
                self._readout_text,
                self._readout_tone or "subtle",
                self._readout_tooltip,
            )
        elif energy_text and energy_text != "N/A" and spread_text and spread_text != "N/A":
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
        self._clear_readout_status()
        self.label_energy.setText("{:.4f}".format(energy_center))
        self.label_energyspread.setText("{:.4f}".format(energy_spread * 1e2))
        self._refresh_status()

    def _set_energy_unavailable(self, status_text=None, tooltip=None, *, energy_center=None):
        if energy_center is None:
            self.label_energy.setText("N/A")
        else:
            self.label_energy.setText("{:.4f}".format(energy_center))
        self.label_energyspread.setText("N/A")
        if status_text:
            self._set_readout_status(status_text, "warning", tooltip)
        else:
            self._clear_readout_status()
        self._refresh_status()

    def _write_energy_result_metadata(
        self,
        *,
        energy0_mev,
        energy0_source,
        energy0_source_pv,
        energy_center_mev,
        energy_spread,
        fit_method,
        archive_result=False,
    ):
        paths = resolve_energy_spectrum_runtime_paths(self.app_context)
        metadata = {
            "schema_version": "energy_spectrum_result_v1",
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "machine_id": self.machine_profile.machine.id,
            "machine_display_name": self.machine_profile.machine.display_name,
            "backend": self.control_backend,
            "fit_method": fit_method,
            "x_reference_mm": self.x_reference_mm,
            "meanx_mm": float(self.meanx),
            "sigx_mm": float(self.sigx),
            "energy0_mev": float(energy0_mev),
            "energy0_source": str(energy0_source),
            "energy_center_mev": float(energy_center_mev),
            "energy_spread_fraction": float(energy_spread),
            "eta_m": float(self.eta_flag),
            "beta_m": float(self.beta_flag),
            "emittance_m": float(self.emi_flag),
            "include_emit": bool(self.with_emit),
        }
        if energy0_source_pv:
            metadata["energy0_source_pv"] = str(energy0_source_pv)
        if isinstance(self.latest_model_snapshot_metadata, dict):
            metadata["model_snapshot"] = dict(self.latest_model_snapshot_metadata)
        else:
            metadata["model_snapshot_warning"] = (
                "No model_snapshot was available when this energy result was calculated."
            )

        try:
            paths["latest_dir"].mkdir(parents=True, exist_ok=True)
            metadata_text = json.dumps(metadata, indent=2, sort_keys=True)
            paths["latest_metadata_path"].write_text(metadata_text, encoding="utf-8")
            if archive_result:
                paths["result_archive_dir"].mkdir(parents=True, exist_ok=True)
                timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
                archive_dir = paths["result_archive_dir"] / f"energy_result_{timestamp}"
                suffix = 2
                while archive_dir.exists():
                    archive_dir = paths["result_archive_dir"] / f"energy_result_{timestamp}_{suffix}"
                    suffix += 1
                archive_dir.mkdir(parents=True, exist_ok=True)
                archive_path = archive_dir / "metadata.json"
                archive_path.write_text(metadata_text, encoding="utf-8")
        except (OSError, ValueError) as exc:
            print(f"Warning: failed to save energy spectrum result metadata: {exc}")


    def _background_metadata(self, *, source, sample_count=None):
        exposure_s = None
        try:
            exposure_s = float(self.lineEdit_expotime.text())
        except (TypeError, ValueError):
            pass
        metadata = dict(self.bg_metadata)
        metadata.update(
            {
                "schema_version": "energy_spectrum_background_v1",
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "machine_id": self.machine_profile.machine.id,
                "backend": self.control_backend,
                "flag_pv": self.flag_pv,
                "shape": [self.flag_pixel[1], self.flag_pixel[0]],
                "pixel_width_mm": self.flag_pixel_width_mm,
                "source": str(source),
            }
        )
        if exposure_s is not None and np.isfinite(exposure_s):
            metadata["exposure_s"] = exposure_s
        if sample_count is not None:
            metadata["sample_count"] = int(sample_count)
        return metadata

    def _set_background_image(self, image, metadata, image_path):
        self.bg_image = image
        self.bg_metadata = dict(metadata)
        self.bg_image_path = Path(image_path)
        saved_exposure = self.bg_metadata.get("exposure_s")
        try:
            saved_exposure = float(saved_exposure)
        except (TypeError, ValueError):
            saved_exposure = None
        try:
            current_exposure = float(self.lineEdit_expotime.text())
        except (TypeError, ValueError):
            current_exposure = None
        if (
            saved_exposure is not None
            and current_exposure is not None
            and not np.isclose(saved_exposure, current_exposure)
        ):
            print(
                "[GUI] Background exposure warning: "
                f"saved={saved_exposure:g} s, current={current_exposure:g} s."
            )
        self._refresh_background_preview()
        self._update_background_status()

    def _update_background_status(self):
        if self.bg_image is None:
            summary = "Background: None"
            detail = "No background loaded"
        else:
            created_at = str(self.bg_metadata.get("created_at", "unknown time"))
            filename = self.bg_image_path.name if self.bg_image_path else "in memory"
            summary = f"Background: {filename} · {created_at}"
            detail = str(self.bg_image_path or "Sampled background is not saved")
        self.background_status_label.setText(summary)
        self.background_path_label.setText(detail)

    def _show_background_dialog(self):
        if self._auto_tune_is_running():
            return
        self._refresh_background_preview()
        self._update_background_status()
        self.background_dialog.exec_()

    def _save_latest_background(self, *, sample_count=None):
        if self.bg_image is None:
            raise BackgroundStoreError("No background image is available to save.")
        paths = resolve_energy_spectrum_runtime_paths(self.app_context)
        metadata = self._background_metadata(
            source="sampled_latest",
            sample_count=sample_count,
        )
        image_path, _metadata_path = save_background(
            self.bg_image,
            paths["background_image_path"],
            paths["background_metadata_path"],
            metadata,
        )
        self.bg_metadata = metadata
        self.bg_image_path = image_path
        self._update_background_status()
        return image_path

    def _load_latest_background(self, _checked=False, *, silent=False):
        paths = resolve_energy_spectrum_runtime_paths(self.app_context)
        image_path = paths["background_image_path"]
        if not image_path.is_file():
            if not silent:
                self._warn(f"No latest background exists at {image_path}.")
            self._update_background_status()
            return False
        try:
            image, metadata = load_background(
                image_path,
                paths["background_metadata_path"],
                expected_shape=(self.flag_pixel[1], self.flag_pixel[0]),
            )
        except BackgroundStoreError as exc:
            if silent:
                print(f"[GUI] Could not auto-load latest background: {exc}")
            else:
                self._warn(str(exc))
            return False
        self._set_background_image(image, metadata, image_path)
        print(f"background image loaded from {image_path}")
        return True

    def background_samples(self):
        """sample background image and subtract later"""
        n_samples = self._get_positive_int(self.lineEdit_samples, "background sample count")
        if n_samples is None:
            return
        print(f"sampling {n_samples} background images...")
        sample_interval_s = self.background_sample_interval_spin.value()
        bg_images = []
        for i in range(n_samples):
            if i > 0 and sample_interval_s > 0:
                time.sleep(sample_interval_s)
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
        self.bg_metadata = self._background_metadata(
            source="sampled",
            sample_count=n_samples,
        )
        try:
            image_path = self._save_latest_background(sample_count=n_samples)
        except (BackgroundStoreError, OSError, ValueError) as exc:
            self.bg_image_path = None
            print(f"background sampling done, but automatic save failed: {exc}")
        else:
            print(f"background sampling done and saved to {image_path}")
        self._refresh_background_preview()
        self._update_background_status()
        self._refresh_status()

    def _create_background_file_dialog(self, title, initial_path, *, save):
        dialog = QFileDialog(self)
        dialog.setObjectName("backgroundFileDialog")
        dialog.setWindowTitle(title)
        dialog.setOption(QFileDialog.DontUseNativeDialog, True)
        dialog.setNameFilters(("NumPy Files (*.npy)", "All Files (*)"))
        dialog.selectNameFilter("NumPy Files (*.npy)")

        initial_path = Path(initial_path)
        initial_directory = initial_path.parent if initial_path.suffix else initial_path
        dialog.setDirectory(str(initial_directory))
        if save:
            dialog.setAcceptMode(QFileDialog.AcceptSave)
            dialog.setFileMode(QFileDialog.AnyFile)
            dialog.setDefaultSuffix("npy")
            dialog.selectFile(initial_path.name)
        else:
            dialog.setAcceptMode(QFileDialog.AcceptOpen)
            dialog.setFileMode(QFileDialog.ExistingFile)
            if initial_path.is_file():
                dialog.selectFile(initial_path.name)

        palette = self._palette()
        qt_palette = QPalette(dialog.palette())
        qt_palette.setColor(QPalette.Window, QColor(palette["window_bg"]))
        qt_palette.setColor(QPalette.WindowText, QColor(palette["window_fg"]))
        qt_palette.setColor(QPalette.Base, QColor(palette["input_bg"]))
        qt_palette.setColor(QPalette.AlternateBase, QColor(palette["panel_bg"]))
        qt_palette.setColor(QPalette.Text, QColor(palette["input_fg"]))
        qt_palette.setColor(QPalette.Button, QColor(palette["button_bg"]))
        qt_palette.setColor(QPalette.ButtonText, QColor(palette["button_fg"]))
        qt_palette.setColor(QPalette.Highlight, QColor(palette["metric_active_fg"]))
        qt_palette.setColor(QPalette.HighlightedText, QColor(palette["window_bg"]))
        dialog.setPalette(qt_palette)
        dialog.setStyleSheet(
            build_energy_spectrum_theme(palette)
            + build_background_file_dialog_theme(palette)
        )
        for child in dialog.findChildren(QWidget):
            child.setPalette(qt_palette)
        return dialog

    def _choose_background_file(self, title, initial_path, *, save):
        dialog = self._create_background_file_dialog(title, initial_path, save=save)
        accepted = dialog.exec_() == QDialog.Accepted
        selected_files = dialog.selectedFiles() if accepted else ()
        dialog.deleteLater()
        return selected_files[0] if selected_files else None
    
    def save_bgfile(self):
        """Save the current background as an archived operator-selected file."""
        if self.bg_image is None:
            print("No background image to save!")
            return
        paths = resolve_energy_spectrum_runtime_paths(self.app_context)
        paths["runs_dir"].mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
        default_path = paths["runs_dir"] / f"background_{timestamp}.npy"
        filePath = self._choose_background_file(
            "Save Background Image",
            default_path,
            save=True,
        )
        if filePath:
            image_path = Path(filePath)
            if image_path.suffix.lower() != ".npy":
                image_path = image_path.with_suffix(".npy")
            metadata_path = image_path.with_suffix(".json")
            metadata = self._background_metadata(source="save_as")
            try:
                save_background(self.bg_image, image_path, metadata_path, metadata)
            except (BackgroundStoreError, OSError, ValueError) as exc:
                self._warn(f"Could not save background: {exc}")
                return
            self.bg_metadata = metadata
            self.bg_image_path = image_path
            self._update_background_status()
            print(f"background image saved to {image_path}")
    
    def load_bgfile(self):
        """load the background image from a file"""
        paths = resolve_energy_spectrum_runtime_paths(self.app_context)
        default_path = paths["background_image_path"]
        initial_path = default_path if default_path.is_file() else paths["latest_dir"]
        filePath = self._choose_background_file(
            "Load Background Image",
            initial_path,
            save=False,
        )
        if filePath:
            image_path = Path(filePath)
            metadata_path = image_path.with_suffix(".json")
            try:
                image, metadata = load_background(
                    image_path,
                    metadata_path,
                    expected_shape=(self.flag_pixel[1], self.flag_pixel[0]),
                )
            except BackgroundStoreError as exc:
                self._warn(str(exc))
                return
            self._set_background_image(image, metadata, image_path)
            print(f"background image loaded from {image_path}")

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
        self.flag_pixel_width_mm = flag_pixel_width

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
        self.timer.timeout.connect(lambda: self.ESA_running(write_latest=False))
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


    def ESA_running(self, write_latest=True, archive_result=False):
        palette = self._palette()
        self.fit_method = self.comboBox_fitmethod.currentText()
        self._update_fit_status(self.fit_method)
        self._clear_readout_status()

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
            self._set_energy_unavailable("No image", f"{self.flag_pv} returned no image data.")
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
            self._set_energy_unavailable("Bad image shape", str(exc))
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
        try:
            projection = project_image_profiles(data, self.flag_pixel_width_mm)
        except SpectrumProfileError as exc:
            self.sigx = None
            self.sigy = None
            print(f"Warning: ESA projection failed: {exc}")
            self._set_energy_unavailable("Bad projection", str(exc))
            self._refresh_status()
            return
        x = projection.x_mm
        y = projection.y_mm
        data = projection.image

        # projection density
        denx0 = projection.density_x
        deny0 = projection.density_y
        if np.max(denx0) == 0 or np.max(deny0) == 0:
            self.sigx = None
            self.sigy = None
            print("Warning: ESA projection is empty; skipping spectrum update.")
            self.ESAflag_image.canvas.draw()
            self._draw_placeholder_plot(self.energy_plot, "Energy Spectrum", "E (MeV)", "Spectrum (arb. units)")
            self._update_fit_status("No beam", "warning", "Projection inside the selected image region is empty.")
            self._set_energy_unavailable("No beam", "Projection inside the selected image region is empty.")
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

        
        
        # Use one shared center definition for the GUI and Auto Find center lock.
        try:
            profile_fit = fit_projection_profile(x, denx0, fit_method)
        except SpectrumProfileError as exc:
            self.sigx = None
            self.sigy = None
            print(f"Spectrum profile fit failed: {exc}")
            self._update_fit_status("Fit failed", "warning", str(exc))
            self._set_energy_unavailable("Fit failed", str(exc))
            self._refresh_status()
            return
        fit_method = profile_fit.method
        fit_norm_denx = profile_fit.fitted_density
        self.meanx = profile_fit.center_mm
        self.sigx = profile_fit.sigma_mm
        self.sigy = None

        if profile_fit.fallback_error is not None:
            print(
                "Gauss fit failed, falling back to direct moments: "
                f"{profile_fit.fallback_error}"
            )
            self._update_fit_status(
                "Direct fallback",
                "warning",
                f"Gauss fit failed: {profile_fit.fallback_error}",
            )
        if fit_method == "Gauss fit":
            fit_denx = fit_norm_denx * self.height * 0.3 + self.ylim[0] * 0.98
            self.ESAflag_image.axes.plot(
                x,
                fit_denx,
                "--",
                color=palette["plot_fit"],
                linewidth=1.4,
            )
            if profile_fit.fallback_error is None:
                self._update_fit_status("Gauss OK", "success")
        else:
            if self._fit_text == self.comboBox_fitmethod.currentText():
                self._update_fit_status("Direct", "success")
            # Keep the existing spline only as a display curve; it does not define center.
            try:
                spline = UnivariateSpline(x, norm_denx, s=0.1)
                fit_norm_denx = spline(x)
                fit_denx = fit_norm_denx * self.height * 0.3 + self.ylim[0] * 0.98
                self.ESAflag_image.axes.plot(
                    x,
                    fit_denx,
                    "--",
                    color=palette["plot_fit"],
                    linewidth=1.4,
                    alpha=0.8,
                )
            except (ValueError, RuntimeError, FloatingPointError) as exc:
                print(f"spline fit failed: {exc}")
        self.ESAflag_image.canvas.draw()


        # -----------------
        # Reference energy corresponding to x_reference_mm.
        energy0, energy0_source, energy0_source_pv = self._read_reference_energy_mev()

        # dispersion calculation and display 
        # self.cal_disp()
        if np.isclose(self.eta_flag, 0.0):
            print("Warning: eta_flag is zero; skipping energy calculation.")
            self._draw_placeholder_plot(self.energy_plot, "Energy Spectrum", "E (MeV)", "Spectrum (arb. units)")
            self._set_energy_unavailable("No eta", "ESA dispersion is zero. Run Update eta or Update optics.")
            self._refresh_status()
            return

        # energy_center and energy_spread calculation and display
        dx_center_m = (self.meanx - self.x_reference_mm) * 1e-3
        energy_center = energy0 * dx_center_m / self.eta_flag + energy0 # MeV
        energy_all = [
            energy0 * (xi - self.x_reference_mm) * 1e-3 / self.eta_flag + energy0
            for xi in x
        ]

        if self.with_emit and (self.beta_flag <= 0 or self.emi_flag <= 0):
            message = "Include emit is enabled, but optics/emittance at the ESA flag are not available."
            print(f"Warning: {message}")
            self._set_energy_unavailable(
                "Update optics first",
                message,
                energy_center=energy_center,
            )
            self.energy_plot.axes.clear()
            self._style_axes(self.energy_plot, "E (MeV)", "Spectrum (arb. units)")
            self.energy_plot.axes.plot(energy_all, norm_denx, "--", color=palette["plot_energy"], linewidth=1.4, label="projection")
            self.energy_plot.canvas.draw()
            self._refresh_status()
            return

        if self.with_emit == True: # 考虑发射度贡献
            spread_term = (((self.sigx*1e-3)**2 - self.beta_flag * self.emi_flag) / self.eta_flag ** 2)
        elif self.with_emit == False: # 不考虑发射度贡献
            spread_term = (((self.sigx*1e-3)**2 - 0 * 0) / self.eta_flag ** 2)
        if spread_term < 0:
            print(f"Warning: negative energy spread term {spread_term}; clamping to zero.")
            self._set_energy_unavailable(
                "Invalid spread",
                "Projected beam size is smaller than the emittance term; check optics, eta, and image calibration.",
                energy_center=energy_center,
            )
            self.energy_plot.axes.clear()
            self._style_axes(self.energy_plot, "E (MeV)", "Spectrum (arb. units)")
            self.energy_plot.axes.plot(energy_all, norm_denx, "--", color=palette["plot_energy"], linewidth=1.4, label="projection")
            self.energy_plot.canvas.draw()
            self._refresh_status()
            return
        energy_spread = math.sqrt(spread_term) * energy0 / energy_center
        
        self._set_energy_outputs(energy_center, energy_spread)
        should_archive = archive_result or self._archive_next_energy_result
        if write_latest or should_archive:
            self._write_energy_result_metadata(
                energy0_mev=energy0,
                energy0_source=energy0_source,
                energy0_source_pv=energy0_source_pv,
                energy_center_mev=energy_center,
                energy_spread=energy_spread,
                fit_method=fit_method,
                archive_result=should_archive,
            )
        self._archive_next_energy_result = False

        # plot energy profile in another figure
        self.energy_plot.axes.clear()
        self._style_axes(self.energy_plot, "E (MeV)", "Spectrum (arb. units)")
        self.energy_plot.axes.plot(energy_all, norm_denx, "--", color=palette["plot_energy"], linewidth=1.4, label="projection")
        if fit_method == "direct":
            self.energy_plot.axes.plot(energy_all, fit_norm_denx, "--", color=palette["plot_fit"], linewidth=1.4, label="spline fit")
        elif fit_method.lower() in ("gauss", "gauss fit"):
            self.energy_plot.axes.plot(energy_all, fit_norm_denx, "--", color=palette["plot_fit"], linewidth=1.4, label="Gauss fit")
        legend = self.energy_plot.axes.legend(frameon=False)
        if legend is not None:
            for text in legend.get_texts():
                text.set_color(palette["plot_text"])
        self.energy_plot.canvas.draw()  # 强制刷新
        self._refresh_status()

    def cal_disp(self, archive_result=False):
        if not self._model_available():
            message = f"Model backend unavailable: {self._model_unavailable_message()}"
            print(message)
            self._use_design_eta(tooltip=message)
            self._refresh_status()
            return

        try:
            # 根据ESA的弯铁SM(L, angle)和Q铁QE01 QE02 QE03(k,L) 漂移段(L)参数计算eta    变量仅为Q_k
            # 采用elegant计算

            snapshot = self._build_esa_quad_model_snapshot()

            #
            lattice_file = self._energy_model_path("source_lattice")
            esa_ini_ele_file = self._energy_model_path("energy_ini_ele_file")
            line_name = self.energy_model_config["energy_dispersion_line_name"]
            working_dir = self._energy_model_working_dir()

            esajson_path = self._energy_model_path("energy_json_path")
            esa_lte_file = self._energy_model_path("energy_lte_file")
            esa_ele_file = self._energy_model_path("energy_ele_file")
            esa_mat_file = self._energy_model_path("energy_mat_file")
            prepare_elegant_model_workdir(
                working_dir,
                output_paths=(esajson_path, esa_lte_file, esa_ele_file, esa_mat_file),
            )

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
            self._apply_lattice_overrides(lattice, snapshot.lattice_overrides)

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
            self._update_model_status(
                f"{self._snapshot_status_label(snapshot)} eta {self.eta_flag:.4f} m",
                "success",
                self._snapshot_status_tooltip(snapshot),
            )
        
        except MachineProfileError as e:
            print(f"Error in cal_disp: {e}")
            self.latest_model_snapshot_metadata = None
            self.latest_model_snapshot_path = None
            self._update_model_status("Snapshot invalid", "warning", str(e))
            self._refresh_status()
            return
        except Exception as e:
            print(f"Error in cal_disp: {e}")
            self.eta_flag = DEFAULT_DESIGN_ETA  # 理论设计值
            self.latest_model_snapshot_metadata = None
            self.latest_model_snapshot_path = None
            print('default dispersion: ',self.eta_flag, 'm')
            self._update_model_status(f"design eta {self.eta_flag:.4f} m", "warning")
            
        self.lineEdit_eta_ESAflag.setText(str(round(self.eta_flag,5)))
        if archive_result:
            self._archive_next_energy_result = True
        self._refresh_status()

    def cal_twiss_disp(self, archive_result=False):
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
        try:
            snapshot = self._build_esa_quad_model_snapshot()
        except MachineProfileError as e:
            print(f"Error in cal_twiss_disp: {e}")
            self.latest_model_snapshot_metadata = None
            self.latest_model_snapshot_path = None
            self._update_model_status("Snapshot invalid", "warning", str(e))
            self._refresh_status()
            return

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
        working_dir = self._energy_model_working_dir()

        esajson_path = self._energy_model_path("energy_json_path")
        esa_lte_file = self._energy_model_path("energy_lte_file")
        esa_ele_file = self._energy_model_path("energy_ele_file")
        esa_twi_file = self._energy_model_path("energy_twi_file")
        prepare_elegant_model_workdir(
            working_dir,
            output_paths=(esajson_path, esa_lte_file, esa_ele_file, esa_twi_file),
        )

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

            self._apply_lattice_overrides(lattice, snapshot.lattice_overrides)

            contl['run_setup']['lattice'] = esa_lte_file.name
            contl['twiss_output']['beta_x'] = str(beta_in)
            contl['twiss_output']['alpha_x'] = str(alpha_in)

            # Map from the entrance of start_element to the configured flag/watch element.
            target_element = self._twiss_target_element()
            id1 = usedline.index(start_element)
            id2 = usedline.index(target_element)
            if id2 < id1:
                raise MachineProfileError(
                    f"Twiss target {target_element!r} is upstream of start element {start_element!r}."
                )
            scanline = usedline[id1 : id2 + 1]

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
            self.latest_model_snapshot_metadata = None
            self.latest_model_snapshot_path = None
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
        self._update_model_status(
            f"{self._snapshot_status_label(snapshot)} eta {self.eta_flag:.4f} m",
            "success",
            self._snapshot_status_tooltip(snapshot),
        )
        if archive_result:
            self._archive_next_energy_result = True
        self._refresh_status()

    def emit_withornot(self, state):
        if state:
            self.with_emit = True
            
        if not state:
            self.with_emit = False
        if self._pv_available:
            self.ESA_running()

    def _update_energy_slider_label(self, value):
        energy = float(value) / self._energy_slider_scale
        spin_was_blocked = self.target_energy_spin.blockSignals(True)
        try:
            self.target_energy_spin.setValue(energy)
        finally:
            self.target_energy_spin.blockSignals(spin_was_blocked)
        self.label_sliderenergy.setText(f"{energy:.2f}")

    def _update_energy_slider_from_spin(self, value):
        slider_value = int(round(float(value) * self._energy_slider_scale))
        slider_value = min(max(slider_value, self.slider_energy.minimum()), self.slider_energy.maximum())
        slider_was_blocked = self.slider_energy.blockSignals(True)
        try:
            self.slider_energy.setValue(slider_value)
        finally:
            self.slider_energy.blockSignals(slider_was_blocked)

    def set_bend_quad(self):
        """
        update the energy0 value according to slider position
        这里energy0是由ESA的弯铁强度决定的
        """
        target_energy = self.target_energy_spin.value()
        self._set_target_energy_control(target_energy)
        if self.control_backend != "real":
            return
        if not self.energy_set_pv:
            print("No energy setpoint PV is configured for the real backend.")
            return
        if self.energy_set_limits is not None:
            low, high = self.energy_set_limits
            if not low <= target_energy <= high:
                self._warn(
                    f"Target energy {target_energy:g} MeV is outside the configured "
                    f"range [{low:g}, {high:g}] MeV."
                )
                return
        try:
            self._require_write_allowed("ESA target energy write")
        except MachineProfileError as exc:
            self._warn(str(exc))
            self._sync_energy_control_state()
            return

        try:
            caput(self.energy_set_pv, float(target_energy))
            print(f"ESA target energy set to {target_energy:.2f} MeV via {self.energy_set_pv}")
        except Exception as exc:
            self._mark_pv_unavailable(exc)
            self._refresh_status()
            print(f"Failed to write ESA target energy: {exc}")

    def _start_auto_tune_run_log(self, bend_scan):
        self._close_auto_tune_run_log()
        paths = resolve_energy_spectrum_runtime_paths(self.app_context)
        start_values = {
            "machine_id": self.machine_profile.machine.id,
            "backend": self.control_backend,
            "objective": bend_scan.get("objective"),
            "actuator_pv": self.auto_tune_pv,
            "x_reference_mm": bend_scan.get("x_reference_mm"),
            "scan_min_mev": bend_scan.get("min"),
            "scan_max_mev": bend_scan.get("max"),
            "coarse_points": bend_scan.get("coarse_steps"),
            "fine_points": bend_scan.get("fine_steps"),
            "frame_samples": bend_scan.get("frame_samples"),
            "min_valid_frames": bend_scan.get("min_valid_frames"),
            "verification_frame_samples": bend_scan.get(
                "verification_frame_samples"
            ),
            "verification_min_valid_frames": bend_scan.get(
                "verification_min_valid_frames"
            ),
            "frame_interval_s": bend_scan.get("frame_interval_s"),
            "settle_time_s": bend_scan.get("settle_time_s"),
            "center_step_mev": bend_scan.get("center_step"),
            "center_tolerance_mm": bend_scan.get("center_tolerance_mm"),
        }
        try:
            self._auto_tune_run_log = ESAAutoTuneRunLog.create(
                paths["runs_dir"],
                start_values,
            )
        except (OSError, ValueError) as exc:
            self._auto_tune_run_log = None
            self._auto_tune_log_path = None
            print(f"[GUI] Could not create ESA Auto Find log: {exc}")
            return
        self._auto_tune_log_path = self._auto_tune_run_log.path
        print(f"[GUI] ESA Auto Find log: {self._auto_tune_log_path}")

    def _record_auto_tune_log(self, method_name, *args):
        logger = self._auto_tune_run_log
        if logger is None:
            return
        try:
            getattr(logger, method_name)(*args)
        except (OSError, ValueError) as exc:
            print(f"[GUI] ESA Auto Find log write failed: {exc}")
            self._auto_tune_run_log = None
            try:
                logger.close()
            except (OSError, ValueError):
                pass

    def _close_auto_tune_run_log(self):
        logger = self._auto_tune_run_log
        if logger is None:
            return
        self._auto_tune_run_log = None
        try:
            logger.close()
        except (OSError, ValueError) as exc:
            print(f"[GUI] ESA Auto Find log close failed: {exc}")
        print(f"[GUI] ESA Auto Find log saved: {logger.path}")

    def stop_esa_auto_tune(self):
        if not self._auto_tune_is_running():
            return
        self._record_auto_tune_log("record_stop_requested")
        self.auto_tune_thread.requestInterruption()
        self._auto_tune_text = "Stopping"
        self._auto_tune_tone = "warning"
        self._sync_energy_control_state()
        self._refresh_status()

    def run_esa_auto_tune(self):
        if self.control_backend != "real":
            print("ESA auto tune is only enabled for the real backend.")
            return
        if not self._auto_tune_configured_for_backend():
            self._warn(
                "Direct bend scan is disabled for this backend because it would bypass "
                "the coordinated energy control."
            )
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
        try:
            bend_scan = self._current_auto_tune_scan()
        except ValueError as exc:
            self._warn(str(exc))
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
            self._start_auto_tune_run_log(bend_scan)

            self.auto_tune_thread = ESAAutoTuneThread(
                flag_pv_obj=self.flag_pv_obj,
                flag_pixel=self.flag_pixel,
                bend_pv=self.auto_tune_pv,
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
            self._sync_energy_control_state()
            QTimer.singleShot(0, self._sync_energy_control_state)
        except Exception as exc:
            self._record_auto_tune_log(
                "record_result",
                {"ok": False, "status": "FAILED", "error": str(exc)},
            )
            self._close_auto_tune_run_log()
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
        self._record_auto_tune_log("record_progress", payload)
        stage = str(payload.get("stage", "")).strip().lower()
        current = payload.get("current")
        has_beam = bool(payload.get("has_beam"))

        if stage == "fine_range":
            range_min = float(payload["range_min"])
            range_max = float(payload["range_max"])
            points = int(payload["points"])
            spacing = float(payload["spacing"])
            print(
                "[GUI] ESA Fine scan range: "
                f"{range_min:.3f}–{range_max:.3f} {self.auto_tune_unit}, "
                f"{points} points, spacing={spacing:.3f} {self.auto_tune_unit}."
            )
            self._auto_tune_text = (
                f"Fine {range_min:.2f}–{range_max:.2f} {self.auto_tune_unit}"
            )
            self._auto_tune_tone = "neutral"
            self._refresh_status()
            return

        if stage == "coarse":
            prefix = "Coarse"
        elif stage == "fine":
            prefix = "Fine"
        elif stage == "final":
            prefix = "Final"
        elif stage == "verify":
            prefix = "Verify"
        elif stage == "center_seed":
            prefix = "Fit seed"
        elif stage == "center_step":
            prefix = "Fit step"
        elif stage == "center_lock":
            prefix = "Fit lock"
        elif stage == "restore":
            prefix = "Restore"
        else:
            prefix = "Scan"

        if stage in {"center_seed", "center_step", "center_lock", "verify"} and (
            "total_frames" in payload
        ):
            valid_frames = int(payload.get("valid_frames", 0))
            total_frames = int(payload["total_frames"])
            if has_beam and payload.get("center_mm") is not None:
                fit_method = str(payload.get("fit_method", "--"))
                print(
                    f"[GUI] ESA {prefix}: "
                    f"E={float(current):.3f} {self.auto_tune_unit}, "
                    f"center={float(payload['center_mm']):+.3f} mm, "
                    f"dx={float(payload['center_offset_mm']):+.3f} mm, "
                    f"frames={valid_frames}/{total_frames}, fit={fit_method}."
                )
            else:
                diagnostic = str(payload.get("diagnostic", "profile fit failed"))
                print(
                    f"[GUI] ESA {prefix}: "
                    f"E={float(current):.3f} {self.auto_tune_unit}, "
                    f"frames={valid_frames}/{total_frames}, failed: {diagnostic}."
                )

        current_text = (
            f"{float(current):.2f} {self.auto_tune_unit}" if current is not None else "--"
        )
        center_offset_pixel = payload.get("center_offset_pixel")
        if has_beam and center_offset_pixel is not None:
            center_offset_mm = float(center_offset_pixel) * self.flag_pixel_width_mm
            suffix = f" dx={center_offset_mm:+.2f} mm"
        else:
            suffix = " beam" if has_beam else " ..."
        self._auto_tune_text = f"{prefix} {current_text}{suffix}"
        self._auto_tune_tone = "success" if has_beam else "warning"
        self._refresh_status()
        if self._pv_available:
            self.ESA_running()

    def _handle_auto_tune_result(self, payload):
        self._record_auto_tune_log("record_result", payload)
        if payload.get("ok"):
            best_current = payload.get("best_current")
            if best_current is not None:
                self._set_target_energy_control(best_current)
                self._auto_tune_text = f"{best_current:.2f} {self.auto_tune_unit}"
                self._auto_tune_tone = "success"
                print(
                    f"[GUI] ESA auto-tuned to {best_current:.3f} {self.auto_tune_unit}"
                )
                center_offset_pixel = payload.get("best_center_offset_pixel")
                if center_offset_pixel is not None:
                    center_offset_mm = (
                        float(center_offset_pixel) * self.flag_pixel_width_mm
                    )
                    print(
                        "[GUI] Final beam-center offset from x_reference_mm: "
                        f"{center_offset_mm:+.3f} mm"
                    )
                hybrid_fit = payload.get("hybrid_fit")
                if hybrid_fit:
                    print(
                        "[GUI] Brightness-gated x fit: "
                        f"r={float(hybrid_fit['correlation']):+.3f}, "
                        f"slope={float(hybrid_fit['slope_pixel_per_unit']):+.3f} px/"
                        f"{self.auto_tune_unit}, points={int(hybrid_fit['points_used'])}."
                    )
                center_lock_result = payload.get("center_lock_result")
                if center_lock_result:
                    print(
                        "[GUI] Peak brightness + fitted center: "
                        f"seed={float(center_lock_result['seed_energy']):.3f}, "
                        f"dx={float(center_lock_result['final_offset_mm']):+.3f} mm, "
                        f"step={float(center_lock_result['center_step']):.3f} "
                        f"{self.auto_tune_unit}, "
                        f"fit={center_lock_result['fit_method']}."
                    )
            else:
                self._auto_tune_text = "Done"
                self._auto_tune_tone = "success"
        else:
            error_text = payload.get("error")
            status_text = payload.get("status", "FAILED")
            if status_text == "CANCELLED":
                initial_value = payload.get("initial_value")
                if initial_value is not None:
                    self._set_target_energy_control(initial_value)
                self._auto_tune_text = "Stopped"
                self._auto_tune_tone = "subtle"
                print("[GUI] ESA auto tune stopped; initial energy restored.")
            elif error_text:
                self._auto_tune_text = "Failed"
                self._auto_tune_tone = "warning"
                print(f"ESA auto tune failed: {error_text}")
            else:
                self._auto_tune_text = "Failed"
                self._auto_tune_tone = "warning"
                message = payload.get("message")
                if message:
                    print(f"[GUI] ESA auto tune failed ({status_text}): {message}")
                else:
                    print(f"[GUI] ESA auto tune failed ({status_text}).")
        self._refresh_status()

    def _on_auto_tune_finished(self):
        self._close_auto_tune_run_log()
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
    
    
    
