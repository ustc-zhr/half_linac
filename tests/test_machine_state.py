from __future__ import annotations

from dataclasses import replace

import pytest

from half_linac.src.shared.machine_profile import load_profile
from half_linac.src.shared.machine_state import (
    CAPTURE_GROUP_OBSERVATIONS,
    DEFAULT_CAPTURE_GROUPS,
    DiffStatus,
    MachineStateError,
    MachineStateSnapshot,
    SampleQuality,
    SnapshotEntry,
    StateClass,
    build_capture_plan,
    build_profile_signature,
    classify_channel,
    compare_snapshots,
    snapshot_from_dict,
    snapshot_to_dict,
)


def _entry(
    key: str = "Q01/current_set",
    *,
    value=1.0,
    value_type: str = "number",
    unit: str | None = "A",
    pv_name: str = "TEST:Q01:SET",
    quality: SampleQuality = SampleQuality.OK,
) -> SnapshotEntry:
    element_id, channel = key.split("/", 1)
    return SnapshotEntry(
        key=key,
        element_id=element_id,
        element_kind="quad",
        element_order=1,
        display_name=element_id,
        logical_channel=channel,
        pv_name=pv_name,
        state_class=StateClass.SETTING,
        value=value,
        value_type=value_type,
        unit=unit,
        source_timestamp=100.0,
        received_at="2026-08-27T00:00:00+00:00",
        alarm_status=0,
        alarm_severity=0,
        native_count=1,
        quality=quality,
    )


def _snapshot(
    entry: SnapshotEntry,
    *,
    snapshot_id: str = "snapshot_a",
    machine_id: str = "half",
    backend: str = "real",
    signature: str = "sig",
) -> MachineStateSnapshot:
    return MachineStateSnapshot(
        snapshot_id=snapshot_id,
        name=snapshot_id,
        operator_note="",
        machine_id=machine_id,
        machine_display_name="HALF Linac",
        backend=backend,
        profile_schema_version="1",
        profile_signature=signature,
        capture_started_at="2026-08-27T00:00:00+00:00",
        capture_finished_at="2026-08-27T00:00:01+00:00",
        capture_status="complete",
        hostname="test",
        consistency="best_effort",
        requested_count=1,
        entries=(entry,),
    )


def test_channel_classification_covers_half_semantics() -> None:
    assert classify_channel("quad", "K1") == StateClass.SETTING
    assert classify_channel("quad", "K1_adj") == StateClass.SETTING
    assert classify_channel("quad", "K1_total") == StateClass.DERIVED
    assert classify_channel("corr", "current_readback") == StateClass.READBACK
    assert classify_channel("modulator", "hv_enable") == StateClass.SETTING
    assert classify_channel("bpm", "x") == StateClass.OBSERVATION
    assert classify_channel("rf", "pickup_waveform") == StateClass.OBSERVATION


def test_capture_plan_filters_groups_backend_and_keeps_machine_order() -> None:
    profile = load_profile("half")
    default_plan = build_capture_plan(profile, "vm", DEFAULT_CAPTURE_GROUPS)
    with_observations = build_capture_plan(
        profile,
        "vm",
        (*DEFAULT_CAPTURE_GROUPS, CAPTURE_GROUP_OBSERVATIONS),
    )

    assert default_plan
    assert all(point.endpoint.backend == "vm" for point in default_plan)
    assert all(point.state_class != StateClass.OBSERVATION for point in default_plan)
    assert any(point.state_class == StateClass.OBSERVATION for point in with_observations)
    assert list(default_plan) == sorted(
        default_plan,
        key=lambda point: (
            point.element_order,
            point.endpoint.element_id,
            point.endpoint.logical_channel,
        ),
    )
    assert len(build_profile_signature(profile, "vm")) == 64


def test_snapshot_json_round_trip_and_unknown_schema(tmp_path) -> None:
    snapshot = _snapshot(_entry())
    payload = snapshot_to_dict(snapshot)

    assert snapshot_from_dict(payload) == snapshot
    payload["schema_version"] = "99"
    with pytest.raises(MachineStateError, match="Unsupported snapshot schema_version"):
        snapshot_from_dict(payload)


def test_snapshot_rejects_duplicate_keys_and_non_finite_values() -> None:
    duplicate = replace(_snapshot(_entry()), entries=(_entry(), _entry()))
    with pytest.raises(MachineStateError, match="duplicate"):
        snapshot_to_dict(duplicate)

    invalid = _snapshot(_entry(value=float("nan")))
    with pytest.raises(MachineStateError, match="NaN"):
        snapshot_to_dict(invalid)


def test_compare_numeric_string_missing_quality_and_mapping() -> None:
    a = _snapshot(_entry(value=1.0, pv_name="OLD"))
    b = _snapshot(
        _entry(value=1.5, pv_name="NEW"),
        snapshot_id="snapshot_b",
        signature="new-sig",
    )
    row = compare_snapshots(a, b)[0]
    assert row.status == DiffStatus.CHANGED
    assert row.delta == pytest.approx(0.5)
    assert row.mapping_changed

    unavailable = replace(b, entries=(replace(b.entries[0], value=None, quality=SampleQuality.DISCONNECTED),))
    assert compare_snapshots(a, unavailable)[0].status == DiffStatus.UNAVAILABLE

    text_a = _snapshot(_entry(value="OFF", value_type="string", unit=None))
    text_b = replace(text_a, snapshot_id="text_b", entries=(_entry(value="ON", value_type="string", unit=None),))
    assert compare_snapshots(text_a, text_b)[0].status == DiffStatus.CHANGED


def test_compare_cross_backend_browses_without_delta_and_cross_machine_rejects() -> None:
    a = _snapshot(_entry())
    vm = _snapshot(_entry(value=2.0), snapshot_id="vm", backend="vm")
    row = compare_snapshots(a, vm)[0]
    assert row.status == DiffStatus.NOT_COMPARABLE
    assert row.delta is None

    other = _snapshot(_entry(), snapshot_id="other", machine_id="other")
    with pytest.raises(MachineStateError, match="different machines"):
        compare_snapshots(a, other)


def test_compare_reports_unit_and_type_mismatch() -> None:
    a = _snapshot(_entry(unit="A"))
    assert compare_snapshots(a, _snapshot(_entry(unit="mA"), snapshot_id="b"))[0].status == DiffStatus.UNIT_MISMATCH
    assert compare_snapshots(
        a,
        _snapshot(_entry(value="1", value_type="string", unit="A"), snapshot_id="b"),
    )[0].status == DiffStatus.TYPE_MISMATCH

