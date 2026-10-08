from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

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
    limits = workflow.get("voltage")
    if not isinstance(limits, Mapping):
        raise MachineProfileError("hv_control.voltage must be a mapping.")
    try:
        low, high = float(limits["low"]), float(limits["high"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MachineProfileError("hv_control.voltage requires numeric low/high values.") from exc
    unit = str(limits.get("unit", "V")).strip() or "V"
    if low >= high:
        raise MachineProfileError("hv_control.voltage.low must be less than high.")
    raw_modulators = workflow.get("modulators")
    if not isinstance(raw_modulators, list) or not raw_modulators:
        raise MachineProfileError("hv_control.modulators must be a non-empty list.")
    backend = context.control_backend.name
    modulators = []
    seen = set()
    for index, raw in enumerate(raw_modulators):
        if not isinstance(raw, Mapping):
            raise MachineProfileError(f"hv_control.modulators[{index}] must be a mapping.")
        name = str(raw.get("name", "")).strip()
        if not name or name in seen:
            raise MachineProfileError(f"Invalid or duplicate modulator name at index {index}.")
        seen.add(name)
        pvs = raw.get("pvs")
        if not isinstance(pvs, Mapping):
            raise MachineProfileError(f"hv_control.modulators[{index}].pvs must be a mapping.")
        selected = pvs.get(backend) or pvs.get("real")
        if not isinstance(selected, Mapping):
            raise MachineProfileError(f"{name} has no PV mapping for backend {backend!r}.")
        values = {key: str(selected.get(key, "")).strip() for key in ("voltage_set", "voltage_readback", "enable_1", "enable_2", "enable_1_state", "enable_2_state")}
        modulators.append(HvModulator(name, **values))
    return HvRuntime(context, tuple(modulators), low, high, unit)
