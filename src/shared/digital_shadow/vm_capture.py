"""Read-only VM capture with an explicit link to one completed calculation."""
from dataclasses import replace
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import socket
import time
import uuid

from half_linac.src.shared.machine_profile.resolver import resolve_channel
from half_linac.src.shared.machine_profile.runtime_resolver import resolve_vm_runtime
from half_linac.src.shared.machine_state import (
    CapturePoint, MachineStateSnapshot, SampleQuality, StateClass,
    build_profile_signature, snapshot_to_dict,
)
from half_linac.src.shared.pv_connection import PvEndpoint
from half_linac.src.shared.pv_sampling import sample_capture_points
from half_linac.src.shared.runtime_state import read_runtime_state
from half_linac.src.virtual_machine.workbench_data import input_version, observation_dir

from .models import positive, timestamp
from .pipeline import prepare_model
from .session import digest


def build_vm_capture_plan(context, request):
    """Resolve VM mappings only, before any connection is attempted."""
    if context.control_backend.name != "vm":
        raise ValueError("VM capture requires control_backend='vm'; real capture is not available")
    _, fields = prepare_model(context, request)
    channels = [(name, "K1", "1/m^2", StateClass.SETTING) for name, _ in fields]
    for diagnostic in request.diagnostics:
        if diagnostic.unit != "mm":
            raise ValueError("VM particle RMS publication uses mm; declare diagnostics in mm")
        channels.append((diagnostic.element, diagnostic.channel, "mm", StateClass.OBSERVATION))
    real_names = {mapping["real"] for element in context.profile.elements
                  for mapping in element.channels.values() if "real" in mapping}
    points = []
    for element_id, channel, unit, state_class in channels:
        element = context.profile.get_element(element_id)
        name = resolve_channel(context, element_id, channel, "vm")
        if name in real_names:
            raise ValueError(f"VM PV {name} is also mapped to real; capture refused")
        points.append(CapturePoint(
            endpoint=PvEndpoint("vm", element_id, element.kind, channel, name),
            element_order=element.order, display_name=element.display_name,
            state_class=state_class,
            capture_group="settings" if state_class == StateClass.SETTING else "observations",
            unit=None,  # Preserve an IOC unit if present; validate it below.
        ))
    return tuple(points), {f"{element}/{channel}": unit for element, channel, unit, _ in channels}


def _ready_observation(runtime_json):
    directory = observation_dir(runtime_json)
    status = read_runtime_state(directory / "status.json")
    state = read_runtime_state(runtime_json)
    result_bytes = (directory / "result.json").read_bytes()
    result = json.loads(result_bytes)
    version = input_version(state)
    if status.get("phase") != "Ready" or status.get("input_version") != version or status.get("result_version") != version:
        raise RuntimeError("VM calculation is not Ready for the current input version")
    if result.get("input_version") != version or input_version(result["input_state"]) != version:
        raise RuntimeError("VM result and runtime input versions differ")
    if not status.get("session") or result.get("session") != status["session"] or result.get("calculation") != status.get("calculation"):
        raise RuntimeError("VM result and status identify different calculations")
    if "rms" not in status.get("publication", {}):
        raise RuntimeError("VM has no RMS publication record; restart it with the updated VM publisher")
    # Check that a transition did not occur while the files were being read.
    if read_runtime_state(directory / "status.json") != status:
        raise RuntimeError("VM status changed while reading the observation")
    return status, result, digest(result)


def _wait_ready(runtime_json, timeout_s):
    deadline = time.monotonic() + positive(timeout_s, "VM ready timeout")
    while True:
        try:
            return _ready_observation(runtime_json)
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(f"VM did not become ready: {exc}") from exc
            time.sleep(min(0.05, remaining))


def verify_vm_evidence(context, snapshot, request, evidence):
    """Verify saved evidence too; replay never reconnects to the VM."""
    points, units = build_vm_capture_plan(context, request)
    if snapshot.backend != "vm" or evidence.get("schema") != "digital_shadow_vm_capture_v1":
        raise ValueError("VM capture evidence requires a VM snapshot")
    if evidence.get("snapshot_hash") != digest(snapshot_to_dict(snapshot)):
        raise ValueError("VM evidence does not belong to this snapshot")
    status, state = evidence["status"], evidence["input_state"]
    version = input_version(state)
    if status.get("phase") != "Ready" or status.get("input_version") != version or status.get("result_version") != version:
        raise ValueError("VM evidence does not identify a completed working point")
    if not status.get("session") or "rms" not in status.get("publication", {}):
        raise ValueError("VM evidence lacks session or RMS publication provenance")
    completed = timestamp(status["completed_at"])
    elapsed = positive(status["elapsed"], "VM elapsed time")
    capture_finished = timestamp(snapshot.capture_finished_at)
    if completed > capture_finished + 1:
        raise ValueError("VM result completed after the capture")
    window = (completed - elapsed - 1, min(completed + 1, capture_finished))
    entries = {entry.key: entry for entry in snapshot.entries}
    screens = evidence["screens"]
    for point in points:
        entry = entries.get(point.key)
        if entry is None or entry.pv_name != point.endpoint.pv_name or entry.quality != SampleQuality.OK:
            raise ValueError(f"Missing or unusable VM sample {point.key}")
        if entry.unit != units[point.key] or entry.native_count != 1 or entry.value_type != "number" or isinstance(entry.value, bool) or (entry.alarm_severity or 0) > 0:
            raise ValueError(f"Invalid VM scalar metadata for {point.key}")
        value = float(entry.value)
        if not math.isfinite(value):
            raise ValueError(f"Nonfinite VM sample {point.key}")
        if point.endpoint.logical_channel == "K1":
            expected = float(state["lattice"][entry.element_id]["K1"])
        else:
            screen = screens[entry.element_id]
            if screen.get("error") or screen.get("name") != entry.element_id:
                raise ValueError(f"VM result has no valid screen {entry.element_id}")
            expected = float(screen["sx" if entry.logical_channel == "sigx" else "sy"])
            if entry.source_timestamp is None or not window[0] <= entry.source_timestamp <= window[1]:
                raise ValueError(f"{point.key} was not published during the completed VM calculation")
        if not math.isfinite(expected) or not math.isclose(value, expected, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError(f"{point.key} does not match the completed VM result")
    return window


def capture_vm_once(context, request, *, runtime_json=None, timeout_s=5.0,
                    ready_timeout_s=10.0, pv_factory=None, poll_function=None, image_capture=None):
    points, units = build_vm_capture_plan(context, request)
    if image_capture is not None and (image_capture.backend != "vm" or image_capture.machine_id != context.machine.id
            or image_capture.request != request):
        raise ValueError("Image capture context/request differs from VM scalar capture")
    runtime_json = Path(runtime_json or resolve_vm_runtime(context.profile).runtime_json).resolve()
    before, result, result_hash = _wait_ready(runtime_json, ready_timeout_s)
    started = datetime.now(timezone.utc)
    bound = min(positive(timeout_s, "capture timeout"), request.max_capture_duration_s)
    deadline = time.monotonic() + bound - (min(0.05, bound / 10) if image_capture is not None else 0)
    sampled = sample_capture_points(
        points, max(0.001, deadline - time.monotonic()),
        pv_factory=pv_factory, poll_function=poll_function,
    )
    if image_capture is not None:
        image_capture.sample(deadline, pv_factory=pv_factory, poll_function=poll_function)
    finished = datetime.now(timezone.utc)
    after, _, after_hash = _ready_observation(runtime_json)
    if before != after or result_hash != after_hash:
        raise RuntimeError("VM changed during capture; no mixed working point will be modeled")
    entries = []
    for entry in sampled.entries:
        expected_unit = units[entry.key]
        if entry.unit is None:
            entry = replace(entry, unit=expected_unit, detail=(entry.detail + "; unit supplied by VM publication contract").lstrip("; "))
        elif entry.unit != expected_unit:
            entry = replace(entry, quality=SampleQuality.READ_ERROR, detail=f"VM unit {entry.unit!r} differs from {expected_unit!r}")
        entries.append(entry)
    snapshot = MachineStateSnapshot(
        snapshot_id=f"vm_capture_{uuid.uuid4().hex}", name="Digital shadow VM capture",
        operator_note=f"Read-only capture matched to VM session {before['session']}, calculation {before['calculation']}",
        machine_id=context.machine.id, machine_display_name=context.machine.display_name,
        backend="vm", profile_schema_version=context.profile.schema_version,
        profile_signature=build_profile_signature(context.profile, "vm"),
        capture_started_at=started.isoformat(), capture_finished_at=finished.isoformat(),
        capture_status=sampled.status, hostname=socket.gethostname(),
        consistency="vm_result_matched", requested_count=sampled.requested_count, entries=tuple(entries),
    )
    selected = {diagnostic.element for diagnostic in request.diagnostics}
    screens = {}
    for screen in result["screens"].values():
        if screen.get("name") in selected:
            name = screen["name"]
            if name in screens:
                raise RuntimeError(f"Ambiguous screen occurrence {name}")
            screens[name] = {key: screen[key] for key in ("name", "sx", "sy", "error") if key in screen}
    evidence = {"schema": "digital_shadow_vm_capture_v1", "snapshot_hash": digest(snapshot_to_dict(snapshot)),
                "status": before, "input_state": result["input_state"], "screens": screens,
                "vm_result_hash": result_hash, "runtime_json": str(runtime_json)}
    verify_vm_evidence(context, snapshot, request, evidence)
    return snapshot, evidence
