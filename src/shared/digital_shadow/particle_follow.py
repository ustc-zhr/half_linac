"""Track the newest captured working point with one in-flight particle calculation."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
from time import monotonic

from half_linac.src.shared.machine_state import MachineStateSnapshot
from half_linac.src.shared.runtime_state import write_runtime_state
from half_linac.src.virtual_machine.beam_source import unquote_elegant_string

from .beam_state import BeamParticles, load_beam_state_archive, save_beam_state
from .models import positive
from .particle_capture import POLL_INTERVAL_S, archive_particle_capture
from .particle_model import ParticleModelRequest, _read_impact_handoff, downstream_sources, downstream_state, run_downstream
from .session import digest, file_digest


FOLLOW_FILE = "follow.json"
FOLLOW_EVENT = "digital_shadow_follow_event_v1"
_FAILURES = (RuntimeError, ValueError, TypeError, KeyError, IndexError, OSError)


def _now():
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class WorkingPoint:
    snapshot: MachineStateSnapshot
    evidence: dict
    input_key: str
    available_at: float


def _freeze_inputs(context, root, beam_path, handoff_path):
    source, template = downstream_sources(context)
    _, state, boundary = downstream_state(source, template)
    beam, _ = load_beam_state_archive(beam_path)
    if beam.boundary != boundary or not isinstance(beam.payload, BeamParticles) or beam.bunch_charge_c is None:
        raise ValueError("Follow requires saved particles with charge at ALL_MAIN / C#1 entrance")
    handoff = _read_impact_handoff(handoff_path, beam)
    model = root / "fixed-inputs"
    model.mkdir(parents=True, exist_ok=False)
    lattice, ele = model / "source.lte", model / "tracking-template.ele"
    shutil.copyfile(source, lattice)
    shutil.copyfile(template, ele)
    if file_digest(lattice) != boundary.lattice_sha256:
        raise ValueError("The source lattice changed while starting Follow")
    resources = {unquote_elegant_string(value) for name in set(state["usedline"])
                 for key, value in state["lattice"][name].items() if key in {"WAKEFILE", "ZWAKEFILE", "TRWAKEFILE"}}
    for value in resources:
        name = Path(value)
        if name.is_absolute() or ".." in name.parts:
            raise ValueError(f"Follow requires a wake path relative to its source lattice: {value}")
        target = model / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source.parent / name, target)
    environment = os.environ.copy()
    if environment.get("RPN_DEFNS"):
        definitions = model / "defns.rpn"
        shutil.copyfile(Path(environment["RPN_DEFNS"]).expanduser(), definitions)
        environment["RPN_DEFNS"] = str(definitions)
    frozen_beam = save_beam_state(beam, model / "entrance.json")
    frozen_handoff = model / "handoff.json"
    write_runtime_state(frozen_handoff, handoff)
    backend = context.model_backend
    frozen_context = replace(context, model_backend=replace(backend, config={
        **backend.config, "source_lattice": lattice, "tracking_ini_ele": ele,
    }))
    files = {path.relative_to(model).as_posix(): file_digest(path) for path in sorted(model.rglob("*"))
             if path.is_file() and not path.name.startswith(".")}
    return frozen_context, environment, frozen_beam, frozen_handoff, files


def follow_working_points(capture, calculate, stop, *, input_hash, poll_interval=POLL_INTERVAL_S, report):
    """Schedule reads independently of tracking. Keep at most one unstarted snapshot."""
    interval = positive(poll_interval, "Follow poll interval")
    pending = active_point = published = None
    capture_future = model_future = None
    capture_error = model_error = None
    last_capture = latest_key = None
    skipped = launches = 0
    next_poll = last_finished = 0.0

    def timed_capture():
        snapshot, evidence = capture(stop.is_set)
        return snapshot, evidence, monotonic()

    def timed_calculation(point, index, skipped_count, previous_finished):
        started = monotonic()
        delay = started - previous_finished if previous_finished else None
        queued = bool(previous_finished and point.available_at <= previous_finished)
        try:
            result = calculate(point, index, skipped_count, delay, queued)
        except _FAILURES as error:
            return None, error, monotonic()
        return result, None, monotonic()

    with ThreadPoolExecutor(max_workers=1) as sampler, ThreadPoolExecutor(max_workers=1) as model:
        while not stop.is_set():
            changed = False
            if capture_future is not None and capture_future.done():
                changed = True
                try:
                    snapshot, evidence, available_at = capture_future.result()
                    values = {entry.key: float(entry.value) or 0.0 for entry in snapshot.entries}
                    key = digest({"fixed_inputs": input_hash, "values": values})
                    last_capture, latest_key, capture_error = snapshot.capture_finished_at, key, None
                    if pending and pending.input_key == key:
                        available_at = pending.available_at
                    next_point = WorkingPoint(snapshot, evidence, key, available_at)
                    reference = active_point.input_key if active_point else published and published["input_key"]
                    if active_point is None and published and key == published["input_key"]:
                        model_error = None
                    if pending and pending.input_key != key:
                        skipped += 1
                    pending = None if key == reference else next_point
                except _FAILURES as error:
                    skipped += int(pending is not None)
                    pending, latest_key, capture_error = None, None, str(error)
                capture_future = None

            if model_future is not None and model_future.done():
                changed = True
                try:
                    result, error, last_finished = model_future.result()
                    if error is not None:
                        raise error
                    if result["status"] != "complete":
                        raise RuntimeError(f"Tracking finished with status {result['status']}")
                    published = result
                    model_error = None
                except _FAILURES as error:
                    model_error = str(error)
                model_future = active_point = None

            if pending and published and model_future is None and pending.input_key == published["input_key"]:
                pending, model_error = None, None

            if stop.is_set():
                break
            if model_future is None and pending is not None:
                changed = True
                active_point, pending = pending, None
                launches += 1
                model_future = model.submit(timed_calculation, active_point, launches, skipped, last_finished)

            if capture_future is None and monotonic() >= next_poll:
                next_poll = monotonic() + interval
                capture_future = sampler.submit(timed_capture)

            if changed:
                error = capture_error or model_error
                state = "stale" if error else "tracking" if active_point else "idle"
                report({"state": state, "error": error, "latest_capture_at": last_capture,
                        "active_snapshot_id": active_point.snapshot.snapshot_id if active_point else None,
                        "pending_snapshot_id": pending.snapshot.snapshot_id if pending else None,
                        "result": published, "result_stale": published is not None and (
                            bool(error) or published["input_key"] != latest_key), "skipped_working_points": skipped})
            stop.wait(0.025)


def run_follow(context, capture, root, *, beam_path, handoff_path, stop, emit, poll_interval=POLL_INTERVAL_S,
               model_timeout=120.0):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    model_timeout = positive(model_timeout, "Model timeout")
    poll_interval = positive(poll_interval, "Follow poll interval")
    context, environment, beam, handoff, files = _freeze_inputs(context, root, beam_path, handoff_path)
    fixed_hash = digest(files)
    session = {"schema": FOLLOW_EVENT, "source": capture.source, "started_at": _now(), "state": "waiting",
               "fixed_inputs": files, "fixed_input_hash": fixed_hash, "result": None}

    def report(update):
        session.update(update)
        write_runtime_state(root / FOLLOW_FILE, session)
        emit({"schema": FOLLOW_EVENT, "source": capture.source, **update})

    def calculate(point, index, skipped, dispatch_delay, queued):
        started, timer = _now(), monotonic()
        directory = root / "cycles" / f"cycle_{index:06d}"
        metrics = {"input_key": point.input_key, "snapshot_id": point.snapshot.snapshot_id, "computed_from": str(beam),
                   "capture_started_at": point.snapshot.capture_started_at,
                   "capture_finished_at": point.snapshot.capture_finished_at, "calculation_started_at": started,
                   "dispatch_delay_s": dispatch_delay, "queued_at_previous_completion": queued,
                   "skipped_working_points": skipped, "source": capture.source}
        if capture.source == "vm":
            status = point.evidence["vm"]["status"]
            metrics.update(vm_session=status["session"], vm_version=status["input_version"],
                           vm_calculation=status["calculation"], source_completed_at=status["completed_at"])
        try:
            snapshot, evidence = archive_particle_capture(directory, point.snapshot, point.evidence)
            for name, sha256 in files.items():
                if file_digest(beam.parent / name) != sha256:
                    raise ValueError(f"Frozen Follow input changed: {name}")
            request = ParticleModelRequest(beam_source="archived", beam_path=str(beam), handoff_path=str(handoff),
                parameter_source="snapshot", snapshot_path=str(snapshot), capture_evidence_path=str(evidence),
                model_timeout_s=model_timeout)
            result = run_downstream(context, request, directory / "shadow_model", environment=environment,
                                    stop_requested=stop.is_set)
            metrics.update(run_dir=result["run_dir"], status=result["status"], end_particles=result["end_particles"])
        except KeyboardInterrupt:
            metrics.update(status="interrupted")
            raise
        except _FAILURES as error:
            metrics.update(status="failed", error=str(error))
            raise
        finally:
            metrics.update(calculation_finished_at=_now(), calculation_s=monotonic() - timer)
            if metrics["status"] == "complete" and not stop.is_set():
                metrics["published_at"] = _now()
            write_runtime_state(directory / "cycle.json", metrics)
        return metrics

    report({"state": "waiting"})
    try:
        follow_working_points(capture, calculate, stop, input_hash=fixed_hash, poll_interval=poll_interval, report=report)
    finally:
        report({"state": "stopped" if stop.is_set() else "failed", "finished_at": _now(),
                "active_snapshot_id": None, "pending_snapshot_id": None})
    return session
