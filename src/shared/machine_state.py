from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

from .machine_profile.models import MachineProfile, MachineProfileError
from .machine_profile.resolver import resolve_write_target
from .pv_connection import PvEndpoint, collect_pv_endpoints
from .runtime_state import read_runtime_state, write_runtime_state


SNAPSHOT_SCHEMA_VERSION = "1"
CAPTURE_GROUP_SETTINGS = "settings"
CAPTURE_GROUP_READBACKS = "readbacks"
CAPTURE_GROUP_OBSERVATIONS = "observations"
CAPTURE_GROUP_MAGNETS = "magnets"
CAPTURE_GROUP_HIGH_VOLTAGE = "high_voltage"
CAPTURE_GROUP_LLRF = "llrf"
CAPTURE_GROUP_TIMING = "timing"
DEFAULT_CAPTURE_GROUPS = frozenset(
    {CAPTURE_GROUP_SETTINGS, CAPTURE_GROUP_READBACKS}
)
ALL_CAPTURE_GROUPS = frozenset(
    {
        CAPTURE_GROUP_SETTINGS,
        CAPTURE_GROUP_READBACKS,
        CAPTURE_GROUP_OBSERVATIONS,
        CAPTURE_GROUP_MAGNETS,
        CAPTURE_GROUP_HIGH_VOLTAGE,
        CAPTURE_GROUP_LLRF,
        CAPTURE_GROUP_TIMING,
    }
)


class MachineStateError(ValueError):
    pass


class StateClass(str, Enum):
    SETTING = "setting"
    READBACK = "readback"
    OBSERVATION = "observation"
    STATUS = "status"
    DERIVED = "derived"
    OTHER = "other"


class SampleQuality(str, Enum):
    OK = "ok"
    DISCONNECTED = "disconnected"
    READ_ERROR = "read_error"
    INVALID_NUMBER = "invalid_number"
    UNSUPPORTED_ARRAY = "unsupported_array"
    TIMESTAMP_MISSING = "timestamp_missing"
    ALARM = "alarm"
    CANCELLED = "cancelled"


class DiffStatus(str, Enum):
    SAME = "same"
    CHANGED = "changed"
    ONLY_IN_A = "only_in_a"
    ONLY_IN_B = "only_in_b"
    UNAVAILABLE = "unavailable"
    UNIT_MISMATCH = "unit_mismatch"
    TYPE_MISMATCH = "type_mismatch"
    NOT_COMPARABLE = "not_comparable"


@dataclass(frozen=True)
class CapturePoint:
    endpoint: PvEndpoint
    element_order: int
    display_name: str
    state_class: StateClass
    capture_group: str
    unit: str | None

    @property
    def key(self) -> str:
        return f"{self.endpoint.element_id}/{self.endpoint.logical_channel}"


@dataclass(frozen=True)
class SnapshotEntry:
    key: str
    element_id: str
    element_kind: str
    element_order: int
    display_name: str
    logical_channel: str
    pv_name: str
    state_class: StateClass
    value: Any
    value_type: str
    unit: str | None
    source_timestamp: float | None
    received_at: str | None
    alarm_status: int | str | None
    alarm_severity: int | None
    native_count: int | None
    quality: SampleQuality
    detail: str = ""


@dataclass(frozen=True)
class MachineStateSnapshot:
    snapshot_id: str
    name: str
    operator_note: str
    machine_id: str
    machine_display_name: str
    backend: str
    profile_schema_version: str
    profile_signature: str
    capture_started_at: str
    capture_finished_at: str
    capture_status: str
    hostname: str
    consistency: str
    requested_count: int
    entries: tuple[SnapshotEntry, ...]
    schema_version: str = SNAPSHOT_SCHEMA_VERSION

    @property
    def ok_count(self) -> int:
        return sum(entry.quality == SampleQuality.OK for entry in self.entries)

    @property
    def failed_count(self) -> int:
        return sum(
            entry.quality
            in {
                SampleQuality.DISCONNECTED,
                SampleQuality.READ_ERROR,
                SampleQuality.INVALID_NUMBER,
                SampleQuality.CANCELLED,
            }
            for entry in self.entries
        )

    @property
    def skipped_count(self) -> int:
        return sum(
            entry.quality == SampleQuality.UNSUPPORTED_ARRAY for entry in self.entries
        )


@dataclass(frozen=True)
class SnapshotDiffRow:
    key: str
    entry_a: SnapshotEntry | None
    entry_b: SnapshotEntry | None
    delta: float | None
    status: DiffStatus
    mapping_changed: bool = False
    detail: str = ""


def classify_channel(element_kind: str, logical_channel: str) -> StateClass:
    channel = str(logical_channel).strip()
    lowered = channel.casefold()
    if lowered == "k1_total":
        return StateClass.DERIVED
    if lowered.endswith("_readback") or lowered == "readback":
        return StateClass.READBACK
    if (
        lowered.endswith("_set")
        or lowered in {
            "setpoint",
            "k1",
            "k1_adj",
            "kick",
            "angle",
            "exposure_time",
        }
        or lowered.endswith("_enable")
    ):
        return StateClass.SETTING
    if (
        element_kind in {"bpm", "ct"}
        or lowered in {"image", "sigx", "sigy"}
        or "waveform" in lowered
    ):
        return StateClass.OBSERVATION
    if any(token in lowered for token in ("status", "state", "ready", "fault", "permit")):
        return StateClass.STATUS
    return StateClass.OTHER


def capture_group_for(state_class: StateClass) -> str:
    if state_class == StateClass.SETTING:
        return CAPTURE_GROUP_SETTINGS
    if state_class == StateClass.OBSERVATION:
        return CAPTURE_GROUP_OBSERVATIONS
    return CAPTURE_GROUP_READBACKS


def subsystem_capture_group(element_kind: str, logical_channel: str) -> str | None:
    """Return the first-edition restore/capture scope for a channel."""
    channel = str(logical_channel).casefold()
    if channel.endswith("_enable"):
        return None
    if element_kind in {"quad", "corr", "bend", "solenoid"}:
        return CAPTURE_GROUP_MAGNETS
    if channel.startswith("voltage_"):
        return CAPTURE_GROUP_HIGH_VOLTAGE
    if channel in {"phase_set", "phase_readback", "amplitude_set", "amplitude_readback"}:
        return CAPTURE_GROUP_LLRF
    if channel.endswith("_delay_set") or channel.endswith("_width_set"):
        return CAPTURE_GROUP_TIMING
    if channel.endswith("_delay_readback") or channel.endswith("_width_readback"):
        return CAPTURE_GROUP_TIMING
    return None


def build_profile_signature(profile: MachineProfile, backend: str) -> str:
    endpoints = collect_pv_endpoints(profile, (backend,))
    records = []
    for endpoint in endpoints:
        element = profile.get_element(endpoint.element_id)
        records.append(
            {
                "element_id": endpoint.element_id,
                "element_kind": endpoint.element_kind,
                "element_order": element.order,
                "logical_channel": endpoint.logical_channel,
                "pv_name": endpoint.pv_name,
                "limits": dict(element.limits_for(endpoint.logical_channel)),
            }
        )
    payload = {
        "machine_id": profile.machine.id,
        "profile_schema_version": profile.schema_version,
        "backend": backend,
        "channels": records,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_capture_plan(
    profile: MachineProfile,
    backend: str,
    groups: Iterable[str] = DEFAULT_CAPTURE_GROUPS,
) -> tuple[CapturePoint, ...]:
    selected_groups = frozenset(str(group).strip() for group in groups)
    unknown = sorted(selected_groups - ALL_CAPTURE_GROUPS)
    if unknown:
        raise MachineStateError(f"Unknown capture group(s): {', '.join(unknown)}")

    points = []
    for endpoint in collect_pv_endpoints(profile, (backend,)):
        element = profile.get_element(endpoint.element_id)
        state_class = classify_channel(element.kind, endpoint.logical_channel)
        capture_group = subsystem_capture_group(element.kind, endpoint.logical_channel)
        if capture_group is None:
            capture_group = capture_group_for(state_class)
        if state_class == StateClass.SETTING and endpoint.logical_channel.casefold().endswith("_enable"):
            continue
        subsystem_groups = {
            CAPTURE_GROUP_MAGNETS,
            CAPTURE_GROUP_HIGH_VOLTAGE,
            CAPTURE_GROUP_LLRF,
            CAPTURE_GROUP_TIMING,
        }
        if capture_group in subsystem_groups and state_class not in {
            StateClass.SETTING,
            StateClass.READBACK,
        }:
            continue
        if selected_groups & {
            CAPTURE_GROUP_MAGNETS,
            CAPTURE_GROUP_HIGH_VOLTAGE,
            CAPTURE_GROUP_LLRF,
            CAPTURE_GROUP_TIMING,
        } and capture_group in {
            CAPTURE_GROUP_MAGNETS,
            CAPTURE_GROUP_HIGH_VOLTAGE,
            CAPTURE_GROUP_LLRF,
            CAPTURE_GROUP_TIMING,
        }:
            pass
        legacy_match = (
            (state_class == StateClass.SETTING and CAPTURE_GROUP_SETTINGS in selected_groups)
            or (state_class == StateClass.READBACK and CAPTURE_GROUP_READBACKS in selected_groups)
            or (state_class == StateClass.OBSERVATION and CAPTURE_GROUP_OBSERVATIONS in selected_groups)
        )
        if capture_group not in selected_groups and not legacy_match:
            continue
        points.append(
            CapturePoint(
                endpoint=endpoint,
                element_order=element.order,
                display_name=element.display_name,
                state_class=state_class,
                capture_group=capture_group,
                unit=_resolve_unit(profile, backend, endpoint),
            )
        )
    return tuple(
        sorted(
            points,
            key=lambda point: (
                point.element_order,
                point.endpoint.element_id,
                point.endpoint.logical_channel,
            ),
        )
    )


def _resolve_unit(
    profile: MachineProfile,
    backend: str,
    endpoint: PvEndpoint,
) -> str | None:
    element = profile.get_element(endpoint.element_id)
    raw_limit = element.limits_for(endpoint.logical_channel)
    raw_unit = raw_limit.get("unit") if isinstance(raw_limit, Mapping) else None
    if raw_unit:
        return str(raw_unit)

    if endpoint.logical_channel.endswith("_readback"):
        set_channel = endpoint.logical_channel[: -len("_readback")] + "_set"
        paired_limit = element.limits_for(set_channel)
        paired_unit = paired_limit.get("unit") if isinstance(paired_limit, Mapping) else None
        if paired_unit:
            return str(paired_unit)

    try:
        target = resolve_write_target(
            profile,
            endpoint.element_id,
            logical_channel=endpoint.logical_channel,
            mode=backend,
        )
    except MachineProfileError:
        return None
    return target.unit


def compare_snapshots(
    snapshot_a: MachineStateSnapshot,
    snapshot_b: MachineStateSnapshot,
) -> tuple[SnapshotDiffRow, ...]:
    if snapshot_a.machine_id != snapshot_b.machine_id:
        raise MachineStateError(
            "Snapshots from different machines cannot be compared: "
            f"{snapshot_a.machine_id!r} != {snapshot_b.machine_id!r}."
        )

    entries_a = {entry.key: entry for entry in snapshot_a.entries}
    entries_b = {entry.key: entry for entry in snapshot_b.entries}
    cross_backend = snapshot_a.backend != snapshot_b.backend
    rows = []
    for key in sorted(
        entries_a.keys() | entries_b.keys(),
        key=lambda item: _diff_sort_key(item, entries_a, entries_b),
    ):
        entry_a = entries_a.get(key)
        entry_b = entries_b.get(key)
        if entry_a is None:
            rows.append(SnapshotDiffRow(key, None, entry_b, None, DiffStatus.ONLY_IN_B))
            continue
        if entry_b is None:
            rows.append(SnapshotDiffRow(key, entry_a, None, None, DiffStatus.ONLY_IN_A))
            continue

        mapping_changed = entry_a.pv_name != entry_b.pv_name
        if cross_backend:
            rows.append(
                SnapshotDiffRow(
                    key,
                    entry_a,
                    entry_b,
                    None,
                    DiffStatus.NOT_COMPARABLE,
                    mapping_changed,
                    "Different control backends",
                )
            )
            continue
        if not _entry_value_available(entry_a) or not _entry_value_available(entry_b):
            rows.append(
                SnapshotDiffRow(
                    key,
                    entry_a,
                    entry_b,
                    None,
                    DiffStatus.UNAVAILABLE,
                    mapping_changed,
                    "One or both values are unavailable",
                )
            )
            continue
        if entry_a.unit != entry_b.unit:
            rows.append(
                SnapshotDiffRow(
                    key,
                    entry_a,
                    entry_b,
                    None,
                    DiffStatus.UNIT_MISMATCH,
                    mapping_changed,
                    "Units differ",
                )
            )
            continue

        numeric_a = _numeric_value(entry_a.value)
        numeric_b = _numeric_value(entry_b.value)
        if numeric_a is not None and numeric_b is not None:
            delta = numeric_b - numeric_a
            status = DiffStatus.SAME if numeric_a == numeric_b else DiffStatus.CHANGED
            rows.append(
                SnapshotDiffRow(
                    key,
                    entry_a,
                    entry_b,
                    delta,
                    status,
                    mapping_changed,
                )
            )
            continue
        if (numeric_a is None) != (numeric_b is None) or entry_a.value_type != entry_b.value_type:
            rows.append(
                SnapshotDiffRow(
                    key,
                    entry_a,
                    entry_b,
                    None,
                    DiffStatus.TYPE_MISMATCH,
                    mapping_changed,
                    "Value types differ",
                )
            )
            continue
        status = DiffStatus.SAME if entry_a.value == entry_b.value else DiffStatus.CHANGED
        rows.append(
            SnapshotDiffRow(
                key,
                entry_a,
                entry_b,
                None,
                status,
                mapping_changed,
            )
        )
    return tuple(rows)


def _diff_sort_key(
    key: str,
    entries_a: Mapping[str, SnapshotEntry],
    entries_b: Mapping[str, SnapshotEntry],
) -> tuple[int, str, str]:
    entry = entries_a.get(key) or entries_b[key]
    return entry.element_order, entry.element_id, entry.logical_channel


def _entry_value_available(entry: SnapshotEntry) -> bool:
    return entry.value is not None and entry.quality not in {
        SampleQuality.DISCONNECTED,
        SampleQuality.READ_ERROR,
        SampleQuality.INVALID_NUMBER,
        SampleQuality.UNSUPPORTED_ARRAY,
        SampleQuality.CANCELLED,
    }


def _numeric_value(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def snapshot_to_dict(snapshot: MachineStateSnapshot) -> dict[str, Any]:
    _validate_snapshot(snapshot)
    return {
        "schema_version": snapshot.schema_version,
        "snapshot_id": snapshot.snapshot_id,
        "name": snapshot.name,
        "operator_note": snapshot.operator_note,
        "machine": {
            "id": snapshot.machine_id,
            "display_name": snapshot.machine_display_name,
            "control_backend": snapshot.backend,
            "profile_schema_version": snapshot.profile_schema_version,
            "profile_signature": snapshot.profile_signature,
        },
        "capture": {
            "started_at": snapshot.capture_started_at,
            "finished_at": snapshot.capture_finished_at,
            "status": snapshot.capture_status,
            "hostname": snapshot.hostname,
            "consistency": snapshot.consistency,
            "requested_count": snapshot.requested_count,
            "ok_count": snapshot.ok_count,
            "failed_count": snapshot.failed_count,
            "skipped_count": snapshot.skipped_count,
        },
        "entries": [_entry_to_dict(entry) for entry in snapshot.entries],
    }


def _entry_to_dict(entry: SnapshotEntry) -> dict[str, Any]:
    return {
        "key": entry.key,
        "element_id": entry.element_id,
        "element_kind": entry.element_kind,
        "element_order": entry.element_order,
        "display_name": entry.display_name,
        "logical_channel": entry.logical_channel,
        "pv_name": entry.pv_name,
        "state_class": entry.state_class.value,
        "value": entry.value,
        "value_type": entry.value_type,
        "unit": entry.unit,
        "source_timestamp": entry.source_timestamp,
        "received_at": entry.received_at,
        "alarm_status": entry.alarm_status,
        "alarm_severity": entry.alarm_severity,
        "native_count": entry.native_count,
        "quality": entry.quality.value,
        "detail": entry.detail,
    }


def snapshot_from_dict(payload: Mapping[str, Any]) -> MachineStateSnapshot:
    if not isinstance(payload, Mapping):
        raise MachineStateError("Snapshot payload must be a mapping.")
    schema_version = str(payload.get("schema_version", ""))
    if schema_version != SNAPSHOT_SCHEMA_VERSION:
        raise MachineStateError(
            f"Unsupported snapshot schema_version {schema_version!r}; "
            f"expected {SNAPSHOT_SCHEMA_VERSION!r}."
        )
    machine = _mapping(payload.get("machine"), "machine")
    capture = _mapping(payload.get("capture"), "capture")
    entries_raw = payload.get("entries")
    if not isinstance(entries_raw, list):
        raise MachineStateError("Snapshot entries must be a list.")
    entries = tuple(_entry_from_dict(item, index) for index, item in enumerate(entries_raw))
    snapshot = MachineStateSnapshot(
        schema_version=schema_version,
        snapshot_id=_text(payload.get("snapshot_id"), "snapshot_id"),
        name=_text(payload.get("name"), "name"),
        operator_note=str(payload.get("operator_note", "")),
        machine_id=_text(machine.get("id"), "machine.id"),
        machine_display_name=_text(machine.get("display_name"), "machine.display_name"),
        backend=_text(machine.get("control_backend"), "machine.control_backend"),
        profile_schema_version=str(machine.get("profile_schema_version", "")),
        profile_signature=_text(machine.get("profile_signature"), "machine.profile_signature"),
        capture_started_at=_text(capture.get("started_at"), "capture.started_at"),
        capture_finished_at=_text(capture.get("finished_at"), "capture.finished_at"),
        capture_status=_text(capture.get("status"), "capture.status"),
        hostname=str(capture.get("hostname", "")),
        consistency=str(capture.get("consistency", "best_effort")),
        requested_count=_integer(capture.get("requested_count"), "capture.requested_count"),
        entries=entries,
    )
    _validate_snapshot(snapshot)
    return snapshot


def _entry_from_dict(payload: Any, index: int) -> SnapshotEntry:
    item = _mapping(payload, f"entries[{index}]")
    try:
        state_class = StateClass(str(item.get("state_class")))
        quality = SampleQuality(str(item.get("quality")))
    except ValueError as exc:
        raise MachineStateError(f"Invalid enum value in entries[{index}].") from exc
    value = _normalize_json_value(item.get("value"))
    return SnapshotEntry(
        key=_text(item.get("key"), f"entries[{index}].key"),
        element_id=_text(item.get("element_id"), f"entries[{index}].element_id"),
        element_kind=_text(item.get("element_kind"), f"entries[{index}].element_kind"),
        element_order=_integer(item.get("element_order"), f"entries[{index}].element_order"),
        display_name=_text(item.get("display_name"), f"entries[{index}].display_name"),
        logical_channel=_text(
            item.get("logical_channel"), f"entries[{index}].logical_channel"
        ),
        pv_name=_text(item.get("pv_name"), f"entries[{index}].pv_name"),
        state_class=state_class,
        value=value,
        value_type=str(item.get("value_type", _value_type(value))),
        unit=str(item["unit"]) if item.get("unit") is not None else None,
        source_timestamp=_optional_float(item.get("source_timestamp")),
        received_at=str(item["received_at"]) if item.get("received_at") is not None else None,
        alarm_status=item.get("alarm_status"),
        alarm_severity=_optional_int(item.get("alarm_severity")),
        native_count=_optional_int(item.get("native_count")),
        quality=quality,
        detail=str(item.get("detail", "")),
    )


def save_snapshot(path: str | Path, snapshot: MachineStateSnapshot) -> None:
    write_runtime_state(path, snapshot_to_dict(snapshot))


def load_snapshot(path: str | Path) -> MachineStateSnapshot:
    try:
        payload = read_runtime_state(path)
    except (OSError, json.JSONDecodeError) as exc:
        raise MachineStateError(f"Could not read snapshot {path}: {exc}") from exc
    return snapshot_from_dict(payload)


def _validate_snapshot(snapshot: MachineStateSnapshot) -> None:
    if snapshot.schema_version != SNAPSHOT_SCHEMA_VERSION:
        raise MachineStateError(
            f"Unsupported snapshot schema_version {snapshot.schema_version!r}."
        )
    if snapshot.capture_status not in {"complete", "partial", "cancelled", "failed"}:
        raise MachineStateError(f"Invalid capture status {snapshot.capture_status!r}.")
    if snapshot.requested_count < 0:
        raise MachineStateError("requested_count must be non-negative.")
    keys = [entry.key for entry in snapshot.entries]
    if len(keys) != len(set(keys)):
        raise MachineStateError("Snapshot contains duplicate entry keys.")
    for entry in snapshot.entries:
        expected_key = f"{entry.element_id}/{entry.logical_channel}"
        if entry.key != expected_key:
            raise MachineStateError(
                f"Entry key {entry.key!r} does not match {expected_key!r}."
            )
        _normalize_json_value(entry.value)


def _normalize_json_value(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        raise MachineStateError("Snapshot values must not contain NaN or Infinity.")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise MachineStateError(f"Unsupported scalar snapshot value type: {type(value).__name__}.")


def value_type(value: Any) -> str:
    return _value_type(value)


def _value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return type(value).__name__


def _mapping(value: Any, location: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MachineStateError(f"{location} must be a mapping.")
    return value


def _text(value: Any, location: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise MachineStateError(f"{location} must not be empty.")
    return text


def _integer(value: Any, location: str) -> int:
    if isinstance(value, bool):
        raise MachineStateError(f"{location} must be an integer.")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise MachineStateError(f"{location} must be an integer.") from exc


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None
