from __future__ import annotations

import math
from typing import Any, Mapping


SPEED_OF_LIGHT_M_S = 2.99792458e8
DEFAULT_FIELD_T_PER_A = 0.599792458e-3
DEFAULT_MAGNET_LENGTH_M = 2.7271
DEFAULT_DEFLECT_ANGLE_RAD = 0.4363323129985824


def get_energy0(
    current: float,
    conversion: Mapping[str, Any] | None = None,
    *,
    magnet_length_m: float = DEFAULT_MAGNET_LENGTH_M,
    deflect_angle_rad: float = DEFAULT_DEFLECT_ANGLE_RAD,
    field_t_per_a: float = DEFAULT_FIELD_T_PER_A,
) -> float:
    """
    Convert ESA bend current to the reference beam energy in MeV.

    Parameters
    ----------
    current:
        ESA bend current in amperes.
    conversion:
        Optional config mapping from `apps/energy_spectrum.json`, for example:
        {
          "magnet_length_m": 2.7271,
          "deflect_angle_rad": 0.4363323129985824,
          "field_t_per_a": 0.000599792458
        }
    magnet_length_m / deflect_angle_rad / field_t_per_a:
        Fallback values used when `conversion` is omitted or partial.
    """

    if conversion is not None:
        magnet_length_m = float(conversion.get("magnet_length_m", magnet_length_m))
        deflect_angle_rad = float(conversion.get("deflect_angle_rad", deflect_angle_rad))
        field_t_per_a = float(conversion.get("field_t_per_a", field_t_per_a))

    rho = float(magnet_length_m) / float(deflect_angle_rad)
    b0 = float(field_t_per_a) * float(current)
    energy0_mev = b0 * rho * SPEED_OF_LIGHT_M_S * 1.0e-6
    return float(energy0_mev)


def select_reference_energy_mev(
    default_energy_mev: float,
    *,
    reference_energy_mev: float | None = None,
    bend_current: float | None = None,
    bend_conversion: Mapping[str, Any] | None = None,
) -> tuple[float, str]:
    """Select a reference energy without applying an implicit bend calibration."""

    default_energy = float(default_energy_mev)
    if not math.isfinite(default_energy) or default_energy <= 0:
        raise ValueError("default_energy_mev must be positive and finite.")

    if reference_energy_mev is not None:
        try:
            reference_energy = float(reference_energy_mev)
        except (TypeError, ValueError):
            reference_energy = math.nan
        if math.isfinite(reference_energy) and reference_energy > 0:
            return reference_energy, "reference_pv"

    if bend_current is not None and bend_conversion is not None:
        try:
            converted_energy = get_energy0(bend_current, bend_conversion)
        except (TypeError, ValueError, ZeroDivisionError):
            converted_energy = math.nan
        if math.isfinite(converted_energy) and converted_energy > 0:
            return converted_energy, "bend_current_conversion"

    return default_energy, "workflow_default"
