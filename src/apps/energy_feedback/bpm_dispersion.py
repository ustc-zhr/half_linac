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
        self.stale_timeout_s = float(source["stale_timeout_s"])
        self.max_timestamp_skew_s = float(source["max_timestamp_skew_s"])
        self.bpm_timestamp_policy = str(
            source.get("bpm_timestamp_policy", "source")
        )
        self.source_timestamp_policy = str(
            source.get("source_timestamp_policy", "enforce")
        )
        self.measurement_keys = frozenset(
            source.get("measurement_keys", source["q_keys"])
        )
        self.bpm_signal_keys = frozenset(source["bpm_signal_keys"])
        self.source_timestamp_exempt_keys = frozenset(
            source.get("source_timestamp_exempt_keys", ())
        )
        self.calibration = dict(source["calibration"])
        self.runtime_metadata = {
            "source_id": source["id"],
            "observable": dict(source["observable"]),
            "geometry": dict(source["geometry"]),
            "calibration": dict(source["calibration"]),
            "resolved_signals": dict(source["resolved_signals"]),
            "bpm_timestamp_policy": self.bpm_timestamp_policy,
            "source_timestamp_policy": self.source_timestamp_policy,
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
        effective_timestamps: dict[str, float | None] = {}
        errors: dict[str, str] = {}
        primary_issue = ""
        for key in self.required_keys:
            reading = readings.get(key)
            effective_reading = reading
            if (
                reading is not None
                and key in self.bpm_signal_keys
                and self.bpm_timestamp_policy == "monitor_receive"
            ):
                effective_reading = replace(
                    reading, source_timestamp=reading.monitor_received_at
                )
            value, reading_issue = _reading_value(
                effective_reading,
                sampled_at,
                self.stale_timeout_s,
                check_source_timestamp=(
                    self.source_timestamp_policy != "ignore"
                    and key not in self.source_timestamp_exempt_keys
                ),
            )
            diagnostics[key] = value
            timestamps[key] = reading.source_timestamp if reading else None
            effective_timestamps[key] = (
                effective_reading.source_timestamp if effective_reading else None
            )
            if reading_issue:
                error = reading_issue
                if (
                    key in self.bpm_signal_keys
                    and self.bpm_timestamp_policy == "monitor_receive"
                ):
                    error = f"BPM monitor event {reading_issue}"
                errors[key] = error
                if key in self.measurement_keys and not primary_issue:
                    primary_issue = error

        issue = primary_issue
        bpm_time_keys = tuple(self.source["bpm_signal_keys"])
        valid_times = [
            effective_timestamps[key]
            for key in bpm_time_keys
            if effective_timestamps.get(key) is not None
        ]
        if (
            not issue
            and self.source_timestamp_policy != "ignore"
            and self.bpm_timestamp_policy == "source"
            and len(valid_times) == len(bpm_time_keys)
        ):
            skew = max(valid_times) - min(valid_times)
            diagnostics["bpm_timestamp_skew_s"] = skew
            if skew > self.max_timestamp_skew_s:
                issue = f"BPM timestamp skew {skew:.3f} s exceeds limit"

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

        source_times = [item for item in valid_times if item is not None]
        valid_reason = "FIR BPM-dispersion sample valid"
        if self.source_timestamp_policy == "ignore":
            valid_reason += "; EPICS source timestamp checks disabled"
        elif self.bpm_timestamp_policy == "monitor_receive":
            valid_reason += "; BPM freshness uses monitor receive time"
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
    now: float,
    stale_timeout_s: float,
    *,
    check_source_timestamp: bool = True,
) -> tuple[float | None, str]:
    if reading is None:
        return None, "result missing"
    if not reading.connected:
        return None, f"disconnected: {reading.error or 'not connected'}"
    if reading.error:
        return None, reading.error
    if reading.value is None or not math.isfinite(reading.value):
        return None, "not finite"
    if not check_source_timestamp:
        return float(reading.value), ""
    if reading.source_timestamp is None:
        return None, "source timestamp missing"
    age = now - reading.source_timestamp
    if age > stale_timeout_s:
        return None, f"source timestamp stale ({age:.2f} s)"
    if age < -stale_timeout_s:
        return None, "source timestamp is in the future"
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
        self._last_source_timestamp: float | None = None

    def accept(self, measurement: EnergyMeasurement) -> FirWindowResult | None:
        if not measurement.quality.valid:
            self._last_source_timestamp = None
        else:
            timestamp = measurement.source_timestamp
            if timestamp is None or not math.isfinite(float(timestamp)):
                self.window.clear()
                self._last_source_timestamp = None
                return None
            timestamp = float(timestamp)
            if timestamp == self._last_source_timestamp:
                return None
            if (
                self._last_source_timestamp is not None
                and timestamp < self._last_source_timestamp
            ):
                self.window.clear()
            self._last_source_timestamp = timestamp
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


def build_rf_hv_safety_checker(source: Mapping[str, Any]) -> SafetyChecker:
    raw = source["rf_safety"]
    safety = raw["safety"]
    reference = raw["reference"]
    channels = reference["channels"]
    cfg = SafetyConfig(
        hv_min_kv=float(safety["hv_min_kv"]),
        hv_max_kv=float(safety["hv_max_kv"]),
        hv_readback_tolerance_kv=float(safety["hv_readback_tolerance_kv"]),
        phase_limit_deg={
            str(key): float(value) for key, value in safety["phase_limit_deg"].items()
        },
        amplitude_ratio_limit_rel=float(safety["amplitude_ratio_limit_rel"]),
        feedback_amplitude_min_rel=float(safety["feedback_amplitude_min_rel"]),
        feedback_amplitude_max_rel=float(safety["feedback_amplitude_max_rel"]),
        require_valid_pv=bool(safety.get("require_valid_pv", True)),
        hold_on_fault=bool(safety.get("hold_on_fault", True)),
    )
    ref = SafetyReference(
        hv_kv=float(reference["hv_kv"]),
        channel_amplitudes={
            str(key): float(value["amplitude"]) for key, value in channels.items()
        },
        channel_phases={
            str(key): float(value["phase_deg"]) for key, value in channels.items()
        },
    )
    return SafetyChecker(cfg, ref, str(raw["default_feedback_channel"]))


def read_closed_loop_inputs(
    client: PVClient,
    source: Mapping[str, Any],
    sampled_at: float,
) -> tuple[dict[str, float], str]:
    keys = tuple(source["closed_loop_keys"])
    readings = client.read_many(keys)
    values: dict[str, float] = {}
    exempt_keys = frozenset(source.get("source_timestamp_exempt_keys", ()))
    check_timestamps = str(
        source.get("source_timestamp_policy", "enforce")
    ) != "ignore"
    for key in keys:
        value, issue = _reading_value(
            readings.get(key),
            sampled_at,
            float(source["stale_timeout_s"]),
            check_source_timestamp=check_timestamps and key not in exempt_keys,
        )
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
