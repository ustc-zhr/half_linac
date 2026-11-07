"""IRFEL FIR BPM-dispersion observable and session safety checks."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, replace
from typing import Any, Mapping

from half_linac.src.shared.rf_hv_safety import (
    SafetyChecker,
    SafetyConfig,
    SafetyReference,
)

from .client import PVClient
from .engine import (
    IncrementalPIController,
    NonOverlappingAggregateWindow,
    PIProposal,
)
from .models import EnergyMeasurement, FeedbackObservable, MeasurementQuality, PVReading


def _numeric_issue(calibration: Mapping[str, Any], key: str) -> str:
    try:
        value = float(calibration.get(key))
    except (TypeError, ValueError):
        return f"FIR calibration {key} is missing"
    if not math.isfinite(value):
        return f"FIR calibration {key} is not finite"
    return ""


def observable_calibration_issue(calibration: Mapping[str, Any]) -> str:
    for key in ("a", "b"):
        issue = _numeric_issue(calibration, key)
        if issue:
            return issue
    return ""


def shadow_calibration_issue(calibration: Mapping[str, Any]) -> str:
    issue = observable_calibration_issue(calibration)
    if issue:
        return issue
    for key in ("response_q_mm_per_kv", "q_action_range_mm"):
        issue = _numeric_issue(calibration, key)
        if issue:
            return issue
    if float(calibration["response_q_mm_per_kv"]) == 0:
        return "FIR calibration response_q_mm_per_kv must be non-zero"
    if float(calibration["q_action_range_mm"]) <= 0:
        return "FIR calibration q_action_range_mm must be positive"
    return ""


def calibration_issue(calibration: Mapping[str, Any]) -> str:
    if calibration.get("ready") is not True:
        return "FIR calibration is not marked ready"
    version = str(calibration.get("version", "")).strip()
    if not version:
        return "FIR calibration version is missing"
    issue = shadow_calibration_issue(calibration)
    if issue:
        return issue
    issue = _numeric_issue(calibration, "fct_min")
    if issue:
        return issue
    if float(calibration["fct_min"]) <= 0:
        return "FIR calibration fct_min must be positive"
    return ""


@dataclass(frozen=True)
class FirSessionReference:
    target_q_mm: float
    window_sequence: int


def capture_session_reference(
    target_q_mm: float,
    *,
    window_sequence: int,
) -> FirSessionReference:
    target_q_mm = float(target_q_mm)
    if not math.isfinite(target_q_mm):
        raise ValueError("FIR target is not finite")
    return FirSessionReference(target_q_mm, int(window_sequence))


def check_session_guards(
    aggregates: Mapping[str, float], calibration: Mapping[str, Any]
) -> str:
    issue = calibration_issue(calibration)
    if issue:
        return issue
    fct = aggregates.get("fct")
    if fct is None or not math.isfinite(fct) or abs(fct) < float(calibration["fct_min"]):
        return "FCT beam-present threshold not met"
    return ""


class BpmDispersionProvider:
    """Compute q = BPM04.x - a*BPM02.x - b*BPM03.x in millimetres."""

    def __init__(self, client: PVClient, source: Mapping[str, Any]) -> None:
        self.client = client
        self.source = source
        self.measurement_keys = frozenset(
            source.get("measurement_keys", source["q_keys"])
        )
        self.calibration = dict(source["calibration"])
        self.runtime_metadata = {
            "source_id": source["id"],
            "observable": dict(source["observable"]),
            "geometry": dict(source["geometry"]),
            "calibration": dict(source["calibration"]),
            "resolved_signals": dict(source["resolved_signals"]),
            "sample_identity": "bpm_monitor_event",
        }

    @property
    def required_keys(self) -> tuple[str, ...]:
        return tuple(self.source["signal_keys"])

    def reset(self) -> None:
        return None

    def sample(self, now: float | None = None) -> EnergyMeasurement:
        sampled_at = time.time() if now is None else float(now)
        try:
            readings = self.client.read_many(self.required_keys)
        except Exception as exc:  # noqa: BLE001
            return self._measurement(sampled_at, {}, {}, f"read failure: {exc}", False)

        diagnostics: dict[str, float | None] = {}
        timestamps: dict[str, float | None] = {}
        errors: dict[str, str] = {}
        primary_issue = ""
        for key in self.required_keys:
            reading = readings.get(key)
            value, reading_issue = _reading_value(reading)
            diagnostics[key] = value
            timestamps[key] = reading.source_timestamp if reading else None
            if reading_issue:
                errors[key] = reading_issue
                if key in self.measurement_keys and not primary_issue:
                    primary_issue = reading_issue

        issue = primary_issue
        bpm_time_keys = tuple(self.source["bpm_signal_keys"])
        monitor_times = tuple(
            readings[key].monitor_received_at
            for key in bpm_time_keys
            if readings.get(key) is not None
            and readings[key].monitor_received_at is not None
            and math.isfinite(float(readings[key].monitor_received_at))
        )
        sample_token = (
            tuple(float(value) for value in monitor_times)
            if len(monitor_times) == len(bpm_time_keys)
            else None
        )
        if not issue and sample_token is None:
            issue = "BPM monitor event identity unavailable"

        calibration_problem = observable_calibration_issue(self.calibration)
        q_value: float | None = None
        try:
            a = float(self.calibration["a"])
            b = float(self.calibration["b"])
            x02 = float(diagnostics[self.source["q_keys"][0]])
            x03 = float(diagnostics[self.source["q_keys"][1]])
            x04 = float(diagnostics[self.source["q_keys"][2]])
            if all(math.isfinite(value) for value in (a, b, x02, x03, x04)):
                q_value = x04 - a * x02 - b * x03
                diagnostics["q_raw_mm"] = q_value
        except (KeyError, TypeError, ValueError):
            pass

        if not issue and calibration_problem:
            issue = calibration_problem
        if not issue and q_value is None:
            issue = "FIR q observable is unavailable"

        source_times = [
            timestamp
            for key in bpm_time_keys
            if (timestamp := timestamps.get(key)) is not None
            and math.isfinite(float(timestamp))
        ]
        valid_reason = (
            "FIR BPM-dispersion sample valid; BPM monitor events define samples; "
            "EPICS timestamps are diagnostic only"
        )
        return EnergyMeasurement(
            sampled_at=sampled_at,
            source_timestamp=max(source_times) if source_times else None,
            target_source_timestamp=None,
            energy_mev=None,
            target_mev=None,
            error_mev=None,
            diagnostics=diagnostics,
            diagnostic_errors=errors,
            quality=MeasurementQuality(
                "HOLD" if issue else "OK",
                issue or valid_reason,
                connected=not any(
                    key in self.measurement_keys and "disconnected" in error
                    for key, error in errors.items()
                ),
                stale=any(
                    key in self.measurement_keys and "stale" in error
                    for key, error in errors.items()
                ),
            ),
            diagnostic_source_timestamps=timestamps,
            observable=FeedbackObservable(
                label=str(self.source["observable"]["label"]),
                unit=str(self.source["observable"]["unit"]),
                value=q_value,
                raw_value=q_value,
            ),
            sample_token=sample_token,
        )

    def _measurement(
        self,
        sampled_at: float,
        diagnostics: Mapping[str, float | None],
        errors: Mapping[str, str],
        reason: str,
        connected: bool,
    ) -> EnergyMeasurement:
        return EnergyMeasurement(
            sampled_at=sampled_at,
            source_timestamp=None,
            target_source_timestamp=None,
            energy_mev=None,
            target_mev=None,
            error_mev=None,
            diagnostics=diagnostics,
            diagnostic_errors=errors,
            quality=MeasurementQuality("HOLD", reason, connected=connected),
            observable=FeedbackObservable(
                label=str(self.source["observable"]["label"]),
                unit=str(self.source["observable"]["unit"]),
                value=None,
            ),
        )


def _reading_value(
    reading: PVReading | None,
) -> tuple[float | None, str]:
    if reading is None:
        return None, "result missing"
    if not reading.connected:
        return None, f"disconnected: {reading.error or 'not connected'}"
    if reading.error:
        return None, reading.error
    if reading.value is None or not math.isfinite(reading.value):
        return None, "not finite"
    return float(reading.value), ""


@dataclass(frozen=True)
class FirWindowResult:
    sequence: int
    measurement: EnergyMeasurement
    aggregates: Mapping[str, float]


class FirFeedbackCoordinator:
    """Own non-overlapping FIR windows, captured references, and PI state."""

    def __init__(
        self,
        source: Mapping[str, Any],
        *,
        window_samples: int = 10,
        kp: float = 0.0,
        ki: float = 0.1,
        period_s: float = 10.0,
        aggregation: str = "median",
    ) -> None:
        self.source = source
        self.calibration = dict(source["calibration"])
        self.window = NonOverlappingAggregateWindow(
            window_samples, aggregation=aggregation
        )
        self.controller = IncrementalPIController(kp=kp, ki=ki, period_s=period_s)
        self.reference: FirSessionReference | None = None
        self.latest_window: FirWindowResult | None = None
        self._last_sample_token: tuple[float, ...] | None = None

    def accept(self, measurement: EnergyMeasurement) -> FirWindowResult | None:
        if not measurement.quality.valid:
            self._last_sample_token = None
        else:
            sample_token = measurement.sample_token
            if sample_token is None:
                self.window.clear()
                self._last_sample_token = None
                return None
            if sample_token == self._last_sample_token:
                return None
            self._last_sample_token = sample_token
        completed = self.window.add(
            measurement.control_value,
            measurement.diagnostics,
            valid=measurement.quality.valid,
        )
        if completed is None:
            return None
        sequence, q_window, aggregates = completed
        target = self.reference.target_q_mm if self.reference is not None else None
        observable = measurement.observable
        assert observable is not None
        window_measurement = replace(
            measurement,
            observable=replace(
                observable,
                value=q_window,
                target=target,
                error=q_window - target if target is not None else None,
            ),
            diagnostics={
                **dict(measurement.diagnostics),
                **dict(aggregates),
                "q_window_mm": q_window,
                "q_target_mm": target,
                "window_sequence": float(sequence),
            },
        )
        result = FirWindowResult(sequence, window_measurement, aggregates)
        self.latest_window = result
        return result

    def capture_latest(self) -> FirSessionReference:
        if self.latest_window is None:
            raise ValueError("wait for a complete FIR control window")
        self.reference = capture_session_reference(
            self.latest_window.measurement.control_value,
            window_sequence=self.latest_window.sequence,
        )
        self.controller.reset(current_error=0.0)
        return self.reference

    def proposal(self, measurement: EnergyMeasurement) -> PIProposal:
        if self.reference is None or measurement.control_error is None:
            raise ValueError("capture the current-energy reference first")
        return self.controller.propose(
            -float(measurement.control_error),
            float(self.calibration["response_q_mm_per_kv"]),
        )

    def commit(self, proposal: PIProposal) -> None:
        self.controller.commit(proposal)

    def configure_controller(self, *, kp: float, ki: float, period_s: float) -> None:
        mode_changed = (self.controller.kp == 0.0) != (float(kp) == 0.0)
        self.controller.configure(kp=kp, ki=ki, period_s=period_s)
        if mode_changed:
            self.controller.reset()


def build_rf_hv_safety_checker(
    source: Mapping[str, Any], aggregate: Mapping[str, float]
) -> SafetyChecker:
    raw = source["rf_safety"]
    safety = raw["safety"]
    channel_ids = tuple(str(channel["id"]) for channel in raw["rf_channels"])
    required = ("hv_setpoint", "hv_readback") + tuple(
        f"rf.{channel_id}.phase" for channel_id in channel_ids
    )
    missing = [key for key in required if key not in aggregate]
    if missing:
        raise ValueError(
            f"cannot capture RF/HV safety reference; missing fields: {missing}"
        )
    values = {key: float(aggregate[key]) for key in required}
    invalid = [key for key, value in values.items() if not math.isfinite(value)]
    if invalid:
        raise ValueError(
            f"cannot capture RF/HV safety reference; invalid fields: {invalid}"
        )
    cfg = SafetyConfig(
        hv_min_kv=None,
        hv_max_kv=None,
        hv_readback_tolerance_kv=float(safety["hv_readback_tolerance_kv"]),
        phase_limit_deg={
            str(key): float(value) for key, value in safety["phase_limit_deg"].items()
        },
        require_valid_pv=bool(safety.get("require_valid_pv", True)),
        hold_on_fault=bool(safety.get("hold_on_fault", True)),
    )
    ref = SafetyReference(
        hv_kv=values["hv_readback"],
        channel_amplitudes={},
        channel_phases={
            channel_id: values[f"rf.{channel_id}.phase"]
            for channel_id in channel_ids
        },
    )
    return SafetyChecker(cfg, ref)


def read_closed_loop_inputs(
    client: PVClient,
    source: Mapping[str, Any],
    _sampled_at: float,
) -> tuple[dict[str, float], str]:
    keys = tuple(source["closed_loop_keys"])
    try:
        readings = client.read_many(keys, fresh=True)
    except Exception as exc:  # noqa: BLE001 - guard failures must block writes
        return {}, f"closed-loop input read failure: {exc}"
    values: dict[str, float] = {}
    for key in keys:
        value, issue = _reading_value(readings.get(key))
        if issue:
            return values, f"{key}: {issue}"
        assert value is not None
        values[key] = value
    return values, ""


def rf_hv_aggregate(
    values: Mapping[str, float | None],
    source: Mapping[str, Any],
) -> dict[str, float]:
    aggregate: dict[str, float] = {}
    for name, key in source["guard_keys"].items():
        value = values.get(key)
        if value is None:
            continue
        value = float(value)
        if math.isfinite(value):
            aggregate[name] = value
    return aggregate
