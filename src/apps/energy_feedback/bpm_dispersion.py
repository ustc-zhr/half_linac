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
    phase_diff_deg,
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

    def commit(
        self,
        proposal: PIProposal,
        *,
        applied_knob_delta: float | None = None,
    ) -> None:
        self.controller.commit(
            proposal,
            applied_knob_delta=applied_knob_delta,
        )

    def reset_controller(self) -> None:
        self.controller.reset()

    def configure_controller(self, *, kp: float, ki: float, period_s: float) -> None:
        changed = (
            self.controller.kp,
            self.controller.ki,
            self.controller.period_s,
        ) != (float(kp), float(ki), float(period_s))
        self.controller.configure(kp=kp, ki=ki, period_s=period_s)
        if changed:
            self.controller.reset()


def shared_rf_phase_limit(source: Mapping[str, Any]) -> float | None:
    limits = source.get("rf_safety", {}).get("safety", {}).get("phase_limit_deg", {})
    values = {float(value) for value in limits.values()}
    if len(values) == 1:
        value = values.pop()
        if 0 < value < 180:
            return value
    return None


def build_rf_hv_safety_checker(
    source: Mapping[str, Any],
    aggregate: Mapping[str, float],
    *,
    phase_limit_deg: float | None = None,
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
            str(key): float(value) if phase_limit_deg is None else phase_limit_deg
            for key, value in safety["phase_limit_deg"].items()
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


def read_window_recording(
    client: PVClient,
    source: Mapping[str, Any],
) -> tuple[
    dict[str, float | None],
    dict[str, float | None],
    dict[str, str],
]:
    """Read one traceable snapshot without changing measurement validity."""

    keys = tuple(source.get("recording_keys", source["closed_loop_keys"]))
    try:
        readings = client.read_many(keys, fresh=True)
    except Exception as exc:  # noqa: BLE001 - failures belong in the audit log
        issue = f"recording input read failure: {exc}"
        return (
            {key: None for key in keys},
            {key: None for key in keys},
            {key: issue for key in keys},
        )

    values: dict[str, float | None] = {}
    timestamps: dict[str, float | None] = {}
    errors: dict[str, str] = {}
    for key in keys:
        reading = readings.get(key)
        value, issue = _reading_value(reading)
        values[key] = value
        timestamps[key] = reading.source_timestamp if reading else None
        if issue:
            errors[key] = issue
    return values, timestamps, errors


def closed_loop_values_from_recording(
    source: Mapping[str, Any],
    values: Mapping[str, float | None],
    errors: Mapping[str, str],
) -> tuple[dict[str, float], str]:
    """Resolve fail-closed guard values from a possibly partial snapshot."""

    resolved: dict[str, float] = {}
    for key in source["closed_loop_keys"]:
        issue = errors.get(key, "")
        value = values.get(key)
        if issue:
            return resolved, f"{key}: {issue}"
        if value is None or not math.isfinite(float(value)):
            return resolved, f"{key}: not finite"
        resolved[key] = float(value)
    return resolved, ""


def safety_audit_fields(
    source: Mapping[str, Any],
    guard_values: Mapping[str, float],
    guard_read_issue: str,
    checker: SafetyChecker | None,
    *,
    phase_limit_deg: float | None = None,
) -> tuple[dict[str, float | None], dict[str, str], dict[str, float], str]:
    """Build safety diagnostics for a freshly recorded window snapshot."""

    calibration = source["calibration"]
    safety = source["rf_safety"]["safety"]
    diagnostics: dict[str, float | None] = {
        "safety.reference_captured": 1.0 if checker is not None else 0.0,
        "safety.fct_minimum": float(calibration["fct_min"]),
        "safety.hv_readback_tolerance_kv": float(
            safety["hv_readback_tolerance_kv"]
        ),
    }
    errors: dict[str, str] = {}

    fct = guard_values.get(source["fct_key"])
    if fct is not None:
        diagnostics["safety.fct_margin"] = (
            abs(float(fct)) - float(calibration["fct_min"])
        )

    aggregate = rf_hv_aggregate(guard_values, source)
    if "hv_setpoint" in aggregate and "hv_readback" in aggregate:
        diagnostics["safety.hv_readback_minus_setpoint_kv"] = (
            aggregate["hv_readback"] - aggregate["hv_setpoint"]
        )

    phase_limits = safety["phase_limit_deg"]
    if checker is not None:
        phase_limits = checker.cfg.phase_limit_deg
    elif phase_limit_deg is not None:
        phase_limits = dict.fromkeys(phase_limits, phase_limit_deg)
    for channel_id, limit in phase_limits.items():
        diagnostics[f"safety.rf.{channel_id}.phase_limit_deg"] = float(limit)
        phase_key = f"rf.{channel_id}.phase"
        if checker is not None and phase_key in aggregate:
            diagnostics[f"safety.rf.{channel_id}.phase_delta_deg"] = (
                phase_diff_deg(
                    aggregate[phase_key],
                    checker.ref.channel_phases[channel_id],
                )
            )

    issue = guard_read_issue
    if not issue:
        issue = check_session_guards(guard_values, calibration)
    if checker is not None and not issue:
        result = checker.check_aggregate(aggregate)
        if not result.ok:
            issue = result.reason
    diagnostics["safety.ok"] = (
        None if checker is None else (0.0 if issue else 1.0)
    )
    if checker is not None and issue:
        errors["safety"] = issue
    return diagnostics, errors, aggregate, issue


def window_guard_details(
    diagnostics: Mapping[str, float | None], source: Mapping[str, Any]
) -> str:
    """Describe all numeric violations in the same window safety snapshot."""

    notices = []
    fct = diagnostics.get(source["fct_key"])
    fct_min = diagnostics.get("safety.fct_minimum")
    if fct is not None and fct_min is not None and abs(fct) < fct_min:
        notices.append(f"|FCT| {abs(fct):.4g} a.u. (min {fct_min:g} a.u.)")

    hv_delta = diagnostics.get("safety.hv_readback_minus_setpoint_kv")
    hv_limit = diagnostics.get("safety.hv_readback_tolerance_kv")
    if hv_delta is not None and hv_limit is not None and abs(hv_delta) > hv_limit:
        notices.append(f"HV deviation {hv_delta:+.4g} kV (limit ±{hv_limit:g} kV)")

    for channel in source["rf_safety"]["rf_channels"]:
        key = f"safety.rf.{channel['id']}"
        phase = diagnostics.get(f"{key}.phase_delta_deg")
        limit = diagnostics.get(f"{key}.phase_limit_deg")
        if phase is not None and limit is not None and abs(phase) > limit:
            notices.append(f"{channel['label']} {phase:+.4g}° (limit ±{limit:g}°)")

    return " · ".join(notices)


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
