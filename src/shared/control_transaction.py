from __future__ import annotations

import math
import time
from dataclasses import dataclass, replace
from typing import Callable, Iterable, Protocol

from .control_point import ControlPoint, snapshot_target_values
from .machine_profile.models import MachineProfile
from .machine_state import MachineStateSnapshot


class ControlClient(Protocol):
    def read(self, pv_name: str) -> float: ...
    def write(self, pv_name: str, value: float) -> None: ...


@dataclass(frozen=True)
class RestorePlanItem:
    point: ControlPoint
    target_value: float
    status: str
    detail: str = ""


@dataclass(frozen=True)
class RestorePlan:
    machine_id: str
    backend: str
    snapshot_id: str
    items: tuple[RestorePlanItem, ...]

    @property
    def ready_items(self) -> tuple[RestorePlanItem, ...]:
        return tuple(item for item in self.items if item.status == "ready")


@dataclass(frozen=True)
class TransactionItemResult:
    item: RestorePlanItem
    status: str
    initial_value: float | None = None
    actual_value: float | None = None
    detail: str = ""


@dataclass(frozen=True)
class TransactionResult:
    status: str
    items: tuple[TransactionItemResult, ...]


def build_restore_plan(
    profile: MachineProfile,
    snapshot: MachineStateSnapshot,
    control_points: Iterable[ControlPoint],
    selected_keys: Iterable[str] | None = None,
) -> RestorePlan:
    if snapshot.machine_id != profile.machine.id:
        raise ValueError("Snapshot and profile belong to different machines")
    targets = snapshot_target_values(snapshot)
    selected = set(targets if selected_keys is None else selected_keys)
    points = {point.key: point for point in control_points}
    items = []
    for key in sorted(selected):
        point = points.get(key)
        target = targets.get(key)
        issues = []
        if point is None:
            continue
        if snapshot.backend != "real" or point.setpoint_pv == "":
            issues.append("partial restore requires the real backend")
        if target is None:
            issues.append("snapshot has no usable numeric setting")
        issues.extend(point.configuration_issues)
        if target is not None and not point.limit.contains(target):
            issues.append(f"target is outside limit {point.limit.describe()}")
        items.append(
            RestorePlanItem(
                point,
                0.0 if target is None else target,
                "blocked" if issues else "ready",
                "; ".join(issues),
            )
        )
    return RestorePlan(profile.machine.id, snapshot.backend, snapshot.snapshot_id, tuple(items))


def execute_restore_plan(
    plan: RestorePlan,
    client: ControlClient,
    *,
    stop_requested: Callable[[], bool] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> TransactionResult:
    if plan.backend != "real":
        raise ValueError("Partial restore only executes real-machine plans")
    if not plan.items or any(item.status != "ready" for item in plan.items):
        raise ValueError("Every restore item must be ready before execution")
    should_stop = stop_requested or (lambda: False)
    results: list[TransactionItemResult] = []
    applied: list[tuple[int, float]] = []
    failed = False
    for index, item in enumerate(plan.items):
        if should_stop():
            results.append(TransactionItemResult(item, "not_executed", detail="cancelled"))
            failed = True
            break
        try:
            initial = _read_finite(client, item.point.setpoint_pv)
            client.write(item.point.setpoint_pv, item.target_value)
            applied.append((index, initial))
            actual = _poll_readback(item, client, monotonic, sleep, should_stop)
            results.append(TransactionItemResult(item, "verified", initial, actual))
        except Exception as exc:
            results.append(
                TransactionItemResult(item, "applied" if applied and applied[-1][0] == index else "not_executed", detail=str(exc))
            )
            failed = True
            break
    if failed:
        while len(results) < len(plan.items):
            results.append(TransactionItemResult(plan.items[len(results)], "not_executed"))
        for index, initial in reversed(applied):
            item = plan.items[index]
            try:
                client.write(item.point.setpoint_pv, initial)
                actual = _poll_value(
                    item.point.readback_pv or item.point.setpoint_pv,
                    initial,
                    item.point.tolerance,
                    item.point.timeout_s,
                    client,
                    monotonic,
                    sleep,
                    lambda: False,
                )
                results[index] = replace(results[index], status="rolled_back", actual_value=actual)
            except Exception as exc:
                results[index] = replace(results[index], status="restore_failed", detail=str(exc))
        status = "restore_failed" if any(r.status == "restore_failed" for r in results) else "rolled_back"
        return TransactionResult(status, tuple(results))
    return TransactionResult("verified", tuple(results))


def _poll_readback(item, client, monotonic, sleep, should_stop) -> float:
    if item.point.settle_s:
        sleep(item.point.settle_s)
    assert item.point.readback_pv is not None
    assert item.point.tolerance is not None
    return _poll_value(
        item.point.readback_pv,
        item.target_value,
        item.point.tolerance,
        item.point.timeout_s,
        client,
        monotonic,
        sleep,
        should_stop,
    )


def _poll_value(pv_name, target, tolerance, timeout_s, client, monotonic, sleep, should_stop):
    deadline = monotonic() + timeout_s
    while True:
        if should_stop():
            raise RuntimeError("cancelled")
        actual = _read_finite(client, pv_name)
        if abs(actual - target) <= tolerance:
            return actual
        if monotonic() >= deadline:
            raise RuntimeError(f"readback mismatch: target={target:g}, actual={actual:g}")
        sleep(min(0.05, max(0.0, deadline - monotonic())))


def _read_finite(client: ControlClient, pv_name: str) -> float:
    value = float(client.read(pv_name))
    if not math.isfinite(value):
        raise RuntimeError(f"{pv_name} returned a non-finite value")
    return value
