from __future__ import annotations

from typing import Mapping

from .models import (
    AppContext,
    BBAPreset,
    ElementConfig,
    EmitPreset,
    MachineProfile,
    MachineProfileError,
    normalize_mode,
    normalize_plane,
)


_LOGICAL_CHANNEL_ALIASES = {
    "k1": ("K1",),
    "K1": ("k1",),
    "current_set": ("setpoint",),
    "setpoint": ("current_set",),
    "current_readback": ("readback",),
    "readback": ("current_readback",),
}

_CORRECTOR_WRITE_CHANNEL_BY_BACKEND = {
    "vm": "kick",
    "real": "current_set",
}

_BEND_WRITE_CHANNEL_BY_BACKEND = {
    "vm": "angle",
    "real": "current_set",
}


def resolve_channel(
    target: MachineProfile | AppContext,
    element_id: str,
    logical_channel: str,
    mode: str | None = None,
) -> str:
    if isinstance(target, AppContext):
        profile = target.profile
        fallback_mode = target.control_backend.name
    else:
        profile = target
        fallback_mode = profile.machine.default_mode

    element = profile.get_element(element_id)
    resolved_logical_channel = _resolve_logical_channel_name(element, logical_channel)
    try:
        channel_modes = element.channels[resolved_logical_channel]
    except KeyError as exc:
        raise MachineProfileError(
            f"Element {element_id} does not define logical channel {logical_channel!r}."
        ) from exc

    normalized_mode = normalize_mode(mode or fallback_mode, "mode")
    try:
        pv_name = channel_modes[normalized_mode]
    except KeyError as exc:
        raise MachineProfileError(
            f"Element {element_id} channel {resolved_logical_channel!r} is missing "
            f"{normalized_mode!r} mapping."
        ) from exc
    if not pv_name:
        raise MachineProfileError(
            f"Element {element_id} channel {resolved_logical_channel!r} has an empty "
            f"{normalized_mode!r} PV."
        )
    return pv_name


def resolve_corrector_write_channel(
    target: MachineProfile | AppContext,
    element_id: str,
    mode: str | None = None,
) -> str:
    if isinstance(target, AppContext):
        profile = target.profile
        fallback_mode = target.control_backend.name
    else:
        profile = target
        fallback_mode = profile.machine.default_mode

    normalized_mode = normalize_mode(mode or fallback_mode, "mode")
    preferred = _CORRECTOR_WRITE_CHANNEL_BY_BACKEND.get(normalized_mode, "current_set")
    for logical_channel in (preferred, "current_set", "kick", "setpoint"):
        try:
            return resolve_channel(profile, element_id, logical_channel, normalized_mode)
        except MachineProfileError:
            continue
    raise MachineProfileError(
        f"Element {element_id} does not define a writable corrector channel "
        f"for {normalized_mode!r} backend."
    )


def resolve_bend_write_channel(
    target: MachineProfile | AppContext,
    element_id: str,
    mode: str | None = None,
) -> str:
    if isinstance(target, AppContext):
        profile = target.profile
        fallback_mode = target.control_backend.name
    else:
        profile = target
        fallback_mode = profile.machine.default_mode

    normalized_mode = normalize_mode(mode or fallback_mode, "mode")
    preferred = _BEND_WRITE_CHANNEL_BY_BACKEND.get(normalized_mode, "current_set")
    for logical_channel in (preferred, "current_set", "angle"):
        try:
            return resolve_channel(profile, element_id, logical_channel, normalized_mode)
        except MachineProfileError:
            continue
    raise MachineProfileError(
        f"Element {element_id} does not define a writable bend channel "
        f"for {normalized_mode!r} backend."
    )


def _resolve_logical_channel_name(element: ElementConfig, logical_channel: str) -> str:
    if logical_channel in element.channels:
        return logical_channel
    for alias in _LOGICAL_CHANNEL_ALIASES.get(logical_channel, ()):
        if alias in element.channels:
            return alias
    return logical_channel


def list_elements(
    target: MachineProfile | AppContext,
    kind: str | None = None,
    role: str | None = None,
    plane: str | None = None,
    logical_channel: str | None = None,
) -> list[ElementConfig]:
    profile = target.profile if isinstance(target, AppContext) else target
    normalized_plane = normalize_plane(plane, "plane") if plane is not None else None
    elements = list(profile.elements)
    if kind is not None:
        elements = [element for element in elements if element.kind == kind]
    if role is not None:
        elements = [element for element in elements if role in element.roles]
    if normalized_plane is not None:
        elements = [element for element in elements if element.plane == normalized_plane]
    if logical_channel is not None:
        elements = [
            element
            for element in elements
            if _resolve_logical_channel_name(element, logical_channel) in element.channels
        ]
    return elements


def get_workflow(profile: MachineProfile, workflow_name: str) -> Mapping[str, object]:
    try:
        workflow = profile.workflows[workflow_name]
    except KeyError as exc:
        raise MachineProfileError(f"Workflow {workflow_name!r} is not defined.") from exc
    if not isinstance(workflow, Mapping):
        raise MachineProfileError(f"workflows.{workflow_name} must be a mapping.")
    return workflow


def get_bba_preset(ctx: AppContext, preset_id: str | None = None) -> BBAPreset:
    if ctx.bba_workflow is None:
        raise MachineProfileError("AppContext does not include a BBA workflow.")
    selected_id = preset_id or ctx.selected_preset_id or ctx.bba_workflow.standard.default_preset
    try:
        return ctx.bba_workflow.presets_by_id[selected_id]
    except KeyError as exc:
        raise MachineProfileError(f"Unknown BBA preset {selected_id!r}.") from exc


def get_emit_preset(ctx: AppContext, preset_id: str | None = None) -> EmitPreset:
    if ctx.emit_measure_workflow is None:
        raise MachineProfileError("AppContext does not include an emit_measure workflow.")
    selected_id = preset_id or ctx.selected_preset_id or ctx.emit_measure_workflow.default_preset
    try:
        return ctx.emit_measure_workflow.presets_by_id[selected_id]
    except KeyError as exc:
        raise MachineProfileError(f"Unknown emit_measure preset {selected_id!r}.") from exc
