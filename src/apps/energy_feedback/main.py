from __future__ import annotations

import math
import queue
import sys
import threading
import time
from bisect import bisect_left
from collections import defaultdict, deque
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

_REPO_BOOTSTRAP_ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "repo_bootstrap.py").is_file()
)
if str(_REPO_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_BOOTSTRAP_ROOT))

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

from matplotlib import dates as mdates
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.ticker import AutoLocator, ScalarFormatter
from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from half_linac.src.apps.energy_feedback.client import EpicsPVClient
from half_linac.src.apps.energy_feedback.cyclic import cyclic_delta
from half_linac.src.apps.energy_feedback.csv_logger import EnergyFeedbackCsvLogger
from half_linac.src.apps.energy_feedback.engine import (
    configure_control_group,
    evaluate_shadow,
    unavailable_decision,
)
from half_linac.src.apps.energy_feedback.models import FeedbackDecision, PVReading
from half_linac.src.apps.energy_feedback.profile_runtime import (
    WORKFLOW_NAME,
    build_measurement_provider,
    load_runtime_config,
)
from half_linac.src.apps.energy_feedback.safety import FeedbackSafetyStateMachine
from half_linac.src.apps.energy_feedback.writer import (
    apply_feedback_decision,
    apply_manual_targets,
)
from half_linac.src.shared.machine_profile import (
    AppContext,
    EnergyControlLock,
    RuntimeContextWidget,
    load_app_context,
    require_workflow_write_allowed,
)
from half_linac.src.shared.window_activation import install_qt_window_raise_handler


PLOT_RANGE_SECONDS = {
    "10 min": 10 * 60,
    "30 min": 30 * 60,
    "1 h": 60 * 60,
    "All": None,
}
MAX_RENDERED_PLOT_POINTS = 1600


DARK_THEME = {
    "window": "#0f1519",
    "text": "#e6edf2",
    "summary": "#1b262d",
    "panel": "#172027",
    "section": "#11191f",
    "border": "#2a3943",
    "muted": "#8ea0ad",
    "metric": "#86e8f2",
    "success": "#45d0bc",
    "warning": "#e4b86f",
    "danger": "#ef8a7e",
    "info": "#8bc5ff",
    "button_hover": "#18242c",
    "mpl": {
        "figure": "#11181e",
        "axes": "#172027",
        "text": "#a8b8c3",
        "grid": "#2a3943",
        "spine": "#445764",
        "energy": "#6cb6ff",
        "target": "#e4b86f",
        "error": "#ef8a7e",
        "phase1": "#f5b971",
        "phase2": "#ff938a",
        "deadband": "#45d0bc",
        "action_window": "#e4b86f",
        "event_applied": "#8bc5ff",
        "event_hold": "#ef8a7e",
        "event_recovered": "#45d0bc",
        "legend": "#1a252c",
    },
}


LIGHT_THEME = {
    "window": "#f2ede5",
    "text": "#2c3942",
    "summary": "#fcf9f3",
    "panel": "#fffdf9",
    "section": "#f8f3eb",
    "border": "#ddd4c8",
    "muted": "#7c7368",
    "metric": "#1f73c9",
    "success": "#166534",
    "warning": "#a65b00",
    "danger": "#b91c1c",
    "info": "#1d4ed8",
    "button_hover": "#efe6d9",
    "mpl": {
        "figure": "#f6f1e8",
        "axes": "#fffdf8",
        "text": "#52636d",
        "grid": "#ddd4c7",
        "spine": "#b5aa9a",
        "energy": "#2f7dc5",
        "target": "#a97118",
        "error": "#b84c5f",
        "phase1": "#ca6c2a",
        "phase2": "#b84c5f",
        "deadband": "#166534",
        "action_window": "#a65b00",
        "event_applied": "#1d4ed8",
        "event_hold": "#b91c1c",
        "event_recovered": "#166534",
        "legend": "#f5efe5",
    },
}


class GuardedDoubleSpinBox(QDoubleSpinBox):
    """Editable numeric field without accidental step-button or wheel changes."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.setButtonSymbols(QDoubleSpinBox.NoButtons)

    def wheelEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        event.ignore()


class AcquisitionThread(QThread):
    sample_ready = pyqtSignal(object, object, object)
    startup_failed = pyqtSignal(str)
    feedback_state = pyqtSignal(bool, str)
    manual_result = pyqtSignal(object)

    def __init__(self, context: AppContext, config: Mapping[str, Any]) -> None:
        super().__init__()
        self.context = context
        self.config = config
        self._stop_event = threading.Event()
        self._feedback_event = threading.Event()
        self._settings_lock = threading.Lock()
        self._manual_requests: queue.Queue[dict[str, float]] = queue.Queue()

        groups = tuple(config["control_groups"])
        group = groups[0] if groups else None
        ratio_config = config.get("operator_controls", {}).get(
            "actuator_ratio", {}
        )
        ratio_actuator_id = (
            str(ratio_config.get("actuator", group.actuators[-1].id))
            if group is not None
            else ""
        )
        ratio_actuator = (
            next(
                actuator
                for actuator in group.actuators
                if actuator.id == ratio_actuator_id
            )
            if group is not None
            else None
        )
        self._settings: dict[str, float | None] = {
            "target_mev": None,
            "loop_gain": group.loop_gain if group is not None else 0.1,
            "ratio": abs(ratio_actuator.coefficient) if ratio_actuator else 1.0,
            "max_knob_step": group.max_knob_step if group is not None else 0.3,
            "action_window_mev": (
                group.action_window_mev if group is not None else 5.0
            ),
            "max_session_delta": (
                min(actuator.max_session_delta for actuator in group.actuators)
                if group is not None
                else 3.0
            ),
        }

    def stop(self) -> None:
        self._feedback_event.clear()
        self._stop_event.set()

    def set_feedback_enabled(self, enabled: bool) -> None:
        if enabled:
            self._feedback_event.set()
        else:
            self._feedback_event.clear()

    def set_control_parameters(self, parameters: Mapping[str, float]) -> None:
        with self._settings_lock:
            for key in self._settings:
                if key in parameters:
                    value = float(parameters[key])
                    if not math.isfinite(value):
                        raise ValueError(f"{key} must be finite")
                    self._settings[key] = value

    def request_manual_targets(self, targets: Mapping[str, float]) -> None:
        self._manual_requests.put({key: float(value) for key, value in targets.items()})

    def _settings_snapshot(self) -> dict[str, float]:
        with self._settings_lock:
            return {
                key: float(value)
                for key, value in self._settings.items()
                if value is not None
            }

    def _configured_group(self, settings: Mapping[str, float]):
        groups = tuple(self.config["control_groups"])
        if not groups:
            return None
        group = groups[0]
        ratio = self.config.get("operator_controls", {}).get(
            "actuator_ratio",
            {"actuator": group.actuators[-1].id, "sign": -1},
        )
        return configure_control_group(
            group,
            loop_gain=settings["loop_gain"],
            ratio_actuator_id=str(ratio["actuator"]),
            ratio=settings["ratio"],
            ratio_sign=float(ratio["sign"]),
            max_knob_step=settings["max_knob_step"],
            action_window_mev=settings["action_window_mev"],
            max_session_delta=settings["max_session_delta"],
        )

    def run(self) -> None:
        try:
            writable_keys = tuple(
                actuator.setpoint_key
                for group in self.config["control_groups"]
                for actuator in group.actuators
            )
            client = EpicsPVClient(
                self.config["pvs"],
                connection_timeout_s=float(
                    self.config["runtime"]["connection_timeout_s"]
                ),
                writable_keys=writable_keys if self.config["write_enabled"] else (),
            )
            provider = build_measurement_provider(self.config, client)
        except Exception as exc:  # noqa: BLE001 - reported as disconnected
            self.startup_failed.emit(str(exc))
            return

        sample_period = float(self.config["runtime"]["sample_period_s"])
        stale_timeout = float(self.config["runtime"]["stale_timeout_s"])
        verification = self.config["write_verification"]
        groups = tuple(self.config["control_groups"])
        key_map = (
            self.config["group_readback_keys"][groups[0].id] if groups else {}
        )
        origins: dict[str, float] = {}
        last_pulse_token: object | None = None
        feedback_active = False
        control_lock: EnergyControlLock | None = None

        try:
            while not self._stop_event.is_set():
                settings = self._settings_snapshot()
                group = self._configured_group(settings)
                measurement = provider.sample()
                target_override = settings.get("target_mev")
                if (
                    target_override is None
                    and measurement.quality.valid
                    and measurement.energy_mev is not None
                    and math.isfinite(measurement.energy_mev)
                ):
                    initial_target = (
                        measurement.target_mev
                        if measurement.target_mev is not None
                        and math.isfinite(measurement.target_mev)
                        else measurement.energy_mev
                    )
                    with self._settings_lock:
                        if self._settings["target_mev"] is None:
                            self._settings["target_mev"] = float(initial_target)
                        target_override = self._settings["target_mev"]
                    settings = dict(settings)
                    settings["target_mev"] = target_override
                if (
                    target_override is not None
                    and measurement.energy_mev is not None
                    and measurement.quality.valid
                ):
                    measurement = replace(
                        measurement,
                        target_source_timestamp=None,
                        target_mev=target_override,
                        error_mev=measurement.energy_mev - target_override,
                    )

                if group is None:
                    decision = replace(
                        unavailable_decision(),
                        error_mev=measurement.error_mev,
                        control_parameters=settings,
                    )
                    self.sample_ready.emit(measurement, decision, {})
                    self._stop_event.wait(sample_period)
                    continue

                readbacks: dict[str, PVReading] = {}
                try:
                    raw = client.read_many(key_map.values())
                    readbacks = {
                        actuator_id: raw[key]
                        for actuator_id, key in key_map.items()
                    }
                except Exception as exc:  # noqa: BLE001
                    decision = FeedbackDecision(
                        "HOLD",
                        f"actuator read failure: {exc}",
                        measurement.error_mev,
                        control_parameters=settings,
                    )
                else:
                    requested = self._feedback_event.is_set()
                    if requested and not feedback_active:
                        try:
                            require_workflow_write_allowed(
                                self.context,
                                WORKFLOW_NAME,
                                "Energy feedback",
                            )
                            control_lock = EnergyControlLock.for_machine(
                                self.config["machine_id"],
                                {
                                    "app": WORKFLOW_NAME,
                                    "operation": "closed_loop_feedback",
                                },
                            )
                            control_lock.acquire()
                        except Exception as exc:  # noqa: BLE001
                            self._feedback_event.clear()
                            self.feedback_state.emit(False, str(exc))
                        else:
                            feedback_active = True
                            origins.clear()
                            last_pulse_token = None
                            self.feedback_state.emit(True, "Feedback enabled")
                    elif not requested and feedback_active:
                        feedback_active = False
                        origins.clear()
                        last_pulse_token = None
                        if control_lock is not None:
                            control_lock.release()
                            control_lock = None
                        self.feedback_state.emit(False, "Feedback stopped")

                    if feedback_active:
                        for actuator_id, reading in readbacks.items():
                            if (
                                actuator_id not in origins
                                and reading.connected
                                and reading.value is not None
                                and math.isfinite(reading.value)
                            ):
                                origins[actuator_id] = float(reading.value)

                    try:
                        decision = evaluate_shadow(
                            measurement,
                            group,
                            readbacks,
                            session_origins=origins if feedback_active else None,
                            stale_timeout_s=stale_timeout,
                        )
                    except Exception as exc:  # noqa: BLE001
                        decision = FeedbackDecision(
                            "HOLD",
                            f"feedback evaluation failure: {exc}",
                            measurement.error_mev,
                        )
                    decision = replace(decision, control_parameters=settings)

                    if feedback_active and decision.has_valid_suggestion:
                        token = self._pulse_token(measurement)
                        if token is None:
                            decision = replace(
                                decision,
                                state="HOLD",
                                reason="measurement pulse timestamp unavailable",
                                commands=(),
                            )
                        elif token == last_pulse_token:
                            decision = replace(
                                decision,
                                state="HOLD",
                                reason="waiting for a new measurement pulse",
                                commands=(),
                            )
                        else:
                            last_pulse_token = token
                            decision = apply_feedback_decision(
                                client,
                                group,
                                decision,
                                timeout_s=float(verification["timeout_s"]),
                                poll_interval_s=float(
                                    verification["poll_interval_s"]
                                ),
                            )
                            if decision.write_detail and not decision.write_verified:
                                self._feedback_event.clear()
                                feedback_active = False
                                if control_lock is not None:
                                    control_lock.release()
                                    control_lock = None
                                self.feedback_state.emit(
                                    False,
                                    f"Feedback stopped after write failure: {decision.write_detail}",
                                )

                    if not feedback_active:
                        manual_decision = self._process_manual_request(
                            client,
                            group,
                            readbacks,
                            verification,
                        )
                        if manual_decision is not None:
                            decision = replace(
                                manual_decision,
                                error_mev=measurement.error_mev,
                                control_parameters=settings,
                            )

                self.sample_ready.emit(measurement, decision, readbacks)
                self._stop_event.wait(sample_period)
        finally:
            if control_lock is not None:
                control_lock.release()

    def _pulse_token(self, measurement) -> object | None:
        if self.config["require_marker_for_write"]:
            if (
                measurement.marker_value is None
                or measurement.marker_source_timestamp is None
            ):
                return None
            return (measurement.marker_value, measurement.marker_source_timestamp)
        return measurement.source_timestamp

    def _process_manual_request(
        self,
        client,
        group,
        readbacks: Mapping[str, PVReading],
        verification: Mapping[str, Any],
    ) -> FeedbackDecision | None:
        try:
            targets = self._manual_requests.get_nowait()
        except queue.Empty:
            return None
        control_lock = EnergyControlLock.for_machine(
            self.config["machine_id"],
            {"app": WORKFLOW_NAME, "operation": "manual_phase_write"},
        )
        try:
            require_workflow_write_allowed(
                self.context,
                WORKFLOW_NAME,
                "Manual phase write",
            )
            control_lock.acquire()
            result = apply_manual_targets(
                client,
                group,
                readbacks,
                targets,
                timeout_s=float(verification["timeout_s"]),
                poll_interval_s=float(verification["poll_interval_s"]),
            )
        except Exception as exc:  # noqa: BLE001
            result = FeedbackDecision("HOLD", f"manual phase write failed: {exc}", None)
        finally:
            control_lock.release()
        self.manual_result.emit(result)
        return result


class EnergyFeedbackWindow(QMainWindow):
    def __init__(self, context: AppContext, *, auto_start: bool = True) -> None:
        super().__init__()
        self.context = context
        self.config = load_runtime_config(context)
        self.safety = FeedbackSafetyStateMachine(
            has_control_group=bool(self.config["control_groups"]),
            write_enabled=bool(self.config["write_enabled"]),
        )
        self.worker: AcquisitionThread | None = None
        self.logger: EnergyFeedbackCsvLogger | None = None
        self._last_plot_at = 0.0
        self._history_limit = int(self.config["runtime"]["max_history_points"])
        self._history: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=self._history_limit)
        )
        self._latest_measurement = None
        self._latest_readbacks: dict[str, PVReading] = {}
        self._initial_phases: dict[str, float] = {}
        self._target_initialized = False
        self._feedback_active = False
        self._plot_enabled = True
        self._last_plot_state: str | None = None
        self._status_items: dict[str, tuple[QFrame, QLabel]] = {}
        self._phase_controls: dict[str, tuple[QLabel, QDoubleSpinBox, QPushButton]] = {}
        self.current_theme = "dark"

        self.setWindowTitle("Energy Stabilizer")
        self.resize(1500, 880)
        self._build_ui()
        self._apply_initial_state()
        if auto_start:
            QTimer.singleShot(0, self.start_acquisition)

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("centralRoot")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(10)

        summary = QFrame(root)
        summary.setObjectName("summaryPanel")
        summary_layout = QVBoxLayout(summary)
        summary_layout.setContentsMargins(14, 10, 14, 10)
        summary_layout.setSpacing(7)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(12)
        title = QLabel("Energy Stabilizer")
        title.setObjectName("summaryTitle")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(
            RuntimeContextWidget(
                machine_id=self.context.profile.machine.id,
                machine_display_name=self.config["machine_display_name"],
                control_backend=self.config["backend"],
                parent=summary,
            )
        )
        self.theme_toggle_button = QToolButton()
        self.theme_toggle_button.setObjectName("themeToggleButton")
        self.theme_toggle_button.setFixedSize(32, 32)
        self.theme_toggle_button.clicked.connect(self._toggle_theme)
        header.addWidget(self.theme_toggle_button)
        summary_layout.addLayout(header)

        status_strip = QFrame()
        status_strip.setObjectName("statusStrip")
        status_layout = QHBoxLayout(status_strip)
        status_layout.setContentsMargins(0, 4, 0, 4)
        status_layout.setSpacing(0)
        self.data_status_value = self._add_status_item(
            status_layout, "data", "DATA", "Not checked"
        )
        self.loop_status_value = self._add_status_item(
            status_layout, "loop", "LOOP", "OFF"
        )
        self.phase_summary_value = self._add_status_item(
            status_layout, "phase", "PHASES", "Not checked"
        )
        self.last_action_value = self._add_status_item(
            status_layout, "action", "LAST ACTION", "Ready"
        )
        self.write_value = self._add_status_item(
            status_layout,
            "access",
            "ACCESS",
            "WRITE ENABLED" if self.config["write_enabled"] else "READ ONLY",
        )
        status_layout.addStretch(1)
        summary_layout.addWidget(status_strip)
        outer.addWidget(summary)

        body = QFrame()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(10)

        left_column = QWidget()
        left_layout = QVBoxLayout(left_column)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)
        plot_group = QGroupBox("Figure")
        plot_group.setProperty("themeCard", True)
        plot_group.setMinimumHeight(480)
        plot_layout = QVBoxLayout(plot_group)
        plot_layout.setContentsMargins(10, 32, 10, 10)
        self.figure = Figure(figsize=(9, 5), tight_layout=True)
        self.canvas = FigureCanvas(self.figure)
        self.energy_axis = None
        self.error_axis = None
        self.actuator_axis = None
        self._rebuild_plot_axes("Feedback")
        plot_layout.addWidget(self.canvas, 1)
        left_layout.addWidget(plot_group, 1)

        metrics_row = QHBoxLayout()
        metrics_row.setSpacing(10)
        energy_group = QGroupBox("Energy Tracking")
        energy_group.setProperty("themeCard", True)
        energy_layout = QHBoxLayout(energy_group)
        energy_layout.setContentsMargins(10, 30, 10, 8)
        self.energy_metric = self._metric_field(
            energy_layout, "Measured Energy", "— MeV"
        )
        self.target_metric = self._metric_field(energy_layout, "Target", "— MeV")
        self.error_metric = self._metric_field(
            energy_layout, "Feedback Error", "— MeV"
        )
        metrics_row.addWidget(energy_group, 1)
        left_layout.addLayout(metrics_row)

        right_column = QWidget()
        right_column.setMinimumWidth(400)
        right_column.setMaximumWidth(470)
        right_layout = QVBoxLayout(right_column)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)

        operation_group = QGroupBox("Control")
        operation_group.setProperty("themeCard", True)
        operation_layout = QVBoxLayout(operation_group)
        operation_layout.setContentsMargins(12, 32, 12, 10)
        operation_layout.setSpacing(7)

        acquisition_card, acquisition_grid = self._create_section_card(
            "Acquisition", "Live PV polling and automatic CSV logging"
        )
        self.start_button = QPushButton("Start")
        self.stop_button = QPushButton("Stop")
        acquisition_grid.addWidget(self.start_button, 0, 0)
        acquisition_grid.addWidget(self.stop_button, 0, 1)
        acquisition_grid.addWidget(self._field_label("Interval"), 1, 0)
        acquisition_grid.addWidget(
            self._read_only_value(
                f"{float(self.config['runtime']['sample_period_s']):g} s"
            ),
            1,
            1,
        )
        acquisition_grid.addWidget(self._field_label("Energy Source"), 2, 0)
        acquisition_grid.addWidget(
            self._read_only_value(self.config["measurement_label"]), 2, 1
        )
        operation_layout.addWidget(acquisition_card)

        plot_card, plot_grid = self._create_section_card(
            "Plot View", "Display controls do not affect feedback"
        )
        self.plot_toggle_button = QPushButton("Pause Plot")
        self.clear_button = QPushButton("Clear")
        plot_grid.addWidget(self.plot_toggle_button, 0, 0, 1, 4)
        plot_grid.addWidget(self.clear_button, 0, 4, 1, 2)
        self.plot_view_combo = QComboBox()
        self.plot_view_combo.addItems(("Feedback", "Error Focus", "Full Detail"))
        self.plot_range_combo = QComboBox()
        self.plot_range_combo.addItems(tuple(PLOT_RANGE_SECONDS))
        self.plot_range_combo.setCurrentText("30 min")
        self.plot_axis_combo = QComboBox()
        self.plot_axis_combo.addItems(("Index", "Elapsed", "Clock"))
        self.plot_axis_combo.setCurrentText("Elapsed")
        plot_grid.addWidget(self._field_label("View"), 1, 0)
        plot_grid.addWidget(self.plot_view_combo, 1, 1)
        plot_grid.addWidget(self._field_label("Range"), 1, 2)
        plot_grid.addWidget(self.plot_range_combo, 1, 3)
        plot_grid.addWidget(self._field_label("X"), 1, 4)
        plot_grid.addWidget(self.plot_axis_combo, 1, 5)
        operation_layout.addWidget(plot_card)

        feedback_card, feedback_grid = self._create_section_card(
            "Feedback Loop",
            "Configured actuator corrections are written only after confirmation",
        )
        self.feedback_start_button = QPushButton("Start")
        self.feedback_stop_button = QPushButton("Stop")
        feedback_grid.addWidget(self._field_label("Feedback"), 0, 0)
        feedback_grid.addWidget(self.feedback_start_button, 0, 1)
        feedback_grid.addWidget(self.feedback_stop_button, 0, 2)

        groups = tuple(self.config["control_groups"])
        group = groups[0] if groups else None
        valid_low, valid_high = self.config["valid_range_mev"]
        controls = self.config.get("operator_controls") or {
            "target": {"minimum": valid_low, "maximum": valid_high, "decimals": 3},
            "gain": {"minimum": 0.001, "maximum": 1.0, "decimals": 4},
            "actuator_ratio": {
                "label": "alpha",
                "actuator": "",
                "sign": -1,
                "minimum": 0.001,
                "maximum": 10.0,
                "decimals": 6,
            },
            "max_knob_step": {"minimum": 0.01, "maximum": 5.0, "decimals": 3},
            "action_window": {"minimum": 0.1, "maximum": 100.0, "decimals": 3},
            "max_session_delta": {"minimum": 0.1, "maximum": 30.0, "decimals": 3},
        }
        ratio_actuator_id = controls["actuator_ratio"]["actuator"]
        if group is not None:
            ratio_actuator = next(
                (
                    actuator
                    for actuator in group.actuators
                    if actuator.id == ratio_actuator_id
                ),
                group.actuators[-1],
            )
            ratio_default = abs(ratio_actuator.coefficient)
        else:
            ratio_default = 1.0
        self.target_energy_spin = self._parameter_spin(
            controls["target"], value=0.0, suffix=" MeV"
        )
        self.gain_spin = self._parameter_spin(
            controls["gain"], value=group.loop_gain if group is not None else 0.1
        )
        self.ratio_spin = self._parameter_spin(
            controls["actuator_ratio"], value=ratio_default
        )
        self.step_limit_spin = self._parameter_spin(
            controls["max_knob_step"],
            value=group.max_knob_step if group is not None else 0.3,
            suffix=" °",
        )
        self.action_window_spin = self._parameter_spin(
            controls["action_window"],
            value=group.action_window_mev if group is not None else 5.0,
            suffix=" MeV",
        )
        self.session_limit_spin = self._parameter_spin(
            controls["max_session_delta"],
            value=(
                min(actuator.max_session_delta for actuator in group.actuators)
                if group is not None
                else 3.0
            ),
            suffix=" °",
        )
        parameter_rows = (
            ("Target Energy", self.target_energy_spin),
            (str(controls["gain"].get("label", "ki")), self.gain_spin),
            (str(controls["actuator_ratio"].get("label", "alpha")), self.ratio_spin),
            ("Step limit", self.step_limit_spin),
            ("Action window", self.action_window_spin),
            ("Session limit", self.session_limit_spin),
        )
        for row, (label, spin) in enumerate(parameter_rows, start=1):
            feedback_grid.addWidget(self._field_label(label), row, 0)
            feedback_grid.addWidget(spin, row, 1, 1, 2)
        operation_layout.addWidget(feedback_card)
        right_layout.addWidget(operation_group)

        phase_group = QGroupBox("Phase Shifter")
        phase_group.setProperty("themeCard", True)
        phase_grid = QGridLayout(phase_group)
        phase_grid.setContentsMargins(12, 32, 12, 10)
        phase_grid.setHorizontalSpacing(8)
        phase_grid.setVerticalSpacing(6)
        phase_grid.addWidget(self._field_label(""), 0, 0)
        phase_grid.addWidget(self._field_label("Readback"), 0, 1)
        phase_grid.addWidget(self._field_label("Setpoint"), 0, 2)
        for row, actuator in enumerate(
            group.actuators if group is not None else (), start=1
        ):
            label = QLabel(actuator.label)
            readback = QLabel("—")
            readback.setProperty("role", "runtimeValue")
            spin = GuardedDoubleSpinBox()
            low = max(actuator.operating_range[0], actuator.machine_limit[0])
            high = min(actuator.operating_range[1], actuator.machine_limit[1])
            spin.setRange(low, high)
            spin.setDecimals(actuator.command_precision)
            spin.setSingleStep(0.1)
            spin.setSuffix(f" {actuator.unit}")
            apply_button = QPushButton("Apply")
            apply_button.clicked.connect(
                lambda _checked=False, actuator_id=actuator.id: self.apply_manual_phase(
                    actuator_id
                )
            )
            phase_grid.addWidget(label, row, 0)
            phase_grid.addWidget(readback, row, 1)
            phase_grid.addWidget(spin, row, 2)
            phase_grid.addWidget(apply_button, row, 3)
            self._phase_controls[actuator.id] = (readback, spin, apply_button)
        footer_row = len(group.actuators if group is not None else ()) + 1
        self.initial_phase_value = QLabel("Initial: —")
        self.initial_phase_value.setProperty("role", "hint")
        self.initial_phase_value.setWordWrap(True)
        self.restore_initial_button = QPushButton("Restore Initial")
        phase_grid.addWidget(self.initial_phase_value, footer_row, 0, 1, 2)
        phase_grid.addWidget(self.restore_initial_button, footer_row, 2, 1, 2)
        self.event_value = QLabel("Ready")
        self.event_value.setProperty("role", "hint")
        self.event_value.setWordWrap(True)
        phase_grid.addWidget(self.event_value, footer_row + 1, 0, 1, 4)
        right_layout.addWidget(phase_group)
        right_layout.addStretch(1)

        body_layout.addWidget(left_column, 7)
        body_layout.addWidget(right_column, 4)
        outer.addWidget(body, 1)
        self.setCentralWidget(root)

        # Familiar names from the original application are kept for operator
        # scripts and focused GUI tests.
        self.stabilizerstart = self.feedback_start_button
        self.stabilizerstop = self.feedback_stop_button
        self.targetElineEdit = self.target_energy_spin
        self.lineEdit_ki_value = self.gain_spin
        self.lineEdit_alpha_value = self.ratio_spin
        self.connection_value = self.data_status_value
        self.quality_value = self.data_status_value
        self.feedback_state_value = self.loop_status_value
        self.state_summary_value = self.last_action_value
        self.log_value = self.event_value
        self.actuator_value = self.phase_summary_value

        self.start_button.clicked.connect(self.start_acquisition)
        self.stop_button.clicked.connect(self.stop_acquisition)
        self.plot_toggle_button.clicked.connect(self.toggle_plot)
        self.clear_button.clicked.connect(self.clear_plot)
        self.feedback_start_button.clicked.connect(self.start_feedback)
        self.feedback_stop_button.clicked.connect(self.stop_feedback)
        self.restore_initial_button.clicked.connect(self.restore_initial_phases)
        self.plot_view_combo.currentTextChanged.connect(
            self._on_plot_view_changed
        )
        self.plot_range_combo.currentTextChanged.connect(self._draw_plot)
        self.plot_axis_combo.currentTextChanged.connect(self._draw_plot)
        for spin in self._parameter_spins():
            spin.editingFinished.connect(self._push_operator_parameters)
        self._apply_theme()

    def _rebuild_plot_axes(self, view: str) -> None:
        self.figure.clear()
        self.energy_axis = None
        self.error_axis = None
        self.actuator_axis = None
        if view == "Error Focus":
            self.error_axis = self.figure.add_subplot(1, 1, 1)
            return
        if view == "Full Detail":
            grid = self.figure.add_gridspec(3, 1, height_ratios=(25, 50, 25))
            self.energy_axis = self.figure.add_subplot(grid[0, 0])
            self.error_axis = self.figure.add_subplot(
                grid[1, 0], sharex=self.energy_axis
            )
            self.actuator_axis = self.figure.add_subplot(
                grid[2, 0], sharex=self.energy_axis
            )
            return
        grid = self.figure.add_gridspec(2, 1, height_ratios=(65, 35))
        self.error_axis = self.figure.add_subplot(grid[0, 0])
        self.actuator_axis = self.figure.add_subplot(
            grid[1, 0], sharex=self.error_axis
        )

    def _on_plot_view_changed(self, view: str) -> None:
        self._rebuild_plot_axes(view)
        self._draw_plot()

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("role", "field")
        return label

    @staticmethod
    def _read_only_value(text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("role", "readOnlyValue")
        label.setAlignment(Qt.AlignCenter)
        return label

    @staticmethod
    def _create_section_card(
        title: str, caption: str
    ) -> tuple[QFrame, QGridLayout]:
        frame = QFrame()
        frame.setProperty("sectionCard", True)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(3)
        heading = QLabel(title)
        heading.setProperty("role", "sectionTitle")
        layout.addWidget(heading)
        caption_label = QLabel(caption)
        caption_label.setProperty("role", "sectionCaption")
        caption_label.setWordWrap(True)
        layout.addWidget(caption_label)
        grid = QGridLayout()
        grid.setContentsMargins(0, 3, 0, 0)
        grid.setHorizontalSpacing(7)
        grid.setVerticalSpacing(5)
        layout.addLayout(grid)
        return frame, grid

    @staticmethod
    def _parameter_spin(
        spec: Mapping[str, Any], *, value: float, suffix: str = ""
    ) -> QDoubleSpinBox:
        spin = GuardedDoubleSpinBox()
        spin.setRange(float(spec["minimum"]), float(spec["maximum"]))
        spin.setDecimals(int(spec.get("decimals", 3)))
        span = float(spec["maximum"]) - float(spec["minimum"])
        spin.setSingleStep(max(10 ** (-spin.decimals()), span / 1000.0))
        spin.setValue(float(value))
        spin.setSuffix(suffix)
        return spin

    def _parameter_spins(self) -> tuple[QDoubleSpinBox, ...]:
        return (
            self.target_energy_spin,
            self.gain_spin,
            self.ratio_spin,
            self.step_limit_spin,
            self.action_window_spin,
            self.session_limit_spin,
        )

    def _operator_parameters(self) -> dict[str, float]:
        return {
            "target_mev": self.target_energy_spin.value(),
            "loop_gain": self.gain_spin.value(),
            "ratio": self.ratio_spin.value(),
            "max_knob_step": self.step_limit_spin.value(),
            "action_window_mev": self.action_window_spin.value(),
            "max_session_delta": self.session_limit_spin.value(),
        }

    def _push_operator_parameters(self) -> None:
        if self._feedback_active:
            return
        if self.worker is not None:
            self.worker.set_control_parameters(self._operator_parameters())

    def _add_status_item(
        self, layout: QHBoxLayout, key: str, title: str, value: str
    ) -> QLabel:
        container = QFrame()
        container.setProperty("statusItem", True)
        container.setProperty("tone", "subtle")
        box = QVBoxLayout(container)
        box.setContentsMargins(8, 2, 8, 2)
        box.setSpacing(1)
        heading = QLabel(title)
        heading.setProperty("role", "statusTitle")
        result = QLabel(value)
        result.setProperty("role", "statusValue")
        result.setProperty("tone", "subtle")
        result.setMinimumWidth(0)
        box.addWidget(heading)
        box.addWidget(result)
        container.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        layout.addWidget(container)
        self._status_items[key] = (container, result)
        return result

    def _set_status_item(self, key: str, value: str, tone: str = "subtle") -> None:
        item = self._status_items.get(key)
        if item is None:
            return
        container, label = item
        label.setText(value)
        label.setToolTip(value)
        container.setProperty("tone", tone)
        label.setProperty("tone", tone)
        for widget in (container, label):
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def _set_data_status(self, measurement) -> None:
        quality = measurement.quality
        if quality.valid:
            value, tone = "OK", "success"
        elif not quality.connected:
            value, tone = "Disconnected", "danger"
        elif quality.stale:
            value, tone = "Stale", "warning"
        else:
            value, tone = "Invalid", "warning"
        self._set_status_item("data", value, tone)
        self.data_status_value.setToolTip(quality.reason)

    @staticmethod
    def _metric_field(layout: QHBoxLayout, title: str, value: str) -> QLabel:
        box = QVBoxLayout()
        heading = QLabel(title)
        heading.setProperty("role", "field")
        heading.setAlignment(Qt.AlignCenter)
        metric = QLabel(value)
        metric.setProperty("role", "metricValue")
        metric.setAlignment(Qt.AlignCenter)
        box.addWidget(heading)
        box.addWidget(metric)
        layout.addLayout(box, 1)
        return metric

    def _theme(self) -> Mapping[str, Any]:
        return DARK_THEME if self.current_theme == "dark" else LIGHT_THEME

    def _toggle_theme(self) -> None:
        self.current_theme = "light" if self.current_theme == "dark" else "dark"
        self._apply_theme()

    def _apply_theme(self) -> None:
        theme = self._theme()
        app = QApplication.instance()
        if app is not None:
            app.setStyle("Fusion")
        self.setStyleSheet(self._build_theme_stylesheet(theme))
        self._apply_combo_popup_theme(theme)
        self.theme_toggle_button.setText("☀" if self.current_theme == "dark" else "☽")
        self.figure.patch.set_facecolor(theme["mpl"]["figure"])
        self.canvas.setStyleSheet(f"background: {theme['mpl']['figure']};")
        self._draw_plot()

    def _apply_combo_popup_theme(self, theme: Mapping[str, Any]) -> None:
        popup_stylesheet = f"""
        QAbstractItemView {{
            background-color: {theme['section']}; color: {theme['text']};
            border: 1px solid {theme['border']}; outline: 0;
        }}
        QAbstractItemView::item {{
            min-height: 24px; padding: 4px 8px;
            background-color: {theme['section']}; color: {theme['text']};
        }}
        QAbstractItemView::item:selected {{
            background-color: {theme['metric']}; color: {theme['window']};
        }}
        QAbstractItemView::item:disabled {{
            background-color: {theme['section']}; color: {theme['muted']};
        }}
        """
        for combo in self.findChildren(QComboBox):
            combo.view().setStyleSheet(popup_stylesheet)

    @staticmethod
    def _build_theme_stylesheet(theme: Mapping[str, Any]) -> str:
        return f"""
        QMainWindow, QWidget#centralRoot {{
            background-color: {theme['window']}; color: {theme['text']};
        }}
        QWidget {{
            color: {theme['text']};
            font-family: "IBM Plex Sans", "Source Han Sans SC", "Segoe UI", sans-serif;
            font-size: 12px;
        }}
        QLabel {{ background: transparent; border: none; }}
        QFrame#summaryPanel {{
            background-color: {theme['summary']};
            border: 1px solid {theme['border']}; border-radius: 14px;
        }}
        QLabel#summaryTitle {{ font-size: 22px; font-weight: 700; }}
        QToolButton#themeToggleButton {{
            background: {theme['section']}; border: 1px solid {theme['border']};
            border-radius: 11px; font-size: 14px; font-weight: 700;
        }}
        QToolButton#themeToggleButton:hover {{ background: {theme['button_hover']}; }}
        QFrame#statusStrip {{
            background: transparent; border: none;
        }}
        QFrame[statusItem="true"] {{
            background: transparent; border: none;
            border-left: 3px solid {theme['muted']};
        }}
        QFrame[statusItem="true"][tone="success"] {{ border-left-color: {theme['success']}; }}
        QFrame[statusItem="true"][tone="warning"] {{ border-left-color: {theme['warning']}; }}
        QFrame[statusItem="true"][tone="danger"] {{ border-left-color: {theme['danger']}; }}
        QFrame[statusItem="true"][tone="info"] {{ border-left-color: {theme['info']}; }}
        QLabel[role="statusTitle"] {{
            color: {theme['muted']}; font-size: 9px; font-weight: 700;
        }}
        QLabel[role="statusValue"] {{ font-size: 10px; font-weight: 700; }}
        QLabel[role="statusValue"][tone="success"] {{ color: {theme['success']}; }}
        QLabel[role="statusValue"][tone="warning"] {{ color: {theme['warning']}; }}
        QLabel[role="statusValue"][tone="danger"] {{ color: {theme['danger']}; }}
        QLabel[role="statusValue"][tone="info"] {{ color: {theme['info']}; }}
        QGroupBox[themeCard="true"] {{
            background: {theme['panel']}; border: 1px solid {theme['border']};
            border-radius: 14px; margin-top: 0; font-size: 14px; font-weight: 700;
        }}
        QGroupBox[themeCard="true"]::title {{
            subcontrol-origin: padding; left: 14px; top: 8px;
        }}
        QFrame[sectionCard="true"] {{
            background: {theme['section']}; border: 1px solid {theme['border']};
            border-radius: 12px;
        }}
        QLabel[role="sectionTitle"] {{ font-size: 12px; font-weight: 700; }}
        QLabel[role="sectionCaption"], QLabel[role="hint"] {{
            color: {theme['muted']}; font-size: 10px;
        }}
        QLabel[role="field"] {{
            color: {theme['muted']}; font-size: 11px; font-weight: 700;
        }}
        QLabel[role="runtimeValue"] {{ font-weight: 700; color: {theme['metric']}; }}
        QLabel[role="readOnlyValue"] {{
            background: {theme['panel']}; border: 1px solid {theme['border']};
            border-radius: 8px; padding: 5px 8px; font-weight: 600;
        }}
        QLabel[role="metricValue"] {{
            color: {theme['metric']}; font-size: 17px; font-weight: 700;
        }}
        QPushButton, QComboBox, QDoubleSpinBox {{
            background: {theme['section']}; border: 1px solid {theme['border']};
            border-radius: 9px; color: {theme['text']};
            min-height: 18px; padding: 5px 8px; font-weight: 700;
        }}
        QComboBox QAbstractItemView {{
            background: {theme['section']}; color: {theme['text']};
            selection-background-color: {theme['metric']};
            selection-color: {theme['window']};
            border: 1px solid {theme['border']}; outline: 0;
        }}
        QComboBox QAbstractItemView::item {{
            min-height: 24px; padding: 4px 8px; color: {theme['text']};
        }}
        QComboBox QAbstractItemView::item:disabled {{ color: {theme['muted']}; }}
        QPushButton:hover, QComboBox:hover, QDoubleSpinBox:hover {{
            background: {theme['button_hover']};
        }}
        QPushButton:disabled, QComboBox:disabled, QDoubleSpinBox:disabled {{
            color: {theme['muted']}; background: {theme['window']};
        }}
        """

    def _apply_initial_state(self) -> None:
        self.stop_button.setEnabled(False)
        self.plot_toggle_button.setText("Pause Plot")
        self.feedback_start_button.setEnabled(False)
        self.feedback_stop_button.setEnabled(False)
        self.target_energy_spin.setEnabled(False)
        for _readback, spin, button in self._phase_controls.values():
            spin.setEnabled(False)
            button.setEnabled(False)
        self.restore_initial_button.setEnabled(False)
        self._set_status_item("data", "Not checked", "subtle")
        self._set_status_item("loop", "OFF", "subtle")
        self._set_status_item("action", "Ready", "subtle")
        self._set_status_item(
            "access",
            "WRITE ENABLED" if self.config["write_enabled"] else "READ ONLY",
            "warning" if self.config["write_enabled"] else "subtle",
        )
        self._draw_plot()

    def start_acquisition(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        self._history.clear()
        self._last_plot_state = None
        self._draw_plot()
        self._latest_measurement = None
        self._latest_readbacks.clear()
        self._initial_phases.clear()
        self._target_initialized = False
        self.target_energy_spin.setEnabled(False)
        self.initial_phase_value.setText("Initial: —")
        self._set_manual_controls(False)
        try:
            self.safety.start_acquisition()
            self.logger = EnergyFeedbackCsvLogger(self.context, self.config)
            self.worker = AcquisitionThread(self.context, self.config)
            self.worker.sample_ready.connect(self._on_sample)
            self.worker.startup_failed.connect(self._on_startup_failed)
            self.worker.feedback_state.connect(self._on_feedback_state)
            self.worker.manual_result.connect(self._on_manual_result)
            self.worker.finished.connect(self._on_worker_finished)
            self.worker.start()
        except Exception as exc:  # noqa: BLE001
            if self.logger is not None:
                self.logger.close("failed")
                self.logger = None
            self.safety.fail(str(exc))
            self.event_value.setText(f"Start failed: {exc}")
            self._set_status_item("data", "Disconnected", "danger")
            self._set_status_item("action", "Start failed", "danger")
            return
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self._set_status_item("data", "Connecting", "info")
        self._set_status_item("phase", "Checking", "info")
        self._set_status_item("action", "Connecting", "info")
        self.event_value.setToolTip(str(self.logger.csv_path))
        self.event_value.setText("Acquisition starting · connection check in progress")

    def stop_acquisition(self) -> None:
        worker = self.worker
        if worker is not None:
            worker.set_feedback_enabled(False)
            worker.stop()
            timeout_ms = int(
                (
                    float(self.config["runtime"]["connection_timeout_s"])
                    * max(1, len(self.config["pvs"]))
                    + 3
                )
                * 1000
            )
            worker.wait(timeout_ms)
        self.worker = None
        self._feedback_active = False
        self.safety.stop()
        if self.logger is not None:
            self.logger.close()
            self.logger = None
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.feedback_start_button.setEnabled(False)
        self.feedback_stop_button.setEnabled(False)
        self._set_parameter_editing(True)
        self._set_manual_controls(False)
        self._set_status_item("data", "Stopped", "subtle")
        self._set_status_item("loop", "OFF", "subtle")
        self._set_status_item("phase", "Not checked", "subtle")
        self._set_status_item("action", "Stopped", "subtle")
        self.event_value.setText("Acquisition stopped")

    def start_feedback(self) -> None:
        if not self.config["control_groups"]:
            self.event_value.setText("Feedback unavailable: no actuator group")
            return
        if not self._feedback_prerequisites_ok():
            self.event_value.setText("Feedback cannot start until all inputs are valid")
            return
        parameters = self._operator_parameters()
        group = self.config["control_groups"][0]
        actuator_text = ", ".join(
            f"{actuator.label}: {self.config['signals'][actuator.setpoint_key]['pv']}"
            for actuator in group.actuators
        )
        message = (
            "Enable automatic real-machine phase writes?\n\n"
            f"{actuator_text}\n"
            f"targetE={parameters['target_mev']:.3f} MeV, "
            f"ki={parameters['loop_gain']:.4f}, "
            f"alpha={parameters['ratio']:.6f}\n"
            f"step≤{parameters['max_knob_step']:.3f}°, "
            f"|error|<{parameters['action_window_mev']:.3f} MeV, "
            f"session≤{parameters['max_session_delta']:.3f}°"
        )
        if (
            QMessageBox.warning(
                self,
                "Enable Energy Feedback",
                message,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            return
        try:
            require_workflow_write_allowed(
                self.context, WORKFLOW_NAME, "Energy feedback"
            )
            assert self.worker is not None
            self.worker.set_control_parameters(parameters)
            self.worker.set_feedback_enabled(True)
        except Exception as exc:  # noqa: BLE001
            self.event_value.setText(f"Feedback start failed: {exc}")
            self._set_status_item("loop", "BLOCKED", "danger")
            self._set_status_item("action", "Start failed", "danger")
            return
        self.safety.request_feedback(True)
        self._set_parameter_editing(False)
        self._set_manual_controls(False)
        self.feedback_start_button.setEnabled(False)
        self.feedback_stop_button.setEnabled(True)
        self._set_status_item("loop", "ARMING", "warning")
        self._set_status_item("action", "Arming feedback", "warning")
        self.event_value.setText("Feedback arming · acquiring energy-control lock")

    def stop_feedback(self) -> None:
        if self.worker is not None:
            self.worker.set_feedback_enabled(False)
        self._feedback_active = False
        self.safety.request_feedback(False)
        self.feedback_stop_button.setEnabled(False)
        self._set_parameter_editing(True)
        self._refresh_write_control_state()
        self._set_status_item("loop", "OFF", "subtle")
        self._set_status_item("action", "Monitoring", "success")
        self.event_value.setText("Feedback stop requested")

    def apply_manual_phase(self, actuator_id: str) -> None:
        if self.worker is None or self._feedback_active:
            return
        _readback, spin, _button = self._phase_controls[actuator_id]
        actuator = next(
            item
            for item in self.config["control_groups"][0].actuators
            if item.id == actuator_id
        )
        if (
            QMessageBox.warning(
                self,
                "Apply Phase Setpoint",
                f"Write {actuator.label} to {spin.value():.{actuator.command_precision}f} {actuator.unit}?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            return
        self._set_manual_controls(False)
        self.worker.request_manual_targets({actuator_id: spin.value()})
        self.event_value.setText(f"Manual write queued for {actuator.label}")

    def restore_initial_phases(self) -> None:
        if (
            self.worker is None
            or self._feedback_active
            or len(self._initial_phases) != len(self._phase_controls)
        ):
            return
        if (
            QMessageBox.warning(
                self,
                "Restore Initial Phases",
                "Restore both phase setpoints to the values captured at acquisition start?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            return
        self._set_manual_controls(False)
        self.worker.request_manual_targets(self._initial_phases)
        self.event_value.setText("Initial phase-pair restore queued")

    def _on_feedback_state(self, enabled: bool, message: str) -> None:
        self._feedback_active = bool(enabled)
        self.safety.request_feedback(enabled)
        self.feedback_stop_button.setEnabled(enabled)
        self._set_parameter_editing(not enabled)
        if enabled:
            self.feedback_start_button.setEnabled(False)
            self._set_manual_controls(False)
            self._set_status_item("loop", "ON", "success")
            self._set_status_item("action", "Feedback running", "success")
        else:
            failed = "fail" in message.lower()
            self._set_status_item("loop", "OFF", "danger" if failed else "subtle")
            self._refresh_write_control_state()
            self._set_status_item(
                "action",
                "Feedback failed" if failed else "Monitoring",
                "danger" if failed else "success",
            )
        self.last_action_value.setToolTip(message)
        self.event_value.setText(message)

    def _on_manual_result(self, decision: FeedbackDecision) -> None:
        if decision.write_verified:
            self.event_value.setText(f"{decision.reason} · {decision.write_detail}")
            self._set_status_item("action", "Manual write verified", "success")
        else:
            self.event_value.setText(f"{decision.reason} · {decision.write_detail}")
            self._set_status_item("action", "Manual write HOLD", "danger")
        self._refresh_write_control_state()

    def _set_parameter_editing(self, enabled: bool) -> None:
        for spin in self._parameter_spins():
            spin.setEnabled(enabled and (spin is not self.target_energy_spin or self._target_initialized))

    def _set_manual_controls(self, enabled: bool) -> None:
        for actuator_id, (_label, spin, button) in self._phase_controls.items():
            reading = self._latest_readbacks.get(actuator_id)
            available = bool(
                enabled
                and reading
                and reading.connected
                and reading.value is not None
                and math.isfinite(reading.value)
            )
            spin.setEnabled(available)
            button.setEnabled(available)
        self.restore_initial_button.setEnabled(
            enabled and len(self._initial_phases) == len(self._phase_controls)
        )

    def _feedback_prerequisites_ok(self) -> bool:
        measurement = self._latest_measurement
        return bool(
            self.config["write_enabled"]
            and self.config["control_groups"]
            and self.worker is not None
            and self.worker.isRunning()
            and measurement is not None
            and measurement.quality.valid
            and self._all_readbacks_valid()
            and self._target_initialized
        )

    def _all_readbacks_valid(self) -> bool:
        return len(self._latest_readbacks) == len(self._phase_controls) and all(
            reading.connected
            and reading.value is not None
            and math.isfinite(reading.value)
            for reading in self._latest_readbacks.values()
        )

    def _refresh_write_control_state(self) -> None:
        feedback_available = (
            self._feedback_prerequisites_ok() and not self._feedback_active
        )
        manual_available = bool(
            self.config["write_enabled"]
            and self.worker is not None
            and self.worker.isRunning()
            and self._all_readbacks_valid()
            and not self._feedback_active
        )
        self.feedback_start_button.setEnabled(feedback_available)
        self._set_manual_controls(manual_available)

    def toggle_plot(self) -> None:
        self._plot_enabled = not self._plot_enabled
        self.plot_toggle_button.setText(
            "Pause Plot" if self._plot_enabled else "Resume Plot"
        )
        if self._plot_enabled:
            self._draw_plot()

    def clear_plot(self) -> None:
        self._history.clear()
        self._last_plot_state = None
        self._draw_plot()
        self.event_value.setText("Plot history cleared")

    def _on_sample(
        self,
        measurement,
        decision: FeedbackDecision,
        actuator_readbacks: Mapping[str, PVReading],
    ) -> None:
        self._latest_measurement = measurement
        self._latest_readbacks = dict(actuator_readbacks)
        log_failure: str | None = None
        if self.logger is not None:
            try:
                self.logger.append(measurement, decision, actuator_readbacks)
            except Exception as exc:  # noqa: BLE001
                log_failure = str(exc)

        self._set_data_status(measurement)
        safety = self.safety.observe(measurement, decision)
        if self._feedback_active:
            if safety.state == "HOLD":
                self._set_status_item("loop", "HOLD", "warning")
            else:
                self._set_status_item("loop", "ON", "success")
        action_tone = (
            "danger"
            if not measurement.quality.connected
            else "warning"
            if safety.state == "HOLD"
            else "success"
        )
        self._set_status_item("action", safety.state, action_tone)
        self.last_action_value.setToolTip(safety.reason)
        if not self._feedback_active:
            self.event_value.setText(f"{safety.state} · {safety.reason}")

        if (
            not self._target_initialized
            and (
                (
                    measurement.target_mev is not None
                    and math.isfinite(measurement.target_mev)
                )
                or (
                    measurement.energy_mev is not None
                    and math.isfinite(measurement.energy_mev)
                )
            )
        ):
            initial_target = (
                measurement.target_mev
                if measurement.target_mev is not None
                and math.isfinite(measurement.target_mev)
                else measurement.energy_mev
            )
            assert initial_target is not None
            self.target_energy_spin.blockSignals(True)
            self.target_energy_spin.setValue(float(initial_target))
            self.target_energy_spin.blockSignals(False)
            self._target_initialized = True
            self.target_energy_spin.setEnabled(not self._feedback_active)
            self._push_operator_parameters()

        self.energy_metric.setText(_format_value(measurement.energy_mev, "MeV"))
        self.target_metric.setText(_format_value(measurement.target_mev, "MeV"))
        self.error_metric.setText(_format_value(measurement.error_mev, "MeV"))

        phase_parts = []
        for actuator_id, (label, spin, _button) in self._phase_controls.items():
            reading = actuator_readbacks.get(actuator_id)
            if (
                reading is not None
                and reading.connected
                and reading.value is not None
                and math.isfinite(reading.value)
            ):
                value = float(reading.value)
                label.setText(f"{value:.2f}°")
                phase_parts.append(f"{actuator_id}={value:.2f}°")
                if actuator_id not in self._initial_phases:
                    self._initial_phases[actuator_id] = value
                    spin.setValue(value)
            else:
                label.setText("—")
                phase_parts.append(f"{actuator_id}=disconnected")
        if self._initial_phases:
            self.initial_phase_value.setText(
                "Initial: "
                + " · ".join(
                    f"{actuator_id}={value:.2f}°"
                    for actuator_id, value in self._initial_phases.items()
                )
            )
        phase_ok = self._all_readbacks_valid()
        self._set_status_item(
            "phase",
            "Ready" if phase_ok else "Unavailable",
            "success" if phase_ok else "danger",
        )
        self.phase_summary_value.setToolTip(" · ".join(phase_parts))
        self._refresh_write_control_state()

        self._history["time"].append(float(measurement.sampled_at))
        self._history["energy"].append(_plot_value(measurement.energy_mev))
        self._history["target"].append(_plot_value(measurement.target_mev))
        self._history["error"].append(_plot_value(measurement.error_mev))
        was_hold = self._last_plot_state == "HOLD"
        is_hold = safety.state == "HOLD"
        self._history["event.applied"].append(
            _plot_value(
                measurement.error_mev if decision.state == "APPLIED" else None
            )
        )
        self._history["event.hold"].append(
            _plot_value(
                measurement.error_mev if is_hold and not was_hold else None
            )
        )
        self._history["event.recovered"].append(
            _plot_value(
                measurement.error_mev if was_hold and not is_hold else None
            )
        )
        self._last_plot_state = safety.state
        commands = {command.actuator_id: command for command in decision.commands}
        groups = tuple(self.config["control_groups"])
        for actuator in groups[0].actuators if groups else ():
            current = actuator_readbacks.get(actuator.id)
            origin = self._initial_phases.get(actuator.id)
            readback_delta = (
                cyclic_delta(
                    float(current.value),
                    origin,
                    period=actuator.cyclic_period,
                )
                if actuator.cyclic_period is not None
                and current is not None
                and current.connected
                and current.value is not None
                and math.isfinite(current.value)
                and origin is not None
                else float(current.value) - origin
                if current is not None
                and current.connected
                and current.value is not None
                and math.isfinite(current.value)
                and origin is not None
                else None
            )
            command = (
                commands.get(actuator.id) if decision.write_verified else None
            )
            command_delta = (
                cyclic_delta(
                    command.proposed,
                    origin,
                    period=actuator.cyclic_period,
                )
                if command is not None
                and origin is not None
                and actuator.cyclic_period is not None
                else command.proposed - origin
                if command is not None and origin is not None
                else None
            )
            self._history[f"actuator.{actuator.id}.readback_delta"].append(
                _plot_value(readback_delta)
            )
            self._history[f"actuator.{actuator.id}.command_delta"].append(
                _plot_value(command_delta)
            )

        if decision.state == "APPLIED":
            self.event_value.setText(f"APPLIED · {decision.write_detail}")
            self._set_status_item("action", "Applied", "success")
            self.last_action_value.setToolTip(decision.write_detail or decision.reason)
        if log_failure is not None:
            self.event_value.setText(f"Log failure: {log_failure}")
            self._set_status_item("action", "Log failed", "danger")
            self.last_action_value.setToolTip(log_failure)
        now = time.monotonic()
        if self._plot_enabled and now - self._last_plot_at >= float(
            self.config["runtime"]["plot_period_s"]
        ):
            self._last_plot_at = now
            self._draw_plot()

    def _on_startup_failed(self, message: str) -> None:
        self._set_status_item("data", "Disconnected", "danger")
        self.data_status_value.setToolTip(message)
        self._set_status_item("loop", "OFF", "subtle")
        self._set_status_item("phase", "Unavailable", "danger")
        self._set_status_item("action", "Start failed", "danger")
        self.last_action_value.setToolTip(message)
        self.event_value.setText(f"Start failed: {message}")
        self.safety.fail(message)
        if self.logger is not None:
            self.logger.close("failed")
            self.logger = None

    def _on_worker_finished(self) -> None:
        self._feedback_active = False
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.feedback_start_button.setEnabled(False)
        self.feedback_stop_button.setEnabled(False)
        self._set_manual_controls(False)

    def _draw_plot(self) -> None:
        theme = self._theme()["mpl"]
        self.figure.patch.set_facecolor(theme["figure"])
        axes = self._plot_axes()
        for axis in axes:
            axis.clear()
            axis.set_facecolor(theme["axes"])
            axis.grid(True, color=theme["grid"], alpha=0.65, linewidth=0.7)
            axis.tick_params(colors=theme["text"], labelsize=9)
            for spine in axis.spines.values():
                spine.set_color(theme["spine"])
        view = (
            self.plot_view_combo.currentText()
            if hasattr(self, "plot_view_combo")
            else "Feedback"
        )
        if self.error_axis is not None:
            groups = tuple(self.config["control_groups"])
            deadband = groups[0].deadband_mev if groups else 0.0
            action_window = (
                self.action_window_spin.value()
                if hasattr(self, "action_window_spin")
                else groups[0].action_window_mev
                if groups
                else 0.0
            )
            if action_window > 0:
                self.error_axis.axhspan(
                    -action_window,
                    action_window,
                    color=theme["action_window"],
                    alpha=0.045,
                    label="Action window",
                )
                self.error_axis.axhline(
                    action_window,
                    color=theme["action_window"],
                    linewidth=0.8,
                    linestyle="--",
                )
                self.error_axis.axhline(
                    -action_window,
                    color=theme["action_window"],
                    linewidth=0.8,
                    linestyle="--",
                )
            if deadband > 0:
                self.error_axis.axhspan(
                    -deadband,
                    deadband,
                    color=theme["deadband"],
                    alpha=0.18,
                    label="Deadband",
                )
            self.error_axis.axhline(
                0.0,
                color=theme["text"],
                linewidth=0.8,
                alpha=0.8,
                label="Zero",
            )
        timestamps = list(self._history.get("time", ()))
        if timestamps:
            duration_s = PLOT_RANGE_SECONDS.get(
                self.plot_range_combo.currentText()
                if hasattr(self, "plot_range_combo")
                else "30 min"
            )
            start = (
                0
                if duration_s is None
                else bisect_left(timestamps, timestamps[-1] - duration_s)
            )
            indices = _decimated_indices(
                list(range(start, len(timestamps))),
                list(self._history.get("error", ())),
                MAX_RENDERED_PLOT_POINTS,
            )
            mode = self.plot_axis_combo.currentText()
            if mode == "Index":
                x_values = indices
            elif mode == "Clock":
                x_values = [
                    datetime.fromtimestamp(timestamps[index]) for index in indices
                ]
            else:
                origin = timestamps[0]
                x_values = [timestamps[index] - origin for index in indices]

            def values(key: str) -> list[float]:
                series = list(self._history.get(key, ()))
                return [
                    series[index] if index < len(series) else math.nan
                    for index in indices
                ]

            if self.energy_axis is not None:
                self.energy_axis.plot(
                    x_values,
                    values("energy"),
                    label="Measured Energy",
                    color=theme["energy"],
                )
                if view == "Full Detail":
                    self.energy_axis.plot(
                        x_values,
                        values("target"),
                        label="Target Energy",
                        color=theme["target"],
                    )
            if self.error_axis is not None:
                self.error_axis.plot(
                    x_values,
                    values("error"),
                    label="Measured − Target",
                    color=theme["error"],
                )
                event_specs = (
                    ("event.applied", "APPLIED", "o", "event_applied"),
                    ("event.hold", "HOLD", "x", "event_hold"),
                    ("event.recovered", "Recovered", "^", "event_recovered"),
                )
                for key, label, marker, color_key in event_specs:
                    event_values = values(key)
                    event_x = [
                        x_value
                        for x_value, value in zip(x_values, event_values)
                        if math.isfinite(value)
                    ]
                    event_y = [
                        value for value in event_values if math.isfinite(value)
                    ]
                    if event_x:
                        self.error_axis.scatter(
                            event_x,
                            event_y,
                            label=label,
                            marker=marker,
                            color=theme[color_key],
                            s=28,
                            zorder=5,
                        )
            if self.actuator_axis is not None:
                colors = (theme["phase1"], theme["phase2"])
                groups = tuple(self.config["control_groups"])
                for color, actuator in zip(
                    colors, groups[0].actuators if groups else ()
                ):
                    self.actuator_axis.plot(
                        x_values,
                        values(f"actuator.{actuator.id}.readback_delta"),
                        label=f"{actuator.label} Δ readback",
                        color=color,
                    )
                    self.actuator_axis.plot(
                        x_values,
                        values(f"actuator.{actuator.id}.command_delta"),
                        label=f"{actuator.label} Δ setpoint",
                        color=color,
                        linestyle="None",
                        marker="o",
                        markersize=3,
                    )
        if self.energy_axis is not None:
            self.energy_axis.set_ylabel(
                "Energy Tracking [MeV]"
                if view == "Full Detail"
                else "Measured Energy [MeV]",
                color=theme["text"],
            )
        if self.error_axis is not None:
            self.error_axis.set_ylabel("Feedback Error [MeV]", color=theme["text"])
        if self.actuator_axis is not None:
            self.actuator_axis.set_ylabel(
                "Phase Correction [deg]", color=theme["text"]
            )
        mode = self.plot_axis_combo.currentText()
        bottom_axis = axes[-1]
        if mode == "Clock":
            bottom_axis.xaxis.set_major_locator(mdates.AutoDateLocator())
            bottom_axis.xaxis.set_major_formatter(
                mdates.DateFormatter("%H:%M:%S")
            )
            x_label = "Clock time"
        elif mode == "Index":
            bottom_axis.xaxis.set_major_locator(AutoLocator())
            bottom_axis.xaxis.set_major_formatter(ScalarFormatter())
            x_label = "Sample index"
        else:
            bottom_axis.xaxis.set_major_locator(AutoLocator())
            bottom_axis.xaxis.set_major_formatter(ScalarFormatter())
            x_label = "Elapsed time [s]"
        bottom_axis.set_xlabel(x_label, color=theme["text"])
        for axis in axes[:-1]:
            axis.tick_params(labelbottom=False)
        for axis in axes:
            handles, _labels = axis.get_legend_handles_labels()
            if handles:
                legend = axis.legend(loc="upper right", fontsize=8)
                legend.get_frame().set_facecolor(theme["legend"])
                legend.get_frame().set_edgecolor(theme["spine"])
                for text in legend.get_texts():
                    text.set_color(theme["text"])
        self.canvas.draw_idle()

    def _plot_axes(self):
        return tuple(
            axis
            for axis in (
                self.energy_axis,
                self.error_axis,
                self.actuator_axis,
            )
            if axis is not None
        )

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.stop_acquisition()
        event.accept()


def _plot_value(value: float | None) -> float:
    return math.nan if value is None else float(value)


def _decimated_indices(
    indices: list[int],
    primary_values: list[float],
    max_points: int,
) -> list[int]:
    """Bound rendering while preserving local extrema of the primary trace."""

    if len(indices) <= max_points:
        return indices
    bucket_count = max(1, (max_points - 2) // 2)
    bucket_size = max(1, math.ceil(len(indices) / bucket_count))
    selected = {indices[0], indices[-1]}
    for offset in range(0, len(indices), bucket_size):
        bucket = indices[offset : offset + bucket_size]
        finite = [
            index
            for index in bucket
            if index < len(primary_values)
            and math.isfinite(primary_values[index])
        ]
        if not finite:
            selected.add(bucket[0])
            continue
        selected.add(min(finite, key=primary_values.__getitem__))
        selected.add(max(finite, key=primary_values.__getitem__))
    return sorted(selected)


def _format_value(value: float | None, unit: str) -> str:
    if value is None:
        return "—" if not unit else f"— {unit}"
    suffix = f" {unit}" if unit else ""
    return f"{float(value):.4f}{suffix}"


def main() -> int:
    app = QApplication(sys.argv)
    try:
        context = load_app_context(WORKFLOW_NAME)
        window = EnergyFeedbackWindow(context)
    except Exception as exc:  # noqa: BLE001
        QMessageBox.critical(None, "Energy Feedback unavailable", str(exc))
        return 2
    install_qt_window_raise_handler(window)
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
