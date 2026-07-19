from __future__ import annotations

import copy
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from half_linac.src.shared.machine_profile import (
    AppContext,
    MachineProfileError,
    get_workflow,
    make_runtime_run_id,
    new_app_run_dir,
    require_workflow_write_allowed,
    resolve_app_runtime_paths,
    resolve_channel,
)


APP_DIR = Path(__file__).resolve().parent
WORKFLOW_NAME = "hv_feedback"
SIGNAL_KEYS = (
    "hv_setpoint",
    "hv_readback",
    "acc1_amp",
    "acc1_phase",
    "buncher_amp",
    "buncher_phase",
)
SNAPSHOT_SECTIONS = ("control", "reference", "safety")


def assert_hv_feedback_runtime(context: AppContext) -> None:
    workflow = get_workflow(context.profile, WORKFLOW_NAME)
    supported = tuple(str(value).strip().lower() for value in workflow["control_backends"])
    if context.profile.machine.id != "irfel" or context.control_backend.name != "real":
        raise MachineProfileError(
            "HV Feedback is available only for the IRFEL real-machine backend."
        )
    if "real" not in supported:
        raise MachineProfileError(
            "The hv_feedback workflow does not allow the real-machine backend."
        )


def load_profile_config(context: AppContext) -> dict[str, Any]:
    assert_hv_feedback_runtime(context)
    workflow = get_workflow(context.profile, WORKFLOW_NAME)
    raw_signals = workflow["signals"]
    pvs: dict[str, dict[str, str]] = {}
    for key in SIGNAL_KEYS:
        signal = raw_signals[key]
        element_id = str(signal["element"])
        logical_channel = str(signal["channel"])
        pvs[key] = {
            "name": resolve_channel(context, element_id, logical_channel),
            "unit": str(signal.get("unit", "")),
            "element": element_id,
            "channel": logical_channel,
        }

    config = {
        "pvs": pvs,
        "control": copy.deepcopy(dict(workflow["control"])),
        "reference": copy.deepcopy(dict(workflow["reference"])),
        "safety": copy.deepcopy(dict(workflow["safety"])),
        "logging": copy.deepcopy(dict(workflow["logging"])),
    }
    config["reference"]["mode"] = "manual"
    validate_session_config(config)
    return config


def require_confirmed_feedback_write(
    context: AppContext,
    *,
    session_confirmed: bool,
) -> None:
    require_feedback_write_policy(context)
    if not session_confirmed:
        raise MachineProfileError(
            "This feedback session has not been explicitly confirmed by the operator."
        )


def require_feedback_write_policy(context: AppContext) -> None:
    assert_hv_feedback_runtime(context)
    require_workflow_write_allowed(
        context,
        WORKFLOW_NAME,
        "HV feedback setpoint write",
    )


def resolve_hv_feedback_runtime_paths(context: AppContext) -> dict[str, Path]:
    base = resolve_app_runtime_paths(APP_DIR, context)
    return {
        **base,
        "snapshots_dir": base["runtime_dir"] / "snapshots",
    }


def new_run_dir(context: AppContext, operation: str) -> Path:
    run_dir = new_app_run_dir(APP_DIR, context, kind=operation)
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
    state: str,
    log_path: Path | None = None,
    detail: str = "",
) -> Path:
    payload = {
        "schema_version": 1,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "machine": context.profile.machine.id,
        "control_backend": context.control_backend.name,
        "operation": operation,
        "state": state,
        "detail": detail,
        "log_path": str(log_path) if log_path is not None else None,
        "signals": copy.deepcopy(config["pvs"]),
        "parameters": snapshot_payload(config),
    }
    metadata_path = run_dir / "metadata.json"
    _write_json(metadata_path, payload)
    latest_path = resolve_hv_feedback_runtime_paths(context)["latest_metadata_path"]
    _write_json(latest_path, payload)
    return metadata_path


def snapshot_payload(config: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        **{
            section: copy.deepcopy(dict(config[section]))
            for section in SNAPSHOT_SECTIONS
        },
    }


def save_runtime_snapshot(
    context: AppContext,
    config: Mapping[str, Any],
) -> Path:
    validate_session_config(config)
    snapshots_dir = resolve_hv_feedback_runtime_paths(context)["snapshots_dir"]
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    path = snapshots_dir / f"{make_runtime_run_id('parameters')}.json"
    _write_json(path, snapshot_payload(config))
    return path


def load_runtime_snapshot(
    context: AppContext,
    path: Path | str,
    base_config: Mapping[str, Any],
) -> dict[str, Any]:
    snapshots_dir = resolve_hv_feedback_runtime_paths(context)["snapshots_dir"].resolve()
    snapshot_path = Path(path).expanduser().resolve()
    if snapshot_path.parent != snapshots_dir:
        raise ValueError("HV feedback snapshots must be loaded from this app's runtime directory.")
    with snapshot_path.open("r", encoding="utf-8") as stream:
        payload = json.load(stream)
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("Unsupported HV feedback snapshot format.")
    if set(payload) - {"schema_version", *SNAPSHOT_SECTIONS}:
        raise ValueError("Snapshot contains unsupported top-level fields.")

    merged = copy.deepcopy(dict(base_config))
    for section in SNAPSHOT_SECTIONS:
        values = payload.get(section)
        if not isinstance(values, dict):
            raise ValueError(f"Snapshot section {section!r} must be a mapping.")
        values = copy.deepcopy(values)
        if section == "control" and "init_window_s" in values:
            if "reference_samples" in values or "reference_sample_interval_s" in values:
                raise ValueError(
                    "Snapshot mixes legacy and current reference measurement parameters."
                )
            legacy_window_s = float(values.pop("init_window_s"))
            legacy_interval_s = float(
                values.get("sample_period_s", merged["control"]["sample_period_s"])
            )
            if not math.isfinite(legacy_window_s) or not math.isfinite(legacy_interval_s):
                raise ValueError("Legacy reference measurement parameters must be finite.")
            if legacy_window_s <= 0 or legacy_interval_s <= 0:
                raise ValueError("Legacy reference measurement parameters must be positive.")
            values["reference_samples"] = max(
                3,
                int(round(legacy_window_s / legacy_interval_s)),
            )
            values["reference_sample_interval_s"] = legacy_interval_s
        unknown = set(values) - set(merged[section])
        if unknown:
            raise ValueError(
                f"Snapshot section {section!r} contains unsupported keys: "
                + ", ".join(sorted(unknown))
            )
        merged[section].update(values)
    merged["reference"]["mode"] = "manual"
    validate_session_config(merged)
    return merged


def validate_session_config(config: Mapping[str, Any]) -> None:
    control = config["control"]
    control_values = _finite_values(
        control,
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

    reference = _finite_values(
        config["reference"],
        (
            "acc1_amp_ref",
            "acc1_phase_ref",
            "buncher_phase_ref",
            "amp_ratio_ref",
            "hv0",
        ),
        "reference",
    )
    if reference["acc1_amp_ref"] <= 0 or reference["amp_ratio_ref"] <= 0:
        raise ValueError("Amplitude and ratio references must be positive.")

    safety = _finite_values(
        config["safety"],
        (
            "hv_min_kv",
            "hv_max_kv",
            "hv_readback_tolerance_kv",
            "acc1_phase_limit_deg",
            "buncher_phase_limit_deg",
            "amp_ratio_limit_rel",
            "acc1_amp_min_rel",
            "acc1_amp_max_rel",
        ),
        "safety",
    )
    if safety["hv_min_kv"] >= safety["hv_max_kv"]:
        raise ValueError("Minimum HV must be less than maximum HV.")
    if any(
        safety[key] <= 0
        for key in (
            "hv_readback_tolerance_kv",
            "acc1_phase_limit_deg",
            "buncher_phase_limit_deg",
            "amp_ratio_limit_rel",
            "acc1_amp_min_rel",
            "acc1_amp_max_rel",
        )
    ):
        raise ValueError("All safety tolerances must be positive.")
    if not safety["acc1_amp_min_rel"] <= 1.0 <= safety["acc1_amp_max_rel"]:
        raise ValueError("The ACC1 amplitude safety range must include 1.0.")
    if not (
        safety["hv_min_kv"]
        <= reference["hv0"] - control_values["total_limit_kv"]
        and reference["hv0"] + control_values["total_limit_kv"]
        <= safety["hv_max_kv"]
    ):
        raise ValueError("hv0 +/- total HV limit must fit inside the absolute HV bounds.")
    if config["safety"].get("require_valid_pv") is not True:
        raise ValueError("Valid PV reads are required.")
    if config["safety"].get("hold_on_fault") is not True:
        raise ValueError("HOLD on safety faults is required.")


def _finite_values(
    mapping: Mapping[str, Any],
    keys: tuple[str, ...],
    section: str,
) -> dict[str, float]:
    out: dict[str, float] = {}
    for key in keys:
        try:
            value = float(mapping[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{section}.{key} must be numeric.") from exc
        if not math.isfinite(value):
            raise ValueError(f"{section}.{key} must be finite.")
        out[key] = value
    return out


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
