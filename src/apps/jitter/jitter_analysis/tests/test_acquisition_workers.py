from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jitter_analysis.acquisition import workers as workers_module
from jitter_analysis.config.models import AnalysisFlags, KnobSpec, LimitSpec, ObjectSpec, SettleSpec
from jitter_analysis.epics.client import ReadResult


class _FakeClock:
    def __init__(self, start_sec: float = 100.0) -> None:
        self.now = float(start_sec)

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


class _LatencySampler:
    def __init__(self, clock: _FakeClock, latency_sec: float) -> None:
        self.clock = clock
        self.latency_sec = float(latency_sec)
        self.sample_started_at: list[float] = []

    def sample_objects(self, objects, step_index=None, batch_index=None):
        self.sample_started_at.append(self.clock.now)
        self.clock.advance(self.latency_sec)
        return []


class _FakeStopEvent:
    def __init__(self, clock: _FakeClock) -> None:
        self.clock = clock
        self.waits: list[float] = []
        self._is_set = False

    def wait(self, seconds: float | None = None) -> bool:
        if seconds is not None and seconds > 0:
            self.waits.append(float(seconds))
            self.clock.advance(seconds)
        return self._is_set

    def is_set(self) -> bool:
        return self._is_set

    def set(self) -> None:
        self._is_set = True


class _SingleKnobClient:
    def __init__(self, initial_value: float = 0.0) -> None:
        self.current_value = float(initial_value)
        self.read_calls: list[str] = []
        self.write_calls: list[tuple[str, float]] = []

    def read(self, pv_name: str) -> ReadResult:
        self.read_calls.append(pv_name)
        return ReadResult(value=self.current_value, connected=True)

    def write(self, pv_name: str, value: float) -> bool:
        self.write_calls.append((pv_name, float(value)))
        self.current_value = float(value)
        return True


class _RandomClient:
    def __init__(self, initial_values: dict[str, float]) -> None:
        self.values = {name: float(value) for name, value in initial_values.items()}
        self.read_many_calls: list[list[str]] = []
        self.write_many_calls: list[list[tuple[str, float]]] = []

    def read_many(self, pv_names: list[str]) -> list[ReadResult]:
        self.read_many_calls.append(list(pv_names))
        return [
            ReadResult(value=self.values.get(pv_name, 0.0), connected=True)
            for pv_name in pv_names
        ]

    def write_many(self, pv_values: list[tuple[str, float]]) -> list[bool]:
        self.write_many_calls.append([(pv_name, float(value)) for pv_name, value in pv_values])
        for pv_name, value in pv_values:
            self.values[pv_name] = float(value)
        return [True] * len(pv_values)


def _object_spec(object_id: str) -> ObjectSpec:
    return ObjectSpec(
        id=object_id,
        name=object_id.upper(),
        group="diag",
        read_pv=f"PV:{object_id}:READ",
        unit="arb",
        precision=3,
        kind="monitor",
        access="ro",
        analysis=AnalysisFlags(),
    )


def _knob_spec(knob_id: str) -> KnobSpec:
    pv_name = f"PV:{knob_id}:SET"
    return KnobSpec(
        id=knob_id,
        name=knob_id.upper(),
        group="ctrl",
        write_pv=pv_name,
        readback_pv=pv_name,
        unit="arb",
        access="rw",
        limits=LimitSpec(low=-10.0, high=10.0),
        step_hint=0.1,
        settle=SettleSpec(
            mode="fixed_delay",
            delay_sec=0.0,
            readback_tolerance=0.0,
            max_wait_sec=0.0,
        ),
    )


def _install_fake_stop_event(worker, clock: _FakeClock) -> _FakeStopEvent:
    stop_event = _FakeStopEvent(clock)
    worker._stop_event = stop_event
    return stop_event


pytestmark = pytest.mark.skipif(workers_module.QtCore is None, reason="PyQt5 is required")


def test_knob_scan_worker_uses_deadline_scheduling_within_each_step(monkeypatch):
    clock = _FakeClock()
    sampler = _LatencySampler(clock, latency_sec=0.05)
    client = _SingleKnobClient(initial_value=0.0)

    monkeypatch.setattr(workers_module.time, "monotonic", clock.monotonic)

    worker = workers_module.KnobScanWorker(
        client,
        sampler,
        _knob_spec("k1"),
        [_object_spec("bpm01_x")],
        [1.0],
        0.0,
        3,
        0.2,
        False,
    )
    stop_event = _install_fake_stop_event(worker, clock)

    worker.run()

    assert client.write_calls == [("PV:k1:SET", 1.0)]
    assert stop_event.waits == pytest.approx([0.15, 0.15])
    assert sampler.sample_started_at == pytest.approx([100.0, 100.2, 100.4])


def test_multi_knob_random_worker_uses_deadline_scheduling_within_each_point(monkeypatch):
    clock = _FakeClock()
    sampler = _LatencySampler(clock, latency_sec=0.05)
    knob = _knob_spec("k1")
    client = _RandomClient({knob.write_pv: 0.0})

    monkeypatch.setattr(workers_module.time, "monotonic", clock.monotonic)

    worker = workers_module.MultiKnobRandomWorker(
        client,
        sampler,
        [knob],
        [_object_spec("bpm01_x")],
        [{knob.id: 1.0}],
        0.0,
        3,
        0.2,
        False,
    )
    stop_event = _install_fake_stop_event(worker, clock)

    worker.run()

    assert client.write_many_calls == [[(knob.write_pv, 1.0)]]
    assert stop_event.waits == pytest.approx([0.15, 0.15])
    assert sampler.sample_started_at == pytest.approx([100.0, 100.2, 100.4])
