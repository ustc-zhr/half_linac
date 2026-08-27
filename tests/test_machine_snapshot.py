from __future__ import annotations

import csv
import re
from dataclasses import replace
from pathlib import Path

from half_linac.src.apps.machine_snapshot.storage import (
    export_comparison_csv,
    list_snapshot_history,
    save_capture_snapshot,
)
from half_linac.src.shared.machine_profile import load_app_context
from half_linac.src.shared.machine_profile.app_runtime import resolve_app_runtime_paths
from half_linac.src.shared.machine_state import (
    DiffStatus,
    MachineStateSnapshot,
    SampleQuality,
    SnapshotDiffRow,
    SnapshotEntry,
    StateClass,
    load_snapshot,
)
from half_linac.src.shared.runtime_state import read_runtime_state
from half_linac.src.shared.machine_profile.validation import validate_machine_profile


def _entry(value: float = 1.0) -> SnapshotEntry:
    return SnapshotEntry(
        key="Q01/current_set",
        element_id="Q01",
        element_kind="quad",
        element_order=1,
        display_name="Q01",
        logical_channel="current_set",
        pv_name="TEST:Q01:SET",
        state_class=StateClass.SETTING,
        value=value,
        value_type="number",
        unit="A",
        source_timestamp=100.0,
        received_at="2026-08-27T00:00:00+00:00",
        alarm_status=0,
        alarm_severity=0,
        native_count=1,
        quality=SampleQuality.OK,
    )


def _snapshot(snapshot_id: str, finished: str, value: float = 1.0) -> MachineStateSnapshot:
    return MachineStateSnapshot(
        snapshot_id=snapshot_id,
        name=snapshot_id,
        operator_note="note",
        machine_id="half",
        machine_display_name="HALF Linac",
        backend="vm",
        profile_schema_version="1",
        profile_signature="signature",
        capture_started_at="2026-08-27T00:00:00+00:00",
        capture_finished_at=finished,
        capture_status="complete",
        hostname="test-host",
        consistency="best_effort",
        requested_count=1,
        entries=(_entry(value),),
    )


def test_machine_snapshot_app_context_loads_only_shared_control_points() -> None:
    context = load_app_context("machine_snapshot", machine_id="half", control_backend="vm")
    assert context.app_name == "machine_snapshot"
    assert set(context.profile.workflows) == {"control_points"}
    assert "real" in context.profile.workflows["control_points"]["backends"]
    assert context.control_backend.name == "vm"

    report = validate_machine_profile("irfel")
    assert report.get_check("app:machine_snapshot").status == "pass"
    assert report.get_check("commissioning:machine_snapshot") is None


def test_save_history_latest_and_collision(tmp_path) -> None:
    context = load_app_context("machine_snapshot", machine_id="half", control_backend="vm")
    older, older_path = save_capture_snapshot(
        tmp_path,
        context,
        _snapshot("snapshot_test", "2026-08-27T00:00:01+00:00"),
    )
    newer, newer_path = save_capture_snapshot(
        tmp_path,
        context,
        _snapshot("snapshot_test", "2026-08-27T00:00:02+00:00", 2.0),
    )

    assert older.snapshot_id == "snapshot_test"
    assert newer.snapshot_id == "snapshot_test_02"
    assert load_snapshot(older_path) == older
    assert load_snapshot(newer_path) == newer
    history = list_snapshot_history(tmp_path, context)
    assert [item.snapshot.snapshot_id for item in history.items] == [
        "snapshot_test_02",
        "snapshot_test",
    ]
    latest = read_runtime_state(
        resolve_app_runtime_paths(tmp_path, context)["latest_metadata_path"]
    )
    assert latest["run_id"] == "snapshot_test_02"
    assert latest["snapshot_path"].endswith("snapshot_test_02/snapshot.json")


def test_history_ignores_unreadable_snapshot_and_csv_is_stable(tmp_path) -> None:
    context = load_app_context("machine_snapshot", machine_id="half", control_backend="vm")
    snapshot_a, _ = save_capture_snapshot(
        tmp_path,
        context,
        _snapshot("a", "2026-08-27T00:00:01+00:00"),
    )
    snapshot_b = replace(
        _snapshot("b", "2026-08-27T00:00:02+00:00", 2.5),
        entries=(_entry(2.5),),
    )
    broken = resolve_app_runtime_paths(tmp_path, context)["runs_dir"] / "broken" / "snapshot.json"
    broken.parent.mkdir(parents=True)
    broken.write_text("not json", encoding="utf-8")

    history = list_snapshot_history(tmp_path, context)
    assert history.unreadable_count == 1
    assert len(history.items) == 1

    row = SnapshotDiffRow(
        key="Q01/current_set",
        entry_a=snapshot_a.entries[0],
        entry_b=snapshot_b.entries[0],
        delta=1.5,
        status=DiffStatus.CHANGED,
    )
    csv_path = tmp_path / "comparison.csv"
    export_comparison_csv(csv_path, snapshot_a, snapshot_b, (row,))
    with csv_path.open(encoding="utf-8", newline="") as stream:
        records = list(csv.reader(stream))
    assert records[0][0:5] == [
        "machine_id",
        "snapshot_a_id",
        "snapshot_b_id",
        "backend_a",
        "backend_b",
    ]
    assert records[1][0:5] == ["half", "a", "b", "vm", "vm"]
    assert records[1][11] == "1.5"


def test_qt_window_is_read_only_and_launcher_entry_exists(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication

    from half_linac.src.apps.machine_snapshot.main import (
        CaptureDialog,
        MachineSnapshotWindow,
    )

    app = QApplication.instance() or QApplication([])
    context = load_app_context("machine_snapshot", machine_id="half", control_backend="real")
    window = MachineSnapshotWindow(context=context, app_dir=tmp_path)
    dialog = CaptureDialog(window)

    assert not hasattr(window, "readonly_label")
    assert dialog.settings_check.isChecked()
    assert dialog.readbacks_check.isChecked()
    assert not dialog.observations_check.isChecked()
    assert not window.export_csv_button.isEnabled()

    repo_root = Path(__file__).resolve().parents[1]
    launcher_source = (repo_root / "src/apps/launcher/main.py").read_text(encoding="utf-8")
    assert '"machine_snapshot"' in launcher_source
    assert 'ROOT / "src/apps/machine_snapshot"' in launcher_source
    window.close()
    dialog.close()
    app.processEvents()


def test_read_only_source_contains_no_epics_write_call() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    sources = (
        repo_root / "src/apps/machine_snapshot/main.py",
        repo_root / "src/shared/pv_sampling.py",
    )
    pattern = re.compile(r"\b(?:caput|put)\s*\(")
    assert all(pattern.search(path.read_text(encoding="utf-8")) is None for path in sources)
