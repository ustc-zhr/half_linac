from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .models import MachineProfileError
from half_linac.src.shared.energy_tuning import (
    EnergyTuningPipelineError,
    legacy_objective_for_pipeline,
    normalize_pipeline,
)


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


def resolve_energy_spectrum_auto_tune(
    workflow: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve nested auto-tune settings with legacy flat-key compatibility."""
    defaults = workflow.get("auto_tune_defaults", {})
    if not isinstance(defaults, Mapping):
        raise MachineProfileError(
            "workflows.energy_spectrum.auto_tune_defaults must be a mapping."
        )
    station = workflow.get("auto_tune", {})
    if not isinstance(station, Mapping):
        raise MachineProfileError(
            "workflows.energy_spectrum.auto_tune must be a mapping."
        )

    resolved: dict[str, Any] = {}
    pipeline = station.get("pipeline", defaults.get("pipeline"))
    if pipeline is not None:
        try:
            normalized_pipeline = normalize_pipeline(pipeline)
            resolved["pipeline"] = list(normalized_pipeline)
            resolved["objective"] = legacy_objective_for_pipeline(normalized_pipeline)
        except EnergyTuningPipelineError as exc:
            raise MachineProfileError(
                f"workflows.energy_spectrum.auto_tune.pipeline is invalid: {exc}"
            ) from exc
    else:
        resolved["objective"] = station.get(
            "objective",
            defaults.get("objective", workflow.get("auto_tune_objective", "find_beam")),
        )
    configured_stages = station.get("stages", defaults.get("stages", {}))
    if configured_stages is None:
        configured_stages = {}
    if not isinstance(configured_stages, Mapping):
        raise MachineProfileError(
            "workflows.energy_spectrum.auto_tune.stages must be a mapping."
        )
    stage_sources = dict(configured_stages)
    for stage_name in ("brightness_peak", "center_lock"):
        if stage_name not in stage_sources:
            legacy = station.get(stage_name, defaults.get(stage_name))
            if legacy is not None:
                stage_sources[stage_name] = legacy
    legacy_center_lock = workflow.get("auto_tune_center_lock")
    if "center_lock" not in stage_sources and legacy_center_lock is not None:
        stage_sources["center_lock"] = legacy_center_lock
    resolved_stages = {}
    for stage_name in ("brightness_peak", "center_lock"):
        stage_config = stage_sources.get(stage_name)
        if stage_config is not None:
            if not isinstance(stage_config, Mapping):
                raise MachineProfileError(
                    f"workflows.energy_spectrum.auto_tune.{stage_name} must be a mapping."
                )
            resolved_stages[stage_name] = dict(stage_config)
    if resolved_stages:
        resolved["stages"] = resolved_stages
        resolved.update(resolved_stages)
    measurement = station.get("measurement", defaults.get("measurement"))
    if measurement is not None:
        if not isinstance(measurement, Mapping):
            raise MachineProfileError(
                "workflows.energy_spectrum.auto_tune.measurement must be a mapping."
            )
        resolved["measurement"] = dict(measurement)
    actuator = station.get("actuator", workflow.get("auto_tune_actuator"))
    if actuator is not None:
        resolved["actuator"] = actuator
    scan = station.get(
        "energy_search",
        station.get(
            "scan",
            workflow.get("energy_search", workflow.get("auto_tune_scan", workflow.get("bend_scan"))),
        ),
    )
    if scan is not None:
        resolved["scan"] = scan
    return resolved
