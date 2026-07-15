from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True)
class PhaseCalibrationFit:
    slope_delta_per_phase: float
    intercept_delta: float
    phase_per_delta: float
    r_squared: float
    n_samples: int

    def as_dict(self) -> dict[str, float | int]:
        return {
            "slope_delta_per_phase": self.slope_delta_per_phase,
            "intercept_delta": self.intercept_delta,
            "phase_per_delta": self.phase_per_delta,
            "r_squared": self.r_squared,
            "n_samples": self.n_samples,
        }


def fit_phase_to_delta(phase_values: list[float], delta_values: list[float]) -> PhaseCalibrationFit:
    phases = np.asarray(phase_values, dtype=float)
    deltas = np.asarray(delta_values, dtype=float)
    if phases.shape != deltas.shape:
        raise ValueError("phase_values and delta_values must have the same length")
    if phases.size < 2:
        raise ValueError("At least two calibration samples are required")
    finite = np.isfinite(phases) & np.isfinite(deltas)
    phases = phases[finite]
    deltas = deltas[finite]
    if phases.size < 2:
        raise ValueError("At least two finite calibration samples are required")

    slope, intercept = np.polyfit(phases, deltas, deg=1)
    if slope == 0:
        raise ValueError("Calibration slope must not be zero")
    predicted = slope * phases + intercept
    residual = deltas - predicted
    ss_res = float(np.sum(residual * residual))
    centered = deltas - float(np.mean(deltas))
    ss_tot = float(np.sum(centered * centered))
    r_squared = 1.0 if ss_tot == 0.0 else 1.0 - ss_res / ss_tot
    return PhaseCalibrationFit(
        slope_delta_per_phase=float(slope),
        intercept_delta=float(intercept),
        phase_per_delta=float(1.0 / slope),
        r_squared=float(r_squared),
        n_samples=int(phases.size),
    )


def load_phase_calibration_csv(
    path: str | Path,
    phase_column: str = "phase_deg",
    delta_column: str = "delta_p_over_p",
) -> PhaseCalibrationFit:
    phases: list[float] = []
    deltas: list[float] = []
    with Path(path).open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if phase_column not in (reader.fieldnames or []):
            raise ValueError(f"Missing phase column: {phase_column}")
        if delta_column not in (reader.fieldnames or []):
            raise ValueError(f"Missing delta column: {delta_column}")
        for row in reader:
            phases.append(float(row[phase_column]))
            deltas.append(float(row[delta_column]))
    return fit_phase_to_delta(phases, deltas)


def actuator_step_for_delta(delta_momentum: float, calibration: dict[str, Any]) -> dict[str, Any]:
    kind = calibration.get("kind", "linear")
    if kind != "linear":
        return {"calibrated": False, "reason": f"Unsupported calibration kind: {kind}"}
    phase_per_delta = calibration.get("phase_per_delta")
    if phase_per_delta is None:
        return {"calibrated": False, "reason": "Missing calibration.phase_per_delta"}
    step = float(delta_momentum) * float(phase_per_delta)
    return {
        "calibrated": True,
        "actuator_step": step,
        "plus_offset": step,
        "minus_offset": -step,
        "phase_per_delta": float(phase_per_delta),
    }
