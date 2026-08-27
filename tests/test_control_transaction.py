from __future__ import annotations

from dataclasses import replace

import pytest

from half_linac.src.shared.control_point import ControlPoint
from half_linac.src.shared.control_transaction import (
    RestorePlan,
    RestorePlanItem,
    build_restore_plan,
    execute_restore_plan,
)
from half_linac.src.shared.machine_profile import load_profile
from half_linac.src.shared.machine_profile.limits import LimitRange
from half_linac.src.shared.machine_state import (
    MachineStateSnapshot,
    SampleQuality,
    SnapshotEntry,
    StateClass,
)


def _point(name: str) -> ControlPoint:
    return ControlPoint(name, "corr", 1, "kick", f"{name}:SP", "kick_readback", f"{name}:RB", "rad", LimitRange(-1, 1, "rad"), 0.01)


class FakeClient:
    def __init__(self, values, fail_write=""):
        self.values = dict(values)
        self.fail_write = fail_write
        self.writes = []

    def read(self, pv_name):
        return self.values[pv_name]

    def write(self, pv_name, value):
        self.writes.append((pv_name, value))
        if pv_name == self.fail_write:
            raise RuntimeError("write failed")
        self.values[pv_name] = value
        self.values[pv_name.replace(":SP", ":RB")] = value


def _plan() -> RestorePlan:
    return RestorePlan("half", "real", "snapshot", tuple(RestorePlanItem(_point(name), target, "ready") for name, target in (("A", 0.2), ("B", -0.3))))


def test_restore_transaction_writes_and_polls_independent_readbacks() -> None:
    client = FakeClient({"A:SP": 0.0, "A:RB": 0.0, "B:SP": 0.0, "B:RB": 0.0})
    result = execute_restore_plan(_plan(), client)
    assert result.status == "verified"
    assert [item.status for item in result.items] == ["verified", "verified"]


def test_restore_transaction_rolls_back_in_reverse_after_failure() -> None:
    client = FakeClient({"A:SP": 0.1, "A:RB": 0.1, "B:SP": 0.2, "B:RB": 0.2}, fail_write="B:SP")
    result = execute_restore_plan(_plan(), client)
    assert result.status == "rolled_back"
    assert [item.status for item in result.items] == ["rolled_back", "not_executed"]
    assert client.writes[-1] == ("A:SP", 0.1)


def test_restore_transaction_rejects_vm_or_blocked_plans() -> None:
    with pytest.raises(ValueError, match="real-machine"):
        execute_restore_plan(replace(_plan(), backend="vm"), FakeClient({}))
    blocked = replace(_plan(), items=(replace(_plan().items[0], status="blocked"),))
    with pytest.raises(ValueError, match="ready"):
        execute_restore_plan(blocked, FakeClient({}))


def test_profile_restore_plan_rejects_vm_backend() -> None:
    profile = load_profile("half")
    entry = SnapshotEntry(
        "XC00/kick", "XC00", "corr", 1, "XC00", "kick", "HALF:XC00",
        StateClass.SETTING, 0.1, "number", "rad", 1.0, "now", 0, 0, 1,
        SampleQuality.OK,
    )
    snapshot = MachineStateSnapshot(
        "snapshot", "snapshot", "", "half", "HALF Linac", "vm", "1", "sig",
        "start", "finish", "complete", "host", "best_effort", 1, (entry,),
    )
    point = replace(_point("XC00"), logical_channel="kick", setpoint_pv="HALF:XC00", readback_channel=None, readback_pv=None, tolerance=None, limit=LimitRange(unit="rad"))

    plan = build_restore_plan(profile, snapshot, (point,))

    assert plan.items[0].status == "blocked"
    assert "requires the real backend" in plan.items[0].detail
