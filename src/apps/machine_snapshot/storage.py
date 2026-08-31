from __future__ import annotations

import csv
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

from half_linac.src.shared.machine_profile.app_runtime import resolve_app_runtime_paths
from half_linac.src.shared.machine_profile.models import AppContext
from half_linac.src.shared.machine_state import (
    MachineStateSnapshot,
    SnapshotDiffRow,
    load_snapshot,
    save_snapshot,
)
from half_linac.src.shared.runtime_state import write_runtime_state


SNAPSHOT_FILENAME = "snapshot.json"
RESTORE_RESULT_FILENAME = "restore_result.json"


@dataclass(frozen=True)
class SnapshotHistoryItem:
    path: Path
    snapshot: MachineStateSnapshot


@dataclass(frozen=True)
class SnapshotHistory:
    items: tuple[SnapshotHistoryItem, ...]
    unreadable_count: int


def save_capture_snapshot(
    app_dir: Path,
    context: AppContext,
    snapshot: MachineStateSnapshot,
) -> tuple[MachineStateSnapshot, Path]:
    paths = resolve_app_runtime_paths(app_dir, context)
    run_id = _unique_run_id(paths["runs_dir"], snapshot.snapshot_id)
    if run_id != snapshot.snapshot_id:
        snapshot = replace(snapshot, snapshot_id=run_id)
    run_dir = paths["runs_dir"] / run_id
    snapshot_path = run_dir / SNAPSHOT_FILENAME
    save_snapshot(snapshot_path, snapshot)
    relative_path = snapshot_path.relative_to(paths["runtime_dir"])
    write_runtime_state(
        paths["latest_metadata_path"],
        {
            "schema_version": "1",
            "run_id": snapshot.snapshot_id,
            "snapshot_path": relative_path.as_posix(),
            "name": snapshot.name,
            "captured_at": snapshot.capture_finished_at,
            "status": snapshot.capture_status,
            "ok_count": snapshot.ok_count,
            "failed_count": snapshot.failed_count,
            "skipped_count": snapshot.skipped_count,
        },
    )
    return snapshot, snapshot_path


def list_snapshot_history(
    app_dir: Path,
    context: AppContext,
) -> SnapshotHistory:
    runs_dir = resolve_app_runtime_paths(app_dir, context)["runs_dir"]
    items = []
    unreadable = 0
    if not runs_dir.is_dir():
        return SnapshotHistory((), 0)
    for snapshot_path in runs_dir.glob(f"*/{SNAPSHOT_FILENAME}"):
        try:
            snapshot = load_snapshot(snapshot_path)
        except Exception:
            unreadable += 1
            continue
        if (
            snapshot.machine_id != context.profile.machine.id
            or snapshot.backend != context.control_backend.name
        ):
            continue
        items.append(SnapshotHistoryItem(snapshot_path, snapshot))
    items.sort(
        key=lambda item: (
            item.snapshot.capture_finished_at,
            item.snapshot.snapshot_id,
        ),
        reverse=True,
    )
    return SnapshotHistory(tuple(items), unreadable)


def export_snapshot_json(path: Path | str, snapshot: MachineStateSnapshot) -> None:
    save_snapshot(path, snapshot)


def save_restore_result(app_dir: Path, context: AppContext, result, source_snapshot_id: str,
                        before_snapshot_id: str | None = None) -> Path:
    """Persist a compact audit record alongside the latest runtime metadata."""
    paths = resolve_app_runtime_paths(app_dir, context)
    destination = paths["runtime_dir"] / RESTORE_RESULT_FILENAME
    destination.parent.mkdir(parents=True, exist_ok=True)
    import json
    payload = {
        "source_snapshot_id": source_snapshot_id,
        "before_snapshot_id": before_snapshot_id,
        "backend": result.backend,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "items": [item.__dict__ for item in result.items],
    }
    destination.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return destination


def export_comparison_csv(
    path: Path | str,
    snapshot_a: MachineStateSnapshot,
    snapshot_b: MachineStateSnapshot,
    rows: Iterable[SnapshotDiffRow],
) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            (
                "machine_id",
                "snapshot_a_id",
                "snapshot_b_id",
                "backend_a",
                "backend_b",
                "element_id",
                "element_kind",
                "logical_channel",
                "state_class",
                "value_a",
                "value_b",
                "delta",
                "unit",
                "status",
                "quality_a",
                "quality_b",
                "source_timestamp_a",
                "source_timestamp_b",
                "pv_a",
                "pv_b",
                "mapping_changed",
                "detail",
            )
        )
        for row in rows:
            entry = row.entry_a or row.entry_b
            assert entry is not None
            writer.writerow(
                (
                    snapshot_a.machine_id,
                    snapshot_a.snapshot_id,
                    snapshot_b.snapshot_id,
                    snapshot_a.backend,
                    snapshot_b.backend,
                    entry.element_id,
                    entry.element_kind,
                    entry.logical_channel,
                    entry.state_class.value,
                    _value(row.entry_a),
                    _value(row.entry_b),
                    "" if row.delta is None else row.delta,
                    entry.unit or "",
                    row.status.value,
                    _quality(row.entry_a),
                    _quality(row.entry_b),
                    _timestamp(row.entry_a),
                    _timestamp(row.entry_b),
                    row.entry_a.pv_name if row.entry_a else "",
                    row.entry_b.pv_name if row.entry_b else "",
                    str(row.mapping_changed).lower(),
                    row.detail,
                )
            )


def _unique_run_id(runs_dir: Path, requested: str) -> str:
    if not (runs_dir / requested).exists():
        return requested
    counter = 2
    while (runs_dir / f"{requested}_{counter:02d}").exists():
        counter += 1
    return f"{requested}_{counter:02d}"


def _value(entry) -> object:
    return "" if entry is None or entry.value is None else entry.value


def _quality(entry) -> str:
    return "" if entry is None else entry.quality.value


def _timestamp(entry) -> object:
    return "" if entry is None or entry.source_timestamp is None else entry.source_timestamp
