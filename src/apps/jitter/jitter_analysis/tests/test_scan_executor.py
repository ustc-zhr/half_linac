from pathlib import Path
import sys
from datetime import datetime

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jitter_analysis.acquisition.scan_executor import KnobScanExecutor
from jitter_analysis.acquisition.plans import KnobScanPlan
from jitter_analysis.config.models import AnalysisFlags, KnobSpec, LimitSpec, ObjectSpec, SettleSpec
from jitter_analysis.domain.types import SampleRecord
from jitter_analysis.epics.client import ReadResult


def test_knob_scan_plan_keeps_values():
    plan = KnobScanPlan(
        knob_id="hc01_current",
        target_ids=["bpm01_x"],
        scan_values=[-0.1, 0.0, 0.1],
        settle_delay_sec=0.5,
        sample_count_per_step=10,
    )
    assert plan.scan_values[1] == 0.0
    assert plan.sample_count_per_step == 10


class _FakeClient:
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


class _FakeSampler:
    def __init__(self, client: _FakeClient, *, fail: bool = False) -> None:
        self.client = client
        self.fail = fail
        self.calls: list[tuple[int | None, int | None]] = []

    def sample_objects(self, objects, step_index=None, batch_index=None):
        self.calls.append((step_index, batch_index))
        if self.fail:
            raise RuntimeError("sample failed")
        return [
            SampleRecord(
                pv_id=obj.id,
                value=self.client.current_value,
                timestamp=datetime.now(),
                step_index=step_index,
                batch_index=batch_index,
            )
            for obj in objects
        ]


def _object_spec(object_id: str = "bpm01_x") -> ObjectSpec:
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


def _knob_spec() -> KnobSpec:
    return KnobSpec(
        id="k1",
        name="K1",
        group="ctrl",
        write_pv="PV:K1:SET",
        readback_pv="PV:K1:RB",
        unit="arb",
        access="rw",
        limits=LimitSpec(low=-5.0, high=5.0),
        step_hint=0.1,
        settle=SettleSpec(
            mode="fixed_delay",
            delay_sec=0.0,
            readback_tolerance=0.0,
            max_wait_sec=0.0,
        ),
    )


def test_knob_scan_executor_writes_targets_samples_after_settle_and_restores(monkeypatch):
    client = _FakeClient(initial_value=0.25)
    sampler = _FakeSampler(client)
    executor = KnobScanExecutor(client, sampler)
    sleeps = []
    monkeypatch.setattr(executor, "_sleep", lambda seconds: sleeps.append(float(seconds)))
    plan = KnobScanPlan(
        knob_id="k1",
        target_ids=["bpm01_x"],
        scan_values=[1.0, 2.0],
        settle_delay_sec=0.2,
        sample_count_per_step=2,
        restore_initial_value=True,
        per_step_interval_sec=0.1,
    )

    steps = executor.create_step_records(plan, _knob_spec(), [_object_spec()])

    assert client.write_calls == [
        ("PV:K1:SET", 1.0),
        ("PV:K1:SET", 2.0),
        ("PV:K1:SET", 0.25),
    ]
    assert sleeps == [0.2, 0.1, 0.2, 0.1]
    assert [step.target_value for step in steps] == [1.0, 2.0]
    assert [step.readback_value for step in steps] == [1.0, 2.0]
    assert sampler.calls == [(0, 0), (0, 1), (1, 2), (1, 3)]
    assert [sample.value for step in steps for sample in step.samples] == [1.0, 1.0, 2.0, 2.0]


def test_knob_scan_executor_rejects_targets_outside_limits():
    client = _FakeClient(initial_value=0.0)
    executor = KnobScanExecutor(client, _FakeSampler(client))
    plan = KnobScanPlan(
        knob_id="k1",
        target_ids=["bpm01_x"],
        scan_values=[6.0],
        settle_delay_sec=0.0,
        sample_count_per_step=1,
    )

    with pytest.raises(ValueError, match="outside limits"):
        executor.create_step_records(plan, _knob_spec(), [_object_spec()])

    assert client.write_calls == []


def test_knob_scan_executor_restores_initial_value_after_sampling_failure(monkeypatch):
    client = _FakeClient(initial_value=-0.5)
    executor = KnobScanExecutor(client, _FakeSampler(client, fail=True))
    monkeypatch.setattr(executor, "_sleep", lambda seconds: None)
    plan = KnobScanPlan(
        knob_id="k1",
        target_ids=["bpm01_x"],
        scan_values=[1.0],
        settle_delay_sec=0.0,
        sample_count_per_step=1,
        restore_initial_value=True,
    )

    with pytest.raises(RuntimeError, match="sample failed"):
        executor.create_step_records(plan, _knob_spec(), [_object_spec()])

    assert client.write_calls == [
        ("PV:K1:SET", 1.0),
        ("PV:K1:SET", -0.5),
    ]
