from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from half_linac.src.apps.dispersion_correction.models import (
    BackendConfig,
    DispersionSectionConfig,
    EnergyKnobConfig,
    JointDispersionTargetConfig,
    JointResponseAnalysisConfig,
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
    diagnostic_only = bool(section_raw.get("diagnostic_only", False))
    joint_requested = bool(
        _mapping(
            section_raw.get("joint_response_analysis", {}),
            "section.joint_response_analysis",
        )
    )

    knobs_raw = raw.get("knobs", [])
    if not isinstance(knobs_raw, list):
        raise ValueError("knobs must be a list")
    if not knobs_raw and not diagnostic_only and not joint_requested:
        raise ValueError("Correction sections require at least one knob")

    target_bpms = raw.get("target_bpms", [])
    if not isinstance(target_bpms, list):
        raise ValueError("target_bpms must be a list")
    if not target_bpms and not diagnostic_only and not joint_requested:
        raise ValueError("Correction sections require target_bpms")
    monitor_bpms = raw.get("monitor_bpms", [])
    if not isinstance(monitor_bpms, list):
        raise ValueError("monitor_bpms must be a list")
    if diagnostic_only and not monitor_bpms:
        raise ValueError("Diagnostic-only sections require monitor_bpms")

    measurement = MeasurementConfig(
        plane=str(measurement_raw.get("plane", "x")).lower(),
        samples_per_step=int(measurement_raw.get("samples_per_step", 5)),
        sample_interval_s=float(measurement_raw.get("sample_interval_s", 0.0)),
        final_samples=int(measurement_raw.get("final_samples", 10)),
        settle_time_s=float(measurement_raw.get("settle_time_s", 0.0)),
    )
    if measurement.plane not in {"x", "y", "xy"}:
        raise ValueError("measurement.plane must be 'x', 'y', or 'xy'")
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
                name=f"{bpm} D{measurement.planes[0]}",
                element=bpm,
                component=f"d{measurement.planes[0]}",
                target=target_dispersion_mm[index],
            )
            for index, bpm in enumerate(target_bpms)
        )
    joint_raw = _mapping(
        section_raw.get("joint_response_analysis", {}),
        "section.joint_response_analysis",
    )
    joint_targets_raw = joint_raw.get("targets", [])
    if not isinstance(joint_targets_raw, list):
        raise ValueError("section.joint_response_analysis.targets must be a list")
    joint_knobs_raw = joint_raw.get("knobs", [])
    if not isinstance(joint_knobs_raw, list):
        raise ValueError("section.joint_response_analysis.knobs must be a list")
    joint_analysis = JointResponseAnalysisConfig(
        targets=tuple(
            _parse_joint_target(item, index)
            for index, item in enumerate(joint_targets_raw)
        ),
        knobs=tuple(
            _parse_knob(item, index)
            for index, item in enumerate(joint_knobs_raw)
        ),
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
            readback_tolerance=(
                None
                if energy_raw.get("readback_tolerance") is None
                else float(energy_raw["readback_tolerance"])
            ),
            readback_confirmations=int(energy_raw.get("readback_confirmations", 1)),
            round_actuator_step_to_integer=bool(
                energy_raw.get("round_actuator_step_to_integer", False)
            ),
            wrap_period=(
                None
                if energy_raw.get("wrap_period") is None
                else float(energy_raw["wrap_period"])
            ),
            wrap_origin=float(energy_raw.get("wrap_origin", 0.0)),
        ),
        target_bpms=tuple(str(name) for name in target_bpms),
        monitor_bpms=tuple(str(name) for name in monitor_bpms),
        knobs=knobs,
        section=DispersionSectionConfig(
            id=str(section_raw.get("id", "default")).strip(),
            display_name=str(section_raw.get("display_name", "Default")).strip(),
            model_entrance=_optional_string(section_raw.get("model_entrance")),
            model_exit=_optional_string(section_raw.get("model_exit")),
            target_dispersion_mm=target_dispersion_mm,
            model_observables=model_observables,
            joint_response_analysis=joint_analysis,
            model_only=bool(section_raw.get("model_only", False)),
            diagnostic_only=diagnostic_only,
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
    if not config.energy_knob.name.strip():
        raise ValueError("energy_knob.name must not be empty")
    if not config.energy_knob.actuator.strip():
        raise ValueError("energy_knob.actuator must not be empty")
    if not config.energy_knob.actuator_unit.strip():
        raise ValueError("energy_knob.actuator_unit must not be empty")
    if (
        config.energy_knob.readback_tolerance is not None
        and (
            not math.isfinite(config.energy_knob.readback_tolerance)
            or config.energy_knob.readback_tolerance < 0
        )
    ):
        raise ValueError("energy_knob.readback_tolerance must be finite and non-negative")
    if config.energy_knob.readback_confirmations <= 0:
        raise ValueError("energy_knob.readback_confirmations must be positive")
    if not isinstance(config.energy_knob.round_actuator_step_to_integer, bool):
        raise ValueError("energy_knob.round_actuator_step_to_integer must be boolean")
    if config.energy_knob.wrap_period is not None:
        if (
            not math.isfinite(config.energy_knob.wrap_period)
            or config.energy_knob.wrap_period <= 0
        ):
            raise ValueError("energy_knob.wrap_period must be finite and positive")
        if not math.isfinite(config.energy_knob.wrap_origin):
            raise ValueError("energy_knob.wrap_origin must be finite")
    if len(set(config.target_bpms)) != len(config.target_bpms):
        raise ValueError("target_bpms must not contain duplicates")
    if len(set(config.monitor_bpms)) != len(config.monitor_bpms):
        raise ValueError("monitor_bpms must not contain duplicates")
    overlap = sorted(set(config.target_bpms) & set(config.monitor_bpms))
    if overlap:
        raise ValueError(
            "BPMs cannot be both correction targets and monitors: "
            + ", ".join(overlap)
        )
    if not config.section.id:
        raise ValueError("section.id must not be empty")
    if config.section.diagnostic_only and config.target_bpms:
        raise ValueError("Diagnostic-only sections must not define target_bpms")
    if config.section.diagnostic_only and config.knobs:
        raise ValueError("Diagnostic-only sections must not define correction knobs")
    joint = config.section.joint_response_analysis
    if bool(joint.targets) != bool(joint.knobs):
        raise ValueError(
            "section.joint_response_analysis requires both targets and knobs"
        )
    if joint.enabled and config.measurement.plane != "xy":
        raise ValueError(
            "section.joint_response_analysis requires measurement.plane='xy'"
        )
    joint_names = [target.name for target in joint.targets]
    if len(set(joint_names)) != len(joint_names):
        raise ValueError("Joint response targets must not contain duplicates")
    measurement_bpms = set(config.measurement_bpms)
    for target in joint.targets:
        if not target.bpm:
            raise ValueError("Joint response target BPM must not be empty")
        if target.plane not in {"x", "y"}:
            raise ValueError("Joint response target plane must be 'x' or 'y'")
        if target.bpm not in measurement_bpms:
            raise ValueError(
                f"Joint response target BPM {target.bpm} is not a measurement BPM"
            )
        if not math.isfinite(target.target_mm):
            raise ValueError("Joint response target values must be finite")
        if not math.isfinite(target.tolerance_mm) or target.tolerance_mm <= 0:
            raise ValueError("Joint response target tolerances must be positive")
    if (
        config.measurement.plane == "xy"
        and not config.section.diagnostic_only
        and not joint.enabled
    ):
        raise ValueError(
            "measurement.plane='xy' currently requires a diagnostic-only section"
        )
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
    if not 0 <= config.solver.min_step_improvement < 1:
        raise ValueError("solver.min_step_improvement must be in [0, 1)")
    if config.solver.svd_cut < 0:
        raise ValueError("solver.svd_cut must be non-negative")
    if config.solver.regularization < 0:
        raise ValueError("solver.regularization must be non-negative")


def _parse_knob(raw: Any, index: int) -> KnobConfig:
    item = _mapping(raw, f"knobs[{index}]")
    devices = _mapping(item.get("devices", {}), f"knobs[{index}].devices")
    if not devices:
        raise ValueError(f"knobs[{index}].devices must not be empty")
    raw_scan = item.get("scan")
    if raw_scan is None:
        scan = {
            "step": item.get("scan_step", 0.0),
            "max_offset": item.get("limit", 0.0),
            "mode": "relative",
            "unit": "",
        }
    else:
        scan = _mapping(raw_scan, f"knobs[{index}].scan")
    knob = KnobConfig(
        name=str(item.get("name", f"knob_{index + 1}")),
        devices=as_float_mapping(devices),
        scan_step=float(scan.get("step", 0.0)),
        limit=float(scan.get("max_offset", 0.0)),
        scan_mode=str(scan.get("mode", "relative")).strip().lower(),
        unit=str(scan.get("unit", "")).strip(),
    )
    if knob.scan_step <= 0 or knob.limit <= 0:
        raise ValueError(f"{knob.name}: scan.step and scan.max_offset must be positive")
    if knob.scan_step > knob.limit:
        raise ValueError(f"{knob.name}: scan.step must not exceed scan.max_offset")
    if knob.scan_mode != "relative":
        raise ValueError(f"{knob.name}: scan.mode must be 'relative'")
    return knob


def _parse_joint_target(raw: Any, index: int) -> JointDispersionTargetConfig:
    item = _mapping(
        raw,
        f"section.joint_response_analysis.targets[{index}]",
    )
    return JointDispersionTargetConfig(
        bpm=str(item.get("bpm", "")).strip(),
        plane=str(item.get("plane", "")).strip().lower(),
        target_mm=float(item.get("target_mm", 0.0)),
        tolerance_mm=float(item.get("tolerance_mm", 1.0)),
    )


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
