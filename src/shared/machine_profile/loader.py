from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

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
    SolenoidCenteringPreset,
    SolenoidCenteringScanRange,
    SolenoidCenteringWorkflowConfig,
    normalize_mode,
    normalize_plane,
)
from .pixel_geometry import resolve_flag_pixel_geometry


SUPPORTED_APP_NAMES = {
    "orbit_correct",
    "orbit_display",
    "beam_monitor",
    "energy_spectrum",
    "bba",
    "emit_measure",
    "solenoid_centering",
}
MODEL_APP_NAMES = {"bba", "emit_measure", "energy_spectrum"}
APP_WORKFLOW_FILES = {
    "orbit": "orbit_correct.json",
    "beam_monitor": "beam_monitor.json",
    "energy_spectrum": "energy_spectrum.json",
    "bba": "bba.json",
    "emit_measure": "emit_measure.json",
    "solenoid_centering": "solenoid_centering.json",
    "virtual_machine": "virtual_machine.json",
}
APP_WORKFLOW_NAMES_BY_APP = {
    "orbit_correct": ("orbit",),
    "orbit_display": ("orbit",),
    "beam_monitor": ("beam_monitor",),
    "energy_spectrum": ("energy_spectrum",),
    "bba": ("bba",),
    "emit_measure": ("emit_measure",),
    "solenoid_centering": ("solenoid_centering",),
}
PATHLIKE_MODEL_CONFIG_KEYS = (
    "_json",
    "_lattice",
    "_ele",
    "_lte",
    "_mat",
    "_file",
    "_path",
)
PATHLIKE_MODEL_CONFIG_NAMES = {"working_dir"}
MACHINE_ID_ENV = "HALF_LINAC_MACHINE_ID"
CONTROL_BACKEND_ENV = "HALF_LINAC_CONTROL_BACKEND"
LEGACY_MACHINE_ID_ENV = "HALF_MACHINE_ID"
LEGACY_CONTROL_BACKEND_ENV = "HALF_CONTROL_BACKEND"


@dataclass(frozen=True)
class VirtualMachinePredefinedUsedline:
    id: str
    label: str
    role: str


@dataclass(frozen=True)
class VirtualMachineLocalSegment:
    id: str
    label: str
    parent_usedline: str
    start_ids: tuple[str, ...]
    end_ids: tuple[str, ...]
    default_start_id: str
    default_end_id: str


@dataclass(frozen=True)
class VirtualMachineUsedlineWorkflow:
    predefined_usedlines: tuple[VirtualMachinePredefinedUsedline, ...]
    default_usedline: str
    local_segments: tuple[VirtualMachineLocalSegment, ...]
    default_segment_id: str
    segment_wait_s: float


def load_profile(machine_id: str | None = None) -> MachineProfile:
    profile_id = resolve_machine_id(machine_id)
    return _load_profile_for_machine_id(profile_id)


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

    profile_id = resolve_machine_id(machine_id)
    required_workflows = APP_WORKFLOW_NAMES_BY_APP[app_name]
    profile = _load_profile_for_machine_id(profile_id, workflow_names=required_workflows)
    _validate_basic_app_support(profile, app_name)
    selected_control_backend = ControlBackendConfig(
        name=resolve_control_backend(control_backend, profile.machine.default_mode)
    )
    requested_model_backend = model_backend
    if requested_model_backend is None and app_name == "energy_spectrum":
        workflow = profile.workflows.get("energy_spectrum")
        if isinstance(workflow, Mapping):
            configured_backend = workflow.get("model_backend")
            if configured_backend is not None:
                requested_model_backend = _expect_non_empty_string(
                    configured_backend,
                    "workflows.energy_spectrum.model_backend",
                )

    selected_model_backend = _resolve_model_backend(
        app_name,
        machine_root(profile_id),
        requested_model_backend,
    )

    orbit_workflow = None
    bba_workflow = None
    emit_measure_workflow = None
    solenoid_centering_workflow = None
    if app_name == "orbit_correct":
        orbit_workflow = load_orbit_workflow(profile)
    elif app_name == "bba":
        bba_workflow = load_bba_workflow(profile)
    elif app_name == "emit_measure":
        emit_measure_workflow = load_emit_measure_workflow(profile)
    elif app_name == "solenoid_centering":
        solenoid_centering_workflow = load_solenoid_centering_workflow(profile)

    if app_name in {"orbit_correct", "solenoid_centering"}:
        selected_model_backend = None

    return AppContext(
        app_name=app_name,
        profile=profile,
        control_backend=selected_control_backend,
        model_backend=selected_model_backend,
        orbit_workflow=orbit_workflow,
        bba_workflow=bba_workflow,
        emit_measure_workflow=emit_measure_workflow,
        solenoid_centering_workflow=solenoid_centering_workflow,
        selected_preset_id=preset_id,
    )


def load_orbit_workflow(profile: MachineProfile) -> OrbitWorkflowConfig:
    workflow = _expect_mapping(profile.workflows.get("orbit"), "workflows.orbit")
    if not any(name in workflow for name in ("bpms", "xcors", "ycors")):
        return _infer_orbit_workflow(profile)
    return OrbitWorkflowConfig(
        bpms=tuple(_expect_string_list(workflow.get("bpms"), "workflows.orbit.bpms")),
        xcors=tuple(_expect_string_list(workflow.get("xcors"), "workflows.orbit.xcors")),
        ycors=tuple(_expect_string_list(workflow.get("ycors"), "workflows.orbit.ycors")),
        default_target_bpms=tuple(
            _expect_optional_string_list(
                workflow.get("default_target_bpms"),
                "workflows.orbit.default_target_bpms",
            )
        ),
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
        presets,
    )
    bba2 = _parse_bba_family(
        workflow.get("bba2"),
        "bba2",
        "workflows.bba.bba2",
        presets,
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
            _expect_optional_string_list(
                workflow.get("twiss_quads"),
                "workflows.emit_measure.twiss_quads",
            )
        ),
        default_preset=_expect_non_empty_string(
            workflow.get("default_preset") or _infer_emit_default_preset(presets),
            "workflows.emit_measure.default_preset",
        ),
    )


def load_solenoid_centering_workflow(profile: MachineProfile) -> SolenoidCenteringWorkflowConfig:
    workflow = _expect_mapping(
        profile.workflows.get("solenoid_centering"),
        "workflows.solenoid_centering",
    )
    presets_raw = _expect_list(
        workflow.get("presets"),
        "workflows.solenoid_centering.presets",
    )

    presets: list[SolenoidCenteringPreset] = []
    presets_by_id: dict[str, SolenoidCenteringPreset] = {}
    for index, raw_preset in enumerate(presets_raw):
        location = f"workflows.solenoid_centering.presets[{index}]"
        preset = _parse_solenoid_centering_preset(raw_preset, location)
        if preset.solenoid is not None:
            element = profile.get_element(preset.solenoid)
            if element.kind != "solenoid":
                raise MachineProfileError(
                    f"{location}.solenoid must reference a solenoid element."
                )
            if "current_set" not in element.channels and "setpoint" not in element.channels:
                raise MachineProfileError(
                    f"{location}.solenoid element {preset.solenoid!r} must define "
                    "current_set or setpoint."
                )
        presets.append(preset)
        presets_by_id[preset.id] = preset

    return SolenoidCenteringWorkflowConfig(
        presets=tuple(presets),
        presets_by_id=presets_by_id,
        default_preset=_expect_non_empty_string(
            workflow.get("default_preset") or _infer_solenoid_centering_default_preset(presets),
            "workflows.solenoid_centering.default_preset",
        ),
    )


def resolve_virtual_machine_segment_choices(
    profile: MachineProfile,
) -> tuple[tuple[str, ...], tuple[str, ...], str, str]:
    usedline_workflow = resolve_virtual_machine_usedline_workflow(profile)
    segment = _select_virtual_machine_local_segment(usedline_workflow)
    start_ids = segment.start_ids
    end_ids = segment.end_ids
    default_start = segment.default_start_id
    default_end = segment.default_end_id

    if not start_ids:
        raise MachineProfileError(
            "VM simplified segment start candidates are empty. Add a virtual_machine workflow "
            "or define at least one quad element."
        )
    if not end_ids:
        raise MachineProfileError(
            "VM simplified segment end candidates are empty. Add a virtual_machine workflow "
            "or define at least one flag element with logical channel 'image'."
        )
    return start_ids, end_ids, default_start, default_end


def resolve_virtual_machine_usedline_workflow(
    profile: MachineProfile,
) -> VirtualMachineUsedlineWorkflow:
    workflow = profile.workflows.get("virtual_machine")
    if isinstance(workflow, Mapping):
        if "predefined_usedlines" in workflow or "local_segments" in workflow:
            return _parse_virtual_machine_usedline_workflow(profile, workflow)
        return _parse_legacy_virtual_machine_workflow(profile, workflow)
    return _infer_virtual_machine_usedline_workflow(profile)


def _select_virtual_machine_local_segment(
    workflow: VirtualMachineUsedlineWorkflow,
) -> VirtualMachineLocalSegment:
    if not workflow.local_segments:
        raise MachineProfileError(
            "VM simplified segment candidates are empty. Add workflows.virtual_machine.local_segments."
        )

    for segment in workflow.local_segments:
        if segment.id == workflow.default_segment_id:
            return segment
    return workflow.local_segments[0]


def _parse_virtual_machine_usedline_workflow(
    profile: MachineProfile,
    workflow: Mapping[str, Any],
) -> VirtualMachineUsedlineWorkflow:
    predefined_raw = _expect_list(
        workflow.get("predefined_usedlines"),
        "workflows.virtual_machine.predefined_usedlines",
    )
    if not predefined_raw:
        raise MachineProfileError("workflows.virtual_machine.predefined_usedlines must not be empty.")

    predefined = tuple(
        _parse_virtual_machine_predefined_usedline(item, index)
        for index, item in enumerate(predefined_raw)
    )
    predefined_ids = tuple(choice.id for choice in predefined)
    _require_unique_ids(predefined_ids, "workflows.virtual_machine.predefined_usedlines")

    default_usedline = _expect_non_empty_string(
        workflow.get("default_usedline") or predefined[0].id,
        "workflows.virtual_machine.default_usedline",
    )
    if default_usedline not in predefined_ids:
        raise MachineProfileError(
            "workflows.virtual_machine.default_usedline must belong to predefined_usedlines."
        )

    local_segments_raw = _expect_list(
        workflow.get("local_segments"),
        "workflows.virtual_machine.local_segments",
    )
    if not local_segments_raw:
        raise MachineProfileError("workflows.virtual_machine.local_segments must not be empty.")

    local_segments = tuple(
        _parse_virtual_machine_local_segment(
            item,
            index,
            default_usedline=default_usedline,
            predefined_ids=predefined_ids,
        )
        for index, item in enumerate(local_segments_raw)
    )
    segment_ids = tuple(segment.id for segment in local_segments)
    _require_unique_ids(segment_ids, "workflows.virtual_machine.local_segments")

    default_segment_id = _expect_non_empty_string(
        workflow.get("default_segment_id") or local_segments[0].id,
        "workflows.virtual_machine.default_segment_id",
    )
    if default_segment_id not in segment_ids:
        raise MachineProfileError(
            "workflows.virtual_machine.default_segment_id must belong to local_segments."
        )
    segment_wait_s = _optional_nonnegative_float(
        workflow,
        "segment_wait_s",
        default=8.0,
        location="workflows.virtual_machine.segment_wait_s",
    )

    return VirtualMachineUsedlineWorkflow(
        predefined_usedlines=predefined,
        default_usedline=default_usedline,
        local_segments=local_segments,
        default_segment_id=default_segment_id,
        segment_wait_s=segment_wait_s,
    )


def _parse_virtual_machine_predefined_usedline(
    raw: Any,
    index: int,
) -> VirtualMachinePredefinedUsedline:
    location = f"workflows.virtual_machine.predefined_usedlines[{index}]"
    if isinstance(raw, str):
        line_id = _expect_non_empty_string(raw, location)
        return VirtualMachinePredefinedUsedline(id=line_id, label=line_id, role="")

    item = _expect_mapping(raw, location)
    line_id = _expect_non_empty_string(item.get("id"), f"{location}.id")
    label = _expect_non_empty_string(item.get("label") or line_id, f"{location}.label")
    role = _expect_non_empty_string(item.get("role") or "", f"{location}.role") if item.get("role") else ""
    return VirtualMachinePredefinedUsedline(id=line_id, label=label, role=role)


def _parse_virtual_machine_local_segment(
    raw: Any,
    index: int,
    *,
    default_usedline: str,
    predefined_ids: tuple[str, ...],
) -> VirtualMachineLocalSegment:
    location = f"workflows.virtual_machine.local_segments[{index}]"
    item = _expect_mapping(raw, location)
    segment_id = _expect_non_empty_string(item.get("id"), f"{location}.id")
    label = _expect_non_empty_string(item.get("label") or segment_id, f"{location}.label")
    parent_usedline = _expect_non_empty_string(
        item.get("parent_usedline") or default_usedline,
        f"{location}.parent_usedline",
    )
    if parent_usedline not in predefined_ids:
        raise MachineProfileError(
            f"{location}.parent_usedline must belong to workflows.virtual_machine.predefined_usedlines."
        )

    start_ids = tuple(_expect_string_list(item.get("start_ids"), f"{location}.start_ids"))
    end_ids = tuple(_expect_string_list(item.get("end_ids"), f"{location}.end_ids"))
    default_start_id = _expect_non_empty_string(
        item.get("default_start_id") or start_ids[0],
        f"{location}.default_start_id",
    )
    if default_start_id not in start_ids:
        raise MachineProfileError(f"{location}.default_start_id must belong to start_ids.")

    default_end_id = _expect_non_empty_string(
        item.get("default_end_id") or end_ids[0],
        f"{location}.default_end_id",
    )
    if default_end_id not in end_ids:
        raise MachineProfileError(f"{location}.default_end_id must belong to end_ids.")

    return VirtualMachineLocalSegment(
        id=segment_id,
        label=label,
        parent_usedline=parent_usedline,
        start_ids=start_ids,
        end_ids=end_ids,
        default_start_id=default_start_id,
        default_end_id=default_end_id,
    )


def _parse_legacy_virtual_machine_workflow(
    profile: MachineProfile,
    workflow: Mapping[str, Any],
) -> VirtualMachineUsedlineWorkflow:
    start_ids = tuple(
        _expect_string_list(
            workflow.get("simple_segment_start_ids"),
            "workflows.virtual_machine.simple_segment_start_ids",
        )
    )
    end_ids = tuple(
        _expect_string_list(
            workflow.get("simple_segment_end_ids"),
            "workflows.virtual_machine.simple_segment_end_ids",
        )
    )

    for element_id in start_ids:
        profile.get_element(element_id)
    for element_id in end_ids:
        profile.get_element(element_id)

    default_start_id = _expect_non_empty_string(
        workflow.get("default_start_id") or start_ids[0],
        "workflows.virtual_machine.default_start_id",
    )
    if default_start_id not in start_ids:
        raise MachineProfileError(
            "workflows.virtual_machine.default_start_id must belong to simple_segment_start_ids."
        )

    default_end_id = _expect_non_empty_string(
        workflow.get("default_end_id") or end_ids[0],
        "workflows.virtual_machine.default_end_id",
    )
    if default_end_id not in end_ids:
        raise MachineProfileError(
            "workflows.virtual_machine.default_end_id must belong to simple_segment_end_ids."
        )

    main_line_id = _profile_runtime_line_name(profile) or "ALL_MAIN"
    predefined = [VirtualMachinePredefinedUsedline(id=main_line_id, label="Main Line", role="main")]
    esa_line_id = workflow.get("esa_line_id")
    if esa_line_id:
        esa_id = _expect_non_empty_string(
            esa_line_id,
            "workflows.virtual_machine.esa_line_id",
        )
        if esa_id != main_line_id:
            predefined.append(
                VirtualMachinePredefinedUsedline(
                    id=esa_id,
                    label="ESA Line",
                    role="energy_spectrum",
                )
            )

    local_segment = VirtualMachineLocalSegment(
        id="legacy_segment",
        label="Legacy Segment",
        parent_usedline=main_line_id,
        start_ids=start_ids,
        end_ids=end_ids,
        default_start_id=default_start_id,
        default_end_id=default_end_id,
    )
    return VirtualMachineUsedlineWorkflow(
        predefined_usedlines=tuple(predefined),
        default_usedline=main_line_id,
        local_segments=(local_segment,),
        default_segment_id=local_segment.id,
        segment_wait_s=_optional_nonnegative_float(
            workflow,
            "segment_wait_s",
            default=8.0,
            location="workflows.virtual_machine.segment_wait_s",
        ),
    )


def _infer_virtual_machine_usedline_workflow(profile: MachineProfile) -> VirtualMachineUsedlineWorkflow:
    start_ids = tuple(element.id for element in profile.elements if element.kind == "quad")
    end_ids = tuple(
        element.id
        for element in profile.elements
        if element.kind == "flag" and "image" in element.channels
    )
    main_line_id = _profile_runtime_line_name(profile)
    predefined = (
        (VirtualMachinePredefinedUsedline(id=main_line_id, label="Main Line", role="main"),)
        if main_line_id
        else ()
    )
    local_segment = VirtualMachineLocalSegment(
        id="inferred_segment",
        label="Inferred Segment",
        parent_usedline=main_line_id,
        start_ids=start_ids,
        end_ids=end_ids,
        default_start_id=start_ids[0] if start_ids else "",
        default_end_id=end_ids[0] if end_ids else "",
    )
    return VirtualMachineUsedlineWorkflow(
        predefined_usedlines=predefined,
        default_usedline=main_line_id,
        local_segments=(local_segment,),
        default_segment_id=local_segment.id,
        segment_wait_s=8.0,
    )


def _profile_runtime_line_name(profile: MachineProfile) -> str:
    if profile.runtime is None:
        return ""
    return profile.runtime.vm.line_name


def _require_unique_ids(values: tuple[str, ...], location: str) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen:
            duplicates.append(value)
        seen.add(value)
    if duplicates:
        raise MachineProfileError(
            f"{location} contains duplicate id(s): {', '.join(sorted(set(duplicates)))}."
        )


def _optional_nonnegative_float(
    raw_mapping: Mapping[str, Any],
    key: str,
    *,
    default: float,
    location: str,
) -> float:
    raw_value = raw_mapping.get(key, default)
    try:
        value = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise MachineProfileError(f"{location} must be numeric.") from exc
    if value < 0:
        raise MachineProfileError(f"{location} must be >= 0.")
    return value


def repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "repo_bootstrap.py").is_file():
            return parent
    raise MachineProfileError("Could not locate repo root from machine_profile package.")


def machine_root(machine_id: str) -> Path:
    return repo_root() / "configs" / "machines" / machine_id


def list_machine_profile_ids() -> tuple[str, ...]:
    machines_dir = repo_root() / "configs" / "machines"
    if not machines_dir.is_dir():
        return ()

    profile_ids: list[str] = []
    for candidate in sorted(machines_dir.iterdir(), key=lambda path: path.name):
        if not candidate.is_dir():
            continue
        if candidate.name.startswith("_"):
            continue
        if _has_directory_profile(candidate) or _has_legacy_profile(candidate):
            profile_ids.append(candidate.name)
    return tuple(profile_ids)


def describe_app_support(machine_id: str | None, app_name: str) -> tuple[bool, str | None]:
    try:
        load_app_context(app_name, machine_id=machine_id)
    except MachineProfileError as exc:
        return False, str(exc)
    return True, None


def list_control_backend_choices(machine_id: str | None = None) -> tuple[str, ...]:
    profile_id = resolve_machine_id(machine_id)
    profile = _load_profile_for_machine_id(profile_id)
    return tuple(profile.control_backends)


def resolve_machine_id(machine_id: str | None) -> str:
    raw_machine_id = machine_id
    if raw_machine_id is None:
        raw_machine_id = _runtime_env_value(MACHINE_ID_ENV, LEGACY_MACHINE_ID_ENV)

    profile_id = str(raw_machine_id).strip() or "half"
    if Path(profile_id).name != profile_id or profile_id in {".", ".."}:
        raise MachineProfileError(
            f"Invalid machine_id {profile_id!r}. Expected a simple profile directory name."
        )
    return profile_id


def _validate_basic_app_support(profile: MachineProfile, app_name: str) -> None:
    if app_name in {"orbit_correct", "orbit_display"}:
        bpm_count = sum(1 for element in profile.elements if element.kind == "bpm")
        if bpm_count <= 0:
            raise MachineProfileError(f"{app_name} requires at least one BPM element.")
        return

    if app_name == "beam_monitor":
        supported_flags = [
            element
            for element in profile.elements
            if element.kind == "flag" and "image" in element.channels
        ]
        if not supported_flags:
            raise MachineProfileError(
                "beam_monitor requires at least one flag element with logical channel 'image'."
            )
        workflow = profile.workflows.get("beam_monitor")
        if not isinstance(workflow, Mapping):
            raise MachineProfileError("beam_monitor requires apps/beam_monitor.json.")
        _validate_beam_monitor_workflow(profile, workflow)
        return

    if app_name == "energy_spectrum":
        workflow = profile.workflows.get("energy_spectrum")
        if not isinstance(workflow, Mapping):
            raise MachineProfileError(
                "energy_spectrum requires apps/energy_spectrum.json."
            )
        _validate_energy_spectrum_workflow(profile, workflow)
        return

    if app_name == "solenoid_centering":
        workflow = profile.workflows.get("solenoid_centering")
        if not isinstance(workflow, Mapping):
            raise MachineProfileError(
                "solenoid_centering requires apps/solenoid_centering.json."
            )
        load_solenoid_centering_workflow(profile)
        return


def resolve_control_backend(control_backend: str | None, default_mode: str) -> str:
    raw_control_backend = control_backend
    if raw_control_backend is None:
        raw_control_backend = _runtime_env_value(CONTROL_BACKEND_ENV, LEGACY_CONTROL_BACKEND_ENV)
    return normalize_mode(raw_control_backend or default_mode, "control_backend")


def _runtime_env_value(primary_name: str, legacy_name: str) -> str:
    primary_value = os.environ.get(primary_name, "")
    if primary_value:
        return primary_value
    return os.environ.get(legacy_name, "")


def _load_profile_for_machine_id(
    profile_id: str,
    workflow_names: tuple[str, ...] | None = None,
) -> MachineProfile:
    root = machine_root(profile_id)
    if _has_directory_profile(root):
        raw = _load_directory_profile_raw(root, workflow_names=workflow_names)
        profile = MachineProfile.from_dict(raw)
    else:
        raw = _load_legacy_profile_raw(root)
        profile = MachineProfile.from_dict(raw)

    _validate_optional_workflows(profile)
    return profile


def _has_directory_profile(root: Path) -> bool:
    return (root / "machine.json").is_file()


def _has_legacy_profile(root: Path) -> bool:
    return (root / "profile.json").is_file()


def _load_directory_profile_raw(
    root: Path,
    workflow_names: tuple[str, ...] | None = None,
) -> Mapping[str, Any]:
    machine_data = _load_directory_machine_data(root)
    machine_config = _expect_mapping(machine_data.get("machine"), "machine.json.machine")
    elements_raw = _expect_list(machine_data.get("elements"), "machine.json.elements")
    backend_channels = _load_directory_control_backend_channels(root, machine_config)
    workflows = _load_directory_workflows(root, workflow_names=workflow_names)

    elements: list[dict[str, Any]] = []
    for index, raw_element in enumerate(elements_raw):
        location = f"machine.json.elements[{index}]"
        element = _expect_mapping(raw_element, location)
        element_id = _expect_non_empty_string(element.get("id"), f"{location}.id")
        logical_channels = _expect_string_list(
            element.get("logical_channels"),
            f"{location}.logical_channels",
        )

        channels: dict[str, dict[str, str]] = {}
        for logical_channel in logical_channels:
            channel_modes: dict[str, str] = {}
            for backend_name, backend_mapping in backend_channels.items():
                raw_element_channels = backend_mapping.get(element_id)
                if raw_element_channels is None:
                    continue
                element_channels = _expect_mapping(
                    raw_element_channels,
                    f"control_backends.{backend_name}.channels.{element_id}",
                )
                if logical_channel not in element_channels:
                    continue
                channel_modes[backend_name] = _expect_non_empty_string(
                    element_channels.get(logical_channel),
                    f"control_backends.{backend_name}.channels.{element_id}.{logical_channel}",
                )
            if channel_modes:
                channels[logical_channel] = channel_modes

        elements.append(
            {
                "id": element_id,
                "kind": _expect_non_empty_string(element.get("kind"), f"{location}.kind"),
                "display_name": _expect_non_empty_string(
                    element.get("display_name"),
                    f"{location}.display_name",
                ),
                "order": _expect_int(element.get("order"), f"{location}.order"),
                "plane": element.get("plane"),
                "roles": _expect_optional_string_list(element.get("roles"), f"{location}.roles")
                if "roles" in element
                else None,
                "tags": _expect_optional_string_list(element.get("tags", []), f"{location}.tags"),
                "limits": dict(_expect_mapping(element.get("limits", {}), f"{location}.limits")),
                "channels": channels,
            }
        )

    return {
        "schema_version": str(machine_data.get("schema_version", "1")),
        "machine": dict(machine_config),
        "control_backends": list(backend_channels.keys()),
        "runtime": machine_data.get("runtime"),
        "elements": elements,
        "workflows": workflows,
    }


def _load_directory_machine_data(root: Path) -> Mapping[str, Any]:
    return _load_json_file(root / "machine.json", "machine.json")


def _load_directory_control_backend_channels(
    root: Path,
    machine_config: Mapping[str, Any],
) -> Mapping[str, Mapping[str, Any]]:
    control_dir = root / "control_backends"
    if not control_dir.is_dir():
        raise MachineProfileError(f"Missing control_backends directory: {control_dir}")

    default_mode = normalize_mode(machine_config.get("default_mode"), "machine.default_mode")
    backend_names = _ordered_backend_names(_discover_directory_control_backends(root), default_mode)
    if not backend_names:
        raise MachineProfileError(f"No control backend definitions found in {control_dir}")
    if default_mode not in backend_names:
        raise MachineProfileError(
            "machine.default_mode must match one of the declared control_backends/*.json files."
        )

    channels_by_backend: dict[str, Mapping[str, Any]] = {}
    for backend_name in backend_names:
        filename = control_dir / f"{backend_name}.json"
        payload = _load_json_file(
            filename,
            f"control_backends/{filename.name}",
        )
        declared_backend = normalize_mode(
            payload.get("backend", backend_name),
            f"control_backends/{filename.name}.backend",
        )
        if declared_backend != backend_name:
            raise MachineProfileError(
                f"control_backends/{filename.name} declares backend {declared_backend!r}, "
                f"expected {backend_name!r}."
            )
        channels_by_backend[backend_name] = _expect_mapping(
            payload.get("channels"),
            f"control_backends/{filename.name}.channels",
        )

    return channels_by_backend


def _discover_directory_control_backends(root: Path) -> tuple[str, ...]:
    control_dir = root / "control_backends"
    if not control_dir.is_dir():
        return ()

    backend_names: list[str] = []
    for path in sorted(control_dir.glob("*.json"), key=lambda file: file.name):
        payload = _load_json_file(path, f"control_backends/{path.name}")
        backend_name = normalize_mode(
            payload.get("backend", path.stem),
            f"control_backends/{path.name}.backend",
        )
        backend_names.append(backend_name)
    return tuple(dict.fromkeys(backend_names))


def _load_directory_workflows(
    root: Path,
    workflow_names: tuple[str, ...] | None = None,
) -> Mapping[str, Any]:
    apps_dir = root / "apps"
    selected_names = tuple(APP_WORKFLOW_FILES.keys() if workflow_names is None else workflow_names)
    if not apps_dir.is_dir():
        if workflow_names is None:
            return {"orbit": {}}
        if workflow_names == ():
            return {}
        if workflow_names == ("orbit",):
            return {"orbit": {}}
        raise MachineProfileError(f"Missing apps workflow directory: {apps_dir}")

    workflows: dict[str, Any] = {}
    for workflow_name in selected_names:
        filename = APP_WORKFLOW_FILES[workflow_name]
        workflow_path = apps_dir / filename
        if workflow_name == "orbit" and not workflow_path.is_file():
            workflows[workflow_name] = {}
            continue
        if not workflow_path.is_file():
            if workflow_names is None:
                continue
            raise MachineProfileError(f"Missing configuration file: apps/{filename}")
        workflows[workflow_name] = _load_json_file(
            workflow_path,
            f"apps/{filename}",
        )
    return workflows


def _load_legacy_profile_raw(root: Path) -> Mapping[str, Any]:
    profile_path = root / "profile.json"
    if not profile_path.is_file():
        raise MachineProfileError(f"Machine profile not found: {profile_path}")
    return _load_json_file(profile_path, str(profile_path))


def _resolve_model_backend(
    app_name: str,
    root: Path,
    model_backend: str | None,
) -> ModelBackendConfig | None:
    if app_name not in MODEL_APP_NAMES:
        return None

    directory_backend = _load_directory_model_backend(root, model_backend)
    if directory_backend is not None:
        return directory_backend

    if _has_directory_profile(root):
        raise MachineProfileError(
            f"{app_name} requires model_backends/*.json for machine directory {root.name!r}."
        )

    raise MachineProfileError(
        f"{app_name} on legacy profile {root.name!r} requires migration to a directory machine profile "
        "with machine.json and model_backends/*.json."
    )


def _validate_energy_spectrum_workflow(
    profile: MachineProfile,
    workflow: Mapping[str, Any],
) -> None:
    required_keys = (
        "flag_element",
        "flag_image_channel",
        "vm_watch_element",
        "bend_element",
        "esa_quads",
        "flag_pixel_shape",
        "flag_pixel_width_mm",
    )
    missing = [key for key in required_keys if key not in workflow]
    if missing:
        raise MachineProfileError(
            "workflows.energy_spectrum is missing required keys: "
            + ", ".join(sorted(missing))
        )

    flag_element = profile.get_element(
        _expect_non_empty_string(workflow.get("flag_element"), "workflows.energy_spectrum.flag_element")
    )
    flag_image_channel = _expect_non_empty_string(
        workflow.get("flag_image_channel"),
        "workflows.energy_spectrum.flag_image_channel",
    )
    _expect_non_empty_string(
        workflow.get("vm_watch_element"),
        "workflows.energy_spectrum.vm_watch_element",
    )
    if flag_image_channel not in flag_element.channels:
        raise MachineProfileError(
            f"Element {flag_element.id} is missing logical channel {flag_image_channel!r} "
            "required by workflows.energy_spectrum.flag_image_channel."
        )

    exposure_channel = workflow.get("flag_exposure_channel")
    if exposure_channel:
        exposure_name = _expect_non_empty_string(
            exposure_channel,
            "workflows.energy_spectrum.flag_exposure_channel",
        )
        if exposure_name not in flag_element.channels:
            raise MachineProfileError(
                f"Element {flag_element.id} is missing logical channel {exposure_name!r} "
                "required by workflows.energy_spectrum.flag_exposure_channel."
            )

    bend_element = profile.get_element(
        _expect_non_empty_string(workflow.get("bend_element"), "workflows.energy_spectrum.bend_element")
    )
    bend_channel = workflow.get("bend_channel")
    if bend_channel:
        bend_channel_name = _expect_non_empty_string(
            bend_channel,
            "workflows.energy_spectrum.bend_channel",
        )
        if bend_channel_name not in bend_element.channels:
            raise MachineProfileError(
                f"Element {bend_element.id} is missing logical channel {bend_channel_name!r} "
                "required by workflows.energy_spectrum.bend_channel."
            )
    elif not any(channel in bend_element.channels for channel in ("angle", "current_set", "current_readback")):
        raise MachineProfileError(
            f"Element {bend_element.id} must define angle, current_set, or current_readback "
            "for workflows.energy_spectrum.bend_element."
        )

    esa_quads = _expect_string_list(
        workflow.get("esa_quads"),
        "workflows.energy_spectrum.esa_quads",
    )
    if not esa_quads:
        raise MachineProfileError("workflows.energy_spectrum.esa_quads must not be empty.")
    for element_id in esa_quads:
        element = profile.get_element(element_id)
        if "K1" not in element.channels and "k1" not in element.channels:
            raise MachineProfileError(
                f"Element {element_id} is missing logical channel 'K1' required by "
                "workflows.energy_spectrum.esa_quads."
            )

    default_start = workflow.get("default_start_element")
    if default_start:
        element = profile.get_element(
            _expect_non_empty_string(
                default_start,
                "workflows.energy_spectrum.default_start_element",
            )
        )
        if element.kind != "quad":
            raise MachineProfileError(
                "workflows.energy_spectrum.default_start_element must reference a quad element."
            )

    pixel_shape = _expect_mapping(
        workflow.get("flag_pixel_shape"),
        "workflows.energy_spectrum.flag_pixel_shape",
    )
    pixel_width = _expect_mapping(
        workflow.get("flag_pixel_width_mm"),
        "workflows.energy_spectrum.flag_pixel_width_mm",
    )
    for backend_name in profile.control_backends:
        shape = pixel_shape.get(backend_name)
        if not isinstance(shape, list) or len(shape) != 2:
            raise MachineProfileError(
                f"workflows.energy_spectrum.flag_pixel_shape.{backend_name} must be [nx, ny]."
            )
        if backend_name not in pixel_width:
            raise MachineProfileError(
                f"workflows.energy_spectrum.flag_pixel_width_mm is missing backend {backend_name!r}."
            )

    conversion = workflow.get("energy_from_bend_current")
    if conversion is not None:
        conversion_map = _expect_mapping(
            conversion,
            "workflows.energy_spectrum.energy_from_bend_current",
        )
        for key in ("magnet_length_m", "deflect_angle_rad", "field_t_per_a"):
            value = conversion_map.get(key)
            if value is None:
                raise MachineProfileError(
                    f"workflows.energy_spectrum.energy_from_bend_current.{key} is required when "
                    "energy_from_bend_current is provided."
                )
            try:
                float(value)
            except (TypeError, ValueError) as exc:
                raise MachineProfileError(
                    f"workflows.energy_spectrum.energy_from_bend_current.{key} must be numeric."
                ) from exc


def _validate_virtual_machine_workflow(
    profile: MachineProfile,
    workflow: Mapping[str, Any],
) -> None:
    resolve_virtual_machine_usedline_workflow(profile)


def _validate_beam_monitor_workflow(
    profile: MachineProfile,
    workflow: Mapping[str, Any],
) -> None:
    has_structured_geometry = "flag_pixel_geometry" in workflow
    has_legacy_geometry = (
        "flag_pixel_shape" in workflow and "flag_pixel_width_mm" in workflow
    )
    if not has_structured_geometry and not has_legacy_geometry:
        raise MachineProfileError(
            "workflows.beam_monitor requires flag_pixel_geometry, or legacy "
            "flag_pixel_shape plus flag_pixel_width_mm."
        )

    flag_ids = {
        element.id
        for element in profile.elements
        if element.kind == "flag" and "image" in element.channels
    }
    geometry = workflow.get("flag_pixel_geometry")
    if isinstance(geometry, Mapping):
        by_flag = geometry.get("by_flag", {})
        if by_flag is not None:
            by_flag = _expect_mapping(
                by_flag,
                "workflows.beam_monitor.flag_pixel_geometry.by_flag",
            )
            unknown_flags = sorted(set(by_flag) - flag_ids)
            if unknown_flags:
                raise MachineProfileError(
                    "workflows.beam_monitor.flag_pixel_geometry.by_flag contains "
                    "unknown flag id(s): "
                    + ", ".join(unknown_flags)
                )
            backend_names = set(profile.control_backends)
            for flag_id, raw_flag_geometry in by_flag.items():
                flag_geometry = _expect_mapping(
                    raw_flag_geometry,
                    f"workflows.beam_monitor.flag_pixel_geometry.by_flag.{flag_id}",
                )
                unknown_backends = sorted(set(flag_geometry) - backend_names)
                if unknown_backends:
                    raise MachineProfileError(
                        "workflows.beam_monitor.flag_pixel_geometry.by_flag."
                        f"{flag_id} contains unknown backend(s): "
                        + ", ".join(unknown_backends)
                    )

    for backend_name in profile.control_backends:
        resolve_flag_pixel_geometry(
            workflow,
            "workflows.beam_monitor",
            backend_name,
        )
        for flag_id in flag_ids:
            resolve_flag_pixel_geometry(
                workflow,
                "workflows.beam_monitor",
                backend_name,
                flag_id,
            )


def _validate_optional_workflows(profile: MachineProfile) -> None:
    workflow = profile.workflows.get("virtual_machine")
    if isinstance(workflow, Mapping):
        _validate_virtual_machine_workflow(profile, workflow)


def _load_directory_model_backend(
    root: Path,
    requested: str | None,
) -> ModelBackendConfig | None:
    model_dir = root / "model_backends"
    if not model_dir.is_dir():
        return None

    configs: list[ModelBackendConfig] = []
    for path in sorted(model_dir.glob("*.json"), key=lambda file: file.name):
        payload = _load_json_file(path, f"model_backends/{path.name}")
        backend_name = _expect_non_empty_string(
            payload.get("backend"),
            f"model_backends/{path.name}.backend",
        )
        engine = _expect_non_empty_string(
            payload.get("engine"),
            f"model_backends/{path.name}.engine",
        )
        config = _expect_mapping(
            payload.get("config"),
            f"model_backends/{path.name}.config",
        )
        configs.append(
            ModelBackendConfig(
                name=backend_name,
                engine=engine,
                config=_resolve_model_config_paths(config),
            )
        )

    if not configs:
        return None

    if requested is None:
        for config in configs:
            if config.name == "simulation":
                return config
        return configs[0]

    requested_name = str(requested).strip().lower().replace("_", " ")
    for config in configs:
        aliases = {
            config.name.lower(),
            str(config.engine or "").lower(),
            f"{config.name}.{config.engine}".lower() if config.engine else config.name.lower(),
        }
        if requested_name in aliases:
            return config

    raise MachineProfileError(
        f"Unknown model backend {requested!r} for machine directory {root.name!r}."
    )


def _resolve_model_config_paths(config: Mapping[str, Any]) -> Mapping[str, Any]:
    resolved: dict[str, Any] = {}
    root = repo_root()
    for key, value in config.items():
        if (
            isinstance(value, str)
            and value.strip()
            and (
                key in PATHLIKE_MODEL_CONFIG_NAMES
                or any(key.endswith(suffix) for suffix in PATHLIKE_MODEL_CONFIG_KEYS)
            )
        ):
            path = Path(value.strip())
            resolved[key] = str(path if path.is_absolute() else root / path)
        else:
            resolved[key] = value
    return resolved


def _infer_orbit_workflow(profile: MachineProfile) -> OrbitWorkflowConfig:
    bpm_ids = _infer_orbit_ids(profile, kind="bpm")
    xcor_ids = _infer_orbit_ids(profile, kind="corr", plane="x")
    ycor_ids = _infer_orbit_ids(profile, kind="corr", plane="y")
    pair_count = min(len(bpm_ids), len(xcor_ids), len(ycor_ids))
    if pair_count <= 0:
        raise MachineProfileError(
            "Cannot infer workflows.orbit because the machine profile does not define "
            "enough BPM/XCOR/YCOR elements."
        )
    return OrbitWorkflowConfig(
        bpms=tuple(bpm_ids[:pair_count]),
        xcors=tuple(xcor_ids[:pair_count]),
        ycors=tuple(ycor_ids[:pair_count]),
    )


def _infer_orbit_ids(
    profile: MachineProfile,
    *,
    kind: str,
    plane: str | None = None,
) -> list[str]:
    candidates = [
        element
        for element in profile.elements
        if element.kind == kind and (plane is None or element.plane == plane)
    ]
    tagged = [element for element in candidates if "orbit" in element.tags]
    source = tagged if tagged else candidates
    return [element.id for element in source]


def _ordered_backend_names(backends: tuple[str, ...] | list[str], default_mode: str) -> list[str]:
    unique = sorted(set(backends))
    if default_mode in unique:
        unique.remove(default_mode)
        return [default_mode, *unique]
    return unique


def _load_json_file(path: Path, location: str) -> Mapping[str, Any]:
    if not path.is_file():
        raise MachineProfileError(f"Missing configuration file: {location}")
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, Mapping):
        raise MachineProfileError(f"{location} must contain a JSON object.")
    return raw


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


def _parse_bba_family(
    raw_family: Any,
    name: str,
    location: str,
    presets: list[BBAPreset],
) -> BBAFamilyConfig:
    family = _expect_optional_mapping(raw_family, location)
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
        correctors=tuple(_expect_optional_string_list(family.get("correctors"), f"{location}.correctors")),
        quads=tuple(_expect_optional_string_list(family.get("quads"), f"{location}.quads")),
        bpm1=tuple(_expect_optional_string_list(family.get("bpm1"), f"{location}.bpm1")),
        bpm2=tuple(_expect_optional_string_list(family.get("bpm2"), f"{location}.bpm2")),
        default_preset=_expect_non_empty_string(
            family.get("default_preset") or _infer_bba_default_preset(presets, name),
            f"{location}.default_preset",
        ),
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
        model_line=(
            _expect_non_empty_string(preset.get("model_line"), f"{location}.model_line")
            if preset.get("model_line") is not None
            else None
        ),
        scan=scan,
        analysis=_parse_emit_analysis_config(analysis_dict),
    )


def _parse_solenoid_centering_preset(
    raw_preset: Any,
    location: str,
) -> SolenoidCenteringPreset:
    preset = _expect_mapping(raw_preset, location)
    solenoid = (
        _expect_non_empty_string(
            preset.get("solenoid"),
            f"{location}.solenoid",
        )
        if preset.get("solenoid") is not None
        else None
    )
    solenoid_setpoint_pv = (
        _expect_non_empty_string(
            preset.get("solenoid_setpoint_pv"),
            f"{location}.solenoid_setpoint_pv",
        )
        if preset.get("solenoid_setpoint_pv") is not None
        else None
    )
    if solenoid is None and solenoid_setpoint_pv is None:
        raise MachineProfileError(f"{location} must define solenoid or solenoid_setpoint_pv.")

    return SolenoidCenteringPreset(
        id=_expect_non_empty_string(preset.get("id"), f"{location}.id"),
        display_name=_expect_non_empty_string(
            preset.get("display_name"),
            f"{location}.display_name",
        ),
        solenoid=solenoid,
        solenoid_setpoint_pv=solenoid_setpoint_pv,
        solenoid_readback_pv=(
            _expect_non_empty_string(
                preset.get("solenoid_readback_pv"),
                f"{location}.solenoid_readback_pv",
            )
            if preset.get("solenoid_readback_pv") is not None
            else None
        ),
        hcorr=_expect_non_empty_string(preset.get("hcorr"), f"{location}.hcorr"),
        vcorr=_expect_non_empty_string(preset.get("vcorr"), f"{location}.vcorr"),
        bpm=_expect_non_empty_string(preset.get("bpm"), f"{location}.bpm"),
        solenoid_scan=_parse_solenoid_centering_scan_range(
            _expect_mapping(preset.get("solenoid_scan"), f"{location}.solenoid_scan"),
        ),
        corrector_scan=_parse_solenoid_centering_scan_range(
            _expect_mapping(preset.get("corrector_scan"), f"{location}.corrector_scan"),
        ),
        samples_per_point=int(preset.get("samples_per_point")),
        settle_time_s=float(preset.get("settle_time_s")),
        sample_interval_s=float(preset.get("sample_interval_s")),
        max_rounds=int(preset.get("max_rounds")),
    )


def _parse_solenoid_centering_scan_range(
    raw_scan: Mapping[str, Any],
) -> SolenoidCenteringScanRange:
    return SolenoidCenteringScanRange(
        relative_from=float(raw_scan.get("relative_from")),
        relative_to=float(raw_scan.get("relative_to")),
        steps=int(raw_scan.get("steps")),
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
        sample_interval=_optional_float(raw_scan, "sample_interval"),
    )


def _parse_bba_analysis_config(raw_analysis: Mapping[str, Any]) -> BBAAnalysisConfig:
    return BBAAnalysisConfig(
        energy_mev=_optional_float(raw_analysis, "energy_mev"),
        bpm1_samples=_optional_int(raw_analysis, "bpm1_samples"),
        by_formula=_optional_string(raw_analysis, "by_formula"),
        bx_formula=_optional_string(raw_analysis, "bx_formula"),
        leff_by=_optional_float(raw_analysis, "leff_by"),
        leff_bx=_optional_float(raw_analysis, "leff_bx"),
        quad_leff=_optional_float(raw_analysis, "quad_leff"),
    )


def _parse_emit_scan_config(raw_scan: Mapping[str, Any]) -> EmitScanConfig:
    return EmitScanConfig(
        k1_from=_optional_float(raw_scan, "k1_from"),
        k1_end=_optional_float(raw_scan, "k1_end"),
        k1_steps=_optional_int(raw_scan, "k1_steps"),
        samples=_optional_int(raw_scan, "samples"),
        settle_time=_optional_float(raw_scan, "settle_time"),
        sample_interval=_optional_float(raw_scan, "sample_interval"),
    )


def _parse_emit_analysis_config(raw_analysis: Mapping[str, Any]) -> EmitAnalysisConfig:
    return EmitAnalysisConfig(
        energy_mev=_optional_float(raw_analysis, "energy_mev"),
    )


def _infer_bba_default_preset(presets: list[BBAPreset], family_name: str) -> str:
    for preset in presets:
        if preset.family == family_name:
            return preset.id
    raise MachineProfileError(
        f"Could not infer default preset for BBA family {family_name!r}: no matching preset found."
    )


def _infer_emit_default_preset(presets: list[EmitPreset]) -> str:
    if not presets:
        raise MachineProfileError("workflows.emit_measure.presets must contain at least one preset.")
    return presets[0].id


def _infer_solenoid_centering_default_preset(
    presets: list[SolenoidCenteringPreset],
) -> str:
    if not presets:
        raise MachineProfileError("workflows.solenoid_centering.presets must contain at least one preset.")
    return presets[0].id


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


def _expect_optional_mapping(value: Any, location: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    return _expect_mapping(value, location)


def _expect_list(value: Any, location: str) -> list[Any]:
    if not isinstance(value, list):
        raise MachineProfileError(f"{location} must be a list.")
    return value


def _expect_string_list(value: Any, location: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise MachineProfileError(f"{location} must be a non-empty list of strings.")
    return [_expect_non_empty_string(item, f"{location}[{index}]") for index, item in enumerate(value)]


def _expect_optional_string_list(value: Any, location: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise MachineProfileError(f"{location} must be a list of strings.")
    return [_expect_non_empty_string(item, f"{location}[{index}]") for index, item in enumerate(value)]


def _expect_non_empty_string(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MachineProfileError(f"{location} must be a non-empty string.")
    return value.strip()


def _expect_int(value: Any, location: str) -> int:
    if not isinstance(value, int):
        raise MachineProfileError(f"{location} must be an integer.")
    return value
