from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, replace
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from half_linac.src.apps.dispersion_correction.config import parse_config
from half_linac.src.apps.dispersion_correction.models import (
    CorrectionResult,
    DispersionMeasurement,
    ResponseMatrixResult,
    RunConfig,
)
from half_linac.src.apps.dispersion_correction.reports import result_to_dict, write_result_files
from half_linac.src.shared.machine_profile import (
    AppContext,
    MachineProfileError,
    get_workflow,
    list_elements,
    load_app_context,
    resolve_channel,
    new_app_run_dir,
    resolve_app_runtime_paths,
    workflow_writes_allowed,
)


WORKFLOW_NAME = "dispersion_correction"
APP_DIR = Path(__file__).resolve().parent


def load_profile_run_config(context: AppContext | None = None) -> tuple[AppContext, RunConfig]:
    """Build the app's existing RunConfig from the selected machine profile."""

    resolved_context = context or load_app_context(WORKFLOW_NAME)
    workflow = get_workflow(resolved_context.profile, WORKFLOW_NAME)
    backend_name = resolved_context.control_backend.name
    supported_backends = _string_sequence(
        workflow.get("control_backends"),
        "control_backends",
    )
    if backend_name not in supported_backends:
        raise MachineProfileError(
            f"Dispersion correction does not support control backend {backend_name!r}; "
            f"configured backends: {', '.join(supported_backends)}."
        )

    backend = {
        "type": "epics",
        "mode": (
            "write_enabled"
            if workflow_writes_allowed(resolved_context, WORKFLOW_NAME)
            else "read_only"
        ),
        "options": {
            "site": resolved_context.profile.machine.id,
            "profile_backend": backend_name,
            "ca_timeout": float(workflow.get("ca_timeout", 0.5)),
            "bpm_position_scale_to_mm": _backend_number(
                workflow.get("bpm_position_scale_to_mm"),
                backend_name,
                default=1.0,
            ),
            "pv_map": _build_profile_pv_map(resolved_context, workflow),
        },
    }

    raw_config = {
        "backend": backend,
        "energy_knob": dict(_mapping(workflow.get("energy_knob"), "energy_knob")),
        "target_bpms": list(_string_sequence(workflow.get("target_bpms"), "target_bpms")),
        "knobs": [dict(item) for item in _mapping_sequence(workflow.get("knobs"), "knobs")],
        "measurement": dict(_mapping(workflow.get("measurement"), "measurement")),
        "solver": dict(_mapping(workflow.get("solver"), "solver")),
        "safety": dict(_mapping(workflow.get("safety"), "safety")),
    }
    return resolved_context, parse_config(raw_config)


def selectable_profile_bpms(context: AppContext) -> tuple[str, ...]:
    """Return profile BPMs that expose an x channel on the active backend."""

    return tuple(
        element.id
        for element in list_elements(context, kind="bpm", logical_channel="x")
        if _channel_is_resolvable(context, element.id, "x")
    )


def selectable_profile_quadrupoles(context: AppContext) -> tuple[str, ...]:
    """Return quadrupoles with a same-unit setpoint/readback path."""

    selected = []
    for element in list_elements(context, kind="quad"):
        if context.control_backend.name == "real":
            if _channel_is_resolvable(context, element.id, "current_set") and _channel_is_resolvable(
                context,
                element.id,
                "current_readback",
            ):
                selected.append(element.id)
            continue
        if _channel_is_resolvable(context, element.id, "K1"):
            selected.append(element.id)
    return tuple(selected)


def apply_profile_selection(
    context: AppContext,
    config: RunConfig,
    *,
    target_bpms: tuple[str, ...],
    knobs,
) -> RunConfig:
    """Resolve PV mappings for one GUI selection without changing the profile."""

    allowed_bpms = set(selectable_profile_bpms(context))
    allowed_quads = set(selectable_profile_quadrupoles(context))
    unknown_bpms = [name for name in target_bpms if name not in allowed_bpms]
    unknown_quads = [
        device
        for knob in knobs
        for device in knob.devices
        if device not in allowed_quads
    ]
    if unknown_bpms:
        raise MachineProfileError(
            "Selected BPMs are unavailable on the active backend: " + ", ".join(unknown_bpms)
        )
    if unknown_quads:
        raise MachineProfileError(
            "Selected quadrupoles are unavailable on the active backend: "
            + ", ".join(dict.fromkeys(unknown_quads))
        )

    workflow = get_workflow(context.profile, WORKFLOW_NAME)
    options = dict(config.backend.options)
    options["pv_map"] = _build_selection_pv_map(
        context,
        target_bpms,
        knobs,
        _mapping(workflow.get("energy_knob"), "energy_knob"),
    )
    return replace(
        config,
        backend=replace(config.backend, options=options),
        target_bpms=tuple(target_bpms),
        knobs=tuple(knobs),
    )


def default_offline_config() -> RunConfig:
    """Small dependency-free fallback used by direct imports and GUI smoke tests."""

    return parse_config(
        {
            "backend": {
                "type": "offline",
                "mode": "read_only",
                "options": {"gui_progress_delay_s": 0.0},
            },
            "energy_knob": {"name": "ENERGY_DELTA", "delta": 1.0e-4},
            "target_bpms": ["BPM01", "BPM02", "BPM03", "BPM04"],
            "knobs": [
                {
                    "name": "Q1_sym",
                    "devices": {"Q1L": 1.0, "Q1R": 1.0},
                    "scan_step": 0.002,
                    "limit": 0.03,
                },
                {
                    "name": "Q2_sym",
                    "devices": {"Q2L": 1.0, "Q2R": 1.0},
                    "scan_step": 0.002,
                    "limit": 0.03,
                },
            ],
            "measurement": {
                "plane": "x",
                "samples_per_step": 5,
                "sample_interval_s": 0.0,
                "final_samples": 10,
                "settle_time_s": 0.0,
            },
            "solver": {
                "svd_cut": 0.001,
                "regularization": 0.001,
                "gain": 0.5,
                "max_step_fraction": 0.25,
                "max_iter": 5,
                "response_update": "once",
                "min_step_improvement": 0.05,
                "success_min_improvement": 2.0,
            },
            "safety": {"max_reference_orbit_change_mm": 1.0},
        }
    )


def write_profile_result(
    context: AppContext,
    result: CorrectionResult,
) -> dict[str, Path]:
    """Write latest and timestamped reports under the standard app runtime tree."""

    return write_profile_operation(context, "run", result)


def write_profile_operation(
    context: AppContext,
    task: str,
    result: CorrectionResult | DispersionMeasurement | ResponseMatrixResult,
    *,
    config: RunConfig | None = None,
    live_preflight=None,
) -> dict[str, Path]:
    """Archive every operator operation with config and preflight context."""

    if task not in {"measure", "response", "run"}:
        raise ValueError(f"Unsupported dispersion-correction task: {task}")

    runtime_paths = resolve_app_runtime_paths(APP_DIR, context)
    latest_dir = runtime_paths["latest_dir"]
    run_dir = new_app_run_dir(APP_DIR, context, kind=f"dispersion_{task}")
    latest_dir.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)

    if isinstance(result, CorrectionResult):
        payload = result_to_dict(result)
        latest_reports = write_result_files(result, latest_dir)
        run_reports = write_result_files(result, run_dir)
    else:
        payload = _operation_payload(result)
        filename = f"dispersion_{task}_result.json"
        payload_text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        latest_result = latest_dir / filename
        run_result = run_dir / filename
        latest_result.write_text(payload_text, encoding="utf-8")
        run_result.write_text(payload_text, encoding="utf-8")
        latest_reports = {"json": latest_result}
        run_reports = {"json": run_result}

    metadata = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "machine_id": context.profile.machine.id,
        "control_backend": context.control_backend.name,
        "app": WORKFLOW_NAME,
        "task": task,
        "success": result.success if isinstance(result, CorrectionResult) else True,
        "reason": result.reason if isinstance(result, CorrectionResult) else "Completed",
        "config": asdict(config) if config is not None else None,
        "live_preflight": (
            live_preflight.as_dict()
            if live_preflight is not None and hasattr(live_preflight, "as_dict")
            else None
        ),
        "run_dir": str(run_dir),
    }
    metadata_text = json.dumps(metadata, indent=2, sort_keys=True)
    latest_dir.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    latest_metadata = latest_dir / "metadata.json"
    run_metadata = run_dir / "metadata.json"
    latest_metadata.write_text(metadata_text, encoding="utf-8")
    run_metadata.write_text(metadata_text, encoding="utf-8")
    return {
        **{f"latest_{kind}": path for kind, path in latest_reports.items()},
        **{f"run_{kind}": path for kind, path in run_reports.items()},
        "latest_metadata": latest_metadata,
        "run_metadata": run_metadata,
    }


def _operation_payload(
    result: DispersionMeasurement | ResponseMatrixResult,
) -> dict[str, Any]:
    if isinstance(result, DispersionMeasurement):
        return _measurement_payload(result)
    return {
        "matrix": result.matrix.tolist(),
        "bpm_names": list(result.bpm_names),
        "knob_names": list(result.knob_names),
        "singular_values": result.singular_values.tolist(),
        "condition_number": result.condition_number,
        "measurement": _measurement_payload(result.measurement),
    }


def _measurement_payload(result: DispersionMeasurement) -> dict[str, Any]:
    return {
        "plane": result.plane,
        "delta": result.delta,
        "rms_mm": result.rms_mm,
        "bpm_names": list(result.bpm_names),
        "values_mm": result.values_mm.tolist(),
        "valid": result.valid.tolist(),
        "plus": _bpm_payload(result.plus),
        "minus": _bpm_payload(result.minus),
    }


def _bpm_payload(reading) -> dict[str, Any]:
    return {
        "names": list(reading.names),
        "x_mm": reading.x_mm.tolist(),
        "y_mm": reading.y_mm.tolist(),
        "valid": reading.valid.tolist(),
        "charge": reading.charge,
        "loss": reading.loss,
    }


def _build_profile_pv_map(context: AppContext, workflow: Mapping[str, object]) -> dict[str, Any]:
    target_bpms = _string_sequence(workflow.get("target_bpms"), "target_bpms")
    knobs = _mapping_sequence(workflow.get("knobs"), "knobs")
    return _build_selection_pv_map(
        context,
        target_bpms,
        knobs,
        _mapping(workflow.get("energy_knob"), "energy_knob"),
    )


def _build_selection_pv_map(
    context: AppContext,
    target_bpms,
    knobs,
    energy_knob: Mapping[str, object],
) -> dict[str, Any]:
    backend_name = context.control_backend.name

    bpms: dict[str, dict[str, str]] = {}
    for bpm_name in target_bpms:
        item = {"x": resolve_channel(context, bpm_name, "x")}
        try:
            item["y"] = resolve_channel(context, bpm_name, "y")
        except MachineProfileError:
            pass
        bpms[bpm_name] = item

    device_names: list[str] = []
    for knob in knobs:
        devices_value = knob.devices if hasattr(knob, "devices") else knob.get("devices")
        devices = _mapping(devices_value, "knobs[].devices")
        device_names.extend(str(name) for name in devices)

    quadrupoles: dict[str, dict[str, str]] = {}
    for device_name in dict.fromkeys(device_names):
        if backend_name == "real":
            try:
                quadrupoles[device_name] = {
                    "control": "current",
                    "current_set": resolve_channel(context, device_name, "current_set"),
                    "current_readback": resolve_channel(context, device_name, "current_readback"),
                }
                continue
            except MachineProfileError:
                pass
        k1_pv = resolve_channel(context, device_name, "K1")
        quadrupoles[device_name] = {"control": "k1", "K1": k1_pv}

    energy_mapping: dict[str, str] = {}
    element_id = str(energy_knob.get("element", "")).strip()
    if element_id:
        set_channel = str(energy_knob.get("set_channel", "phase_set"))
        readback_channel = str(energy_knob.get("readback_channel", "phase_readback"))
        energy_mapping["phase_set"] = resolve_channel(context, element_id, set_channel)
        try:
            energy_mapping["phase_readback"] = resolve_channel(context, element_id, readback_channel)
        except MachineProfileError:
            pass

    return {
        "bpms": bpms,
        "quadrupoles": quadrupoles,
        "energy_knob": energy_mapping,
    }


def _channel_is_resolvable(context: AppContext, element_id: str, logical_channel: str) -> bool:
    try:
        resolve_channel(context, element_id, logical_channel)
    except MachineProfileError:
        return False
    return True


def _mapping(value: object, location: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MachineProfileError(f"workflows.{WORKFLOW_NAME}.{location} must be a mapping.")
    return value


def _mapping_sequence(value: object, location: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list) or not value:
        raise MachineProfileError(f"workflows.{WORKFLOW_NAME}.{location} must be a non-empty list.")
    items = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise MachineProfileError(
                f"workflows.{WORKFLOW_NAME}.{location}[{index}] must be a mapping."
            )
        items.append(item)
    return tuple(items)


def _string_sequence(value: object, location: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise MachineProfileError(f"workflows.{WORKFLOW_NAME}.{location} must be a non-empty list.")
    names = tuple(str(item).strip() for item in value)
    if any(not name for name in names):
        raise MachineProfileError(f"workflows.{WORKFLOW_NAME}.{location} contains an empty name.")
    return names


def _backend_number(value: object, backend_name: str, *, default: float) -> float:
    if isinstance(value, Mapping):
        value = value.get(backend_name, value.get("default", default))
    if value is None:
        value = default
    return float(value)
