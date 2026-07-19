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
    load_profile_config,
    load_runtime_snapshot,
    new_run_dir,
    require_confirmed_feedback_write,
    require_feedback_write_policy,
    resolve_hv_feedback_runtime_paths,
    save_runtime_snapshot,
    validate_session_config,
    write_run_metadata,
)
from half_linac.src.apps.hv_feedback.reference import REFERENCE_KEYS, auto_reference
from half_linac.src.apps.hv_feedback.runtime import (
    REQUIRED_KEYS,
    FeedbackEngine,
    create_client,
)
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

REFERENCE_FIELDS = (
    ("acc1_amp_ref", "ACC1 amplitude", 0.000001, 1.0e9, 1.0, 6),
    ("acc1_phase_ref", "ACC1 phase (deg)", -180.0, 180.0, 0.1, 4),
    ("buncher_phase_ref", "Buncher phase (deg)", -180.0, 180.0, 0.1, 4),
    ("amp_ratio_ref", "Amplitude ratio", 0.000001, 1.0e6, 0.001, 6),
    ("hv0", "Reference HV (kV)", 0.0, 100.0, 0.001, 6),
)

SAFETY_FIELDS = (
    ("hv_min_kv", "Minimum HV (kV)", 0.0, 100.0, 0.01, 4),
    ("hv_max_kv", "Maximum HV (kV)", 0.0, 100.0, 0.01, 4),
    ("hv_readback_tolerance_kv", "HV mismatch limit (kV)", 0.000001, 10.0, 0.001, 6),
    ("acc1_phase_limit_deg", "ACC1 phase limit (deg)", 0.000001, 180.0, 0.1, 4),
    ("buncher_phase_limit_deg", "Buncher phase limit (deg)", 0.000001, 180.0, 0.1, 4),
    ("amp_ratio_limit_rel", "Ratio relative limit", 0.000001, 1.0, 0.001, 6),
    ("acc1_amp_min_rel", "ACC1 minimum relative", 0.000001, 10.0, 0.01, 6),
    ("acc1_amp_max_rel", "ACC1 maximum relative", 0.000001, 10.0, 0.01, 6),
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


class FeedbackWorker(QObject):
    rows_ready = pyqtSignal(list)
    status_ready = pyqtSignal(str, str)
    log_ready = pyqtSignal(str)
    failed = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(
        self,
        context: AppContext,
        config: dict[str, Any],
        operation: str,
        *,
        session_confirmed: bool,
    ) -> None:
        super().__init__()
        self.context = context
        self.config = copy.deepcopy(config)
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
            run_dir = new_run_dir(self.context, self.operation)
            logging_cfg = self.config["logging"]
            logger = CSVLogger(
                run_dir,
                str(logging_cfg["file_prefix"]),
                int(logging_cfg["flush_every_n_rows"]),
            )
            write_run_metadata(
                self.context,
                run_dir,
                operation=self.operation,
                config=self.config,
                state="CONNECTING",
                log_path=logger.path,
            )
            self.log_ready.emit(str(logger.path))
            authorizer = None
            if self.operation == "feedback":
                authorizer = lambda: require_confirmed_feedback_write(
                    self.context,
                    session_confirmed=self.session_confirmed,
                )
            engine = FeedbackEngine(
                self.config,
                mode=self.operation,
                write_authorizer=authorizer,
            )
            active_state = "FEEDBACK ACTIVE" if self.operation == "feedback" else "MONITORING"
            self.status_ready.emit(active_state, "danger" if self.operation == "feedback" else "success")

            while not self._stop_event.is_set():
                rows = engine.step()
                for row in rows:
                    logger.write(row)
                self.rows_ready.emit(rows)
                hold_rows = [row for row in rows if row.get("event") == "HOLD"]
                if hold_rows:
                    final_state = "HOLD"
                    detail = str(hold_rows[-1].get("reason", "safety hold"))
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


class ReferenceWorker(QObject):
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
            buffer = DataBuffer(max_age_s=None)
            started = time.monotonic()
            for index in range(sample_count):
                target_time = started + index * sample_interval
                delay = max(0.0, target_time - time.monotonic())
                if self._stop_event.wait(delay):
                    return
                values = client.read_many(REQUIRED_KEYS)
                errors = {
                    key: value.error
                    for key, value in values.items()
                    if not value.ok or value.value is None
                }
                if errors:
                    raise RuntimeError(f"PV read invalid during reference measurement: {errors}")
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
            result = auto_reference(buffer, self.config["safety"])
            if result.reference is None:
                raise RuntimeError(result.reason or "Reference measurement failed.")
            self.measured.emit(
                {key: float(getattr(result.reference, key)) for key in REFERENCE_KEYS}
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
        self.base_config = load_profile_config(self.app_context)
        self.config = copy.deepcopy(self.base_config)
        self.current_theme = resolve_initial_theme()

        self.session_thread: QThread | None = None
        self.session_worker: FeedbackWorker | None = None
        self.reference_thread: QThread | None = None
        self.reference_worker: ReferenceWorker | None = None
        self._operation = "stopped"
        self._signal_history: dict[str, list[float]] = {
            "time": [],
            "acc1_amp": [],
            "buncher_amp": [],
            "acc1_phase": [],
            "buncher_phase": [],
            "hv_setpoint": [],
            "hv_readback": [],
        }
        self._hv_command_history: dict[str, list[float]] = {
            "time": [],
            "hv_next": [],
        }
        self._history_t0: float | None = None
        self._last_sample_timestamp: float | None = None
        self._last_sample_valid: bool | None = None
        self._plot_redraw_pending = False
        self._last_plot_draw_monotonic = 0.0
        self._parameter_spins: dict[str, dict[str, QDoubleSpinBox | QSpinBox]] = {
            "control": {},
            "reference": {},
            "safety": {},
        }
        self._reference_summary_labels: dict[str, QLabel] = {}
        self._safety_summary_labels: dict[str, QLabel] = {}
        self._parameter_edit_buttons: list[QPushButton] = []
        self._value_labels: dict[str, QLabel] = {}

        self._build_ui()
        self._apply_config_to_ui(self.config)
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
        central.setObjectName("centralWidget")
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
        self.measure_reference_button.setObjectName("measureReferenceButton")
        self.stop_button = QPushButton("Stop", actions)
        self.stop_button.setObjectName("stopButton")
        self.save_snapshot_button = QPushButton("Save Snapshot", actions)
        self.save_snapshot_button.setObjectName("saveSnapshotButton")
        self.load_snapshot_button = QPushButton("Load Snapshot", actions)
        self.load_snapshot_button.setObjectName("loadSnapshotButton")
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

        self._build_parameter_dialogs()

        self.start_monitor_button.clicked.connect(self.start_monitor)
        self.start_feedback_button.clicked.connect(self.start_feedback)
        self.measure_reference_button.clicked.connect(self.measure_reference)
        self.stop_button.clicked.connect(self.stop_operation)
        self.save_snapshot_button.clicked.connect(self.save_snapshot)
        self.load_snapshot_button.clicked.connect(self.load_snapshot)

    def _build_monitor_panel(self, parent: QWidget) -> QWidget:
        panel = QWidget(parent)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(10)

        values_card = QFrame(panel)
        values_card.setObjectName("controlCard")
        values_card_layout = QVBoxLayout(values_card)
        values_card_layout.setContentsMargins(12, 11, 12, 12)
        values_card_layout.setSpacing(9)
        values_header = QHBoxLayout()
        values_title = QLabel("Latest Sample", values_card)
        values_title.setObjectName("panelTitle")
        values_header.addWidget(values_title)
        values_header.addStretch(1)
        self.sample_freshness_label = QLabel("Waiting for acquisition", values_card)
        self.sample_freshness_label.setProperty("role", "sampleFreshness")
        self.sample_freshness_label.setProperty("tone", "subtle")
        values_header.addWidget(self.sample_freshness_label)
        values_card_layout.addLayout(values_header)
        values_layout = QGridLayout()
        values_layout.setContentsMargins(0, 0, 0, 0)
        values_layout.setHorizontalSpacing(8)
        values_layout.setVerticalSpacing(8)
        value_specs = (
            ("hv_setpoint", "HV setpoint", "— kV"),
            ("hv_readback", "HV readback", "— kV"),
            ("hv_mismatch", "HV mismatch", "— kV"),
            ("acc1_level", "ACC1 level", "— % of ref"),
            ("amp_ratio_error", "Amplitude ratio error", "— %"),
            ("phase_error", "ACC1 / Buncher phase error", "— / — deg"),
        )
        for index, (key, label_text, placeholder) in enumerate(value_specs):
            row, col = divmod(index, 3)
            card = QFrame(values_card)
            card.setObjectName("metricCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 8, 10, 8)
            card_layout.setSpacing(2)
            name = QLabel(label_text, card)
            name.setProperty("role", "metricName")
            value = QLabel(placeholder, card)
            value.setProperty("role", "metricValue")
            value.setProperty("tone", "subtle")
            card_layout.addWidget(name)
            card_layout.addWidget(value)
            values_layout.addWidget(card, row, col)
            self._value_labels[key] = value
        values_card_layout.addLayout(values_layout)
        layout.addWidget(values_card)

        plot_card = QFrame(panel)
        plot_card.setObjectName("plotCard")
        plot_layout = QVBoxLayout(plot_card)
        plot_layout.setContentsMargins(12, 11, 12, 12)
        plot_layout.setSpacing(6)
        plot_header = QHBoxLayout()
        plot_header.setSpacing(6)
        plot_title = QLabel("Feedback Trends", plot_card)
        plot_title.setObjectName("panelTitle")
        plot_header.addWidget(plot_title)
        plot_header.addStretch(1)

        scale_label = QLabel("Scale", plot_card)
        scale_label.setProperty("role", "field")
        plot_header.addWidget(scale_label)
        self.plot_scale_combo = QComboBox(plot_card)
        self.plot_scale_combo.addItems(("Relative", "Raw"))
        self.plot_scale_combo.setToolTip(
            "Relative shows deviations from the active feedback reference."
        )
        plot_header.addWidget(self.plot_scale_combo)

        view_label = QLabel("View", plot_card)
        view_label.setProperty("role", "field")
        plot_header.addWidget(view_label)
        self.plot_window_combo = QComboBox(plot_card)
        for text, seconds in (
            ("Recent 15 min", 15 * 60),
            ("Recent 30 min", 30 * 60),
            ("Recent 60 min", 60 * 60),
            ("All", None),
        ):
            self.plot_window_combo.addItem(text, seconds)
        self.plot_window_combo.setToolTip(
            "Limit the visible trend by elapsed time; All shows the current in-memory run."
        )
        plot_header.addWidget(self.plot_window_combo)

        time_label = QLabel("Time", plot_card)
        time_label.setProperty("role", "field")
        plot_header.addWidget(time_label)
        self.plot_time_axis_combo = QComboBox(plot_card)
        self.plot_time_axis_combo.addItems(("Elapsed", "Clock"))
        self.plot_time_axis_combo.setToolTip(
            "Elapsed uses minutes from session start; Clock uses local wall-clock time."
        )
        plot_header.addWidget(self.plot_time_axis_combo)
        plot_layout.addLayout(plot_header)

        self.figure = Figure(figsize=(8, 6), constrained_layout=True)
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.amp_axis = self.figure.add_subplot(311)
        self.phase_axis = self.figure.add_subplot(312, sharex=self.amp_axis)
        self.hv_axis = self.figure.add_subplot(313, sharex=self.amp_axis)
        self.plot_toolbar = CompactNavigationToolbar(self.canvas, plot_card)
        self.plot_toolbar.setObjectName("plotToolbar")
        plot_layout.addWidget(self.plot_toolbar)
        plot_layout.addWidget(self.canvas)
        self.plot_scale_combo.currentTextChanged.connect(self._plot_control_changed)
        self.plot_window_combo.currentIndexChanged.connect(self._plot_control_changed)
        self.plot_time_axis_combo.currentTextChanged.connect(self._plot_control_changed)
        self.plot_toolbar.navigation_mode_changed.connect(self._plot_navigation_changed)
        layout.addWidget(plot_card, 1)
        return panel

    def _build_configuration_panel(self, parent: QWidget) -> QWidget:
        scroll = QScrollArea(parent)
        scroll.setObjectName("configurationScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        panel = QWidget(scroll)
        panel.setObjectName("configurationPanel")
        panel.setMinimumWidth(430)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 0, 0, 0)
        layout.setSpacing(10)

        feedback_card = QFrame(panel)
        feedback_card.setObjectName("controlCard")
        feedback_layout = QVBoxLayout(feedback_card)
        feedback_layout.setContentsMargins(12, 11, 12, 12)
        feedback_layout.setSpacing(8)
        feedback_header = QHBoxLayout()
        feedback_title = QLabel("Feedback Control", feedback_card)
        feedback_title.setObjectName("panelTitle")
        feedback_header.addWidget(feedback_title)
        feedback_header.addStretch(1)
        self.advanced_settings_button = QPushButton("Advanced…", feedback_card)
        self.advanced_settings_button.setProperty("compact", True)
        feedback_header.addWidget(self.advanced_settings_button)
        feedback_layout.addLayout(feedback_header)
        feedback_layout.addLayout(
            self._build_parameter_form(
                "control",
                self._select_specs(CONTROL_FIELDS, PRIMARY_CONTROL_KEYS),
                feedback_card,
            )
        )
        self.timing_summary_label = QLabel("", feedback_card)
        self.timing_summary_label.setProperty("role", "field")
        self.timing_summary_label.setWordWrap(True)
        feedback_layout.addWidget(self.timing_summary_label)
        layout.addWidget(feedback_card)

        reference_card, self.edit_reference_button = self._build_summary_card(
            "Reference Target",
            (
                ("acc1_amp_ref", "ACC1 amplitude"),
                ("phases", "ACC1 / Buncher phase"),
                ("amp_ratio_ref", "Amplitude ratio"),
                ("hv0", "Reference HV"),
            ),
            self._reference_summary_labels,
            panel,
        )
        layout.addWidget(reference_card)

        safety_card, self.edit_safety_button = self._build_summary_card(
            "Safety Limits",
            (
                ("hv_range", "HV range"),
                ("hv_mismatch", "Readback mismatch"),
                ("phase_limits", "Phase deviation"),
                ("amplitude_limits", "Amplitude deviation"),
            ),
            self._safety_summary_labels,
            panel,
        )
        layout.addWidget(safety_card)
        self._parameter_edit_buttons.extend(
            (
                self.advanced_settings_button,
                self.edit_reference_button,
                self.edit_safety_button,
            )
        )
        layout.addStretch(1)
        scroll.setWidget(panel)
        return scroll

    @staticmethod
    def _select_specs(
        specs: tuple[tuple[str, str, float, float, float, int | None], ...],
        keys: tuple[str, ...],
    ) -> tuple[tuple[str, str, float, float, float, int | None], ...]:
        by_key = {spec[0]: spec for spec in specs}
        return tuple(by_key[key] for key in keys)

    def _build_parameter_form(
        self,
        section: str,
        specs: tuple[tuple[str, str, float, float, float, int | None], ...],
        parent: QWidget,
    ) -> QFormLayout:
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for spec in specs:
            _key, label, *_ = spec
            field_label = QLabel(label, parent)
            field_label.setProperty("role", "field")
            spin = self._create_parameter_spin(section, spec, parent)
            form.addRow(field_label, spin)
        return form

    def _create_parameter_spin(
        self,
        section: str,
        spec: tuple[str, str, float, float, float, int | None],
        parent: QWidget,
    ) -> QDoubleSpinBox | QSpinBox:
        key, _label, minimum, maximum, step, decimals = spec
        if decimals is None:
            spin = QSpinBox(parent)
            spin.setRange(int(minimum), int(maximum))
            spin.setSingleStep(int(step))
        else:
            spin = CompactDoubleSpinBox(parent)
            spin.setRange(minimum, maximum)
            spin.setSingleStep(step)
            spin.setDecimals(decimals)
        spin.setKeyboardTracking(False)
        spin.setProperty("numeric", True)
        spin.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._parameter_spins[section][key] = spin
        return spin

    def _build_summary_card(
        self,
        title: str,
        rows: tuple[tuple[str, str], ...],
        labels: dict[str, QLabel],
        parent: QWidget,
    ) -> tuple[QFrame, QPushButton]:
        card = QFrame(parent)
        card.setObjectName("controlCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 11, 12, 12)
        card_layout.setSpacing(8)

        header = QHBoxLayout()
        title_label = QLabel(title, card)
        title_label.setObjectName("panelTitle")
        header.addWidget(title_label)
        header.addStretch(1)
        edit_button = QPushButton("Edit…", card)
        edit_button.setProperty("compact", True)
        header.addWidget(edit_button)
        card_layout.addLayout(header)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        for row, (key, text) in enumerate(rows):
            field_label = QLabel(text, card)
            field_label.setProperty("role", "field")
            value_label = QLabel("—", card)
            value_label.setProperty("role", "summaryValue")
            value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(field_label, row, 0)
            grid.addWidget(value_label, row, 1)
            labels[key] = value_label
        grid.setColumnStretch(1, 1)
        card_layout.addLayout(grid)
        return card, edit_button

    def _build_parameter_dialogs(self) -> None:
        self.advanced_settings_dialog = self._build_parameter_dialog(
            "Advanced Settings",
            "control",
            self._select_specs(CONTROL_FIELDS, ADVANCED_CONTROL_KEYS),
            accept_text="Apply",
        )
        self.reference_measurement_dialog = self._build_parameter_dialog(
            "Measure Reference",
            "control",
            self._select_specs(CONTROL_FIELDS, REFERENCE_MEASUREMENT_KEYS),
            accept_text="Start Measurement",
        )
        self.reference_measurement_estimate_label = QLabel(
            "",
            self.reference_measurement_dialog,
        )
        self.reference_measurement_estimate_label.setProperty("role", "field")
        self.reference_measurement_estimate_label.setWordWrap(True)
        measurement_layout = self.reference_measurement_dialog.layout()
        if not isinstance(measurement_layout, QVBoxLayout):
            raise RuntimeError("Reference measurement dialog requires a vertical layout.")
        measurement_layout.insertWidget(
            measurement_layout.count() - 1,
            self.reference_measurement_estimate_label,
        )
        self.reference_dialog = self._build_parameter_dialog(
            "Edit Reference",
            "reference",
            REFERENCE_FIELDS,
            accept_text="Apply",
        )
        self.safety_dialog = self._build_parameter_dialog(
            "Edit Safety Limits",
            "safety",
            SAFETY_FIELDS,
            accept_text="Apply",
        )
        self.advanced_settings_button.clicked.connect(
            lambda _checked=False: self._open_parameter_dialog(
                self.advanced_settings_dialog,
                "control",
                ADVANCED_CONTROL_KEYS,
            )
        )
        self.edit_reference_button.clicked.connect(
            lambda _checked=False: self._open_parameter_dialog(
                self.reference_dialog,
                "reference",
                tuple(spec[0] for spec in REFERENCE_FIELDS),
            )
        )
        self.edit_safety_button.clicked.connect(
            lambda _checked=False: self._open_parameter_dialog(
                self.safety_dialog,
                "safety",
                tuple(spec[0] for spec in SAFETY_FIELDS),
            )
        )
        for fields in self._parameter_spins.values():
            for spin in fields.values():
                spin.valueChanged.connect(self._refresh_parameter_summaries)
        self._parameter_spins["control"]["reference_samples"].valueChanged.connect(
            self._update_reference_measurement_estimate
        )
        self._parameter_spins["control"]["reference_sample_interval_s"].valueChanged.connect(
            self._update_reference_measurement_estimate
        )
        self._update_reference_measurement_estimate()

    def _build_parameter_dialog(
        self,
        title: str,
        section: str,
        specs: tuple[tuple[str, str, float, float, float, int | None], ...],
        *,
        accept_text: str,
    ) -> QDialog:
        dialog = QDialog(self)
        dialog.setObjectName("parameterDialog")
        dialog.setWindowTitle(title)
        dialog.setModal(True)
        dialog.setMinimumWidth(480)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        card = QFrame(dialog)
        card.setObjectName("controlCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 11, 12, 12)
        card_layout.addLayout(self._build_parameter_form(section, specs, card))
        layout.addWidget(card)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dialog)
        buttons.button(QDialogButtonBox.Ok).setText(accept_text)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        return dialog

    def _open_parameter_dialog(
        self,
        dialog: QDialog,
        section: str,
        keys: tuple[str, ...],
    ) -> bool:
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
                self._refresh_parameter_summaries()
                if section in {"reference", "safety"}:
                    self._reset_monitor_display()
                return True

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
        if duration_s < 60.0:
            duration = f"{self._format_value(duration_s, 1)} s"
        else:
            duration = f"{self._format_value(duration_s / 60.0, 1)} min"
        self.reference_measurement_estimate_label.setText(
            f"Estimated duration: ≈{duration}  ·  requires {samples} valid samples"
        )

    @pyqtSlot()
    def _refresh_parameter_summaries(self) -> None:
        if not self._reference_summary_labels or not self._safety_summary_labels:
            return
        control = {key: spin.value() for key, spin in self._parameter_spins["control"].items()}
        reference = {
            key: spin.value() for key, spin in self._parameter_spins["reference"].items()
        }
        safety = {key: spin.value() for key, spin in self._parameter_spins["safety"].items()}

        fmt = self._format_value
        self.timing_summary_label.setText(
            "Sampling "
            f"{fmt(control['sample_period_s'], 2)} s  ·  "
            f"average {fmt(control['average_window_s'], 2)} s"
        )
        self._reference_summary_labels["acc1_amp_ref"].setText(
            f"{fmt(reference['acc1_amp_ref'], 1)} a.u."
        )
        self._reference_summary_labels["phases"].setText(
            f"{fmt(reference['acc1_phase_ref'], 3)}° / "
            f"{fmt(reference['buncher_phase_ref'], 3)}°"
        )
        self._reference_summary_labels["amp_ratio_ref"].setText(
            fmt(reference["amp_ratio_ref"], 4)
        )
        self._reference_summary_labels["hv0"].setText(
            f"{fmt(reference['hv0'], 3)} kV"
        )

        self._safety_summary_labels["hv_range"].setText(
            f"{fmt(safety['hv_min_kv'], 3)}–{fmt(safety['hv_max_kv'], 3)} kV"
        )
        self._safety_summary_labels["hv_mismatch"].setText(
            f"≤ {fmt(safety['hv_readback_tolerance_kv'], 3)} kV"
        )
        self._safety_summary_labels["phase_limits"].setText(
            f"±{fmt(safety['acc1_phase_limit_deg'], 2)}° / "
            f"±{fmt(safety['buncher_phase_limit_deg'], 2)}°"
        )
        self._safety_summary_labels["amplitude_limits"].setText(
            f"Ratio ±{fmt(safety['amp_ratio_limit_rel'] * 100.0, 1)}%  ·  "
            f"ACC1 {fmt(safety['acc1_amp_min_rel'] * 100.0, 1)}–"
            f"{fmt(safety['acc1_amp_max_rel'] * 100.0, 1)}%"
        )

    def _apply_config_to_ui(self, config: dict[str, Any]) -> None:
        for section, fields in self._parameter_spins.items():
            for key, spin in fields.items():
                value = config[section][key]
                spin.setValue(int(value) if isinstance(spin, QSpinBox) else float(value))
        self._refresh_parameter_summaries()

    def _config_from_ui(self) -> dict[str, Any]:
        config = copy.deepcopy(self.config)
        for section, fields in self._parameter_spins.items():
            for key, spin in fields.items():
                config[section][key] = (
                    int(spin.value()) if isinstance(spin, QSpinBox) else float(spin.value())
                )
        config["reference"]["mode"] = "manual"
        validate_session_config(config)
        return config

    def _validated_config_or_message(self) -> dict[str, Any] | None:
        try:
            return self._config_from_ui()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Invalid HV Feedback Parameters", str(exc))
            return None

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

        control = config["control"]
        reference = config["reference"]
        safety = config["safety"]
        target_pv = config["pvs"]["hv_setpoint"]["name"]
        message = (
            "Start live high-voltage feedback?\n\n"
            f"Target PV: {target_pv}\n"
            f"Current setpoint: {values['hv_setpoint'].value:.6f} kV\n"
            f"Current readback: {values['hv_readback'].value:.6f} kV\n"
            f"Reference HV: {reference['hv0']:.6f} kV\n"
            f"Maximum step: {control['max_step_kv']:.6f} kV\n"
            f"Total offset limit: ±{control['total_limit_kv']:.6f} kV\n"
            f"Absolute safety range: [{safety['hv_min_kv']:.6f}, "
            f"{safety['hv_max_kv']:.6f}] kV\n"
            f"ACC1 amplitude reference: {reference['acc1_amp_ref']:.6f}\n\n"
            "Every write will be re-authorized against the active machine profile."
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

    def _start_session(
        self,
        operation: str,
        config: dict[str, Any],
        *,
        session_confirmed: bool,
    ) -> None:
        if self.session_thread is not None or self.reference_thread is not None:
            return
        self.config = copy.deepcopy(config)
        self._operation = operation
        self._reset_monitor_display()
        self.session_thread = QThread(self)
        self.session_worker = FeedbackWorker(
            self.app_context,
            config,
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
        self.status_panel.set_value("operation", operation.title())
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
        self.reference_worker = ReferenceWorker(config)
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
        self.status_panel.set_value("operation", "Reference")
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
                reason = str(row.get("reason", "Safety hold"))
                self.status_panel.set_value("state", "HOLD", "warning")
                self.status_panel.set_value("write", "BLOCKED", "warning")
                self._set_message(reason)
            elif event in {"MONITOR", "CAPUT_HV"} and row.get("hv_next") is not None:
                self._append_hv_command(row)
                if event == "CAPUT_HV":
                    self._set_message(
                        f"Feedback wrote {float(row['hv_next']):.6f} kV to the HV setpoint."
                    )

    def _append_sample(self, row: dict[str, Any]) -> None:
        timestamp = float(row["timestamp"])
        if self._history_t0 is None:
            self._history_t0 = timestamp
        self._signal_history["time"].append(timestamp)
        for key in self._signal_history:
            if key == "time":
                continue
            self._signal_history[key].append(self._numeric_or_nan(row.get(key)))
        self._last_sample_timestamp = timestamp
        self._last_sample_valid = all(
            math.isfinite(self._signal_history[key][-1])
            for key in self._signal_history
            if key != "time"
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
        if len(self._hv_command_history["time"]) > MAX_PLOT_SAMPLES:
            for values in self._hv_command_history.values():
                del values[:-MAX_PLOT_SAMPLES]
        self._schedule_plot_draw()

    @staticmethod
    def _numeric_or_nan(value: Any) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return float("nan")
        return numeric if math.isfinite(numeric) else float("nan")

    def _update_latest_sample_values(self, row: dict[str, Any]) -> None:
        reference = self.config["reference"]
        safety = self.config["safety"]
        hv_setpoint = self._numeric_or_nan(row.get("hv_setpoint"))
        hv_readback = self._numeric_or_nan(row.get("hv_readback"))
        acc1_amp = self._numeric_or_nan(row.get("acc1_amp"))
        buncher_amp = self._numeric_or_nan(row.get("buncher_amp"))
        acc1_phase = self._numeric_or_nan(row.get("acc1_phase"))
        buncher_phase = self._numeric_or_nan(row.get("buncher_phase"))

        hv_min = float(safety["hv_min_kv"])
        hv_max = float(safety["hv_max_kv"])
        hv_setpoint_tone = (
            "danger"
            if math.isfinite(hv_setpoint) and not hv_min <= hv_setpoint <= hv_max
            else "subtle"
        )
        hv_readback_tone = (
            "danger"
            if math.isfinite(hv_readback) and not hv_min <= hv_readback <= hv_max
            else "subtle"
        )
        self._set_metric(
            "hv_setpoint",
            f"{hv_setpoint:.6g} kV" if math.isfinite(hv_setpoint) else "INVALID",
            hv_setpoint_tone,
        )
        self._set_metric(
            "hv_readback",
            f"{hv_readback:.6g} kV" if math.isfinite(hv_readback) else "INVALID",
            hv_readback_tone,
        )

        if math.isfinite(hv_setpoint) and math.isfinite(hv_readback):
            mismatch = hv_readback - hv_setpoint
            mismatch_limit = float(safety["hv_readback_tolerance_kv"])
            self._set_metric(
                "hv_mismatch",
                f"{mismatch:+.6g} kV",
                self._threshold_tone(abs(mismatch), mismatch_limit),
            )
        else:
            self._set_metric("hv_mismatch", "INVALID", "danger")

        acc1_ref = float(reference["acc1_amp_ref"])
        if math.isfinite(acc1_amp) and acc1_ref > 0.0:
            acc1_rel = acc1_amp / acc1_ref
            acc1_tone = self._range_tone(
                acc1_rel,
                float(safety["acc1_amp_min_rel"]),
                float(safety["acc1_amp_max_rel"]),
            )
            self._set_metric("acc1_level", f"{acc1_rel * 100.0:.3f}% of ref", acc1_tone)
        else:
            self._set_metric("acc1_level", "INVALID", "danger")

        ratio_ref = float(reference["amp_ratio_ref"])
        if (
            math.isfinite(acc1_amp)
            and math.isfinite(buncher_amp)
            and acc1_amp != 0.0
            and ratio_ref != 0.0
        ):
            ratio_error = (buncher_amp / acc1_amp / ratio_ref) - 1.0
            self._set_metric(
                "amp_ratio_error",
                f"{ratio_error * 100.0:+.3f}%",
                self._threshold_tone(
                    abs(ratio_error), float(safety["amp_ratio_limit_rel"])
                ),
            )
        else:
            self._set_metric("amp_ratio_error", "INVALID", "danger")

        if math.isfinite(acc1_phase) and math.isfinite(buncher_phase):
            acc1_error = phase_diff_deg(acc1_phase, float(reference["acc1_phase_ref"]))
            buncher_error = phase_diff_deg(
                buncher_phase, float(reference["buncher_phase_ref"])
            )
            phase_tone = max(
                (
                    self._threshold_tone(
                        abs(acc1_error), float(safety["acc1_phase_limit_deg"])
                    ),
                    self._threshold_tone(
                        abs(buncher_error), float(safety["buncher_phase_limit_deg"])
                    ),
                ),
                key=self._tone_rank,
            )
            self._set_metric(
                "phase_error",
                f"{acc1_error:+.3f}° / {buncher_error:+.3f}°",
                phase_tone,
            )
        else:
            self._set_metric("phase_error", "INVALID", "danger")

    def _set_metric(self, key: str, text: str, tone: str) -> None:
        label = self._value_labels[key]
        label.setText(text)
        label.setProperty("tone", tone)
        label.style().unpolish(label)
        label.style().polish(label)

    @staticmethod
    def _tone_rank(tone: str) -> int:
        return {"subtle": 0, "warning": 1, "danger": 2}.get(tone, 0)

    @staticmethod
    def _threshold_tone(value: float, limit: float) -> str:
        if limit <= 0.0 or value > limit:
            return "danger"
        if value >= 0.8 * limit:
            return "warning"
        return "subtle"

    @staticmethod
    def _range_tone(value: float, minimum: float, maximum: float) -> str:
        if value < minimum or value > maximum:
            return "danger"
        if value < 1.0:
            warning_boundary = 1.0 + 0.8 * (minimum - 1.0)
            return "warning" if value <= warning_boundary else "subtle"
        warning_boundary = 1.0 + 0.8 * (maximum - 1.0)
        return "warning" if value >= warning_boundary else "subtle"

    def _reset_monitor_display(self) -> None:
        for values in self._signal_history.values():
            values.clear()
        for values in self._hv_command_history.values():
            values.clear()
        self._history_t0 = None
        self._last_sample_timestamp = None
        self._last_sample_valid = None
        placeholders = {
            "hv_setpoint": "— kV",
            "hv_readback": "— kV",
            "hv_mismatch": "— kV",
            "acc1_level": "— % of ref",
            "amp_ratio_error": "— %",
            "phase_error": "— / — deg",
        }
        for key, placeholder in placeholders.items():
            self._set_metric(key, placeholder, "subtle")
        self._update_sample_freshness()
        self._draw_plots()

    def _update_sample_freshness(self) -> None:
        if not hasattr(self, "sample_freshness_label"):
            return
        tone = "subtle"
        if self._last_sample_timestamp is None:
            text = (
                "Waiting for first sample"
                if self._operation != "stopped"
                else "Waiting for acquisition"
            )
        elif self._last_sample_valid is False:
            stamp = datetime.fromtimestamp(self._last_sample_timestamp).strftime("%H:%M:%S")
            text = (
                "Latest sample invalid"
                if self._operation != "stopped"
                else f"Last sample invalid · {stamp}"
            )
            tone = "danger"
        elif self._operation == "stopped":
            stamp = datetime.fromtimestamp(self._last_sample_timestamp).strftime("%H:%M:%S")
            text = f"Last sample · {stamp}"
        else:
            age_s = max(0.0, time.time() - self._last_sample_timestamp)
            text = f"Updated {age_s:.1f} s ago"
            stale_after_s = max(
                2.0,
                3.0 * float(self.config["control"]["sample_period_s"]),
            )
            if age_s > stale_after_s:
                tone = "warning"
                text = f"Sample stale · {age_s:.1f} s ago"
        self.sample_freshness_label.setText(text)
        self.sample_freshness_label.setProperty("tone", tone)
        self.sample_freshness_label.style().unpolish(self.sample_freshness_label)
        self.sample_freshness_label.style().polish(self.sample_freshness_label)

    def _schedule_plot_draw(self) -> None:
        if self._plot_redraw_pending or bool(self.plot_toolbar.mode):
            return
        elapsed_ms = (time.monotonic() - self._last_plot_draw_monotonic) * 1000.0
        delay_ms = max(0, int(PLOT_REDRAW_INTERVAL_MS - elapsed_ms))
        self._plot_redraw_pending = True
        QTimer.singleShot(delay_ms, self._flush_scheduled_plot_draw)

    @pyqtSlot()
    def _flush_scheduled_plot_draw(self) -> None:
        self._plot_redraw_pending = False
        self._draw_plots()

    def _plot_control_changed(self, *_args: Any) -> None:
        self._draw_plots()

    def _plot_navigation_changed(self, active: bool) -> None:
        if not active:
            self._schedule_plot_draw()

    @pyqtSlot(str, str)
    def _set_session_state(self, state: str, tone: str) -> None:
        self.status_panel.set_value("state", state, tone)

    @pyqtSlot(str)
    def _set_log_path(self, path: str) -> None:
        self.status_panel.set_value("log", Path(path).name)
        container, label = self.status_panel._items["log"]
        container.setToolTip(path)
        label.setToolTip(path)

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
    def _reference_measured(self, values: dict[str, float]) -> None:
        for key, value in values.items():
            self._parameter_spins["reference"][key].setValue(float(value))
        self.config["reference"].update(values)
        self._reset_monitor_display()
        self._set_message(
            "Reference measurement completed. Save a runtime snapshot if these values should be reused."
        )

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
        self._set_message(f"Saved runtime snapshot: {path}")

    def load_snapshot(self) -> None:
        snapshots_dir = resolve_hv_feedback_runtime_paths(self.app_context)["snapshots_dir"]
        if not snapshots_dir.is_dir():
            QMessageBox.information(self, "Load Snapshot", "No runtime snapshots have been saved yet.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load HV Feedback Snapshot",
            str(snapshots_dir),
            "JSON snapshots (*.json)",
        )
        if not path:
            return
        try:
            config = load_runtime_snapshot(self.app_context, path, self.base_config)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Load Snapshot Failed", str(exc))
            return
        self.config = config
        self._apply_config_to_ui(config)
        self._reset_monitor_display()
        self._set_message(f"Loaded runtime snapshot: {path}")

    def _set_busy(self, busy: bool) -> None:
        self.start_monitor_button.setEnabled(not busy)
        self.start_feedback_button.setEnabled(not busy)
        self.measure_reference_button.setEnabled(not busy)
        self.stop_button.setEnabled(busy)
        self.save_snapshot_button.setEnabled(not busy)
        self.load_snapshot_button.setEnabled(not busy)
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
        self._style_plot(palette)

    def _style_plot(self, palette: dict[str, str]) -> None:
        self.figure.patch.set_facecolor(palette["plot_card_bg"])
        for axis in (self.amp_axis, self.phase_axis, self.hv_axis):
            axis.set_facecolor(palette["plot_bg"])
            axis.tick_params(colors=palette["plot_text"], labelsize=8)
            axis.xaxis.label.set_color(palette["plot_text"])
            axis.yaxis.label.set_color(palette["plot_text"])
            axis.title.set_color(palette["plot_text"])
            for spine in axis.spines.values():
                spine.set_color(palette["plot_spine"])
            axis.grid(True, color=palette["plot_grid"], alpha=0.75, linestyle="--")
        self._draw_plots()

    def _draw_plots(self) -> None:
        palette = DARK_THEME if self.current_theme == "dark" else LIGHT_THEME
        all_times = self._signal_history["time"]
        window_seconds = self.plot_window_combo.currentData()
        start_index = 0
        if all_times and window_seconds is not None:
            cutoff = all_times[-1] - float(window_seconds)
            start_index = next(
                (index for index, timestamp in enumerate(all_times) if timestamp >= cutoff),
                len(all_times),
            )
        times = all_times[start_index:]
        visible_signals = {
            key: values[start_index:]
            for key, values in self._signal_history.items()
            if key != "time"
        }

        command_start = 0
        command_times = self._hv_command_history["time"]
        if command_times and window_seconds is not None and all_times:
            cutoff = all_times[-1] - float(window_seconds)
            command_start = next(
                (
                    index
                    for index, timestamp in enumerate(command_times)
                    if timestamp >= cutoff
                ),
                len(command_times),
            )
        visible_command_times = command_times[command_start:]
        visible_commands = self._hv_command_history["hv_next"][command_start:]

        for axis in (self.amp_axis, self.phase_axis, self.hv_axis):
            axis.clear()
            axis.set_facecolor(palette["plot_bg"])
            axis.tick_params(colors=palette["plot_text"], labelsize=8)
            for spine in axis.spines.values():
                spine.set_color(palette["plot_spine"])
            axis.grid(True, color=palette["plot_grid"], alpha=0.75, linestyle="--")

        relative = self.plot_scale_combo.currentText() == "Relative"
        clock_axis = self.plot_time_axis_combo.currentText() == "Clock"
        if clock_axis:
            x = [datetime.fromtimestamp(timestamp) for timestamp in times]
            command_x = [
                datetime.fromtimestamp(timestamp) for timestamp in visible_command_times
            ]
        else:
            t0 = self._history_t0 if self._history_t0 is not None else 0.0
            x = [(timestamp - t0) / 60.0 for timestamp in times]
            command_x = [
                (timestamp - t0) / 60.0 for timestamp in visible_command_times
            ]

        if relative:
            reference = self.config["reference"]
            acc1_ref = float(reference["acc1_amp_ref"])
            ratio_ref = float(reference["amp_ratio_ref"])
            hv0 = float(reference["hv0"])
            acc1_values = visible_signals.get("acc1_amp", [])
            buncher_values = visible_signals.get("buncher_amp", [])
            acc1_plot = [
                ((value / acc1_ref) - 1.0) * 100.0
                if math.isfinite(value) and acc1_ref != 0.0
                else float("nan")
                for value in acc1_values
            ]
            ratio_plot = [
                (((buncher / acc1) / ratio_ref) - 1.0) * 100.0
                if math.isfinite(acc1)
                and math.isfinite(buncher)
                and acc1 != 0.0
                and ratio_ref != 0.0
                else float("nan")
                for acc1, buncher in zip(acc1_values, buncher_values)
            ]
            acc1_phase_plot = [
                phase_diff_deg(value, float(reference["acc1_phase_ref"]))
                if math.isfinite(value)
                else float("nan")
                for value in visible_signals.get("acc1_phase", [])
            ]
            buncher_phase_plot = [
                phase_diff_deg(value, float(reference["buncher_phase_ref"]))
                if math.isfinite(value)
                else float("nan")
                for value in visible_signals.get("buncher_phase", [])
            ]
            hv_setpoint_plot = [
                (value - hv0) * 1000.0 if math.isfinite(value) else float("nan")
                for value in visible_signals.get("hv_setpoint", [])
            ]
            hv_readback_plot = [
                (value - hv0) * 1000.0 if math.isfinite(value) else float("nan")
                for value in visible_signals.get("hv_readback", [])
            ]
            hv_command_plot = [(value - hv0) * 1000.0 for value in visible_commands]
            self.amp_axis.set_title(
                "Amplitude Error from Reference",
                color=palette["plot_text"],
                fontsize=10,
            )
            self.phase_axis.set_title(
                "Phase Error from Reference",
                color=palette["plot_text"],
                fontsize=10,
            )
            self.hv_axis.set_title(
                "High Voltage Offset from Reference",
                color=palette["plot_text"],
                fontsize=10,
            )
            self.amp_axis.set_ylabel("Error (%)", color=palette["plot_text"])
            self.phase_axis.set_ylabel("Error (deg)", color=palette["plot_text"])
            self.hv_axis.set_ylabel("HV - HV0 (V)", color=palette["plot_text"])
            for axis in (self.amp_axis, self.phase_axis, self.hv_axis):
                axis.axhline(
                    0.0,
                    color=palette["plot_spine"],
                    linewidth=0.9,
                    alpha=0.8,
                )
        else:
            acc1_plot = visible_signals.get("acc1_amp", [])
            ratio_plot = visible_signals.get("buncher_amp", [])
            acc1_phase_plot = visible_signals.get("acc1_phase", [])
            buncher_phase_plot = visible_signals.get("buncher_phase", [])
            hv_setpoint_plot = visible_signals.get("hv_setpoint", [])
            hv_readback_plot = visible_signals.get("hv_readback", [])
            hv_command_plot = visible_commands
            self.amp_axis.set_title("Amplitude", color=palette["plot_text"], fontsize=10)
            self.phase_axis.set_title("Phase", color=palette["plot_text"], fontsize=10)
            self.hv_axis.set_title("High Voltage", color=palette["plot_text"], fontsize=10)
            self.amp_axis.set_ylabel("Amplitude (a.u.)", color=palette["plot_text"])
            self.phase_axis.set_ylabel("Phase (deg)", color=palette["plot_text"])
            self.hv_axis.set_ylabel("HV (kV)", color=palette["plot_text"])

        if x:
            self.amp_axis.plot(
                x,
                acc1_plot,
                label="ACC1 level" if relative else "ACC1",
                color="#45d0bc",
            )
            self.amp_axis.plot(
                x,
                ratio_plot,
                label="Amp ratio" if relative else "Buncher",
                color="#e4b86f",
            )
            self.phase_axis.plot(x, acc1_phase_plot, label="ACC1", color="#79a9f5")
            self.phase_axis.plot(
                x,
                buncher_phase_plot,
                label="Buncher",
                color="#d68ae8",
            )
            self.hv_axis.plot(
                x,
                hv_setpoint_plot,
                label="Setpoint",
                color="#e37878",
            )
            self.hv_axis.plot(
                x,
                hv_readback_plot,
                label="Readback",
                color="#7dd7c5",
            )
        if command_x:
            self.hv_axis.plot(
                command_x,
                hv_command_plot,
                label=(
                    "Written target"
                    if self._operation == "feedback"
                    else "Computed target"
                ),
                color="#f0b45a",
                linewidth=1.2,
                marker="o",
                markersize=3,
                drawstyle="steps-post",
            )

        for axis in (self.amp_axis, self.phase_axis, self.hv_axis):
            handles, labels = axis.get_legend_handles_labels()
            if handles:
                legend = axis.legend(
                    handles,
                    labels,
                    loc="best",
                    fontsize=8,
                    frameon=False,
                )
                if legend is not None:
                    for label in legend.get_texts():
                        label.set_color(palette["plot_text"])
        self.amp_axis.tick_params(labelbottom=False)
        self.phase_axis.tick_params(labelbottom=False)
        if clock_axis:
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
