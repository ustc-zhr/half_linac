"""Resolve the machine profile into an I/O-free application configuration."""

from __future__ import annotations

import copy
import hashlib
import math
from pathlib import Path
from typing import Any, Mapping

from half_linac.src.shared.energy_measurement import (
    BpmEnergySampler,
    build_bpm_transport_model,
    discover_bpm_transport_segment,
)
from half_linac.src.shared.machine_profile import (
    AppContext,
    MachineProfileError,
    resolve_channel,
    resolve_energy_feedback_workflow,
    workflow_writes_allowed,
)

from .measurement import BpmTransportEnergyProvider, PROVIDER_FACTORIES
from .models import ShadowActuator, ShadowControlGroup


APP_DIR = Path(__file__).resolve().parent
WORKFLOW_NAME = "energy_feedback"


def load_runtime_config(
    context: AppContext,
    *,
    discovery_backend: object | None = None,
) -> dict[str, Any]:
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

    default_source_id = str(
        workflow.get(
            "default_measurement_source",
            measurement.get("id", measurement["kind"]),
        )
    )
    default_measurement_id = str(measurement.get("id", measurement["kind"]))
    direct_signal_keys = ["energy", *diagnostic_keys]
    if target_key is not None:
        direct_signal_keys.append(target_key)
    if marker_key is not None:
        direct_signal_keys.append(marker_key)
    measurement_sources: dict[str, dict[str, Any]] = {
        default_measurement_id: {
            "id": default_measurement_id,
            "kind": str(measurement["kind"]),
            "label": str(measurement["label"]),
            "capabilities": tuple(
                str(capability) for capability in measurement["capabilities"]
            ),
            "valid_range_mev": tuple(
                float(value) for value in measurement["valid_range_mev"]
            ),
            "median_window": int(measurement.get("median_window", 1)),
            "diagnostic_keys": tuple(diagnostic_keys),
            "marker_key": marker_key,
            "target_key": target_key,
            "signal_keys": tuple(direct_signal_keys),
        }
    }

    for raw_source in workflow.get("measurement_alternatives", ()):
        source_id = str(raw_source["id"])
        bpm_ids = tuple(str(value) for value in raw_source["bpm_elements"])
        model_line, model_path, quadrupoles = discover_bpm_transport_segment(
            context,
            bpm_ids,
            backend=discovery_backend,
        )
        plane = str(raw_source["plane"])
        bpm_keys: dict[str, str] = {}
        source_signal_keys: list[str] = []
        for bpm_id in bpm_ids:
            key = f"source.{source_id}.bpm.{bpm_id}.{plane}"
            add_signal(
                key,
                {
                    "element": bpm_id,
                    "channel": plane,
                    "unit": "mm",
                },
            )
            bpm_keys[bpm_id] = key
            source_signal_keys.append(key)

        reference = raw_source["reference_energy"]
        reference_key = f"source.{source_id}.reference_energy"
        add_signal(reference_key, reference)
        source_signal_keys.append(reference_key)

        strength_channel = str(
            raw_source["model"]["quadrupole_strength_channel"]
        )
        k1_keys: dict[str, str] = {}
        for quadrupole in quadrupoles:
            key = f"source.{source_id}.k1.{quadrupole}"
            add_signal(
                key,
                {
                    "element": quadrupole,
                    "channel": strength_channel,
                    "unit": "1/m^2",
                },
            )
            k1_keys[quadrupole] = key
            source_signal_keys.append(key)

        measurement_sources[source_id] = {
            "id": source_id,
            "kind": "bpm_transport",
            "label": str(raw_source["label"]),
            "capabilities": tuple(
                str(capability) for capability in raw_source["capabilities"]
            ),
            "valid_range_mev": tuple(
                float(value) for value in raw_source["valid_range_mev"]
            ),
            "median_window": int(raw_source["median_window"]),
            "bpm_ids": bpm_ids,
            "bpm_keys": bpm_keys,
            "bpm_scale_to_mm": float(
                context.profile.machine.bpm_scale_to_mm(backend)
            ),
            "reference_key": reference_key,
            "k1_keys": k1_keys,
            "model_line": model_line,
            "model_path": model_path,
            "quadrupoles": quadrupoles,
            "offsets_mm": tuple(
                float(raw_source["offset_mm"][bpm_id]) for bpm_id in bpm_ids
            ),
            "runtime": copy.deepcopy(dict(raw_source["runtime"])),
            "model": copy.deepcopy(dict(raw_source["model"])),
            "signal_keys": tuple(source_signal_keys),
            "diagnostic_keys": (),
            "marker_key": None,
            "target_key": target_key,
        }

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

    config = {
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
        "measurement_sources": measurement_sources,
    }
    activate_measurement_source(config, default_source_id)
    return config


def activate_measurement_source(config: dict[str, Any], source_id: str) -> None:
    try:
        source = config["measurement_sources"][str(source_id)]
    except KeyError as exc:
        raise MachineProfileError(
            f"Unknown Energy Feedback measurement source {source_id!r}."
        ) from exc
    config["measurement_source_id"] = str(source["id"])
    config["measurement_kind"] = str(source["kind"])
    config["measurement_label"] = str(source["label"])
    config["measurement_capabilities"] = tuple(source["capabilities"])
    config["valid_range_mev"] = tuple(source["valid_range_mev"])
    config["median_window"] = int(source["median_window"])
    config["diagnostic_keys"] = tuple(source.get("diagnostic_keys", ()))
    config["marker_key"] = source.get("marker_key")
    config["target_key"] = source.get("target_key")


def build_measurement_provider(
    config: Mapping[str, Any], client, *, context: AppContext | None = None
):
    kind = str(config["measurement_kind"])
    if kind == "bpm_transport":
        if context is None:
            raise MachineProfileError(
                "BPM transport measurement requires an application context."
            )
        source = config["measurement_sources"][config["measurement_source_id"]]
        startup_keys = (source["reference_key"], *source["k1_keys"].values())
        startup_readings = client.read_many(startup_keys)
        reference_energy = _required_finite_reading(
            startup_readings, source["reference_key"], "LINAC_ENERGY"
        )
        low, high = source["valid_range_mev"]
        if not float(low) <= reference_energy <= float(high):
            raise MachineProfileError(
                f"LINAC_ENERGY {reference_energy:.6g} MeV is outside "
                f"[{float(low):.6g}, {float(high):.6g}] MeV."
            )
        startup_k1 = {
            quadrupole: _required_finite_reading(
                startup_readings, key, f"{quadrupole} K1_total"
            )
            for quadrupole, key in source["k1_keys"].items()
        }
        transport_model = build_bpm_transport_model(
            context, source, reference_energy, startup_k1
        )
        sampler = BpmEnergySampler(client, source, transport_model, startup_k1)
        runtime_metadata = {
            "source_id": source["id"],
            "model_line": source["model_line"],
            "model_path": list(source["model_path"]),
            "quadrupoles": list(source["quadrupoles"]),
            "quadrupole_strength_channel": source["model"][
                "quadrupole_strength_channel"
            ],
            "startup_reference_energy_mev": reference_energy,
            "startup_k1_values_per_m2": startup_k1,
            "transport_model": transport_model.as_metadata(),
            "model_backend": (
                {
                    "name": context.model_backend.name,
                    "engine": context.model_backend.engine,
                    "config": copy.deepcopy(dict(context.model_backend.config)),
                    "input_files": _model_input_files(context.model_backend.config),
                }
                if context.model_backend is not None
                else None
            ),
            "bend_assumption": source["model"]["bend_assumption"],
            "resolved_signals": {
                key: config["signals"][key] for key in source["signal_keys"]
            },
            "thresholds": {
                **dict(source["runtime"]),
                **dict(source["model"]),
                "valid_range_mev": list(source["valid_range_mev"]),
                "median_window": source["median_window"],
            },
        }
        return BpmTransportEnergyProvider(
            sampler, runtime_metadata=runtime_metadata
        )
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


def _required_finite_reading(
    readings: Mapping[str, object], key: str, label: str
) -> float:
    reading = readings.get(key)
    if reading is None or not bool(getattr(reading, "connected", False)):
        raise MachineProfileError(f"{label} PV is disconnected.")
    try:
        value = float(getattr(reading, "value", None))
    except (TypeError, ValueError):
        raise MachineProfileError(f"{label} did not return a numeric value.") from None
    if not math.isfinite(value):
        raise MachineProfileError(f"{label} returned a non-finite value.")
    return value


def _model_input_files(config: Mapping[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    repo_root = APP_DIR.parents[2]
    for key in ("source_lattice", "optics_ini_ele"):
        configured = config.get(key)
        if not isinstance(configured, str) or not configured.strip():
            continue
        path = Path(configured)
        resolved = path if path.is_absolute() else repo_root / path
        entry: dict[str, object] = {
            "configured_path": configured,
            "resolved_path": str(resolved.resolve()),
        }
        if resolved.is_file():
            digest = hashlib.sha256()
            with resolved.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            entry["sha256"] = digest.hexdigest()
        result[key] = entry
    return result
