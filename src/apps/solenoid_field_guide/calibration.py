from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from half_linac.src.shared.machine_profile import MachineProfile, MachineProfileError, load_profile
from half_linac.src.shared.machine_profile.limits import LimitRange


class CalibrationError(ValueError):
    pass


@dataclass(frozen=True)
class MagnetCalibration:
    element_id: str
    serial: str
    test_date: str
    source: str
    currents: tuple[float, ...]
    peak_fields: tuple[float, ...]
    reference_current: float
    reference_peak_field: float
    reference_integral_field: float
    machine_current_limit: LimitRange = LimitRange()
    design_peak_field: float | None = None

    @property
    def current_range(self) -> tuple[float, float]:
        return self.currents[0], self.currents[-1]

    @property
    def peak_range(self) -> tuple[float, float]:
        return self.peak_fields[0], self.peak_fields[-1]

    def peak_from_current(self, current: float) -> float:
        return _interpolate(current, self.currents, self.peak_fields, "current")

    def current_from_peak(self, peak_field: float) -> float:
        return _interpolate(peak_field, self.peak_fields, self.currents, "peak field")

    def integral_from_current(self, current: float) -> float:
        peak = self.peak_from_current(current)
        return self.reference_integral_field * peak / self.reference_peak_field

    def integral_from_peak(self, peak_field: float) -> float:
        if peak_field <= 0:
            raise CalibrationError("Peak field must be greater than zero.")
        if not self.peak_range[0] <= peak_field <= self.peak_range[1]:
            raise CalibrationError(
                f"Peak field {peak_field:g} is outside calibrated range "
                f"[{self.peak_range[0]:g}, {self.peak_range[1]:g}]."
            )
        return self.reference_integral_field * peak_field / self.reference_peak_field

    @property
    def design_integral_field(self) -> float:
        if self.design_peak_field is None:
            raise CalibrationError(f"{self.element_id}: no design peak field is configured.")
        return self.integral_from_peak(self.design_peak_field)

    def current_from_integral(self, integral_field: float) -> float:
        peak = integral_field * self.reference_peak_field / self.reference_integral_field
        return self.current_from_peak(peak)


@dataclass(frozen=True)
class MagnetRecommendation:
    element_id: str
    current: float
    peak_field: float
    integral_field: float


@dataclass(frozen=True)
class CalibrationCatalog:
    machine_id: str
    calibrations: dict[str, MagnetCalibration]


def load_calibrations(
    path: str | Path | None = None,
    *,
    profile: MachineProfile | None = None,
) -> CalibrationCatalog:
    path = Path(path) if path else default_calibration_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationError(f"Cannot load calibration file {path}: {exc}") from exc
    machine_id = str(payload.get("machine", "")).strip()
    if not machine_id:
        raise CalibrationError("Calibration file must identify its machine.")
    if payload.get("kind") != "solenoid":
        raise CalibrationError("This calibration file must have kind='solenoid'.")
    convention = payload.get("field_convention")
    if not isinstance(convention, dict) or convention.get("current_unit") != "A" or convention.get("peak_field_unit") != "T" or convention.get("integral_field_unit") != "T*m":
        raise CalibrationError("Solenoid calibration units must be A, T, and T*m.")
    profile = profile or load_profile(machine_id)
    if profile.machine.id != machine_id:
        raise CalibrationError(
            f"Calibration machine {machine_id!r} does not match profile {profile.machine.id!r}."
        )
    raw_calibrations = payload.get("calibrations")
    if not isinstance(raw_calibrations, list) or not raw_calibrations:
        raise CalibrationError("Calibration file must contain a non-empty calibrations list.")
    calibrations = {}
    for raw in raw_calibrations:
        element_id = str(raw.get("element_id", "")).strip()
        if not element_id or element_id in calibrations:
            raise CalibrationError(f"Invalid or duplicate calibration element_id: {element_id!r}.")
        calibrations[element_id] = _parse_calibration(profile, element_id, raw)
    return CalibrationCatalog(machine_id, calibrations)


def default_calibration_path() -> Path:
    return Path(__file__).resolve().parents[3] / "configs/machines/half/calibrations/solenoids.json"


def recommend_single(calibration: MagnetCalibration, target: float, quantity: str) -> MagnetRecommendation:
    if target <= 0:
        raise CalibrationError("Target field must be greater than zero.")
    if quantity == "integral":
        current = calibration.current_from_integral(target)
    elif quantity == "peak":
        current = calibration.current_from_peak(target)
    else:
        raise CalibrationError(f"Unknown target quantity: {quantity}")
    if not calibration.machine_current_limit.contains(current):
        raise CalibrationError(
            f"Recommended current {current:g} A is outside machine limit "
            f"{calibration.machine_current_limit.describe()}."
        )
    peak = calibration.peak_from_current(current)
    return MagnetRecommendation(calibration.element_id, current, peak, calibration.integral_from_current(current))


def _parse_calibration(profile: MachineProfile, element_id: str, raw: Any) -> MagnetCalibration:
    if not isinstance(raw, dict):
        raise CalibrationError(f"{element_id}: calibration record must be an object.")
    try:
        element = profile.get_element(element_id)
    except MachineProfileError as exc:
        raise CalibrationError(str(exc)) from exc
    if element.kind != "solenoid":
        raise CalibrationError(f"{element_id}: calibrated element must be a solenoid.")
    curve = raw.get("curve")
    if not isinstance(curve, dict) or curve.get("input_quantity") != "current" or curve.get("output_quantity") != "peak_field":
        raise CalibrationError(f"{element_id}: curve must map current to peak_field.")
    points = curve.get("points")
    if not isinstance(points, list) or len(points) < 2:
        raise CalibrationError(f"{element_id}: at least two calibration points are required.")
    pairs = []
    for point in points:
        if not isinstance(point, list) or len(point) != 2:
            raise CalibrationError(f"{element_id}: points must be [current, peak_field].")
        pairs.append((float(point[0]), abs(float(point[1]))))
    pairs.sort()
    currents = tuple(pair[0] for pair in pairs)
    fields = tuple(pair[1] for pair in pairs)
    if any(currents[i] >= currents[i + 1] for i in range(len(currents) - 1)):
        raise CalibrationError(f"{element_id}: current points must be strictly increasing.")
    if any(fields[i] >= fields[i + 1] for i in range(len(fields) - 1)):
        raise CalibrationError(f"{element_id}: peak field points must be strictly increasing.")
    reference = raw.get("reference")
    measurement = raw.get("measurement", {})
    design = raw.get("design", {})
    if not isinstance(reference, dict):
        raise CalibrationError(f"{element_id}: reference must be an object.")
    ref_current = float(reference["current"])
    ref_peak = abs(float(reference.get("peak_field", _interpolate(ref_current, currents, fields, "reference current"))))
    ref_integral = abs(float(reference["integral_field"]))
    if ref_peak <= 0 or ref_integral <= 0:
        raise CalibrationError(f"{element_id}: reference fields must be positive.")
    if not currents[0] <= ref_current <= currents[-1]:
        raise CalibrationError(f"{element_id}: reference current is outside calibration range.")
    machine_limit = LimitRange.from_mapping(element.limits_for("current_set"))
    design_peak = design.get("peak_field") if isinstance(design, dict) else None
    if design_peak is not None and not fields[0] <= float(design_peak) <= fields[-1]:
        raise CalibrationError(f"{element_id}: design peak field is outside calibration range.")
    return MagnetCalibration(
        element_id,
        str(measurement.get("serial", "")),
        str(measurement.get("date", "")),
        str(measurement.get("source", "")),
        currents,
        fields,
        ref_current,
        ref_peak,
        ref_integral,
        machine_limit,
        None if design_peak is None else float(design_peak),
    )


def _interpolate(value: float, x: tuple[float, ...], y: tuple[float, ...], label: str) -> float:
    value = float(value)
    if not x[0] <= value <= x[-1]:
        raise CalibrationError(f"{label.title()} {value:g} is outside calibrated range [{x[0]:g}, {x[-1]:g}].")
    for index in range(len(x) - 1):
        if value <= x[index + 1]:
            fraction = (value - x[index]) / (x[index + 1] - x[index])
            return y[index] + fraction * (y[index + 1] - y[index])
    return y[-1]
