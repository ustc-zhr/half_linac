"""One explicit read-only real capture; injected readers support offline work."""
from dataclasses import replace
from datetime import datetime, timezone
import json
import math
import socket
import time
import uuid

from half_linac.src.shared.machine_profile.loader import machine_root
from half_linac.src.shared.machine_profile.resolver import resolve_channel
from half_linac.src.shared.machine_state import (
    CapturePoint, MachineStateSnapshot, SampleQuality, StateClass, SnapshotEntry,
    build_profile_signature, snapshot_to_dict, value_type,
)
from half_linac.src.shared.pv_connection import PvEndpoint
from half_linac.src.shared.pv_sampling import sample_capture_points

from .models import positive, timestamp
from .pipeline import prepare_model


STRENGTH_UNITS = frozenset({"1/m^2", "m^-2", "1/m²", "m⁻²"})


def observation_metadata(context):
    path = machine_root(context.machine.id) / "apps/digital_shadow.json"
    data = json.loads(path.read_text())["real_observations"]
    if not isinstance(data, dict) or data.get("unit") != "mm" or not isinstance(data.get("width_definitions"), dict):
        raise ValueError("Real width metadata must explicitly declare mm")
    for item in data.get("width_definitions", {}).values():
        if not isinstance(item, dict) or item.get("definition") not in {"gaussian_sigma", "unknown"}:
            raise ValueError("Real SIZE metadata cannot be relabeled as particle RMS")
    return data


def build_real_capture_plan(context, request):
    if context.machine.id != "half" or context.control_backend.name != "real":
        raise ValueError("Real capture requires an explicit HALF real context")
    _, fields = prepare_model(context, request)
    metadata = observation_metadata(context)
    vm_names = {mapping["vm"] for element in context.profile.elements
                for mapping in element.channels.values() if "vm" in mapping}
    points, missing, eligible = [], [], set()

    def add(element, channel, state_class, group):
        pv = resolve_channel(context, element.id, channel, "real")
        if pv in vm_names:
            raise ValueError(f"Real PV {pv} is also mapped to VM")
        points.append(CapturePoint(PvEndpoint("real", element.id, element.kind, channel, pv),
                                   element.order, element.display_name, state_class, group, None))

    for element in context.profile.elements:
        if element.kind != "quad":
            continue
        channels = element.channels
        k1 = channels.get("K1", {}).get("real")
        readback = channels.get("K1_total", {}).get("real")
        currents = {channels.get(key, {}).get("real") for key in ("current_set", "current_readback")}
        if not k1 or not readback or k1 in currents or readback in currents:
            missing.append({"element": element.id, "reason": "No verified normalized K1/K1_total mapping; no current fallback"})
            continue
        eligible.add(element.id)
        add(element, "K1", StateClass.SETTING, "settings")
        add(element, "K1_total", StateClass.READBACK, "readbacks")
    unavailable = {name for name, _ in fields} - eligible
    if unavailable:
        raise ValueError(f"Required normalized readback mapping unavailable: {', '.join(sorted(unavailable))}")
    diagnostics = {}
    for diagnostic in request.diagnostics:
        if diagnostic.unit != "mm":
            raise ValueError("Real SIZE capture currently requires diagnostics in mm")
        element = context.profile.get_element(diagnostic.element)
        add(element, diagnostic.channel, StateClass.OBSERVATION, "observations")
        definition = metadata.get("width_definitions", {}).get(element.id, {"definition": "unknown", "source": "Not confirmed"})
        diagnostics[diagnostic.key] = {"unit": "mm", "width_definition": definition["definition"],
                                       "definition_source": definition.get("source"), "unit_source": metadata["unit_source"]}
    if len({point.endpoint.pv_name for point in points}) != len(points):
        raise ValueError("Real capture plan contains ambiguous duplicate PV mappings")
    return tuple(points), {"missing_normalized_quadrupoles": missing,
                           "normalized_quadrupole_count": len(eligible),
                           "required_model_readbacks": [f"{name}/K1_total" for name, _ in fields],
                           "diagnostic_metadata": metadata, "diagnostics": diagnostics}


def capture_real_once(context, request, *, timeout_s=5.0, allow_live=False,
                      pv_factory=None, poll_function=None, synthetic=False, image_capture=None):
    points, scope = build_real_capture_plan(context, request)
    if image_capture is not None and (image_capture.backend != "real" or image_capture.machine_id != context.machine.id
            or image_capture.request != request or image_capture.synthetic != synthetic):
        raise ValueError("Image capture context/request/source differs from real scalar capture")
    if pv_factory is None:
        if allow_live is not True or synthetic:
            raise ValueError("Live real reads require explicit allow_live; use an injected reader offline")
        if "SYNTHETIC" in request.beam_provenance.upper():
            raise ValueError("Replace the synthetic demo beam/source before live real capture")
    elif poll_function is None:
        raise ValueError("An injected reader requires an injected poll_function; no implicit CA polling")
    started = datetime.now(timezone.utc)
    bound = min(positive(timeout_s, "capture timeout"), request.max_capture_duration_s)
    # Retain one shared deadline; leave a small part of the declared window for
    # disconnect/finalization. Unrelated disconnected PVs must not starve the
    # model's required readbacks or the selected observations.
    deadline = time.monotonic() + bound - min(0.05, bound / 10)
    required = set(scope["required_model_readbacks"])
    groups = (
        tuple(point for point in points if point.key in required),
        tuple(point for point in points if point.state_class == StateClass.OBSERVATION),
        tuple(point for point in points if point.key not in required and point.state_class != StateClass.OBSERVATION),
    )
    sampled_entries, cancelled = [], False
    for index, group in enumerate(groups):
        if index == 2 and image_capture is not None:
            image_capture.sample(deadline, pv_factory=pv_factory, poll_function=poll_function, allow_live=allow_live)
        if not group:
            continue
        remaining = deadline - time.monotonic()
        if remaining > 0:
            sampled = sample_capture_points(group, remaining, pv_factory=pv_factory, poll_function=poll_function)
            sampled_entries.extend(sampled.entries)
            cancelled = cancelled or sampled.status == "cancelled"
        else:
            for point in group:
                endpoint = point.endpoint
                sampled_entries.append(SnapshotEntry(
                    key=point.key, element_id=endpoint.element_id, element_kind=endpoint.element_kind,
                    element_order=point.element_order, display_name=point.display_name,
                    logical_channel=endpoint.logical_channel, pv_name=endpoint.pv_name,
                    state_class=point.state_class, value=None, value_type=value_type(None), unit=None,
                    source_timestamp=None, received_at=None, alarm_status=None, alarm_severity=None,
                    native_count=None, quality=SampleQuality.READ_ERROR,
                    detail="Shared real capture budget exhausted; PV was not read"))
    finished = datetime.now(timezone.utc)
    entries = []
    for entry in sampled_entries:
        expected = entry.unit is not None and (entry.unit.strip() in STRENGTH_UNITS
                    if entry.element_kind == "quad" else entry.unit == "mm")
        if entry.quality == SampleQuality.OK and not expected:
            entry = replace(entry, quality=SampleQuality.READ_ERROR,
                            detail=f"Unexpected or missing source EGU {entry.unit!r}; original value/unit preserved")
        entries.append(entry)
    kind = "synthetic" if synthetic else "injected_reader" if pv_factory is not None else "live"
    snapshot = MachineStateSnapshot(
        snapshot_id=("synthetic_" if synthetic else "") + "real_capture_" + uuid.uuid4().hex,
        name=("SYNTHETIC " if synthetic else "") + "Mapped HALF normalized strengths and widths",
        operator_note=f"Reader: {kind}. Sequential read window, not atomic; screen/bunch alignment not established.",
        machine_id=context.machine.id, machine_display_name=context.machine.display_name, backend="real",
        profile_schema_version=context.profile.schema_version,
        profile_signature=build_profile_signature(context.profile, "real"),
        capture_started_at=started.isoformat(), capture_finished_at=finished.isoformat(),
        capture_status="cancelled" if cancelled else "complete" if all(e.quality == SampleQuality.OK for e in entries) else "partial",
        hostname=socket.gethostname(), consistency="sequential_read_window", requested_count=len(points), entries=tuple(entries))
    from .session import digest
    evidence = {"schema": "digital_shadow_real_capture_v1", "snapshot_hash": digest(snapshot_to_dict(snapshot)),
                "reader_kind": kind, "scope": scope, "same_bunch_confirmed": False,
                "captured_keys": [entry.key for entry in entries]}
    return snapshot, evidence


def verify_real_evidence(context, snapshot, request, evidence):
    from .session import digest
    if snapshot.backend != "real" or evidence.get("schema") != "digital_shadow_real_capture_v1":
        raise ValueError("Real capture evidence requires a real snapshot")
    if context.control_backend.name != "real" or context.machine.id != snapshot.machine_id:
        raise ValueError("Real evidence context mismatch")
    if snapshot.profile_signature != build_profile_signature(context.profile, "real"):
        raise ValueError("Real snapshot profile signature mismatch")
    if evidence.get("snapshot_hash") != digest(snapshot_to_dict(snapshot)):
        raise ValueError("Real evidence does not belong to this snapshot")
    if evidence.get("reader_kind") not in {"synthetic", "injected_reader", "live"}:
        raise ValueError("Unknown real capture reader kind")
    if evidence["reader_kind"] == "synthetic" and not snapshot.snapshot_id.startswith("synthetic_"):
        raise ValueError("Synthetic reader provenance differs from snapshot")
    scope = evidence.get("scope")
    if not isinstance(scope, dict) or not isinstance(scope.get("diagnostics"), dict) or not isinstance(scope.get("diagnostic_metadata"), dict):
        raise ValueError("Malformed real capture scope metadata")
    keys = evidence.get("captured_keys")
    if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys) or len(keys) != len(snapshot.entries) or set(keys) != {e.key for e in snapshot.entries}:
        raise ValueError("Real capture key inventory differs from snapshot")
    if scope["diagnostic_metadata"].get("unit") != "mm":
        raise ValueError("Real observation unit metadata differs")
    width_definitions = scope["diagnostic_metadata"].get("width_definitions")
    if not isinstance(width_definitions, dict):
        raise ValueError("Malformed real width definitions")
    for entry in snapshot.entries:
        if entry.pv_name != resolve_channel(context, entry.element_id, entry.logical_channel, "real"):
            raise ValueError(f"Real PV mapping mismatch for {entry.key}")
    definitions = {}
    for diagnostic in request.diagnostics:
        source = scope["diagnostics"].get(diagnostic.key)
        expected = width_definitions.get(diagnostic.element, {"definition": "unknown"})
        if not isinstance(source, dict) or not isinstance(expected, dict) or source.get("unit") != "mm" or source.get("width_definition") != expected.get("definition"):
            raise ValueError(f"Missing or conflicting real diagnostic metadata: {diagnostic.key}")
        if source["width_definition"] not in {"gaussian_sigma", "unknown"}:
            raise ValueError("Real SIZE cannot be relabeled as particle RMS")
        definitions[diagnostic.key] = source["width_definition"]
    return definitions


def normalized_real_input(snapshot, fields):
    """Return a derived copy for the strict model converter; never edit raw EGU."""
    keys = {f"{name}/K1_total" for name, _ in fields}
    finished = timestamp(snapshot.capture_finished_at)
    entries, changes = [], []
    for entry in snapshot.entries:
        if entry.key in keys:
            if entry.source_timestamp is None or not math.isfinite(entry.source_timestamp) or entry.source_timestamp > finished:
                raise ValueError(f"Invalid source timestamp for {entry.key}")
            if entry.unit is None or entry.unit.strip() not in STRENGTH_UNITS:
                raise ValueError(f"Non-equivalent or missing strength unit for {entry.key}: {entry.unit!r}")
            if entry.unit != "1/m^2":
                changes.append({"key": entry.key, "source_unit": entry.unit, "model_input_unit": "1/m^2", "scale": 1.0})
                entry = replace(entry, unit="1/m^2")
        entries.append(entry)
    return replace(snapshot, entries=tuple(entries)), changes
