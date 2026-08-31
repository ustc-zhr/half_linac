from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from half_linac.src.shared.machine_profile import (
    AppContext,
    MachineProfileError,
    load_app_context,
    require_workflow_write_allowed,
)


QUANTITIES = ("phase", "amplitude")


@dataclass(frozen=True)
class QuantityConfig:
    name: str
    label: str
    set_channel: str
    readback_channel: str
    setpoint_pv: str
    readback_pv: str
    low: float
    high: float
    unit: str
    default_step: float
    step_choices: tuple[float, ...]
    readback_tolerance: float


@dataclass(frozen=True)
class LlrfGroup:
    element_id: str
    display_name: str
    quantities: Mapping[str, QuantityConfig]


@dataclass(frozen=True)
class LlrfRuntime:
    context: AppContext
    groups: tuple[LlrfGroup, ...]
    default_element: str


def load_llrf_runtime() -> LlrfRuntime:
    context = load_app_context("llrf_control")
    require_workflow_write_allowed(context, "llrf_control", "LLRF setpoint write")
    workflow = context.profile.workflows.get("llrf_control")
    if not isinstance(workflow, Mapping):
        raise MachineProfileError("Missing llrf_control workflow configuration.")

    tag = str(workflow.get("element_tag", "")).strip()
    if not tag:
        raise MachineProfileError("llrf_control.element_tag must not be empty.")
    backend = context.control_backend.name
    groups = []
    for element in context.profile.elements:
        if tag not in element.tags:
            continue
        quantities = {
            name: _load_quantity(element, backend, workflow, name)
            for name in QUANTITIES
        }
        groups.append(LlrfGroup(element.id, element.display_name, quantities))
    if not groups:
        raise MachineProfileError("No LLRF elements are configured.")

    first_element = str(workflow.get("first_element", "")).strip()
    if first_element:
        first_matches = [group for group in groups if group.element_id == first_element]
        if not first_matches:
            raise MachineProfileError(
                "llrf_control.first_element must reference a configured LLRF."
            )
        groups = first_matches + [
            group for group in groups if group.element_id != first_element
        ]

    default_element = str(workflow.get("default_element", groups[0].element_id))
    if default_element not in {group.element_id for group in groups}:
        raise MachineProfileError(
            "llrf_control.default_element must reference a configured LLRF."
        )
    return LlrfRuntime(context, tuple(groups), default_element)


def _load_quantity(element, backend: str, workflow: Mapping[str, Any], name: str) -> QuantityConfig:
    set_channel = f"{name}_set"
    readback_channel = f"{name}_readback"
    try:
        setpoint_pv = element.channels[set_channel][backend]
        readback_pv = element.channels[readback_channel][backend]
    except KeyError as exc:
        raise MachineProfileError(
            f"{element.id} is missing {backend!r} {name} setpoint/readback channels."
        ) from exc

    raw_limit = element.limits_for(set_channel)
    try:
        low = _finite(raw_limit["low"], f"{element.id}.{set_channel}.low")
        high = _finite(raw_limit["high"], f"{element.id}.{set_channel}.high")
        unit = str(raw_limit["unit"]).strip()
    except KeyError as exc:
        raise MachineProfileError(
            f"{element.id}.{set_channel} requires low, high, and unit limits."
        ) from exc
    if low >= high or not unit:
        raise MachineProfileError(f"{element.id}.{set_channel} limits are invalid.")

    raw_quantity = workflow.get(name)
    if not isinstance(raw_quantity, Mapping):
        raise MachineProfileError(f"llrf_control.{name} must be a mapping.")
    default_step = _positive(raw_quantity.get("default_step"), f"llrf_control.{name}.default_step")
    raw_choices = raw_quantity.get("step_choices")
    if not isinstance(raw_choices, list) or not raw_choices:
        raise MachineProfileError(f"llrf_control.{name}.step_choices must be a non-empty list.")
    step_choices = tuple(
        _positive(value, f"llrf_control.{name}.step_choices[]")
        for value in raw_choices
    )
    readback_tolerance = _positive(
        raw_quantity.get("readback_tolerance"),
        f"llrf_control.{name}.readback_tolerance",
    )
    return QuantityConfig(
        name=name,
        label=name.title(),
        set_channel=set_channel,
        readback_channel=readback_channel,
        setpoint_pv=setpoint_pv,
        readback_pv=readback_pv,
        low=low,
        high=high,
        unit=unit,
        default_step=default_step,
        step_choices=step_choices,
        readback_tolerance=readback_tolerance,
    )


def _finite(value: object, location: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MachineProfileError(f"{location} must be numeric.") from exc
    if not math.isfinite(number):
        raise MachineProfileError(f"{location} must be finite.")
    return number


def _positive(value: object, location: str) -> float:
    number = _finite(value, location)
    if number <= 0:
        raise MachineProfileError(f"{location} must be positive.")
    return number
