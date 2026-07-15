from __future__ import annotations

import numpy as np

from half_linac.src.apps.dispersion_correction.models import BPMReading, SafetyConfig, SafetyStatus


def evaluate_safety(
    config: SafetyConfig,
    reference: BPMReading | None,
    current: BPMReading | None,
) -> SafetyStatus:
    if reference is not None and current is not None:
        common_valid = reference.valid & current.valid
        if np.any(common_valid):
            orbit_change = np.abs(current.x_mm[common_valid] - reference.x_mm[common_valid])
            max_orbit_change = float(np.max(orbit_change))
            if max_orbit_change > config.max_reference_orbit_change_mm:
                return SafetyStatus(
                    ok=False,
                    reason="Reference orbit change exceeded limit",
                    max_orbit_change_mm=max_orbit_change,
                )
        else:
            return SafetyStatus(ok=False, reason="No valid BPMs for safety orbit check")
    else:
        max_orbit_change = None

    return SafetyStatus(
        ok=True,
        reason="OK",
        max_orbit_change_mm=max_orbit_change,
    )
