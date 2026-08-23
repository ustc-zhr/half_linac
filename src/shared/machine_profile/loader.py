from __future__ import annotations

import json
import math
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
    EmitAdaptiveScanConfig,
    EmitMeasureWorkflowConfig,
    EmitPreset,
    EmitScanConfig,
    MachineProfile,
    MachineProfileError,
    ModelBackendConfig,
    OrbitWorkflowConfig,
    SolenoidCenteringPreset,
    SolenoidCenteringScanRange,
    SolenoidCenteringMotionVerification,
    SolenoidCenteringWorkflowConfig,
    normalize_mode,
    normalize_plane,
)
from .energy_spectrum import (
    resolve_energy_spectrum_auto_tune,
    resolve_energy_spectrum_stations,
)
from .limits import LimitRange, effective_limit


SUPPORTED_APP_NAMES = {
    "orbit_correct",
    "orbit_display",
    "beam_monitor",
    "energy_spectrum",
    "rf_phase_scan",
    "bba",
    "emit_measure",
    "solenoid_centering",
    "solenoid_field_guide",
    "dispersion_correction",
    "hv_feedback",
    "ct_monitor",
    "power_source_timing",
}
MODEL_APP_NAMES = {"bba", "emit_measure", "energy_spectrum", "dispersion_correction"}
APP_WORKFLOW_FILES = {
    "orbit": "orbit_correct.json",
    "beam_monitor": "beam_monitor.json",
    "energy_spectrum": "energy_spectrum.json",
    "rf_phase_scan": "rf_phase_scan.json",
    "bba": "bba.json",
    "emit_measure": "emit_measure.json",
    "solenoid_centering": "solenoid_centering.json",
    "solenoid_field_guide": "solenoid_field_guide.json",
    "virtual_machine": "virtual_machine.json",
    "dispersion_correction": "dispersion_correction.json",
    "hv_feedback": "hv_feedback.json",
    "ct_monitor": "ct_monitor.json",
    "power_source_timing": "power_source_timing.json",
}
APP_WORKFLOW_NAMES_BY_APP = {
    "orbit_correct": ("orbit",),
    "orbit_display": ("orbit",),
    "beam_monitor": ("beam_monitor",),
    "energy_spectrum": ("energy_spectrum",),
    "rf_phase_scan": ("rf_phase_scan",),
    "bba": ("bba",),
    "emit_measure": ("emit_measure",),
    "solenoid_centering": ("solenoid_centering",),
    "solenoid_field_guide": ("solenoid_field_guide",),
    "dispersion_correction": ("dispersion_correction",),
    "hv_feedback": ("hv_feedback",),
    "ct_monitor": ("ct_monitor",),
    "power_source_timing": ("power_source_timing",),
}
PATHLIKE_MODEL_CONFIG_KEYS = (
    "_json",
    "_lattice",
    "_ele",
    "_lte",
    "_mat",
    "_twi",
    "_file",
    "_path",
)
PATHLIKE_MODEL_CONFIG_NAMES = {
    "working_dir",
    "optics_working_dir",
    "emit_working_dir",
    "energy_working_dir",
}
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
    selected_control_backend = ControlBackendConfig(
        name=resolve_control_backend(control_backend, profile.machine.default_mode)
    )
    _validate_basic_app_support(profile, app_name, selected_control_backend.name)
    selected_model_backend = _resolve_model_backend(
        app_name,
        machine_root(profile_id),
        model_backend,
    )

    orbit_workflow = None
    bba_workflow = None
    emit_measure_workflow = None
    solenoid_centering_workflow = None
    if app_name == "orbit_correct":
        orbit_workflow = load_orbit_workflow(profile)
    elif app_name == "bba":
        bba_workflow = load_bba_workflow(profile, selected_control_backend.name)
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


def load_model_context(
    machine_id: str | None = None,
    model_backend: str | None = None,
) -> AppContext:
    """Load a model-only context without selecting an application workflow."""
    profile_id = resolve_machine_id(machine_id)
    profile = _load_profile_for_machine_id(profile_id)
    selected_model_backend = _resolve_model_backend(
        "emit_measure",
        machine_root(profile_id),
        model_backend,
    )
    return AppContext(
        app_name="model_preview",
        profile=profile,
        control_backend=ControlBackendConfig(name="vm"),
        model_backend=selected_model_backend,
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


def load_bba_workflow(
    profile: MachineProfile,
    control_backend: str | None = None,
) -> BBAWorkflowConfig:
    workflow = _expect_mapping(profile.workflows.get("bba"), "workflows.bba")
    backend = normalize_mode(
        control_backend or profile.machine.default_mode,
        "BBA control backend",
    )
    presets_raw = _expect_list(workflow.get("presets"), "workflows.bba.presets")

    presets: list[BBAPreset] = []
    presets_by_id: dict[str, BBAPreset] = {}
    for index, raw_preset in enumerate(presets_raw):
        location = f"workflows.bba.presets[{index}]"
        preset = _parse_bba_preset(raw_preset, location, backend)
        presets.append(preset)
        presets_by_id[preset.id] = preset

    bba1 = _parse_bba_family(
        workflow.get("bba1"),
        "bba1",
        "workflows.bba.bba1",
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
        bba1=bba1,
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
        preset = _parse_solenoid_centering_preset(
            _merge_solenoid_centering_defaults(workflow, raw_preset, location),
            location,
        )
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


def _validate_basic_app_support(
    profile: MachineProfile,
    app_name: str,
    control_backend: str | None = None,
) -> None:
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

    if app_name == "rf_phase_scan":
        workflow = profile.workflows.get("rf_phase_scan")
        if not isinstance(workflow, Mapping):
            raise MachineProfileError("rf_phase_scan requires apps/rf_phase_scan.json.")
        if workflow.get("enabled", True) is False:
            return
        backends = tuple(normalize_mode(v, "workflows.rf_phase_scan.control_backends[]") for v in _expect_string_list(workflow.get("control_backends"), "workflows.rf_phase_scan.control_backends"))
        if control_backend not in backends:
            raise MachineProfileError(f"rf_phase_scan does not support backend {control_backend!r}.")
        candidates = [e for e in profile.elements if e.kind == "rf" and "llrf" in e.tags and "wrapped_phase" in e.tags and all("phase_set" in e.channels and b in e.channels["phase_set"] for b in backends)]
        if not candidates:
            raise MachineProfileError("rf_phase_scan requires configured LLRF phase_set elements.")
        if str(workflow.get("default_element", "")) not in {e.id for e in candidates}:
            raise MachineProfileError("workflows.rf_phase_scan.default_element is not an eligible LLRF.")
        diagnostics = _expect_mapping(workflow.get("diagnostics"), "workflows.rf_phase_scan.diagnostics")
        flag_element = _expect_non_empty_string(diagnostics.get("flag_element"), "workflows.rf_phase_scan.diagnostics.flag_element")
        flag_channel = _expect_non_empty_string(diagnostics.get("flag_image_channel"), "workflows.rf_phase_scan.diagnostics.flag_image_channel")
        flag = profile.get_element(flag_element)
        if flag_channel not in flag.channels or any(backend not in flag.channels[flag_channel] for backend in backends):
            raise MachineProfileError("workflows.rf_phase_scan flag image channel is unavailable.")
        energy_element = _expect_non_empty_string(workflow.get("energy_element"), "workflows.rf_phase_scan.energy_element")
        energy_channel = _expect_non_empty_string(workflow.get("energy_set_channel"), "workflows.rf_phase_scan.energy_set_channel")
        energy = profile.get_element(energy_element)
        if energy_channel not in energy.channels or any(backend not in energy.channels[energy_channel] for backend in backends):
            raise MachineProfileError("workflows.rf_phase_scan coordinated energy channel is unavailable.")
        _expect_finite_number(diagnostics.get("x_reference_mm"), "workflows.rf_phase_scan.diagnostics.x_reference_mm")
        if _expect_finite_number(diagnostics.get("design_eta_m"), "workflows.rf_phase_scan.diagnostics.design_eta_m") == 0:
            raise MachineProfileError("workflows.rf_phase_scan.diagnostics.design_eta_m must not be zero.")
        scan = _expect_mapping(workflow.get("scan"), "workflows.rf_phase_scan.scan")
        phase_scan = _expect_mapping(scan.get("phase"), "workflows.rf_phase_scan.scan.phase")
        phase_mode = str(phase_scan.get("mode", "")).strip().lower()
        if phase_mode not in {"relative", "absolute"}:
            raise MachineProfileError("workflows.rf_phase_scan.scan.phase.mode must be 'relative' or 'absolute'.")
        if str(phase_scan.get("unit", "")).strip().lower() != "deg":
            raise MachineProfileError("workflows.rf_phase_scan.scan.phase.unit must be 'deg'.")
        phase_start = float(phase_scan.get("low", 0))
        phase_stop = float(phase_scan.get("high", 0))
        if phase_start >= phase_stop or phase_stop - phase_start > 360:
            raise MachineProfileError("workflows.rf_phase_scan phase range is invalid.")
        if int(phase_scan.get("steps", 0)) < 3:
            raise MachineProfileError("workflows.rf_phase_scan.scan.phase.steps must be at least 3.")
        tracking = _expect_mapping(scan.get("energy_tracking"), "workflows.rf_phase_scan.scan.energy_tracking")
        tracking_window = float(tracking.get("tracking_half_window_mev", 0))
        fallback_window = float(tracking.get("fallback_half_window_mev", 0))
        if tracking_window <= 0 or fallback_window < tracking_window:
            raise MachineProfileError("workflows.rf_phase_scan energy tracking windows are invalid.")
        if int(tracking.get("max_consecutive_failures", 0)) < 1:
            raise MachineProfileError("workflows.rf_phase_scan.scan.energy_tracking.max_consecutive_failures must be at least 1.")
        sampling = _expect_mapping(scan.get("point_measurement"), "workflows.rf_phase_scan.scan.point_measurement")
        samples = int(sampling.get("samples_per_point", 0))
        min_valid = int(sampling.get("min_valid_samples", 0))
        if samples < 1 or min_valid < 1 or min_valid > samples:
            raise MachineProfileError("workflows.rf_phase_scan point measurement sample counts are invalid.")
        if float(sampling.get("settle_time_s", -1)) < 0:
            raise MachineProfileError("workflows.rf_phase_scan.scan.point_measurement.settle_time_s must not be negative.")
        if float(sampling.get("sample_interval_s", -1)) < 0:
            raise MachineProfileError("workflows.rf_phase_scan.scan.point_measurement.sample_interval_s must not be negative.")
        energy_match = _expect_mapping(workflow.get("energy_match"), "workflows.rf_phase_scan.energy_match")
        search = _expect_mapping(energy_match.get("search"), "workflows.rf_phase_scan.energy_match.search")
        match_location = "workflows.rf_phase_scan.energy_match"
        defaults_location = "workflows.rf_phase_scan.energy_match_defaults"
        match_defaults = _expect_mapping(workflow.get("energy_match_defaults"), defaults_location)
        match_low = _expect_finite_number(search.get("low"), f"{match_location}.search.low")
        match_high = _expect_finite_number(search.get("high"), f"{match_location}.search.high")
        if match_low >= match_high:
            raise MachineProfileError(f"{match_location}.search.low must be less than high.")
        if _expect_int(search.get("reacquire_steps"), f"{match_location}.search.reacquire_steps") < 2:
            raise MachineProfileError(f"{match_location}.search.reacquire_steps must be at least 2.")
        if _expect_finite_number(search.get("settle_time_s"), f"{match_location}.search.settle_time_s") < 0:
            raise MachineProfileError(f"{match_location}.search.settle_time_s must not be negative.")
        if str(search.get("unit", "")).strip().lower() != "mev":
            raise MachineProfileError(f"{match_location}.search.unit must be 'MeV'.")
        if str(search.get("mode", "")).strip().lower() != "absolute":
            raise MachineProfileError(f"{match_location}.search.mode must be 'absolute'.")
        if not isinstance(search.get("restore_initial_on_failure"), bool):
            raise MachineProfileError(f"{match_location}.search.restore_initial_on_failure must be boolean.")
        if str(match_defaults.get("profile_fit_method", "")).strip() not in {"Gauss fit", "Direct"}:
            raise MachineProfileError(f"{defaults_location}.profile_fit_method must be 'Gauss fit' or 'Direct'.")
        center_lock = _expect_mapping(match_defaults.get("center_lock"), f"{defaults_location}.center_lock")
        for samples_key, minimum_key in (
            ("frame_samples", "min_valid_frames"),
            ("verification_frame_samples", "verification_min_valid_frames"),
        ):
            sample_count = _expect_int(center_lock.get(samples_key), f"{defaults_location}.center_lock.{samples_key}")
            minimum_count = _expect_int(center_lock.get(minimum_key), f"{defaults_location}.center_lock.{minimum_key}")
            if sample_count < 1 or minimum_count < 1 or minimum_count > sample_count:
                raise MachineProfileError(f"{defaults_location}.center_lock {minimum_key} must be between 1 and {samples_key}.")
        if _expect_finite_number(center_lock.get("frame_interval_s"), f"{defaults_location}.center_lock.frame_interval_s") < 0:
            raise MachineProfileError(f"{defaults_location}.center_lock.frame_interval_s must not be negative.")
        for key in ("max_correction_step_mev", "center_tolerance_mm"):
            if _expect_finite_number(center_lock.get(key), f"{defaults_location}.center_lock.{key}") <= 0:
                raise MachineProfileError(f"{defaults_location}.center_lock.{key} must be positive.")
        if _expect_int(center_lock.get("max_iterations"), f"{defaults_location}.center_lock.max_iterations") < 1:
            raise MachineProfileError(f"{defaults_location}.center_lock.max_iterations must be at least 1.")
        return

    if app_name == "solenoid_centering":
        workflow = profile.workflows.get("solenoid_centering")
        if not isinstance(workflow, Mapping):
            raise MachineProfileError(
                "solenoid_centering requires apps/solenoid_centering.json."
            )
        configured_backends = workflow.get("control_backends")
        if configured_backends is not None:
            if not isinstance(configured_backends, (list, tuple)) or not configured_backends:
                raise MachineProfileError(
                    "workflows.solenoid_centering.control_backends must be a non-empty list."
                )
            normalized_backends = tuple(
                normalize_mode(
                    value,
                    f"workflows.solenoid_centering.control_backends[{index}]",
                )
                for index, value in enumerate(configured_backends)
            )
            unknown_backends = sorted(
                set(normalized_backends) - set(profile.control_backends)
            )
            if unknown_backends:
                raise MachineProfileError(
                    "workflows.solenoid_centering.control_backends contains "
                    f"unconfigured backend(s): {', '.join(unknown_backends)}."
                )
            if control_backend is not None and control_backend not in normalized_backends:
                raise MachineProfileError(
                    "solenoid_centering supports only "
                    f"{', '.join(normalized_backends)} backend(s); "
                    f"{control_backend!r} was requested."
                )
        load_solenoid_centering_workflow(profile)
        return

    if app_name == "dispersion_correction":
        workflow = profile.workflows.get("dispersion_correction")
        if not isinstance(workflow, Mapping):
            raise MachineProfileError(
                "dispersion_correction requires apps/dispersion_correction.json."
            )
        _validate_dispersion_correction_workflow(profile, workflow)
        return

    if app_name == "hv_feedback":
        workflow = profile.workflows.get("hv_feedback")
        if not isinstance(workflow, Mapping):
            raise MachineProfileError(
                "hv_feedback requires apps/hv_feedback.json."
            )
        _validate_hv_feedback_workflow(profile, resolve_hv_feedback_workflow(profile))
        return

    if app_name == "ct_monitor":
        workflow = profile.workflows.get("ct_monitor")
        if not isinstance(workflow, Mapping):
            raise MachineProfileError("ct_monitor requires apps/ct_monitor.json.")
        _validate_ct_monitor_workflow(
            profile,
            _normalize_ct_monitor_workflow(workflow),
            control_backend,
        )
        return

    if app_name == "power_source_timing":
        workflow = profile.workflows.get("power_source_timing")
        if not isinstance(workflow, Mapping):
            raise MachineProfileError(
                "power_source_timing requires apps/power_source_timing.json."
            )
        configured_backends = tuple(
            normalize_mode(value, "workflows.power_source_timing.control_backends[]")
            for value in _expect_string_list(
                workflow.get("control_backends"),
                "workflows.power_source_timing.control_backends",
            )
        )
        if control_backend not in configured_backends:
            raise MachineProfileError(
                f"power_source_timing does not support backend {control_backend!r}."
            )
        tag = _expect_non_empty_string(
            workflow.get("element_tag"),
            "workflows.power_source_timing.element_tag",
        )
        devices = tuple(
            str(value).strip().lower()
            for value in _expect_string_list(
                workflow.get("devices"),
                "workflows.power_source_timing.devices",
            )
        )
        if devices != ("hv", "llrf", "ssa", "kly"):
            raise MachineProfileError(
                "workflows.power_source_timing.devices must be [hv, llrf, ssa, kly]."
            )
        required_channels = {
            f"{device}_{suffix}"
            for device in devices
            for suffix in (
                "delay_set",
                "delay_readback",
                "enable",
                "width_set",
                "width_readback",
            )
        }
        candidates = [element for element in profile.elements if tag in element.tags]
        if not candidates:
            raise MachineProfileError(
                "power_source_timing requires at least one tagged timing element."
            )
        for element in candidates:
            missing = sorted(
                channel
                for channel in required_channels
                if channel not in element.channels
                or control_backend not in element.channels[channel]
            )
            if missing:
                raise MachineProfileError(
                    f"{element.id} is missing power-source timing channels: "
                    + ", ".join(missing)
                )
        if str(workflow.get("default_element", "")) not in {
            element.id for element in candidates
        }:
            raise MachineProfileError(
                "workflows.power_source_timing.default_element is not eligible."
            )
        if _expect_finite_number(
            workflow.get("minimum_us"),
            "workflows.power_source_timing.minimum_us",
        ) < 0:
            raise MachineProfileError(
                "workflows.power_source_timing.minimum_us must not be negative."
            )
        for key in ("readback_tolerance_us", "delay_step_us", "width_step_us"):
            if _expect_finite_number(
                workflow.get(key), f"workflows.power_source_timing.{key}"
            ) <= 0:
                raise MachineProfileError(
                    f"workflows.power_source_timing.{key} must be positive."
                )
        alignment = _expect_mapping(
            workflow.get("waveform_alignment", {}),
            "workflows.power_source_timing.waveform_alignment",
        )
        if alignment:
            reference = _expect_non_empty_string(
                alignment.get("reference_device"),
                "workflows.power_source_timing.waveform_alignment.reference_device",
            ).lower()
            if reference not in devices:
                raise MachineProfileError(
                    "power_source_timing waveform reference_device must be one of "
                    "hv, llrf, ssa, or kly."
                )
            display_mode = _expect_non_empty_string(
                alignment.get("default_display_mode"),
                "workflows.power_source_timing.waveform_alignment.default_display_mode",
            ).lower()
            if display_mode not in {"raw", "normalized"}:
                raise MachineProfileError(
                    "power_source_timing waveform default_display_mode must be raw "
                    "or normalized."
                )
            for key in ("default_threshold_fraction", "baseline_fraction"):
                value = _expect_finite_number(
                    alignment.get(key),
                    f"workflows.power_source_timing.waveform_alignment.{key}",
                )
                if not 0.0 < value < 1.0:
                    raise MachineProfileError(
                        f"power_source_timing waveform {key} must be between 0 and 1."
                    )
            if _expect_int(
                alignment.get("refresh_interval_ms"),
                "workflows.power_source_timing.waveform_alignment.refresh_interval_ms",
            ) <= 0:
                raise MachineProfileError(
                    "power_source_timing waveform refresh_interval_ms must be positive."
                )
            if _expect_finite_number(
                alignment.get("stale_after_s"),
                "workflows.power_source_timing.waveform_alignment.stale_after_s",
            ) <= 0:
                raise MachineProfileError(
                    "power_source_timing waveform stale_after_s must be positive."
                )
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
        image_geometry = dict(
            _expect_mapping(
                element.get("image_geometry", {}),
                f"{location}.image_geometry",
            )
        )
        if "image" in logical_channels:
            for backend_name, backend_mapping in backend_channels.items():
                element_channels = backend_mapping.get(element_id)
                if not isinstance(element_channels, Mapping) or "image" not in element_channels:
                    continue
                if backend_name not in image_geometry:
                    raise MachineProfileError(
                        f"{location}.image_geometry is missing backend "
                        f"{backend_name!r} for its image channel."
                    )

        limits = dict(_expect_mapping(element.get("limits", {}), f"{location}.limits"))
        if limits and not ({"low", "high"} & set(limits)):
            unknown_channels = sorted(set(limits) - set(logical_channels))
            if unknown_channels:
                raise MachineProfileError(
                    f"{location}.limits contains channels not declared in logical_channels: "
                    f"{', '.join(unknown_channels)}."
                )
            for channel_name, raw_channel_limits in limits.items():
                channel_limits = _expect_mapping(
                    raw_channel_limits,
                    f"{location}.limits.{channel_name}",
                )
                if set(channel_limits) != {"low", "high", "unit"}:
                    raise MachineProfileError(
                        f"{location}.limits.{channel_name} must define exactly "
                        "low, high, and unit."
                    )
                low = _expect_finite_number(
                    channel_limits.get("low"), f"{location}.limits.{channel_name}.low"
                )
                high = _expect_finite_number(
                    channel_limits.get("high"), f"{location}.limits.{channel_name}.high"
                )
                if low >= high:
                    raise MachineProfileError(
                        f"{location}.limits.{channel_name}.low must be less than high."
                    )
                _expect_non_empty_string(
                    channel_limits.get("unit"), f"{location}.limits.{channel_name}.unit"
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
                "limits": limits,
                "channels": channels,
                "image_geometry": image_geometry,
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
    stations = workflow.get("stations")
    if stations is not None:
        _, resolved_stations = resolve_energy_spectrum_stations(workflow)
        for effective_station in resolved_stations.values():
            _validate_energy_spectrum_workflow(profile, effective_station)
        return

    auto_tune = resolve_energy_spectrum_auto_tune(workflow)
    workflow = dict(workflow)
    workflow.setdefault("auto_tune_objective", auto_tune["objective"])
    if "center_lock" in auto_tune:
        workflow.setdefault("auto_tune_center_lock", auto_tune["center_lock"])
    if "scan" in auto_tune:
        workflow.setdefault("auto_tune_scan", auto_tune["scan"])
    if "actuator" in auto_tune:
        workflow.setdefault("auto_tune_actuator", auto_tune["actuator"])
    elif workflow.get("energy_element"):
        scan = auto_tune.get("scan", {})
        workflow["auto_tune_actuator"] = {
            "element": workflow["energy_element"],
            "channel": workflow.get("energy_set_channel", "setpoint"),
            "unit": scan.get("unit", "a.u.") if isinstance(scan, Mapping) else "a.u.",
        }

    required_keys = (
        "flag_element",
        "flag_image_channel",
        "bend_element",
        "esa_quads",
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

    for pv_key in ("energy_set_pv", "energy_reference_pv"):
        raw_pv = workflow.get(pv_key)
        if raw_pv is None:
            continue
        if isinstance(raw_pv, Mapping):
            unknown_backends = sorted(set(raw_pv) - set(profile.control_backends))
            if unknown_backends:
                raise MachineProfileError(
                    f"workflows.energy_spectrum.{pv_key} contains unknown backend(s): "
                    + ", ".join(unknown_backends)
                )
            for backend_name, pv_name in raw_pv.items():
                _expect_non_empty_string(
                    pv_name,
                    f"workflows.energy_spectrum.{pv_key}.{backend_name}",
                )
        else:
            _expect_non_empty_string(raw_pv, f"workflows.energy_spectrum.{pv_key}")

    for backend_key in ("energy_control_backends",):
        configured_backends = workflow.get(backend_key)
        if configured_backends is None:
            continue
        backend_names = _expect_optional_string_list(
            configured_backends,
            f"workflows.energy_spectrum.{backend_key}",
        )
        unknown_backends = sorted(set(backend_names) - set(profile.control_backends))
        if unknown_backends:
            raise MachineProfileError(
                f"workflows.energy_spectrum.{backend_key} contains unknown backend(s): "
                + ", ".join(unknown_backends)
            )

    for line_key in ("energy_dispersion_line_name", "energy_twiss_line_name"):
        line_name = workflow.get(line_key)
        if line_name is not None:
            _expect_non_empty_string(
                line_name,
                f"workflows.energy_spectrum.{line_key}",
            )

    model_lines = workflow.get("model_lines")
    if model_lines is not None:
        lines = _expect_mapping(
            model_lines,
            "workflows.energy_spectrum.model_lines",
        )
        unknown_lines = sorted(set(lines) - {"dispersion", "twiss"})
        if unknown_lines:
            raise MachineProfileError(
                "workflows.energy_spectrum.model_lines contains unknown key(s): "
                + ", ".join(unknown_lines)
            )
        for calculation in ("dispersion", "twiss"):
            _expect_non_empty_string(
                lines.get(calculation),
                f"workflows.energy_spectrum.model_lines.{calculation}",
            )

    model_snapshot_source = workflow.get("model_snapshot_source")
    if model_snapshot_source is not None:
        source_name = _expect_non_empty_string(
            model_snapshot_source,
            "workflows.energy_spectrum.model_snapshot_source",
        ).lower()
        if source_name not in {"design", "live", "real", "vm", "live_from_real", "live_from_vm"}:
            raise MachineProfileError(
                "workflows.energy_spectrum.model_snapshot_source must select design or live data."
            )

    energy_range = workflow.get("energy_range_mev")
    if energy_range is not None:
        if not isinstance(energy_range, list) or len(energy_range) != 2:
            raise MachineProfileError(
                "workflows.energy_spectrum.energy_range_mev must be [low, high]."
            )
        try:
            energy_low, energy_high = (float(energy_range[0]), float(energy_range[1]))
        except (TypeError, ValueError) as exc:
            raise MachineProfileError(
                "workflows.energy_spectrum.energy_range_mev must be numeric."
            ) from exc
        if (
            not math.isfinite(energy_low)
            or not math.isfinite(energy_high)
            or energy_low >= energy_high
        ):
            raise MachineProfileError(
                "workflows.energy_spectrum.energy_range_mev requires finite low < high."
            )

    design_eta = workflow.get("design_eta_m")
    if design_eta is not None:
        try:
            design_eta_value = float(design_eta)
        except (TypeError, ValueError) as exc:
            raise MachineProfileError(
                "workflows.energy_spectrum.design_eta_m must be numeric."
            ) from exc
        if not math.isfinite(design_eta_value):
            raise MachineProfileError(
                "workflows.energy_spectrum.design_eta_m must be finite."
            )

    energy_element_id = workflow.get("energy_element")
    if energy_element_id is not None:
        energy_element = profile.get_element(
            _expect_non_empty_string(
                energy_element_id,
                "workflows.energy_spectrum.energy_element",
            )
        )
        if energy_element.kind != "energy":
            raise MachineProfileError(
                "workflows.energy_spectrum.energy_element must reference an energy element."
            )
        for channel_key in ("energy_set_channel", "energy_reference_channel"):
            channel_name = workflow.get(channel_key)
            if channel_name is None:
                continue
            channel_name = _expect_non_empty_string(
                channel_name,
                f"workflows.energy_spectrum.{channel_key}",
            )
            if channel_name not in energy_element.channels:
                raise MachineProfileError(
                    f"Element {energy_element.id} is missing logical channel {channel_name!r} "
                    f"required by workflows.energy_spectrum.{channel_key}."
                )

    auto_tune_objective = workflow.get("auto_tune_objective")
    if auto_tune_objective is not None:
        objective = _expect_non_empty_string(
            auto_tune_objective,
            "workflows.energy_spectrum.auto_tune_objective",
        )
        if objective not in {
            "find_beam",
            "center_x_reference",
            "brightness_gated_x_fit",
            "brightness_then_profile_lock",
        }:
            raise MachineProfileError(
                "workflows.energy_spectrum.auto_tune_objective must be "
                "'find_beam', 'center_x_reference', 'brightness_gated_x_fit', or "
                "'brightness_then_profile_lock'."
            )

    actuator_element = None
    actuator_channel = "current_set"
    actuator_unit = "A"
    auto_tune_actuator = workflow.get("auto_tune_actuator")
    if auto_tune_actuator is not None:
        actuator = _expect_mapping(
            auto_tune_actuator,
            "workflows.energy_spectrum.auto_tune_actuator",
        )
        actuator_element = profile.get_element(
            _expect_non_empty_string(
                actuator.get("element"),
                "workflows.energy_spectrum.auto_tune_actuator.element",
            )
        )
        actuator_channel = _expect_non_empty_string(
            actuator.get("channel"),
            "workflows.energy_spectrum.auto_tune_actuator.channel",
        )
        if actuator_channel not in actuator_element.channels:
            raise MachineProfileError(
                f"Element {actuator_element.id} is missing logical channel "
                f"{actuator_channel!r} required by auto_tune_actuator."
            )
        actuator_unit = _expect_non_empty_string(
            actuator.get("unit"),
            "workflows.energy_spectrum.auto_tune_actuator.unit",
        )

    auto_tune_scan = workflow.get("auto_tune_scan", workflow.get("bend_scan"))
    if auto_tune_scan is not None:
        scan = _expect_mapping(
            auto_tune_scan,
            "workflows.energy_spectrum.auto_tune_scan",
        )
        numeric_values = {}
        for key in ("low", "high", "settle_time_s"):
            legacy_key = {"low": "min", "high": "max"}.get(key)
            raw_key = key if key in scan else legacy_key
            if key == "settle_time_s" and raw_key not in scan:
                numeric_values[key] = 0.5
                continue
            if raw_key is None or raw_key not in scan:
                raise MachineProfileError(
                    f"workflows.energy_spectrum.auto_tune_scan.{key} is required."
                )
            try:
                numeric_values[key] = float(scan[raw_key])
            except (TypeError, ValueError) as exc:
                raise MachineProfileError(
                    f"workflows.energy_spectrum.auto_tune_scan.{key} must be numeric."
                ) from exc
            if not math.isfinite(numeric_values[key]):
                raise MachineProfileError(
                    f"workflows.energy_spectrum.auto_tune_scan.{key} must be finite."
                )
        if numeric_values["low"] >= numeric_values["high"]:
            raise MachineProfileError(
                "workflows.energy_spectrum.auto_tune_scan.low must be less than high."
            )
        if numeric_values["settle_time_s"] < 0:
            raise MachineProfileError(
                "workflows.energy_spectrum.auto_tune_scan.settle_time_s must not be negative."
            )
        for key in ("coarse_steps", "fine_steps"):
            steps = _expect_int(
                scan.get(key),
                f"workflows.energy_spectrum.auto_tune_scan.{key}",
            )
            if steps < 2:
                raise MachineProfileError(
                    f"workflows.energy_spectrum.auto_tune_scan.{key} must be at least 2."
                )

        scan_mode = str(scan.get("mode", "absolute")).strip().lower()
        if scan_mode not in {"absolute", "relative"}:
            raise MachineProfileError(
                "workflows.energy_spectrum.auto_tune_scan.mode must be "
                "'absolute' or 'relative'."
            )
        scan_unit = str(scan.get("unit", actuator_unit)).strip()
        if not scan_unit:
            raise MachineProfileError(
                "workflows.energy_spectrum.auto_tune_scan.unit must be non-empty."
            )
        if scan_unit.casefold() != actuator_unit.casefold():
            raise MachineProfileError(
                "workflows.energy_spectrum.auto_tune_scan.unit must match the "
                f"actuator unit {actuator_unit!r}."
            )

        if actuator_element is None:
            actuator_element = profile.get_element(str(workflow["bend_element"]))
        if actuator_element is not None:
            limits = actuator_element.limits_for(actuator_channel)
            if limits:
                machine_limit = LimitRange.from_mapping(limits)
                if scan_mode == "absolute":
                    effective_limit(
                        LimitRange(
                            numeric_values["low"], numeric_values["high"], scan_unit
                        ),
                        machine_limit,
                    )
                elif (
                    machine_limit.unit is not None
                    and machine_limit.unit.casefold() != scan_unit.casefold()
                ):
                    raise MachineProfileError(
                        f"Cannot use {scan_unit!r} scan limits with "
                        f"{machine_limit.unit!r} actuator limits."
                    )

    auto_tune_hybrid = workflow.get("auto_tune_hybrid")
    if auto_tune_hybrid is not None:
        hybrid = _expect_mapping(
            auto_tune_hybrid,
            "workflows.energy_spectrum.auto_tune_hybrid",
        )
        frame_samples = _expect_int(
            hybrid.get("frame_samples"),
            "workflows.energy_spectrum.auto_tune_hybrid.frame_samples",
        )
        min_valid_frames = _expect_int(
            hybrid.get("min_valid_frames"),
            "workflows.energy_spectrum.auto_tune_hybrid.min_valid_frames",
        )
        if frame_samples < 1:
            raise MachineProfileError(
                "workflows.energy_spectrum.auto_tune_hybrid.frame_samples must be at least 1."
            )
        if not 1 <= min_valid_frames <= frame_samples:
            raise MachineProfileError(
                "workflows.energy_spectrum.auto_tune_hybrid.min_valid_frames must be "
                "between 1 and frame_samples."
            )

        numeric_hybrid = {}
        for key in (
            "frame_interval_s",
            "brightness_fraction",
            "max_center_spread_mm",
            "target_tolerance_mm",
            "min_fit_correlation",
        ):
            try:
                numeric_hybrid[key] = float(hybrid[key])
            except KeyError as exc:
                raise MachineProfileError(
                    f"workflows.energy_spectrum.auto_tune_hybrid.{key} is required."
                ) from exc
            except (TypeError, ValueError) as exc:
                raise MachineProfileError(
                    f"workflows.energy_spectrum.auto_tune_hybrid.{key} must be numeric."
                ) from exc
            if not math.isfinite(numeric_hybrid[key]):
                raise MachineProfileError(
                    f"workflows.energy_spectrum.auto_tune_hybrid.{key} must be finite."
                )
        if numeric_hybrid["frame_interval_s"] < 0:
            raise MachineProfileError(
                "workflows.energy_spectrum.auto_tune_hybrid.frame_interval_s must not be negative."
            )
        if not 0 < numeric_hybrid["brightness_fraction"] <= 1:
            raise MachineProfileError(
                "workflows.energy_spectrum.auto_tune_hybrid.brightness_fraction must be in (0, 1]."
            )
        for key in ("max_center_spread_mm", "target_tolerance_mm"):
            if numeric_hybrid[key] <= 0:
                raise MachineProfileError(
                    f"workflows.energy_spectrum.auto_tune_hybrid.{key} must be positive."
                )
        if not 0 <= numeric_hybrid["min_fit_correlation"] <= 1:
            raise MachineProfileError(
                "workflows.energy_spectrum.auto_tune_hybrid.min_fit_correlation must be in [0, 1]."
            )

    auto_tune_center_lock = workflow.get("auto_tune_center_lock")
    if auto_tune_center_lock is not None:
        center_lock = _expect_mapping(
            auto_tune_center_lock,
            "workflows.energy_spectrum.auto_tune_center_lock",
        )
        integer_values = {}
        for key in (
            "frame_samples",
            "min_valid_frames",
            "verification_frame_samples",
            "verification_min_valid_frames",
        ):
            integer_values[key] = _expect_int(
                center_lock.get(key),
                f"workflows.energy_spectrum.auto_tune_center_lock.{key}",
            )
        if integer_values["frame_samples"] < 1:
            raise MachineProfileError(
                "workflows.energy_spectrum.auto_tune_center_lock.frame_samples "
                "must be at least 1."
            )
        if not (
            1
            <= integer_values["min_valid_frames"]
            <= integer_values["frame_samples"]
        ):
            raise MachineProfileError(
                "workflows.energy_spectrum.auto_tune_center_lock.min_valid_frames "
                "must be between 1 and frame_samples."
            )
        if integer_values["verification_frame_samples"] < 1:
            raise MachineProfileError(
                "workflows.energy_spectrum.auto_tune_center_lock."
                "verification_frame_samples must be at least 1."
            )
        if not (
            1
            <= integer_values["verification_min_valid_frames"]
            <= integer_values["verification_frame_samples"]
        ):
            raise MachineProfileError(
                "workflows.energy_spectrum.auto_tune_center_lock."
                "verification_min_valid_frames must be between 1 and "
                "verification_frame_samples."
            )
        numeric_center_lock = {}
        for key in (
            "frame_interval_s",
            "center_step",
            "max_total_offset",
            "center_tolerance_mm",
        ):
            try:
                numeric_center_lock[key] = float(center_lock[key])
            except KeyError as exc:
                raise MachineProfileError(
                    f"workflows.energy_spectrum.auto_tune_center_lock.{key} is required."
                ) from exc
            except (TypeError, ValueError) as exc:
                raise MachineProfileError(
                    f"workflows.energy_spectrum.auto_tune_center_lock.{key} must be numeric."
                ) from exc
            if not math.isfinite(numeric_center_lock[key]):
                raise MachineProfileError(
                    f"workflows.energy_spectrum.auto_tune_center_lock.{key} must be finite."
                )
        if numeric_center_lock["frame_interval_s"] < 0:
            raise MachineProfileError(
                "workflows.energy_spectrum.auto_tune_center_lock.frame_interval_s "
                "must not be negative."
            )
        for key in (
            "center_step",
            "max_total_offset",
            "center_tolerance_mm",
        ):
            if numeric_center_lock[key] <= 0:
                raise MachineProfileError(
                    f"workflows.energy_spectrum.auto_tune_center_lock.{key} must be positive."
                )


def _validate_dispersion_correction_workflow(
    profile: MachineProfile,
    workflow: Mapping[str, Any],
) -> None:
    required_keys = (
        "control_backends",
        "energy_knob",
        "measurement",
        "solver",
        "safety",
    )
    missing = [key for key in required_keys if key not in workflow]
    if missing:
        raise MachineProfileError(
            "workflows.dispersion_correction is missing required keys: "
            + ", ".join(sorted(missing))
        )

    supported_backends = tuple(
        normalize_mode(value, "workflows.dispersion_correction.control_backends[]")
        for value in _expect_string_list(
            workflow.get("control_backends"),
            "workflows.dispersion_correction.control_backends",
        )
    )
    if not supported_backends:
        raise MachineProfileError(
            "workflows.dispersion_correction.control_backends must not be empty."
        )
    unknown_backends = sorted(set(supported_backends) - set(profile.control_backends))
    if unknown_backends:
        raise MachineProfileError(
            "workflows.dispersion_correction.control_backends contains unconfigured backend(s): "
            + ", ".join(unknown_backends)
        )
    model_only_backends = tuple(
        normalize_mode(
            value,
            "workflows.dispersion_correction.model_only_control_backends[]",
        )
        for value in _expect_optional_string_list(
            workflow.get("model_only_control_backends"),
            "workflows.dispersion_correction.model_only_control_backends",
        )
    )
    invalid_model_only_backends = sorted(
        set(model_only_backends) - set(supported_backends)
    )
    if invalid_model_only_backends:
        raise MachineProfileError(
            "workflows.dispersion_correction.model_only_control_backends contains "
            "backend(s) not listed in control_backends: "
            + ", ".join(invalid_model_only_backends)
        )
    raw_quadrupole_control = workflow.get("quadrupole_control")
    if raw_quadrupole_control is not None:
        quadrupole_control = _expect_mapping(
            raw_quadrupole_control,
            "workflows.dispersion_correction.quadrupole_control",
        )
        unknown_control_backends = sorted(
            set(quadrupole_control) - set(supported_backends)
        )
        if unknown_control_backends:
            raise MachineProfileError(
                "workflows.dispersion_correction.quadrupole_control contains "
                "backend(s) not listed in control_backends: "
                + ", ".join(unknown_control_backends)
            )
        missing_control_backends = sorted(
            set(supported_backends) - set(quadrupole_control)
        )
        if missing_control_backends:
            raise MachineProfileError(
                "workflows.dispersion_correction.quadrupole_control is missing "
                "configured backend(s): " + ", ".join(missing_control_backends)
            )
        for backend_name, raw_control in quadrupole_control.items():
            control = _expect_non_empty_string(
                raw_control,
                "workflows.dispersion_correction."
                f"quadrupole_control.{backend_name}",
            ).lower()
            if control not in {"current", "k1"}:
                raise MachineProfileError(
                    "workflows.dispersion_correction."
                    f"quadrupole_control.{backend_name} must be 'current' or 'K1'."
                )

    workflow_measurement = _expect_mapping(
        workflow.get("measurement"),
        "workflows.dispersion_correction.measurement",
    )
    default_plane = str(workflow_measurement.get("plane", "x")).strip().lower()
    if default_plane not in {"x", "y", "xy"}:
        raise MachineProfileError(
            "workflows.dispersion_correction.measurement.plane must be "
            "'x', 'y', or 'xy'."
        )

    sections = workflow.get("sections")
    if sections is None:
        _validate_dispersion_section(
            profile,
            workflow,
            "workflows.dispersion_correction",
            default_plane,
        )
    else:
        raw_sections = _expect_list(
            sections,
            "workflows.dispersion_correction.sections",
        )
        if not raw_sections:
            raise MachineProfileError(
                "workflows.dispersion_correction.sections must not be empty."
            )
        section_ids: list[str] = []
        for index, raw_section in enumerate(raw_sections):
            location = f"workflows.dispersion_correction.sections[{index}]"
            section = _expect_mapping(raw_section, location)
            section_id = _expect_non_empty_string(section.get("id"), f"{location}.id")
            section_ids.append(section_id)
            section_measurement = section.get("measurement")
            if section_measurement is None:
                section_plane = default_plane
            else:
                measurement_mapping = _expect_mapping(
                    section_measurement,
                    f"{location}.measurement",
                )
                section_plane = str(
                    measurement_mapping.get("plane", default_plane)
                ).strip().lower()
            if section_plane not in {"x", "y", "xy"}:
                raise MachineProfileError(
                    f"{location}.measurement.plane must be 'x', 'y', or 'xy'."
                )
            _validate_dispersion_section(
                profile,
                section,
                location,
                section_plane,
            )
            diagnostic_only = bool(section.get("diagnostic_only", False))
            for endpoint_key in ("model_entrance", "model_exit"):
                _expect_non_empty_string(
                    section.get(endpoint_key),
                    f"{location}.{endpoint_key}",
                )
            observables = _expect_list(
                section.get("model_observables", []),
                f"{location}.model_observables",
            )
            for observable_index, raw_observable in enumerate(observables):
                observable_location = (
                    f"{location}.model_observables[{observable_index}]"
                )
                observable = _expect_mapping(raw_observable, observable_location)
                _expect_non_empty_string(
                    observable.get("name"),
                    f"{observable_location}.name",
                )
                element_id = _expect_non_empty_string(
                    observable.get("element"),
                    f"{observable_location}.element",
                )
                if not diagnostic_only:
                    profile.get_element(element_id)
                component = _expect_non_empty_string(
                    observable.get("component"),
                    f"{observable_location}.component",
                ).lower()
                if component not in {"dx", "dxp", "dy", "dyp"}:
                    raise MachineProfileError(
                        f"{observable_location}.component must be dx, dxp, dy, or dyp."
                    )
                try:
                    target = float(observable.get("target", 0.0))
                except (TypeError, ValueError) as exc:
                    raise MachineProfileError(
                        f"{observable_location}.target must be numeric."
                    ) from exc
                if not math.isfinite(target):
                    raise MachineProfileError(
                        f"{observable_location}.target must be finite."
                    )
        if len(set(section_ids)) != len(section_ids):
            raise MachineProfileError(
                "workflows.dispersion_correction.sections contains duplicate ids."
            )
        default_section = str(workflow.get("default_section", section_ids[0])).strip()
        if default_section not in section_ids:
            raise MachineProfileError(
                "workflows.dispersion_correction.default_section must reference a configured section."
            )

    energy_knob = _expect_mapping(
        workflow.get("energy_knob"),
        "workflows.dispersion_correction.energy_knob",
    )
    energy_element = energy_knob.get("element")
    if energy_element:
        if isinstance(energy_element, Mapping):
            unknown_energy_backends = sorted(
                set(energy_element) - set(supported_backends) - {"default"}
            )
            if unknown_energy_backends:
                raise MachineProfileError(
                    "workflows.dispersion_correction.energy_knob.element contains "
                    "unknown backend(s): " + ", ".join(unknown_energy_backends)
                )
            energy_elements = {
                backend_name: str(
                    energy_element.get(
                        backend_name,
                        energy_element.get("default", ""),
                    )
                    or ""
                ).strip()
                for backend_name in supported_backends
            }
        else:
            element_id = _expect_non_empty_string(
                energy_element,
                "workflows.dispersion_correction.energy_knob.element",
            )
            energy_elements = {
                backend_name: element_id
                for backend_name in supported_backends
            }
        raw_set_channel = energy_knob.get("set_channel", "phase_set")
        for backend_name, element_id in energy_elements.items():
            if not element_id or backend_name in model_only_backends:
                continue
            element = profile.get_element(element_id)
            if isinstance(raw_set_channel, Mapping):
                set_channel = str(
                    raw_set_channel.get(
                        backend_name,
                        raw_set_channel.get("default", ""),
                    )
                    or ""
                ).strip()
            else:
                set_channel = _expect_non_empty_string(
                    raw_set_channel,
                    "workflows.dispersion_correction.energy_knob.set_channel",
                )
            if not set_channel:
                raise MachineProfileError(
                    "workflows.dispersion_correction.energy_knob.set_channel "
                    f"is missing backend {backend_name!r}."
                )
            channel_modes = element.channels.get(set_channel)
            if channel_modes is None or backend_name not in channel_modes:
                raise MachineProfileError(
                    f"Dispersion energy element {element.id!r} channel "
                    f"{set_channel!r} is missing backend mapping {backend_name!r}."
                )

    for key in ("solver", "safety"):
        _expect_mapping(
            workflow.get(key),
            f"workflows.dispersion_correction.{key}",
        )


def _validate_dispersion_section(
    profile: MachineProfile,
    section: Mapping[str, Any],
    location: str,
    plane: str,
) -> None:
    diagnostic_only = bool(section.get("diagnostic_only", False))
    joint = _expect_mapping(
        section.get("joint_response_analysis", {}),
        f"{location}.joint_response_analysis",
    )
    joint_targets = _expect_list(
        joint.get("targets", []),
        f"{location}.joint_response_analysis.targets",
    )
    joint_knobs = _expect_list(
        joint.get("knobs", []),
        f"{location}.joint_response_analysis.knobs",
    )
    joint_enabled = bool(joint_targets and joint_knobs)
    if bool(joint_targets) != bool(joint_knobs):
        raise MachineProfileError(
            f"{location}.joint_response_analysis requires targets and knobs."
        )
    if plane == "xy" and not diagnostic_only and not joint_enabled:
        raise MachineProfileError(
            f"{location}.measurement.plane='xy' requires diagnostic_only=true "
            "or joint_response_analysis."
        )
    target_bpms = _expect_optional_string_list(
        section.get("target_bpms", []),
        f"{location}.target_bpms",
    )
    if not target_bpms and not diagnostic_only and not joint_enabled:
        raise MachineProfileError(f"{location}.target_bpms must not be empty.")
    monitor_bpms = _expect_optional_string_list(
        section.get("monitor_bpms"),
        f"{location}.monitor_bpms",
    )
    if diagnostic_only and not monitor_bpms:
        raise MachineProfileError(
            f"{location}.monitor_bpms must not be empty for diagnostic-only sections."
        )
    if diagnostic_only and target_bpms:
        raise MachineProfileError(
            f"{location}.target_bpms must be empty for diagnostic-only sections."
        )
    overlap = sorted(set(target_bpms) & set(monitor_bpms))
    if overlap:
        raise MachineProfileError(
            f"{location}.monitor_bpms must not repeat correction target BPMs: "
            + ", ".join(overlap)
        )
    required_planes = ("x", "y") if plane == "xy" else (plane,)
    for bpm_id in (*monitor_bpms, *target_bpms):
        element = profile.get_element(bpm_id)
        if element.kind != "bpm" or any(
            required_plane not in element.channels
            for required_plane in required_planes
        ):
            raise MachineProfileError(
                f"Dispersion BPM {bpm_id!r} must reference a bpm with logical "
                "channel(s) " + ", ".join(required_planes) + "."
            )
    measurement_bpms = set((*monitor_bpms, *target_bpms))
    for index, raw_target in enumerate(joint_targets):
        target_location = (
            f"{location}.joint_response_analysis.targets[{index}]"
        )
        item = _expect_mapping(raw_target, target_location)
        bpm_id = _expect_non_empty_string(
            item.get("bpm"),
            f"{target_location}.bpm",
        )
        target_plane = _expect_non_empty_string(
            item.get("plane"),
            f"{target_location}.plane",
        ).lower()
        if target_plane not in {"x", "y"}:
            raise MachineProfileError(
                f"{target_location}.plane must be 'x' or 'y'."
            )
        if bpm_id not in measurement_bpms:
            raise MachineProfileError(
                f"{target_location}.bpm must be a measurement BPM."
            )

    target = section.get("target_dispersion_mm", [0.0] * len(target_bpms))
    if not isinstance(target, list) or len(target) != len(target_bpms):
        raise MachineProfileError(
            f"{location}.target_dispersion_mm must match target_bpms length."
        )

    knobs = _expect_list(section.get("knobs", []), f"{location}.knobs")
    if not knobs and not diagnostic_only and not joint_enabled:
        raise MachineProfileError(f"{location}.knobs must not be empty.")
    if diagnostic_only and knobs:
        raise MachineProfileError(
            f"{location}.knobs must be empty for diagnostic-only sections."
        )
    for index, raw_knob in enumerate((*knobs, *joint_knobs)):
        knob_location = (
            f"{location}.knobs[{index}]"
            if index < len(knobs)
            else (
                f"{location}.joint_response_analysis.knobs"
                f"[{index - len(knobs)}]"
            )
        )
        knob = _expect_mapping(raw_knob, knob_location)
        _expect_non_empty_string(knob.get("name"), f"{knob_location}.name")
        devices = _expect_mapping(knob.get("devices"), f"{knob_location}.devices")
        if not devices:
            raise MachineProfileError(f"{knob_location}.devices must not be empty.")
        raw_scan = knob.get("scan")
        if raw_scan is None:
            scan_step = knob.get("scan_step")
            max_offset = knob.get("limit")
        else:
            scan = _expect_mapping(raw_scan, f"{knob_location}.scan")
            scan_step = scan.get("step")
            max_offset = scan.get("max_offset")
            mode = _expect_non_empty_string(
                scan.get("mode", "relative"),
                f"{knob_location}.scan.mode",
            ).lower()
            if mode != "relative":
                raise MachineProfileError(
                    f"{knob_location}.scan.mode must be 'relative'."
                )
            if "unit" in scan:
                _expect_non_empty_string(
                    scan.get("unit"),
                    f"{knob_location}.scan.unit",
                )
        try:
            numeric_step = float(scan_step)
            numeric_max_offset = float(max_offset)
        except (TypeError, ValueError) as exc:
            raise MachineProfileError(
                f"{knob_location}.scan step and max_offset must be numeric."
            ) from exc
        if (
            not math.isfinite(numeric_step)
            or not math.isfinite(numeric_max_offset)
            or numeric_step <= 0
            or numeric_max_offset <= 0
            or numeric_step > numeric_max_offset
        ):
            raise MachineProfileError(
                f"{knob_location}.scan requires 0 < step <= max_offset."
            )
        for device_id in devices:
            element = profile.get_element(str(device_id))
            if element.kind != "quad":
                raise MachineProfileError(
                    f"{knob_location}.devices.{device_id} must reference a quad element."
                )


def resolve_hv_feedback_workflow(profile: MachineProfile) -> Mapping[str, Any]:
    workflow = _expect_mapping(
        profile.workflows.get("hv_feedback"),
        "workflows.hv_feedback",
    )
    normalized = dict(workflow)
    raw_units = _expect_list(
        workflow.get("feedback_units"),
        "workflows.hv_feedback.feedback_units",
    )
    normalized["feedback_units"] = [
        _normalize_hv_feedback_unit(
            raw_unit,
            f"workflows.hv_feedback.feedback_units[{index}]",
        )
        for index, raw_unit in enumerate(raw_units)
    ]
    return normalized


def _normalize_hv_feedback_unit(raw_unit: Any, location: str) -> dict[str, Any]:
    unit = dict(_expect_mapping(raw_unit, location))
    structured_control_keys = {"sampling", "feedback", "feedback_sampling", "reference_sampling"}
    has_structured_control = bool(structured_control_keys & set(unit))
    if has_structured_control and "control" in unit:
        raise MachineProfileError(
            f"{location} must not define both control and structured control sections."
        )
    if has_structured_control:
        sampling = _expect_mapping(unit.get("sampling"), f"{location}.sampling")
        feedback = _expect_mapping(unit.get("feedback"), f"{location}.feedback")
        if "feedback_sampling" in unit and "reference_sampling" in unit:
            raise MachineProfileError(
                f"{location} must not define both feedback_sampling and reference_sampling."
            )
        sampling_key = "feedback_sampling" if "feedback_sampling" in unit else "reference_sampling"
        reference_sampling = _expect_mapping(
            unit.get(sampling_key),
            f"{location}.{sampling_key}",
        )
        unit["control"] = {
            "sample_period_s": sampling.get("sample_period_s"),
            "average_window_s": sampling.get("average_window_s"),
            "update_period_s": feedback.get("update_period_s"),
            "gain_kv_per_relerr": feedback.get("gain_kv_per_relerr"),
            "max_step_kv": feedback.get("max_step_kv"),
            "total_limit_kv": feedback.get("total_limit_kv"),
            "reference_samples": reference_sampling.get("samples"),
            "reference_sample_interval_s": reference_sampling.get("sample_interval_s"),
        }
        for key in structured_control_keys:
            unit.pop(key, None)

    safety = dict(_expect_mapping(unit.get("safety"), f"{location}.safety"))
    hv_range = safety.get("hv_range_kv")
    legacy_hv_range = {"hv_min_kv", "hv_max_kv"} & set(safety)
    if hv_range is not None and legacy_hv_range:
        raise MachineProfileError(
            f"{location}.safety must not mix hv_range_kv with hv_min_kv/hv_max_kv."
        )
    if hv_range is not None:
        selected = _expect_mapping(hv_range, f"{location}.safety.hv_range_kv")
        safety["hv_min_kv"] = selected.get("low")
        safety["hv_max_kv"] = selected.get("high")
        safety.pop("hv_range_kv", None)

    amplitude_range = safety.get("feedback_amplitude_range_rel")
    legacy_amplitude_range = {
        "feedback_amplitude_min_rel",
        "feedback_amplitude_max_rel",
    } & set(safety)
    if amplitude_range is not None and legacy_amplitude_range:
        raise MachineProfileError(
            f"{location}.safety must not mix feedback_amplitude_range_rel with "
            "feedback_amplitude_min_rel/feedback_amplitude_max_rel."
        )
    if amplitude_range is not None:
        selected = _expect_mapping(
            amplitude_range,
            f"{location}.safety.feedback_amplitude_range_rel",
        )
        safety["feedback_amplitude_min_rel"] = selected.get("low")
        safety["feedback_amplitude_max_rel"] = selected.get("high")
        safety.pop("feedback_amplitude_range_rel", None)

    # These are mandatory safety invariants, not machine-tunable options.
    for key in ("require_valid_pv", "hold_on_fault"):
        if key in safety and safety[key] is not True:
            raise MachineProfileError(f"{location}.safety.{key} must be true.")
        safety[key] = True
    unit["safety"] = safety
    return unit


def _validate_hv_feedback_workflow(
    profile: MachineProfile,
    workflow: Mapping[str, Any],
) -> None:
    required_sections = (
        "control_backends",
        "feedback_units",
        "real_status",
        "write_control",
    )
    missing_sections = [key for key in required_sections if key not in workflow]
    if missing_sections:
        raise MachineProfileError(
            "workflows.hv_feedback is missing required keys: "
            + ", ".join(sorted(missing_sections))
        )

    configured_backends = tuple(
        normalize_mode(value, "workflows.hv_feedback.control_backends[]")
        for value in _expect_string_list(
            workflow.get("control_backends"),
            "workflows.hv_feedback.control_backends",
        )
    )
    if configured_backends != ("real",):
        raise MachineProfileError(
            "workflows.hv_feedback.control_backends must contain only 'real'."
        )
    if "real" not in profile.control_backends:
        raise MachineProfileError(
            "workflows.hv_feedback requires a configured real control backend."
        )

    units = _expect_list(
        workflow.get("feedback_units"),
        "workflows.hv_feedback.feedback_units",
    )
    if not units:
        raise MachineProfileError(
            "workflows.hv_feedback.feedback_units must not be empty."
        )
    unit_ids: set[str] = set()
    write_targets: dict[str, str] = {}
    for index, raw_unit in enumerate(units):
        location = f"workflows.hv_feedback.feedback_units[{index}]"
        unit = _normalize_hv_feedback_unit(raw_unit, location)
        unit_id = _expect_non_empty_string(unit.get("id"), f"{location}.id")
        if unit_id in unit_ids:
            raise MachineProfileError(f"{location}.id duplicates {unit_id!r}.")
        unit_ids.add(unit_id)
        _validate_hv_feedback_unit(profile, unit, location)
        hv = _expect_mapping(unit.get("hv"), f"{location}.hv")
        setpoint = _expect_mapping(hv.get("setpoint"), f"{location}.hv.setpoint")
        element_id = _expect_non_empty_string(
            setpoint.get("element"), f"{location}.hv.setpoint.element"
        )
        channel = _expect_non_empty_string(
            setpoint.get("channel"), f"{location}.hv.setpoint.channel"
        )
        target = str(profile.get_element(element_id).channels[channel]["real"])
        duplicate = write_targets.get(target)
        if duplicate is not None:
            raise MachineProfileError(
                f"{location}.hv.setpoint resolves to {target!r}, already used by "
                f"feedback unit {duplicate!r}."
            )
        write_targets[target] = unit_id

    write_control = _expect_mapping(
        workflow.get("write_control"),
        "workflows.hv_feedback.write_control",
    )
    if write_control.get("default") != "blocked" or write_control.get("real") != "allowed":
        raise MachineProfileError(
            "workflows.hv_feedback.write_control must block by default and allow real."
        )


def _validate_hv_feedback_unit(
    profile: MachineProfile,
    unit: Mapping[str, Any],
    location: str,
) -> None:
    for key in (
        "id",
        "label",
        "hv",
        "rf_channels",
        "default_feedback_channel",
        "control",
        "reference",
        "safety",
        "logging",
    ):
        if key not in unit:
            raise MachineProfileError(f"{location}.{key} is required.")
    _expect_non_empty_string(unit.get("label"), f"{location}.label")

    hv = _expect_mapping(unit.get("hv"), f"{location}.hv")
    for signal_name in ("setpoint", "readback"):
        _validate_hv_feedback_signal(
            profile,
            hv.get(signal_name),
            f"{location}.hv.{signal_name}",
        )

    raw_channels = _expect_list(unit.get("rf_channels"), f"{location}.rf_channels")
    if not raw_channels:
        raise MachineProfileError(f"{location}.rf_channels must not be empty.")
    channel_ids: set[str] = set()
    for index, raw_channel in enumerate(raw_channels):
        channel_location = f"{location}.rf_channels[{index}]"
        channel = _expect_mapping(raw_channel, channel_location)
        channel_id = _expect_non_empty_string(
            channel.get("id"), f"{channel_location}.id"
        )
        if channel_id in channel_ids:
            raise MachineProfileError(
                f"{channel_location}.id duplicates {channel_id!r}."
            )
        channel_ids.add(channel_id)
        _expect_non_empty_string(channel.get("label"), f"{channel_location}.label")
        for signal_name in ("amplitude", "phase"):
            _validate_hv_feedback_signal(
                profile,
                channel.get(signal_name),
                f"{channel_location}.{signal_name}",
            )
    default_channel = _expect_non_empty_string(
        unit.get("default_feedback_channel"),
        f"{location}.default_feedback_channel",
    )
    if default_channel not in channel_ids:
        raise MachineProfileError(
            f"{location}.default_feedback_channel must name one of its RF channels."
        )

    control = _expect_mapping(unit.get("control"), f"{location}.control")
    control_values = _finite_workflow_numbers(
        control,
        f"{location}.control",
        (
            "sample_period_s",
            "update_period_s",
            "average_window_s",
            "reference_samples",
            "reference_sample_interval_s",
            "gain_kv_per_relerr",
            "max_step_kv",
            "total_limit_kv",
        ),
    )
    if any(value <= 0 for value in control_values.values()):
        raise MachineProfileError(f"{location}.control values must be positive.")
    samples = control_values["reference_samples"]
    if not samples.is_integer() or not 3 <= samples <= 100000:
        raise MachineProfileError(
            f"{location}.control.reference_samples must be an integer between 3 and 100000."
        )
    if control_values["max_step_kv"] > control_values["total_limit_kv"]:
        raise MachineProfileError(
            f"{location}.control.max_step_kv must not exceed total_limit_kv."
        )

    reference = _expect_mapping(unit.get("reference"), f"{location}.reference")
    hv_kv = _finite_workflow_numbers(
        reference, f"{location}.reference", ("hv_kv",)
    )["hv_kv"]
    reference_channels = _expect_mapping(
        reference.get("channels"), f"{location}.reference.channels"
    )
    if set(reference_channels) != channel_ids:
        raise MachineProfileError(
            f"{location}.reference.channels must exactly match rf_channels."
        )
    for channel_id in channel_ids:
        values = _expect_mapping(
            reference_channels[channel_id],
            f"{location}.reference.channels.{channel_id}",
        )
        reference_values = _finite_workflow_numbers(
            values,
            f"{location}.reference.channels.{channel_id}",
            ("amplitude", "phase_deg"),
        )
        if reference_values["amplitude"] <= 0:
            raise MachineProfileError(
                f"{location}.reference.channels.{channel_id}.amplitude must be positive."
            )

    safety = _expect_mapping(unit.get("safety"), f"{location}.safety")
    safety_values = _finite_workflow_numbers(
        safety,
        f"{location}.safety",
        (
            "hv_min_kv",
            "hv_max_kv",
            "hv_readback_tolerance_kv",
            "amplitude_ratio_limit_rel",
            "feedback_amplitude_min_rel",
            "feedback_amplitude_max_rel",
        ),
    )
    if safety_values["hv_min_kv"] >= safety_values["hv_max_kv"]:
        raise MachineProfileError(
            f"{location}.safety.hv_min_kv must be less than hv_max_kv."
        )
    if any(
        safety_values[key] <= 0
        for key in (
            "hv_readback_tolerance_kv",
            "amplitude_ratio_limit_rel",
            "feedback_amplitude_min_rel",
            "feedback_amplitude_max_rel",
        )
    ):
        raise MachineProfileError(f"{location}.safety tolerances must be positive.")
    if not (
        safety_values["feedback_amplitude_min_rel"]
        <= 1.0
        <= safety_values["feedback_amplitude_max_rel"]
    ):
        raise MachineProfileError(
            f"{location}.safety feedback amplitude range must include 1.0."
        )
    phase_limits = _expect_mapping(
        safety.get("phase_limit_deg"), f"{location}.safety.phase_limit_deg"
    )
    if set(phase_limits) != channel_ids:
        raise MachineProfileError(
            f"{location}.safety.phase_limit_deg must exactly match rf_channels."
        )
    if any(
        _finite_workflow_numbers(
            phase_limits, f"{location}.safety.phase_limit_deg", (channel_id,)
        )[channel_id]
        <= 0
        for channel_id in channel_ids
    ):
        raise MachineProfileError(f"{location}.safety phase limits must be positive.")
    total_limit = control_values["total_limit_kv"]
    if not (
        safety_values["hv_min_kv"] <= hv_kv - total_limit
        and hv_kv + total_limit <= safety_values["hv_max_kv"]
    ):
        raise MachineProfileError(
            f"{location} reference HV +/- total_limit_kv must fit inside safety bounds."
        )
    for key in ("require_valid_pv", "hold_on_fault"):
        if safety.get(key) is not True:
            raise MachineProfileError(f"{location}.safety.{key} must be true.")

    logging = _expect_mapping(unit.get("logging"), f"{location}.logging")
    _expect_non_empty_string(
        logging.get("file_prefix"), f"{location}.logging.file_prefix"
    )
    flush_rows = _expect_int(
        logging.get("flush_every_n_rows"),
        f"{location}.logging.flush_every_n_rows",
    )
    if flush_rows <= 0:
        raise MachineProfileError(
            f"{location}.logging.flush_every_n_rows must be positive."
        )


def _validate_hv_feedback_signal(
    profile: MachineProfile,
    raw_signal: Any,
    location: str,
) -> None:
    signal = _expect_mapping(raw_signal, location)
    element_id = _expect_non_empty_string(signal.get("element"), f"{location}.element")
    channel = _expect_non_empty_string(signal.get("channel"), f"{location}.channel")
    element = profile.get_element(element_id)
    channel_modes = element.channels.get(channel)
    if channel_modes is None:
        raise MachineProfileError(
            f"{location} references missing channel {element_id}.{channel}."
        )
    if "real" not in channel_modes:
        raise MachineProfileError(
            f"{location} channel {element_id}.{channel} has no real mapping."
        )


def _finite_workflow_numbers(
    values: Mapping[str, Any],
    location: str,
    keys: tuple[str, ...],
) -> dict[str, float]:
    parsed: dict[str, float] = {}
    for key in keys:
        try:
            value = float(values[key])
        except KeyError as exc:
            raise MachineProfileError(f"{location}.{key} is required.") from exc
        except (TypeError, ValueError) as exc:
            raise MachineProfileError(f"{location}.{key} must be numeric.") from exc
        if not math.isfinite(value):
            raise MachineProfileError(f"{location}.{key} must be finite.")
        parsed[key] = value
    return parsed


def _validate_virtual_machine_workflow(
    profile: MachineProfile,
    workflow: Mapping[str, Any],
) -> None:
    resolve_virtual_machine_usedline_workflow(profile)


def _validate_beam_monitor_workflow(
    profile: MachineProfile,
    workflow: Mapping[str, Any],
) -> None:
    profile_method = workflow.get("profile_method", "Gaussian fit")
    if profile_method not in {"Gaussian fit", "RMS moments"}:
        raise MachineProfileError(
            "workflows.beam_monitor.profile_method must be 'Gaussian fit' or "
            "'RMS moments'."
        )

    sample_count = workflow.get("background_sample_count", 5)
    if not isinstance(sample_count, int) or isinstance(sample_count, bool) or sample_count <= 0:
        raise MachineProfileError(
            "workflows.beam_monitor.background_sample_count must be a positive integer."
        )
    try:
        sample_interval = float(workflow.get("background_sample_interval_s", 1.0))
    except (TypeError, ValueError) as exc:
        raise MachineProfileError(
            "workflows.beam_monitor.background_sample_interval_s must be numeric."
        ) from exc
    if not math.isfinite(sample_interval) or sample_interval < 0:
        raise MachineProfileError(
            "workflows.beam_monitor.background_sample_interval_s must be finite and non-negative."
        )


def resolve_ct_monitor_workflow(profile: MachineProfile) -> Mapping[str, Any]:
    workflow = _expect_mapping(
        profile.workflows.get("ct_monitor"),
        "workflows.ct_monitor",
    )
    return _normalize_ct_monitor_workflow(workflow)


def _normalize_ct_monitor_workflow(workflow: Mapping[str, Any]) -> dict[str, Any]:
    structured_keys = {
        "measurement",
        "default_pair",
        "acquisition",
        "rolling",
        "trend",
        "display",
    }
    if not structured_keys & set(workflow):
        return dict(workflow)

    legacy_keys = {
        "default_upstream",
        "default_downstream",
        "measurement_channel",
        "measurement_label",
        "measurement_unit",
        "scale_to_display_unit",
        "refresh_interval_ms",
        "event_queue_size",
        "pair_tolerance_s",
        "stale_timeout_s",
        "minimum_upstream_value",
        "rolling_window",
        "rolling_window_options",
        "rolling_window_input_range",
        "trend_window_s",
        "trend_window_options_s",
        "trend_window_input_range_s",
        "history_size",
        "max_plot_points",
        "trend_gap_s",
        "efficiency_axis_default_max_percent",
    }
    mixed = sorted(legacy_keys & set(workflow))
    if mixed:
        raise MachineProfileError(
            "workflows.ct_monitor must not mix structured and legacy fields: "
            + ", ".join(mixed)
        )

    measurement = _expect_mapping(
        workflow.get("measurement"),
        "workflows.ct_monitor.measurement",
    )
    default_pair = _expect_mapping(
        workflow.get("default_pair"),
        "workflows.ct_monitor.default_pair",
    )
    acquisition = _expect_mapping(
        workflow.get("acquisition"),
        "workflows.ct_monitor.acquisition",
    )
    rolling = _expect_mapping(
        workflow.get("rolling"),
        "workflows.ct_monitor.rolling",
    )
    trend = _expect_mapping(
        workflow.get("trend"),
        "workflows.ct_monitor.trend",
    )
    display = _expect_mapping(
        workflow.get("display"),
        "workflows.ct_monitor.display",
    )
    measurement_channel = measurement.get("channel")
    measurement_label = measurement.get("label", measurement_channel)

    normalized = {
        "control_backends": workflow.get("control_backends"),
        "default_upstream": default_pair.get("upstream"),
        "default_downstream": default_pair.get("downstream"),
        "measurement_channel": measurement_channel,
        "measurement_label": measurement_label,
        "measurement_unit": measurement.get("unit"),
        "scale_to_display_unit": measurement.get("scale_to_display_unit"),
        "minimum_upstream_value": measurement.get("minimum_upstream_value"),
        "refresh_interval_ms": acquisition.get("refresh_interval_ms"),
        "event_queue_size": acquisition.get("event_queue_size"),
        "pair_tolerance_s": acquisition.get("pair_tolerance_s"),
        "stale_timeout_s": acquisition.get("stale_timeout_s"),
        "rolling_window": rolling.get("default_window"),
        "rolling_window_options": rolling.get("options"),
        "rolling_window_input_range": rolling.get("input_range"),
        "trend_window_s": trend.get("default_window_s"),
        "trend_window_options_s": trend.get("options_s"),
        "trend_window_input_range_s": trend.get("input_range_s"),
        "history_size": trend.get("history_size"),
        "max_plot_points": trend.get("max_plot_points"),
        "trend_gap_s": trend.get("gap_s"),
        "efficiency_axis_default_max_percent": display.get(
            "efficiency_axis_max_percent"
        ),
    }
    for key in ("real_status", "write_control"):
        if key in workflow:
            normalized[key] = workflow[key]
    return normalized


def _validate_ct_monitor_workflow(
    profile: MachineProfile,
    workflow: Mapping[str, Any],
    control_backend: str | None,
) -> None:
    backend = normalize_mode(
        control_backend or profile.machine.default_mode,
        "ct_monitor control backend",
    )
    configured_backends = tuple(
        normalize_mode(value, "workflows.ct_monitor.control_backends[]")
        for value in _expect_string_list(
            workflow.get("control_backends"),
            "workflows.ct_monitor.control_backends",
        )
    )
    if not configured_backends:
        raise MachineProfileError(
            "workflows.ct_monitor.control_backends must not be empty."
        )
    unknown_backends = sorted(set(configured_backends) - set(profile.control_backends))
    if unknown_backends:
        raise MachineProfileError(
            "workflows.ct_monitor.control_backends contains unconfigured backend(s): "
            + ", ".join(unknown_backends)
        )
    if backend not in configured_backends:
        raise MachineProfileError(
            f"ct_monitor does not support backend {backend!r}; configured backends: "
            + ", ".join(configured_backends)
            + "."
        )
    measurement_channel = _expect_non_empty_string(
        workflow.get("measurement_channel"),
        "workflows.ct_monitor.measurement_channel",
    )
    _expect_non_empty_string(
        workflow.get("measurement_label"),
        "workflows.ct_monitor.measurement_label",
    )
    _expect_non_empty_string(
        workflow.get("measurement_unit"),
        "workflows.ct_monitor.measurement_unit",
    )
    measurement_elements = [
        element
        for element in profile.elements
        if element.kind == "ct"
        and backend in element.channels.get(measurement_channel, {})
    ]
    if len(measurement_elements) < 2:
        raise MachineProfileError(
            f"ct_monitor requires at least two CT elements with channel "
            f"{measurement_channel!r} for backend {backend!r}."
        )

    measurement_ids = {element.id for element in measurement_elements}
    upstream = _expect_non_empty_string(
        workflow.get("default_upstream"),
        "workflows.ct_monitor.default_upstream",
    )
    downstream = _expect_non_empty_string(
        workflow.get("default_downstream"),
        "workflows.ct_monitor.default_downstream",
    )
    if upstream == downstream:
        raise MachineProfileError(
            "workflows.ct_monitor default upstream and downstream must be different."
        )
    for key, element_id in (("default_upstream", upstream), ("default_downstream", downstream)):
        if element_id not in measurement_ids:
            raise MachineProfileError(
                f"workflows.ct_monitor.{key} must reference a CT with channel "
                f"{measurement_channel!r} available for backend {backend!r}."
            )

    scale_by_backend = _expect_mapping(
        workflow.get("scale_to_display_unit"),
        "workflows.ct_monitor.scale_to_display_unit",
    )
    unknown_scale_backends = sorted(set(scale_by_backend) - set(configured_backends))
    if unknown_scale_backends:
        raise MachineProfileError(
            "workflows.ct_monitor.scale_to_display_unit contains unsupported backend(s): "
            + ", ".join(unknown_scale_backends)
        )
    for backend_name in configured_backends:
        try:
            scale = float(scale_by_backend[backend_name])
        except KeyError as exc:
            raise MachineProfileError(
                "workflows.ct_monitor.scale_to_display_unit is missing backend "
                f"{backend_name!r}."
            ) from exc
        except (TypeError, ValueError) as exc:
            raise MachineProfileError(
                f"workflows.ct_monitor.scale_to_display_unit.{backend_name} must be numeric."
            ) from exc
        if not math.isfinite(scale) or scale <= 0:
            raise MachineProfileError(
                f"workflows.ct_monitor.scale_to_display_unit.{backend_name} must be finite and positive."
            )

    positive_numbers = (
        "refresh_interval_ms",
        "pair_tolerance_s",
        "minimum_upstream_value",
        "trend_window_s",
    )
    for key in positive_numbers:
        try:
            value = float(workflow[key])
        except KeyError as exc:
            raise MachineProfileError(f"workflows.ct_monitor.{key} is required.") from exc
        except (TypeError, ValueError) as exc:
            raise MachineProfileError(
                f"workflows.ct_monitor.{key} must be numeric."
            ) from exc
        if not math.isfinite(value) or value <= 0:
            raise MachineProfileError(
                f"workflows.ct_monitor.{key} must be finite and positive."
            )

    rolling_window = workflow.get("rolling_window")
    if not isinstance(rolling_window, int) or isinstance(rolling_window, bool) or rolling_window <= 0:
        raise MachineProfileError(
            "workflows.ct_monitor.rolling_window must be a positive integer."
        )
    event_queue_size = workflow.get("event_queue_size")
    if (
        not isinstance(event_queue_size, int)
        or isinstance(event_queue_size, bool)
        or event_queue_size < 2
    ):
        raise MachineProfileError(
            "workflows.ct_monitor.event_queue_size must be an integer of at least 2."
        )

    rolling_options = workflow.get("rolling_window_options")
    if (
        not isinstance(rolling_options, list)
        or not rolling_options
        or any(
            not isinstance(value, int) or isinstance(value, bool) or value <= 0
            for value in rolling_options
        )
        or rolling_window not in rolling_options
    ):
        raise MachineProfileError(
            "workflows.ct_monitor.rolling_window_options must be a non-empty list of "
            "positive integers containing rolling_window."
        )

    trend_options = workflow.get("trend_window_options_s")
    if not isinstance(trend_options, list) or not trend_options:
        raise MachineProfileError(
            "workflows.ct_monitor.trend_window_options_s must be a non-empty list."
        )
    try:
        normalized_trend_options = [float(value) for value in trend_options]
    except (TypeError, ValueError) as exc:
        raise MachineProfileError(
            "workflows.ct_monitor.trend_window_options_s must contain numeric values."
        ) from exc
    if (
        any(
            not math.isfinite(value) or value <= 0 or not value.is_integer()
            for value in normalized_trend_options
        )
        or not float(workflow["trend_window_s"]).is_integer()
        or float(workflow["trend_window_s"]) not in normalized_trend_options
    ):
        raise MachineProfileError(
            "workflows.ct_monitor.trend_window_options_s must contain positive integer "
            "second values including trend_window_s."
        )

    input_ranges: dict[str, tuple[int, int]] = {}
    for key in ("rolling_window_input_range", "trend_window_input_range_s"):
        raw_range = workflow.get(key)
        if (
            not isinstance(raw_range, list)
            or len(raw_range) != 2
            or any(
                not isinstance(value, int) or isinstance(value, bool) or value <= 0
                for value in raw_range
            )
            or raw_range[0] > raw_range[1]
        ):
            raise MachineProfileError(
                f"workflows.ct_monitor.{key} must be [minimum, maximum] positive integers."
            )
        input_ranges[key] = (raw_range[0], raw_range[1])

    rolling_input_min, rolling_input_max = input_ranges["rolling_window_input_range"]
    if (
        not rolling_input_min <= rolling_window <= rolling_input_max
        or any(
            not rolling_input_min <= value <= rolling_input_max
            for value in rolling_options
        )
    ):
        raise MachineProfileError(
            "workflows.ct_monitor rolling defaults and options must be inside "
            "rolling_window_input_range."
        )

    trend_input_min, trend_input_max = input_ranges["trend_window_input_range_s"]
    if (
        not trend_input_min <= float(workflow["trend_window_s"]) <= trend_input_max
        or any(
            not trend_input_min <= value <= trend_input_max
            for value in normalized_trend_options
        )
    ):
        raise MachineProfileError(
            "workflows.ct_monitor trend defaults and options must be inside "
            "trend_window_input_range_s."
        )

    history_size = workflow.get("history_size")
    max_plot_points = workflow.get("max_plot_points")
    if (
        not isinstance(history_size, int)
        or isinstance(history_size, bool)
        or history_size < rolling_input_max
    ):
        raise MachineProfileError(
            "workflows.ct_monitor.history_size must cover rolling_window_input_range."
        )
    if (
        not isinstance(max_plot_points, int)
        or isinstance(max_plot_points, bool)
        or max_plot_points < 12
        or max_plot_points > history_size
    ):
        raise MachineProfileError(
            "workflows.ct_monitor.max_plot_points must be between 12 and history_size."
        )

    try:
        efficiency_axis_max = float(workflow["efficiency_axis_default_max_percent"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MachineProfileError(
            "workflows.ct_monitor.efficiency_axis_default_max_percent must be numeric."
        ) from exc
    if not math.isfinite(efficiency_axis_max) or efficiency_axis_max <= 100:
        raise MachineProfileError(
            "workflows.ct_monitor.efficiency_axis_default_max_percent must exceed 100."
        )

    stale_by_backend = _expect_mapping(
        workflow.get("stale_timeout_s"),
        "workflows.ct_monitor.stale_timeout_s",
    )
    unknown_stale_backends = sorted(set(stale_by_backend) - set(configured_backends))
    if unknown_stale_backends:
        raise MachineProfileError(
            "workflows.ct_monitor.stale_timeout_s contains unsupported backend(s): "
            + ", ".join(unknown_stale_backends)
        )
    for backend_name in configured_backends:
        if backend_name not in stale_by_backend:
            raise MachineProfileError(
                f"workflows.ct_monitor.stale_timeout_s is missing backend {backend_name!r}."
            )
        raw_timeout = stale_by_backend[backend_name]
        if raw_timeout is None:
            continue
        try:
            timeout = float(raw_timeout)
        except (TypeError, ValueError) as exc:
            raise MachineProfileError(
                f"workflows.ct_monitor.stale_timeout_s.{backend_name} must be numeric or null."
            ) from exc
        if not math.isfinite(timeout) or timeout <= 0:
            raise MachineProfileError(
                f"workflows.ct_monitor.stale_timeout_s.{backend_name} must be finite and positive."
            )

    gap_by_backend = _expect_mapping(
        workflow.get("trend_gap_s"),
        "workflows.ct_monitor.trend_gap_s",
    )
    unknown_gap_backends = sorted(set(gap_by_backend) - set(configured_backends))
    if unknown_gap_backends:
        raise MachineProfileError(
            "workflows.ct_monitor.trend_gap_s contains unsupported backend(s): "
            + ", ".join(unknown_gap_backends)
        )
    for backend_name in configured_backends:
        if backend_name not in gap_by_backend:
            raise MachineProfileError(
                f"workflows.ct_monitor.trend_gap_s is missing backend {backend_name!r}."
            )
        raw_gap = gap_by_backend[backend_name]
        if raw_gap is None:
            continue
        try:
            gap = float(raw_gap)
        except (TypeError, ValueError) as exc:
            raise MachineProfileError(
                f"workflows.ct_monitor.trend_gap_s.{backend_name} must be numeric or null."
            ) from exc
        if not math.isfinite(gap) or gap <= 0:
            raise MachineProfileError(
                f"workflows.ct_monitor.trend_gap_s.{backend_name} must be finite and positive."
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


def _parse_bba_preset(
    raw_preset: Any,
    location: str,
    control_backend: str,
) -> BBAPreset:
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
        scan=_parse_bba_scan_config(
            _expect_mapping(preset.get("scan", {}), f"{location}.scan"),
            control_backend,
            f"{location}.scan",
        ),
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
    max_iters_raw = preset.get("max_iters")
    legacy_max_rounds_raw = preset.get("max_rounds")
    if max_iters_raw is not None and legacy_max_rounds_raw is not None:
        raise MachineProfileError(
            f"{location} must not define both max_iters and max_rounds."
        )
    max_iters = max_iters_raw if max_iters_raw is not None else legacy_max_rounds_raw
    readback_raw = preset.get("readback_verification")
    legacy_motion_raw = preset.get("motion_verification")
    if readback_raw is not None and legacy_motion_raw is not None:
        raise MachineProfileError(
            f"{location} must not define both readback_verification and "
            "motion_verification."
        )
    motion_raw = readback_raw if readback_raw is not None else legacy_motion_raw
    verification_location = (
        f"{location}.readback_verification"
        if readback_raw is not None
        else f"{location}.motion_verification"
    )
    motion_verification = (
        _parse_solenoid_centering_motion_verification(
            _expect_mapping(motion_raw, verification_location),
            verification_location,
        )
        if motion_raw is not None
        else None
    )
    quality_raw = _expect_mapping(
        preset.get("quality_gate", {}),
        f"{location}.quality_gate",
    )
    minimum_relative_score_improvement = float(
        quality_raw.get("minimum_relative_score_improvement", 0.05)
    )
    if not 0.0 <= minimum_relative_score_improvement < 1.0:
        raise MachineProfileError(
            f"{location}.quality_gate.minimum_relative_score_improvement must be in [0, 1)."
        )

    structured_scan_raw = preset.get("scan")
    legacy_scan_keys = ("solenoid_scan", "corrector_scan")
    if structured_scan_raw is not None and any(preset.get(key) is not None for key in legacy_scan_keys):
        raise MachineProfileError(
            f"{location} must not define both scan and legacy solenoid_scan/corrector_scan."
        )
    if structured_scan_raw is not None:
        structured_scan = _expect_mapping(structured_scan_raw, f"{location}.scan")
        solenoid_scan_raw = _expect_mapping(
            structured_scan.get("solenoid"),
            f"{location}.scan.solenoid",
        )
        corrector_scan_raw = _expect_mapping(
            structured_scan.get("corrector"),
            f"{location}.scan.corrector",
        )
        solenoid_scan = _parse_solenoid_centering_scan_range(
            solenoid_scan_raw,
            f"{location}.scan.solenoid",
            structured=True,
        )
        corrector_scan = _parse_solenoid_centering_scan_range(
            corrector_scan_raw,
            f"{location}.scan.corrector",
            structured=True,
        )
    else:
        solenoid_scan = _parse_solenoid_centering_scan_range(
            _expect_mapping(preset.get("solenoid_scan"), f"{location}.solenoid_scan"),
            f"{location}.solenoid_scan",
        )
        corrector_scan = _parse_solenoid_centering_scan_range(
            _expect_mapping(preset.get("corrector_scan"), f"{location}.corrector_scan"),
            f"{location}.corrector_scan",
        )

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
        solenoid_scan=solenoid_scan,
        corrector_scan=corrector_scan,
        samples_per_point=int(preset.get("samples_per_point")),
        settle_time_s=float(preset.get("settle_time_s")),
        sample_interval_s=float(preset.get("sample_interval_s")),
        max_rounds=int(max_iters),
        motion_verification=motion_verification,
        minimum_relative_score_improvement=minimum_relative_score_improvement,
    )


def _parse_solenoid_centering_motion_verification(
    raw_motion: Mapping[str, Any],
    location: str,
) -> SolenoidCenteringMotionVerification:
    values = {
        name: raw_motion.get(name)
        for name in (
            "solenoid_readback_tolerance",
            "corrector_readback_tolerance",
            "readback_timeout_s",
        )
    }
    missing = [name for name, value in values.items() if value is None]
    if missing:
        raise MachineProfileError(f"{location} is missing required field(s): {', '.join(missing)}.")
    solenoid_tolerance = float(values["solenoid_readback_tolerance"])
    corrector_tolerance = float(values["corrector_readback_tolerance"])
    timeout_s = float(values["readback_timeout_s"])
    poll_interval_s = float(raw_motion.get("poll_interval_s", 0.1))
    if solenoid_tolerance <= 0 or corrector_tolerance <= 0:
        raise MachineProfileError(f"{location} readback tolerances must be positive.")
    if timeout_s <= 0 or poll_interval_s <= 0:
        raise MachineProfileError(f"{location} timeout and poll interval must be positive.")
    return SolenoidCenteringMotionVerification(
        solenoid_readback_tolerance=solenoid_tolerance,
        corrector_readback_tolerance=corrector_tolerance,
        readback_timeout_s=timeout_s,
        poll_interval_s=poll_interval_s,
    )


def _merge_solenoid_centering_defaults(
    workflow: Mapping[str, Any],
    raw_preset: Any,
    location: str,
) -> dict[str, Any]:
    preset = dict(_expect_mapping(raw_preset, location))
    sampling = _expect_mapping(
        workflow.get("sampling", {}),
        "workflows.solenoid_centering.sampling",
    )
    for field in ("samples_per_point", "settle_time_s", "sample_interval_s"):
        if field not in preset and field in sampling:
            preset[field] = sampling[field]
    if "max_iters" not in preset and "max_rounds" not in preset and "max_iters" in workflow:
        preset["max_iters"] = workflow["max_iters"]
    for field in ("readback_verification", "quality_gate"):
        common = _expect_mapping(
            workflow.get(field, {}),
            f"workflows.solenoid_centering.{field}",
        )
        if common:
            local = _expect_mapping(preset.get(field, {}), f"{location}.{field}")
            preset[field] = {**common, **local}
    return preset


def _parse_solenoid_centering_scan_range(
    raw_scan: Mapping[str, Any],
    location: str,
    *,
    structured: bool = False,
) -> SolenoidCenteringScanRange:
    if structured:
        mode = _expect_non_empty_string(raw_scan.get("mode"), f"{location}.mode")
        unit = _expect_non_empty_string(raw_scan.get("unit"), f"{location}.unit")
        if mode != "relative":
            raise MachineProfileError(f"{location}.mode must be 'relative'.")
        if unit != "A":
            raise MachineProfileError(f"{location}.unit must be 'A'.")
        low_raw = raw_scan.get("low")
        high_raw = raw_scan.get("high")
    else:
        low_raw = raw_scan.get("relative_from")
        high_raw = raw_scan.get("relative_to")
    try:
        low = float(low_raw)
        high = float(high_raw)
        steps = int(raw_scan.get("steps"))
    except (TypeError, ValueError) as exc:
        raise MachineProfileError(f"{location} must define numeric scan bounds and steps.") from exc
    if not math.isfinite(low) or not math.isfinite(high) or low >= high:
        raise MachineProfileError(f"{location} scan bounds must be finite and low < high.")
    if steps < 2:
        raise MachineProfileError(f"{location}.steps must be at least 2.")
    return SolenoidCenteringScanRange(
        relative_from=low,
        relative_to=high,
        steps=steps,
    )


def _parse_bba_scan_config(
    raw_scan: Mapping[str, Any],
    control_backend: str,
    location: str,
) -> BBAScanConfig:
    structured_keys = {"corrector", "quadrupole", "sampling"}
    if structured_keys & set(raw_scan):
        legacy_keys = {
            "corr_from", "corr_end", "corr_steps",
            "quad_from", "quad_end", "quad_steps",
            "samples", "settle_time", "sample_interval",
        }
        mixed = sorted(legacy_keys & set(raw_scan))
        if mixed:
            raise MachineProfileError(
                f"{location} must not mix structured and legacy scan fields: "
                + ", ".join(mixed)
            )
        corrector = _select_bba_scan_range(
            raw_scan.get("corrector"),
            control_backend,
            f"{location}.corrector",
        )
        quadrupole = _select_bba_scan_range(
            raw_scan.get("quadrupole"),
            control_backend,
            f"{location}.quadrupole",
        )
        sampling = _expect_mapping(raw_scan.get("sampling"), f"{location}.sampling")
        return BBAScanConfig(
            corr_from=corrector["low"],
            corr_end=corrector["high"],
            corr_steps=corrector["steps"],
            quad_from=quadrupole["low"],
            quad_end=quadrupole["high"],
            quad_steps=quadrupole["steps"],
            samples=_required_positive_int(
                sampling.get("samples_per_point"),
                f"{location}.sampling.samples_per_point",
            ),
            settle_time=_required_nonnegative_float(
                sampling.get("settle_time_s"),
                f"{location}.sampling.settle_time_s",
            ),
            sample_interval=_required_nonnegative_float(
                sampling.get("sample_interval_s"),
                f"{location}.sampling.sample_interval_s",
            ),
            corr_unit=corrector["unit"],
            quad_unit=quadrupole["unit"],
            corr_mode=corrector["mode"],
            quad_mode=quadrupole["mode"],
        )
    return BBAScanConfig(
        corr_from=_optional_float(raw_scan, "corr_from"),
        corr_end=_optional_float(raw_scan, "corr_end"),
        corr_steps=_optional_int(raw_scan, "corr_steps"),
        quad_from=_optional_float(raw_scan, "quad_from"),
        quad_end=_optional_float(raw_scan, "quad_end"),
        quad_steps=_optional_int(raw_scan, "quad_steps"),
        samples=_optional_int(raw_scan, "samples"),
        settle_time=_optional_float(raw_scan, "settle_time"),
        sample_interval=_optional_float(raw_scan, "sample_interval"),
    )


def _select_bba_scan_range(
    raw_range: Any,
    control_backend: str,
    location: str,
) -> dict[str, Any]:
    configured = _expect_mapping(raw_range, location)
    if {"low", "high", "steps", "unit"} <= set(configured):
        selected = configured
        selected_location = location
    else:
        selected = _expect_mapping(
            configured.get(control_backend),
            f"{location}.{control_backend}",
        )
        selected_location = f"{location}.{control_backend}"

    low = _required_finite_float(selected.get("low"), f"{selected_location}.low")
    high = _required_finite_float(selected.get("high"), f"{selected_location}.high")
    if low >= high:
        raise MachineProfileError(
            f"{selected_location}.low must be less than {selected_location}.high."
        )
    steps = _required_positive_int(selected.get("steps"), f"{selected_location}.steps")
    unit = _expect_non_empty_string(selected.get("unit"), f"{selected_location}.unit")
    mode = _expect_non_empty_string(
        selected.get("mode", "absolute"),
        f"{selected_location}.mode",
    ).lower()
    if mode not in {"absolute", "relative"}:
        raise MachineProfileError(
            f"{selected_location}.mode must be 'absolute' or 'relative'."
        )
    return {"low": low, "high": high, "steps": steps, "unit": unit, "mode": mode}


def _required_finite_float(value: Any, location: str) -> float:
    try:
        selected = float(value)
    except (TypeError, ValueError) as exc:
        raise MachineProfileError(f"{location} must be numeric.") from exc
    if not math.isfinite(selected):
        raise MachineProfileError(f"{location} must be finite.")
    return selected


def _required_nonnegative_float(value: Any, location: str) -> float:
    selected = _required_finite_float(value, location)
    if selected < 0:
        raise MachineProfileError(f"{location} must be non-negative.")
    return selected


def _required_positive_int(value: Any, location: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise MachineProfileError(f"{location} must be a positive integer.")
    return value


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
    structured = "quadrupole" in raw_scan or "sampling" in raw_scan
    if structured:
        legacy_keys = {
            "k1_from", "k1_end", "k1_steps", "unit", "mode",
            "samples", "settle_time", "sample_interval",
        }
        mixed = sorted(legacy_keys & set(raw_scan))
        if mixed:
            raise MachineProfileError(
                "emit scan must not mix structured and legacy fields: " + ", ".join(mixed)
            )
        quadrupole = _expect_mapping(raw_scan.get("quadrupole"), "emit scan quadrupole")
        sampling = _expect_mapping(raw_scan.get("sampling"), "emit scan sampling")
        adaptive_raw = raw_scan.get("adaptive")
        mode = (_optional_string(quadrupole, "mode") or "absolute").lower()
        source = {
            "k1_from": quadrupole.get("low"),
            "k1_end": quadrupole.get("high"),
            "k1_steps": quadrupole.get("steps"),
            "unit": quadrupole.get("unit"),
            "samples": sampling.get("samples_per_point"),
            "settle_time": sampling.get("settle_time_s"),
            "sample_interval": sampling.get("sample_interval_s"),
        }
    else:
        adaptive_raw = raw_scan.get("adaptive")
        mode = (_optional_string(raw_scan, "mode") or "absolute").lower()
        source = raw_scan
    if mode not in {"absolute", "relative"}:
        raise MachineProfileError("emit scan mode must be 'absolute' or 'relative'.")
    return EmitScanConfig(
        k1_from=_optional_float(source, "k1_from"),
        k1_end=_optional_float(source, "k1_end"),
        k1_steps=_optional_int(source, "k1_steps"),
        samples=_optional_int(source, "samples"),
        settle_time=_optional_float(source, "settle_time"),
        sample_interval=_optional_float(source, "sample_interval"),
        unit=_optional_string(source, "unit") or "1/m^2",
        mode=mode,
        adaptive=(
            None
            if adaptive_raw is None
            else _parse_emit_adaptive_scan_config(
                _expect_mapping(adaptive_raw, "emit scan adaptive")
            )
        ),
    )


def _parse_emit_adaptive_scan_config(
    raw_adaptive: Mapping[str, Any],
) -> EmitAdaptiveScanConfig:
    structured = "low" in raw_adaptive or "high" in raw_adaptive
    if structured and ({"k1_min", "k1_max"} & set(raw_adaptive)):
        raise MachineProfileError(
            "emit scan adaptive must not mix low/high with k1_min/k1_max."
        )
    return EmitAdaptiveScanConfig(
        k1_min=_optional_float(raw_adaptive, "low" if structured else "k1_min"),
        k1_max=_optional_float(raw_adaptive, "high" if structured else "k1_max"),
        initial_points=_optional_int(raw_adaptive, "initial_points"),
        target_points_per_plane=_optional_int(raw_adaptive, "target_points_per_plane"),
        max_unique_points=_optional_int(raw_adaptive, "max_unique_points"),
        waist_size_squared_ratio=_optional_float(
            raw_adaptive,
            "waist_size_squared_ratio",
        ),
        reuse_tolerance=_optional_float(raw_adaptive, "reuse_tolerance"),
        max_retries=_optional_int(raw_adaptive, "max_retries"),
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


def _expect_finite_number(value: Any, location: str) -> float:
    if isinstance(value, bool):
        raise MachineProfileError(f"{location} must be a finite number.")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise MachineProfileError(f"{location} must be a finite number.") from exc
    if not math.isfinite(numeric):
        raise MachineProfileError(f"{location} must be a finite number.")
    return numeric
