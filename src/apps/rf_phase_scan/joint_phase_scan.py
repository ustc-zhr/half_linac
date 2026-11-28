"""Use corrector response ordering to identify the accelerating half of an RF scan."""

from __future__ import annotations

import math
import statistics
from contextlib import contextmanager
from dataclasses import dataclass, replace

from half_linac.src.apps.rf_phase_scan.acceleration_check import (
    AccelerationCheck,
    AccelerationCheckConfig,
    BeamResponseUnavailable,
    SETPOINT_TOLERANCE,
    _number,
    resolve_acceleration_check,
)
from half_linac.src.apps.rf_phase_scan.phase_energy_scan import phase_difference_deg
from half_linac.src.shared.machine_profile import resolve_channel


DEFAULT_CORRECTOR_SETTLE_TIME_S = 1.0
ACCELERATION_EDGE_MARGIN_DEG = 10.0
# Three robust standard deviations, using the median absolute deviation.
RESPONSE_SUM_MAD_MULTIPLIER = 3.0 * 1.4826


@dataclass(frozen=True)
class JointPhaseScanConfig:
    response: AccelerationCheckConfig
    minimum_transmission_sum: float
    high_sum_fraction: float
    optics_current_pvs: tuple[str, ...]
    optics_current_tolerance_a: float
    forward_power_pv: str
    maximum_relative_power_change: float

    def with_corrector_step(self, step, *, settle_time_s=None):
        step = _number(step, "Corrector step", minimum=SETPOINT_TOLERANCE)
        if settle_time_s is None:
            settle_time_s = self.response.settle_time_s
        settle_time_s = _number(settle_time_s, "Corrector settle time")
        if settle_time_s < 0:
            raise ValueError("Corrector settle time must be nonnegative.")
        corrector = self.response.corrector
        low, high = corrector.limits
        if 2 * step > high - low:
            raise ValueError(f"{corrector.element}: the two perturbations exceed the current limits.")
        corrector = replace(corrector, step=step, tolerance=min(corrector.tolerance, step / 4))
        response = replace(self.response, corrector=corrector, settle_time_s=settle_time_s)
        return replace(self, response=response)


def resolve_joint_phase_scan(context, stage, settings, spec):
    route = spec["phase_response"]
    conditions = spec["conditions"]
    if any(
        item["channel"] != "K1_total"
        or context.profile.get_element(item["element"]).kind != "quad"
        for item in conditions
    ):
        raise ValueError("A phase-response path requires horizontally defocusing quadrupoles.")
    fraction = _number(route["high_sum_fraction"], "high_sum_fraction", minimum=0)
    power_change = _number(
        route["maximum_relative_power_change"], "maximum_relative_power_change", minimum=0,
    )
    if fraction > 1 or power_change >= 1 or not 0 <= settings.readback_tolerance_deg < 90:
        raise ValueError("Invalid phase-response selection or readback tolerance.")
    response = resolve_acceleration_check(
        context, stage, settings,
        {
            **spec,
            **route,
            "samples_per_state": max(2, settings.samples_per_point),
            "settle_time_s": DEFAULT_CORRECTOR_SETTLE_TIME_S,
            "sample_timeout_s": 5.0 + settings.samples_per_point * settings.sample_interval_s,
        },
        fixed_amplitude=True,
    )
    return JointPhaseScanConfig(
        response=replace(response, corrector_readback_timeout_s=5.0),
        minimum_transmission_sum=_number(
            route["minimum_transmission_sum"], "minimum_transmission_sum", minimum=0,
        ),
        high_sum_fraction=fraction,
        optics_current_pvs=tuple(
            resolve_channel(context, item["element"], "current_readback")
            for item in conditions
        ),
        optics_current_tolerance_a=_number(
            route["optics_current_tolerance_a"], "optics_current_tolerance_a", minimum=0,
        ),
        forward_power_pv=resolve_channel(context, stage.rf_element, "kly_forward_power"),
        maximum_relative_power_change=power_change,
    )


class PhaseResponseMeasurement(AccelerationCheck):
    """Measure BPM response under the transmission runner's existing machine lock."""

    def __init__(self, *, route, check_cancelled, **options):
        super().__init__(config=route.response, applied_phase=0.0, recovery_path=None,
                         path=None, **options)
        self.route = route
        self.check_cancelled = check_cancelled

    def _check_stop(self):
        self.check_cancelled()

    def _read_optics(self):
        return {pv: self._read(pv).value for pv in self.route.optics_current_pvs}

    @contextmanager
    def open(self):
        self.client = self.client_factory()
        try:
            self._snapshot_actuators()
            self.amplitude = self.initial[self.config.amplitude]
            self.current = self.initial[self.config.corrector]
            step = self.config.corrector.step
            low, high = self.config.corrector.limits
            if not low <= self.current - step < self.current + step <= high:
                raise RuntimeError(f"{self.config.corrector.element}: perturbation exceeds current limits.")
            self.optics = self._read_optics()
            self.forward_power = self._read(self.route.forward_power_pv).value
            if self.amplitude <= 0 or self.forward_power <= 0:
                raise RuntimeError(f"{self.config.rf_element}: RF amplitude or forward power is zero.")
            yield {
                "initial_amplitude": self.amplitude,
                "initial_current_a": self.current,
                "initial_optics_currents_a": self.optics,
                "initial_forward_power_readback": self.forward_power,
            }
        finally:
            self.client.close()

    def _check_conditions(self):
        phase = super()._check_conditions()
        readbacks = self.result["condition_readbacks"]
        conditions = self.config.conditions
        for condition, reading in zip(conditions, readbacks[-len(conditions):]):
            if reading["value"] > 0:
                raise RuntimeError(f"{condition.element}: the horizontal optics are focusing.")
        optics = self._read_optics()
        self.result["optics_currents_a"].append(optics)
        for pv, current in optics.items():
            if abs(current - self.optics[pv]) > self.route.optics_current_tolerance_a:
                raise RuntimeError(f"{pv}: quadrupole current changed during the phase scan.")
        power = self._read(self.route.forward_power_pv).value
        self.result["forward_power_readbacks"].append(power)
        if abs(power / self.forward_power - 1) > self.route.maximum_relative_power_change:
            raise RuntimeError(f"{self.config.rf_element}: forward power changed during the phase scan.")
        return phase

    def _verify_phase(self, phase):
        self.phase_reference = phase
        self._check_conditions()
        self._verify_readback(self.config.amplitude, self.amplitude)
        self._verify_readback(self.config.corrector, self.current)

    def verify_phase(self, phase, record):
        record.update(condition_readbacks=[], optics_currents_a=[], forward_power_readbacks=[])
        self.result = record
        self._verify_phase(phase)

    def measure(self, phase, record):
        record.update(
            status="invalid", nominal_samples=[], groups=[], condition_readbacks=[],
            optics_currents_a=[], forward_power_readbacks=[], restore_errors={},
        )
        self.result = record
        self.touched.clear()
        try:
            self._verify_phase(phase)
            self._sample(record["nominal_samples"])
            signals = [sample["sum"]["value"] for sample in record["nominal_samples"]]
            record["nominal_sum"] = statistics.median(signals)
            for signal in signals:
                self._check_sum_stability(signal, record["nominal_sum"])
            slope = self._group(
                "Corrector response", self.amplitude, self.current, record["nominal_sum"],
                fixed_amplitude=True,
            )
            minus, plus = record["groups"][0]["points"]
            x_minus = [sample["x"]["value"] for sample in minus["samples"]]
            x_plus = [sample["x"]["value"] for sample in plus["samples"]]
            lower = min(x_plus) - max(x_minus)
            upper = max(x_plus) - min(x_minus)
            if lower <= 0 <= upper:
                raise BeamResponseUnavailable("The corrector response is indistinguishable from BPM noise.")
            min_current = (minus["current"], minus["current_after"])
            max_current = (plus["current"], plus["current_after"])
            min_span = min(max_current) - max(min_current)
            max_span = max(max_current) - min(min_current)
            low_x, high_x = sorted((abs(lower), abs(upper)))
            self._verify_phase(phase)
            record.update(
                status="valid", slope=slope, magnitude=abs(slope),
                magnitude_interval=(low_x / max_span, high_x / min_span),
            )
        except BeamResponseUnavailable as exc:
            record["reason"] = str(exc)
        finally:
            self._restore()
            if record["restore_errors"]:
                errors = ", ".join(record["restore_errors"].values())
                raise RuntimeError(f"Corrector restore failed: {errors}")


def _intersect_arc(intervals, center, half_width):
    """Intersect crest intervals on [0, 360] with an arc that can cross zero."""
    center %= 360
    clipped = []
    for low, high in intervals:
        for turn in (-360, 0, 360):
            start = max(low, center + turn - half_width)
            end = min(high, center + turn + half_width)
            if start <= end:
                clipped.append((start, end))
    return clipped


def _inside_arc(intervals, center, half_width):
    center %= 360
    return all(
        any(abs(low - center - turn) < half_width and abs(high - center - turn) < half_width
            for turn in (-360, 0, 360))
        for low, high in intervals
    )


def usable_phase_responses(points):
    """Exclude low BPM signals using the nominal SUM and pulse variation from this scan."""
    valid = [point for point in points if point["response"]["status"] == "valid"]
    quality = {"valid_response_points": len(valid), "usable_response_points": 0}
    if not valid:
        return valid, quality

    nominal_sums = [point["response"]["nominal_sum"] for point in valid]
    typical_sum = statistics.median(nominal_sums)
    phase_mad = statistics.median(abs(value - typical_sum) for value in nominal_sums)
    pulse_deviations = [
        abs(sample["sum"]["value"] - point["response"]["nominal_sum"])
        for point in valid for sample in point["response"]["nominal_samples"]
    ]
    pulse_mad = statistics.median(pulse_deviations)
    minimum_sum = max(0.0, typical_sum - RESPONSE_SUM_MAD_MULTIPLIER * max(phase_mad, pulse_mad))
    usable = [point for point in valid if point["response"]["nominal_sum"] >= minimum_sum]
    quality.update(
        usable_response_points=len(usable),
        response_sum_median=typical_sum,
        response_sum_minimum=minimum_sum,
    )
    return usable, quality


def select_joint_phase(points, reference, repeat, route):
    """Choose a high-transmission point with a resolved accelerating sign."""
    usable, quality = usable_phase_responses(points)
    for point in points:
        point["acceleration"] = "undetermined"
        point["response"].pop("comparison_interval", None)
    analysis = {"status": "undetermined", **quality}
    if len(usable) < 3:
        analysis["reason"] = "Fewer than three phase points have usable BPM responses."
        return None, analysis
    if repeat.get("status") != "valid":
        analysis["reason"] = f"Reference response could not be repeated. {repeat.get('reason', '')}"
        return None, analysis
    if reference not in usable or repeat["nominal_sum"] < analysis["response_sum_minimum"]:
        analysis["reason"] = "Reference BPM response SUM fell below the scan quality limit."
        return None, analysis

    reference_slope = reference["response"]["slope"]
    if any(point["response"]["slope"] * reference_slope <= 0 for point in usable) or (
        repeat["slope"] * reference_slope <= 0
    ):
        analysis["reason"] = "Corrector response changed polarity during the scan."
        return None, analysis
    drift = abs(repeat["magnitude"] - abs(reference_slope))
    analysis["reference_response_drift"] = drift
    phase_error = route.response.phase_tolerance_deg
    intervals = [(0.0, 360.0)]
    ordered_pairs = 0
    measured = []
    for point in usable:
        phase = point["command_phase_deg"]
        low, high = point["response"]["magnitude_interval"]
        # Include the observed change at the repeated reference in every comparison.
        limits = (max(0.0, low - drift), high + drift)
        point["response"]["comparison_interval"] = limits
        measured.append((phase, limits, math.cos(math.radians(phase)), math.sin(math.radians(phase))))
    for index, (phase, (low, high), x, y) in enumerate(measured):
        for other_phase, (other_low, other_high), other_x, other_y in measured[:index]:
            if abs(phase_difference_deg(phase, other_phase)) <= max(2 * phase_error, 1e-9):
                continue
            if high < other_low:
                dx, dy = x - other_x, y - other_y
            elif other_high < low:
                dx, dy = other_x - x, other_y - y
            else:
                continue
            # The higher-energy point has the larger cosine relative to the RF crest.
            center = math.degrees(math.atan2(dy, dx))
            intervals = _intersect_arc(intervals, center, 90 + phase_error)
            ordered_pairs += 1
            if not intervals:
                analysis["reason"] = "BPM response ordering is inconsistent with one RF crest."
                return None, analysis
    analysis.update(
        crest_intervals_deg=intervals,
        ordered_pairs=ordered_pairs,
        acceleration_edge_margin_deg=ACCELERATION_EDGE_MARGIN_DEG,
    )
    if not ordered_pairs:
        analysis["reason"] = "Phase-to-phase response differences are within measurement uncertainty."
        return None, analysis

    accelerating = []
    half_width = 90 - phase_error - ACCELERATION_EDGE_MARGIN_DEG
    for point in usable:
        phase = point["command_phase_deg"]
        if _inside_arc(intervals, phase, half_width):
            point["acceleration"] = "accelerating"
            accelerating.append(point)
        elif _inside_arc(intervals, phase + 180, half_width):
            point["acceleration"] = "decelerating"
    if not accelerating:
        analysis["reason"] = "No confirmed accelerating point has a usable BPM response."
        return None, analysis
    accelerating_peak = max(point["median_sum"] for point in accelerating)
    minimum_sum = max(route.minimum_transmission_sum, accelerating_peak * route.high_sum_fraction)
    analysis.update(accelerating_peak_sum=accelerating_peak, selection_minimum_sum=minimum_sum)
    candidates = [point for point in accelerating if point["median_sum"] >= minimum_sum]
    if not candidates:
        analysis["reason"] = "No confirmed accelerating point meets the transmission SUM limit."
        return None, analysis
    selected = min(
        candidates,
        key=lambda point: (
            point["offset_deg"] != 0.0, -point["median_sum"],
            abs(point["offset_deg"]), point["offset_deg"],
        ),
    )
    analysis["status"] = "accelerating"
    return selected, analysis
