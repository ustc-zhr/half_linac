from __future__ import annotations

import numpy as np

from half_linac.src.apps.dispersion_correction.models import BPMReading, SafetyConfig, SafetyStatus


def evaluate_safety(
    config: SafetyConfig,
    reference: BPMReading | None,
    current: BPMReading | None,
) -> SafetyStatus:
    if reference is not None and current is not None:
        if reference.names != current.names:
            return SafetyStatus(
                ok=False,
                reason="BPM order changed during safety orbit check",
            )
        common_valid = reference.valid & current.valid
        if np.any(common_valid):
            common_indices = np.flatnonzero(common_valid)
            orbit_change = np.abs(
                current.x_mm[common_indices] - reference.x_mm[common_indices]
            )
            max_local_index = int(np.argmax(orbit_change))
            max_index = int(common_indices[max_local_index])
            max_orbit_change = float(orbit_change[max_local_index])
            if max_orbit_change > config.max_reference_orbit_change_mm:
                return SafetyStatus(
                    ok=False,
                    reason=(
                        f"Reference orbit change {max_orbit_change:.3f} mm at "
                        f"{reference.names[max_index]} exceeded "
                        f"{config.max_reference_orbit_change_mm:.3f} mm limit"
                    ),
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
