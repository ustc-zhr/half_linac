"""Resolve the machine profile into an I/O-free application configuration."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

from half_linac.src.shared.machine_profile import (
    AppContext,
    MachineProfileError,
    resolve_channel,
    resolve_energy_feedback_workflow,
    workflow_writes_allowed,
)

from .measurement import PROVIDER_FACTORIES
from .models import ShadowActuator, ShadowControlGroup


APP_DIR = Path(__file__).resolve().parent
WORKFLOW_NAME = "energy_feedback"


def load_runtime_config(context: AppContext) -> dict[str, Any]:
    workflow = resolve_energy_feedback_workflow(context.profile)
    backend = context.control_backend.name
    if backend not in workflow["control_backends"]:
        raise MachineProfileError(
            f"Energy feedback does not support backend {backend!r}."
        )

    pvs: dict[str, str] = {}
    signals: dict[str, dict[str, str]] = {}

    def add_signal(key: str, signal: Mapping[str, Any]) -> str:
        element_id = str(signal["element"])
        channel = str(signal["channel"])
        pv_name = resolve_channel(context, element_id, channel)
        pvs[key] = pv_name
        signals[key] = {
            "pv": pv_name,
            "element": element_id,
            "channel": channel,
            "unit": str(signal.get("unit", "")),
        }
        return key

    measurement = workflow["measurement"]
    add_signal("energy", measurement["energy"])
    target_key = None
    if workflow.get("target") is not None:
        target_key = "target"
        add_signal(target_key, workflow["target"])
    diagnostic_keys: list[str] = []
    for name, signal in measurement.get("diagnostics", {}).items():
        key = f"diagnostic.{name}"
        diagnostic_keys.append(key)
        add_signal(key, signal)

    marker_key = None
    marker = workflow["pulse"].get("marker")
    if marker is not None:
        marker_key = "pulse.marker"
        add_signal(marker_key, marker)

    groups: list[ShadowControlGroup] = []
    group_readback_keys: dict[str, dict[str, str]] = {}
    write_verification = dict(workflow.get("write_verification", {}))
    command_precision = int(write_verification.get("command_precision", 2))
    for raw_group in workflow["control_groups"]:
        actuators: list[ShadowActuator] = []
        readback_keys: dict[str, str] = {}
        for raw_actuator in raw_group["actuators"]:
            actuator_id = str(raw_actuator["id"])
            element_id = str(raw_actuator["element"])
            setpoint_channel = str(raw_actuator["setpoint"])
            readback_channel = str(raw_actuator["readback"])
            setpoint_pv = resolve_channel(context, element_id, setpoint_channel)
            readback_key = f"actuator.{raw_group['id']}.{actuator_id}.readback"
            setpoint_key = f"actuator.{raw_group['id']}.{actuator_id}.setpoint"
            add_signal(
                readback_key,
                {"element": element_id, "channel": readback_channel, "unit": raw_actuator["unit"]},
            )
            pvs[setpoint_key] = setpoint_pv
            signals[setpoint_key] = {
                "pv": setpoint_pv,
                "element": element_id,
                "channel": setpoint_channel,
                "unit": str(raw_actuator["unit"]),
            }
            readback_keys[actuator_id] = readback_key
            machine_limits = context.profile.get_element(element_id).limits_for(
                setpoint_channel
            )
            actuators.append(
                ShadowActuator(
                    id=actuator_id,
                    label=str(raw_actuator.get("label", actuator_id)),
                    coefficient=float(raw_actuator["coefficient"]),
                    unit=str(raw_actuator["unit"]),
                    max_step=float(raw_actuator["max_step"]),
                    max_session_delta=float(raw_actuator["max_session_delta"]),
                    operating_range=tuple(
                        float(value) for value in raw_actuator["operating_range"]
                    ),
                    machine_limit=(
                        float(machine_limits["low"]),
                        float(machine_limits["high"]),
                    ),
                    setpoint_key=setpoint_key,
                    readback_key=readback_key,
                    verify_tolerance=float(raw_actuator["verify_tolerance"]),
                    command_precision=command_precision,
                    cyclic_period=(
                        float(raw_actuator["cyclic_period"])
                        if raw_actuator.get("cyclic_period") is not None
                        else None
                    ),
                )
            )
        group_id = str(raw_group["id"])
        groups.append(
            ShadowControlGroup(
                id=group_id,
                label=str(raw_group.get("label", group_id)),
                response_mev_per_knob_unit=float(
                    raw_group["response_mev_per_knob_unit"]
                ),
                loop_gain=float(raw_group["loop_gain"]),
                max_knob_step=float(raw_group["max_knob_step"]),
                deadband_mev=float(raw_group.get("deadband_mev", 0)),
                action_window_mev=float(raw_group["action_window_mev"]),
                actuators=tuple(actuators),
            )
        )
        group_readback_keys[group_id] = readback_keys

    return {
        "machine_id": context.profile.machine.id,
        "machine_display_name": context.profile.machine.display_name,
        "backend": backend,
        "workflow": copy.deepcopy(workflow),
        "pvs": pvs,
        "signals": signals,
        "measurement_kind": str(measurement["kind"]),
        "measurement_label": str(measurement["label"]),
        "measurement_capabilities": tuple(
            str(capability) for capability in measurement["capabilities"]
        ),
        "valid_range_mev": tuple(
            float(value) for value in measurement["valid_range_mev"]
        ),
        "median_window": int(measurement.get("median_window", 1)),
        "diagnostic_keys": tuple(diagnostic_keys),
        "marker_key": marker_key,
        "target_key": target_key,
        "require_marker_for_write": bool(
            workflow["pulse"]["require_marker_for_write"]
        ),
        "write_enabled": workflow_writes_allowed(
            context, WORKFLOW_NAME, mode=backend
        ),
        "control_groups": tuple(groups),
        "group_readback_keys": group_readback_keys,
        "write_verification": write_verification,
        "operator_controls": copy.deepcopy(
            dict(workflow.get("operator_controls", {}))
        ),
        "runtime": copy.deepcopy(dict(workflow["runtime"])),
        "logging": copy.deepcopy(dict(workflow["logging"])),
    }


def build_measurement_provider(config: Mapping[str, Any], client):
    kind = str(config["measurement_kind"])
    factory = PROVIDER_FACTORIES.get(kind)
    if factory is None:
        raise MachineProfileError(f"Unknown measurement provider {kind!r}.")
    return factory(
        client,
        valid_range_mev=tuple(config["valid_range_mev"]),
        stale_timeout_s=float(config["runtime"]["stale_timeout_s"]),
        diagnostic_keys=tuple(config["diagnostic_keys"]),
        marker_key=config["marker_key"],
        median_window=int(config["median_window"]),
        target_key=config.get("target_key"),
    )
