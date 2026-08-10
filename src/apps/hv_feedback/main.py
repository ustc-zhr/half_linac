from __future__ import annotations

import copy
import math
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any


_REPO_BOOTSTRAP_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file()
)
if str(_REPO_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_BOOTSTRAP_ROOT))

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

import matplotlib.dates as mdates
import matplotlib.ticker as mticker
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
from PyQt5.QtCore import QObject, QThread, QTimer, Qt, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from half_linac.src.apps.hv_feedback.data_buffer import DataBuffer, Sample
from half_linac.src.apps.hv_feedback.epics_client import BaseClient
from half_linac.src.apps.hv_feedback.logger import CSVLogger
from half_linac.src.apps.hv_feedback.profile_runtime import (
    apply_machine_hv_limit,
    amplitude_key,
    get_unit_config,
    load_profile_config,
    load_runtime_snapshot,
    new_run_dir,
    phase_key,
    require_confirmed_feedback_write,
    require_feedback_write_policy,
    required_signal_keys,
    resolve_hv_feedback_runtime_paths,
    save_runtime_snapshot,
    validate_session_config,
    write_run_metadata,
)
from half_linac.src.apps.hv_feedback.reference import auto_reference
from half_linac.src.apps.hv_feedback.runtime import FeedbackEngine, create_client
from half_linac.src.apps.hv_feedback.utils import phase_diff_deg
from half_linac.src.shared.app_theme import resolve_initial_theme
from half_linac.src.shared.machine_profile import (
    AppContext,
    MachineProfileError,
    RuntimeContextWidget,
    load_app_context,
)
from half_linac.src.shared.window_activation import install_qt_window_raise_handler


HEADER_ACTION_HEIGHT = 32
MAX_PLOT_SAMPLES = 200_000
PLOT_REDRAW_INTERVAL_MS = 250

CONTROL_FIELDS = (
    ("sample_period_s", "Sample period (s)", 0.1, 3600.0, 0.1, 2),
    ("update_period_s", "Feedback interval (s)", 0.1, 3600.0, 1.0, 2),
    ("average_window_s", "Average window (s)", 0.1, 3600.0, 1.0, 2),
    ("reference_samples", "Samples", 3, 100000, 1, None),
    (
        "reference_sample_interval_s",
        "Sample interval (s)",
        0.01,
        3600.0,
        0.1,
        2,
    ),
    ("gain_kv_per_relerr", "Integral gain (kV/rel)", 0.000001, 100.0, 0.1, 6),
    ("max_step_kv", "Maximum step (kV)", 0.000001, 10.0, 0.001, 6),
    ("total_limit_kv", "Total offset limit (kV)", 0.000001, 20.0, 0.01, 6),
)

PRIMARY_CONTROL_KEYS = (
    "update_period_s",
    "gain_kv_per_relerr",
    "max_step_kv",
    "total_limit_kv",
)

ADVANCED_CONTROL_KEYS = (
    "sample_period_s",
    "average_window_s",
)

REFERENCE_MEASUREMENT_KEYS = (
    "reference_samples",
    "reference_sample_interval_s",
)

BASE_SAFETY_FIELDS = (
    ("hv_min_kv", "Minimum HV (kV)", 0.0, 100.0, 0.01, 4),
    ("hv_max_kv", "Maximum HV (kV)", 0.0, 100.0, 0.01, 4),
    ("hv_readback_tolerance_kv", "HV mismatch limit (kV)", 0.000001, 10.0, 0.001, 6),
    ("amplitude_ratio_limit_rel", "Ratio relative limit", 0.000001, 1.0, 0.001, 6),
    ("feedback_amplitude_min_rel", "Feedback minimum relative", 0.000001, 10.0, 0.01, 6),
    ("feedback_amplitude_max_rel", "Feedback maximum relative", 0.000001, 10.0, 0.01, 6),
)

PLOT_COLORS = ("#45d0bc", "#e4b86f", "#79a9f5", "#d68ae8", "#ef8f6b")


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
    "status_strip_bg": "#131c22",
    "status_strip_border": "#2a3943",
    "status_separator": "#31424d",
    "status_item_idle_bar": "#4f6270",
    "status_title_fg": "#8ea0ad",
    "metric_active_fg": "#45d0bc",
    "metric_warning_fg": "#e4b86f",
    "metric_danger_fg": "#e37878",
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
    "status_strip_bg": "#f7f1e8",
    "status_strip_border": "#ddd2c4",
    "status_separator": "#ddd4c7",
    "status_item_idle_bar": "#c8bfb3",
    "status_title_fg": "#7c7368",
    "metric_active_fg": "#2d7f6d",
    "metric_warning_fg": "#a97118",
    "metric_danger_fg": "#b64b4b",
    "metric_idle_fg": "#4e5a62",
}


def build_hv_feedback_theme(palette: dict[str, str]) -> str:
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

QFrame#plotCard, QFrame#controlCard, QFrame#actionBar {{
    background-color: {panel_bg};
    border: 1px solid {panel_border};
    border-radius: 14px;
}}

QFrame#metricCard {{
    background-color: {plot_card_bg};
    border: 1px solid {panel_border};
    border-radius: 10px;
}}

QLabel {{
    color: {window_fg};
    background: transparent;
    border: none;
    font-size: 12px;
    font-weight: 600;
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

QLabel[role="field"], QLabel[role="metricName"] {{
    color: {muted_fg};
    font-size: 11px;
    font-weight: 600;
}}

QLabel[role="summaryValue"] {{
    color: {summary_title_fg};
    font-family: "IBM Plex Mono", "Roboto Mono", monospace;
    font-size: 12px;
    font-weight: 700;
}}

QLabel[role="metricValue"] {{
    color: {metric_idle_fg};
    font-family: "IBM Plex Mono", "Roboto Mono", monospace;
    font-size: 15px;
    font-weight: 700;
}}

QLabel[role="metricValue"][tone="warning"],
QLabel[role="sampleFreshness"][tone="warning"] {{
    color: {metric_warning_fg};
}}

QLabel[role="metricValue"][tone="danger"],
QLabel[role="sampleFreshness"][tone="danger"] {{
    color: {metric_danger_fg};
}}

QLabel[role="sampleFreshness"] {{
    color: {muted_fg};
    font-size: 10px;
    font-weight: 600;
}}

QLabel#messageLabel {{
    color: {muted_fg};
    font-size: 11px;
    font-weight: 600;
    padding: 1px 8px 0px 8px;
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

QPushButton:hover {{ background-color: {button_hover_bg}; }}
QPushButton:pressed {{ background-color: {button_pressed_bg}; }}
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

QPushButton#startMonitorButton {{
    border-color: {metric_active_fg};
    color: {metric_active_fg};
}}

QPushButton#startFeedbackButton {{
    border-color: {metric_danger_fg};
    color: {metric_danger_fg};
}}

QPushButton#stopButton {{
    border-color: {metric_warning_fg};
    color: {metric_warning_fg};
}}

QPushButton#startMonitorButton:disabled,
QPushButton#startFeedbackButton:disabled,
QPushButton#stopButton:disabled {{
    color: {button_disabled_fg};
    border-color: {button_disabled_border};
    background-color: {button_disabled_bg};
}}

QComboBox, QSpinBox, QDoubleSpinBox {{
    background-color: {input_bg};
    border: 1px solid {input_border};
    border-radius: 10px;
    color: {input_fg};
    padding: 5px 10px;
    min-height: 18px;
    selection-background-color: {metric_active_fg};
}}

QComboBox {{
    padding: 4px 8px;
    min-height: 20px;
    font-size: 11px;
    font-weight: 600;
}}

QSpinBox[numeric="true"], QDoubleSpinBox[numeric="true"] {{
    font-family: "IBM Plex Mono", "Roboto Mono", monospace;
    font-size: 11px;
    font-weight: 600;
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

QToolButton#themeToggleButton:hover {{ background-color: {button_hover_bg}; }}
QToolButton#themeToggleButton:pressed {{ background-color: {button_pressed_bg}; }}

QToolBar#plotToolbar {{
    background: transparent;
    border: none;
    spacing: 2px;
}}

QToolBar#plotToolbar QToolButton {{
    background: {button_bg};
    border: 1px solid {button_border};
    border-radius: 6px;
    color: {button_fg};
    padding: 2px;
    min-width: 24px;
    max-width: 24px;
    min-height: 24px;
    max-height: 24px;
}}

QToolBar#plotToolbar QToolButton:hover {{ background: {button_hover_bg}; }}
QToolBar#plotToolbar QToolButton:checked {{ border-color: {metric_active_fg}; }}

QScrollArea#configurationScroll, QWidget#configurationPanel {{
    background: {window_bg};
    border: none;
}}

QSplitter::handle {{
    background: {panel_border};
    width: 2px;
}}
""".format_map(theme_values)


def build_status_strip_theme(palette: dict[str, str]) -> str:
    theme_values = dict(
        palette,
        status_tone_success_bar=palette["metric_active_fg"],
        status_tone_warning_bar=palette["metric_warning_fg"],
        status_tone_danger_bar=palette["metric_danger_fg"],
        status_tone_success_fg=palette["metric_active_fg"],
        status_tone_warning_fg=palette["metric_warning_fg"],
        status_tone_danger_fg=palette["metric_danger_fg"],
        status_tone_subtle_fg=palette["metric_idle_fg"],
    )
    return """
QFrame#statusStrip {{
    background: transparent;
    border: none;
    border-radius: 0px;
}}
QFrame#statusItem {{
    background: transparent;
    border: none;
    border-left: 4px solid {status_item_idle_bar};
    border-radius: 0px;
}}
QFrame#statusItem[tone="success"] {{ border-left-color: {status_tone_success_bar}; }}
QFrame#statusItem[tone="warning"] {{ border-left-color: {status_tone_warning_bar}; }}
QFrame#statusItem[tone="danger"] {{ border-left-color: {status_tone_danger_bar}; }}
QFrame#statusSeparator {{
    background: {status_separator};
    min-width: 1px;
    max-width: 1px;
    border: none;
}}
QLabel[role="statusTitle"] {{
    color: {status_title_fg};
    background: transparent;
    border: none;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.8px;
}}
QLabel[role="statusValue"] {{
    color: {status_tone_subtle_fg};
    background: transparent;
    border: none;
    font-size: 13px;
    font-weight: 700;
}}
QLabel[role="statusValue"][tone="success"] {{ color: {status_tone_success_fg}; }}
QLabel[role="statusValue"][tone="warning"] {{ color: {status_tone_warning_fg}; }}
QLabel[role="statusValue"][tone="danger"] {{ color: {status_tone_danger_fg}; }}
""".format_map(theme_values)


class CompactDoubleSpinBox(QDoubleSpinBox):
    """Preserve configured precision while suppressing insignificant trailing zeros."""

    def textFromValue(self, value: float) -> str:  # noqa: N802
        text = f"{value:.{self.decimals()}f}".rstrip("0").rstrip(".")
        return "0" if text in {"", "-0"} else text


class CompactNavigationToolbar(NavigationToolbar):
    """Keep the standard Matplotlib navigation actions without the bulky extras."""

    navigation_mode_changed = pyqtSignal(bool)

    toolitems = (
        ("Home", "Reset original view", "home", "home"),
        ("Back", "Back to previous view", "back", "back"),
        ("Forward", "Forward to next view", "forward", "forward"),
        (None, None, None, None),
        ("Pan", "Pan axes; live redraw pauses while active", "move", "pan"),
        (
            "Zoom",
            "Zoom to rectangle; live redraw pauses while active",
            "zoom_to_rect",
            "zoom",
        ),
        (None, None, None, None),
        ("Save", "Save the figure", "filesave", "save_figure"),
    )

    def refresh_icons(self) -> None:
        """Rebuild icons after the parent switches between dark and light themes."""
        for _text, _tooltip, image_file, callback in self.toolitems:
            if image_file is None or callback is None:
                continue
            action = self._actions.get(callback)
            if action is not None:
                action.setIcon(self._icon(f"{image_file}.png"))

    def pan(self, *args: Any) -> None:
        super().pan(*args)
        self.navigation_mode_changed.emit(bool(self.mode))

    def zoom(self, *args: Any) -> None:
        super().zoom(*args)
        self.navigation_mode_changed.emit(bool(self.mode))


class StatusStrip(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("statusStrip")
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(8, 4, 8, 4)
        self._layout.setSpacing(0)
        self._items: dict[str, tuple[QFrame, QLabel]] = {}

    def add_item(self, key: str, title: str, value: str) -> None:
        if self._items:
            separator = QFrame(self)
            separator.setObjectName("statusSeparator")
            separator.setFrameShape(QFrame.VLine)
            separator.setFrameShadow(QFrame.Plain)
            self._layout.addWidget(separator)

        box = QFrame(self)
        box.setObjectName("statusItem")
        box.setProperty("tone", "subtle")
        box.setMinimumWidth(118)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 0, 6, 0)
        layout.setSpacing(2)
        title_label = QLabel(title, box)
        title_label.setProperty("role", "statusTitle")
        value_label = QLabel(value, box)
        value_label.setProperty("role", "statusValue")
        value_label.setProperty("tone", "subtle")
        value_label.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(value_label)
        self._layout.addWidget(box)
        self._items[key] = (box, value_label)

    def finish(self) -> None:
        self._layout.addStretch(1)

    def apply_theme(self, palette: dict[str, str]) -> None:
        self.setStyleSheet(build_status_strip_theme(palette))
        for container, label in self._items.values():
            self._refresh_tone(container, label)

    def set_value(self, key: str, value: str, tone: str = "subtle") -> None:
        container, label = self._items[key]
        label.setText(value)
        container.setProperty("tone", tone)
        label.setProperty("tone", tone)
        self._refresh_tone(container, label)

    @staticmethod
    def _refresh_tone(container: QFrame, label: QLabel) -> None:
        container.style().unpolish(container)
        container.style().polish(container)
        label.style().unpolish(label)
        label.style().polish(label)
        container.update()
        label.update()


class GeneralizedFeedbackWorker(QObject):
    rows_ready = pyqtSignal(list)
    status_ready = pyqtSignal(str, str)
    log_ready = pyqtSignal(str)
    failed = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(
        self,
        context: AppContext,
        config: dict[str, Any],
        feedback_channel_id: str,
        operation: str,
        *,
        session_confirmed: bool,
    ) -> None:
        super().__init__()
        self.context = context
        self.config = copy.deepcopy(config)
        self.feedback_channel_id = feedback_channel_id
        self.operation = operation
        self.session_confirmed = session_confirmed
        self._stop_event = threading.Event()

    @pyqtSlot()
    def run(self) -> None:
        run_dir: Path | None = None
        logger: CSVLogger | None = None
        engine: FeedbackEngine | None = None
        detail = ""
        final_state = "STOPPED"
        try:
            unit_id = str(self.config["feedback_unit_id"])
            run_dir = new_run_dir(self.context, self.operation, unit_id)
            logging_config = self.config["logging"]
            logger = CSVLogger(
                run_dir,
                str(logging_config["file_prefix"]),
                self.config,
                int(logging_config["flush_every_n_rows"]),
            )
            write_run_metadata(
                self.context,
                run_dir,
                operation=self.operation,
                config=self.config,
                feedback_channel_id=self.feedback_channel_id,
                state="CONNECTING",
                log_path=logger.path,
            )
            self.log_ready.emit(str(logger.path))
            authorizer = None
            if self.operation == "feedback":
                target_pv = str(self.config["pvs"]["hv_setpoint"]["name"])
                authorizer = lambda: require_confirmed_feedback_write(
                    self.context,
                    session_confirmed=self.session_confirmed,
                    feedback_unit_id=unit_id,
                    target_pv=target_pv,
                )
            engine = FeedbackEngine(
                self.config,
                mode=self.operation,
                feedback_channel_id=self.feedback_channel_id,
                write_authorizer=authorizer,
            )
            active_state = (
                "FEEDBACK ACTIVE" if self.operation == "feedback" else "MONITORING"
            )
            tone = "danger" if self.operation == "feedback" else "success"
            self.status_ready.emit(active_state, tone)
            while not self._stop_event.is_set():
                rows = engine.step()
                for row in rows:
                    logger.write(row)
                self.rows_ready.emit(rows)
                holds = [row for row in rows if row.get("event") == "HOLD"]
                if holds:
                    final_state = "HOLD"
                    detail = str(holds[-1].get("reason", "safety hold"))
                    self.status_ready.emit("HOLD", "warning")
                if self._stop_event.wait(engine.sample_period_s):
                    break
            stop_row = engine.stop_row()
            logger.write(stop_row)
            self.rows_ready.emit([stop_row])
        except Exception as exc:  # noqa: BLE001
            final_state = "ERROR"
            detail = str(exc)
            self.failed.emit(detail)
        finally:
            if logger is not None:
                logger.close()
            if run_dir is not None:
                try:
                    write_run_metadata(
                        self.context,
                        run_dir,
                        operation=self.operation,
                        config=self.config,
                        feedback_channel_id=self.feedback_channel_id,
                        state=final_state,
                        log_path=logger.path if logger is not None else None,
                        detail=detail,
                    )
                except Exception as exc:  # noqa: BLE001
                    if not detail:
                        self.failed.emit(f"Could not write run metadata: {exc}")
            self.finished.emit()

    def stop(self) -> None:
        self._stop_event.set()


class GeneralizedReferenceWorker(QObject):
    measured = pyqtSignal(dict)
    status_ready = pyqtSignal(str)
    failed = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__()
        self.config = copy.deepcopy(config)
        self._stop_event = threading.Event()

    @pyqtSlot()
    def run(self) -> None:
        try:
            sample_count = int(self.config["control"]["reference_samples"])
            sample_interval = float(
                self.config["control"]["reference_sample_interval_s"]
            )
            client = create_client(self.config)
            keys = required_signal_keys(self.config)
            buffer = DataBuffer(max_age_s=None)
            started = time.monotonic()
            for index in range(sample_count):
                target_time = started + index * sample_interval
                if self._stop_event.wait(max(0.0, target_time - time.monotonic())):
                    return
                values = client.read_many(keys)
                errors = {
                    key: value.error
                    for key, value in values.items()
                    if not value.ok or value.value is None
                }
                if errors:
                    raise RuntimeError(
                        f"PV read invalid during reference measurement: {errors}"
                    )
                buffer.append(
                    Sample(
                        timestamp=time.time(),
                        values={key: value.value for key, value in values.items()},
                        ok=True,
                        errors={},
                    )
                )
                self.status_ready.emit(
                    f"Measuring reference: {index + 1}/{sample_count} samples"
                )
            if self._stop_event.is_set():
                return
            result = auto_reference(buffer, self.config)
            if result.reference is None:
                raise RuntimeError(result.reason or "Reference measurement failed.")
            self.measured.emit(
                {
                    "hv_kv": result.reference.hv_kv,
                    "channels": {
                        channel_id: {
                            "amplitude": result.reference.channel_amplitudes[channel_id],
                            "phase_deg": result.reference.channel_phases[channel_id],
                        }
                        for channel_id in result.reference.channel_amplitudes
                    },
                }
            )
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()

    def stop(self) -> None:
        self._stop_event.set()


class HVFeedbackWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        install_qt_window_raise_handler(self)
        self.app_context = load_app_context("hv_feedback")
        self.machine_profile = self.app_context.profile
        self.profile_config = load_profile_config(self.app_context)
        self.base_configs = {
            unit_id: get_unit_config(self.profile_config, unit_id)
            for unit_id in self.profile_config["unit_order"]
        }
        self.unit_configs = copy.deepcopy(self.base_configs)
        self.active_unit_id = str(self.profile_config["unit_order"][0])
        self.base_config = copy.deepcopy(self.base_configs[self.active_unit_id])
        self.config = copy.deepcopy(self.unit_configs[self.active_unit_id])
        self.selected_feedback_channels = {
            unit_id: str(config["default_feedback_channel"])
            for unit_id, config in self.unit_configs.items()
        }
        self.feedback_channel_id = self.selected_feedback_channels[self.active_unit_id]
        self.current_theme = resolve_initial_theme()

        self.session_thread: QThread | None = None
        self.session_worker: GeneralizedFeedbackWorker | None = None
        self.reference_thread: QThread | None = None
        self.reference_worker: GeneralizedReferenceWorker | None = None
        self._operation = "stopped"
        self._selector_guard = False
        self._history_t0: float | None = None
        self._last_sample_timestamp: float | None = None
        self._last_sample_valid: bool | None = None
        self._plot_redraw_pending = False
        self._last_plot_draw_monotonic = 0.0
        self._signal_history: dict[str, list[float]] = {}
        self._hv_command_history = {"time": [], "hv_next": []}
        self._parameter_spins: dict[str, dict[str, QDoubleSpinBox | QSpinBox]] = {
            "control": {},
            "reference": {},
            "safety": {},
        }
        self._reference_summary_labels: dict[str, QLabel] = {}
        self._safety_summary_labels: dict[str, QLabel] = {}
        self._parameter_edit_buttons: list[QPushButton] = []
        self._value_labels: dict[str, QLabel] = {}
        self._rf_value_labels: dict[str, dict[str, QLabel]] = {}

        self._build_ui()
        self._configure_active_unit(rebuild_dialogs=True)
        self._apply_theme()
        self._set_idle_state()
        self.sample_age_timer = QTimer(self)
        self.sample_age_timer.setInterval(500)
        self.sample_age_timer.timeout.connect(self._update_sample_freshness)
        self.sample_age_timer.start()

    def _build_ui(self) -> None:
        self.setWindowTitle("HV Feedback")
        self.resize(1500, 940)
        self.setMinimumSize(1180, 760)
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        header = QFrame(central)
        header.setObjectName("summaryPanel")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(12, 10, 12, 10)
        header_layout.setSpacing(7)
        title_row = QHBoxLayout()
        title = QLabel("HV Feedback", header)
        title.setObjectName("summaryTitle")
        title_row.addWidget(title)
        title_row.addStretch(1)
        title_row.addWidget(
            RuntimeContextWidget(
                machine_id=self.machine_profile.machine.id,
                machine_display_name=self.machine_profile.machine.display_name,
                control_backend=self.app_context.control_backend.name,
                parent=header,
            )
        )
        self.theme_toggle_button = QToolButton(header)
        self.theme_toggle_button.setObjectName("themeToggleButton")
        self.theme_toggle_button.setFixedSize(HEADER_ACTION_HEIGHT, HEADER_ACTION_HEIGHT)
        self.theme_toggle_button.clicked.connect(self._toggle_theme)
        title_row.addWidget(self.theme_toggle_button)
        header_layout.addLayout(title_row)
        self.status_panel = StatusStrip(header)
        self.status_panel.add_item("operation", "Operation", "None")
        self.status_panel.add_item("state", "State", "STOPPED")
        self.status_panel.add_item("write", "HV Write", "NOT ARMED")
        self.status_panel.add_item("log", "Log", "—")
        self.status_panel.finish()
        header_layout.addWidget(self.status_panel)
        self.message_label = QLabel("", header)
        self.message_label.setObjectName("messageLabel")
        self.message_label.setWordWrap(True)
        self.message_label.hide()
        header_layout.addWidget(self.message_label)
        root.addWidget(header)

        actions = QFrame(central)
        actions.setObjectName("actionBar")
        action_layout = QHBoxLayout(actions)
        action_layout.setContentsMargins(10, 8, 10, 8)
        action_layout.setSpacing(8)
        self.start_monitor_button = QPushButton("Start Monitor", actions)
        self.start_monitor_button.setObjectName("startMonitorButton")
        self.start_feedback_button = QPushButton("Start Feedback", actions)
        self.start_feedback_button.setObjectName("startFeedbackButton")
        self.measure_reference_button = QPushButton("Measure Reference", actions)
        self.stop_button = QPushButton("Stop", actions)
        self.stop_button.setObjectName("stopButton")
        self.load_snapshot_button = QPushButton("Load Snapshot", actions)
        self.save_snapshot_button = QPushButton("Save Snapshot", actions)
        for button in (
            self.start_monitor_button,
            self.start_feedback_button,
            self.measure_reference_button,
            self.stop_button,
        ):
            action_layout.addWidget(button)
        action_layout.addStretch(1)
        action_layout.addWidget(self.load_snapshot_button)
        action_layout.addWidget(self.save_snapshot_button)
        root.addWidget(actions)

        splitter = QSplitter(Qt.Horizontal, central)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_monitor_panel(splitter))
        splitter.addWidget(self._build_configuration_panel(splitter))
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([900, 550])
        root.addWidget(splitter, 1)

        self._build_static_parameter_dialogs()
        self.start_monitor_button.clicked.connect(self.start_monitor)
        self.start_feedback_button.clicked.connect(self.start_feedback)
        self.measure_reference_button.clicked.connect(self.measure_reference)
        self.stop_button.clicked.connect(self.stop_operation)
        self.load_snapshot_button.clicked.connect(self.load_snapshot)
        self.save_snapshot_button.clicked.connect(self.save_snapshot)
        self.feedback_unit_combo.currentIndexChanged.connect(self._unit_changed)
        self.feedback_channel_combo.currentIndexChanged.connect(
            self._feedback_channel_changed
        )
        self.edit_reference_button.clicked.connect(self._edit_reference)
        self.edit_safety_button.clicked.connect(self._edit_safety)

    def _build_monitor_panel(self, parent: QWidget) -> QWidget:
        panel = QWidget(parent)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(10)
        values_card = QFrame(panel)
        values_card.setObjectName("controlCard")
        values_layout = QVBoxLayout(values_card)
        values_layout.setContentsMargins(12, 11, 12, 12)
        values_layout.setSpacing(9)
        header = QHBoxLayout()
        title = QLabel("Latest Sample", values_card)
        title.setObjectName("panelTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.sample_freshness_label = QLabel("Waiting for acquisition", values_card)
        self.sample_freshness_label.setProperty("role", "sampleFreshness")
        self.sample_freshness_label.setProperty("tone", "subtle")
        header.addWidget(self.sample_freshness_label)
        values_layout.addLayout(header)
        hv_layout = QGridLayout()
        for column, (key, label, placeholder) in enumerate(
            (
                ("hv_setpoint", "HV setpoint", "— kV"),
                ("hv_readback", "HV readback", "— kV"),
                ("hv_mismatch", "HV mismatch", "— kV"),
            )
        ):
            card, value = self._metric_card(values_card, label, placeholder)
            hv_layout.addWidget(card, 0, column)
            self._value_labels[key] = value
        values_layout.addLayout(hv_layout)
        self.rf_metrics_widget = QWidget(values_card)
        self.rf_metrics_layout = QVBoxLayout(self.rf_metrics_widget)
        self.rf_metrics_layout.setContentsMargins(0, 0, 0, 0)
        self.rf_metrics_layout.setSpacing(6)
        values_layout.addWidget(self.rf_metrics_widget)
        layout.addWidget(values_card)

        plot_card = QFrame(panel)
        plot_card.setObjectName("plotCard")
        plot_layout = QVBoxLayout(plot_card)
        plot_layout.setContentsMargins(12, 11, 12, 12)
        plot_layout.setSpacing(6)
        plot_header = QHBoxLayout()
        plot_title = QLabel("Feedback Trends", plot_card)
        plot_title.setObjectName("panelTitle")
        plot_header.addWidget(plot_title)
        plot_header.addStretch(1)
        for label_text, attr, items in (
            ("Scale", "plot_scale_combo", (("Relative", None), ("Raw", None))),
            (
                "View",
                "plot_window_combo",
                (("Recent 15 min", 900), ("Recent 30 min", 1800), ("Recent 60 min", 3600), ("All", None)),
            ),
            ("Time", "plot_time_axis_combo", (("Elapsed", None), ("Clock", None))),
        ):
            label = QLabel(label_text, plot_card)
            label.setProperty("role", "field")
            plot_header.addWidget(label)
            combo = QComboBox(plot_card)
            for text, data in items:
                combo.addItem(text, data)
            setattr(self, attr, combo)
            combo.currentIndexChanged.connect(self._plot_control_changed)
            plot_header.addWidget(combo)
        plot_layout.addLayout(plot_header)
        self.figure = Figure(figsize=(8.0, 6.0), tight_layout=True)
        self.amp_axis = self.figure.add_subplot(311)
        self.phase_axis = self.figure.add_subplot(312)
        self.hv_axis = self.figure.add_subplot(313)
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        plot_layout.addWidget(self.canvas, 1)
        self.plot_toolbar = CompactNavigationToolbar(self.canvas, plot_card)
        self.plot_toolbar.setObjectName("plotToolbar")
        self.plot_toolbar.navigation_mode_changed.connect(self._plot_navigation_changed)
        plot_layout.addWidget(self.plot_toolbar)
        layout.addWidget(plot_card, 1)
        return panel

    @staticmethod
    def _metric_card(parent: QWidget, label: str, placeholder: str) -> tuple[QFrame, QLabel]:
        card = QFrame(parent)
        card.setObjectName("metricCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(2)
        name = QLabel(label, card)
        name.setProperty("role", "metricName")
        value = QLabel(placeholder, card)
        value.setProperty("role", "metricValue")
        value.setProperty("tone", "subtle")
        layout.addWidget(name)
        layout.addWidget(value)
        return card, value

    def _build_configuration_panel(self, parent: QWidget) -> QWidget:
        scroll = QScrollArea(parent)
        scroll.setObjectName("configurationScroll")
        scroll.setWidgetResizable(True)
        panel = QWidget(scroll)
        panel.setObjectName("configurationPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 0, 0, 0)
        layout.setSpacing(10)

        context_card = QFrame(panel)
        context_card.setObjectName("controlCard")
        context_layout = QVBoxLayout(context_card)
        context_layout.setContentsMargins(12, 11, 12, 12)
        context_title = QLabel("Feedback Context", context_card)
        context_title.setObjectName("panelTitle")
        context_layout.addWidget(context_title)
        form = QFormLayout()
        self.feedback_unit_combo = QComboBox(context_card)
        for unit_id in self.profile_config["unit_order"]:
            config = self.unit_configs[str(unit_id)]
            self.feedback_unit_combo.addItem(str(config["feedback_unit_label"]), unit_id)
        self.feedback_channel_combo = QComboBox(context_card)
        form.addRow("Feedback Unit", self.feedback_unit_combo)
        form.addRow("Feedback Channel", self.feedback_channel_combo)
        context_layout.addLayout(form)
        layout.addWidget(context_card)

        feedback_card = QFrame(panel)
        feedback_card.setObjectName("controlCard")
        feedback_layout = QVBoxLayout(feedback_card)
        feedback_layout.setContentsMargins(12, 11, 12, 12)
        feedback_title = QLabel("Feedback Settings", feedback_card)
        feedback_title.setObjectName("panelTitle")
        feedback_layout.addWidget(feedback_title)
        feedback_layout.addLayout(
            self._build_parameter_form(
                "control",
                self._select_specs(CONTROL_FIELDS, PRIMARY_CONTROL_KEYS),
                feedback_card,
            )
        )
        self.advanced_settings_button = QPushButton("Advanced Settings…", feedback_card)
        self.advanced_settings_button.setProperty("compact", True)
        feedback_layout.addWidget(self.advanced_settings_button)
        self.timing_summary_label = QLabel("", feedback_card)
        self.timing_summary_label.setProperty("role", "field")
        feedback_layout.addWidget(self.timing_summary_label)
        layout.addWidget(feedback_card)

        reference_card, self.edit_reference_button = self._summary_card(
            "Reference Target",
            (("feedback", "Feedback channel"), ("channels", "RF references"), ("hv", "Reference HV")),
            self._reference_summary_labels,
            panel,
        )
        layout.addWidget(reference_card)
        safety_card, self.edit_safety_button = self._summary_card(
            "Safety Limits",
            (("hv", "HV range"), ("mismatch", "Readback mismatch"), ("phase", "Phase deviation"), ("amplitude", "Amplitude deviation")),
            self._safety_summary_labels,
            panel,
        )
        layout.addWidget(safety_card)
        self._parameter_edit_buttons.extend(
            (self.advanced_settings_button, self.edit_reference_button, self.edit_safety_button)
        )
        layout.addStretch(1)
        scroll.setWidget(panel)
        return scroll

    @staticmethod
    def _summary_card(
        title: str,
        rows: tuple[tuple[str, str], ...],
        labels: dict[str, QLabel],
        parent: QWidget,
    ) -> tuple[QFrame, QPushButton]:
        card = QFrame(parent)
        card.setObjectName("controlCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 11, 12, 12)
        layout.setSpacing(8)
        header = QHBoxLayout()
        title_label = QLabel(title, card)
        title_label.setObjectName("panelTitle")
        header.addWidget(title_label)
        header.addStretch(1)
        button = QPushButton("Edit…", card)
        button.setProperty("compact", True)
        header.addWidget(button)
        layout.addLayout(header)
        grid = QGridLayout()
        for row, (key, text) in enumerate(rows):
            field = QLabel(text, card)
            field.setProperty("role", "field")
            value = QLabel("—", card)
            value.setProperty("role", "summaryValue")
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            value.setWordWrap(True)
            grid.addWidget(field, row, 0)
            grid.addWidget(value, row, 1)
            labels[key] = value
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        return card, button

    @staticmethod
    def _select_specs(specs, keys):
        by_key = {spec[0]: spec for spec in specs}
        return tuple(by_key[key] for key in keys)

    def _build_parameter_form(self, section: str, specs, parent: QWidget) -> QFormLayout:
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)
        for spec in specs:
            field = QLabel(spec[1], parent)
            field.setProperty("role", "field")
            form.addRow(field, self._create_parameter_spin(section, spec, parent))
        return form

    def _create_parameter_spin(self, section: str, spec, parent: QWidget):
        key, _label, minimum, maximum, step, decimals = spec
        if decimals is None:
            spin: QDoubleSpinBox | QSpinBox = QSpinBox(parent)
            spin.setRange(int(minimum), int(maximum))
            spin.setSingleStep(int(step))
        else:
            spin = CompactDoubleSpinBox(parent)
            spin.setRange(float(minimum), float(maximum))
            spin.setSingleStep(float(step))
            spin.setDecimals(int(decimals))
        spin.setKeyboardTracking(False)
        spin.setProperty("numeric", True)
        spin.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._parameter_spins[section][key] = spin
        return spin

    def _build_static_parameter_dialogs(self) -> None:
        self.advanced_settings_dialog = self._parameter_dialog(
            "Advanced Settings",
            "control",
            self._select_specs(CONTROL_FIELDS, ADVANCED_CONTROL_KEYS),
            "Apply",
        )
        self.reference_measurement_dialog = self._parameter_dialog(
            "Measure Reference",
            "control",
            self._select_specs(CONTROL_FIELDS, REFERENCE_MEASUREMENT_KEYS),
            "Start Measurement",
        )
        self.reference_measurement_estimate_label = QLabel(
            "", self.reference_measurement_dialog
        )
        self.reference_measurement_estimate_label.setProperty("role", "field")
        layout = self.reference_measurement_dialog.layout()
        assert isinstance(layout, QVBoxLayout)
        layout.insertWidget(layout.count() - 1, self.reference_measurement_estimate_label)
        self.advanced_settings_button.clicked.connect(
            lambda: self._open_parameter_dialog(
                self.advanced_settings_dialog, "control", ADVANCED_CONTROL_KEYS
            )
        )
        for spin in self._parameter_spins["control"].values():
            spin.valueChanged.connect(self._refresh_parameter_summaries)
        self._parameter_spins["control"]["reference_samples"].valueChanged.connect(
            self._update_reference_measurement_estimate
        )
        self._parameter_spins["control"]["reference_sample_interval_s"].valueChanged.connect(
            self._update_reference_measurement_estimate
        )
        self._update_reference_measurement_estimate()

    def _rebuild_dynamic_parameter_dialogs(self) -> None:
        for name in ("reference_dialog", "safety_dialog"):
            dialog = getattr(self, name, None)
            if dialog is not None:
                dialog.close()
                dialog.deleteLater()
        self._parameter_spins["reference"] = {}
        self._parameter_spins["safety"] = {}
        reference_specs = [
            ("hv_kv", "Reference HV (kV)", 0.0, 100.0, 0.001, 6)
        ]
        safety_specs = list(BASE_SAFETY_FIELDS)
        for channel in self.config["rf_channels"]:
            channel_id = str(channel["id"])
            label = str(channel["label"])
            reference_specs.extend(
                (
                    (f"channels/{channel_id}/amplitude", f"{label} amplitude", 0.000001, 1.0e9, 1.0, 6),
                    (f"channels/{channel_id}/phase_deg", f"{label} phase (deg)", -180.0, 180.0, 0.1, 4),
                )
            )
            safety_specs.append(
                (f"phase_limit_deg/{channel_id}", f"{label} phase limit (deg)", 0.000001, 180.0, 0.1, 4)
            )
        self.reference_dialog = self._parameter_dialog(
            "Edit Reference", "reference", tuple(reference_specs), "Apply"
        )
        self.safety_dialog = self._parameter_dialog(
            "Edit Safety Limits", "safety", tuple(safety_specs), "Apply"
        )
        for section in ("reference", "safety"):
            for spin in self._parameter_spins[section].values():
                spin.valueChanged.connect(self._refresh_parameter_summaries)

    def _parameter_dialog(self, title: str, section: str, specs, accept_text: str) -> QDialog:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setModal(True)
        dialog.setMinimumWidth(480)
        layout = QVBoxLayout(dialog)
        card = QFrame(dialog)
        card.setObjectName("controlCard")
        card_layout = QVBoxLayout(card)
        card_layout.addLayout(self._build_parameter_form(section, specs, card))
        layout.addWidget(card)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dialog)
        buttons.button(QDialogButtonBox.Ok).setText(accept_text)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        return dialog

    def _edit_reference(self) -> None:
        self._open_parameter_dialog(
            self.reference_dialog,
            "reference",
            tuple(self._parameter_spins["reference"]),
        )

    def _edit_safety(self) -> None:
        self._open_parameter_dialog(
            self.safety_dialog,
            "safety",
            tuple(self._parameter_spins["safety"]),
        )

    def _open_parameter_dialog(self, dialog: QDialog, section: str, keys) -> bool:
        fields = self._parameter_spins[section]
        original = {key: fields[key].value() for key in keys}
        while True:
            if dialog.exec_() != QDialog.Accepted:
                for key, value in original.items():
                    fields[key].setValue(value)
                self._refresh_parameter_summaries()
                return False
            config = self._validated_config_or_message()
            if config is not None:
                self.config = copy.deepcopy(config)
                self.unit_configs[self.active_unit_id] = copy.deepcopy(config)
                self._refresh_parameter_summaries()
                if section in {"reference", "safety"}:
                    self._reset_monitor_display()
                return True

    @staticmethod
    def _nested_value(mapping: dict[str, Any], key: str) -> Any:
        value: Any = mapping
        for part in key.split("/"):
            value = value[part]
        return value

    @staticmethod
    def _set_nested_value(mapping: dict[str, Any], key: str, value: Any) -> None:
        parts = key.split("/")
        target = mapping
        for part in parts[:-1]:
            target = target[part]
        target[parts[-1]] = value

    def _apply_config_to_ui(self, config: dict[str, Any]) -> None:
        for section, fields in self._parameter_spins.items():
            for key, spin in fields.items():
                value = self._nested_value(config[section], key)
                spin.setValue(int(value) if isinstance(spin, QSpinBox) else float(value))
        self._refresh_parameter_summaries()

    def _config_from_ui(self) -> dict[str, Any]:
        config = copy.deepcopy(self.config)
        for section, fields in self._parameter_spins.items():
            for key, spin in fields.items():
                value = int(spin.value()) if isinstance(spin, QSpinBox) else float(spin.value())
                self._set_nested_value(config[section], key, value)
        config = apply_machine_hv_limit(config)
        validate_session_config(config)
        return config

    def _validated_config_or_message(self) -> dict[str, Any] | None:
        try:
            return self._config_from_ui()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Invalid HV Feedback Parameters", str(exc))
            return None

    def _configure_active_unit(self, *, rebuild_dialogs: bool) -> None:
        self.config = copy.deepcopy(self.unit_configs[self.active_unit_id])
        self.base_config = copy.deepcopy(self.base_configs[self.active_unit_id])
        self.feedback_channel_id = self.selected_feedback_channels[self.active_unit_id]
        self._selector_guard = True
        self._populate_feedback_channel_combo()
        self._selector_guard = False
        if rebuild_dialogs:
            self._rebuild_dynamic_parameter_dialogs()
        self._apply_config_to_ui(self.config)
        self._rebuild_rf_metrics()
        self._reset_monitor_display()

    def _populate_feedback_channel_combo(self) -> None:
        self.feedback_channel_combo.clear()
        selected_index = 0
        for index, channel in enumerate(self.config["rf_channels"]):
            channel_id = str(channel["id"])
            self.feedback_channel_combo.addItem(str(channel["label"]), channel_id)
            if channel_id == self.feedback_channel_id:
                selected_index = index
        self.feedback_channel_combo.setCurrentIndex(selected_index)

    @pyqtSlot(int)
    def _unit_changed(self, index: int) -> None:
        if self._selector_guard or index < 0:
            return
        new_unit_id = str(self.feedback_unit_combo.itemData(index))
        if new_unit_id == self.active_unit_id:
            return
        current = self._validated_config_or_message()
        if current is None:
            self._selector_guard = True
            old_index = self.feedback_unit_combo.findData(self.active_unit_id)
            self.feedback_unit_combo.setCurrentIndex(old_index)
            self._selector_guard = False
            return
        self.unit_configs[self.active_unit_id] = copy.deepcopy(current)
        self.active_unit_id = new_unit_id
        self._configure_active_unit(rebuild_dialogs=True)

    @pyqtSlot(int)
    def _feedback_channel_changed(self, index: int) -> None:
        if self._selector_guard or index < 0:
            return
        channel_id = str(self.feedback_channel_combo.itemData(index))
        if channel_id == self.feedback_channel_id:
            return
        self.feedback_channel_id = channel_id
        self.selected_feedback_channels[self.active_unit_id] = channel_id
        self._refresh_parameter_summaries()
        self._rebuild_rf_metrics()
        self._reset_monitor_display()

    def _rebuild_rf_metrics(self) -> None:
        while self.rf_metrics_layout.count():
            item = self.rf_metrics_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._rf_value_labels = {}
        for channel in self.config["rf_channels"]:
            channel_id = str(channel["id"])
            label = str(channel["label"])
            row = QFrame(self.rf_metrics_widget)
            row.setObjectName("metricCard")
            grid = QGridLayout(row)
            grid.setContentsMargins(10, 6, 10, 6)
            role = "Feedback Channel" if channel_id == self.feedback_channel_id else "Monitored"
            name = QLabel(f"{label}  ·  {role}", row)
            name.setProperty("role", "metricName")
            amplitude = QLabel("—", row)
            amplitude.setProperty("role", "metricValue")
            amplitude.setProperty("tone", "subtle")
            phase = QLabel("— deg", row)
            phase.setProperty("role", "metricValue")
            phase.setProperty("tone", "subtle")
            grid.addWidget(name, 0, 0, 1, 2)
            grid.addWidget(amplitude, 1, 0)
            grid.addWidget(phase, 1, 1)
            self.rf_metrics_layout.addWidget(row)
            self._rf_value_labels[channel_id] = {"amplitude": amplitude, "phase": phase}

    @staticmethod
    def _format_value(value: float, decimals: int) -> str:
        text = f"{value:.{decimals}f}".rstrip("0").rstrip(".")
        return "0" if text in {"", "-0"} else text

    @pyqtSlot()
    def _update_reference_measurement_estimate(self) -> None:
        fields = self._parameter_spins["control"]
        samples = int(fields["reference_samples"].value())
        interval = float(fields["reference_sample_interval_s"].value())
        duration_s = max(0.0, (samples - 1) * interval)
        duration = (
            f"{self._format_value(duration_s, 1)} s"
            if duration_s < 60.0
            else f"{self._format_value(duration_s / 60.0, 1)} min"
        )
        self.reference_measurement_estimate_label.setText(
            f"Estimated duration: ≈{duration}  ·  requires {samples} valid samples"
        )

    @pyqtSlot()
    def _refresh_parameter_summaries(self) -> None:
        if not self._reference_summary_labels:
            return
        try:
            config = self._config_from_ui()
        except Exception:
            config = self.config
        control = config["control"]
        reference = config["reference"]
        safety = config["safety"]
        labels = {str(channel["id"]): str(channel["label"]) for channel in config["rf_channels"]}
        feedback_label = labels[self.feedback_channel_id]
        self.timing_summary_label.setText(
            f"Sampling {self._format_value(float(control['sample_period_s']), 2)} s  ·  "
            f"average {self._format_value(float(control['average_window_s']), 2)} s"
        )
        self._reference_summary_labels["feedback"].setText(feedback_label)
        self._reference_summary_labels["channels"].setText(
            "  ·  ".join(
                f"{labels[channel_id]} {float(values['amplitude']):.4g} / {float(values['phase_deg']):.3f}°"
                for channel_id, values in reference["channels"].items()
            )
        )
        self._reference_summary_labels["hv"].setText(
            f"{float(reference['hv_kv']):.3f} kV"
        )
        self._safety_summary_labels["hv"].setText(
            f"{float(safety['hv_min_kv']):.3f}–{float(safety['hv_max_kv']):.3f} kV"
        )
        self._safety_summary_labels["mismatch"].setText(
            f"≤ {float(safety['hv_readback_tolerance_kv']):.3f} kV"
        )
        self._safety_summary_labels["phase"].setText(
            "  ·  ".join(
                f"{labels[channel_id]} ±{float(limit):.2f}°"
                for channel_id, limit in safety["phase_limit_deg"].items()
            )
        )
        self._safety_summary_labels["amplitude"].setText(
            f"Feedback {float(safety['feedback_amplitude_min_rel']) * 100:.1f}–"
            f"{float(safety['feedback_amplitude_max_rel']) * 100:.1f}%  ·  "
            f"ratio ±{float(safety['amplitude_ratio_limit_rel']) * 100:.1f}%"
        )

    def start_monitor(self) -> None:
        config = self._validated_config_or_message()
        if config is not None:
            self._start_session("monitor", config, session_confirmed=False)

    def start_feedback(self) -> None:
        config = self._validated_config_or_message()
        if config is None:
            return
        try:
            require_feedback_write_policy(self.app_context)
            QApplication.setOverrideCursor(Qt.WaitCursor)
            client: BaseClient = create_client(config)
            values = client.read_many(("hv_setpoint", "hv_readback"))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "HV Feedback Preflight Failed", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
        if any(not value.ok or value.value is None for value in values.values()):
            detail = "; ".join(
                f"{key}: {value.error or 'invalid value'}"
                for key, value in values.items()
                if not value.ok or value.value is None
            )
            QMessageBox.critical(self, "HV Feedback Preflight Failed", detail)
            return
        channel = next(
            channel
            for channel in config["rf_channels"]
            if channel["id"] == self.feedback_channel_id
        )
        reference = config["reference"]
        safety = config["safety"]
        control = config["control"]
        message = (
            "Start live high-voltage feedback?\n\n"
            f"Feedback Unit: {config['feedback_unit_label']}\n"
            f"Feedback Channel: {channel['label']}\n"
            f"Target PV: {config['pvs']['hv_setpoint']['name']}\n"
            f"Current setpoint: {values['hv_setpoint'].value:.6f} kV\n"
            f"Current readback: {values['hv_readback'].value:.6f} kV\n"
            f"Reference amplitude: {reference['channels'][self.feedback_channel_id]['amplitude']:.6f}\n"
            f"Reference HV: {reference['hv_kv']:.6f} kV\n"
            f"Maximum step: {control['max_step_kv']:.6f} kV\n"
            f"Total offset limit: ±{control['total_limit_kv']:.6f} kV\n"
            f"Absolute safety range: [{safety['hv_min_kv']:.6f}, {safety['hv_max_kv']:.6f}] kV\n\n"
            "Every write will revalidate the selected unit target against the active profile."
        )
        answer = QMessageBox.warning(
            self,
            "Confirm Live HV Feedback",
            message,
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer == QMessageBox.Yes:
            self._start_session("feedback", config, session_confirmed=True)

    def _start_session(self, operation: str, config: dict[str, Any], *, session_confirmed: bool) -> None:
        if self.session_thread is not None or self.reference_thread is not None:
            return
        self.config = copy.deepcopy(config)
        self.unit_configs[self.active_unit_id] = copy.deepcopy(config)
        self._operation = operation
        self._reset_monitor_display()
        self.session_thread = QThread(self)
        self.session_worker = GeneralizedFeedbackWorker(
            self.app_context,
            config,
            self.feedback_channel_id,
            operation,
            session_confirmed=session_confirmed,
        )
        self.session_worker.moveToThread(self.session_thread)
        self.session_thread.started.connect(self.session_worker.run)
        self.session_worker.rows_ready.connect(self._consume_rows)
        self.session_worker.status_ready.connect(self._set_session_state)
        self.session_worker.log_ready.connect(self._set_log_path)
        self.session_worker.failed.connect(self._session_failed)
        self.session_worker.finished.connect(self.session_thread.quit)
        self.session_worker.finished.connect(self.session_worker.deleteLater)
        self.session_thread.finished.connect(self._session_finished)
        self.session_thread.finished.connect(self.session_thread.deleteLater)
        self._set_busy(True)
        self.status_panel.set_value(
            "operation",
            f"{operation.title()} · {config['feedback_unit_label']}",
        )
        self.status_panel.set_value("state", "CONNECTING", "warning")
        self.status_panel.set_value(
            "write",
            "CONFIRMED" if operation == "feedback" else "READ ONLY",
            "danger" if operation == "feedback" else "success",
        )
        self._set_message("")
        self.session_thread.start()

    def measure_reference(self) -> None:
        if self.session_thread is not None or self.reference_thread is not None:
            return
        if not self._open_parameter_dialog(
            self.reference_measurement_dialog,
            "control",
            REFERENCE_MEASUREMENT_KEYS,
        ):
            return
        config = self._validated_config_or_message()
        if config is None:
            return
        self.config = copy.deepcopy(config)
        self.reference_thread = QThread(self)
        self.reference_worker = GeneralizedReferenceWorker(config)
        self.reference_worker.moveToThread(self.reference_thread)
        self.reference_thread.started.connect(self.reference_worker.run)
        self.reference_worker.measured.connect(self._reference_measured)
        self.reference_worker.status_ready.connect(self._set_message)
        self.reference_worker.failed.connect(self._reference_failed)
        self.reference_worker.finished.connect(self.reference_thread.quit)
        self.reference_worker.finished.connect(self.reference_worker.deleteLater)
        self.reference_thread.finished.connect(self._reference_finished)
        self.reference_thread.finished.connect(self.reference_thread.deleteLater)
        self._set_busy(True)
        self.status_panel.set_value("operation", f"Reference · {config['feedback_unit_label']}")
        self.status_panel.set_value("state", "MEASURING", "warning")
        self.status_panel.set_value("write", "READ ONLY", "success")
        self.reference_thread.start()

    def stop_operation(self) -> None:
        if self.session_worker is not None:
            self.session_worker.stop()
            self._set_message("Stopping current session…")
        if self.reference_worker is not None:
            self.reference_worker.stop()
            self._set_message("Stopping reference measurement…")

    @pyqtSlot(list)
    def _consume_rows(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            event = str(row.get("event", ""))
            if event == "SAMPLE":
                self._append_sample(row)
            elif event == "HOLD":
                self.status_panel.set_value("state", "HOLD", "warning")
                self.status_panel.set_value("write", "BLOCKED", "warning")
                self._set_message(str(row.get("reason", "Safety hold")))
            elif event in {"MONITOR", "CAPUT_HV"} and row.get("hv_next") is not None:
                self._append_hv_command(row)
                if event == "CAPUT_HV":
                    self._set_message(
                        f"Feedback wrote {float(row['hv_next']):.6f} kV to the HV setpoint."
                    )

    def _new_signal_history(self) -> dict[str, list[float]]:
        return {"time": [], **{key: [] for key in required_signal_keys(self.config)}}

    def _append_sample(self, row: dict[str, Any]) -> None:
        timestamp = float(row["timestamp"])
        if self._history_t0 is None:
            self._history_t0 = timestamp
        self._signal_history["time"].append(timestamp)
        for key in required_signal_keys(self.config):
            self._signal_history[key].append(self._numeric_or_nan(row.get(key)))
        self._last_sample_timestamp = timestamp
        self._last_sample_valid = all(
            math.isfinite(self._signal_history[key][-1])
            for key in required_signal_keys(self.config)
        )
        self._update_latest_sample_values(row)
        self._update_sample_freshness()
        if len(self._signal_history["time"]) > MAX_PLOT_SAMPLES:
            for values in self._signal_history.values():
                del values[:-MAX_PLOT_SAMPLES]
        self._schedule_plot_draw()

    def _append_hv_command(self, row: dict[str, Any]) -> None:
        timestamp = self._numeric_or_nan(row.get("timestamp"))
        hv_next = self._numeric_or_nan(row.get("hv_next"))
        if not (math.isfinite(timestamp) and math.isfinite(hv_next)):
            return
        self._hv_command_history["time"].append(timestamp)
        self._hv_command_history["hv_next"].append(hv_next)

    @staticmethod
    def _numeric_or_nan(value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return float("nan")
        return number if math.isfinite(number) else float("nan")

    def _update_latest_sample_values(self, row: dict[str, Any]) -> None:
        reference = self.config["reference"]
        safety = self.config["safety"]
        hv_setpoint = self._numeric_or_nan(row.get("hv_setpoint"))
        hv_readback = self._numeric_or_nan(row.get("hv_readback"))
        for key, value in (("hv_setpoint", hv_setpoint), ("hv_readback", hv_readback)):
            tone = "danger" if math.isfinite(value) and not float(safety["hv_min_kv"]) <= value <= float(safety["hv_max_kv"]) else "subtle"
            self._set_metric(key, f"{value:.6g} kV" if math.isfinite(value) else "INVALID", tone)
        if math.isfinite(hv_setpoint) and math.isfinite(hv_readback):
            mismatch = hv_readback - hv_setpoint
            self._set_metric(
                "hv_mismatch",
                f"{mismatch:+.6g} kV",
                self._threshold_tone(abs(mismatch), float(safety["hv_readback_tolerance_kv"])),
            )
        else:
            self._set_metric("hv_mismatch", "INVALID", "danger")

        feedback_amp = self._numeric_or_nan(row.get(amplitude_key(self.feedback_channel_id)))
        feedback_ref = float(reference["channels"][self.feedback_channel_id]["amplitude"])
        for channel in self.config["rf_channels"]:
            channel_id = str(channel["id"])
            amplitude = self._numeric_or_nan(row.get(amplitude_key(channel_id)))
            phase = self._numeric_or_nan(row.get(phase_key(channel_id)))
            labels = self._rf_value_labels[channel_id]
            if channel_id == self.feedback_channel_id and math.isfinite(amplitude):
                relative = amplitude / feedback_ref
                text = f"{amplitude:.6g}  ·  {relative * 100:.3f}% ref"
                tone = self._range_tone(
                    relative,
                    float(safety["feedback_amplitude_min_rel"]),
                    float(safety["feedback_amplitude_max_rel"]),
                )
            elif math.isfinite(amplitude) and math.isfinite(feedback_amp) and feedback_amp != 0:
                channel_ref = float(reference["channels"][channel_id]["amplitude"])
                ratio_ref = channel_ref / feedback_ref
                ratio_error = (amplitude / feedback_amp) / ratio_ref - 1.0
                text = f"{amplitude:.6g}  ·  ratio {ratio_error * 100:+.3f}%"
                tone = self._threshold_tone(
                    abs(ratio_error), float(safety["amplitude_ratio_limit_rel"])
                )
            else:
                text, tone = "INVALID", "danger"
            self._set_label_tone(labels["amplitude"], text, tone)
            if math.isfinite(phase):
                error = phase_diff_deg(
                    phase, float(reference["channels"][channel_id]["phase_deg"])
                )
                phase_tone = self._threshold_tone(
                    abs(error), float(safety["phase_limit_deg"][channel_id])
                )
                self._set_label_tone(labels["phase"], f"phase {error:+.3f}°", phase_tone)
            else:
                self._set_label_tone(labels["phase"], "INVALID", "danger")

    def _set_metric(self, key: str, text: str, tone: str) -> None:
        self._set_label_tone(self._value_labels[key], text, tone)

    @staticmethod
    def _set_label_tone(label: QLabel, text: str, tone: str) -> None:
        label.setText(text)
        label.setProperty("tone", tone)
        label.style().unpolish(label)
        label.style().polish(label)

    @staticmethod
    def _threshold_tone(value: float, limit: float) -> str:
        if limit <= 0 or value > limit:
            return "danger"
        return "warning" if value >= 0.8 * limit else "subtle"

    @staticmethod
    def _range_tone(value: float, minimum: float, maximum: float) -> str:
        if value < minimum or value > maximum:
            return "danger"
        distance = min(value - minimum, maximum - value)
        span = min(1.0 - minimum, maximum - 1.0)
        return "warning" if span > 0 and distance <= 0.2 * span else "subtle"

    def _reset_monitor_display(self) -> None:
        self._signal_history = self._new_signal_history()
        self._hv_command_history = {"time": [], "hv_next": []}
        self._history_t0 = None
        self._last_sample_timestamp = None
        self._last_sample_valid = None
        for key in ("hv_setpoint", "hv_readback", "hv_mismatch"):
            self._set_metric(key, "— kV", "subtle")
        for labels in self._rf_value_labels.values():
            self._set_label_tone(labels["amplitude"], "—", "subtle")
            self._set_label_tone(labels["phase"], "— deg", "subtle")
        self._update_sample_freshness()
        if hasattr(self, "canvas"):
            self._draw_plots()

    def _update_sample_freshness(self) -> None:
        tone = "subtle"
        if self._last_sample_timestamp is None:
            text = "Waiting for first sample" if self._operation != "stopped" else "Waiting for acquisition"
        elif self._last_sample_valid is False:
            text, tone = "Latest sample invalid", "danger"
        elif self._operation == "stopped":
            text = f"Last sample · {datetime.fromtimestamp(self._last_sample_timestamp):%H:%M:%S}"
        else:
            age = max(0.0, time.time() - self._last_sample_timestamp)
            text = f"Updated {age:.1f} s ago"
            if age > max(2.0, 3.0 * float(self.config["control"]["sample_period_s"])):
                text, tone = f"Sample stale · {age:.1f} s ago", "warning"
        self._set_label_tone(self.sample_freshness_label, text, tone)

    def _schedule_plot_draw(self) -> None:
        if self._plot_redraw_pending or bool(self.plot_toolbar.mode):
            return
        elapsed_ms = (time.monotonic() - self._last_plot_draw_monotonic) * 1000
        self._plot_redraw_pending = True
        QTimer.singleShot(
            max(0, int(PLOT_REDRAW_INTERVAL_MS - elapsed_ms)),
            self._flush_scheduled_plot_draw,
        )

    @pyqtSlot()
    def _flush_scheduled_plot_draw(self) -> None:
        self._plot_redraw_pending = False
        self._draw_plots()

    def _plot_control_changed(self, *_args: Any) -> None:
        self._draw_plots()

    def _plot_navigation_changed(self, active: bool) -> None:
        if not active:
            self._schedule_plot_draw()

    def _draw_plots(self) -> None:
        palette = DARK_THEME if self.current_theme == "dark" else LIGHT_THEME
        times_all = self._signal_history.get("time", [])
        window_seconds = self.plot_window_combo.currentData()
        start = 0
        if times_all and window_seconds is not None:
            cutoff = times_all[-1] - float(window_seconds)
            start = next((i for i, stamp in enumerate(times_all) if stamp >= cutoff), len(times_all))
        times = times_all[start:]
        signals = {key: values[start:] for key, values in self._signal_history.items() if key != "time"}
        clock = self.plot_time_axis_combo.currentText() == "Clock"
        t0 = self._history_t0 or 0.0
        x = [datetime.fromtimestamp(value) for value in times] if clock else [(value - t0) / 60 for value in times]
        for axis in (self.amp_axis, self.phase_axis, self.hv_axis):
            axis.clear()
            axis.set_facecolor(palette["plot_bg"])
            axis.tick_params(colors=palette["plot_text"], labelsize=8)
            for spine in axis.spines.values():
                spine.set_color(palette["plot_spine"])
            axis.grid(True, color=palette["plot_grid"], alpha=0.75, linestyle="--")
        relative = self.plot_scale_combo.currentText() == "Relative"
        reference = self.config["reference"]
        channels = self.config["rf_channels"]
        feedback_values = signals.get(amplitude_key(self.feedback_channel_id), [])
        feedback_ref = float(reference["channels"][self.feedback_channel_id]["amplitude"])
        for index, channel in enumerate(channels):
            channel_id = str(channel["id"])
            label = str(channel["label"])
            amplitude_values = signals.get(amplitude_key(channel_id), [])
            phase_values = signals.get(phase_key(channel_id), [])
            if relative:
                if channel_id == self.feedback_channel_id:
                    amplitude_plot = [((value / feedback_ref) - 1) * 100 if math.isfinite(value) else float("nan") for value in amplitude_values]
                    amplitude_label = f"{label} level"
                else:
                    ratio_ref = float(reference["channels"][channel_id]["amplitude"]) / feedback_ref
                    amplitude_plot = [
                        (((value / feedback) / ratio_ref) - 1) * 100
                        if math.isfinite(value) and math.isfinite(feedback) and feedback != 0
                        else float("nan")
                        for value, feedback in zip(amplitude_values, feedback_values)
                    ]
                    amplitude_label = f"{label} ratio"
                phase_plot = [phase_diff_deg(value, float(reference["channels"][channel_id]["phase_deg"])) if math.isfinite(value) else float("nan") for value in phase_values]
            else:
                amplitude_plot = amplitude_values
                amplitude_label = label
                phase_plot = phase_values
            color = PLOT_COLORS[index % len(PLOT_COLORS)]
            if x:
                self.amp_axis.plot(x, amplitude_plot, label=amplitude_label, color=color)
                self.phase_axis.plot(x, phase_plot, label=label, color=color)
        hv0 = float(reference["hv_kv"])
        hv_setpoint = signals.get("hv_setpoint", [])
        hv_readback = signals.get("hv_readback", [])
        if relative:
            hv_setpoint = [(value - hv0) * 1000 if math.isfinite(value) else float("nan") for value in hv_setpoint]
            hv_readback = [(value - hv0) * 1000 if math.isfinite(value) else float("nan") for value in hv_readback]
        if x:
            self.hv_axis.plot(x, hv_setpoint, label="Setpoint", color="#e37878")
            self.hv_axis.plot(x, hv_readback, label="Readback", color="#7dd7c5")
        command_times = self._hv_command_history["time"]
        command_values = self._hv_command_history["hv_next"]
        if command_times:
            command_x = [datetime.fromtimestamp(value) for value in command_times] if clock else [(value - t0) / 60 for value in command_times]
            if relative:
                command_values = [(value - hv0) * 1000 for value in command_values]
            self.hv_axis.plot(command_x, command_values, label="Written target" if self._operation == "feedback" else "Computed target", color="#f0b45a", marker="o", markersize=3, drawstyle="steps-post")
        self.amp_axis.set_title("Amplitude Error from Reference" if relative else "Amplitude", color=palette["plot_text"], fontsize=10)
        self.phase_axis.set_title("Phase Error from Reference" if relative else "Phase", color=palette["plot_text"], fontsize=10)
        self.hv_axis.set_title("High Voltage Offset from Reference" if relative else "High Voltage", color=palette["plot_text"], fontsize=10)
        self.amp_axis.set_ylabel("Error (%)" if relative else "Amplitude (a.u.)", color=palette["plot_text"])
        self.phase_axis.set_ylabel("Error (deg)" if relative else "Phase (deg)", color=palette["plot_text"])
        self.hv_axis.set_ylabel("HV - HV0 (V)" if relative else "HV (kV)", color=palette["plot_text"])
        for axis in (self.amp_axis, self.phase_axis, self.hv_axis):
            handles, labels = axis.get_legend_handles_labels()
            if handles:
                legend = axis.legend(handles, labels, loc="best", fontsize=8, frameon=False)
                for label in legend.get_texts():
                    label.set_color(palette["plot_text"])
            if relative:
                axis.axhline(0, color=palette["plot_spine"], linewidth=0.9, alpha=0.8)
        self.amp_axis.tick_params(labelbottom=False)
        self.phase_axis.tick_params(labelbottom=False)
        if clock:
            locator = mdates.AutoDateLocator()
            self.hv_axis.xaxis.set_major_locator(locator)
            self.hv_axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
            self.hv_axis.set_xlabel("Clock time", color=palette["plot_text"])
        else:
            self.hv_axis.xaxis.set_major_locator(mticker.AutoLocator())
            self.hv_axis.xaxis.set_major_formatter(mticker.ScalarFormatter())
            self.hv_axis.set_xlabel("Elapsed time (min)", color=palette["plot_text"])
        self.canvas.draw_idle()
        self._last_plot_draw_monotonic = time.monotonic()

    @pyqtSlot(str, str)
    def _set_session_state(self, state: str, tone: str) -> None:
        self.status_panel.set_value("state", state, tone)

    @pyqtSlot(str)
    def _set_log_path(self, path: str) -> None:
        self.status_panel.set_value("log", Path(path).name)

    @pyqtSlot(str)
    def _session_failed(self, message: str) -> None:
        self.status_panel.set_value("state", "ERROR", "danger")
        self.status_panel.set_value("write", "BLOCKED", "warning")
        self._set_message(message)
        QMessageBox.critical(self, "HV Feedback Session Failed", message)

    @pyqtSlot()
    def _session_finished(self) -> None:
        self.session_thread = None
        self.session_worker = None
        self._set_idle_state()

    @pyqtSlot(dict)
    def _reference_measured(self, values: dict[str, Any]) -> None:
        self.config["reference"] = copy.deepcopy(values)
        self.unit_configs[self.active_unit_id] = copy.deepcopy(self.config)
        self._apply_config_to_ui(self.config)
        self._reset_monitor_display()
        self._set_message("Reference measurement completed. Save a runtime snapshot if these values should be reused.")

    @pyqtSlot(str)
    def _reference_failed(self, message: str) -> None:
        self._set_message(message)
        QMessageBox.critical(self, "Reference Measurement Failed", message)

    @pyqtSlot()
    def _reference_finished(self) -> None:
        self.reference_thread = None
        self.reference_worker = None
        self._set_idle_state(preserve_message=True)

    def save_snapshot(self) -> None:
        config = self._validated_config_or_message()
        if config is None:
            return
        try:
            path = save_runtime_snapshot(self.app_context, config)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Save Snapshot Failed", str(exc))
            return
        self.config = copy.deepcopy(config)
        self.unit_configs[self.active_unit_id] = copy.deepcopy(config)
        self._set_message(f"Saved runtime snapshot: {path}")

    def load_snapshot(self) -> None:
        paths = resolve_hv_feedback_runtime_paths(self.app_context, self.active_unit_id)
        snapshots_dir = paths["snapshots_dir"]
        start_dir = snapshots_dir if snapshots_dir.is_dir() else paths["snapshots_root"]
        if not start_dir.is_dir():
            QMessageBox.information(self, "Load Snapshot", "No runtime snapshots have been saved yet.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Load HV Feedback Snapshot", str(start_dir), "JSON snapshots (*.json)")
        if not path:
            return
        try:
            config = load_runtime_snapshot(
                self.app_context,
                path,
                self.base_configs[self.active_unit_id],
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Load Snapshot Failed", str(exc))
            return
        self.config = config
        self.unit_configs[self.active_unit_id] = copy.deepcopy(config)
        self._apply_config_to_ui(config)
        self._reset_monitor_display()
        self._set_message(f"Loaded runtime snapshot: {path}")

    def _set_busy(self, busy: bool) -> None:
        for widget in (
            self.start_monitor_button,
            self.start_feedback_button,
            self.measure_reference_button,
            self.save_snapshot_button,
            self.load_snapshot_button,
            self.feedback_unit_combo,
            self.feedback_channel_combo,
        ):
            widget.setEnabled(not busy)
        self.stop_button.setEnabled(busy)
        for button in self._parameter_edit_buttons:
            button.setEnabled(not busy)
        for fields in self._parameter_spins.values():
            for spin in fields.values():
                spin.setEnabled(not busy)

    def _set_idle_state(self, *, preserve_message: bool = False) -> None:
        self._operation = "stopped"
        self._set_busy(False)
        self.status_panel.set_value("operation", "None")
        self.status_panel.set_value("state", "STOPPED")
        self.status_panel.set_value("write", "NOT ARMED")
        self._update_sample_freshness()
        if not preserve_message:
            self._set_message("")

    @pyqtSlot(str)
    def _set_message(self, message: str) -> None:
        text = str(message).strip()
        self.message_label.setText(text)
        self.message_label.setVisible(bool(text))

    def _toggle_theme(self) -> None:
        self.current_theme = "light" if self.current_theme == "dark" else "dark"
        self._apply_theme()

    def _apply_theme(self) -> None:
        palette = DARK_THEME if self.current_theme == "dark" else LIGHT_THEME
        self.theme_toggle_button.setText("☀" if self.current_theme == "dark" else "☾")
        self.theme_toggle_button.setToolTip(
            "Switch to light theme" if self.current_theme == "dark" else "Switch to dark theme"
        )
        self.setStyleSheet(build_hv_feedback_theme(palette))
        self.plot_toolbar.refresh_icons()
        self.status_panel.apply_theme(palette)
        self.status_panel.setFixedHeight(self.status_panel.sizeHint().height())
        self.figure.patch.set_facecolor(palette["plot_card_bg"])
        self._draw_plots()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.session_worker is not None:
            self.session_worker.stop()
        if self.reference_worker is not None:
            self.reference_worker.stop()
        for thread in (self.session_thread, self.reference_thread):
            if thread is not None:
                thread.quit()
                thread.wait(2500)
        super().closeEvent(event)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    try:
        window = HVFeedbackWindow()
    except (MachineProfileError, ValueError) as exc:
        QMessageBox.critical(None, "HV Feedback Unavailable", str(exc))
        return 2
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
