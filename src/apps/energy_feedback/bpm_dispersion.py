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


CALIBRATION_FIELDS = (
    "a",
    "b",
    "response_q_mm_per_kv",
    "fct_min",
    "q_action_range_mm",
    "bpm05_max_offset_mm",
    "quadrupole_max_delta_a",
)


def calibration_issue(calibration: Mapping[str, Any]) -> str:
    if calibration.get("ready") is not True:
        return "FIR calibration is not marked ready"
    version = str(calibration.get("version", "")).strip()
    if not version:
        return "FIR calibration version is missing"
    for key in CALIBRATION_FIELDS:
        try:
            value = float(calibration.get(key))
        except (TypeError, ValueError):
            return f"FIR calibration {key} is missing"
        if not math.isfinite(value):
            return f"FIR calibration {key} is not finite"
    if float(calibration["response_q_mm_per_kv"]) == 0:
        return "FIR calibration response_q_mm_per_kv must be non-zero"
    for key in (
        "fct_min",
        "q_action_range_mm",
        "bpm05_max_offset_mm",
        "quadrupole_max_delta_a",
    ):
        if float(calibration[key]) <= 0:
            return f"FIR calibration {key} must be positive"
    return ""


@dataclass(frozen=True)
class FirSessionReference:
    target_q_mm: float
    bpm05_x_mm: float
    fct: float
    quadrupoles_a: Mapping[str, float]
    window_sequence: int


def capture_session_reference(
    target_q_mm: float,
    aggregates: Mapping[str, float],
    quadrupole_ids: tuple[str, ...],
    *,
    window_sequence: int,
) -> FirSessionReference:
    required = ["bpm.BPM05.x", "fct", *(f"quad.{item}" for item in quadrupole_ids)]
    missing = [
        key
        for key in required
        if key not in aggregates or not math.isfinite(aggregates[key])
    ]
    if missing:
        raise ValueError(f"session reference fields missing: {missing}")
    return FirSessionReference(
        target_q_mm=float(target_q_mm),
        bpm05_x_mm=float(aggregates["bpm.BPM05.x"]),
        fct=float(aggregates["fct"]),
        quadrupoles_a={
            item: float(aggregates[f"quad.{item}"]) for item in quadrupole_ids
        },
        window_sequence=int(window_sequence),
    )


def check_session_guards(
    aggregates: Mapping[str, float],
    reference: FirSessionReference,
    calibration: Mapping[str, Any],
) -> str:
    issue = calibration_issue(calibration)
    if issue:
        return issue
    fct = aggregates.get("fct")
    if fct is None or not math.isfinite(fct) or abs(fct) < float(calibration["fct_min"]):
        return "FCT beam-present threshold not met"
    bpm05 = aggregates.get("bpm.BPM05.x")
    if bpm05 is None or not math.isfinite(bpm05):
        return "BPM05 exit orbit is unavailable"
    if abs(bpm05 - reference.bpm05_x_mm) > float(calibration["bpm05_max_offset_mm"]):
        return "BPM05 exit orbit offset exceeds limit"
    tolerance = float(calibration["quadrupole_max_delta_a"])
    for quad_id, initial in reference.quadrupoles_a.items():
        current = aggregates.get(f"quad.{quad_id}")
        if current is None or not math.isfinite(current):
            return f"{quad_id} readback is unavailable"
        if abs(current - initial) > tolerance:
            return f"{quad_id} changed beyond session tolerance"
    return ""


class BpmDispersionProvider:
    """Compute q = BPM04.x - a*BPM02.x - b*BPM03.x in millimetres."""

    def __init__(self, client: PVClient, source: Mapping[str, Any]) -> None:
        self.client = client
        self.source = source
        self.stale_timeout_s = float(source["stale_timeout_s"])
        self.max_timestamp_skew_s = float(source["max_timestamp_skew_s"])
        self.calibration = dict(source["calibration"])
        self.runtime_metadata = {
            "source_id": source["id"],
            "observable": dict(source["observable"]),
            "geometry": dict(source["geometry"]),
            "calibration": dict(source["calibration"]),
            "resolved_signals": dict(source["resolved_signals"]),
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
        for key in self.required_keys:
            reading = readings.get(key)
            value, issue = _reading_value(reading, sampled_at, self.stale_timeout_s)
            diagnostics[key] = value
            timestamps[key] = reading.source_timestamp if reading else None
            if issue:
                errors[key] = issue

        issue = next(iter(errors.values()), "")
        bpm_time_keys = tuple(self.source["bpm_signal_keys"]) + (self.source["fct_key"],)
        valid_times = [timestamps[key] for key in bpm_time_keys if timestamps.get(key) is not None]
        if not issue and len(valid_times) == len(bpm_time_keys):
            skew = max(valid_times) - min(valid_times)
            diagnostics["bpm_fct_timestamp_skew_s"] = skew
            if skew > self.max_timestamp_skew_s:
                issue = f"BPM/FCT timestamp skew {skew:.3f} s exceeds limit"

        calibration_problem = calibration_issue(self.calibration)
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
        if (
            not issue
            and abs(q_value) >= float(self.calibration["q_action_range_mm"])
        ):
            issue = "FIR q exceeds action range"
        if (
            not issue
            and abs(float(diagnostics[self.source["fct_key"]]))
            < float(self.calibration["fct_min"])
        ):
            issue = "FCT beam-present threshold not met"

        source_times = [item for item in valid_times if item is not None]
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
                issue or "FIR BPM-dispersion sample valid",
                connected=not any("disconnected" in item for item in errors.values()),
                stale=any("stale" in item for item in errors.values()),
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
) -> tuple[float | None, str]:
    if reading is None:
        return None, "result missing"
    if not reading.connected:
        return None, f"disconnected: {reading.error or 'not connected'}"
    if reading.error:
        return None, reading.error
    if reading.value is None or not math.isfinite(reading.value):
        return None, "not finite"
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
            self.latest_window.aggregates,
            tuple(self.source["quadrupole_ids"]),
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


def rf_hv_aggregate(
    values: Mapping[str, float],
    source: Mapping[str, Any],
) -> dict[str, float]:
    return {
        name: float(values[key])
        for name, key in source["guard_keys"].items()
        if key in values
    }
