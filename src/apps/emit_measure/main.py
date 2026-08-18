
import sys
import epics
import time
import json
import numpy as np
import math
from pathlib import Path
from datetime import datetime
from collections.abc import Mapping
from dataclasses import replace

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
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from half_linac.src.shared.app_theme import resolve_initial_theme
from half_linac.src.shared.beam_diagnostics import (
    BEAM_IMAGE_COLORMAPS,
    DEFAULT_BEAM_IMAGE_COLORMAP,
    BackgroundStoreError,
    analyze_beam_image,
    load_background,
    resolve_beam_background_paths,
    save_background,
)
from half_linac.src.shared.machine_profile import (
    METADATA_FILENAME,
    MachineProfileError,
    RuntimeContextWidget,
    build_model_backend,
    build_model_snapshot,
    describe_app_model_support,
    get_emit_preset,
    get_workflow,
    list_elements,
    load_app_context,
    load_profile,
    model_snapshot_lattice_overrides,
    require_workflow_write_allowed,
    resolve_app_runtime_paths,
    resolve_channel,
    resolve_element_image_geometry,
    resolve_write_target,
)
from half_linac.src.shared.window_activation import install_qt_window_raise_handler
from half_linac.src.apps.emit_measure.adaptive_scan import (
    AdaptiveObservation,
    AdaptiveScanConfig,
    MAX_QUALITY_SUPPLEMENT_POINTS,
    MIN_FINAL_POINTS_PER_PLANE,
    build_adaptive_plan,
    build_final_fit_windows,
    final_window_point_count,
    quality_supplement_values,
    quality_recovery_values,
    seed_values,
    validate_adaptive_scan,
)
from half_linac.src.apps.emit_measure.profile_runtime import effective_k1_scan_limit

nest_dict    = lambda: defaultdict(nest_dict)

ELECTRON_MASS_EV = 0.51099895000e6
SCAN_RESULTS_FILENAME = "scanResults.txt"
TWISS_RESULTS_FILENAME = "twissResults.jsonl"
APP_DIR = Path(__file__).resolve().parent
SCAN_DATA_SCHEMA_VERSION = "emit_scan_v2"
SCAN_POINT_COLUMNS = ("Use", "K1", "sigx (mm)", "sigy (mm)")
HALF_UNAVAILABLE_TWISS_QUADS = frozenset(f"QL{index:02d}" for index in range(13, 28))
TWISS_TRANSPORT_TOOLTIP = (
    "Twiss transport assumes geometric emittance is conserved along the selected "
    "model path. Use it only for paths without acceleration or other processes "
    "that change geometric emittance."
)

HEADER_ACTION_HEIGHT = 32
LEAST_SQUARES_REQUIRED_RANK = 3
LEAST_SQUARES_MAX_CONDITION = 1.0e12
ADAPTIVE_SCAN_STRATEGIES = frozenset(("adaptive", "adaptive_quality"))
QUALITY_MIN_SIGMA_PIXELS = 1.5
QUALITY_MIN_CONTAINMENT_SIGMA = 3.0
QUALITY_MAX_EDGE_RATIO = 0.05
QUALITY_MAX_FIT_RESIDUAL = 0.15


def _image_extent_from_geometry(geometry):
    pixel_width = geometry.pixel_width_mm
    width = geometry.shape[0] * pixel_width
    height = geometry.shape[1] * pixel_width
    return (-0.5 * width, 0.5 * width, -0.5 * height, 0.5 * height)


def _projection_measurement_quality(projection):
    payload = {
        "status": "fit_failed",
        "usable": False,
        "sigma_pixels": None,
        "containment_sigma": None,
        "edge_ratio": None,
        "fit_residual": projection.residual_rms,
    }
    if not projection.valid or projection.center is None or projection.sigma_abs is None:
        return payload

    axis = np.asarray(projection.axis, dtype=float)
    values = np.asarray(projection.projection, dtype=float)
    if axis.size < 2 or values.size != axis.size:
        return payload
    pixel_width = float(np.median(np.abs(np.diff(axis))))
    sigma = float(projection.sigma_abs)
    center = float(projection.center)
    sigma_pixels = sigma / pixel_width if pixel_width > 0 else 0.0
    margin = min(center - float(axis[0]), float(axis[-1]) - center)
    containment = margin / sigma if sigma > 0 else 0.0

    baseline = float(projection.offset or 0.0) * float(np.max(values))
    signal = np.clip(values - baseline, 0.0, None)
    peak = float(np.max(signal)) if signal.size else 0.0
    edge_bins = max(2, min(5, signal.size // 20))
    edge_level = max(float(np.mean(signal[:edge_bins])), float(np.mean(signal[-edge_bins:])))
    edge_ratio = edge_level / peak if peak > 0 else 1.0
    residual = projection.residual_rms

    if containment < QUALITY_MIN_CONTAINMENT_SIGMA or edge_ratio > QUALITY_MAX_EDGE_RATIO:
        status = "clipped"
    elif sigma_pixels < QUALITY_MIN_SIGMA_PIXELS:
        status = "underresolved"
    elif residual is not None and residual > QUALITY_MAX_FIT_RESIDUAL:
        status = "poor_fit"
    else:
        status = "usable"
    payload.update(
        {
            "status": status,
            "usable": status == "usable",
            "sigma_pixels": sigma_pixels,
            "containment_sigma": containment,
            "edge_ratio": edge_ratio,
        }
    )
    return payload


def _read_flag_image_fit(image_pv, pixel_shape, extent, *, background=None):
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

    raw_image = np.reshape(flat_image, (pixel_shape[1], pixel_shape[0]))
    return analyze_beam_image(raw_image, extent=extent, background=background)


def _read_optional_scalar_pv(pv_name):
    if not pv_name:
        return None
    try:
        return _finite_float_or_none(epics.caget(pv_name, timeout=0.05))
    except Exception:
        return None


def _read_optional_size_pvs(sigx_pv, sigy_pv):
    values = (_read_optional_scalar_pv(sigx_pv), _read_optional_scalar_pv(sigy_pv))
    return tuple(value if value is not None and value > 0 else None for value in values)


def _load_beam_monitor_config(machine_id):
    profile = load_profile(machine_id)
    try:
        return get_workflow(profile, "beam_monitor")
    except MachineProfileError as exc:
        raise MachineProfileError(
            "emit_measure local image fitting requires beam_monitor configuration "
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
        ("rank", "rank"),
        ("condition_number", "condition_number"),
        ("residual_rms", "residual_rms"),
    ):
        value = _finite_float_or_none(_read_result_field(result, source_key))
        if value is not None:
            summary[target_key] = int(value) if source_key == "rank" else value
    solver = _read_result_field(result, "solver")
    if solver:
        summary["solver"] = str(solver)
    fit_selection = _read_result_field(result, "fit_selection")
    if isinstance(fit_selection, Mapping):
        summary["fit_selection"] = dict(fit_selection)
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


def _least_squares_diagnostic_text(result, *, include_message=False):
    details = []
    fit_selection = _read_result_field(result, "fit_selection")
    if isinstance(fit_selection, Mapping):
        used = fit_selection.get("points_used")
        total = fit_selection.get("points_total")
        status = fit_selection.get("status")
        if used is not None and total is not None:
            details.append(f"fit points {used}/{total} ({status})")
    validation_status = _read_result_field(result, "validation_status")
    if validation_status:
        details.append(f"validation {validation_status}")
    coverage_message = str(_read_result_field(result, "coverage_message", "") or "").strip()
    if coverage_message:
        details.append(coverage_message)
    solver = _read_result_field(result, "solver")
    if solver:
        details.append(str(solver))
    rank = _read_result_field(result, "rank")
    if rank is not None:
        details.append(f"rank {int(rank)}/{LEAST_SQUARES_REQUIRED_RANK}")
    condition = _finite_float_or_none(_read_result_field(result, "condition_number"))
    if condition is not None:
        details.append(f"condition {condition:.3e}")
    elif rank is not None and int(rank) < LEAST_SQUARES_REQUIRED_RANK:
        details.append("condition infinite")
    residual_rms = _finite_float_or_none(_read_result_field(result, "residual_rms"))
    if residual_rms is not None:
        details.append(f"residual RMS {residual_rms:.3e} mm²")
    if include_message:
        message = str(_read_result_field(result, "message", "") or "").strip()
        if message:
            details.append(message)
    return "; ".join(details)


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

QPushButton[role="primary"] {{
    background-color: {metric_active_fg};
    border-color: {metric_active_fg};
    color: {panel_bg};
}}

QPushButton[role="danger"] {{
    border-color: {metric_warning_fg};
    color: {metric_warning_fg};
}}

QPushButton[twissMetric="true"]:checked {{
    background-color: {metric_active_fg};
    border-color: {metric_active_fg};
    color: {panel_bg};
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
    background-color: transparent;
    border: none;
    padding: 0px;
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

QProgressBar {{
    background-color: {input_bg};
    border: 1px solid {input_border};
    border-radius: 7px;
    color: {window_fg};
    min-height: 20px;
    max-height: 20px;
    text-align: center;
}}

QProgressBar::chunk {{
    background-color: {metric_active_fg};
    border-radius: 6px;
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
        install_qt_window_raise_handler(self)
        self.app_context = load_app_context("emit_measure")
        self.machine_profile = self.app_context.profile
        self.emit_workflow = self.app_context.emit_measure_workflow
        if self.emit_workflow is None:
            raise ValueError("Emit measure workflow is not available in the current app context.")
        self.beam_monitor_config = _load_beam_monitor_config(self.machine_profile.machine.id)

        self.current_theme = resolve_initial_theme()
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
        self.adaptive_search_min = None
        self.adaptive_search_max = None
        self.adaptive_initial_points = None
        self.adaptive_waist_size_squared_ratio = None
        self.custom_k1_mode = "absolute"
        self.custom_k1_unit = "1/m^2"
        self.adaptive_search_button = None
        self.scan_strategy_combo = QComboBox(self)
        self.scan_strategy_combo.addItem("Grid", "grid")
        self.scan_strategy_combo.addItem("Adaptive", "adaptive")
        self.scan_strategy_combo.addItem("Adaptive Quality", "adaptive_quality")
        self.scan_strategy_label = QLabel("Mode", self)
        self.scan_strategy_status_label = QLabel("Grid scan", self)
        self._last_scan_strategy = "grid"
        self._grid_steps_text = self.lineEdit_9.text()
        self._adaptive_max_points_text = None
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
        self.latest_scan_completion = None
        self._scan_result_ready = False
        self.latest_twiss_summary = None
        self.latest_twiss_profile = None
        self.latest_twiss_design_profile = None
        self.twiss_initial_source = {"kind": "manual"}
        self.latest_beam_image = None
        self.latest_beam_fit_result = None
        self.latest_beam_fit_flag = None
        self.latest_beam_fit_k1 = None
        self.latest_beam_size_pv = (None, None)
        self.latest_beam_background_status = "Off"
        self._applying_emit_preset = False
        self.background_dialog = None
        self.background_sample_button = None
        self.background_preview = None
        self.background_image = None
        self.background_metadata = {}
        self.background_image_path = None
        self.background_flag_id = None
        self.scan_progress = None
        self._scan_progress_completed = 0
        self._scan_progress_limit = 1
        self._scan_progress_bounded = True
        self._scan_progress_stage = "Idle"
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
        self.scan_strategy_combo.currentIndexChanged.connect(
            self._handle_scan_strategy_changed
        )

        # other function
        self.comboBox.currentIndexChanged.connect(self.updateComboBox4)
        self.comboBox_4.currentIndexChanged.connect(self._handle_emit_flag_changed)
        self.comboBox_2.currentIndexChanged.connect(self._update_twiss_path_status)
        self.comboBox_3.currentIndexChanged.connect(self._update_twiss_path_status)
        self.twiss_direction_combo.currentIndexChanged.connect(self._update_twiss_path_status)
        self.twiss_plane_combo.currentIndexChanged.connect(self._update_twiss_path_status)
        self.lineEdit.textEdited.connect(self._mark_twiss_initial_manual)
        self.lineEdit_3.textEdited.connect(self._mark_twiss_initial_manual)
        self.lineEdit.textChanged.connect(self._sync_initial_twiss_gamma)
        self.lineEdit_3.textChanged.connect(self._sync_initial_twiss_gamma)
        self.tabWidget.currentChanged.connect(self._refresh_status)
        for edit in (
            self.lineEdit_2,
            self.lineEdit_24,
            self.lineEdit_7,
            self.lineEdit_8,
            self.lineEdit_9,
            self.lineEdit_10,
            self.sample_interval_edit,
        ):
            edit.textEdited.connect(self._mark_emit_preset_modified)
        self.lineEdit_9.textEdited.connect(self._handle_scan_points_text_edited)
        # self.pushButton_6.clicked.connect(self.simply_VM)
        # self.pushButton_7.clicked.connect(self.full_VM)

        self._configure_machine_profile()
        self._refresh_model_controls()
        self._apply_theme()
        self._draw_placeholder_plots()
        self._draw_twiss_profile()
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
        self._build_twiss_plot_panel()
        twiss_tab_index = self.tabWidget.addTab(self.twiss_tab, "Twiss")
        self.tabWidget.setTabToolTip(twiss_tab_index, TWISS_TRANSPORT_TOOLTIP)

    def _build_twiss_plot_panel(self):
        self.twiss_plot_card = QFrame(self.twiss_tab)
        self.twiss_plot_card.setObjectName("plotCard")
        self.twiss_plot_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        layout = QVBoxLayout(self.twiss_plot_card)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        title = QLabel("Twiss Profile", self.twiss_plot_card)
        title.setObjectName("panelTitle")
        header.addWidget(title)
        header.addStretch(1)

        self.twiss_plot_cursor_label = QLabel("--", self.twiss_plot_card)
        self.twiss_plot_cursor_label.setProperty("role", "field")
        header.addWidget(self.twiss_plot_cursor_label)

        self.twiss_design_checkbox = QCheckBox("Design", self.twiss_plot_card)
        self.twiss_design_checkbox.setChecked(True)
        self.twiss_design_checkbox.setToolTip(
            "Overlay the Elegant design-lattice Twiss profile."
        )
        self.twiss_design_checkbox.toggled.connect(self._draw_twiss_profile)
        header.addWidget(self.twiss_design_checkbox)

        self.twiss_metric_group = QButtonGroup(self.twiss_plot_card)
        self.twiss_metric_group.setExclusive(True)
        self.twiss_metric_buttons = {}
        self.twiss_plot_metric = "beta"
        for metric, label in (("beta", "β"), ("alpha", "α"), ("gamma", "γ")):
            button = QPushButton(label, self.twiss_plot_card)
            button.setCheckable(True)
            button.setFixedWidth(40)
            button.setProperty("compact", True)
            button.setProperty("twissMetric", True)
            button.setToolTip(f"Plot {metric} along the selected model path.")
            button.clicked.connect(
                lambda _checked=False, selected=metric: self._select_twiss_plot_metric(selected)
            )
            self.twiss_metric_group.addButton(button)
            self.twiss_metric_buttons[metric] = button
            header.addWidget(button)
        self.twiss_metric_buttons["beta"].setChecked(True)
        layout.addLayout(header)

        self.twiss_plot_widget = MplWidget(self.twiss_plot_card)
        self.twiss_plot_widget.fig.clear()
        twiss_grid = self.twiss_plot_widget.fig.add_gridspec(
            2,
            1,
            height_ratios=(10, 2),
            hspace=0.04,
        )
        self.twiss_plot_widget.axes = self.twiss_plot_widget.fig.add_subplot(twiss_grid[0])
        self.twiss_lattice_axes = self.twiss_plot_widget.fig.add_subplot(
            twiss_grid[1],
            sharex=self.twiss_plot_widget.axes,
        )
        self.twiss_plot_widget.setMinimumHeight(360)
        self.twiss_plot_widget.canvas.mpl_connect(
            "motion_notify_event",
            self._handle_twiss_plot_hover,
        )
        layout.addWidget(self.twiss_plot_widget, 1)

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

        header_layout.addWidget(
            RuntimeContextWidget(
                machine_id=self.machine_profile.machine.id,
                machine_display_name=self.machine_profile.machine.display_name,
                control_backend=self.machine_type,
                parent=panel,
            )
        )

        self.theme_toggle_button = QToolButton(panel)
        self.theme_toggle_button.setObjectName("themeToggleButton")
        self.theme_toggle_button.setFixedSize(HEADER_ACTION_HEIGHT, HEADER_ACTION_HEIGHT)
        self.theme_toggle_button.clicked.connect(self._toggle_theme)
        header_layout.addWidget(self.theme_toggle_button)

        outer_layout.addLayout(header_layout)

        self.status_panel = EmitStatusStrip(panel)
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

        self.beam_image_title_label = QLabel("Current PRF Image", card)
        self.beam_image_title_label.setObjectName("panelTitle")
        self.beam_image_title_label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        header.addWidget(self.beam_image_title_label)
        header.addStretch(1)

        colormap_label = QLabel("Colormap", card)
        colormap_label.setProperty("role", "field")
        self.beam_image_colormap_combo = QComboBox(card)
        self.beam_image_colormap_combo.addItems(BEAM_IMAGE_COLORMAPS)
        self.beam_image_colormap_combo.setCurrentText(DEFAULT_BEAM_IMAGE_COLORMAP)
        self.beam_image_colormap_combo.setMaximumWidth(105)
        self.beam_image_colormap_combo.currentTextChanged.connect(self._redraw_latest_beam_image)

        self.beam_image_auto_refresh_checkbox = QCheckBox("Auto refresh", card)
        self.beam_image_auto_refresh_checkbox.setChecked(True)
        self.beam_image_auto_refresh_checkbox.stateChanged.connect(self._update_beam_image_auto_refresh)
        self.beam_image_projection_checkbox = QCheckBox("Projection", card)
        self.beam_image_projection_checkbox.setChecked(True)
        self.beam_image_projection_checkbox.stateChanged.connect(self._redraw_latest_beam_image)
        self.beam_image_fit_curve_checkbox = QCheckBox("Fit curve", card)
        self.beam_image_fit_curve_checkbox.setChecked(True)
        self.beam_image_fit_curve_checkbox.stateChanged.connect(self._redraw_latest_beam_image)
        self.beam_image_background_checkbox = QCheckBox("Apply", card)
        self.beam_image_background_checkbox.setChecked(False)
        self.beam_image_background_checkbox.setToolTip(
            "Subtract the matching saved background before fitting."
        )
        self.beam_image_background_checkbox.toggled.connect(
            self._set_background_application
        )

        self.preview_fit_button = QPushButton("Refresh", card)
        self.preview_fit_button.setProperty("compact", True)
        self.preview_fit_button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.preview_fit_button.clicked.connect(lambda: self.refresh_current_beam_image_fit())

        header.addWidget(self.beam_image_auto_refresh_checkbox)
        header.addWidget(self.preview_fit_button)
        layout.addLayout(header)

        display_row = QHBoxLayout()
        display_row.setContentsMargins(0, 0, 0, 0)
        display_row.setSpacing(8)
        self.beam_background_status_label = QLabel("Off", card)
        self.beam_background_status_label.setProperty("role", "field")
        self.beam_background_manage_button = QPushButton("Manage…", card)
        self.beam_background_manage_button.setProperty("compact", True)
        self.beam_background_manage_button.clicked.connect(self._show_background_dialog)
        separator = QFrame(card)
        separator.setFrameShape(QFrame.VLine)
        separator.setFrameShadow(QFrame.Sunken)
        separator.setMaximumHeight(22)
        background_label = QLabel("BG:", card)
        background_label.setProperty("role", "field")
        for widget in (
            colormap_label,
            self.beam_image_colormap_combo,
            self.beam_image_projection_checkbox,
            self.beam_image_fit_curve_checkbox,
            separator,
            background_label,
            self.beam_background_status_label,
        ):
            display_row.addWidget(widget)
        display_row.addStretch(1)
        display_row.addWidget(self.beam_background_manage_button)
        display_row.addWidget(self.beam_image_background_checkbox)
        layout.addLayout(display_row)

        self.beam_image_widget = MplWidget(card)
        layout.addWidget(self.beam_image_widget, 1)

        status_grid = QGridLayout()
        status_grid.setHorizontalSpacing(8)
        status_grid.setVerticalSpacing(4)
        self.beam_fit_flag_label = QLabel("Local fit", card)
        self.beam_fit_sigx_label = QLabel("--", card)
        self.beam_fit_sigy_label = QLabel("--", card)
        self.beam_fit_status_label = QLabel("No image", card)
        published_size_tooltip = (
            "Optional cross-check values read from the configured sigx/sigy channels. "
            "They are not used for scan points or emittance calculations."
        )
        self.beam_size_pv_source_label = QLabel("Published size", card)
        self.beam_size_pv_sigx_label = QLabel("--", card)
        self.beam_size_pv_sigy_label = QLabel("--", card)
        self.beam_size_pv_status_label = QLabel("Unavailable", card)
        for label in (
            self.beam_fit_flag_label,
            self.beam_fit_sigx_label,
            self.beam_fit_sigy_label,
            self.beam_fit_status_label,
            self.beam_size_pv_source_label,
            self.beam_size_pv_sigx_label,
            self.beam_size_pv_sigy_label,
            self.beam_size_pv_status_label,
        ):
            label.setWordWrap(True)
        for label in (
            self.beam_size_pv_source_label,
            self.beam_size_pv_sigx_label,
            self.beam_size_pv_sigy_label,
            self.beam_size_pv_status_label,
        ):
            label.setToolTip(published_size_tooltip)
        for col, text in enumerate(("Source", "σx (mm)", "σy (mm)", "Status")):
            label = QLabel(text, card)
            label.setProperty("role", "field")
            status_grid.addWidget(label, 0, col)
        status_grid.addWidget(self.beam_fit_flag_label, 1, 0)
        status_grid.addWidget(self.beam_fit_sigx_label, 1, 1)
        status_grid.addWidget(self.beam_fit_sigy_label, 1, 2)
        status_grid.addWidget(self.beam_fit_status_label, 1, 3)
        status_grid.addWidget(self.beam_size_pv_source_label, 2, 0)
        status_grid.addWidget(self.beam_size_pv_sigx_label, 2, 1)
        status_grid.addWidget(self.beam_size_pv_sigy_label, 2, 2)
        status_grid.addWidget(self.beam_size_pv_status_label, 2, 3)
        status_grid.setColumnStretch(0, 2)
        status_grid.setColumnStretch(1, 1)
        status_grid.setColumnStretch(2, 1)
        status_grid.setColumnStretch(3, 2)
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
        self._clear_layout_positions(self.scan_left_column_layout)
        self.scan_left_column_layout.addWidget(self.widget_4)
        self.scan_left_column_layout.addWidget(self.scan_points_card)
        self.gridLayout_2.addWidget(
            self.scan_left_column,
            0,
            0,
            2,
            1,
            Qt.AlignTop,
        )
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

        self.twiss_tab_layout.addWidget(self.widget_13, 0, 0, Qt.AlignTop)
        self.twiss_tab_layout.addWidget(self.twiss_plot_card, 0, 1)
        self.twiss_tab_layout.setColumnStretch(0, 2)
        self.twiss_tab_layout.setColumnStretch(1, 5)
        self.twiss_tab_layout.setRowStretch(0, 1)

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
        self.scan_points_card = QFrame(self.X_Plane)
        self.scan_points_card.setObjectName("plotCard")
        self.scan_left_column = QWidget(self.X_Plane)
        self.scan_left_column.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Maximum,
        )
        self.scan_left_column_layout = QVBoxLayout(self.scan_left_column)
        self.scan_left_column_layout.setContentsMargins(0, 0, 0, 0)
        self.scan_left_column_layout.setSpacing(10)
        self.widget_5.setObjectName("resultCard")
        self.widget_13.setObjectName("controlCard")
        self.widget_10.setObjectName("resultCard")
        for widget in (
            self.widget_4,
            self.scan_points_card,
            self.widget_5,
            self.widget_10,
            self.widget_13,
        ):
            widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.widget_13.setMaximumWidth(580)

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
        self.pushButton.setText("Start")
        self.pushButton_2.setText("Recalculate")
        self.pushButton_3.setText("Clear Results")
        self.pushButton_4.setText("Calculate Twiss")
        self.pushButton_5.setText("Stop")
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
        self.lineEdit_6.setReadOnly(True)
        self.lineEdit_6.setToolTip("Derived from beta and alpha using gamma = (1 + alpha²) / beta.")
        self.label_49.setToolTip(self.lineEdit_6.toolTip())

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
        self.pushButton_5.setToolTip("Stop the running scan and restore the quadrupole setting.")
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
        self.scan_strategy_combo.setToolTip(
            "Grid uses the existing fixed range. Adaptive uses From/To as a small "
            "initial probe, selects additional K1 values inside the editable search bounds, "
            "then fits X/Y from their own adaptive windows."
        )
        self.scan_strategy_label.setToolTip(self.scan_strategy_combo.toolTip())
        search_tooltip = (
            "Edit adaptive scan settings for this scan. The preset supplies the defaults."
        )
        self.adaptive_search_button.setToolTip(search_tooltip)
        self.comboBox_2.setToolTip("Start element for Twiss transport.")
        if self.machine_profile.machine.id == "half":
            self.comboBox_2.setToolTip(
                self.comboBox_2.toolTip()
                + " QL13–QL27 are temporarily unavailable until transport across accelerating sections is validated."
            )
        self.label_4.setToolTip(self.comboBox_2.toolTip())
        self.comboBox_3.setToolTip("End element for Twiss transport.")
        if self.machine_profile.machine.id == "half":
            self.comboBox_3.setToolTip(
                self.comboBox_3.toolTip()
                + " QL13–QL27 are temporarily unavailable until transport across accelerating sections is validated."
            )
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

        preset_label = QLabel("Preset", self.widget_4)
        preset_label.setProperty("role", "field")
        self.preset_combo = QComboBox(self.widget_4)
        self.preset_combo.setToolTip("Load recommended settings; all scan fields remain editable.")
        self.preset_combo.currentIndexChanged.connect(self._handle_preset_selected)
        self.preset_modified_label = QLabel("", self.widget_4)
        self.preset_modified_label.setProperty("role", "field")
        form.addWidget(preset_label, 0, 0)
        form.addWidget(self.preset_combo, 0, 1, 1, 2)
        form.addWidget(self.preset_modified_label, 0, 3, Qt.AlignRight)

        self.scan_strategy_label.setProperty("role", "field")
        self.scan_strategy_status_label.setProperty("role", "field")
        self.adaptive_search_button = QPushButton("Settings...", self.widget_4)
        self.adaptive_search_button.setProperty("compact", True)
        self.adaptive_search_button.setSizePolicy(
            QSizePolicy.Maximum,
            QSizePolicy.Fixed,
        )
        self.adaptive_search_button.clicked.connect(self._show_adaptive_search_dialog)
        form.addWidget(self.scan_strategy_label, 1, 0)
        form.addWidget(self.scan_strategy_combo, 1, 1)
        form.addWidget(self.adaptive_search_button, 1, 2)
        form.addWidget(self.scan_strategy_status_label, 1, 3, Qt.AlignRight)

        form.addWidget(self.label_45, 2, 0)
        form.addWidget(self.comboBox_4, 2, 1)
        form.addWidget(self.label_10, 2, 2)
        form.addWidget(self.comboBox, 2, 3)
        form.addWidget(self.label_22, 3, 0)
        form.addWidget(self.lineEdit_2, 3, 1)
        form.addWidget(self.label_32, 3, 2)
        form.addWidget(self.lineEdit_24, 3, 3)
        form.addWidget(self.label_11, 4, 0)
        form.addWidget(self.lineEdit_7, 4, 1)
        form.addWidget(self.label_12, 4, 2)
        form.addWidget(self.lineEdit_8, 4, 3)
        form.addWidget(self.label_13, 5, 0)
        form.addWidget(self.lineEdit_9, 5, 1)
        form.addWidget(self.label_14, 5, 2)
        form.addWidget(self.lineEdit_10, 5, 3)
        self.sample_interval_label = QLabel("Sample interval (s)", self.widget_4)
        self.sample_interval_label.setProperty("role", "field")
        self.sample_interval_edit = QLineEdit(self.widget_4)
        self.sample_interval_edit.setText("0.5")
        form.addWidget(self.sample_interval_label, 6, 0)
        form.addWidget(self.sample_interval_edit, 6, 1)
        self.k1_range_mode_label = QLabel("", self.widget_4)
        self.k1_range_mode_label.setToolTip(
            "Adaptive low/high inherit the same K1 unit and range mode. "
            "Relative ranges use the K1 value read at scan start."
        )
        k1_range_mode_title = QLabel("Range mode", self.widget_4)
        k1_range_mode_title.setProperty("role", "field")
        form.addWidget(k1_range_mode_title, 7, 0)
        form.addWidget(self.k1_range_mode_label, 7, 1, 1, 3)
        form.setColumnStretch(1, 1)
        form.setColumnStretch(3, 1)
        layout.addLayout(form)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)

        self.pushButton.setProperty("role", "primary")
        self.pushButton_5.setProperty("role", "danger")
        for button in (self.pushButton, self.pushButton_5):
            button.setMinimumWidth(88)
            button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            actions.addWidget(button)
        actions.addStretch(1)
        self.pushButton_3.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        actions.addWidget(self.pushButton_3)

        layout.addLayout(actions)

        self.scan_progress = QProgressBar(self.widget_4)
        self.scan_progress.setRange(0, 1)
        self.scan_progress.setValue(0)
        self.scan_progress.setFormat("Idle")
        self.scan_progress.setToolTip(
            "Grid shows exact K1 progress. Adaptive shows consumed point budget."
        )
        layout.addWidget(self.scan_progress)

        points_layout = QVBoxLayout(self.scan_points_card)
        points_layout.setContentsMargins(8, 8, 8, 8)
        points_layout.setSpacing(6)

        points_header = QHBoxLayout()
        points_header.setContentsMargins(0, 0, 0, 0)
        points_header.setSpacing(6)

        points_title = QLabel("Scan Points", self.scan_points_card)
        points_title.setObjectName("panelTitle")
        points_header.addWidget(points_title)
        points_header.addStretch(1)

        self.scan_points_summary_label = QLabel(
            "0 active / 0 total",
            self.scan_points_card,
        )
        self.scan_points_summary_label.setProperty("role", "field")
        points_header.addWidget(self.scan_points_summary_label)
        points_layout.addLayout(points_header)

        self.scan_points_table = QTableWidget(
            0,
            len(SCAN_POINT_COLUMNS),
            self.scan_points_card,
        )
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
        points_layout.addWidget(self.scan_points_table)

        point_actions = QHBoxLayout()
        point_actions.setContentsMargins(0, 0, 0, 0)
        point_actions.setSpacing(6)
        self.pushButton_2.setParent(self.scan_points_card)
        self.load_points_button = QPushButton("Load Points", self.scan_points_card)
        self.exclude_points_button = QPushButton(
            "Exclude Selected",
            self.scan_points_card,
        )
        self.restore_points_button = QPushButton("Use All Points", self.scan_points_card)
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
        points_layout.addLayout(point_actions)

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
        if self.adaptive_search_button is not None:
            self.scan_strategy_combo.ensurePolished()
            self.adaptive_search_button.setFixedHeight(
                self.scan_strategy_combo.sizeHint().height()
            )
        if hasattr(self, "status_panel"):
            self.status_panel.apply_theme(palette)
            self.status_panel.setFixedHeight(self.status_panel.sizeHint().height())
        self._update_theme_toggle_button()
        self._style_all_plots()
        self._refresh_emit_background_preview()

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
        self._draw_twiss_profile()
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

    def _style_twiss_plot_axes(self, metric):
        labels = {
            "beta": "β (m)",
            "alpha": "α",
            "gamma": "γ (1/m)",
        }
        self._style_axes(self.twiss_plot_widget, ylabel=labels[metric])
        self.twiss_plot_widget.axes.tick_params(labelbottom=False)

        palette = self._palette()
        axes = self.twiss_lattice_axes
        axes.set_facecolor(palette["plot_bg"])
        axes.set_ylim(0, 1.15)
        axes.set_yticks([])
        axes.set_xlabel("Distance from From (m)", color=palette["plot_text"])
        axes.tick_params(colors=palette["plot_text"], which="both", labelsize=9)
        for spine in axes.spines.values():
            spine.set_edgecolor(palette["plot_spine"])
        axes.grid(False)

    def _style_all_plots(self):
        self._style_axes(self.widget, "$K_1 (m^{-2})$", "sigx (mm)")
        self._style_axes(self.widget_2, "$-K= K_1 L_q (m^{-1})$", "$sigx^2 (mm^2)$")
        self._style_axes(self.widget_8, "$K_1 (m^{-2})$", "sigy (mm)")
        self._style_axes(self.widget_9, "$K= K_1 L_q (m^{-1})$", "$sigy^2 (mm^2)$")
        if hasattr(self, "beam_image_widget"):
            self._style_axes(self.beam_image_widget, "x (mm)", "y (mm)")
        if hasattr(self, "twiss_plot_widget"):
            self._style_twiss_plot_axes(self.twiss_plot_metric)
        for plot in (self.widget, self.widget_2, self.widget_8, self.widget_9):
            plot.canvas.draw_idle()
        if hasattr(self, "beam_image_widget"):
            self.beam_image_widget.canvas.draw_idle()
        if hasattr(self, "twiss_plot_widget"):
            self.twiss_plot_widget.canvas.draw_idle()

    def _select_twiss_plot_metric(self, metric):
        if metric not in {"beta", "alpha", "gamma"}:
            return
        self.twiss_plot_metric = metric
        self.twiss_metric_buttons[metric].setChecked(True)
        self._draw_twiss_profile()

    def _draw_twiss_profile(self):
        if not hasattr(self, "twiss_plot_widget"):
            return
        rows = self.latest_twiss_profile or ()
        design_rows = self.latest_twiss_design_profile or ()
        metric = self.twiss_plot_metric
        if not rows:
            self.twiss_plot_cursor_label.setText("--")
            axes = self.twiss_plot_widget.axes
            axes.clear()
            self.twiss_lattice_axes.clear()
            self._style_twiss_plot_axes(metric)
            axes.text(
                0.5,
                0.5,
                "No Twiss profile yet",
                transform=axes.transAxes,
                ha="center",
                va="center",
                color=self._palette()["muted_fg"],
                fontsize=10,
            )
            self._twiss_cursor_line = None
            self._twiss_lattice_cursor_line = None
            self.twiss_plot_widget.canvas.draw_idle()
            return

        palette = self._palette()
        distances = np.asarray([row["distance_m"] for row in rows], dtype=float)
        values = np.asarray([row[metric] for row in rows], dtype=float)
        axes = self.twiss_plot_widget.axes
        axes.clear()
        self.twiss_lattice_axes.clear()
        self._style_twiss_plot_axes(metric)
        if self.twiss_design_checkbox.isChecked() and design_rows:
            design_distances = np.asarray(
                [row["distance_m"] for row in design_rows], dtype=float
            )
            design_values = np.asarray([row[metric] for row in design_rows], dtype=float)
            axes.plot(
                design_distances,
                design_values,
                color=palette["muted_fg"],
                linewidth=1.4,
                linestyle="--",
                label="Design",
            )
        axes.plot(
            distances,
            values,
            color=palette["plot_fit"],
            linewidth=1.8,
            label="Current K1",
        )
        axes.scatter(
            (distances[0], distances[-1]),
            (values[0], values[-1]),
            color=palette["plot_point"],
            s=24,
            zorder=3,
        )
        axes.margins(x=0.02, y=0.08)
        legend = axes.legend(loc="upper right", frameon=False, fontsize=8)
        for text in legend.get_texts():
            text.set_color(palette["plot_text"])
        self._twiss_cursor_line = axes.axvline(
            distances[0],
            color=palette["plot_point"],
            linewidth=1,
            alpha=0.65,
            visible=False,
        )
        self._draw_twiss_lattice_strip(rows)
        self._set_twiss_cursor_point(rows[0], metric)
        self.twiss_plot_widget.canvas.draw_idle()

    def _draw_twiss_lattice_strip(self, rows):
        axes = self.twiss_lattice_axes
        palette = self._palette()
        total_distance = max(float(row["distance_m"]) for row in rows)
        backward = (self.latest_twiss_summary or {}).get("direction") == "backward"
        span = max(total_distance, 1.0)
        minimum_width = span * 0.003
        colors = {
            "bend_h": "#db8b3d",
            "bend_v": "#3aa6b9",
            "quad": "#9b72cf",
            "bpm": "#4dbb83",
            "rf": "#b27ad8",
        }
        visible_families = set()

        axes.axhline(0.5, color=palette["muted_fg"], linewidth=0.8, alpha=0.75)

        for row in rows:
            name = str(row.get("element_name", ""))
            element_type = str(row.get("element_type", "")).upper()
            is_bend = "BEND" in element_type or element_type in {"SBEN", "RBEN"}
            is_bpm = name.upper().startswith("BPM") or element_type == "MONI"
            is_rf = "RF" in element_type or element_type in {"TWLA", "KICKMAP"}
            if is_bend:
                tilt = float(row.get("element_tilt_rad", 0.0))
                family = "bend_v" if abs(math.sin(tilt)) > 0.7 else "bend_h"
            elif "QUAD" in element_type:
                family = "quad"
            elif is_bpm:
                family = "bpm"
            elif is_rf:
                family = "rf"
            else:
                continue
            visible_families.add(family)

            distance = float(row["distance_m"])
            length = max(0.0, float(row.get("element_length_m", 0.0)))
            left = distance if backward else distance - length
            left = min(max(0.0, left), total_distance)
            width = max(min(total_distance, left + length) - left, minimum_width)
            center = left + width / 2.0

            if family == "quad":
                k1 = float(row.get("element_k1_m2", float("nan")))
                bottom = 0.5 if not math.isfinite(k1) or k1 >= 0 else 0.12
                axes.add_patch(
                    mpl.patches.Rectangle(
                        (left, bottom), width, 0.38,
                        facecolor=colors[family], edgecolor=colors[family], alpha=0.95,
                    )
                )
            elif family == "bpm":
                axes.vlines(center, 0.27, 0.76, color=colors[family], linewidth=1.5)
                axes.scatter(
                    [center], [0.82], marker="v", s=22,
                    color=colors[family], edgecolors="none", zorder=3,
                )
            else:
                height = 0.34 if family.startswith("bend") else 0.24
                axes.add_patch(
                    mpl.patches.Rectangle(
                        (left, 0.5 - height / 2.0), width, height,
                        facecolor=colors[family], edgecolor=colors[family], alpha=0.95,
                    )
                )

        legend_labels = (
            ("bend_h", "Bend-H"),
            ("bend_v", "Bend-V"),
            ("quad", "Quad +/-"),
            ("bpm", "BPM"),
            ("rf", "RF"),
        )
        handles = [
            mpl.patches.Patch(color=colors[family], label=label)
            for family, label in legend_labels
            if family in visible_families
        ]
        if handles:
            legend = axes.legend(
                handles=handles,
                loc="upper left",
                bbox_to_anchor=(0.0, 1.03),
                ncol=len(handles),
                frameon=False,
                fontsize=7,
                handlelength=1.0,
                handletextpad=0.35,
                columnspacing=0.8,
                borderaxespad=0.0,
            )
            for text in legend.get_texts():
                text.set_color(palette["muted_fg"])

        self._twiss_lattice_cursor_line = axes.axvline(
            0.0,
            color=palette["window_fg"],
            linewidth=1,
            visible=False,
        )
        axes.set_xlim(0.0, total_distance if total_distance > 0 else 1.0)

    @staticmethod
    def _format_twiss_profile_point(row, metric, design_row=None):
        units = {"beta": "m", "alpha": "", "gamma": "1/m"}
        suffix = f" {units[metric]}" if units[metric] else ""
        if design_row is None:
            text = (
                f"{row['element_name']} · {row['distance_m']:.3f} m · "
                f"{metric} {row[metric]:.5g}{suffix}"
            )
        else:
            current_value = float(row[metric])
            design_value = float(design_row[metric])
            text = (
                f"{row['element_name']} · {row['distance_m']:.3f} m · "
                f"Current {current_value:.5g} · Design {design_value:.5g} · "
                f"Δ {current_value - design_value:+.4g}{suffix}"
            )
        k1 = float(row.get("element_k1_m2", float("nan")))
        if math.isfinite(k1):
            text += f" · K1 {k1:.5g} 1/m²"
        return text

    def _set_twiss_cursor_point(self, row, metric):
        design_row = None
        design_rows = self.latest_twiss_design_profile or ()
        if self.twiss_design_checkbox.isChecked() and design_rows:
            design_row = min(
                design_rows,
                key=lambda candidate: abs(
                    float(candidate["distance_m"]) - float(row["distance_m"])
                ),
            )
        self.twiss_plot_cursor_label.setText(
            self._format_twiss_profile_point(row, metric, design_row)
        )

    def _handle_twiss_plot_hover(self, event):
        rows = self.latest_twiss_profile or ()
        if (
            not rows
            or event.inaxes not in {self.twiss_plot_widget.axes, self.twiss_lattice_axes}
            or event.xdata is None
        ):
            return
        distances = np.asarray([row["distance_m"] for row in rows], dtype=float)
        index = int(np.argmin(np.abs(distances - float(event.xdata))))
        row = rows[index]
        self._set_twiss_cursor_point(row, self.twiss_plot_metric)
        if self._twiss_cursor_line is not None:
            self._twiss_cursor_line.set_xdata([row["distance_m"], row["distance_m"]])
            self._twiss_cursor_line.set_visible(True)
            if self._twiss_lattice_cursor_line is not None:
                self._twiss_lattice_cursor_line.set_xdata(
                    [row["distance_m"], row["distance_m"]]
                )
                self._twiss_lattice_cursor_line.set_visible(True)
            self.twiss_plot_widget.canvas.draw_idle()

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
        self.latest_beam_size_pv = (None, None)
        self.latest_beam_background_status = "Off"
        if hasattr(self, "beam_image_title_label"):
            flag_name = self.comboBox_4.currentText()
            suffix = f" · {flag_name}" if flag_name else ""
            self.beam_image_title_label.setText(f"Current PRF Image{suffix}")
        self._draw_placeholder(self.beam_image_widget, "x (mm)", "y (mm)", note)
        if hasattr(self, "beam_fit_flag_label"):
            self.beam_fit_flag_label.setText("Local fit")
            self.beam_fit_sigx_label.setText("--")
            self.beam_fit_sigy_label.setText("--")
            self.beam_fit_status_label.setText("No image")
            self.beam_size_pv_sigx_label.setText("--")
            self.beam_size_pv_sigy_label.setText("--")
            self.beam_size_pv_status_label.setText("Unavailable")
            self.beam_background_status_label.setText("Not checked")

    def _redraw_latest_beam_image(self, *args):
        del args
        if self.latest_beam_image is None or self.latest_beam_fit_result is None:
            return
        self._display_beam_image_fit(
            self.latest_beam_fit_flag or self.comboBox_4.currentText(),
            self.latest_beam_image,
            self.latest_beam_fit_result,
            k1=self.latest_beam_fit_k1,
            size_pv=self.latest_beam_size_pv,
            background_status=self.latest_beam_background_status,
        )

    def _display_beam_image_fit(
        self,
        flag_name,
        image,
        fit_result,
        *,
        k1=None,
        extent=None,
        size_pv=(None, None),
        background_status="Off",
    ):
        if not hasattr(self, "beam_image_widget"):
            return
        if extent is None:
            extent = self._current_flag_image_extent(flag_name)
        palette = self._palette()
        widget = self.beam_image_widget
        widget.axes.clear()
        self._style_axes(widget, "x (mm)", "y (mm)")
        widget.axes.imshow(
            image,
            cmap=self.beam_image_colormap_combo.currentText() if hasattr(self, "beam_image_colormap_combo") else "viridis",
            origin="lower",
            extent=extent,
            aspect="auto",
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

        if k1 is not None:
            widget.axes.set_title(
                f"K1 {float(k1):.6g}",
                color=palette["plot_text"],
                fontsize=10,
                loc="left",
            )
        widget.canvas.draw()

        self.latest_beam_image = image
        self.latest_beam_fit_result = fit_result
        self.latest_beam_fit_flag = flag_name
        self.latest_beam_fit_k1 = k1
        self.latest_beam_size_pv = tuple(size_pv)
        self.latest_beam_background_status = background_status
        self.beam_image_title_label.setText(f"Current PRF Image · {flag_name}")
        self.beam_fit_flag_label.setText("Local fit")
        self.beam_fit_sigx_label.setText(f"{fit_result.sigx_mm:.3f}" if fit_result.sigx_mm is not None else "--")
        self.beam_fit_sigy_label.setText(f"{fit_result.sigy_mm:.3f}" if fit_result.sigy_mm is not None else "--")
        if fit_result.valid:
            self.beam_fit_status_label.setText("valid")
        else:
            self.beam_fit_status_label.setText(fit_result.status)
        size_sigx, size_sigy = size_pv
        self.beam_size_pv_sigx_label.setText(
            f"{size_sigx:.3f}" if size_sigx is not None else "--"
        )
        self.beam_size_pv_sigy_label.setText(
            f"{size_sigy:.3f}" if size_sigy is not None else "--"
        )
        self.beam_size_pv_status_label.setText(
            "Cross-check" if size_sigx is not None or size_sigy is not None else "Unavailable"
        )
        self.beam_background_status_label.setText(background_status)
        self._refresh_status()

    def _refresh_status(self):
        if not hasattr(self, "status_panel"):
            return
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
        elif self.latest_scan_completion:
            points = self.latest_scan_completion.get("points", 0)
            action = (
                "Recalculated"
                if self.latest_scan_completion.get("mode") == "recalculate"
                else "Complete"
            )
            points_text = f" · {points} points" if points else ""
            self.status_panel.set_item(
                "scan",
                f"{action}{points_text}",
                "success",
                "Measurement and transfer-matrix reconstruction completed.",
            )
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
        self._update_scan_run_controls()

    def _refresh_emit_fit_status(self):
        summary = self.latest_emit_fit_summary
        if not summary:
            self.status_panel.set_item("emit", "No result", "subtle")
            return
        status = summary.get("quality_status", summary.get("status", "unresolved"))
        method = summary.get("method", "fit")
        if status in {"valid", "validated"}:
            tone = "success"
        elif status == "error":
            tone = "warning"
        else:
            tone = "warning"

        x_summary = summary.get("xplane", {})
        y_summary = summary.get("yplane", {})
        x_status = x_summary.get("validation_status", x_summary.get("status", "unknown"))
        y_status = y_summary.get("validation_status", y_summary.get("status", "unknown"))
        if status == "error":
            text = _compact_status_text(summary.get("message", "error"), limit=90)
        else:
            text = f"{method}: {status} (x {x_status}, y {y_status})"
        diagnostic_lines = []
        for plane_label, plane_key in (("X", "xplane"), ("Y", "yplane")):
            plane_summary = summary.get(plane_key, {})
            details = _least_squares_diagnostic_text(
                plane_summary,
                include_message=plane_summary.get("status") != "valid",
            )
            if details:
                diagnostic_lines.append(f"{plane_label}: {details}")
        self.status_panel.set_item(
            "emit",
            text,
            tone,
            "\n".join(diagnostic_lines) or None,
        )

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
        self._update_scan_run_controls()

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
            "size_pv_sigx": paras.flagSigxPV,
            "size_pv_sigy": paras.flagSigyPV,
            "background_status": paras.background_status,
            "energy_mev": paras.EnergyMeV,
            "k1_from": paras.k1_from,
            "k1_end": paras.k1_end,
            "k1_steps": paras.k1_steps,
            "k1_mode": paras.k1_mode,
            "k1_unit": paras.k1_unit,
            "samples": paras.samples,
            "settle_time": paras.settle_time,
            "sample_interval": paras.sample_interval,
            "scan_strategy": paras.scan_strategy,
            "image_geometry": {
                "shape": list(paras.flag_pixel_shape),
                "extent_mm": list(paras.flag_image_extent),
            },
        }
        if paras.scan_strategy == "adaptive_quality":
            metadata["quality_limits"] = {
                "min_sigma_pixels": QUALITY_MIN_SIGMA_PIXELS,
                "min_containment_sigma": QUALITY_MIN_CONTAINMENT_SIGMA,
                "max_edge_ratio": QUALITY_MAX_EDGE_RATIO,
                "max_fit_residual": QUALITY_MAX_FIT_RESIDUAL,
            }
        adaptive_config = getattr(paras, "adaptive_config", None)
        if adaptive_config is not None:
            metadata["adaptive"] = {
                "k1_min": adaptive_config.k1_min,
                "k1_max": adaptive_config.k1_max,
                "initial_points": adaptive_config.initial_points,
                "target_points_per_plane": adaptive_config.target_points_per_plane,
                "max_unique_points": adaptive_config.max_unique_points,
                "waist_size_squared_ratio": adaptive_config.waist_size_squared_ratio,
                "reuse_tolerance": adaptive_config.reuse_tolerance,
                "max_retries": adaptive_config.max_retries,
            }
            metadata["adaptive_preset_defaults"] = {
                "k1_min": getattr(paras, "adaptive_preset_k1_min", None),
                "k1_max": getattr(paras, "adaptive_preset_k1_max", None),
            }
        model_snapshot = getattr(paras, "model_snapshot_metadata", None)
        if isinstance(model_snapshot, Mapping):
            metadata["model_snapshot"] = dict(model_snapshot)
        background_path = getattr(paras, "background_image_path", None)
        if background_path is not None:
            metadata["background_image_path"] = str(background_path)
        return metadata

    def _metadata_path_for_results(self, results_path):
        results_path = Path(results_path)
        if results_path.name == SCAN_RESULTS_FILENAME:
            return results_path.parent / METADATA_FILENAME
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
            raise RuntimeError(f"{source_label} has no scan metadata.")
        if metadata.get("schema_version") != SCAN_DATA_SCHEMA_VERSION:
            raise RuntimeError(
                f"{source_label} has unsupported metadata schema: {metadata.get('schema_version')!r}."
            )

        mismatches = []
        for key in ("machine_id", "backend", "quad", "flag"):
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

    def _apply_scan_metadata_to_controls(self, metadata, source_label):
        if metadata is None:
            raise RuntimeError(f"{source_label} has no scan metadata.")
        if metadata.get("schema_version") != SCAN_DATA_SCHEMA_VERSION:
            raise RuntimeError(
                f"{source_label} has unsupported metadata schema: "
                f"{metadata.get('schema_version')!r}."
            )

        runtime_context = {
            "machine_id": self.machine_profile.machine.id,
            "backend": self.machine_type,
        }
        mismatches = [
            f"{key}: file={metadata.get(key)!r}, current={runtime_context[key]!r}"
            for key in ("machine_id", "backend")
            if metadata.get(key) != runtime_context[key]
        ]
        if mismatches:
            raise RuntimeError(
                f"{source_label} cannot be loaded in the current runtime: "
                + "; ".join(mismatches)
                + "."
            )

        required_fields = (
            "quad",
            "flag",
            "energy_mev",
            "k1_from",
            "k1_end",
            "k1_mode",
            "k1_unit",
            "samples",
            "settle_time",
            "sample_interval",
            "scan_strategy",
            "model_snapshot",
        )
        missing_fields = [field for field in required_fields if metadata.get(field) is None]
        if missing_fields:
            raise RuntimeError(
                f"{source_label} is missing required scan metadata: "
                + ", ".join(missing_fields)
                + "."
            )
        if not isinstance(metadata["model_snapshot"], Mapping):
            raise RuntimeError(f"{source_label} has invalid model_snapshot metadata.")

        quad_name = str(metadata.get("quad") or "")
        flag_name = str(metadata.get("flag") or "")
        if self.comboBox.findText(quad_name) < 0:
            raise RuntimeError(f"{source_label} references unavailable quad {quad_name!r}.")
        if self.comboBox_4.findText(flag_name) < 0:
            raise RuntimeError(f"{source_label} references unavailable flag {flag_name!r}.")

        strategy = str(metadata["scan_strategy"])
        if strategy not in {"grid", *ADAPTIVE_SCAN_STRATEGIES}:
            raise RuntimeError(
                f"{source_label} uses unsupported scan strategy {strategy!r}."
            )
        gui_strategy = strategy
        adaptive = metadata.get("adaptive")
        if gui_strategy in ADAPTIVE_SCAN_STRATEGIES and not isinstance(adaptive, Mapping):
            raise RuntimeError(f"{source_label} has no Adaptive scan metadata.")
        if gui_strategy == "adaptive_quality":
            for field in ("image_geometry", "quality_limits", "point_quality"):
                if not isinstance(metadata.get(field), (Mapping, list)):
                    raise RuntimeError(
                        f"{source_label} has invalid Adaptive Quality field {field!r}."
                    )
        archive_adaptive = None
        try:
            if gui_strategy in ADAPTIVE_SCAN_STRATEGIES:
                archive_adaptive = AdaptiveScanConfig(
                    k1_min=float(adaptive["k1_min"]),
                    k1_max=float(adaptive["k1_max"]),
                    initial_points=int(adaptive["initial_points"]),
                    target_points_per_plane=int(adaptive["target_points_per_plane"]),
                    max_unique_points=int(adaptive["max_unique_points"]),
                    waist_size_squared_ratio=float(adaptive["waist_size_squared_ratio"]),
                    reuse_tolerance=float(adaptive["reuse_tolerance"]),
                    max_retries=int(adaptive["max_retries"]),
                )
                points = archive_adaptive.max_unique_points
            else:
                points = int(metadata["k1_steps"])
                if points <= 0:
                    raise ValueError("k1_steps must be positive")
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"{source_label} has invalid scan configuration: {exc}.") from exc

        if archive_adaptive is not None:
            self.adaptive_search_min = archive_adaptive.k1_min
            self.adaptive_search_max = archive_adaptive.k1_max
            self.adaptive_initial_points = archive_adaptive.initial_points
            self.adaptive_waist_size_squared_ratio = (
                archive_adaptive.waist_size_squared_ratio
            )

        self._applying_emit_preset = True
        try:
            self._set_combo_current_text(self.comboBox, quad_name)
            self._set_combo_current_text(self.comboBox_4, flag_name)
            custom_index = self.preset_combo.findData(None)
            self.preset_combo.setCurrentIndex(custom_index)

            field_values = (
                (self.lineEdit_2, metadata["energy_mev"]),
                (self.lineEdit_7, metadata["k1_from"]),
                (self.lineEdit_8, metadata["k1_end"]),
                (self.lineEdit_10, metadata["samples"]),
                (self.lineEdit_24, metadata["settle_time"]),
                (self.sample_interval_edit, metadata["sample_interval"]),
            )
            for field, value in field_values:
                field.setText(f"{value:g}" if isinstance(value, float) else str(value))

            points_text = str(points)
            if gui_strategy == "grid":
                self._grid_steps_text = points_text
            else:
                self._adaptive_max_points_text = points_text

            blocked = self.scan_strategy_combo.blockSignals(True)
            self.scan_strategy_combo.setCurrentIndex(
                self.scan_strategy_combo.findData(gui_strategy)
            )
            self.scan_strategy_combo.blockSignals(blocked)
            self.lineEdit_9.setText(points_text)
            self._last_scan_strategy = gui_strategy
            if gui_strategy == "grid":
                self._set_adaptive_search_fields_visible(False)
                self.label_13.setText("Steps")
                self.scan_strategy_status_label.setText("Grid scan")
            else:
                self._set_adaptive_search_fields_visible(True)
                self.label_13.setText("Max points")
                self._update_adaptive_search_status()
        finally:
            self._applying_emit_preset = False

        mode = str(metadata["k1_mode"])
        self.custom_k1_mode = mode
        self.custom_k1_unit = str(metadata["k1_unit"])
        mode_text = "Relative to initial setpoint" if mode == "relative" else "Absolute setpoints"
        unit = self.custom_k1_unit
        self.k1_range_mode_label.setText(f"K1: {mode_text} ({unit}) · Loaded archive")
        self.preset_modified_label.setText("Loaded")
        self._draw_beam_image_placeholder()
        self._sync_emit_background_for_flag()
        if self._beam_image_auto_refresh_ready:
            self._schedule_beam_image_refresh()

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
        archive_dir = self._scan_archive_dir()
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
            results_path = Path(path)
            metadata = self._read_scan_metadata(results_path)
            self._apply_scan_metadata_to_controls(metadata, str(results_path))
            self._load_scan_results_into_table(results_path)
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
            "profile": [dict(row) for row in result.get("profile", ())],
            "design_profile": [dict(row) for row in result.get("design_profile", ())],
        }
        design_endpoint = result.get("design_endpoint")
        if isinstance(design_endpoint, Mapping):
            record["design_result_twiss"] = {
                key: _finite_float_or_none(design_endpoint.get(key))
                for key in ("beta", "alpha", "gamma")
            }
        if result.get("design_error"):
            record["design_error"] = str(result["design_error"])
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

    @staticmethod
    def _scan_progress_stage_label(stage):
        return {
            "grid": "Grid",
            "seed": "Seed",
            "seed_recovery": "Seed recovery",
            "adapt_range": "Planning",
            "refine": "Refine",
            "validation_supplement": "Validate",
            "quality_supplement": "Quality supplement",
            "validate": "Validate",
            "finalizing": "Finalizing",
        }.get(str(stage or ""), "Scanning")

    def _update_scan_run_controls(self):
        if not hasattr(self, "pushButton_5"):
            return
        running = self.scan is not None and (
            self.scan.isRunning() or self.scan_mode in {"scan", "recalculate", "stopping"}
        )
        stopping = running and self.scan_mode == "stopping"
        self.pushButton.setEnabled(self._model_backend_available and not running)
        self.pushButton_5.setEnabled(running and not stopping)
        self.pushButton_5.setText("Stopping..." if stopping else "Stop")
        self.pushButton_3.setEnabled(not running)

    def _begin_scan_progress(self, paras):
        strategy = str(paras.scan_strategy)
        self._scan_progress_completed = 0
        self._scan_progress_bounded = strategy == "grid"
        if strategy == "grid":
            self._scan_progress_limit = max(1, int(paras.k1_steps))
            self._scan_progress_stage = "Grid"
            suffix = str(self._scan_progress_limit)
        else:
            extra = MAX_QUALITY_SUPPLEMENT_POINTS if strategy == "adaptive_quality" else 0
            self._scan_progress_limit = max(
                1,
                int(paras.adaptive_config.max_unique_points) + extra,
            )
            self._scan_progress_stage = "Seed"
            suffix = f"≤{self._scan_progress_limit}"
        self.scan_progress.setRange(0, self._scan_progress_limit)
        self.scan_progress.setValue(0)
        self.scan_progress.setFormat(
            f"{self._scan_progress_stage} · 0 / {suffix} points"
        )

    def _update_scan_progress(self, payload):
        if self.scan_progress is None or not isinstance(payload, Mapping):
            return
        completed = max(0, int(payload.get("completed_points", self._scan_progress_completed)))
        self._scan_progress_completed = completed
        if payload.get("stage"):
            self._scan_progress_stage = self._scan_progress_stage_label(payload["stage"])
        self.scan_progress.setValue(min(completed, self._scan_progress_limit))
        total_text = (
            str(self._scan_progress_limit)
            if self._scan_progress_bounded
            else f"≤{self._scan_progress_limit}"
        )
        sample_index = payload.get("sample_index")
        sample_count = payload.get("sample_count")
        if sample_index is not None and sample_count is not None:
            current_point = min(completed + 1, self._scan_progress_limit)
            text = (
                f"{self._scan_progress_stage} · Point {current_point} / {total_text} "
                f"· Sample {int(sample_index)}/{int(sample_count)}"
            )
        else:
            text = (
                f"{self._scan_progress_stage} · {completed} / {total_text} points"
            )
        self.scan_progress.setFormat(text)

    def _finish_scan_progress(self, status):
        if self.scan_progress is None:
            return
        completed = self._scan_progress_completed
        if status == "complete":
            self.scan_progress.setValue(self._scan_progress_limit)
            self.scan_progress.setFormat(f"Complete · {completed} points")
        elif status == "stopped":
            self.scan_progress.setFormat(f"Stopped · {completed} points")
        else:
            self.scan_progress.setFormat(f"Failed · {completed} points")

    def _twiss_is_running(self):
        return self.twissCal is not None and self.twissCal.isRunning()

    def _on_scan_finished(self):
        completed_mode = self.scan_mode
        completed = self._scan_result_ready and completed_mode != "stopping"
        should_show_analysis = completed and self.tabWidget.currentWidget() is self.X_Plane
        if completed:
            active, total = self._scan_points_counts()
            if completed_mode == "scan":
                try:
                    self.loaded_scan_metadata = self._read_scan_metadata(
                        self._latest_scan_results_path()
                    )
                    self.loaded_scan_results_path = self._latest_scan_results_path()
                except RuntimeError as exc:
                    print(f"Warning: could not refresh completed scan metadata: {exc}")
            self.latest_scan_completion = {
                "mode": completed_mode,
                "active_points": active,
                "points": total,
            }
            action = "Recalculated" if completed_mode == "recalculate" else "Scan complete"
            self.scan_strategy_status_label.setText(f"{action} · {total} points")
            self.scan_strategy_status_label.setToolTip(
                "Measurement and transfer-matrix reconstruction completed."
            )
            if completed_mode == "recalculate":
                self.scan_progress.setRange(0, 1)
                self.scan_progress.setValue(1)
                self.scan_progress.setFormat("Recalculated")
            else:
                self._finish_scan_progress("complete")
        elif completed_mode == "stopping":
            self._finish_scan_progress("stopped")
        self.scan = None
        self.scan_mode = None
        self.pending_scan_metadata = None
        self.scan_strategy_combo.setEnabled(True)
        self._refresh_status()
        if should_show_analysis:
            QTimer.singleShot(0, self._show_completed_scan_analysis)
        if self._beam_image_auto_refresh_ready:
            self._schedule_beam_image_refresh()

    def _show_completed_scan_analysis(self):
        if self.latest_scan_completion and self.tabWidget.currentWidget() is self.X_Plane:
            self.tabWidget.setCurrentWidget(self.tab_2)

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
        summary = self.latest_twiss_summary or {}
        current_selection = (
            self.comboBox_2.currentText(),
            self.comboBox_3.currentText(),
            "backward" if self._selected_twiss_inverse_map() else "forward",
            self._selected_twiss_plane(),
        )
        result_selection = (
            summary.get("from_element"),
            summary.get("to_element"),
            summary.get("direction"),
            summary.get("plane"),
        )
        if summary.get("status") in {"valid", "error"} and current_selection != result_selection:
            self.latest_twiss_summary = None
            self.latest_twiss_profile = None
            self.latest_twiss_design_profile = None
            for field in (self.lineEdit_17, self.lineEdit_21, self.lineEdit_22):
                field.clear()
            self.twiss_map_edit.setPlainText("No Twiss calculation yet")
            self._draw_twiss_profile()
        message = self._twiss_path_validation_message()
        self.twiss_status_edit.setText(message or self._format_twiss_path_status())

    def _mark_twiss_initial_manual(self):
        self.twiss_initial_source = {
            "kind": "manual",
            "source_quad": self.comboBox_2.currentText() or None,
        }

    def _sync_initial_twiss_gamma(self):
        try:
            beta = float(self.lineEdit.text())
            alpha = float(self.lineEdit_3.text())
        except ValueError:
            self.lineEdit_6.clear()
            return
        if beta <= 0 or not math.isfinite(beta) or not math.isfinite(alpha):
            self.lineEdit_6.clear()
            return
        self.lineEdit_6.setText(f"{(1.0 + alpha**2) / beta:.8g}")

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
        if beta is None or alpha is None:
            self._warn_twiss(f"Latest {self._format_twiss_plane_label(plane)} plane fit has incomplete Twiss values.")
            return

        self.lineEdit.setText(f"{beta:.8g}")
        self.lineEdit_3.setText(f"{alpha:.8g}")
        self._sync_initial_twiss_gamma()
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

    def _parse_finite_float(self, text, field_name):
        try:
            value = float(text)
        except ValueError:
            raise ValueError(f"{field_name} must be numeric.")
        if not math.isfinite(value):
            raise ValueError(f"{field_name} must be finite.")
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

    def _active_emit_preset(self):
        preset_id = self.preset_combo.currentData()
        if preset_id is None:
            return None
        preset = self._find_emit_preset(preset_id)
        if preset is None:
            return None
        if preset.quad != self.comboBox.currentText() or preset.flag != self.comboBox_4.currentText():
            return None
        return preset

    def _adaptive_template_preset(self):
        active = self._active_emit_preset()
        if active is not None:
            return active
        default = self._find_emit_preset(self.emit_workflow.default_preset)
        if default is not None and default.scan.adaptive is not None:
            return default
        return next(
            (preset for preset in self.emit_workflow.presets if preset.scan.adaptive is not None),
            None,
        )

    def _emit_quad_choices(self):
        return [
            element.id
            for element in list_elements(
                self.app_context,
                kind="quad",
                logical_channel="K1",
                control_backend=self.machine_type,
            )
        ]

    def _emit_flag_choices(self):
        return [
            element.id
            for element in list_elements(
                self.app_context,
                kind="flag",
                logical_channel="image",
                control_backend=self.machine_type,
            )
        ]

    def _selected_scan_strategy(self):
        return str(self.scan_strategy_combo.currentData() or "grid")

    @staticmethod
    def _adaptive_config_from_preset(
        preset,
        *,
        initial_points=None,
        k1_min=None,
        k1_max=None,
        max_unique_points=None,
        waist_size_squared_ratio=None,
    ):
        raw = None if preset is None else preset.scan.adaptive
        if raw is None:
            return None
        values = raw.as_dict()
        required = ("k1_min", "k1_max", "initial_points")
        if any(values.get(name) is None for name in required):
            return None
        return AdaptiveScanConfig(
            k1_min=float(values["k1_min"] if k1_min is None else k1_min),
            k1_max=float(values["k1_max"] if k1_max is None else k1_max),
            initial_points=int(
                values["initial_points"] if initial_points is None else initial_points
            ),
            target_points_per_plane=int(values.get("target_points_per_plane", 7)),
            max_unique_points=int(
                values.get("max_unique_points", 16)
                if max_unique_points is None
                else max_unique_points
            ),
            waist_size_squared_ratio=float(
                values.get("waist_size_squared_ratio", 2.0)
                if waist_size_squared_ratio is None
                else waist_size_squared_ratio
            ),
            reuse_tolerance=float(values.get("reuse_tolerance", 0.01)),
            max_retries=int(values.get("max_retries", 2)),
        )

    def _set_adaptive_search_fields_visible(self, visible):
        self.adaptive_search_button.setVisible(visible)

    def _update_adaptive_search_status(self, *_args):
        if self._selected_scan_strategy() not in ADAPTIVE_SCAN_STRATEGIES:
            return
        if self.adaptive_search_min is None or self.adaptive_search_max is None:
            self.scan_strategy_status_label.setText("Set search range")
            return
        details = [f"{self.adaptive_search_min:g} … {self.adaptive_search_max:g}"]
        if self.adaptive_initial_points is not None:
            details.append(f"seed {self.adaptive_initial_points}")
        max_points = self._adaptive_max_points_text
        if self._selected_scan_strategy() in ADAPTIVE_SCAN_STRATEGIES:
            max_points = self.lineEdit_9.text().strip() or max_points
        if max_points:
            details.append(f"max {max_points}")
        self.scan_strategy_status_label.setText(" · ".join(details))

    def _handle_scan_points_text_edited(self, text):
        if self._selected_scan_strategy() in ADAPTIVE_SCAN_STRATEGIES:
            self._adaptive_max_points_text = text
            self._update_adaptive_search_status()

    def _set_adaptive_search_bounds(self, lower, upper, *, mark_modified=False):
        lower = self._parse_finite_float(str(lower), "Adaptive K1 min")
        upper = self._parse_finite_float(str(upper), "Adaptive K1 max")
        if lower >= upper:
            raise ValueError("Adaptive K1 min must be smaller than K1 max.")
        self.adaptive_search_min = lower
        self.adaptive_search_max = upper
        if mark_modified:
            self._mark_emit_preset_modified()
        self._update_adaptive_search_status()

    def _show_adaptive_search_dialog(self):
        if self._selected_scan_strategy() not in ADAPTIVE_SCAN_STRATEGIES:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Adaptive Settings")
        dialog.setModal(True)
        layout = QVBoxLayout(dialog)

        form = QGridLayout()
        lower_edit = QLineEdit(dialog)
        upper_edit = QLineEdit(dialog)
        initial_points_spin = QSpinBox(dialog)
        waist_ratio_spin = QDoubleSpinBox(dialog)
        lower_edit.setText(
            "" if self.adaptive_search_min is None else f"{self.adaptive_search_min:g}"
        )
        upper_edit.setText(
            "" if self.adaptive_search_max is None else f"{self.adaptive_search_max:g}"
        )
        preset = self._adaptive_template_preset()
        adaptive = self._adaptive_config_from_preset(preset)
        initial_points_spin.setRange(3, 100)
        initial_points_spin.setValue(
            self.adaptive_initial_points
            if self.adaptive_initial_points is not None
            else adaptive.initial_points
        )
        waist_ratio_spin.setRange(1.01, 10.0)
        waist_ratio_spin.setDecimals(2)
        waist_ratio_spin.setSingleStep(0.1)
        waist_ratio_spin.setValue(
            self.adaptive_waist_size_squared_ratio
            if self.adaptive_waist_size_squared_ratio is not None
            else adaptive.waist_size_squared_ratio
        )
        waist_ratio_spin.setToolTip(
            "Defines each plane's fitting window by the allowed sigma-squared "
            "growth relative to its estimated waist."
        )
        form.addWidget(QLabel("K1 min", dialog), 0, 0)
        form.addWidget(lower_edit, 0, 1)
        form.addWidget(QLabel("K1 max", dialog), 1, 0)
        form.addWidget(upper_edit, 1, 1)
        form.addWidget(QLabel("Initial points", dialog), 2, 0)
        form.addWidget(initial_points_spin, 2, 1)
        waist_ratio_label = QLabel("Waist coverage ratio", dialog)
        waist_ratio_label.setToolTip(waist_ratio_spin.toolTip())
        form.addWidget(waist_ratio_label, 3, 0)
        form.addWidget(waist_ratio_spin, 3, 1)
        layout.addLayout(form)

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel_button = QPushButton("Cancel", dialog)
        apply_button = QPushButton("Apply", dialog)
        actions.addWidget(cancel_button)
        actions.addWidget(apply_button)
        layout.addLayout(actions)

        def apply_settings():
            try:
                lower = self._parse_finite_float(lower_edit.text(), "Adaptive K1 min")
                upper = self._parse_finite_float(upper_edit.text(), "Adaptive K1 max")
                if lower >= upper:
                    raise ValueError("Adaptive K1 min must be smaller than K1 max.")
                seed_from = self._parse_finite_float(self.lineEdit_7.text(), "K1 From")
                seed_to = self._parse_finite_float(self.lineEdit_8.text(), "K1 To")
                if not lower <= seed_from < seed_to <= upper:
                    raise ValueError(
                        "Adaptive range must satisfy K1 min <= From < To <= K1 max."
                    )
                initial_points = initial_points_spin.value()
                max_points = self._parse_positive_int(
                    self.lineEdit_9.text(), "Max points"
                )
                if max_points < initial_points + 4:
                    raise ValueError(
                        "Max points must be at least Initial points + 4 "
                        "to leave room for refinement and validation."
                    )
                waist_ratio = waist_ratio_spin.value()
                self._adaptive_config_from_preset(
                    preset,
                    initial_points=initial_points,
                    k1_min=lower,
                    k1_max=upper,
                    max_unique_points=max_points,
                    waist_size_squared_ratio=waist_ratio,
                )
            except ValueError as exc:
                QMessageBox.warning(dialog, "Adaptive Settings", str(exc))
                return
            self.adaptive_search_min = lower
            self.adaptive_search_max = upper
            self.adaptive_initial_points = initial_points
            self.adaptive_waist_size_squared_ratio = waist_ratio
            self._adaptive_max_points_text = str(max_points)
            self._mark_emit_preset_modified()
            self._update_adaptive_search_status()
            dialog.accept()

        cancel_button.clicked.connect(dialog.reject)
        apply_button.clicked.connect(apply_settings)
        lower_edit.returnPressed.connect(apply_settings)
        upper_edit.returnPressed.connect(apply_settings)
        dialog.exec_()

    def _handle_scan_strategy_changed(self, _index):
        strategy = self._selected_scan_strategy()
        preset = self._adaptive_template_preset()
        adaptive = self._adaptive_config_from_preset(preset)
        if strategy in ADAPTIVE_SCAN_STRATEGIES and adaptive is None:
            self._set_adaptive_search_fields_visible(False)
            blocked = self.scan_strategy_combo.blockSignals(True)
            self.scan_strategy_combo.setCurrentIndex(
                self.scan_strategy_combo.findData("grid")
            )
            self.scan_strategy_combo.blockSignals(blocked)
            self.scan_strategy_status_label.setText("Adaptive unavailable")
            if not self._applying_emit_preset:
                self._warn("The selected preset has no adaptive scan configuration.")
            return

        if self._last_scan_strategy == "grid":
            self._grid_steps_text = self.lineEdit_9.text()
        else:
            self._adaptive_max_points_text = self.lineEdit_9.text()

        if strategy in ADAPTIVE_SCAN_STRATEGIES:
            self._set_adaptive_search_fields_visible(True)
            if self.adaptive_search_min is None or self.adaptive_search_max is None:
                self._set_adaptive_search_bounds(adaptive.k1_min, adaptive.k1_max)
            if self.adaptive_initial_points is None:
                self.adaptive_initial_points = adaptive.initial_points
            if self.adaptive_waist_size_squared_ratio is None:
                self.adaptive_waist_size_squared_ratio = adaptive.waist_size_squared_ratio
            max_points_text = self._adaptive_max_points_text or str(
                adaptive.max_unique_points
            )
            self.lineEdit_9.setText(max_points_text)
            self.label_13.setText("Max points")
            self._update_adaptive_search_status()
        else:
            self._set_adaptive_search_fields_visible(False)
            self.lineEdit_9.setText(self._grid_steps_text)
            self.label_13.setText("Steps")
            self.scan_strategy_status_label.setText("Grid scan")
        self._last_scan_strategy = strategy

    def _sync_scan_strategy_for_preset(self, preset):
        self._grid_steps_text = (
            str(preset.scan.k1_steps)
            if preset is not None and preset.scan.k1_steps is not None
            else self.lineEdit_9.text()
        )
        adaptive = self._adaptive_config_from_preset(preset)
        self.adaptive_initial_points = adaptive.initial_points if adaptive is not None else None
        self.adaptive_waist_size_squared_ratio = (
            adaptive.waist_size_squared_ratio if adaptive is not None else None
        )
        self._adaptive_max_points_text = (
            str(adaptive.max_unique_points) if adaptive is not None else None
        )
        if self._selected_scan_strategy() in ADAPTIVE_SCAN_STRATEGIES and adaptive is not None:
            self.lineEdit_9.setText(self._adaptive_max_points_text)
        elif self._selected_scan_strategy() == "grid":
            self.lineEdit_9.setText(self._grid_steps_text)
        self._handle_scan_strategy_changed(self.scan_strategy_combo.currentIndex())

    def _resolve_optional_channel(self, element_id, logical_channel):
        try:
            return resolve_channel(
                self.machine_profile,
                element_id,
                logical_channel,
                self.machine_type,
            )
        except MachineProfileError:
            return None

    def _current_flag_pixel_geometry(self, flag_name=None):
        flag_name = flag_name or self.comboBox_4.currentText()
        return resolve_element_image_geometry(
            self.app_context,
            flag_name,
            self.machine_type,
        )

    def _current_flag_image_extent(self, flag_name=None):
        return _image_extent_from_geometry(self._current_flag_pixel_geometry(flag_name))

    def _twiss_from_choices(self):
        if self.emit_workflow.twiss_quads:
            return [
                element_id
                for element_id in self.emit_workflow.twiss_quads
                if self._twiss_element_is_available(element_id)
            ]

        choices = []
        for preset in self.emit_workflow.presets:
            if preset.quad not in choices and self._twiss_element_is_available(preset.quad):
                choices.append(preset.quad)
        return choices

    def _twiss_element_is_available(self, element_id):
        return not (
            self.machine_profile.machine.id == "half"
            and element_id in HALF_UNAVAILABLE_TWISS_QUADS
        )

    def _twiss_to_choices(self):
        choices = [
            element.id
            for element in self.machine_profile.elements
            if element.kind == "quad" and self._twiss_element_is_available(element.id)
        ]
        return choices or self._twiss_from_choices()

    def _configure_machine_profile(self):
        self._set_combo_items(self.comboBox, self._emit_quad_choices())
        self._set_combo_items(self.comboBox_4, self._emit_flag_choices())
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItem("Custom", None)
        for preset in self.emit_workflow.presets:
            self.preset_combo.addItem(f"{preset.flag}  ·  {preset.quad}", preset.id)
        self.preset_combo.blockSignals(False)

        twiss_from_quads = self._twiss_from_choices()
        twiss_to_quads = self._twiss_to_choices()
        self._set_combo_items(self.comboBox_2, twiss_from_quads)
        self._set_combo_items(self.comboBox_3, twiss_to_quads)

        default_preset = self._find_emit_preset(self.emit_workflow.default_preset)
        self._apply_emit_preset(default_preset)
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
        self._update_twiss_path_status()

    def _apply_emit_preset(self, preset):
        if preset is None:
            return
        self._applying_emit_preset = True
        try:
            self.preset_combo.blockSignals(True)
            preset_index = self.preset_combo.findData(preset.id)
            if preset_index >= 0:
                self.preset_combo.setCurrentIndex(preset_index)
            self.preset_combo.blockSignals(False)

            self.comboBox.blockSignals(True)
            self._set_combo_current_text(self.comboBox, preset.quad)
            self.comboBox.blockSignals(False)
            self.comboBox_4.blockSignals(True)
            self._set_combo_current_text(self.comboBox_4, preset.flag)
            self.comboBox_4.blockSignals(False)
            self._sync_emit_preset_defaults()
            self._sync_scan_strategy_for_preset(preset)
            self.preset_modified_label.setText("")
        finally:
            self._applying_emit_preset = False
        self._draw_beam_image_placeholder()
        self._sync_emit_background_for_flag()
        if self._beam_image_auto_refresh_ready:
            self._schedule_beam_image_refresh()

    def _handle_preset_selected(self, index):
        if self._applying_emit_preset or index < 0:
            return
        preset_id = self.preset_combo.itemData(index)
        if preset_id is None:
            return
        self._apply_emit_preset(self._find_emit_preset(preset_id))

    def _mark_emit_preset_modified(self, *_args):
        if not self._applying_emit_preset:
            self.preset_modified_label.setText("Modified")

    def _mark_emit_pair_custom(self):
        self.preset_combo.blockSignals(True)
        custom_index = self.preset_combo.findData(None)
        self.preset_combo.setCurrentIndex(custom_index)
        self.preset_combo.blockSignals(False)
        self.preset_modified_label.setText("Custom")
        self.k1_range_mode_label.setText(
            "K1: Absolute setpoints (1/m^2) · Custom selection"
        )

    def _sync_emit_preset_defaults(self):
        preset = self._current_emit_preset()
        if preset is None:
            return
        if preset.energy_mev is not None:
            self.lineEdit_2.setText(str(preset.energy_mev))
        scan = preset.scan
        mode_text = (
            "Relative to initial setpoint"
            if scan.mode == "relative"
            else "Absolute setpoints"
        )
        unit_suffix = f" ({scan.unit})" if scan.unit else ""
        self.k1_range_mode_label.setText(
            f"K1: {mode_text}{unit_suffix} · Adaptive inherits this setting"
        )
        if scan.k1_from is not None:
            self.lineEdit_7.setText(str(scan.k1_from))
        if scan.k1_end is not None:
            self.lineEdit_8.setText(str(scan.k1_end))
        if scan.k1_steps is not None and self._selected_scan_strategy() == "grid":
            self.lineEdit_9.setText(str(scan.k1_steps))
        if scan.samples is not None:
            self.lineEdit_10.setText(str(scan.samples))
        if scan.settle_time is not None:
            self.lineEdit_24.setText(str(scan.settle_time))
        if scan.sample_interval is not None and self.sample_interval_edit is not None:
            self.sample_interval_edit.setText(str(scan.sample_interval))
        adaptive = self._adaptive_config_from_preset(preset)
        if adaptive is not None:
            self._set_adaptive_search_bounds(adaptive.k1_min, adaptive.k1_max)
        self.custom_k1_mode = scan.mode or "absolute"
        self.custom_k1_unit = scan.unit or "1/m^2"

    def _handle_emit_flag_changed(self, index):
        del index
        if self._applying_emit_preset:
            return
        self._mark_emit_pair_custom()
        self._draw_beam_image_placeholder()
        self._sync_emit_background_for_flag()
        if self._beam_image_auto_refresh_ready:
            self._schedule_beam_image_refresh()

    def updateComboBox4(self, index):
        del index
        if self._applying_emit_preset:
            return
        self._mark_emit_pair_custom()
        self._draw_beam_image_placeholder()
        self._sync_emit_background_for_flag()
        if self._beam_image_auto_refresh_ready:
            self._schedule_beam_image_refresh()


    def get_setting(self, *, show_warning=True):
        try:
            para = structData()
            # get scan parameters
            para.quad_name = self.comboBox.currentText()
            para.flag_name = self.comboBox_4.currentText()
            preset = self._active_emit_preset()
            para.quadPV = resolve_write_target(
                self.machine_profile,
                para.quad_name,
                quantity="K1",
                mode=self.machine_type,
            ).pv_name
            para.flagImagePV = resolve_channel(self.machine_profile, para.flag_name, "image", self.machine_type)
            para.flagSigxPV = self._resolve_optional_channel(para.flag_name, "sigx")
            para.flagSigyPV = self._resolve_optional_channel(para.flag_name, "sigy")
            para.flagExposurePV = self._resolve_optional_channel(
                para.flag_name,
                "exposure_time",
            )
            geometry = self._current_flag_pixel_geometry(para.flag_name)
            para.flag_pixel_shape = geometry.shape
            para.flag_image_extent = _image_extent_from_geometry(geometry)
            para.background_image = None
            para.background_status = "Off"
            para.background_image_path = None
            if self.beam_image_background_checkbox.isChecked():
                if not self._background_reference_is_usable():
                    blocked = self.beam_image_background_checkbox.blockSignals(True)
                    self.beam_image_background_checkbox.setChecked(False)
                    self.beam_image_background_checkbox.blockSignals(blocked)
                    self._update_emit_background_status()
                else:
                    para.background_image = self.background_image
                    para.background_status = "Applied"
                    para.background_image_path = self.background_image_path
            model_backend = build_model_backend(
                self.app_context,
                line_name=preset.model_line if preset is not None else None,
            )
            model_path = model_backend.get_line_elements(
                para.quad_name,
                para.flag_name,
            )
            if not model_path or str(model_path[0]["NAME"]) != para.quad_name:
                raise ValueError(
                    f"Select a flag downstream of {para.quad_name}; "
                    f"{para.flag_name} is upstream."
                )
            para.model_line = model_backend.line_name
            para.app_context = self.app_context

            para.k1_from  = float(self.lineEdit_7.text())
            para.k1_end   = float(self.lineEdit_8.text())
            para.k1_mode = (
                preset.scan.mode if preset is not None else self.custom_k1_mode
            ) or "absolute"
            para.k1_unit = (
                preset.scan.unit if preset is not None else self.custom_k1_unit
            ) or "1/m^2"
            para.scan_strategy = self._selected_scan_strategy()
            steps_name = (
                "Max points"
                if para.scan_strategy in ADAPTIVE_SCAN_STRATEGIES
                else "K1 steps"
            )
            para.k1_steps = self._parse_positive_int(self.lineEdit_9.text(), steps_name)
            para.adaptive_config = None
            if para.scan_strategy in ADAPTIVE_SCAN_STRATEGIES:
                search_min = self.adaptive_search_min
                search_max = self.adaptive_search_max
                if search_min is None or search_max is None:
                    raise ValueError("Set the adaptive K1 search range before scanning.")
                initial_points = self.adaptive_initial_points
                waist_ratio = self.adaptive_waist_size_squared_ratio
                if initial_points is None or waist_ratio is None:
                    raise ValueError("Open Adaptive Settings and apply the scan settings.")
                if para.k1_steps < initial_points + 4:
                    raise ValueError(
                        "Max points must be at least Initial points + 4 "
                        "to leave room for refinement and validation."
                    )
                adaptive_template = self._adaptive_template_preset()
                para.adaptive_config = self._adaptive_config_from_preset(
                    adaptive_template,
                    initial_points=initial_points,
                    k1_min=search_min,
                    k1_max=search_max,
                    max_unique_points=para.k1_steps,
                    waist_size_squared_ratio=waist_ratio,
                )
                if para.adaptive_config is None:
                    raise ValueError("No usable adaptive scan configuration is available.")
                preset_adaptive = self._adaptive_config_from_preset(preset)
                para.adaptive_preset_k1_min = (
                    preset_adaptive.k1_min if preset_adaptive is not None else None
                )
                para.adaptive_preset_k1_max = (
                    preset_adaptive.k1_max if preset_adaptive is not None else None
                )
                if not (
                    para.adaptive_config.k1_min
                    <= para.k1_from
                    < para.k1_end
                    <= para.adaptive_config.k1_max
                ):
                    raise ValueError(
                        "Adaptive range must satisfy K1 min <= From < To <= K1 max "
                        f"([{para.adaptive_config.k1_min:g}, "
                        f"{para.adaptive_config.k1_max:g}])."
                    )
                seed_values(para.k1_from, para.k1_end, para.adaptive_config)
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
            if show_warning:
                self._warn(str(exc))
            return None

    def refresh_current_beam_image_fit(self, paras=None, *, show_warning=True):
        if paras is None:
            paras = self.get_setting(show_warning=show_warning)
            if paras is None:
                return False
        try:
            image, fit_result = _read_flag_image_fit(
                paras.flagImagePV,
                paras.flag_pixel_shape,
                paras.flag_image_extent,
                background=paras.background_image,
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
            size_pv=_read_optional_size_pvs(paras.flagSigxPV, paras.flagSigyPV),
            background_status=paras.background_status,
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

    def _show_background_dialog(self):
        if self.background_dialog is None:
            dialog = QDialog(self)
            dialog.setWindowTitle("Background Reference")
            dialog.setModal(True)
            dialog.resize(720, 620)
            layout = QVBoxLayout(dialog)
            note = QLabel(
                "Remove the beam before sampling. The reference is shared with Beam Monitor "
                "and is valid only for the same PRF, image size and exposure.",
                dialog,
            )
            note.setWordWrap(True)
            layout.addWidget(note)

            self.background_preview = MplWidget(dialog)
            self.background_preview.setMinimumHeight(300)
            layout.addWidget(self.background_preview, 1)

            form = QGridLayout()
            self.background_samples_spin = QSpinBox(dialog)
            self.background_samples_spin.setRange(1, 100)
            self.background_samples_spin.setValue(
                int(self.beam_monitor_config.get("background_sample_count", 5))
            )
            self.background_interval_spin = QDoubleSpinBox(dialog)
            self.background_interval_spin.setRange(0.0, 60.0)
            self.background_interval_spin.setDecimals(2)
            self.background_interval_spin.setSuffix(" s")
            self.background_interval_spin.setValue(
                float(self.beam_monitor_config.get("background_sample_interval_s", 1.0))
            )
            form.addWidget(QLabel("Samples", dialog), 0, 0)
            form.addWidget(self.background_samples_spin, 0, 1)
            form.addWidget(QLabel("Interval", dialog), 0, 2)
            form.addWidget(self.background_interval_spin, 0, 3)
            form.setColumnStretch(1, 1)
            form.setColumnStretch(3, 1)
            layout.addLayout(form)

            self.background_dialog_status_label = QLabel("", dialog)
            self.background_dialog_status_label.setWordWrap(True)
            self.background_dialog_status_label.setProperty("role", "field")
            layout.addWidget(self.background_dialog_status_label)

            buttons = QHBoxLayout()
            self.background_sample_button = QPushButton("Sample Background", dialog)
            load_latest_button = QPushButton("Load Latest", dialog)
            load_file_button = QPushButton("Load File", dialog)
            save_as_button = QPushButton("Save As", dialog)
            close_button = QPushButton("Close", dialog)
            for button in (
                self.background_sample_button,
                load_latest_button,
                load_file_button,
                save_as_button,
                close_button,
            ):
                button.setProperty("compact", True)
            self.background_sample_button.clicked.connect(self._sample_background)
            load_latest_button.clicked.connect(
                lambda: self._load_latest_emit_background(silent=False)
            )
            load_file_button.clicked.connect(self._load_emit_background_file)
            save_as_button.clicked.connect(self._save_emit_background_as)
            close_button.clicked.connect(dialog.hide)
            buttons.addWidget(self.background_sample_button)
            buttons.addWidget(load_latest_button)
            buttons.addWidget(load_file_button)
            buttons.addWidget(save_as_button)
            buttons.addStretch(1)
            buttons.addWidget(close_button)
            layout.addLayout(buttons)
            self.background_dialog = dialog

        self.background_dialog.setWindowTitle(
            f"Background Reference — {self.comboBox_4.currentText()}"
        )
        self._update_emit_background_status()
        self._refresh_emit_background_preview()
        self.background_dialog.show()
        self.background_dialog.raise_()
        self.background_dialog.activateWindow()

    def _sync_emit_background_for_flag(self):
        self._clear_emit_background_reference()
        self._load_latest_emit_background(silent=True)
        if self.background_dialog is not None:
            self.background_dialog.setWindowTitle(
                f"Background Reference — {self.comboBox_4.currentText()}"
            )

    def _validate_emit_background_metadata(self, metadata, flag_name):
        expected = {
            "machine_id": self.machine_profile.machine.id,
            "control_backend": self.machine_type,
            "flag_id": flag_name,
        }
        for key, expected_value in expected.items():
            actual = metadata.get(key)
            if actual is not None and str(actual) != str(expected_value):
                raise BackgroundStoreError(
                    f"Background {key} {actual!r} does not match current {expected_value!r}."
                )

    def _emit_background_exposure_mismatch(self):
        if not self.background_metadata or not self.background_flag_id:
            return False
        exposure_pv = self._resolve_optional_channel(
            self.background_flag_id,
            "exposure_time",
        )
        current = _read_optional_scalar_pv(exposure_pv)
        saved = _finite_float_or_none(self.background_metadata.get("exposure_s"))
        return (
            current is not None
            and saved is not None
            and not math.isclose(current, saved, rel_tol=1e-6, abs_tol=1e-9)
        )

    def _background_reference_is_usable(self):
        return (
            self.background_image is not None
            and self.background_flag_id == self.comboBox_4.currentText()
            and not self._emit_background_exposure_mismatch()
        )

    def _set_emit_background_reference(self, image, metadata, image_path, *, warn=True):
        flag_name = self.comboBox_4.currentText()
        self._validate_emit_background_metadata(metadata, flag_name)
        self.background_image = np.asarray(image, dtype=float)
        self.background_metadata = dict(metadata)
        self.background_image_path = Path(image_path) if image_path is not None else None
        self.background_flag_id = flag_name
        if self._emit_background_exposure_mismatch():
            blocked = self.beam_image_background_checkbox.blockSignals(True)
            self.beam_image_background_checkbox.setChecked(False)
            self.beam_image_background_checkbox.blockSignals(blocked)
            if warn:
                self._warn(
                    "Loaded background exposure differs from the current camera exposure; "
                    "background subtraction remains disabled."
                )
        self._update_emit_background_status()
        self._refresh_emit_background_preview()

    def _clear_emit_background_reference(self):
        self.background_image = None
        self.background_metadata = {}
        self.background_image_path = None
        self.background_flag_id = None
        blocked = self.beam_image_background_checkbox.blockSignals(True)
        self.beam_image_background_checkbox.setChecked(False)
        self.beam_image_background_checkbox.blockSignals(blocked)
        self._update_emit_background_status()
        self._refresh_emit_background_preview()

    def _update_emit_background_status(self):
        if self.background_image is None:
            text = "None"
        else:
            sample_count = self.background_metadata.get("sample_count")
            sample_text = f" · {sample_count} frames" if sample_count else ""
            mismatch = " · exposure mismatch" if self._emit_background_exposure_mismatch() else ""
            applied = " · applied" if self.beam_image_background_checkbox.isChecked() else " · not applied"
            text = f"{self.background_flag_id}{sample_text}{mismatch}{applied}"
        self.beam_background_status_label.setText(text)
        if self.background_dialog is not None:
            self.background_dialog_status_label.setText(f"Background: {text}")

    def _refresh_emit_background_preview(self):
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
                cmap=self.beam_image_colormap_combo.currentText(),
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

    def _load_latest_emit_background(self, *, silent=False):
        flag_name = self.comboBox_4.currentText()
        if not flag_name:
            return False
        geometry = self._current_flag_pixel_geometry(flag_name)
        paths = resolve_beam_background_paths(self.app_context, flag_name)
        image_path = paths["background_image_path"]
        if not image_path.is_file():
            if not silent:
                self._warn(f"No saved background is available for {flag_name}.")
            return False
        try:
            image, metadata = load_background(
                image_path,
                paths["background_metadata_path"],
                expected_shape=(geometry.shape[1], geometry.shape[0]),
            )
            self._set_emit_background_reference(
                image,
                metadata,
                image_path,
                warn=not silent,
            )
        except (BackgroundStoreError, MachineProfileError, OSError, ValueError) as exc:
            if not silent:
                self._warn(f"Could not load background: {exc}")
            else:
                print(f"Could not auto-load background for {flag_name}: {exc}")
            return False
        return True

    def _choose_emit_background_file(self, *, save):
        paths = resolve_beam_background_paths(
            self.app_context,
            self.comboBox_4.currentText(),
        )
        dialog = QFileDialog(self.background_dialog or self)
        dialog.setOption(QFileDialog.DontUseNativeDialog, True)
        dialog.setNameFilter("NumPy files (*.npy)")
        if save:
            paths["runs_dir"].mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
            dialog.setWindowTitle("Save PRF Background")
            dialog.setAcceptMode(QFileDialog.AcceptSave)
            dialog.setDefaultSuffix("npy")
            dialog.setDirectory(str(paths["runs_dir"]))
            dialog.selectFile(
                f"{self.comboBox_4.currentText()}_background_{timestamp}.npy"
            )
        else:
            dialog.setWindowTitle("Load PRF Background")
            dialog.setFileMode(QFileDialog.ExistingFile)
            initial = paths["background_image_path"]
            dialog.setDirectory(
                str(initial.parent if initial.parent.is_dir() else paths["latest_dir"])
            )
        if dialog.exec_() != QDialog.Accepted:
            return None
        selected = dialog.selectedFiles()
        return Path(selected[0]) if selected else None

    def _load_emit_background_file(self):
        image_path = self._choose_emit_background_file(save=False)
        if image_path is None:
            return
        geometry = self._current_flag_pixel_geometry()
        try:
            image, metadata = load_background(
                image_path,
                image_path.with_suffix(".json"),
                expected_shape=(geometry.shape[1], geometry.shape[0]),
            )
            self._set_emit_background_reference(image, metadata, image_path)
        except (BackgroundStoreError, MachineProfileError, OSError, ValueError) as exc:
            self._warn(f"Could not load background: {exc}")

    def _save_emit_background_as(self):
        if self.background_image is None:
            self._warn("No background is available to save.")
            return
        image_path = self._choose_emit_background_file(save=True)
        if image_path is None:
            return
        metadata = dict(self.background_metadata)
        metadata.update({
            "source": "emit_measure_save_as",
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        })
        try:
            save_background(
                self.background_image,
                image_path,
                image_path.with_suffix(".json"),
                metadata,
            )
        except (BackgroundStoreError, OSError, ValueError) as exc:
            self._warn(f"Could not save background: {exc}")

    def _set_background_application(self, checked):
        if checked and not self._background_reference_is_usable():
            blocked = self.beam_image_background_checkbox.blockSignals(True)
            self.beam_image_background_checkbox.setChecked(False)
            self.beam_image_background_checkbox.blockSignals(blocked)
            self._warn(
                "Load or sample a matching background before enabling subtraction."
            )
            self._update_emit_background_status()
            return
        self._update_emit_background_status()
        self.refresh_current_beam_image_fit(show_warning=False)

    def _sample_background(self):
        if self._scan_is_running():
            self._warn("Stop the emittance scan before sampling a background.")
            return
        if QMessageBox.question(
            self,
            "Sample PRF Background",
            "Confirm that the beam is absent. Sample and replace the saved background now?",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        ) != QMessageBox.Yes:
            return

        flag_name = self.comboBox_4.currentText()
        try:
            image_pv = resolve_channel(self.machine_profile, flag_name, "image", self.machine_type)
            exposure_pv = self._resolve_optional_channel(flag_name, "exposure_time")
            pixel_shape = self._current_flag_pixel_geometry(flag_name).shape
        except (MachineProfileError, ValueError) as exc:
            self._warn(str(exc))
            return

        count = self.background_samples_spin.value()
        interval_s = self.background_interval_spin.value()
        expected_shape = (pixel_shape[1], pixel_shape[0])
        timer_was_active = self.beam_image_timer.isActive()
        self.beam_image_timer.stop()
        self.background_sample_button.setEnabled(False)
        self.background_dialog_status_label.setText(f"Sampling {count} frames from {flag_name}…")
        QApplication.processEvents()
        images = []
        try:
            for index in range(count):
                if index > 0 and interval_s > 0:
                    deadline = time.monotonic() + interval_s
                    while time.monotonic() < deadline:
                        QApplication.processEvents()
                        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
                raw = epics.caget(image_pv)
                if raw is None:
                    raise BackgroundStoreError(f"{image_pv} returned no image data.")
                image = np.asarray(raw, dtype=float).reshape(expected_shape)
                if not np.all(np.isfinite(image)):
                    raise BackgroundStoreError("Sampled background contains non-finite values.")
                images.append(image)

            background = np.mean(images, axis=0)
            paths = resolve_beam_background_paths(self.app_context, flag_name)
            metadata = {
                "machine_id": self.machine_profile.machine.id,
                "control_backend": self.machine_type,
                "flag_id": flag_name,
                "image_pv": image_pv,
                "pixel_shape": [int(pixel_shape[0]), int(pixel_shape[1])],
                "exposure_s": _read_optional_scalar_pv(exposure_pv),
                "source": "emit_measure_sampled",
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "sample_count": count,
                "sample_interval_s": interval_s,
            }
            image_path, _ = save_background(
                background,
                paths["background_image_path"],
                paths["background_metadata_path"],
                metadata,
            )
        except (BackgroundStoreError, OSError, TypeError, ValueError) as exc:
            self._warn(f"Background sampling failed: {exc}")
            self.background_dialog_status_label.setText(f"Sampling failed: {exc}")
        else:
            self._set_emit_background_reference(background, metadata, image_path)
            self.beam_image_background_checkbox.setChecked(True)
            self.background_dialog_status_label.setText(
                f"Saved {count} frames for {flag_name} · {image_path.name}"
            )
            self.refresh_current_beam_image_fit(show_warning=False)
        finally:
            self.background_sample_button.setEnabled(True)
            if timer_was_active and self.beam_image_auto_refresh_checkbox.isChecked():
                self.beam_image_timer.start()
 
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
        self.latest_scan_completion = None
        self._scan_result_ready = False
        self.display({"clear": True, "preserve_beam_image": True})
        self.paras.recal = False
        self.paras.clear = False 
        self.paras.scan_metadata = self._scan_metadata_from_paras(self.paras)
        self.paras.scan_latest_dir = self._scan_latest_dir()
        self.paras.scan_archive_dir = self._scan_archive_dir()
        self.pending_scan_metadata = dict(self.paras.scan_metadata)
        self.scan_mode = "scan"
        self._begin_scan_progress(self.paras)
        self.scan_strategy_combo.setEnabled(False)
        scan_start_label = (
            "Starting adaptive scan"
            if self.paras.scan_strategy in ADAPTIVE_SCAN_STRATEGIES
            else "Grid scan running"
        )
        self.scan_strategy_status_label.setText(
            scan_start_label
        )
        self.scan_strategy_status_label.setToolTip("")
        self.scan = scanThread(self.paras)
        self.scan.trigger.connect(self.display)
        self.scan.finished.connect(self._on_scan_finished)
        self.scan.start()
        self._refresh_status()

    def stopScan(self):
        if self._scan_is_running():
            self.scan_mode = "stopping"
            self.latest_scan_completion = None
            self._scan_result_ready = False
            self.scan_progress.setFormat(
                f"Stopping · {self._scan_progress_completed} points"
            )
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

        model_snapshot = self.loaded_scan_metadata["model_snapshot"]
        archived_overrides = model_snapshot_lattice_overrides(model_snapshot)
        if archived_overrides is None:
            self._warn("Scan metadata model_snapshot has no usable lattice overrides.")
            return
        self.paras.model_snapshot_metadata = dict(model_snapshot)
        self.paras.model_lattice_overrides = archived_overrides
        if isinstance(self.loaded_scan_metadata, Mapping):
            self.paras.scan_metadata = dict(self.loaded_scan_metadata)
            self.paras.scan_strategy = str(self.loaded_scan_metadata["scan_strategy"])

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
        self.latest_scan_completion = None
        self._scan_result_ready = False
        self.scan_mode = "recalculate"
        self._scan_progress_completed = 0
        self._scan_progress_limit = 1
        self._scan_progress_bounded = True
        self.scan_progress.setRange(0, 1)
        self.scan_progress.setValue(0)
        self.scan_progress.setFormat("Recalculating")
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
        self.latest_twiss_profile = None
        self.latest_twiss_design_profile = None
        if not self.twiss_design_checkbox.isEnabled():
            blocked = self.twiss_design_checkbox.blockSignals(True)
            self.twiss_design_checkbox.setChecked(True)
            self.twiss_design_checkbox.blockSignals(blocked)
        self.twiss_design_checkbox.setEnabled(False)
        self._draw_twiss_profile()
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
            self.latest_scan_completion = None
            self._scan_result_ready = False
            self.scan_strategy_status_label.setText("Scan error")
            self.scan_strategy_status_label.setToolTip(str(dict["error"]))
            self.latest_emit_fit_summary = {
                "method": "scan",
                "status": "error",
                "message": str(dict["error"]),
            }
            self._finish_scan_progress("failed")
            self._refresh_status()
            self._warn(dict["error"])
            return

        if "scan_progress" in dict:
            self._update_scan_progress(dict["scan_progress"])
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
            self.latest_scan_completion = None
            self._scan_result_ready = False
            self.latest_twiss_summary = None
            self.latest_twiss_profile = None
            self.latest_twiss_design_profile = None
            self.twiss_initial_source = {"kind": "manual"}
            if self.scan_progress is not None:
                self._scan_progress_completed = 0
                self._scan_progress_limit = 1
                self.scan_progress.setRange(0, 1)
                self.scan_progress.setValue(0)
                self.scan_progress.setFormat("Idle")
            self._draw_twiss_profile()
            if self._selected_scan_strategy() == "grid":
                self.scan_strategy_status_label.setText("Grid scan")
                self.scan_strategy_status_label.setToolTip("")
            self._refresh_status()
            
            return

        if "adaptive_status" in dict:
            adaptive_status = str(dict["adaptive_status"])
            self.scan_strategy_status_label.setText(
                _compact_status_text(adaptive_status, limit=46)
            )
            self.scan_strategy_status_label.setToolTip(adaptive_status)

        if "beam_image" in dict and "beam_fit" in dict:
            self._display_beam_image_fit(
                dict.get("flag", self.comboBox_4.currentText()),
                dict["beam_image"],
                dict["beam_fit"],
                k1=dict.get("k1"),
                extent=dict.get("beam_extent"),
                size_pv=dict.get("size_pv", (None, None)),
                background_status=dict.get("background_status", "Off"),
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
            self._scan_result_ready = True

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
                fit_x_raw = np.asarray(xx, dtype=float)
                fit_y_raw = np.asarray(result["fit_yy"], dtype=float)
                fit_coefficients = np.polyfit(fit_x_raw, fit_y_raw, 2)
                fit_x_internal = np.linspace(
                    float(np.min(fit_x_raw)),
                    float(np.max(fit_x_raw)),
                    200,
                )
                fit_x = plot_sign * fit_x_internal
                fit_y = np.polyval(fit_coefficients, fit_x_internal)
                fit_order = np.argsort(fit_x)
                widget.axes.plot(
                    fit_x[fit_order],
                    fit_y[fit_order],
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

        fields[0].setText(
            "Non-physical fit"
            if result.get("status") == "non_physical"
            else "Fit failed"
        )
        for field in fields[1:]:
            field.setText("--")
        text_field.setText(_compact_status_text(result.get("message", result.get("status"))))

    def _display_least_square_plane(self, plane, result):
        if plane == "xplane":
            fields = (self.lineEdit_4, self.lineEdit_5, self.lineEdit_20, self.lineEdit_19, self.lineEdit_18)
        else:
            fields = (self.lineEdit_41, self.lineEdit_42, self.lineEdit_43, self.lineEdit_44, self.lineEdit_45)

        diagnostic_text = _least_squares_diagnostic_text(
            result,
            include_message=result.get("status") != "valid",
        )
        for field in fields:
            field.setToolTip(diagnostic_text)

        if result.get("status") == "valid":
            for field, key in zip(fields, ("ex", "exn", "beta", "alpha", "gamma")):
                field.setText(str(result.get(key)))
            return

        failure_labels = {
            "non_physical": "Non-physical fit",
            "rank_deficient": "Rank-deficient fit",
            "ill_conditioned": "Ill-conditioned fit",
        }
        fields[0].setText(failure_labels.get(result.get("status"), "Fit failed"))
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
            self.latest_twiss_profile = None
            self.latest_twiss_design_profile = None
            self.twiss_design_checkbox.setEnabled(True)
            self._draw_twiss_profile()
            self._refresh_status()
            self._warn_twiss(message)
            return
        beta = round(dict["beta"], 2)
        alpha = round(dict["alpha"], 2)
        gamma = round(dict["gamma"], 2)
        matrix_summary = dict.get("matrix")
        self.latest_twiss_profile = tuple(dict.get("profile") or ())
        self.latest_twiss_design_profile = tuple(dict.get("design_profile") or ())
        if self.latest_twiss_design_profile:
            self.twiss_design_checkbox.setEnabled(True)
            self.twiss_design_checkbox.setToolTip(
                "Overlay the Elegant design-lattice Twiss profile."
            )
        else:
            blocked = self.twiss_design_checkbox.blockSignals(True)
            self.twiss_design_checkbox.setChecked(False)
            self.twiss_design_checkbox.blockSignals(blocked)
            self.twiss_design_checkbox.setEnabled(False)
            self.twiss_design_checkbox.setToolTip(
                str(dict.get("design_error") or "Design profile is unavailable.")
            )

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
        self._draw_twiss_profile()
        status_text = self._format_twiss_status_tooltip(self.latest_twiss_summary)
        try:
            log_path = self._append_twiss_result_log(dict)
        except Exception as exc:
            print(f"Warning: failed to write Twiss result log: {exc}")
        else:
            status_text = f"{status_text}; logged to {log_path.name}"
        if dict.get("design_error"):
            status_text = f"{status_text}; design comparison unavailable"
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
            profile = trans.getTwissProfile(
                quad1,
                quad2,
                twiss0,
                plane=plane,
                inverse=inverse,
            )
            matrix = profile.matrix
            endpoint = profile.rows[-1]
            twiss1 = {
                "beta": endpoint["beta"],
                "alpha": endpoint["alpha"],
                "gamma": endpoint["gamma"],
                "profile": [dict(row) for row in profile.rows],
            }
            try:
                design_trans = transfer(
                    self.input["EnergyMeV"],
                    app_context=self.input["app_context"],
                )
                design_profile = design_trans.getTwissProfile(
                    quad1,
                    quad2,
                    twiss0,
                    plane=plane,
                    inverse=inverse,
                )
            except Exception as exc:
                twiss1["design_profile"] = []
                twiss1["design_endpoint"] = None
                twiss1["design_error"] = str(exc)
            else:
                twiss1["design_profile"] = [dict(row) for row in design_profile.rows]
                design_endpoint = design_profile.rows[-1]
                twiss1["design_endpoint"] = {
                    key: design_endpoint[key] for key in ("beta", "alpha", "gamma")
                }
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
        self.flagSigxPV = getattr(paras, "flagSigxPV", None)
        self.flagSigyPV = getattr(paras, "flagSigyPV", None)
        self.flag_pixel_shape = paras.flag_pixel_shape
        self.flag_image_extent = paras.flag_image_extent
        self.background_image = getattr(paras, "background_image", None)
        self.background_status = getattr(paras, "background_status", "Off")
        self.k1_from    = paras.k1_from   
        self.k1_end     = paras.k1_end    
        self.k1_steps   = paras.k1_steps  
        self.k1_mode = getattr(paras, "k1_mode", "absolute")
        self.k1_unit = getattr(paras, "k1_unit", "1/m^2")
        self.samples    = paras.samples   
        self.EnergyMeV  = paras.EnergyMeV
        self.settle_time = paras.settle_time
        self.sample_interval = paras.sample_interval
        self.scan_strategy = paras.scan_strategy
        self.adaptive_config = getattr(paras, "adaptive_config", None)
        self.model_line = paras.model_line
        self.app_context = paras.app_context
        self.model_snapshot_metadata = getattr(paras, "model_snapshot_metadata", None)
        self.model_lattice_overrides = getattr(paras, "model_lattice_overrides", None)
        self.quad_length = None

        self.recal      = paras.recal 
        self.recal_points = getattr(paras, "recal_points", None)
        self.scan_metadata = getattr(paras, "scan_metadata", None)
        self.scan_archive_dir = Path(
            getattr(paras, "scan_archive_dir", APP_DIR / "runtime" / "unknown" / "unknown" / "runs")
        )
        self.scan_latest_dir = Path(
            getattr(paras, "scan_latest_dir", self.scan_archive_dir / "latest")
        )
        self.scan_results_path = self.scan_latest_dir / SCAN_RESULTS_FILENAME
        self.scan_results_meta_path = self.scan_latest_dir / METADATA_FILENAME
        self.scan_metadata_paths = []
        self.point_quality = []
        self.x_quality_usable = []
        self.y_quality_usable = []
        self.completed_k1_points = 0
        self._progress_stage = "grid" if self.scan_strategy == "grid" else "seed"
        self.adaptive_plane_validation = None
        self.final_plane_validation = None
        self.is_running = True
        self.effective_k1_limit = None

    def _sleep_or_stop(self, seconds):
        end_time = time.time() + seconds
        while self.is_running and time.time() < end_time:
            time.sleep(min(0.1, end_time - time.time()))
        return self.is_running

    def _restore_quad(self, value):
        if value is not None:
            epics.caput(self.quadPV, value)

    @staticmethod
    def _sample_error(values):
        values = np.asarray(values, dtype=float)
        if values.size < 2:
            return None
        return float(np.std(values, ddof=1) / np.sqrt(values.size))

    def _emit_adaptive_status(self, text, **extra):
        payload = {"method": None, "adaptive_status": text}
        payload.update(extra)
        if extra.get("adaptive_stage"):
            self._progress_stage = str(extra["adaptive_stage"])
            payload["scan_progress"] = self._scan_progress_payload()
        self.trigger.emit(payload)

    def _scan_progress_payload(self, *, sample_index=None):
        payload = {
            "completed_points": self.completed_k1_points,
            "stage": self._progress_stage,
        }
        if sample_index is not None:
            payload.update(
                {
                    "sample_index": sample_index,
                    "sample_count": self.samples,
                }
            )
        return payload

    def _emit_scan_progress(self, *, stage=None, sample_index=None):
        if stage is not None:
            self._progress_stage = str(stage)
        self.trigger.emit(
            {
                "method": None,
                "scan_progress": self._scan_progress_payload(
                    sample_index=sample_index,
                ),
            }
        )

    def _acquire_k1(self, k1, *, adaptive=False):
        if self.effective_k1_limit is not None and not self.effective_k1_limit.contains(k1):
            raise MachineProfileError(
                f"Planned K1 {float(k1):g} is outside effective limit "
                f"{self.effective_k1_limit.describe()} for {self.quad_name}.K1."
            )
        epics.caput(self.quadPV, k1)
        if not self._sleep_or_stop(self.settle_time):
            return None

        sigx_values = []
        sigy_values = []
        usable_sigx_values = []
        usable_sigy_values = []
        for sample_index in range(self.samples):
            if not self.is_running:
                return None
            self._emit_scan_progress(sample_index=sample_index + 1)
            if sample_index > 0 and not self._sleep_or_stop(self.sample_interval):
                return None

            retry = 0
            while self.is_running:
                try:
                    image, fit_result = _read_flag_image_fit(
                        self.flagImagePV,
                        self.flag_pixel_shape,
                        self.flag_image_extent,
                        background=self.background_image,
                    )
                except RuntimeError as exc:
                    if not adaptive or retry >= self.adaptive_config.max_retries:
                        if adaptive:
                            self._emit_adaptive_status(
                                f"Rejected K1 {k1:g}: image unavailable",
                                k1=k1,
                            )
                            return None
                        raise
                    retry += 1
                    self._emit_adaptive_status(
                        f"Retrying K1 {k1:g} ({retry}/{self.adaptive_config.max_retries})",
                        k1=k1,
                        retry_reason=str(exc),
                    )
                    if self.sample_interval > 0 and not self._sleep_or_stop(
                        self.sample_interval
                    ):
                        return None
                    continue
                size_pv = _read_optional_size_pvs(
                    self.flagSigxPV,
                    self.flagSigyPV,
                )
                point = {
                    "method": None,
                    "k1": k1,
                    "flag": self.flag_name,
                    "beam_image": image,
                    "beam_fit": fit_result,
                    "beam_extent": self.flag_image_extent,
                    "size_pv": size_pv,
                    "background_status": self.background_status,
                }
                if fit_result.valid:
                    break
                self.trigger.emit(point)
                detail = f": {fit_result.message}" if fit_result.message else ""
                if not adaptive or retry >= self.adaptive_config.max_retries:
                    if adaptive:
                        self._emit_adaptive_status(
                            f"Rejected K1 {k1:g}: invalid image fit",
                            k1=k1,
                        )
                        return None
                    raise RuntimeError(
                        f"Flag image fit failed for {self.flag_name} "
                        f"({fit_result.status}){detail}."
                    )
                retry += 1
                self._emit_adaptive_status(
                    f"Retrying K1 {k1:g} ({retry}/{self.adaptive_config.max_retries})",
                    k1=k1,
                )
                if self.sample_interval > 0 and not self._sleep_or_stop(self.sample_interval):
                    return None

            if not self.is_running:
                return None
            sigx = float(fit_result.sigx_mm)
            sigy = float(fit_result.sigy_mm)
            if self.scan_strategy == "adaptive_quality":
                x_quality = _projection_measurement_quality(fit_result.x_projection)
                y_quality = _projection_measurement_quality(fit_result.y_projection)
            else:
                x_quality = {"status": "usable", "usable": True}
                y_quality = {"status": "usable", "usable": True}
            print("Quad K1=", k1, "sigmax=", sigx, "sigmay=", sigy)
            point["sigx"] = sigx
            point["sigy"] = sigy
            point["plane_quality"] = {"x": x_quality, "y": y_quality}
            if self.scan_strategy == "adaptive_quality" and (
                not x_quality["usable"] or not y_quality["usable"]
            ):
                self._emit_adaptive_status(
                    f"Quality K1 {k1:g} · X {x_quality['status']} · Y {y_quality['status']}",
                    k1=k1,
                    plane_quality=point["plane_quality"],
                )
            point["adaptive_stage"] = "measurement" if adaptive else None
            self.k1l.append(k1)
            self.sigxl.append(sigx)
            self.sigyl.append(sigy)
            self.x_quality_usable.append(bool(x_quality["usable"]))
            self.y_quality_usable.append(bool(y_quality["usable"]))
            self.point_quality.append(
                {"k1": float(k1), "x": x_quality, "y": y_quality}
            )
            sigx_values.append(sigx)
            sigy_values.append(sigy)
            if x_quality["usable"]:
                usable_sigx_values.append(sigx)
            if y_quality["usable"]:
                usable_sigy_values.append(sigy)
            self.trigger.emit(point)

        if not sigx_values:
            return None
        self.completed_k1_points += 1
        self._emit_scan_progress()
        return AdaptiveObservation(
            k1=float(k1),
            sigx=float(np.mean(usable_sigx_values)) if usable_sigx_values else None,
            sigy=float(np.mean(usable_sigy_values)) if usable_sigy_values else None,
            sigx_err=self._sample_error(usable_sigx_values),
            sigy_err=self._sample_error(usable_sigy_values),
            x_usable=bool(usable_sigx_values),
            y_usable=bool(usable_sigy_values),
        )

    def _run_grid_scan(self):
        self._emit_scan_progress(stage="grid")
        for k1 in np.linspace(self.k1_from, self.k1_end, self.k1_steps):
            if not self.is_running:
                return
            if self._acquire_k1(float(k1), adaptive=False) is None:
                return

    def _run_adaptive_scan(self):
        if self.adaptive_config is None:
            raise RuntimeError("Adaptive scan configuration is missing.")
        observations = []
        initial_values = seed_values(
            self.k1_from,
            self.k1_end,
            self.adaptive_config,
        )
        for index, k1 in enumerate(initial_values, 1):
            if not self.is_running:
                return
            self._emit_adaptive_status(
                f"Seed {index}/{len(initial_values)} · K1 {k1:g}",
                adaptive_stage="seed",
            )
            observation = self._acquire_k1(k1, adaptive=True)
            if observation is not None:
                observations.append(observation)

        if not self.is_running:
            return
        if len(observations) < 3:
            raise RuntimeError(
                "Adaptive seed scan produced fewer than 3 valid measurement points."
            )
        recovery_values = []
        if self.scan_strategy == "adaptive_quality":
            while len(observations) < self.adaptive_config.max_unique_points:
                candidates = quality_recovery_values(observations, self.adaptive_config)
                if not candidates:
                    break
                recovered_observation = False
                for k1 in candidates:
                    if not self.is_running:
                        return
                    recovery_values.append(k1)
                    self._emit_adaptive_status(
                        f"Seed recovery {len(recovery_values)} · K1 {k1:g}",
                        adaptive_stage="seed_recovery",
                    )
                    observation = self._acquire_k1(k1, adaptive=True)
                    if observation is not None:
                        observations.append(observation)
                        recovered_observation = True
                if not recovered_observation:
                    break
        try:
            plan = build_adaptive_plan(observations, self.adaptive_config)
        except ValueError as exc:
            if self.scan_strategy == "adaptive_quality":
                raise RuntimeError(
                    "Adaptive Quality could not find 3 usable seed observations "
                    f"for both planes: {exc}"
                ) from exc
            raise
        self._emit_adaptive_status(
            "Adapted ranges · "
            f"X [{plan.x.k1_from:.3g}, {plan.x.k1_to:.3g}] · "
            f"Y [{plan.y.k1_from:.3g}, {plan.y.k1_to:.3g}]",
            adaptive_stage="adapt_range",
            adaptive_plan={
                "x": [plan.x.k1_from, plan.x.k1_to],
                "y": [plan.y.k1_from, plan.y.k1_to],
            },
        )
        for index, k1 in enumerate(plan.new_values, 1):
            if not self.is_running:
                return
            self._emit_adaptive_status(
                f"Refine {index}/{len(plan.new_values)} · K1 {k1:g}",
                adaptive_stage="refine",
            )
            observation = self._acquire_k1(k1, adaptive=True)
            if observation is not None:
                observations.append(observation)
        if len({round(point.k1, 12) for point in observations}) < 3:
            raise RuntimeError("Adaptive scan has fewer than 3 valid unique K1 values.")

        validation = validate_adaptive_scan(observations, self.adaptive_config)
        supplement_values = validation.new_values
        if supplement_values:
            self._emit_adaptive_status(
                "Validation supplement · "
                f"X {validation.x.status} · Y {validation.y.status}",
                adaptive_stage="validation_supplement",
                plane_validation=validation.as_dict(),
            )
            for index, k1 in enumerate(supplement_values, 1):
                if not self.is_running:
                    return
                self._emit_adaptive_status(
                    f"Validate {index}/{len(supplement_values)} · K1 {k1:g}",
                    adaptive_stage="validation_supplement",
                )
                observation = self._acquire_k1(k1, adaptive=True)
                if observation is not None:
                    observations.append(observation)
            validation = validate_adaptive_scan(observations, self.adaptive_config)

        quality_supplement_attempted = []
        if self.scan_strategy == "adaptive_quality":
            final_windows = build_final_fit_windows(observations, self.adaptive_config)
            remaining = MAX_QUALITY_SUPPLEMENT_POINTS
            while remaining > 0 and (
                final_window_point_count(observations, final_windows.x, self.adaptive_config)
                < MIN_FINAL_POINTS_PER_PLANE
                or final_window_point_count(observations, final_windows.y, self.adaptive_config)
                < MIN_FINAL_POINTS_PER_PLANE
            ):
                candidates = quality_supplement_values(
                    observations,
                    self.adaptive_config,
                    excluded_values=quality_supplement_attempted,
                    max_new_points=remaining,
                )
                if not candidates:
                    break
                acquired = False
                for k1 in candidates:
                    if not self.is_running:
                        return
                    quality_supplement_attempted.append(k1)
                    self._emit_adaptive_status(
                        "Quality supplement "
                        f"{len(quality_supplement_attempted)}/{MAX_QUALITY_SUPPLEMENT_POINTS} "
                        f"· K1 {k1:g}",
                        adaptive_stage="quality_supplement",
                    )
                    observation = self._acquire_k1(k1, adaptive=True)
                    if observation is not None:
                        observations.append(observation)
                        acquired = True
                remaining = MAX_QUALITY_SUPPLEMENT_POINTS - len(
                    quality_supplement_attempted
                )
                if not acquired:
                    break
                final_windows = build_final_fit_windows(
                    observations,
                    self.adaptive_config,
                )
            validation = validate_adaptive_scan(observations, self.adaptive_config)
        else:
            final_windows = None

        validation_payload = validation.as_dict()
        validation_payload["supplement_attempted"] = list(supplement_values)
        if final_windows is not None:
            quality_counts = {
                "minimum": MIN_FINAL_POINTS_PER_PLANE,
                "x": final_window_point_count(
                    observations,
                    final_windows.x,
                    self.adaptive_config,
                ),
                "y": final_window_point_count(
                    observations,
                    final_windows.y,
                    self.adaptive_config,
                ),
                "supplement_attempted": list(quality_supplement_attempted),
                "supplement_limit": MAX_QUALITY_SUPPLEMENT_POINTS,
            }
            validation_payload["quality_points"] = quality_counts
            for plane in ("x", "y"):
                validation_payload[plane]["quality_points"] = quality_counts[plane]
                validation_payload[plane]["minimum_quality_points"] = (
                    MIN_FINAL_POINTS_PER_PLANE
                )
                if quality_counts[plane] < MIN_FINAL_POINTS_PER_PLANE:
                    validation_payload[plane]["status"] = "insufficient_quality_points"
                    validation_payload[plane]["message"] = (
                        f"only {quality_counts[plane]} quality-approved unique K1 points "
                        f"are available; at least {MIN_FINAL_POINTS_PER_PLANE} are required"
                    )
        self.adaptive_plane_validation = validation_payload
        self._emit_adaptive_status(
            "Adaptive validation · "
            f"X {validation.x.status} · Y {validation.y.status}",
            adaptive_stage="validate",
            plane_validation=validation_payload,
        )
        fit_x = final_windows.x if final_windows is not None else plan.x
        fit_y = final_windows.y if final_windows is not None else plan.y
        if isinstance(self.scan_metadata, dict):
            self.scan_metadata["adaptive_result"] = {
                "x_range": [fit_x.k1_from, fit_x.k1_to],
                "y_range": [fit_y.k1_from, fit_y.k1_to],
                "x_waist_k1": fit_x.waist_k1,
                "y_waist_k1": fit_y.waist_k1,
                "x_method": fit_x.method,
                "y_method": fit_y.method,
                "seed_x_range": [plan.x.k1_from, plan.x.k1_to],
                "seed_y_range": [plan.y.k1_from, plan.y.k1_to],
                "seed_recovery_values": list(recovery_values),
                "quality_supplement_values": list(quality_supplement_attempted),
                "minimum_final_points_per_plane": MIN_FINAL_POINTS_PER_PLANE,
                "max_quality_supplement_points": MAX_QUALITY_SUPPLEMENT_POINTS,
                "validation_reserved_points": plan.validation_reserved_points,
                "valid_unique_k1": len(observations),
            }
            self.scan_metadata["plane_validation"] = validation_payload
            self.scan_metadata["point_quality"] = list(self.point_quality)

    def _restore_point_quality_for_recalculation(self):
        total = len(self.k1l)
        if self.scan_strategy != "adaptive_quality":
            self.x_quality_usable = [True] * total
            self.y_quality_usable = [True] * total
            return
        metadata = self.scan_metadata if isinstance(self.scan_metadata, Mapping) else {}
        entries = metadata.get("point_quality")
        if not isinstance(entries, list):
            raise RuntimeError("Adaptive Quality scan metadata has no point_quality records.")

        quality_by_k1 = defaultdict(lambda: {"x": False, "y": False})
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            try:
                key = round(float(entry["k1"]), 12)
            except (KeyError, TypeError, ValueError):
                continue
            for plane in ("x", "y"):
                quality = entry.get(plane)
                if isinstance(quality, Mapping) and bool(quality.get("usable")):
                    quality_by_k1[key][plane] = True
        self.x_quality_usable = [
            quality_by_k1[round(float(k1), 12)]["x"] for k1 in self.k1l
        ]
        self.y_quality_usable = [
            quality_by_k1[round(float(k1), 12)]["y"] for k1 in self.k1l
        ]

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
                self.k1l =[]
                self.sigxl = []
                self.sigyl = []
                self.x_quality_usable = []
                self.y_quality_usable = []
                self.point_quality = []

                iniK1 = epics.caget(self.quadPV)
                if iniK1 is None:
                    raise RuntimeError(f"Failed to read initial quad value from {self.quadPV}.")
                scan_limit = effective_k1_scan_limit(
                    self.app_context,
                    self.quad_name,
                    self.k1_from,
                    self.k1_end,
                    self.k1_mode,
                    self.k1_unit,
                    iniK1,
                )
                assert scan_limit.low is not None and scan_limit.high is not None
                self.k1_from, self.k1_end = scan_limit.low, scan_limit.high
                self.effective_k1_limit = scan_limit
                if self.scan_strategy in ADAPTIVE_SCAN_STRATEGIES:
                    adaptive_limit = effective_k1_scan_limit(
                        self.app_context,
                        self.quad_name,
                        self.adaptive_config.k1_min,
                        self.adaptive_config.k1_max,
                        self.k1_mode,
                        self.k1_unit,
                        iniK1,
                    )
                    assert adaptive_limit.low is not None and adaptive_limit.high is not None
                    self.adaptive_config = replace(
                        self.adaptive_config,
                        k1_min=adaptive_limit.low,
                        k1_max=adaptive_limit.high,
                    )
                    self.effective_k1_limit = adaptive_limit
                    self._run_adaptive_scan()
                else:
                    self._run_grid_scan()
                if not self.is_running:
                    print("Stop scan, quad is back to initial values, K1=", iniK1)
                    return

                self._emit_scan_progress(stage="finalizing")
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
                self._restore_point_quality_for_recalculation()
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
            if self.scan_strategy in ADAPTIVE_SCAN_STRATEGIES:
                k1_array = np.asarray(self.k1l, dtype=float)
                sigx_array = np.asarray(self.sigxl, dtype=float)
                sigy_array = np.asarray(self.sigyl, dtype=float)
                polynomial_design = np.column_stack(
                    (np.ones_like(k1_array), k1_array, k1_array**2)
                )
                x_indices, x_selection = self._least_squares_selection(
                    k1_array, polynomial_design, "xplane"
                )
                y_indices, y_selection = self._least_squares_selection(
                    k1_array, polynomial_design, "yplane"
                )
                tmp["method"] = "parabolic"
                tmp["xplane"] = self._parabolic_selected_plane_result(
                    k1_array[x_indices],
                    sigx_array[x_indices],
                    m11,
                    m12,
                    "xplane",
                    x_selection,
                )
                tmp["yplane"] = self._parabolic_selected_plane_result(
                    -k1_array[y_indices],
                    sigy_array[y_indices],
                    m33,
                    m34,
                    "yplane",
                    y_selection,
                )
                parabolic_summary = _method_fit_summary(
                    "parabolic", tmp["xplane"], tmp["yplane"]
                )
                fit_summary = {"parabolic": parabolic_summary}
                tmp["fit_summary"] = parabolic_summary
                self.trigger.emit(tmp)
                if not self._sleep_or_stop(2):
                    return
                print(f"Parabolic fitting finished: {parabolic_summary['status']}")
            else:
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
            self._attach_adaptive_plane_validation(
                least_squares_summary,
                {"xplane": tmpxx, "yplane": tmpyy},
            )
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

        self.scan_archive_dir.mkdir(parents=True, exist_ok=True)
        archive_dir = self.scan_archive_dir / self._scan_archive_stem()
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = archive_dir / SCAN_RESULTS_FILENAME
        archive_meta_path = archive_dir / METADATA_FILENAME
        np.savetxt(archive_path, data, fmt="%.6e")
        archive_meta_path.write_text(metadata_text, encoding="utf-8")
        self.scan_metadata_paths = [
            self.scan_results_meta_path,
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
            if self.final_plane_validation is not None:
                metadata["plane_validation"] = self.final_plane_validation
            metadata["fit_summary_updated_at"] = datetime.now().isoformat(timespec="seconds")
            metadata_path.write_text(
                json.dumps(metadata, indent=2, sort_keys=True),
                encoding="utf-8",
            )

    def _attach_adaptive_plane_validation(self, fit_summary, plane_payloads):
        validation = self.adaptive_plane_validation
        if self.scan_strategy not in ADAPTIVE_SCAN_STRATEGIES or not isinstance(validation, Mapping):
            return

        final = {"status": "unresolved"}
        validated_count = 0
        for plane_key, coverage_key in (("xplane", "x"), ("yplane", "y")):
            coverage = validation.get(coverage_key, {})
            reconstruction = fit_summary.get(plane_key, {})
            coverage_status = str(coverage.get("status", "unresolved"))
            reconstruction_status = str(reconstruction.get("status", "unresolved"))
            if coverage_status == "validated" and reconstruction_status == "valid":
                final_status = "validated"
                validated_count += 1
            elif reconstruction_status != "valid":
                final_status = reconstruction_status
            else:
                final_status = coverage_status

            plane_validation = dict(coverage)
            plane_validation.update(
                {
                    "status": final_status,
                    "coverage_status": coverage_status,
                    "reconstruction_status": reconstruction_status,
                }
            )
            final[coverage_key] = plane_validation
            reconstruction["validation_status"] = final_status
            reconstruction["coverage_status"] = coverage_status
            reconstruction["coverage_message"] = str(coverage.get("message", "") or "")
            reconstruction["coverage_warnings"] = list(coverage.get("warnings", ()))
            raw_payload = plane_payloads[plane_key]
            raw_payload["validation_status"] = final_status
            raw_payload["coverage_status"] = coverage_status
            raw_payload["coverage_message"] = reconstruction["coverage_message"]
            raw_payload["coverage_warnings"] = reconstruction["coverage_warnings"]

        if validated_count == 2:
            quality_status = "validated"
        elif validated_count == 1:
            quality_status = "partial"
        else:
            quality_status = "unresolved"
        final["status"] = quality_status
        final["supplement_attempted"] = list(validation.get("supplement_attempted", ()))
        fit_summary["quality_status"] = quality_status
        fit_summary["plane_validation"] = final
        self.final_plane_validation = final

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
        for key in (
            "determinant",
            "rank",
            "condition_number",
            "residual_rms",
            "solver",
            "fit_selection",
        ):
            value = getattr(result, key, None)
            if value is not None:
                payload[key] = value
        return payload

    def _parabolic_plane_result(self, k1l, sigxl, m11, m12, plane):
        try:
            return self.parabolicfitting(k1l, sigxl, m11, m12)
        except Exception as exc:
            print(f"Warning: parabolic fitting failed for {plane}: {exc}")
            return _invalid_plane_result("fit_failed", str(exc))

    def _parabolic_selected_plane_result(
        self,
        k1l,
        sigxl,
        m11,
        m12,
        plane,
        fit_selection,
    ):
        try:
            dim0 = len(k1l) / self.samples
            grouped_k1 = np.reshape(k1l, (int(dim0), self.samples))
            grouped_sigma = np.reshape(sigxl, (int(dim0), self.samples))
        except ValueError:
            result = _invalid_plane_result(
                "fit_failed",
                "selected scan points cannot be reshaped into equal samples per K1",
            )
        else:
            result = self._parabolic_plane_result(
                grouped_k1,
                grouped_sigma,
                m11,
                m12,
                plane,
            )
        result["fit_selection"] = fit_selection
        return result

    def leastSquare(self):
        k1l  = np.asarray(self.k1l, dtype=float)
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
            A0_x.append((A11, A12, A13))

            # Y-plane
            A11 = mat[2,2]**2
            A12 = 2*mat[2,2]*mat[2,3]
            A13 = mat[2,3]**2
            A0_y.append((A11, A12, A13))

        A0_x = np.asarray(A0_x, dtype=float)
        A0_y = np.asarray(A0_y, dtype=float)
        x_indices, x_selection = self._least_squares_selection(k1l, A0_x, "xplane")
        y_indices, y_selection = self._least_squares_selection(k1l, A0_y, "yplane")

        tmpx = self._solveMat(A0_x[x_indices], k1l[x_indices], sigxx[x_indices])
        tmpy = self._solveMat(A0_y[y_indices], k1l[y_indices], sigyy[y_indices])
        tmpx.fit_selection = x_selection
        tmpy.fit_selection = y_selection

        return tmpx,tmpy

    def _least_squares_selection(self, k1l, design, plane):
        all_indices = np.arange(len(k1l), dtype=int)
        if self.scan_strategy not in ADAPTIVE_SCAN_STRATEGIES:
            return all_indices, scanThread._fit_selection_payload(
                k1l,
                all_indices,
                status="all_points",
                requested_range=None,
            )

        metadata = self.scan_metadata if isinstance(self.scan_metadata, Mapping) else {}
        adaptive_result = metadata.get("adaptive_result")
        range_key = "x_range" if plane == "xplane" else "y_range"
        raw_range = adaptive_result.get(range_key) if isinstance(adaptive_result, Mapping) else None
        if not isinstance(raw_range, (list, tuple)) or len(raw_range) != 2:
            if self.scan_strategy == "adaptive_quality":
                return np.asarray([], dtype=int), scanThread._fit_selection_payload(
                    k1l,
                    np.asarray([], dtype=int),
                    status="missing_window",
                    requested_range=None,
                )
            return all_indices, scanThread._fit_selection_payload(
                k1l,
                all_indices,
                status="missing_window_all_points",
                requested_range=None,
            )

        lower, upper = sorted(float(value) for value in raw_range)
        tolerance = 0.0
        adaptive_metadata = metadata.get("adaptive")
        if isinstance(adaptive_metadata, Mapping):
            tolerance = max(0.0, float(adaptive_metadata.get("reuse_tolerance", 0.0)))
        quality_mask = np.ones(len(k1l), dtype=bool)
        if self.scan_strategy == "adaptive_quality":
            quality_values = (
                getattr(self, "x_quality_usable", ())
                if plane == "xplane"
                else getattr(self, "y_quality_usable", ())
            )
            if len(quality_values) != len(k1l):
                quality_mask[:] = False
            else:
                quality_mask = np.asarray(quality_values, dtype=bool)
        selected = set(
            np.flatnonzero(
                (k1l >= lower - tolerance)
                & (k1l <= upper + tolerance)
                & quality_mask
            ).tolist()
        )

        outside_groups = []
        for value in sorted(set(float(item) for item in k1l)):
            group = np.flatnonzero(
                np.isclose(k1l, value, rtol=0.0, atol=tolerance) & quality_mask
            ).tolist()
            if not group:
                continue
            if any(index in selected for index in group):
                continue
            distance = lower - value if value < lower else value - upper
            outside_groups.append((max(0.0, distance), value, group))
        outside_groups.sort(key=lambda item: (item[0], item[1]))

        expanded = False
        while not scanThread._fit_design_is_usable(design, selected):
            if not outside_groups:
                break
            _distance, _value, group = outside_groups.pop(0)
            selected.update(group)
            expanded = True

        indices = np.asarray(sorted(selected), dtype=int)
        usable = scanThread._fit_design_is_usable(design, selected)
        if usable:
            status = "expanded_window" if expanded else "window"
        else:
            status = "insufficient_window"
        return indices, scanThread._fit_selection_payload(
            k1l,
            indices,
            status=status,
            requested_range=(lower, upper),
        )

    @staticmethod
    def _fit_design_is_usable(design, selected):
        if len(selected) < LEAST_SQUARES_REQUIRED_RANK:
            return False
        rows = np.asarray(design, dtype=float)[sorted(selected)]
        if np.linalg.matrix_rank(rows) < LEAST_SQUARES_REQUIRED_RANK:
            return False
        singular_values = np.linalg.svd(rows, compute_uv=False)
        if not singular_values.size or singular_values[-1] <= 0:
            return False
        return float(singular_values[0] / singular_values[-1]) <= LEAST_SQUARES_MAX_CONDITION

    @staticmethod
    def _fit_selection_payload(k1l, indices, *, status, requested_range):
        selected_values = np.asarray(k1l, dtype=float)[indices]
        unique_values = sorted(set(float(value) for value in selected_values))
        payload = {
            "status": status,
            "points_used": int(len(indices)),
            "points_total": int(len(k1l)),
            "unique_k1_used": len(unique_values),
            "selected_k1": unique_values,
        }
        if requested_range is not None:
            payload["requested_range"] = [float(requested_range[0]), float(requested_range[1])]
        if unique_values:
            payload["actual_range"] = [unique_values[0], unique_values[-1]]
        return payload

    def _solveMat(self,A0,k1l,sigxx):
        determinant = None
        rank = None
        condition_number = None
        residual_rms = None
        solver = "numpy.linalg.lstsq"
        try:
            A = np.asarray(A0, dtype=float).reshape(len(k1l), 3)
            b = np.asarray(sigxx, dtype=float).reshape(-1)
            if A.shape[0] != b.size:
                raise ValueError(
                    f"design matrix rows ({A.shape[0]}) do not match measurements ({b.size})"
                )
            if not np.all(np.isfinite(A)) or not np.all(np.isfinite(b)):
                raise ValueError("least-squares input contains non-finite values")

            xx, _reported_residuals, rank_value, singular_values = np.linalg.lstsq(
                A,
                b,
                rcond=None,
            )
            rank = int(rank_value)
            fitted = A @ xx
            residual_rms = (
                float(np.sqrt(np.mean((fitted - b) ** 2)))
                if b.size
                else None
            )
            if singular_values.size and singular_values[-1] > 0:
                condition_number = float(singular_values[0] / singular_values[-1])

            if rank < LEAST_SQUARES_REQUIRED_RANK:
                raise ValueError(
                    f"rank-deficient fit: rank={rank}/{LEAST_SQUARES_REQUIRED_RANK}; "
                    "scan points do not provide enough independent optics settings"
                )
            if condition_number is None or condition_number > LEAST_SQUARES_MAX_CONDITION:
                condition_text = "infinite" if condition_number is None else f"{condition_number:.3e}"
                raise ValueError(
                    f"ill-conditioned fit: condition={condition_text} exceeds "
                    f"{LEAST_SQUARES_MAX_CONDITION:.1e}"
                )

            sig11 = float(xx[0])
            sig12 = float(xx[1])
            sig22 = float(xx[2])

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
            if "non-physical" in str(exc):
                tmp.status = "non_physical"
            elif "rank-deficient" in str(exc):
                tmp.status = "rank_deficient"
            elif "ill-conditioned" in str(exc):
                tmp.status = "ill_conditioned"
            else:
                tmp.status = "failed"
            tmp.message = str(exc)
            tmp.determinant = determinant

        tmp.rank = rank
        tmp.condition_number = condition_number
        tmp.residual_rms = residual_rms
        tmp.solver = solver

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

    def getTwissProfile(self, quad1, quad2, twiss0, plane="xplane", inverse=False):
        return self.model_backend.get_twiss_profile(
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
