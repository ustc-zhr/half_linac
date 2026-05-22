from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

import half_linac.runtime_config as st

from .models import (
    AppContext,
    BBAAnalysisConfig,
    BBAFamilyConfig,
    BBAPreset,
    BBAScanConfig,
    BBAWorkflowConfig,
    ControlBackendConfig,
    EmitAnalysisConfig,
    EmitMeasureWorkflowConfig,
    EmitPreset,
    EmitScanConfig,
    MachineProfile,
    MachineProfileError,
    ModelBackendConfig,
    OrbitWorkflowConfig,
    normalize_mode,
    normalize_plane,
)


SUPPORTED_APP_NAMES = {"orbit_correct", "bba", "emit_measure"}


def load_profile(machine_id: str | None = None) -> MachineProfile:
    profile_id = resolve_machine_id(machine_id)
    profile_path = repo_root() / "configs" / "machines" / profile_id / "profile.json"
    if not profile_path.is_file():
        raise MachineProfileError(f"Machine profile not found: {profile_path}")

    with profile_path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    return MachineProfile.from_dict(raw)


def load_app_context(
    app_name: str,
    machine_id: str | None = None,
    control_backend: str | None = None,
    model_backend: str | None = None,
    preset_id: str | None = None,
) -> AppContext:
    if app_name not in SUPPORTED_APP_NAMES:
        supported = ", ".join(sorted(SUPPORTED_APP_NAMES))
        raise MachineProfileError(
            f"Unsupported app_name {app_name!r}. Expected one of: {supported}."
        )

    profile = load_profile(machine_id)
    selected_control_backend = ControlBackendConfig(
        name=normalize_mode(
            control_backend or profile.machine.default_mode,
            "control_backend",
        )
    )

    selected_model_backend = _resolve_model_backend(app_name, model_backend)

    orbit_workflow = load_orbit_workflow(profile)
    bba_workflow = load_bba_workflow(profile)
    emit_measure_workflow = load_emit_measure_workflow(profile)

    if app_name == "orbit_correct":
        selected_model_backend = None

    return AppContext(
        app_name=app_name,
        profile=profile,
        control_backend=selected_control_backend,
        model_backend=selected_model_backend,
        orbit_workflow=orbit_workflow,
        bba_workflow=bba_workflow,
        emit_measure_workflow=emit_measure_workflow,
        selected_preset_id=preset_id,
    )


def load_orbit_workflow(profile: MachineProfile) -> OrbitWorkflowConfig:
    workflow = _expect_mapping(profile.workflows.get("orbit"), "workflows.orbit")
    return OrbitWorkflowConfig(
        bpms=tuple(_expect_string_list(workflow.get("bpms"), "workflows.orbit.bpms")),
        xcors=tuple(_expect_string_list(workflow.get("xcors"), "workflows.orbit.xcors")),
        ycors=tuple(_expect_string_list(workflow.get("ycors"), "workflows.orbit.ycors")),
    )


def load_bba_workflow(profile: MachineProfile) -> BBAWorkflowConfig:
    workflow = _expect_mapping(profile.workflows.get("bba"), "workflows.bba")
    presets_raw = _expect_list(workflow.get("presets"), "workflows.bba.presets")

    presets: list[BBAPreset] = []
    presets_by_id: dict[str, BBAPreset] = {}
    for index, raw_preset in enumerate(presets_raw):
        location = f"workflows.bba.presets[{index}]"
        preset = _parse_bba_preset(raw_preset, location)
        presets.append(preset)
        presets_by_id[preset.id] = preset

    standard = _parse_bba_family(
        workflow.get("standard"),
        "standard",
        "workflows.bba.standard",
    )
    bba2 = _parse_bba_family(
        workflow.get("bba2"),
        "bba2",
        "workflows.bba.bba2",
    )
    return BBAWorkflowConfig(
        presets=tuple(presets),
        presets_by_id=presets_by_id,
        standard=standard,
        bba2=bba2,
    )


def load_emit_measure_workflow(profile: MachineProfile) -> EmitMeasureWorkflowConfig:
    workflow = _expect_mapping(
        profile.workflows.get("emit_measure"),
        "workflows.emit_measure",
    )
    presets_raw = _expect_list(workflow.get("presets"), "workflows.emit_measure.presets")

    presets: list[EmitPreset] = []
    presets_by_id: dict[str, EmitPreset] = {}
    for index, raw_preset in enumerate(presets_raw):
        location = f"workflows.emit_measure.presets[{index}]"
        preset = _parse_emit_preset(raw_preset, location)
        presets.append(preset)
        presets_by_id[preset.id] = preset

    return EmitMeasureWorkflowConfig(
        presets=tuple(presets),
        presets_by_id=presets_by_id,
        twiss_quads=tuple(
            _expect_string_list(
                workflow.get("twiss_quads"),
                "workflows.emit_measure.twiss_quads",
            )
        ),
        default_preset=_expect_non_empty_string(
            workflow.get("default_preset"),
            "workflows.emit_measure.default_preset",
        ),
    )


def repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "repo_bootstrap.py").is_file():
            return parent
    raise MachineProfileError("Could not locate repo root from machine_profile package.")


def resolve_machine_id(machine_id: str | None) -> str:
    raw_machine_id = machine_id
    if raw_machine_id is None:
        raw_machine_id = os.environ.get("HALF_MACHINE_ID", "")

    profile_id = str(raw_machine_id).strip() or "half"
    if Path(profile_id).name != profile_id or profile_id in {".", ".."}:
        raise MachineProfileError(
            f"Invalid machine_id {profile_id!r}. Expected a simple profile directory name."
        )
    return profile_id


def _resolve_model_backend(
    app_name: str,
    model_backend: str | None,
) -> ModelBackendConfig | None:
    if app_name not in {"bba", "emit_measure"}:
        return None

    backend_name = str(model_backend or "simulation").strip().lower().replace("_", " ")
    if backend_name in {"simulation", "elegant"}:
        return ModelBackendConfig(
            name="simulation",
            engine="elegant",
            config=_default_elegant_model_config(),
        )

    return ModelBackendConfig(name=backend_name)


def _default_elegant_model_config() -> Mapping[str, Any]:
    root = Path(st.rootpath)
    elegant_dir = root / "src" / "virtual_machine" / "half_elegant" / "elegant"
    vm_dir = root / "src" / "virtual_machine" / "half_elegant"
    return {
        "source_json": str(vm_dir / "halflinac.json"),
        "source_lattice": str(elegant_dir / "lattice_ini.lte"),
        "emit_ini_ele": str(elegant_dir / "emit_ini.ele"),
        "emit_lte": str(elegant_dir / "emit.lte"),
        "emit_ele": str(elegant_dir / "emit.ele"),
        "emit_json": str(vm_dir / "emit.json"),
        "emit_mat": str(elegant_dir / "emit.mat"),
        "emit_log": "emit.log",
        "line_name": "ALL",
    }


def _parse_bba_preset(raw_preset: Any, location: str) -> BBAPreset:
    preset = _expect_mapping(raw_preset, location)
    return BBAPreset(
        id=_expect_non_empty_string(preset.get("id"), f"{location}.id"),
        family=_expect_non_empty_string(preset.get("family"), f"{location}.family"),
        plane=normalize_plane(preset.get("plane"), f"{location}.plane"),
        quad=_expect_non_empty_string(preset.get("quad"), f"{location}.quad"),
        corr=_expect_non_empty_string(preset.get("corr"), f"{location}.corr"),
        bpm1=_expect_non_empty_string(preset.get("bpm1"), f"{location}.bpm1"),
        bpm2=_expect_non_empty_string(preset.get("bpm2"), f"{location}.bpm2"),
        mode=normalize_mode(preset.get("mode"), f"{location}.mode") if "mode" in preset else None,
        scan=_parse_bba_scan_config(_expect_mapping(preset.get("scan", {}), f"{location}.scan")),
        analysis=_parse_bba_analysis_config(
            _expect_mapping(preset.get("analysis", {}), f"{location}.analysis"),
        ),
    )


def _parse_bba_family(raw_family: Any, name: str, location: str) -> BBAFamilyConfig:
    family = _expect_mapping(raw_family, location)
    control_backends: tuple[str, ...] = ()
    if "control_backends" in family:
        control_backends = tuple(
            normalize_mode(backend, f"{location}.control_backends[{index}]")
            for index, backend in enumerate(
                _expect_string_list(family.get("control_backends"), f"{location}.control_backends")
            )
        )
    elif "modes" in family:
        control_backends = tuple(
            normalize_mode(mode, f"{location}.modes[{index}]")
            for index, mode in enumerate(_expect_string_list(family.get("modes"), f"{location}.modes"))
        )

    return BBAFamilyConfig(
        name=name,
        correctors=tuple(_expect_string_list(family.get("correctors"), f"{location}.correctors")),
        quads=tuple(_expect_string_list(family.get("quads"), f"{location}.quads")),
        bpm1=tuple(_expect_string_list(family.get("bpm1"), f"{location}.bpm1")),
        bpm2=tuple(_expect_string_list(family.get("bpm2"), f"{location}.bpm2")),
        default_preset=_expect_non_empty_string(family.get("default_preset"), f"{location}.default_preset"),
        control_backends=control_backends,
    )


def _parse_emit_preset(raw_preset: Any, location: str) -> EmitPreset:
    preset = _expect_mapping(raw_preset, location)
    scan = _parse_emit_scan_config(_expect_mapping(preset.get("scan", {}), f"{location}.scan"))
    analysis_dict = dict(_expect_mapping(preset.get("analysis", {}), f"{location}.analysis"))
    energy_mev = preset.get("energy_mev")
    if energy_mev is not None:
        analysis_dict["energy_mev"] = energy_mev

    return EmitPreset(
        id=_expect_non_empty_string(preset.get("id"), f"{location}.id"),
        quad=_expect_non_empty_string(preset.get("quad"), f"{location}.quad"),
        flag=_expect_non_empty_string(preset.get("flag"), f"{location}.flag"),
        scan=scan,
        analysis=_parse_emit_analysis_config(analysis_dict),
    )


def _parse_bba_scan_config(raw_scan: Mapping[str, Any]) -> BBAScanConfig:
    return BBAScanConfig(
        corr_from=_optional_float(raw_scan, "corr_from"),
        corr_end=_optional_float(raw_scan, "corr_end"),
        corr_steps=_optional_int(raw_scan, "corr_steps"),
        quad_from=_optional_float(raw_scan, "quad_from"),
        quad_end=_optional_float(raw_scan, "quad_end"),
        quad_steps=_optional_int(raw_scan, "quad_steps"),
        samples=_optional_int(raw_scan, "samples"),
        sleeptime=_optional_float(raw_scan, "sleeptime"),
    )


def _parse_bba_analysis_config(raw_analysis: Mapping[str, Any]) -> BBAAnalysisConfig:
    return BBAAnalysisConfig(
        energy_mev=_optional_float(raw_analysis, "energy_mev"),
        bpm1_samples=_optional_int(raw_analysis, "bpm1_samples"),
        by_formula=_optional_string(raw_analysis, "by_formula"),
        bx_formula=_optional_string(raw_analysis, "bx_formula"),
        leff_by=_optional_float(raw_analysis, "leff_by"),
        leff_bx=_optional_float(raw_analysis, "leff_bx"),
    )


def _parse_emit_scan_config(raw_scan: Mapping[str, Any]) -> EmitScanConfig:
    return EmitScanConfig(
        k1_from=_optional_float(raw_scan, "k1_from"),
        k1_end=_optional_float(raw_scan, "k1_end"),
        k1_steps=_optional_int(raw_scan, "k1_steps"),
        samples=_optional_int(raw_scan, "samples"),
        sleeptime=_optional_float(raw_scan, "sleeptime"),
    )


def _parse_emit_analysis_config(raw_analysis: Mapping[str, Any]) -> EmitAnalysisConfig:
    return EmitAnalysisConfig(
        energy_mev=_optional_float(raw_analysis, "energy_mev"),
    )


def _optional_float(raw_mapping: Mapping[str, Any], key: str) -> float | None:
    value = raw_mapping.get(key)
    return float(value) if value is not None else None


def _optional_int(raw_mapping: Mapping[str, Any], key: str) -> int | None:
    value = raw_mapping.get(key)
    return int(value) if value is not None else None


def _optional_string(raw_mapping: Mapping[str, Any], key: str) -> str | None:
    value = raw_mapping.get(key)
    if value is None:
        return None
    return _expect_non_empty_string(value, key)


def _expect_mapping(value: Any, location: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MachineProfileError(f"{location} must be a mapping.")
    return value


def _expect_list(value: Any, location: str) -> list[Any]:
    if not isinstance(value, list):
        raise MachineProfileError(f"{location} must be a list.")
    return value


def _expect_string_list(value: Any, location: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise MachineProfileError(f"{location} must be a non-empty list of strings.")
    return [_expect_non_empty_string(item, f"{location}[{index}]") for index, item in enumerate(value)]


def _expect_non_empty_string(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MachineProfileError(f"{location} must be a non-empty string.")
    return value.strip()
