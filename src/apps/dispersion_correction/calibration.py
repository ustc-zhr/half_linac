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

    def as_dict(self) -> dict[str, float | int]:
        return {
            "slope_delta_per_actuator": self.slope_delta_per_actuator,
            "intercept_delta": self.intercept_delta,
            "actuator_per_delta": self.actuator_per_delta,
            "r_squared": self.r_squared,
            "n_samples": self.n_samples,
        }

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
    )


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
        actuator_per_delta = calibration_actuator_per_delta(calibration)
    except (TypeError, ValueError) as exc:
        return {"calibrated": False, "reason": str(exc)}
    if actuator_per_delta is None:
        return {"calibrated": False, "reason": "Missing calibration.actuator_per_delta"}
    step = float(delta_momentum) * actuator_per_delta
    return {
        "calibrated": True,
        "actuator_step": step,
        "plus_offset": step,
        "minus_offset": -step,
        "actuator_per_delta": actuator_per_delta,
    }
