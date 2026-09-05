from __future__ import annotations

import numpy as np

from half_linac.src.apps.dispersion_correction.models import BPMReading, SafetyConfig, SafetyStatus


def evaluate_safety(
    config: SafetyConfig,
    reference: BPMReading | None,
    current: BPMReading | None,
    *,
    plane: str = "x",
) -> SafetyStatus:
    normalized_plane = str(plane).strip().lower()
    if normalized_plane not in {"x", "y", "xy"}:
        raise ValueError("Safety orbit plane must be 'x', 'y', or 'xy'")
    if reference is not None and current is not None:
        if reference.names != current.names:
            return SafetyStatus(
                ok=False,
                reason="BPM order changed during safety orbit check",
            )
        common_valid = reference.valid & current.valid
        if np.any(common_valid):
            common_indices = np.flatnonzero(common_valid)
            checked_planes = (
                ("x", "y") if normalized_plane == "xy" else (normalized_plane,)
            )
            maximum: tuple[float, int, str] | None = None
            for checked_plane in checked_planes:
                reference_values = getattr(reference, f"{checked_plane}_mm")
                current_values = getattr(current, f"{checked_plane}_mm")
                orbit_change = np.abs(
                    current_values[common_indices]
                    - reference_values[common_indices]
                )
                max_local_index = int(np.argmax(orbit_change))
                candidate = (
                    float(orbit_change[max_local_index]),
                    int(common_indices[max_local_index]),
                    checked_plane,
                )
                if maximum is None or candidate[0] > maximum[0]:
                    maximum = candidate
            assert maximum is not None
            max_orbit_change, max_index, max_plane = maximum
            if max_orbit_change > config.max_reference_orbit_change_mm:
                return SafetyStatus(
                    ok=False,
                    reason=(
                        f"Reference orbit change {max_orbit_change:.3f} mm at "
                        f"{reference.names[max_index]} ({max_plane}) exceeded "
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
