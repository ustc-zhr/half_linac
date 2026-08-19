from __future__ import annotations

import math
import json
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from half_linac.src.shared.setpoint_transfer import TransferPlan


class PvClient(Protocol):
    def read(self, pv_name: str) -> float: ...
    def write(self, pv_name: str, value: float) -> None: ...


@dataclass(frozen=True)
class AppliedSetpoint:
    element_id: str
    pv_name: str
    old_value: float
    target_value: float
    actual_value: float


@dataclass(frozen=True)
class RestoredSetpoint:
    element_id: str
    pv_name: str
    value_before_restore: float
    restored_value: float
    actual_value: float


class TransferExecutionError(RuntimeError):
    def __init__(
        self,
        message: str,
        completed: tuple[AppliedSetpoint, ...],
        failed_element_id: str,
    ):
        super().__init__(message)
        self.completed = completed
        self.failed_element_id = failed_element_id


class RestoreExecutionError(RuntimeError):
    def __init__(self, message, completed, failed_element_id):
        super().__init__(message)
        self.completed = tuple(completed)
        self.failed_element_id = failed_element_id


def find_restore_conflicts(result, client: PvClient, *, tolerance: float = 1.0e-6):
    conflicts = []
    for item in reversed(tuple(result)):
        try:
            current = float(client.read(item.pv_name))
        except Exception as exc:
            conflicts.append((item.element_id, None, str(exc)))
            continue
        if not math.isfinite(current) or abs(current - item.actual_value) > tolerance:
            conflicts.append((item.element_id, current, "changed since Apply"))
    return tuple(conflicts)


def execute_restore(result, client: PvClient, *, tolerance: float = 1.0e-6):
    completed = []
    for item in reversed(tuple(result)):
        try:
            before = float(client.read(item.pv_name))
            if not math.isfinite(before):
                raise RuntimeError("current value is non-finite")
            client.write(item.pv_name, item.old_value)
            actual = float(client.read(item.pv_name))
            if not math.isfinite(actual) or abs(actual - item.old_value) > tolerance:
                raise RuntimeError(
                    f"readback mismatch: target={item.old_value:g}, actual={actual:g}"
                )
            completed.append(
                RestoredSetpoint(
                    item.element_id, item.pv_name, before, item.old_value, actual
                )
            )
        except Exception as exc:
            raise RestoreExecutionError(
                f"{item.element_id} restore failed: {exc}", completed, item.element_id
            ) from exc
    return tuple(completed)


def save_transfer_transaction(path, *, machine_id, backend, result):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "machine": machine_id,
        "backend": backend,
        "items": [
            {
                "element_id": item.element_id,
                "pv_name": item.pv_name,
                "old_value": item.old_value,
                "target_value": item.target_value,
                "readback_value": item.actual_value,
            }
            for item in result
        ],
    }
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def preflight_transfer_plan(plan: TransferPlan, client: PvClient) -> None:
    """Verify every selected PV is readable immediately before a write."""
    if plan.target_backend not in {"vm", "real"}:
        raise ValueError("Only VM and Real control backends are supported.")
    if not plan.writable_items or any(item.status != "ready" for item in plan.items):
        raise ValueError("Every selected transfer item must be ready.")
    for item in plan.writable_items:
        try:
            value = float(client.read(item.pv_name))
        except Exception as exc:
            raise TransferExecutionError(
                f"{item.element_id} preflight failed: {exc}", (), item.element_id
            ) from exc
        if not math.isfinite(value):
            raise TransferExecutionError(
                f"{item.element_id} preflight failed: current value is non-finite",
                (), item.element_id,
            )


def append_execution_log(
    path: str | Path,
    plan: TransferPlan,
    result=(),
    *,
    error: str = "",
    failed_element_id: str = "",
) -> None:
    """Append a JSONL audit record without changing machine or runtime files."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "target_backend": plan.target_backend,
        "items": [
            {
                "element_id": item.element_id,
                "field": item.field,
                "pv_name": item.pv_name,
                "design_value": item.design_value,
                "current_value": item.current_value,
                "target_value": item.target_value,
                "status": item.status,
            }
            for item in plan.items
        ],
        "result": [
            {
                "element_id": item.element_id,
                "pv_name": item.pv_name,
                "old_value": item.old_value,
                "target_value": item.target_value,
                "actual_value": item.actual_value,
            }
            for item in result
        ],
        "applied": [item.element_id for item in result],
        "failed": failed_element_id or None,
        "not_executed": [
            item.element_id
            for item in plan.writable_items
            if item.element_id not in {applied.element_id for applied in result}
            and item.element_id != failed_element_id
        ],
        "error": error,
    }
    with destination.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=True) + "\n")


def execute_transfer_plan(
    plan: TransferPlan,
    client: PvClient,
    *,
    tolerance: float = 1.0e-6,
) -> tuple[AppliedSetpoint, ...]:
    if plan.target_backend not in {"vm", "real"}:
        raise ValueError("Only VM and Real control backends are supported.")
    preflight_transfer_plan(plan, client)

    snapshot: dict[str, float] = {}
    for item in plan.writable_items:
        try:
            old_value = float(client.read(item.pv_name))
            if not math.isfinite(old_value):
                raise RuntimeError("current value is non-finite")
            snapshot[item.pv_name] = old_value
        except Exception as exc:
            raise TransferExecutionError(
                f"{item.element_id} snapshot failed: {exc}", (), item.element_id
            ) from exc

    completed: list[AppliedSetpoint] = []
    for item in plan.writable_items:
        try:
            old_value = snapshot[item.pv_name]
            target = float(item.target_value)
            client.write(item.pv_name, target)
            actual = float(client.read(item.pv_name))
            if not math.isfinite(actual) or abs(actual - target) > tolerance:
                raise RuntimeError(f"readback mismatch: target={target:g}, actual={actual:g}")
            completed.append(AppliedSetpoint(item.element_id, item.pv_name, old_value, target, actual))
        except Exception as exc:
            raise TransferExecutionError(
                f"{item.element_id} failed: {exc}", tuple(completed), item.element_id
            ) from exc
    return tuple(completed)
