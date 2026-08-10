from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .limits import LimitRange
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

_DEFAULT_WRITE_CHANNEL_BY_KIND_BACKEND = {
    ("corr", "vm"): "kick",
    ("corr", "real"): "current_set",
    ("bend", "vm"): "angle",
    ("bend", "real"): "current_set",
    ("solenoid", "vm"): "current_set",
    ("solenoid", "real"): "current_set",
}

_WRITE_CHANNEL_BY_KIND_QUANTITY = {
    ("corr", "current"): "current_set",
    ("corr", "kick"): "kick",
    ("bend", "current"): "current_set",
    ("bend", "angle"): "angle",
    ("quad", "current"): "current_set",
    ("quad", "k1"): "K1",
    ("solenoid", "current"): "current_set",
}

_UNIT_BY_WRITE_CHANNEL = {
    "current_set": "A",
    "kick": "rad",
    "angle": "rad",
    "K1": "1/m^2",
    "k1": "1/m^2",
}


@dataclass(frozen=True)
class WriteTarget:
    element_id: str
    element_kind: str
    control_backend: str
    logical_channel: str
    pv_name: str
    unit: str | None
    machine_limit: LimitRange


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
    profile = target.profile if isinstance(target, AppContext) else target
    element = profile.get_element(element_id)
    if element.kind != "corr":
        raise MachineProfileError(f"Element {element_id} is not a corrector.")
    return resolve_write_target(target, element_id, mode=mode).pv_name


def resolve_bend_write_channel(
    target: MachineProfile | AppContext,
    element_id: str,
    mode: str | None = None,
) -> str:
    profile = target.profile if isinstance(target, AppContext) else target
    element = profile.get_element(element_id)
    if element.kind != "bend":
        raise MachineProfileError(f"Element {element_id} is not a bend.")
    return resolve_write_target(target, element_id, mode=mode).pv_name


def resolve_write_target(
    target: MachineProfile | AppContext,
    element_id: str,
    *,
    quantity: str | None = None,
    logical_channel: str | None = None,
    unit: str | None = None,
    mode: str | None = None,
) -> WriteTarget:
    """Resolve one writable PV and the physical limit for the same logical channel."""
    if quantity is not None and logical_channel is not None:
        raise MachineProfileError("Specify quantity or logical_channel, not both.")

    if isinstance(target, AppContext):
        profile = target.profile
        fallback_mode = target.control_backend.name
    else:
        profile = target
        fallback_mode = profile.machine.default_mode

    element = profile.get_element(element_id)
    backend = normalize_mode(mode or fallback_mode, "mode")
    requested_channel: str
    if quantity is not None:
        quantity_key = str(quantity).strip().casefold()
        try:
            requested_channel = _WRITE_CHANNEL_BY_KIND_QUANTITY[(element.kind, quantity_key)]
        except KeyError as exc:
            raise MachineProfileError(
                f"Element {element_id} kind {element.kind!r} does not support writable "
                f"quantity {quantity!r}."
            ) from exc
    elif logical_channel is not None:
        requested_channel = str(logical_channel).strip()
        if not requested_channel:
            raise MachineProfileError("logical_channel must not be empty.")
    else:
        try:
            requested_channel = _DEFAULT_WRITE_CHANNEL_BY_KIND_BACKEND[
                (element.kind, backend)
            ]
        except KeyError as exc:
            if element.kind == "quad":
                raise MachineProfileError(
                    f"Quadrupole {element_id} requires quantity='K1' or quantity='current'."
                ) from exc
            raise MachineProfileError(
                f"Element {element_id} kind {element.kind!r} has no unambiguous writable "
                f"channel for {backend!r} backend."
            ) from exc

    resolved_channel = _resolve_write_logical_channel_name(element, requested_channel)
    try:
        channel_modes = element.channels[resolved_channel]
        pv_name = channel_modes[backend]
    except KeyError as exc:
        raise MachineProfileError(
            f"Element {element_id} writable channel {requested_channel!r} is missing "
            f"{backend!r} mapping."
        ) from exc
    if not pv_name:
        raise MachineProfileError(
            f"Element {element_id} channel {resolved_channel!r} has an empty "
            f"{backend!r} PV."
        )

    canonical_unit = _UNIT_BY_WRITE_CHANNEL.get(requested_channel)
    requested_unit = str(unit).strip() if unit is not None else None
    if requested_unit == "":
        requested_unit = None
    if canonical_unit is not None and requested_unit is not None:
        _require_compatible_units(
            requested_unit,
            canonical_unit,
            f"{element_id}.{resolved_channel}",
        )
    resolved_unit = requested_unit or canonical_unit

    raw_limit = element.limits_for(resolved_channel)
    machine_limit = LimitRange()
    if raw_limit:
        machine_limit = LimitRange.from_mapping(raw_limit)
        if resolved_unit is not None and machine_limit.unit is not None:
            _require_compatible_units(
                resolved_unit,
                machine_limit.unit,
                f"{element_id}.{resolved_channel} limit",
            )
        if machine_limit.unit is None and resolved_unit is not None:
            machine_limit = LimitRange(machine_limit.low, machine_limit.high, resolved_unit)
        elif resolved_unit is None:
            resolved_unit = machine_limit.unit

    return WriteTarget(
        element_id=element.id,
        element_kind=element.kind,
        control_backend=backend,
        logical_channel=resolved_channel,
        pv_name=pv_name,
        unit=resolved_unit,
        machine_limit=machine_limit,
    )


def _resolve_write_logical_channel_name(
    element: ElementConfig,
    logical_channel: str,
) -> str:
    if logical_channel in element.channels:
        return logical_channel
    if logical_channel in {"K1", "k1"}:
        alias = "k1" if logical_channel == "K1" else "K1"
        if alias in element.channels:
            return alias
    if element.kind in {"corr", "quad", "bend", "solenoid"}:
        alias = {"current_set": "setpoint", "setpoint": "current_set"}.get(
            logical_channel
        )
        if alias in element.channels:
            return alias
    raise MachineProfileError(
        f"Element {element.id} does not define writable logical channel "
        f"{logical_channel!r}."
    )


def _require_compatible_units(first: str, second: str, location: str) -> None:
    if first.casefold() != second.casefold():
        raise MachineProfileError(
            f"Unit mismatch for {location}: {first!r} does not match {second!r}."
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
    control_backend: str | None = None,
) -> list[ElementConfig]:
    profile = target.profile if isinstance(target, AppContext) else target
    normalized_plane = normalize_plane(plane, "plane") if plane is not None else None
    normalized_backend = (
        normalize_mode(control_backend, "control_backend")
        if control_backend is not None
        else None
    )
    if normalized_backend is not None and logical_channel is None:
        raise MachineProfileError(
            "list_elements control_backend filtering requires logical_channel."
        )
    elements = list(profile.elements)
    if kind is not None:
        elements = [element for element in elements if element.kind == kind]
    if role is not None:
        elements = [element for element in elements if role in element.roles]
    if normalized_plane is not None:
        elements = [element for element in elements if element.plane == normalized_plane]
    if logical_channel is not None:
        filtered_elements = []
        for element in elements:
            resolved_channel = _resolve_logical_channel_name(element, logical_channel)
            channel_modes = element.channels.get(resolved_channel)
            if channel_modes is None:
                continue
            if normalized_backend is not None and normalized_backend not in channel_modes:
                continue
            filtered_elements.append(element)
        elements = filtered_elements
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
    selected_id = preset_id or ctx.selected_preset_id or ctx.bba_workflow.bba1.default_preset
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
