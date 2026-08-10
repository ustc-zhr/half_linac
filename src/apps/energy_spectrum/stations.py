from __future__ import annotations

from half_linac.src.shared.machine_profile.energy_spectrum import (
    LEGACY_ENERGY_SPECTRUM_STATION_ID,
    resolve_energy_spectrum_stations,
)


LEGACY_STATION_ID = LEGACY_ENERGY_SPECTRUM_STATION_ID


__all__ = ["LEGACY_STATION_ID", "resolve_energy_spectrum_stations"]
