from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from half_linac.src.shared.machine_profile import (
    AppContext,
    MachineProfileError,
    load_app_context,
)


@dataclass(frozen=True)
class HvModulator:
    name: str
    voltage_set: str
    voltage_readback: str
    enable_1: str
    enable_2: str
    enable_1_state: str
    enable_2_state: str
    low: float
    high: float
    unit: str


@dataclass(frozen=True)
class HvRuntime:
    context: AppContext
    modulators: tuple[HvModulator, ...]
    low: float
    high: float
    unit: str


def load_hv_runtime() -> HvRuntime:
    context = load_app_context("hv_control")
    workflow = context.profile.workflows.get("hv_control")
    if not isinstance(workflow, Mapping):
        raise MachineProfileError("Missing hv_control workflow configuration.")
    tag = str(workflow.get("element_tag", "hvdc")).strip()
    if not tag:
        raise MachineProfileError("hv_control.element_tag must not be empty.")

    elements = tuple(
        element for element in context.profile.elements
        if element.kind == "modulator" and tag in element.tags
    )
    if not elements:
        raise MachineProfileError(
            f"hv_control requires at least one modulator element tagged {tag!r}."
        )

    backend = context.control_backend.name
    channel_names = (
        "voltage_set",
        "voltage_readback",
        "modulator_enable_set",
        "modulator_enable_readback",
        "hv_enable_set",
        "hv_enable_readback",
    )
    modulators = []
    ranges = []
    for element in elements:
        limits = element.limits_for("voltage_set")
        try:
            low = float(limits["low"])
            high = float(limits["high"])
        except (KeyError, TypeError, ValueError) as exc:
            raise MachineProfileError(
                f"{element.id}.limits.voltage_set must define numeric low/high values."
            ) from exc
        unit = str(limits.get("unit", "V")).strip() or "V"
        if low >= high:
            raise MachineProfileError(
                f"{element.id}.limits.voltage_set.low must be less than high."
            )
        ranges.append((low, high, unit))
        channels = {
            key: element.channels.get(key, {}).get(backend, "")
            for key in channel_names
        }
        modulators.append(HvModulator(
            name=element.id,
            voltage_set=channels["voltage_set"],
            voltage_readback=channels["voltage_readback"],
            enable_1=channels["modulator_enable_set"],
            enable_2=channels["hv_enable_set"],
            enable_1_state=channels["modulator_enable_readback"],
            enable_2_state=channels["hv_enable_readback"],
            low=low,
            high=high,
            unit=unit,
        ))

    units = {unit for _, _, unit in ranges}
    if len(units) != 1:
        raise MachineProfileError("HV modulators must use one common voltage unit.")
    low = max(item[0] for item in ranges)
    high = min(item[1] for item in ranges)
    if low >= high:
        raise MachineProfileError("HV modulator voltage limits have no common range.")
    return HvRuntime(context, tuple(modulators), low, high, ranges[0][2])
