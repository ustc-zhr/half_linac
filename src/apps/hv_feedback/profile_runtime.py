from __future__ import annotations

import copy
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from half_linac.src.shared.machine_profile import (
    AppContext,
    LimitRange,
    MachineProfileError,
    effective_limit,
    make_runtime_run_id,
    new_app_run_dir,
    require_workflow_write_allowed,
    resolve_app_runtime_paths,
    resolve_channel,
    resolve_hv_feedback_workflow,
    resolve_write_target,
)


APP_DIR = Path(__file__).resolve().parent
WORKFLOW_NAME = "hv_feedback"
SNAPSHOT_SECTIONS = ("control", "reference", "safety")


def amplitude_key(channel_id: str) -> str:
    return f"rf.{channel_id}.amplitude"


def phase_key(channel_id: str) -> str:
    return f"rf.{channel_id}.phase"


def required_signal_keys(config: Mapping[str, Any]) -> tuple[str, ...]:
    keys = ["hv_setpoint", "hv_readback"]
    for channel in config["rf_channels"]:
        channel_id = str(channel["id"])
        keys.extend((amplitude_key(channel_id), phase_key(channel_id)))
    return tuple(keys)


def assert_hv_feedback_runtime(context: AppContext) -> None:
    workflow = resolve_hv_feedback_workflow(context.profile)
    supported = tuple(str(value).strip().lower() for value in workflow["control_backends"])
    if context.profile.machine.id != "irfel" or context.control_backend.name != "real":
        raise MachineProfileError(
            "HV Feedback is available only for the IRFEL real-machine backend."
        )
    if "real" not in supported:
        raise MachineProfileError(
            "The hv_feedback workflow does not allow the real-machine backend."
        )


def _resolve_signal(context: AppContext, signal: Mapping[str, Any]) -> dict[str, str]:
    element_id = str(signal["element"])
    logical_channel = str(signal["channel"])
    return {
        "name": resolve_channel(context, element_id, logical_channel),
        "unit": str(signal.get("unit", "")),
        "element": element_id,
        "channel": logical_channel,
    }


def _resolve_write_signal(
    context: AppContext,
    signal: Mapping[str, Any],
) -> tuple[dict[str, str], LimitRange]:
    element_id = str(signal["element"])
    logical_channel = str(signal["channel"])
    unit = str(signal.get("unit", "")).strip() or None
    target = resolve_write_target(
        context,
        element_id,
        logical_channel=logical_channel,
        unit=unit,
    )
    return (
        {
            "name": target.pv_name,
            "unit": target.unit or "",
            "element": target.element_id,
            "channel": target.logical_channel,
        },
        target.machine_limit,
    )


def load_profile_config(context: AppContext) -> dict[str, Any]:
    """Resolve all configured units without connecting to any EPICS PV."""
    assert_hv_feedback_runtime(context)
    workflow = resolve_hv_feedback_workflow(context.profile)
    units: dict[str, dict[str, Any]] = {}
    unit_order: list[str] = []
    write_targets: dict[str, str] = {}
    for raw_unit in workflow["feedback_units"]:
        unit_id = str(raw_unit["id"])
        unit_order.append(unit_id)
        hv_setpoint, machine_hv_limit = _resolve_write_signal(
            context,
            raw_unit["hv"]["setpoint"],
        )
        pvs = {
            "hv_setpoint": hv_setpoint,
            "hv_readback": _resolve_signal(context, raw_unit["hv"]["readback"]),
        }
        channels: list[dict[str, str]] = []
        for raw_channel in raw_unit["rf_channels"]:
            channel_id = str(raw_channel["id"])
            channels.append({"id": channel_id, "label": str(raw_channel["label"])})
            pvs[amplitude_key(channel_id)] = _resolve_signal(
                context, raw_channel["amplitude"]
            )
            pvs[phase_key(channel_id)] = _resolve_signal(context, raw_channel["phase"])

        target_name = pvs["hv_setpoint"]["name"]
        duplicate = write_targets.get(target_name)
        if duplicate is not None:
            raise MachineProfileError(
                f"HV feedback units {duplicate!r} and {unit_id!r} resolve to the same "
                f"write PV {target_name!r}."
            )
        write_targets[target_name] = unit_id
        config = {
            "feedback_unit_id": unit_id,
            "feedback_unit_label": str(raw_unit["label"]),
            "default_feedback_channel": str(raw_unit["default_feedback_channel"]),
            "rf_channels": channels,
            "pvs": pvs,
            "control": copy.deepcopy(dict(raw_unit["control"])),
            "reference": copy.deepcopy(dict(raw_unit["reference"])),
            "safety": copy.deepcopy(dict(raw_unit["safety"])),
            "logging": copy.deepcopy(dict(raw_unit["logging"])),
            "_machine_hv_limit": {
                key: value
                for key, value in {
                    "low": machine_hv_limit.low,
                    "high": machine_hv_limit.high,
                    "unit": machine_hv_limit.unit,
                }.items()
                if value is not None
            },
        }
        config = apply_machine_hv_limit(config)
        validate_session_config(config)
        units[unit_id] = config
    return {"unit_order": unit_order, "units": units}


def get_unit_config(profile_config: Mapping[str, Any], unit_id: str) -> dict[str, Any]:
    try:
        return copy.deepcopy(profile_config["units"][unit_id])
    except KeyError as exc:
        raise ValueError(f"Unknown HV feedback unit: {unit_id}") from exc


def _workflow_unit(context: AppContext, unit_id: str) -> Mapping[str, Any]:
    workflow = resolve_hv_feedback_workflow(context.profile)
    for unit in workflow["feedback_units"]:
        if str(unit["id"]) == unit_id:
            return unit
    raise MachineProfileError(f"HV feedback unit {unit_id!r} is not in the active profile.")


def require_confirmed_feedback_write(
    context: AppContext,
    *,
    session_confirmed: bool,
    feedback_unit_id: str,
    target_pv: str,
) -> None:
    require_feedback_write_policy(context)
    if not session_confirmed:
        raise MachineProfileError(
            "This feedback session has not been explicitly confirmed by the operator."
        )
    unit = _workflow_unit(context, feedback_unit_id)
    hv_signal = unit["hv"]["setpoint"]
    expected_pv = resolve_write_target(
        context,
        str(hv_signal["element"]),
        logical_channel=str(hv_signal["channel"]),
        unit=str(hv_signal.get("unit", "")).strip() or None,
    ).pv_name
    if str(target_pv) != expected_pv:
        raise MachineProfileError(
            f"HV write target changed for unit {feedback_unit_id!r}: "
            f"expected {expected_pv!r}, got {target_pv!r}."
        )


def require_feedback_write_policy(context: AppContext) -> None:
    assert_hv_feedback_runtime(context)
    require_workflow_write_allowed(
        context,
        WORKFLOW_NAME,
        "HV feedback setpoint write",
    )


def resolve_hv_feedback_runtime_paths(
    context: AppContext,
    feedback_unit_id: str | None = None,
) -> dict[str, Path]:
    base = resolve_app_runtime_paths(APP_DIR, context)
    snapshots_root = base["runtime_dir"] / "snapshots"
    return {
        **base,
        "snapshots_root": snapshots_root,
        "snapshots_dir": (
            snapshots_root / feedback_unit_id
            if feedback_unit_id is not None
            else snapshots_root
        ),
    }


def new_run_dir(context: AppContext, operation: str, feedback_unit_id: str) -> Path:
    run_dir = new_app_run_dir(APP_DIR, context, kind=f"{feedback_unit_id}_{operation}")
    candidate = run_dir
    sequence = 1
    while candidate.exists():
        candidate = run_dir.with_name(f"{run_dir.name}_{sequence:02d}")
        sequence += 1
    candidate.mkdir(parents=True, exist_ok=False)
    return candidate


def write_run_metadata(
    context: AppContext,
    run_dir: Path,
    *,
    operation: str,
    config: Mapping[str, Any],
    feedback_channel_id: str,
    state: str,
    log_path: Path | None = None,
    detail: str = "",
) -> Path:
    payload = {
        "schema_version": 2,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "machine": context.profile.machine.id,
        "control_backend": context.control_backend.name,
        "feedback_unit_id": config["feedback_unit_id"],
        "feedback_channel_id": feedback_channel_id,
        "operation": operation,
        "state": state,
        "detail": detail,
        "log_path": str(log_path) if log_path is not None else None,
        "signals": copy.deepcopy(config["pvs"]),
        "rf_channels": copy.deepcopy(config["rf_channels"]),
        "parameters": snapshot_payload(config),
    }
    metadata_path = run_dir / "metadata.json"
    _write_json(metadata_path, payload)
    latest_path = resolve_hv_feedback_runtime_paths(context)["latest_metadata_path"]
    _write_json(latest_path, payload)
    return metadata_path


def snapshot_payload(config: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "feedback_unit_id": str(config["feedback_unit_id"]),
        **{
            section: copy.deepcopy(dict(config[section]))
            for section in SNAPSHOT_SECTIONS
        },
    }


def save_runtime_snapshot(
    context: AppContext,
    config: Mapping[str, Any],
) -> Path:
    config = apply_machine_hv_limit(config)
    validate_session_config(config)
    unit_id = str(config["feedback_unit_id"])
    snapshots_dir = resolve_hv_feedback_runtime_paths(context, unit_id)["snapshots_dir"]
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    path = snapshots_dir / f"{make_runtime_run_id('parameters')}.json"
    _write_json(path, snapshot_payload(config))
    return path


def load_runtime_snapshot(
    context: AppContext,
    path: Path | str,
    base_config: Mapping[str, Any],
) -> dict[str, Any]:
    unit_id = str(base_config["feedback_unit_id"])
    paths = resolve_hv_feedback_runtime_paths(context, unit_id)
    snapshot_path = Path(path).expanduser().resolve()
    with snapshot_path.open("r", encoding="utf-8") as stream:
        payload = json.load(stream)
    if not isinstance(payload, dict):
        raise ValueError("Unsupported HV feedback snapshot format.")
    schema_version = payload.get("schema_version")
    if schema_version == 1:
        allowed_parents = {
            paths["snapshots_dir"].resolve(),
            paths["snapshots_root"].resolve(),
        }
        if snapshot_path.parent not in allowed_parents:
            raise ValueError(
                "Legacy HV feedback snapshots must be loaded from the runtime snapshot directory."
            )
        payload = _migrate_schema_1_snapshot(payload, base_config)
    elif schema_version != 2:
        raise ValueError("Unsupported HV feedback snapshot format.")
    elif snapshot_path.parent != paths["snapshots_dir"].resolve():
        raise ValueError(
            "HV feedback snapshots must be loaded from this unit's runtime directory."
        )
    allowed_keys = {"schema_version", "feedback_unit_id", *SNAPSHOT_SECTIONS}
    if set(payload) - allowed_keys:
        raise ValueError("Snapshot contains unsupported top-level fields.")
    if payload.get("feedback_unit_id") != unit_id:
        raise ValueError(
            f"Snapshot belongs to feedback unit {payload.get('feedback_unit_id')!r}, "
            f"not {unit_id!r}."
        )

    merged = copy.deepcopy(dict(base_config))
    for section in SNAPSHOT_SECTIONS:
        values = payload.get(section)
        if not isinstance(values, dict):
            raise ValueError(f"Snapshot section {section!r} must be a mapping.")
        unknown = set(values) - set(merged[section])
        if unknown:
            raise ValueError(
                f"Snapshot section {section!r} contains unsupported keys: "
                + ", ".join(sorted(unknown))
            )
        merged[section].update(copy.deepcopy(values))
    merged = apply_machine_hv_limit(merged)
    validate_session_config(merged)
    return merged


def _migrate_schema_1_snapshot(
    payload: Mapping[str, Any],
    base_config: Mapping[str, Any],
) -> dict[str, Any]:
    channel_ids = [str(channel["id"]) for channel in base_config["rf_channels"]]
    if (
        str(base_config["feedback_unit_id"]) != "kly1"
        or channel_ids != ["acc1", "buncher"]
        or str(base_config["default_feedback_channel"]) != "acc1"
    ):
        raise ValueError("Schema 1 snapshots are only compatible with the IRFEL KLY1 unit.")
    if set(payload) - {"schema_version", *SNAPSHOT_SECTIONS}:
        raise ValueError("Snapshot contains unsupported top-level fields.")
    control = copy.deepcopy(payload.get("control"))
    reference = copy.deepcopy(payload.get("reference"))
    safety = copy.deepcopy(payload.get("safety"))
    if not all(isinstance(value, dict) for value in (control, reference, safety)):
        raise ValueError("Legacy snapshot sections must be mappings.")
    assert isinstance(control, dict)
    assert isinstance(reference, dict)
    assert isinstance(safety, dict)
    if "init_window_s" in control:
        if "reference_samples" in control or "reference_sample_interval_s" in control:
            raise ValueError(
                "Snapshot mixes legacy and current reference measurement parameters."
            )
        legacy_window_s = float(control.pop("init_window_s"))
        legacy_interval_s = float(
            control.get("sample_period_s", base_config["control"]["sample_period_s"])
        )
        if not math.isfinite(legacy_window_s) or not math.isfinite(legacy_interval_s):
            raise ValueError("Legacy reference measurement parameters must be finite.")
        if legacy_window_s <= 0 or legacy_interval_s <= 0:
            raise ValueError("Legacy reference measurement parameters must be positive.")
        control["reference_samples"] = max(3, int(round(legacy_window_s / legacy_interval_s)))
        control["reference_sample_interval_s"] = legacy_interval_s
    acc1_amplitude = float(reference["acc1_amp_ref"])
    migrated_reference = {
        "hv_kv": float(reference["hv0"]),
        "channels": {
            "acc1": {
                "amplitude": acc1_amplitude,
                "phase_deg": float(reference["acc1_phase_ref"]),
            },
            "buncher": {
                "amplitude": acc1_amplitude * float(reference["amp_ratio_ref"]),
                "phase_deg": float(reference["buncher_phase_ref"]),
            },
        },
    }
    migrated_safety = {
        "hv_min_kv": safety["hv_min_kv"],
        "hv_max_kv": safety["hv_max_kv"],
        "hv_readback_tolerance_kv": safety["hv_readback_tolerance_kv"],
        "phase_limit_deg": {
            "acc1": safety["acc1_phase_limit_deg"],
            "buncher": safety["buncher_phase_limit_deg"],
        },
        "amplitude_ratio_limit_rel": safety["amp_ratio_limit_rel"],
        "feedback_amplitude_min_rel": safety["acc1_amp_min_rel"],
        "feedback_amplitude_max_rel": safety["acc1_amp_max_rel"],
        "require_valid_pv": safety["require_valid_pv"],
        "hold_on_fault": safety["hold_on_fault"],
    }
    return {
        "schema_version": 2,
        "feedback_unit_id": "kly1",
        "control": control,
        "reference": migrated_reference,
        "safety": migrated_safety,
    }


def apply_machine_hv_limit(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return a session config with its HV safety range intersected by machine limits."""
    selected = copy.deepcopy(dict(config))
    safety = selected.get("safety")
    if not isinstance(safety, dict):
        raise ValueError("HV feedback safety configuration must be a mapping.")
    application_limit = LimitRange(
        safety.get("hv_min_kv"),
        safety.get("hv_max_kv"),
        "kV",
    )
    raw_machine_limit = selected.get("_machine_hv_limit")
    machine_limit = (
        LimitRange.from_mapping(raw_machine_limit)
        if isinstance(raw_machine_limit, Mapping) and raw_machine_limit
        else LimitRange()
    )
    try:
        selected_limit = effective_limit(application_limit, machine_limit)
    except MachineProfileError as exc:
        raise ValueError(f"Invalid effective HV safety range: {exc}") from exc
    safety["hv_min_kv"] = selected_limit.low
    safety["hv_max_kv"] = selected_limit.high
    return selected


def validate_session_config(config: Mapping[str, Any]) -> None:
    channel_ids = [str(channel["id"]) for channel in config["rf_channels"]]
    if not channel_ids or len(set(channel_ids)) != len(channel_ids):
        raise ValueError("An HV feedback unit requires unique RF channel IDs.")
    if str(config["default_feedback_channel"]) not in channel_ids:
        raise ValueError("Default feedback channel must belong to the feedback unit.")
    required_pvs = set(required_signal_keys(config))
    if set(config["pvs"]) != required_pvs:
        raise ValueError("Resolved PV signals do not match the feedback unit topology.")

    control_values = _finite_values(
        config["control"],
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
        "control",
    )
    if any(value <= 0 for value in control_values.values()):
        raise ValueError("All control parameters must be positive.")
    reference_samples = control_values["reference_samples"]
    if not reference_samples.is_integer() or not 3 <= reference_samples <= 100000:
        raise ValueError("Reference samples must be an integer between 3 and 100000.")
    if control_values["max_step_kv"] > control_values["total_limit_kv"]:
        raise ValueError("Maximum HV step cannot exceed the total HV limit.")

    reference = config["reference"]
    hv_kv = _finite_value(reference, "hv_kv", "reference")
    reference_channels = reference.get("channels")
    if not isinstance(reference_channels, Mapping) or set(reference_channels) != set(channel_ids):
        raise ValueError("Reference channels must exactly match the feedback unit RF channels.")
    for channel_id in channel_ids:
        values = reference_channels[channel_id]
        amplitude = _finite_value(values, "amplitude", f"reference.channels.{channel_id}")
        _finite_value(values, "phase_deg", f"reference.channels.{channel_id}")
        if amplitude <= 1e-9:
            raise ValueError(f"Reference amplitude for {channel_id!r} is too small.")

    safety = config["safety"]
    safety_values = _finite_values(
        safety,
        (
            "hv_min_kv",
            "hv_max_kv",
            "hv_readback_tolerance_kv",
            "amplitude_ratio_limit_rel",
            "feedback_amplitude_min_rel",
            "feedback_amplitude_max_rel",
        ),
        "safety",
    )
    if safety_values["hv_min_kv"] >= safety_values["hv_max_kv"]:
        raise ValueError("Minimum HV must be less than maximum HV.")
    effective_config = apply_machine_hv_limit(config)
    effective_safety = effective_config["safety"]
    if (
        float(effective_safety["hv_min_kv"]) != safety_values["hv_min_kv"]
        or float(effective_safety["hv_max_kv"]) != safety_values["hv_max_kv"]
    ):
        raise ValueError("HV safety range must stay inside the machine voltage_set limit.")
    if any(value <= 0 for key, value in safety_values.items() if key not in {"hv_min_kv", "hv_max_kv"}):
        raise ValueError("All safety tolerances must be positive.")
    if not (
        safety_values["feedback_amplitude_min_rel"]
        <= 1.0
        <= safety_values["feedback_amplitude_max_rel"]
    ):
        raise ValueError("The feedback amplitude safety range must include 1.0.")
    phase_limits = safety.get("phase_limit_deg")
    if not isinstance(phase_limits, Mapping) or set(phase_limits) != set(channel_ids):
        raise ValueError("Phase limits must exactly match the feedback unit RF channels.")
    for channel_id in channel_ids:
        if _finite_value(phase_limits, channel_id, "safety.phase_limit_deg") <= 0:
            raise ValueError("All phase limits must be positive.")
    if not (
        safety_values["hv_min_kv"] <= hv_kv - control_values["total_limit_kv"]
        and hv_kv + control_values["total_limit_kv"] <= safety_values["hv_max_kv"]
    ):
        raise ValueError("Reference HV +/- total HV limit must fit inside absolute HV bounds.")
    if safety.get("require_valid_pv") is not True:
        raise ValueError("Valid PV reads are required.")
    if safety.get("hold_on_fault") is not True:
        raise ValueError("HOLD on safety faults is required.")


def _finite_values(
    mapping: Mapping[str, Any],
    keys: tuple[str, ...],
    section: str,
) -> dict[str, float]:
    return {key: _finite_value(mapping, key, section) for key in keys}


def _finite_value(mapping: Mapping[str, Any], key: str, section: str) -> float:
    try:
        value = float(mapping[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{section}.{key} must be numeric.") from exc
    if not math.isfinite(value):
        raise ValueError(f"{section}.{key} must be finite.")
    return value


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
