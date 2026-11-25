"""Measure a local orbit response for RF gain checks and commissioning."""

from __future__ import annotations

import math
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from threading import Event

from half_linac.src.apps.rf_phase_scan.amp_scan import OUTPUT_CHANNEL, EpicsAmpClient
from half_linac.src.apps.rf_phase_scan.phase_energy_scan import phase_difference_deg
from half_linac.src.shared.machine_profile import (
    EnergyControlLock,
    require_workflow_write_allowed,
    resolve_channel,
    resolve_write_target,
)
from half_linac.src.shared.runtime_state import write_runtime_state


POLL_INTERVAL_S = 0.05
SETPOINT_TOLERANCE = 1e-6


@dataclass(frozen=True)
class Perturbation:
    element: str
    setpoint: str
    readback: str
    step: float
    tolerance: float
    limits: tuple[float, float]


@dataclass(frozen=True)
class ReadbackCondition:
    element: str
    channel: str
    pv: str
    limits: tuple[float, float] | None


@dataclass(frozen=True)
class AccelerationCheckConfig:
    rf_element: str
    bpm: str
    x_pv: str
    sum_pv: str
    phase_pv: str
    phase_readback_pv: str
    amplitude: Perturbation
    corrector: Perturbation
    conditions: tuple[ReadbackCondition, ...]
    samples_per_state: int
    minimum_sum: float | None
    maximum_relative_sum_change: float | None
    sample_timeout_s: float
    pair_tolerance_s: float | None
    settle_time_s: float
    minimum_response_difference: float | None
    maximum_baseline_drift: float | None
    phase_tolerance_deg: float
    readback_timeout_s: float
    capture_only: bool = False
    kly_forward_power_pv: str | None = None


def _number(value, name, *, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number.")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite.")
    if minimum is not None and value <= minimum:
        raise ValueError(f"{name} must be greater than {minimum}.")
    return float(value)


def _range(values, name):
    if not isinstance(values, (list, tuple)) or len(values) != 2:
        raise ValueError(f"{name} must contain a lower and an upper bound.")
    low, high = (_number(value, name) for value in values)
    if low >= high:
        raise ValueError(f"{name} must be increasing.")
    return low, high


def resolve_acceleration_check(context, stage, settings, spec, *, capture_only=False):
    """Resolve the approved diagnostic path independently of the SUM scan."""
    bpm = context.profile.get_element(spec["bpm"])
    corrector = context.profile.get_element(spec["corrector"])
    if bpm.kind != "bpm" or corrector.kind != "corr" or corrector.plane != "x":
        raise ValueError("The check requires a horizontal corrector and a position BPM.")
    # Profile order groups device kinds. The calibrated path supplies beamline order.

    def perturbation(element, channel, readback, name):
        config = spec[name]
        target = resolve_write_target(context, element, logical_channel=channel)
        if capture_only:
            limits = _range(
                (target.machine_limit.low, target.machine_limit.high), f"{name} machine limits"
            )
        else:
            limits = _range(config["window"], f"{name}.window")
        if not all(target.machine_limit.contains(value) for value in limits):
            raise ValueError(f"{name}.window is outside machine limits.")
        step = _number(config["step"], f"{name}.step", minimum=0)
        tolerance = _number(config["tolerance"], f"{name}.tolerance", minimum=0)
        if step <= max(2 * tolerance, SETPOINT_TOLERANCE):
            raise ValueError(f"{name}.step must exceed twice its readback tolerance.")
        return Perturbation(
            element, target.pv_name, resolve_channel(context, element, readback),
            step, tolerance, limits,
        )

    raw_conditions = spec["conditions"]
    if not isinstance(raw_conditions, list):
        raise ValueError("conditions must be a list of readbacks.")
    conditions = tuple(
        ReadbackCondition(
            item["element"], item["channel"],
            resolve_channel(context, item["element"], item["channel"]),
            None if item["channel"] == "K1_total" and "window" not in item
            else _range(item["window"], "conditions.window"),
        )
        for item in raw_conditions
    )
    samples = spec["samples_per_state"]
    if type(samples) is not int or samples < 2:
        raise ValueError("samples_per_state must be an integer of at least 2.")
    settle = _number(spec["settle_time_s"], "settle_time_s")
    if settle < 0 or settings.readback_timeout_s <= 0:
        raise ValueError("Settle time must be nonnegative and readback timeout positive.")
    def threshold(name):
        return None if capture_only else _number(spec[name], name, minimum=0)

    sum_change = threshold("maximum_relative_sum_change")
    if sum_change is not None and sum_change >= 1:
        raise ValueError("maximum_relative_sum_change must be less than 1.")
    power_pv = None
    rf = context.profile.get_element(stage.rf_element)
    if capture_only and context.control_backend.name in rf.channels.get(OUTPUT_CHANNEL, {}):
        power_pv = resolve_channel(context, stage.rf_element, OUTPUT_CHANNEL)
    config = AccelerationCheckConfig(
        rf_element=stage.rf_element,
        bpm=bpm.id,
        x_pv=resolve_channel(context, bpm.id, "x"),
        sum_pv=resolve_channel(context, bpm.id, "sum"),
        phase_pv=stage.phase_pv,
        phase_readback_pv=stage.phase_readback_pv,
        amplitude=perturbation(stage.rf_element, "amplitude_set", "amplitude_readback", "amplitude"),
        corrector=perturbation(corrector.id, "current_set", "current_readback", "current"),
        conditions=conditions,
        samples_per_state=samples,
        minimum_sum=threshold("minimum_sum"),
        maximum_relative_sum_change=sum_change,
        sample_timeout_s=_number(spec["sample_timeout_s"], "sample_timeout_s", minimum=0),
        pair_tolerance_s=threshold("pair_tolerance_s"),
        settle_time_s=settle,
        minimum_response_difference=threshold("minimum_response_difference"),
        maximum_baseline_drift=threshold("maximum_baseline_drift"),
        phase_tolerance_deg=settings.readback_tolerance_deg,
        readback_timeout_s=settings.readback_timeout_s,
        capture_only=capture_only,
        kly_forward_power_pv=power_pv,
    )
    signals = (
        config.amplitude.setpoint, config.amplitude.readback, config.corrector.setpoint,
        config.corrector.readback, config.phase_pv, config.phase_readback_pv,
        config.x_pv, config.sum_pv,
    )
    if power_pv is not None:
        signals += (power_pv,)
    if len(set(signals)) != len(signals) or any(
        condition.pv in signals for condition in conditions
    ):
        raise ValueError("Check controls, BPM signals, and read-only conditions must be distinct.")
    return config


def resolve_acceleration_capture(context, stage, settings, spec, *, amplitude_step, current_step):
    """Use operator-selected perturbations to gather unclassified commissioning data."""
    if spec.get("capture_enabled") is not True:
        raise ValueError(f"{stage.rf_element}: test data capture is disabled.")
    capture = {
        **spec,
        "amplitude": {"step": amplitude_step, "tolerance": amplitude_step / 4},
        "current": {"step": current_step, "tolerance": current_step / 4},
        "samples_per_state": settings.samples_per_point,
        "settle_time_s": settings.settle_time_s,
        "sample_timeout_s": 5.0 + settings.samples_per_point * settings.sample_interval_s,
    }
    return resolve_acceleration_check(context, stage, settings, capture, capture_only=True)


def discover_acceleration_checks(context, recovery, stages, settings):
    configs, unavailable = {}, {}
    for spec in recovery["stages"]:
        rf_element = spec["rf_element"]
        check = spec.get("acceleration_check", {})
        if not isinstance(check, dict) or not isinstance(check.get("enabled", False), bool):
            unavailable[rf_element] = "Invalid acceleration check configuration."
        elif check.get("enabled") is not True:
            unavailable[rf_element] = "Measurement path has not been enabled and calibrated."
        else:
            try:
                configs[rf_element] = resolve_acceleration_check(
                    context, stages[rf_element], settings, check
                )
            except (KeyError, ValueError, TypeError, OverflowError) as exc:
                unavailable[rf_element] = f"Invalid acceleration check configuration: {exc}"
    return configs, unavailable


def classify_response(baseline, lower, repeat, *, threshold, maximum_drift):
    if min(abs(baseline), abs(lower), abs(repeat)) == 0 or not (
        (baseline > 0) == (lower > 0) == (repeat > 0)
    ):
        return "undetermined", "The orbit response vanished or changed polarity."
    if abs(abs(baseline) - abs(repeat)) > maximum_drift:
        return "undetermined", "The nominal-amplitude response did not reproduce."
    differences = abs(lower) - abs(baseline), abs(lower) - abs(repeat)
    if min(differences) > threshold:
        return "accelerating", "The orbit response fell at the higher LLRF AMP setting."
    if max(differences) < -threshold:
        return "decelerating", "The orbit response rose at the higher LLRF AMP setting."
    return "undetermined", "The response difference was not resolved in both comparisons."


class AccelerationCheck:
    def __init__(self, *, context, config, applied_phase, recovery_path, path, on_state,
                 client_factory=EpicsAmpClient, lock_factory=EnergyControlLock.for_machine,
                 monotonic=time.monotonic, sleep=time.sleep):
        self.context = context
        self.config = config
        self.phase_reference = (
            None if config.capture_only and applied_phase is None
            else _number(applied_phase, "Applied phase")
        )
        self.path = Path(path)
        self.on_state = on_state
        self.client_factory = client_factory
        self.lock_factory = lock_factory
        self.monotonic = monotonic
        self.sleep = sleep
        self.stopping = Event()
        self.initial = {}
        self.expected = {}
        self.touched = set()
        self.result = {
            "schema_version": "rf_acceleration_check_v1",
            "machine_id": context.machine.id,
            "backend": context.control_backend.name,
            "rf_element": config.rf_element,
            "recovery_path": str(recovery_path) if recovery_path is not None else None,
            "json_path": str(path),
            "applied_phase_deg": self.phase_reference,
            "mode": "capture" if config.capture_only else "check",
            "configuration": asdict(config),
            "status": "undetermined",
            "reason": "",
            "groups": [],
            "condition_readbacks": [],
            "restore_errors": {},
        }

    def stop(self):
        self.stopping.set()

    def _check_stop(self):
        if self.stopping.is_set():
            raise RuntimeError("Check stopped by operator.")

    def _read(self, pv, **kwargs):
        reading = self.client.read(pv, **kwargs)
        if not math.isfinite(reading.value):
            raise RuntimeError(f"{pv}: invalid reading.")
        return reading

    def _owned(self, actuator):
        actual = self._read(actuator.setpoint).value
        if not any(
            math.isclose(actual, expected, rel_tol=0, abs_tol=SETPOINT_TOLERANCE)
            for expected in self.expected[actuator]
        ):
            raise RuntimeError(f"{actuator.element}: setpoint changed outside this check ({actual:g}).")
        return actual

    def _readback(self, actuator, target, *, restoring=False):
        deadline = self.monotonic() + self.config.readback_timeout_s
        while True:
            if not restoring:
                self._check_stop()
            actual = self._read(actuator.readback).value
            if abs(actual - target) <= actuator.tolerance:
                self._owned(actuator)
                return actual
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                raise RuntimeError(f"{actuator.element}: readback did not reach {target:g}.")
            self.sleep(min(POLL_INTERVAL_S, remaining))

    def _move(self, actuator, target, *, restoring=False):
        actual = self._owned(actuator)
        if not math.isclose(actual, target, rel_tol=0, abs_tol=SETPOINT_TOLERANCE):
            if not restoring:
                self._check_stop()
            self.expected[actuator] = (actual, target)
            self.touched.add(actuator)
            self.client.write(actuator.setpoint, target)
        self.expected[actuator] = (target,)
        return self._readback(actuator, target, restoring=restoring)

    def _verify_readback(self, actuator, target):
        self._owned(actuator)
        actual = self._read(actuator.readback).value
        if (
            abs(actual - target) > actuator.tolerance
            or not actuator.limits[0] <= actual <= actuator.limits[1]
        ):
            raise RuntimeError(f"{actuator.element}: readback drifted outside the calibrated setting.")
        return actual

    def _check_conditions(self):
        self._check_stop()
        config = self.config
        values = [self._read(pv).value for pv in (config.phase_pv, config.phase_readback_pv)]
        if self.phase_reference is None:
            self.phase_reference = values[0]
        if any(abs(phase_difference_deg(value, self.phase_reference)) > config.phase_tolerance_deg
               for value in values):
            raise RuntimeError(f"{config.rf_element}: phase differs from the reference phase.")
        for condition in config.conditions:
            reading = self._read(condition.pv)
            self.result["condition_readbacks"].append({
                "element": condition.element,
                "channel": condition.channel,
                **asdict(reading),
            })
            limits = condition.limits
            if limits is not None and not limits[0] <= reading.value <= limits[1]:
                raise RuntimeError(f"{condition.element} {condition.channel} is outside the calibrated range.")
        return values[1]

    def _settle(self):
        deadline = self.monotonic() + self.config.settle_time_s
        while (remaining := deadline - self.monotonic()) > 0:
            self._check_stop()
            self.sleep(min(POLL_INTERVAL_S, remaining))

    def _sample_buffer(self):
        return {"x": [], "sum": []} if self.config.capture_only else []

    def _sample(self, samples, reference_sum=None, power=None):
        config = self.config
        pvs = config.x_pv, config.sum_pv
        deadline = self.monotonic() + config.sample_timeout_s
        watermark = []
        for pv in pvs:
            self._check_stop()
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                raise RuntimeError(f"{config.bpm}: BPM read timed out.")
            watermark.append(self._read(pv, timeout=min(0.5, remaining)).timestamp)
        if any(value is None or not math.isfinite(value) for value in watermark):
            raise RuntimeError(f"{config.bpm}: BPM source timestamps unavailable.")
        while True:
            self._check_stop()
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                qualifier = "fresh" if config.capture_only else "fresh, paired"
                raise RuntimeError(f"{config.bpm}: insufficient {qualifier} X and SUM samples.")
            readings = tuple(self._read(pv, timeout=min(0.5, remaining)) for pv in pvs)
            if config.capture_only:
                for index, (channel, reading) in enumerate(zip(("x", "sum"), readings)):
                    if (
                        len(samples[channel]) < config.samples_per_state
                        and reading.timestamp is not None and math.isfinite(reading.timestamp)
                        and reading.timestamp > watermark[index]
                    ):
                        samples[channel].append(asdict(reading))
                        watermark[index] = reading.timestamp
                self._capture_forward_power(power, timeout=min(0.5, remaining))
                if all(len(values) == config.samples_per_state for values in samples.values()):
                    return
                self.sleep(min(POLL_INTERVAL_S, remaining))
                continue
            x, signal = readings
            if (
                x.timestamp is not None and signal.timestamp is not None
                and math.isfinite(x.timestamp) and math.isfinite(signal.timestamp)
                and x.timestamp > watermark[0] and signal.timestamp > watermark[1]
                and abs(x.timestamp - signal.timestamp) <= config.pair_tolerance_s
            ):
                watermark = [x.timestamp, signal.timestamp]
                samples.append({"x": asdict(x), "sum": asdict(signal)})
                if signal.value < config.minimum_sum:
                    raise RuntimeError(f"{config.bpm}: beam SUM is below the minimum.")
                if reference_sum is not None:
                    self._check_sum_stability(signal.value, reference_sum)
            if len(samples) == config.samples_per_state:
                return
            self.sleep(min(POLL_INTERVAL_S, remaining))

    def _check_sum_stability(self, signal, reference):
        if abs(signal / reference - 1) > self.config.maximum_relative_sum_change:
            raise RuntimeError(f"{self.config.bpm}: beam SUM moved outside its stability range.")

    def _capture_forward_power(self, record, *, timeout):
        if record is None or record["error"] or len(record["samples"]) >= self.config.samples_per_state:
            return
        try:
            reading = self._read(self.config.kly_forward_power_pv, timeout=timeout)
        except Exception as exc:
            record["error"] = str(exc) or type(exc).__name__
            return
        stamp = reading.timestamp
        if stamp is None or not math.isfinite(stamp):
            record["error"] = "KLY power source timestamp unavailable."
            return
        if record["after_timestamp"] is None:
            record["after_timestamp"] = stamp
            return
        samples = record["samples"]
        previous = samples[-1]["timestamp"] if samples else record["after_timestamp"]
        if stamp > previous:
            samples.append(asdict(reading))

    def _compare_forward_power(self):
        if self.config.kly_forward_power_pv is None:
            return
        records = [group["kly_forward_power"] for group in self.result["groups"]]
        if any(len(record["samples"]) < self.config.samples_per_state for record in records):
            self.result["kly_forward_power_check"] = "insufficient_samples"
            return
        before, lower, after = ([sample["value"] for sample in record["samples"]] for record in records)
        nominal = before + after
        if max(lower) < min(nominal):
            outcome = "lower_at_low_amp"
        elif min(lower) > max(nominal):
            outcome = "higher_at_low_amp"
        else:
            outcome = "overlap"
        self.result["kly_forward_power_check"] = outcome

    def _group(self, name, amplitude, current, reference_sum):
        config = self.config
        self.on_state(f"{config.rf_element} · {name}")
        self._check_conditions()
        self._owned(config.corrector)
        self._move(config.amplitude, amplitude)
        self._settle()
        self._check_conditions()
        amp_readback = self._verify_readback(config.amplitude, amplitude)
        group = {"name": name, "amplitude": amp_readback, "points": []}
        self.result["groups"].append(group)
        power = None
        if config.kly_forward_power_pv is not None:
            power = {"after_timestamp": None, "samples": [], "error": ""}
            group["kly_forward_power"] = power
            self._capture_forward_power(power, timeout=0.5)
        for target in (current - config.corrector.step, current + config.corrector.step):
            self._check_conditions()
            self._verify_readback(config.amplitude, amplitude)
            self._move(config.corrector, target)
            self._settle()
            self._check_conditions()
            actual = self._verify_readback(config.corrector, target)
            point = {"current_setpoint": target, "current": actual, "samples": self._sample_buffer()}
            group["points"].append(point)
            self._sample(point["samples"], reference_sum, power)
            self._check_conditions()
            self._verify_readback(config.amplitude, amplitude)
            self._verify_readback(config.corrector, target)
            positions = (
                point["samples"]["x"] if config.capture_only
                else [sample["x"] for sample in point["samples"]]
            )
            point["median_x"] = statistics.median(reading["value"] for reading in positions)
        self._move(config.corrector, current)
        if power is not None:
            readings = [sample["value"] for sample in power["samples"]]
            power["median"] = statistics.median(readings) if readings else None
        minus, plus = group["points"]
        slope = (plus["median_x"] - minus["median_x"]) / (plus["current"] - minus["current"])
        if not math.isfinite(slope):
            raise RuntimeError("The orbit response is not finite.")
        group["slope"] = slope
        return slope

    def _measure(self):
        config = self.config
        self.result["measured_phase_deg"] = self._check_conditions()
        for actuator in (config.amplitude, config.corrector):
            self.initial[actuator] = self._read(actuator.setpoint).value
            self.expected[actuator] = (self.initial[actuator],)
            self._readback(actuator, self.initial[actuator])
        amplitude, current = (self.initial[item] for item in (config.amplitude, config.corrector))
        lower = amplitude - config.amplitude.step
        self.result.update(
            initial_amplitude=amplitude, initial_current=current,
            preflight_samples=self._sample_buffer(),
        )
        for actuator, values in (
            (config.amplitude, (lower, amplitude)),
            (config.corrector, (current - config.corrector.step, current + config.corrector.step)),
        ):
            if not all(actuator.limits[0] <= value <= actuator.limits[1] for value in values):
                raise RuntimeError(f"{actuator.element}: perturbation exceeds its calibrated window.")
        self._sample(self.result["preflight_samples"])
        reference_sum = None
        if not config.capture_only:
            signals = [sample["sum"]["value"] for sample in self.result["preflight_samples"]]
            reference_sum = statistics.median(signals)
            for signal in signals:
                self._check_sum_stability(signal, reference_sum)
        baseline = self._group("Baseline", amplitude, current, reference_sum)
        lower_response = self._group("Lower LLRF AMP", lower, current, reference_sum)
        repeat = self._group("Baseline repeat", amplitude, current, reference_sum)
        self.result["measured_phase_deg"] = self._check_conditions()
        if config.capture_only:
            self._compare_forward_power()
            return
        self.result["status"], self.result["reason"] = classify_response(
            baseline, lower_response, repeat, threshold=config.minimum_response_difference,
            maximum_drift=config.maximum_baseline_drift,
        )

    def _restore(self):
        if self.touched:
            self.on_state(f"{self.config.rf_element} · restoring settings")
        for actuator in (self.config.corrector, self.config.amplitude):
            if actuator in self.touched:
                try:
                    self._move(actuator, self.initial[actuator], restoring=True)
                except Exception as exc:
                    self.result["restore_errors"][actuator.element] = str(exc)
        self.result["restore_status"] = (
            "failed" if self.result["restore_errors"] else "verified" if self.touched else "not_needed"
        )
        if self.result["restore_errors"]:
            self.result["status"] = "undetermined"
            self.result["reason"] = "Device settings could not be restored."

    def run(self):
        try:
            require_workflow_write_allowed(self.context, "rf_phase_scan", "Acceleration Check")
            with self.lock_factory(
                self.context.machine.id, {"app": "rf_phase_scan", "operation": "Acceleration Check"}
            ):
                self._check_stop()
                self.client = self.client_factory()
                try:
                    self._measure()
                finally:
                    try:
                        self._restore()
                        if self.result["status"] != "undetermined":
                            self.result["measured_phase_deg"] = self._check_conditions()
                    finally:
                        self.client.close()
                if self.config.capture_only and not self.result["restore_errors"]:
                    self.result.update(capture_completed=True, reason="Three test data groups captured.")
        except Exception as exc:
            self.result.update(status="undetermined", reason=str(exc) or type(exc).__name__)
        if self.config.capture_only:
            self.result.setdefault("capture_completed", False)
        self.result.setdefault("restore_status", "not_needed")
        self.result["finished_at"] = datetime.now().astimezone().isoformat(timespec="milliseconds")
        write_runtime_state(self.path, self.result)
        return self.result
