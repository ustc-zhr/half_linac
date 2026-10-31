from __future__ import annotations

import math

import numpy as np

# These compatibility implementations are shared by the RF phase and linked
# timing scans.  The neutral names keep new callers independent of RF-phase UI
# modules while the established RF names remain import-compatible.
from half_linac.src.apps.rf_phase_scan.energy_match_tuner import (
    RFPhaseEnergyMatcher,
    center_out_values,
    reference_x_pixel,
)
from half_linac.src.apps.rf_phase_scan.image_acquisition import RFImageAcquisition


ScreenEnergyMatcher = RFPhaseEnergyMatcher
ScreenImageAcquisition = RFImageAcquisition


def frame_energies_mev(
    energy_setpoint: float,
    frame_centers_mm,
    x_reference_mm: float,
    design_eta_m: float,
) -> list[float]:
    if not frame_centers_mm:
        return []
    setpoint = float(energy_setpoint)
    reference = float(x_reference_mm)
    eta = float(design_eta_m)
    if not math.isfinite(eta) or eta == 0:
        raise ValueError("design_eta_m must be finite and nonzero.")
    return [
        float(setpoint * (1.0 + (float(center) - reference) * 1e-3 / eta))
        for center in frame_centers_mm
    ]


def aggregate_frame_energies(values, enabled=None, *, min_frames=2):
    """Return arithmetic-mean energy statistics for selected finite frames."""
    energies = list(values or ())
    selected = [True] * len(energies) if enabled is None else list(enabled)
    if len(selected) != len(energies):
        raise ValueError("Frame selection length must match frame energy count.")
    used_values = []
    for value, use in zip(energies, selected):
        if not use:
            continue
        try:
            energy = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(energy):
            used_values.append(energy)
    used = np.asarray(used_values, dtype=float)
    if len(used) < int(min_frames):
        return None
    standard_deviation = (
        float(np.std(used, ddof=1)) if len(used) > 1 else 0.0
    )
    return {
        "mean_mev": float(np.mean(used)),
        "standard_deviation_mev": standard_deviation,
        "standard_error_mev": standard_deviation / math.sqrt(len(used)),
        "frames_used": int(len(used)),
    }


__all__ = [
    "ScreenEnergyMatcher",
    "ScreenImageAcquisition",
    "aggregate_frame_energies",
    "center_out_values",
    "frame_energies_mev",
    "reference_x_pixel",
]
