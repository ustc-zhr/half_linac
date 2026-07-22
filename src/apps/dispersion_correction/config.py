from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from half_linac.src.apps.dispersion_correction.models import (
    BackendConfig,
    DispersionSectionConfig,
    EnergyKnobConfig,
    KnobConfig,
    MeasurementConfig,
    ModelObservableConfig,
    RunConfig,
    SafetyConfig,
    SolverConfig,
    as_float_mapping,
)


def load_config(path: str | Path) -> RunConfig:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as stream:
        if config_path.suffix.lower() == ".json":
            raw = json.load(stream)
        elif config_path.suffix.lower() in {".yaml", ".yml"}:
            try:
                import yaml
            except ImportError as exc:
                raise RuntimeError("YAML compatibility requires the optional PyYAML dependency") from exc
            raw = yaml.safe_load(stream) or {}
        else:
            raise ValueError("Configuration file must use .json, .yaml, or .yml")
    if not isinstance(raw, dict):
        raise ValueError("Configuration root must be a mapping")
    return parse_config(raw)


def parse_config(raw: dict[str, Any]) -> RunConfig:
    backend_raw = _mapping(raw.get("backend", {}), "backend")
    energy_raw = _mapping(raw.get("energy_knob", {}), "energy_knob")
    measurement_raw = _mapping(raw.get("measurement", {}), "measurement")
    solver_raw = _mapping(raw.get("solver", {}), "solver")
    safety_raw = _mapping(raw.get("safety", {}), "safety")
    section_raw = _mapping(raw.get("section", {}), "section")

    knobs_raw = raw.get("knobs", [])
    if not isinstance(knobs_raw, list) or not knobs_raw:
        raise ValueError("Configuration requires at least one knob")

    target_bpms = raw.get("target_bpms", [])
    if not isinstance(target_bpms, list) or not target_bpms:
        raise ValueError("Configuration requires target_bpms")

    measurement = MeasurementConfig(
        plane=str(measurement_raw.get("plane", "x")).lower(),
        samples_per_step=int(measurement_raw.get("samples_per_step", 5)),
        sample_interval_s=float(measurement_raw.get("sample_interval_s", 0.0)),
        final_samples=int(measurement_raw.get("final_samples", 10)),
        settle_time_s=float(measurement_raw.get("settle_time_s", 0.0)),
    )
    if measurement.plane != "x":
        raise ValueError("MVP supports horizontal plane only: measurement.plane must be 'x'")
    if measurement.samples_per_step <= 0 or measurement.final_samples <= 0:
        raise ValueError("Measurement sample counts must be positive")
    if measurement.sample_interval_s < 0:
        raise ValueError("measurement.sample_interval_s must be non-negative")
    if measurement.settle_time_s < 0:
        raise ValueError("measurement.settle_time_s must be non-negative")

    knobs = tuple(_parse_knob(item, index) for index, item in enumerate(knobs_raw))
    target_raw = section_raw.get(
        "target_dispersion_mm",
        raw.get("target_dispersion_mm", [0.0] * len(target_bpms)),
    )
    if not isinstance(target_raw, (list, tuple)):
        raise ValueError("section.target_dispersion_mm must be a list")
    target_dispersion_mm = tuple(float(value) for value in target_raw)
    observables_raw = section_raw.get("model_observables", [])
    if not isinstance(observables_raw, list):
        raise ValueError("section.model_observables must be a list")
    model_observables = tuple(
        _parse_model_observable(item, index)
        for index, item in enumerate(observables_raw)
    )
    if not model_observables and section_raw.get("model_entrance"):
        model_observables = tuple(
            ModelObservableConfig(
                name=f"{bpm} Dx",
                element=bpm,
                component="dx",
                target=target_dispersion_mm[index],
            )
            for index, bpm in enumerate(target_bpms)
        )

    config = RunConfig(
        backend=BackendConfig(
            type=str(backend_raw.get("type", "offline")),
            mode=str(backend_raw.get("mode", "read_only")),
            options=dict(backend_raw.get("options", {}) or {}),
        ),
        energy_knob=EnergyKnobConfig(
            name=str(energy_raw.get("name", "ENERGY_DELTA")),
            delta=float(energy_raw.get("delta", 1.0e-4)),
            actuator=str(energy_raw.get("actuator", "delta")),
            actuator_unit=str(energy_raw.get("actuator_unit", "delta_p_over_p")),
            calibration=dict(energy_raw.get("calibration", {}) or {}),
        ),
        target_bpms=tuple(str(name) for name in target_bpms),
        knobs=knobs,
        section=DispersionSectionConfig(
            id=str(section_raw.get("id", "default")).strip(),
            display_name=str(section_raw.get("display_name", "Default")).strip(),
            model_entrance=_optional_string(section_raw.get("model_entrance")),
            model_exit=_optional_string(section_raw.get("model_exit")),
            target_dispersion_mm=target_dispersion_mm,
            model_observables=model_observables,
            model_only=bool(section_raw.get("model_only", False)),
        ),
        measurement=measurement,
        solver=SolverConfig(
            svd_cut=float(solver_raw.get("svd_cut", 1.0e-3)),
            regularization=float(solver_raw.get("regularization", 1.0e-3)),
            gain=float(solver_raw.get("gain", 0.5)),
            max_step_fraction=float(solver_raw.get("max_step_fraction", 0.25)),
            max_iter=int(solver_raw.get("max_iter", 5)),
            response_update=str(solver_raw.get("response_update", "once")).lower(),
            min_step_improvement=float(solver_raw.get("min_step_improvement", 0.05)),
            success_min_improvement=float(solver_raw.get("success_min_improvement", 2.0)),
        ),
        safety=SafetyConfig(
            max_reference_orbit_change_mm=float(safety_raw.get("max_reference_orbit_change_mm", 1.0)),
        ),
    )
    validate_config(config)
    return config


def validate_config(config: RunConfig) -> None:
    if config.energy_knob.delta <= 0:
        raise ValueError("energy_knob.delta must be positive")
    if len(set(config.target_bpms)) != len(config.target_bpms):
        raise ValueError("target_bpms must not contain duplicates")
    if not config.section.id:
        raise ValueError("section.id must not be empty")
    if len(config.section.target_dispersion_mm) != len(config.target_bpms):
        raise ValueError("section.target_dispersion_mm length must match target_bpms")
    if not all(math.isfinite(value) for value in config.section.target_dispersion_mm):
        raise ValueError("section.target_dispersion_mm values must be finite")
    if config.section.model_only and (
        config.section.model_entrance is None or config.section.model_exit is None
    ):
        raise ValueError("model-only sections require model_entrance and model_exit")
    if config.section.model_only and not config.section.model_observables:
        raise ValueError("model-only sections require model_observables")
    for observable in config.section.model_observables:
        if not observable.name or not observable.element:
            raise ValueError("model observable name and element must not be empty")
        if observable.component not in {"dx", "dxp", "dy", "dyp"}:
            raise ValueError(
                "model observable component must be one of dx, dxp, dy, or dyp"
            )
        if not math.isfinite(observable.target):
            raise ValueError("model observable target must be finite")
    if config.solver.max_iter <= 0:
        raise ValueError("solver.max_iter must be positive")
    if config.solver.response_update not in {"once", "every_iteration"}:
        raise ValueError("solver.response_update must be 'once' or 'every_iteration'")
    if not 0 < config.solver.gain <= 1:
        raise ValueError("solver.gain must be in (0, 1]")
    if not 0 < config.solver.max_step_fraction <= 1:
        raise ValueError("solver.max_step_fraction must be in (0, 1]")
    if config.solver.svd_cut < 0:
        raise ValueError("solver.svd_cut must be non-negative")
    if config.solver.regularization < 0:
        raise ValueError("solver.regularization must be non-negative")


def _parse_knob(raw: Any, index: int) -> KnobConfig:
    item = _mapping(raw, f"knobs[{index}]")
    devices = _mapping(item.get("devices", {}), f"knobs[{index}].devices")
    if not devices:
        raise ValueError(f"knobs[{index}].devices must not be empty")
    knob = KnobConfig(
        name=str(item.get("name", f"knob_{index + 1}")),
        devices=as_float_mapping(devices),
        scan_step=float(item.get("scan_step", 0.0)),
        limit=float(item.get("limit", 0.0)),
    )
    if knob.scan_step <= 0 or knob.limit <= 0:
        raise ValueError(f"{knob.name}: scan_step and limit must be positive")
    if knob.scan_step > knob.limit:
        raise ValueError(f"{knob.name}: scan_step must not exceed limit")
    return knob


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping")
    return value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_model_observable(raw: Any, index: int) -> ModelObservableConfig:
    item = _mapping(raw, f"section.model_observables[{index}]")
    element = str(item.get("element", "")).strip()
    component = str(item.get("component", "")).strip().lower()
    name = str(item.get("name", f"{element} {component}")).strip()
    return ModelObservableConfig(
        name=name,
        element=element,
        component=component,
        target=float(item.get("target", 0.0)),
    )
