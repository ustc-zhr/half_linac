from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .models import MachineProfileError


LEGACY_ENERGY_SPECTRUM_STATION_ID = "default"


def resolve_energy_spectrum_stations(
    workflow: Mapping[str, Any],
) -> tuple[str, dict[str, dict[str, Any]]]:
    """Return the default station id and fully merged station configurations."""

    raw_stations = workflow.get("stations")
    if raw_stations is None:
        return LEGACY_ENERGY_SPECTRUM_STATION_ID, {
            LEGACY_ENERGY_SPECTRUM_STATION_ID: dict(workflow)
        }
    if not isinstance(raw_stations, Mapping) or not raw_stations:
        raise MachineProfileError(
            "workflows.energy_spectrum.stations must be a non-empty mapping."
        )

    default_station = str(workflow.get("default_station", "")).strip()
    if not default_station or default_station not in raw_stations:
        raise MachineProfileError(
            "workflows.energy_spectrum.default_station must reference a configured station."
        )

    common = dict(workflow)
    common.pop("stations", None)
    common.pop("default_station", None)
    stations: dict[str, dict[str, Any]] = {}
    for raw_station_id, raw_overrides in raw_stations.items():
        station_id = str(raw_station_id).strip()
        if not station_id:
            raise MachineProfileError(
                "workflows.energy_spectrum station ids must be non-empty strings."
            )
        if not isinstance(raw_overrides, Mapping):
            raise MachineProfileError(
                f"workflows.energy_spectrum.stations.{station_id} must be a mapping."
            )
        effective = dict(common)
        effective.update(raw_overrides)
        effective["station_id"] = station_id
        effective.setdefault("label", station_id.upper())
        stations[station_id] = effective
    return default_station, stations


def resolve_default_energy_spectrum_station(
    workflow: Mapping[str, Any],
) -> dict[str, Any]:
    default_station, stations = resolve_energy_spectrum_stations(workflow)
    return stations[default_station]
