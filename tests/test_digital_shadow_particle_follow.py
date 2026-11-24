from dataclasses import replace
from datetime import datetime, timezone
from threading import Event, Timer
from types import SimpleNamespace

import pytest

from half_linac.src.shared.digital_shadow.particle_capture import ParticleCapture, verify_particle_capture
from half_linac.src.shared.digital_shadow.particle_follow import follow_working_points
from half_linac.src.shared.elegant_runtime import run_elegant_input
from half_linac.src.shared.machine_profile.loader import load_model_context
from half_linac.src.shared.runtime_state import write_runtime_state
from half_linac.src.virtual_machine.workbench_data import input_version, observation_dir


def test_real_capture_uses_all_readbacks_and_rejects_changed_second_read(monkeypatch):
    def no_ca(*_args, **_kwargs):
        pytest.fail("Simulated Real capture must not access CA")

    monkeypatch.setattr("epics.PV", no_ca)
    monkeypatch.setattr("epics.ca.poll", no_ca)
    context = load_model_context("half")
    capture = ParticleCapture(context, "demo-real")
    snapshot, evidence = capture()
    assert snapshot.snapshot_id.startswith("synthetic_real_particle_")
    assert len(snapshot.entries) == 63
    assert all(entry.logical_channel == "K1_total" for entry in snapshot.entries)
    verify_particle_capture(context, capture.state, snapshot, evidence)
    with pytest.raises(ValueError, match="does not match"):
        verify_particle_capture(context, capture.state, replace(snapshot, snapshot_id="different"), evidence)

    read = capture.reader
    name = capture.plan[0].endpoint.pv_name
    count = 0

    def changing_factory(pv):
        nonlocal count
        if pv == name:
            count += 1
            if count == 2:
                value, unit = read.values[name]
                read.values[name] = (value + 1, unit)
        return read(pv)

    capture.reader = changing_factory
    with pytest.raises(ValueError, match="changed during capture"):
        capture()


@pytest.mark.parametrize("fault", [None, "changed", "missing"])
def test_full_line_vm_capture_binds_all_required_k1_to_a_ready_calculation(tmp_path, fault):
    runtime = tmp_path / "runtime.json"
    context = load_model_context("half")
    readbacks = {}
    now = datetime.now(timezone.utc)

    class PV:
        connected = True
        count = 1
        def __init__(self, name):
            self.name = name

        def get_with_metadata(self, **_options):
            if fault == "changed" and self.name == first:
                write_runtime_state(observation_dir(runtime) / "status.json", {**status, "phase": "Calculating"})
            if fault == "missing" and self.name == first:
                return None
            return {"timestamp": now.timestamp(), "value": readbacks[self.name], "units": "1/m^2", "count": 1,
                    "severity": 0, "status": 0}

    capture = ParticleCapture(context, "vm", runtime_json=runtime, pv_factory=PV,
                              poll_function=lambda _interval: None, ready_timeout_s=0.05)
    points, _units = capture.plan
    first = points[0].endpoint.pv_name
    readbacks.update({point.endpoint.pv_name: float(capture.state["lattice"][point.endpoint.element_id]["K1"])
                      for point in points})
    write_runtime_state(runtime, capture.state)
    version = input_version(capture.state)
    status = {"phase": "Ready", "session": "test-vm", "calculation": 3, "input_version": version,
              "result_version": version, "elapsed": 1.0, "completed_at": now.isoformat(), "publication": {"rms": True}}
    write_runtime_state(observation_dir(runtime) / "status.json", status)
    write_runtime_state(observation_dir(runtime) / "result.json", {
        "input_version": version, "input_state": capture.state, "session": "test-vm", "calculation": 3, "screens": {},
    })
    if fault == "changed":
        with pytest.raises(RuntimeError, match="not Ready"):
            capture()
    elif fault == "missing":
        with pytest.raises(ValueError, match="unusable VM sample"):
            capture()
    else:
        snapshot, evidence = capture()
        assert len(snapshot.entries) == 63
        assert evidence["vm"]["status"]["input_version"] == version
        verify_particle_capture(context, capture.state, snapshot, evidence)


def _snapshot(value):
    return SimpleNamespace(snapshot_id=str(value), capture_finished_at="captured",
                           entries=(SimpleNamespace(key="QT02/K1", value=value),))


def _exercise(capture, calculate, report, stop):
    watchdog = Timer(3.0, stop.set)
    watchdog.start()
    try:
        follow_working_points(capture, calculate, stop, input_hash="fixed", poll_interval=0.01, report=report)
    finally:
        watchdog.cancel()


def test_follow_merges_pending_inputs_and_does_not_repeat_identical_settings():
    stop, release = Event(), Event()
    values = iter((1, 2, 3))
    launched, elapsed = [], []
    repeats = 0

    def capture(_stopped):
        return _snapshot(next(values, 3)), {}

    def calculate(point, _index, _skipped, dispatch_delay, queued):
        launched.append(point.snapshot.snapshot_id)
        if point.snapshot.snapshot_id == "1":
            release.wait(timeout=1)
        else:
            assert queued
            elapsed.append(dispatch_delay)
        return {"status": "complete", "snapshot_id": point.snapshot.snapshot_id, "input_key": point.input_key}

    def report(event):
        nonlocal repeats
        if event["pending_snapshot_id"] == "3":
            assert event["skipped_working_points"] == 1
            release.set()
        if (event["result"] or {}).get("snapshot_id") == "3":
            repeats += 1
            if repeats == 4:
                stop.set()

    _exercise(capture, calculate, report, stop)
    assert launched == ["1", "3"] and repeats == 4
    assert len(elapsed) == 1 and elapsed[0] <= 1


@pytest.mark.parametrize("fault", ["capture rejected", "tracking timed out"])
def test_follow_leaves_previous_result_visible_after_failure(fault):
    stop = Event()
    current = 1
    seen = []

    def capture(_stopped):
        if current == 2 and fault == "capture rejected":
            raise ValueError(fault)
        return _snapshot(current), {}

    def calculate(point, *_args):
        if point.snapshot.snapshot_id == "2":
            raise RuntimeError(fault)
        return {"status": "complete", "snapshot_id": "1", "input_key": point.input_key}

    def report(event):
        nonlocal current
        if event["result"]:
            current = 2
        if event["error"]:
            seen.append(event)
            stop.set()

    _exercise(capture, calculate, report, stop)
    assert len(seen) == 1 and seen[0]["error"] == fault
    assert seen[0]["result_stale"] and seen[0]["result"]["snapshot_id"] == "1"


def test_cancellable_elegant_still_enforces_model_timeout(tmp_path, monkeypatch):
    executable = tmp_path / "elegant"
    executable.write_text("#!/bin/sh\nexec /bin/sleep 20\n")
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(RuntimeError, match="timed out"):
        run_elegant_input("tracking.ele", "tracking.log", workdir=tmp_path,
                          timeout_s=0.05, stop_requested=lambda: False)
