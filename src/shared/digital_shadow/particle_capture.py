"""Read the full-line particle model's settings from VM or a simulated real reader."""

from dataclasses import replace
from datetime import datetime, timezone
import math
import socket
import time
import uuid

from half_linac.src.shared.machine_profile.models import ControlBackendConfig
from half_linac.src.shared.machine_profile.resolver import resolve_channel
from half_linac.src.shared.machine_state import (
    CapturePoint, MachineStateSnapshot, SampleQuality, StateClass, build_profile_signature,
    save_snapshot, snapshot_to_dict,
)
from half_linac.src.shared.pv_connection import PvEndpoint
from half_linac.src.shared.pv_sampling import sample_capture_points
from half_linac.src.shared.runtime_state import write_runtime_state

from .models import positive, timestamp
from .particle_model import MODEL_LINE, downstream_sources, downstream_state, quadrupole_fields
from .real_capture import STRENGTH_UNITS
from .real_demo import SimulatedRealReader
from .session import digest
from .vm_capture import capture_vm_plan, verify_vm_samples, vm_capture_points


EVIDENCE_FILE = "particle_capture_evidence.json"
CAPTURE_SCHEMA = "digital_shadow_particle_capture_v1"
CAPTURE_SOURCES = {"vm": "VM", "demo-real": "Real · simulated · NO PV"}
CAPTURE_TIMEOUT_S = 5.0
POLL_INTERVAL_S = 1.0
CAPTURE_COMMAND = "capture-particle-snapshot"
FOLLOW_COMMAND = "follow-particle"


def _real_points(context, fields):
    vm_names = {mapping["vm"] for element in context.profile.elements
                for mapping in element.channels.values() if "vm" in mapping}
    points = []
    for name, _ in fields:
        element = context.profile.get_element(name)
        pv = resolve_channel(context, name, "K1_total", "real")
        currents = {element.channels.get(key, {}).get("real") for key in ("current_set", "current_readback")}
        if pv in vm_names or pv in currents:
            raise ValueError(f"Invalid normalized real quadrupole mapping for {name}")
        points.append(CapturePoint(PvEndpoint("real", name, element.kind, "K1_total", pv),
                                  element.order, element.display_name, StateClass.READBACK, "readbacks", None))
    if len({point.endpoint.pv_name for point in points}) != len(points):
        raise ValueError("Full-line real readbacks have duplicate PV mappings")
    return tuple(points)


def _backend_context(context, source):
    if source not in CAPTURE_SOURCES or context.machine.id != "half":
        raise ValueError("Choose HALF VM or simulated Real capture")
    return replace(context, control_backend=ControlBackendConfig("vm" if source == "vm" else "real"))


def _real_values(entries, points, started_at, finished_at):
    by_key = {entry.key: entry for entry in entries}
    if len(by_key) != len(entries) or len(by_key) != len(points):
        raise ValueError("Full-line Real readbacks are incomplete")
    values = {}
    start, end = timestamp(started_at), timestamp(finished_at)
    for point in points:
        entry = by_key.get(point.key)
        if (entry is None or entry.pv_name != point.endpoint.pv_name or entry.quality != SampleQuality.OK
                or entry.native_count != 1 or entry.value_type != "number" or isinstance(entry.value, bool)
                or (entry.alarm_severity or 0) > 0 or entry.unit is None or entry.unit.strip() not in STRENGTH_UNITS
                or not math.isfinite(float(entry.value)) or entry.source_timestamp is None
                or not math.isfinite(entry.source_timestamp) or entry.source_timestamp > end
                or entry.received_at is None or not start <= timestamp(entry.received_at) <= end):
            raise ValueError(f"Missing or invalid Real readback {point.key}")
        values[entry.key] = float(entry.value)
    return values


def verify_particle_capture(context, state, snapshot, evidence):
    if not isinstance(evidence, dict):
        raise ValueError("Expected particle capture evidence JSON object")
    source = evidence.get("source")
    context = _backend_context(context, source)
    if (evidence.get("schema") != CAPTURE_SCHEMA or evidence.get("model_line") != MODEL_LINE
            or snapshot.machine_id != context.machine.id or snapshot.backend != context.control_backend.name
            or snapshot.profile_signature != build_profile_signature(context.profile, snapshot.backend)
            or evidence.get("snapshot_hash") != digest(snapshot_to_dict(snapshot))):
        raise ValueError("Particle capture evidence does not match this snapshot and model")
    fields = quadrupole_fields(state)
    if source == "vm":
        _verify_vm_line(state, evidence["vm"])
        verify_vm_samples(snapshot, evidence["vm"], vm_capture_points(context, fields))
    else:
        if not snapshot.snapshot_id.startswith("synthetic_real_particle_"):
            raise ValueError("Full-line Real capture requires a simulated snapshot")
        values = _real_values(snapshot.entries, _real_points(context, fields),
                              snapshot.capture_started_at, snapshot.capture_finished_at)
        if evidence.get("first_readbacks") != values:
            raise ValueError("Real readbacks changed during capture")


def _verify_vm_line(state, evidence):
    if evidence["input_state"]["usedline"] != state["usedline"]:
        raise ValueError("The VM Ready calculation must use the full ALL_MAIN line")


class ParticleCapture:
    def __init__(self, context, source, *, runtime_json=None, timeout_s=CAPTURE_TIMEOUT_S,
                 ready_timeout_s=10.0, pv_factory=None, poll_function=None):
        self.context = _backend_context(context, source)
        self.source = source
        self.runtime_json = runtime_json
        self.timeout = positive(timeout_s, "Particle capture timeout")
        self.ready_timeout = positive(ready_timeout_s, "VM ready timeout")
        _, self.state, _ = downstream_state(*downstream_sources(context))
        fields = quadrupole_fields(self.state)
        self.signature = build_profile_signature(context.profile, self.context.control_backend.name)
        self.reader, self.poll = pv_factory, poll_function
        if source == "vm":
            self.plan = vm_capture_points(self.context, fields)
        else:
            self.plan = _real_points(self.context, fields)
            self.reader = pv_factory or SimulatedRealReader(self.context, points=self.plan, state=self.state,
                                                            record_reads=False)
            self.poll = poll_function or (lambda _interval: None)

    def __call__(self, stop_requested=None):
        extra = {}
        if self.source == "vm":
            snapshot, extra["vm"] = capture_vm_plan(self.context, self.plan, runtime_json=self.runtime_json,
                timeout_s=self.timeout, ready_timeout_s=self.ready_timeout, pv_factory=self.reader,
                poll_function=self.poll, stop_requested=stop_requested)
            _verify_vm_line(self.state, extra["vm"])
        else:
            snapshot, extra["first_readbacks"] = self._capture_real_demo(stop_requested)
        evidence = {"schema": CAPTURE_SCHEMA, "source": self.source, "model_line": MODEL_LINE,
                    "snapshot_hash": digest(snapshot_to_dict(snapshot)), **extra}
        return snapshot, evidence

    def _capture_real_demo(self, stop_requested):
        context = self.context
        started = datetime.now(timezone.utc).isoformat()
        deadline = time.monotonic() + self.timeout
        samples = []
        for _ in range(2):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or (stop_requested and stop_requested()):
                raise RuntimeError("Real demo capture stopped or timed out")
            samples.append(sample_capture_points(self.plan, remaining, pv_factory=self.reader,
                                                poll_function=self.poll, stop_requested=stop_requested))
        finished = datetime.now(timezone.utc).isoformat()
        first, second = (_real_values(sample.entries, self.plan, started, finished) for sample in samples)
        if first != second:
            raise ValueError("Real readbacks changed during capture")
        snapshot = MachineStateSnapshot(
            snapshot_id="synthetic_real_particle_" + uuid.uuid4().hex, name="SYNTHETIC full-line real readbacks",
            operator_note="Simulated reader, two matching reads; no real hardware", machine_id=context.machine.id,
            machine_display_name=context.machine.display_name, backend="real",
            profile_schema_version=context.profile.schema_version, profile_signature=self.signature,
            capture_started_at=started, capture_finished_at=finished, capture_status="complete",
            hostname=socket.gethostname(), consistency="sequential_read_window", requested_count=len(self.plan),
            entries=samples[-1].entries,
        )
        return snapshot, first


def archive_particle_capture(directory, snapshot, evidence):
    directory.mkdir(parents=True, exist_ok=True)
    snapshot_path, evidence_path = directory / "machine_snapshot.json", directory / EVIDENCE_FILE
    write_runtime_state(evidence_path, evidence)
    save_snapshot(snapshot_path, snapshot)
    return snapshot_path, evidence_path
