from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True)
class EnergyKnobCalibrationFit:
    slope_delta_per_actuator: float
    intercept_delta: float
    actuator_per_delta: float
    r_squared: float
    n_samples: int
    kind: str = "linear"
    order: int = 1
    baseline_actuator: float | None = None
    coefficients: tuple[float, ...] = ()

    def as_dict(self) -> dict[str, float | int | str | list[float] | None]:
        payload: dict[str, float | int | str | list[float] | None] = {
            "kind": self.kind,
            "order": self.order,
            "slope_delta_per_actuator": self.slope_delta_per_actuator,
            "intercept_delta": self.intercept_delta,
            "actuator_per_delta": self.actuator_per_delta,
            "r_squared": self.r_squared,
            "n_samples": self.n_samples,
        }
        if self.baseline_actuator is not None:
            payload["baseline_actuator"] = self.baseline_actuator
        if self.coefficients:
            payload["coefficients"] = list(self.coefficients)
        return payload

    @property
    def slope_delta_per_phase(self) -> float:
        """Legacy name retained for callers using phase calibration."""

        return self.slope_delta_per_actuator

    @property
    def phase_per_delta(self) -> float:
        """Legacy name retained for callers using phase calibration."""

        return self.actuator_per_delta


PhaseCalibrationFit = EnergyKnobCalibrationFit


def is_direct_delta_actuator(actuator: str) -> bool:
    normalized = str(actuator).strip().lower().replace("-", "_").replace(" ", "_")
    return normalized in {"delta", "delta_p_over_p", "dp_over_p", "momentum_delta"}


def calibration_actuator_per_delta(calibration: dict[str, Any]) -> float | None:
    """Return the physical actuator change per unit dp/p.

    ``phase_per_delta`` is accepted as a compatibility alias for existing
    profiles. New profiles should use ``actuator_per_delta``.
    """

    kind = str(calibration.get("kind", "linear")).strip().lower()
    if kind in {"polynomial", "polynomial_relative", "quadratic_relative"}:
        coefficients = _calibration_coefficients(calibration)
        if len(coefficients) < 2:
            raise ValueError("polynomial calibration requires coefficients")
        slope = float(coefficients[-2])
        if not np.isfinite(slope) or slope == 0:
            raise ValueError("polynomial calibration baseline slope must be finite and non-zero")
        return float(1.0 / slope)
    if kind not in {"linear", "linear_relative"}:
        raise ValueError(f"Unsupported calibration kind: {kind}")
    value = calibration.get("actuator_per_delta")
    if value is None:
        value = calibration.get("phase_per_delta")
    if value is None:
        return None
    scale = float(value)
    if not np.isfinite(scale) or scale == 0:
        raise ValueError("calibration.actuator_per_delta must be finite and non-zero")
    return scale


def calibration_actuator_for_delta(delta_momentum: float, calibration: dict[str, Any]) -> float:
    kind = str(calibration.get("kind", "linear")).strip().lower()
    delta = float(delta_momentum)
    if not np.isfinite(delta):
        raise ValueError("delta_momentum must be finite")
    if kind in {"linear", "linear_relative"}:
        scale = calibration_actuator_per_delta(calibration)
        if scale is None:
            raise ValueError("Missing calibration.actuator_per_delta")
        baseline = calibration.get("baseline_actuator")
        offset = delta * float(scale)
        return offset if baseline is None else float(baseline) + offset
    if kind in {"polynomial", "polynomial_relative", "quadratic_relative"}:
        baseline = calibration.get("baseline_actuator")
        if baseline is None:
            raise ValueError("polynomial calibration requires baseline_actuator")
        offset = calibration_actuator_offset_for_delta(delta, calibration)
        return float(baseline) + offset
    raise ValueError(f"Unsupported calibration kind: {kind}")


def calibration_delta_for_actuator(actuator_value: float, calibration: dict[str, Any]) -> float:
    kind = str(calibration.get("kind", "linear")).strip().lower()
    actuator = float(actuator_value)
    if not np.isfinite(actuator):
        raise ValueError("actuator_value must be finite")
    if kind in {"linear", "linear_relative"}:
        scale = calibration_actuator_per_delta(calibration)
        if scale is None:
            raise ValueError("Missing calibration.actuator_per_delta")
        baseline = calibration.get("baseline_actuator")
        offset = actuator if baseline is None else actuator - float(baseline)
        return float(offset / scale)
    if kind in {"polynomial", "polynomial_relative", "quadratic_relative"}:
        baseline = calibration.get("baseline_actuator")
        if baseline is None:
            raise ValueError("polynomial calibration requires baseline_actuator")
        coefficients = _calibration_coefficients(calibration)
        offset = actuator - float(baseline)
        return float(np.polyval(np.asarray(coefficients, dtype=float), offset))
    raise ValueError(f"Unsupported calibration kind: {kind}")


def calibration_actuator_offset_for_delta(delta_momentum: float, calibration: dict[str, Any]) -> float:
    coefficients = _calibration_coefficients(calibration)
    valid_min = calibration.get("valid_actuator_min")
    valid_max = calibration.get("valid_actuator_max")
    baseline = calibration.get("baseline_actuator")
    offset_min = offset_max = None
    if baseline is not None and valid_min is not None and valid_max is not None:
        offset_min = float(valid_min) - float(baseline)
        offset_max = float(valid_max) - float(baseline)
    return _solve_polynomial_offset(
        coefficients,
        float(delta_momentum),
        offset_min=offset_min,
        offset_max=offset_max,
    )


def fit_actuator_to_delta(
    actuator_values: list[float],
    delta_values: list[float],
) -> EnergyKnobCalibrationFit:
    actuators = np.asarray(actuator_values, dtype=float)
    deltas = np.asarray(delta_values, dtype=float)
    if actuators.shape != deltas.shape:
        raise ValueError("actuator_values and delta_values must have the same length")
    if actuators.size < 2:
        raise ValueError("At least two calibration samples are required")
    finite = np.isfinite(actuators) & np.isfinite(deltas)
    actuators = actuators[finite]
    deltas = deltas[finite]
    if actuators.size < 2:
        raise ValueError("At least two finite calibration samples are required")

    slope, intercept = np.polyfit(actuators, deltas, deg=1)
    if slope == 0:
        raise ValueError("Calibration slope must not be zero")
    predicted = slope * actuators + intercept
    residual = deltas - predicted
    ss_res = float(np.sum(residual * residual))
    centered = deltas - float(np.mean(deltas))
    ss_tot = float(np.sum(centered * centered))
    r_squared = 1.0 if ss_tot == 0.0 else 1.0 - ss_res / ss_tot
    return EnergyKnobCalibrationFit(
        slope_delta_per_actuator=float(slope),
        intercept_delta=float(intercept),
        actuator_per_delta=float(1.0 / slope),
        r_squared=float(r_squared),
        n_samples=int(actuators.size),
        kind="linear",
        order=1,
        coefficients=(float(slope), float(intercept)),
    )


def fit_quadratic_actuator_to_delta(
    actuator_values: list[float],
    delta_values: list[float],
    *,
    baseline_actuator: float,
) -> EnergyKnobCalibrationFit:
    actuators = np.asarray(actuator_values, dtype=float)
    deltas = np.asarray(delta_values, dtype=float)
    if actuators.shape != deltas.shape:
        raise ValueError("actuator_values and delta_values must have the same length")
    finite = np.isfinite(actuators) & np.isfinite(deltas)
    actuators = actuators[finite]
    deltas = deltas[finite]
    if actuators.size < 5:
        raise ValueError("At least five finite calibration samples are required")
    baseline = float(baseline_actuator)
    if not np.isfinite(baseline):
        raise ValueError("baseline_actuator must be finite")

    offsets = actuators - baseline
    design = np.column_stack((offsets * offsets, offsets))
    a, b = (float(value) for value in np.linalg.lstsq(design, deltas, rcond=None)[0])
    c = 0.0
    coefficients = np.asarray((a, b, c), dtype=float)
    if not np.isfinite(b) or b == 0:
        raise ValueError("Quadratic baseline slope must be finite and non-zero")
    predicted = np.polyval(coefficients, offsets)
    residual = deltas - predicted
    ss_res = float(np.sum(residual * residual))
    centered = deltas - float(np.mean(deltas))
    ss_tot = float(np.sum(centered * centered))
    r_squared = 1.0 if ss_tot == 0.0 else 1.0 - ss_res / ss_tot
    return EnergyKnobCalibrationFit(
        slope_delta_per_actuator=b,
        intercept_delta=c,
        actuator_per_delta=float(1.0 / b),
        r_squared=float(r_squared),
        n_samples=int(actuators.size),
        kind="quadratic",
        order=2,
        baseline_actuator=baseline,
        coefficients=(a, b, c),
    )


def predict_delta_from_fit(
    fit: EnergyKnobCalibrationFit,
    actuator_values: np.ndarray,
) -> np.ndarray:
    actuators = np.asarray(actuator_values, dtype=float)
    if fit.order == 2 and fit.baseline_actuator is not None and fit.coefficients:
        offsets = actuators - float(fit.baseline_actuator)
        return np.polyval(np.asarray(fit.coefficients, dtype=float), offsets)
    return fit.slope_delta_per_actuator * actuators + fit.intercept_delta


def fit_phase_to_delta(
    phase_values: list[float],
    delta_values: list[float],
) -> PhaseCalibrationFit:
    return fit_actuator_to_delta(phase_values, delta_values)


def load_energy_knob_calibration_csv(
    path: str | Path,
    actuator_column: str = "actuator_value",
    delta_column: str = "delta_p_over_p",
) -> EnergyKnobCalibrationFit:
    actuators: list[float] = []
    deltas: list[float] = []
    with Path(path).open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        fieldnames = reader.fieldnames or []
        resolved_actuator_column = actuator_column
        if actuator_column == "actuator_value" and actuator_column not in fieldnames:
            if "phase_deg" in fieldnames:
                resolved_actuator_column = "phase_deg"
        if resolved_actuator_column not in fieldnames:
            raise ValueError(f"Missing actuator column: {actuator_column}")
        if delta_column not in fieldnames:
            raise ValueError(f"Missing delta column: {delta_column}")
        for row in reader:
            actuators.append(float(row[resolved_actuator_column]))
            deltas.append(float(row[delta_column]))
    return fit_actuator_to_delta(actuators, deltas)


def load_phase_calibration_csv(
    path: str | Path,
    phase_column: str = "phase_deg",
    delta_column: str = "delta_p_over_p",
) -> PhaseCalibrationFit:
    return load_energy_knob_calibration_csv(path, phase_column, delta_column)


def actuator_step_for_delta(delta_momentum: float, calibration: dict[str, Any]) -> dict[str, Any]:
    try:
        kind = str(calibration.get("kind", "linear")).strip().lower()
        actuator_per_delta = calibration_actuator_per_delta(calibration)
        if actuator_per_delta is None:
            return {"calibrated": False, "reason": "Missing calibration.actuator_per_delta"}
        if kind in {"polynomial", "polynomial_relative", "quadratic_relative"}:
            plus = calibration_actuator_offset_for_delta(float(delta_momentum), calibration)
            minus = calibration_actuator_offset_for_delta(-float(delta_momentum), calibration)
            return {
                "calibrated": True,
                "kind": kind,
                "actuator_step": max(abs(plus), abs(minus)),
                "plus_offset": plus,
                "minus_offset": minus,
                "actuator_per_delta": actuator_per_delta,
            }
    except (TypeError, ValueError) as exc:
        return {"calibrated": False, "reason": str(exc)}
    step = float(delta_momentum) * float(actuator_per_delta)
    return {
        "calibrated": True,
        "kind": kind,
        "actuator_step": step,
        "plus_offset": step,
        "minus_offset": -step,
        "actuator_per_delta": actuator_per_delta,
    }


def effective_delta_for_integer_actuator_step(
    delta_momentum: float,
    calibration: dict[str, Any],
) -> float:
    """Adjust a linear-calibration delta so its actuator offset is integral."""
    delta = float(delta_momentum)
    kind = str(calibration.get("kind", "linear")).strip().lower()
    if kind not in {"linear", "linear_relative"}:
        return delta
    actuator_per_delta = calibration_actuator_per_delta(calibration)
    if actuator_per_delta is None:
        return delta
    if (
        not np.isfinite(delta)
        or not np.isfinite(actuator_per_delta)
        or actuator_per_delta == 0
    ):
        raise ValueError("delta and calibration actuator scale must be finite and non-zero")
    return float(round(delta * actuator_per_delta) / actuator_per_delta)


def _calibration_coefficients(calibration: dict[str, Any]) -> tuple[float, ...]:
    raw = calibration.get("coefficients")
    if raw is None:
        raw = calibration.get("polynomial_coefficients")
    if raw is None:
        raise ValueError("polynomial calibration requires coefficients")
    coefficients = tuple(float(value) for value in raw)
    if len(coefficients) < 2:
        raise ValueError("polynomial calibration requires at least two coefficients")
    if not all(np.isfinite(value) for value in coefficients):
        raise ValueError("polynomial calibration coefficients must be finite")
    return coefficients


def _solve_polynomial_offset(
    coefficients: tuple[float, ...],
    target_delta: float,
    *,
    offset_min: float | None = None,
    offset_max: float | None = None,
) -> float:
    target = float(target_delta)
    if not np.isfinite(target):
        raise ValueError("target delta must be finite")
    polynomial = np.asarray(coefficients, dtype=float).copy()
    polynomial[-1] -= target
    roots = np.roots(polynomial)
    real_roots = [
        float(root.real)
        for root in roots
        if abs(float(root.imag)) <= 1.0e-9 and np.isfinite(float(root.real))
    ]
    if offset_min is not None and offset_max is not None:
        low = min(float(offset_min), float(offset_max)) - 1.0e-9
        high = max(float(offset_min), float(offset_max)) + 1.0e-9
        real_roots = [root for root in real_roots if low <= root <= high]
    if not real_roots:
        raise ValueError("target delta is outside the calibrated actuator range")
    slope = np.polyval(np.polyder(np.asarray(coefficients, dtype=float)), 0.0)
    if np.isfinite(slope) and slope != 0 and target != 0:
        preferred_sign = np.sign(target / slope)
        preferred = [root for root in real_roots if root * preferred_sign >= -1.0e-9]
        if preferred:
            real_roots = preferred
    return min(real_roots, key=abs)
