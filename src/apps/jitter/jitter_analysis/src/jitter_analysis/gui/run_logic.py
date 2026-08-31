from __future__ import annotations

from pathlib import Path

from ..domain.types import RunMode, RunResult


def run_browser_scope_kind(root_dir: str | Path) -> str:
    root_path = Path(root_dir)
    if not root_path.exists():
        return "root"
    if any((root_path / filename).exists() for filename in ("raw.h5", "metadata.json", "result.json")):
        return "single_run"
    return "root"


def current_record_count(records, stored_count: int) -> int:
    return len(records) if records else int(stored_count)


def has_run_data(record_count: int, series_values) -> bool:
    if int(record_count) > 0:
        return True
    return any(len(values) > 0 for values in series_values.values())


def validate_loaded_run_config(result: RunResult, loaded_config) -> tuple[bool, str]:
    if loaded_config is None:
        return False, "No PV library is loaded."

    expected_object_ids = [
        str(item).strip()
        for item in result.details.get("target_object_ids", [])
        if str(item).strip()
    ]
    available_object_ids = {obj.id for obj in loaded_config.objects}
    missing_object_ids = [object_id for object_id in expected_object_ids if object_id not in available_object_ids]

    expected_knob_ids: list[str] = []
    if result.metadata.mode == RunMode.KNOB_SCAN:
        knob_id = str(result.details.get("knob_id", "")).strip()
        if knob_id:
            expected_knob_ids = [knob_id]
    elif result.metadata.mode == RunMode.MULTI_KNOB_RANDOM:
        knob_ranges = result.details.get("knob_ranges", [])
        expected_knob_ids = [
            str(item.get("knob_id", "")).strip()
            for item in knob_ranges
            if isinstance(item, dict) and str(item.get("knob_id", "")).strip()
        ]

    available_knob_ids = {knob.id for knob in loaded_config.knobs}
    missing_knob_ids = [knob_id for knob_id in expected_knob_ids if knob_id not in available_knob_ids]

    if not missing_object_ids and not missing_knob_ids:
        return True, ""

    missing_parts = []
    if missing_object_ids:
        missing_parts.append("read PV IDs: " + ", ".join(missing_object_ids[:8]))
    if missing_knob_ids:
        missing_parts.append("control PV IDs: " + ", ".join(missing_knob_ids[:8]))
    return (
        False,
        "The loaded PV library does not match this saved run.\nMissing "
        + " | ".join(missing_parts),
    )


def resolve_loaded_run_selection(
    details: dict[str, object],
    mode: RunMode | None,
    available_object_ids,
    record_pv_ids=(),
    series_pv_ids=(),
) -> dict[str, object]:
    selected_object_ids = list(details.get("target_object_ids", []))
    if not selected_object_ids:
        known_object_ids = set(available_object_ids)
        seen = []
        for pv_id in record_pv_ids:
            if pv_id in known_object_ids and pv_id not in seen:
                seen.append(pv_id)
        if not seen:
            for pv_id in series_pv_ids:
                if pv_id in known_object_ids and pv_id not in seen:
                    seen.append(pv_id)
        selected_object_ids = seen

    selected_knob_ids: list[str] = []
    active_knob_id = None
    if mode == RunMode.KNOB_SCAN:
        knob_id = str(details.get("knob_id", "")).strip()
        if knob_id:
            selected_knob_ids = [knob_id]
            active_knob_id = knob_id
    elif mode == RunMode.MULTI_KNOB_RANDOM:
        knob_ranges = details.get("knob_ranges", [])
        selected_knob_ids = [
            str(item.get("knob_id"))
            for item in knob_ranges
            if isinstance(item, dict) and str(item.get("knob_id", "")).strip()
        ]
        active_knob_id = selected_knob_ids[0] if selected_knob_ids else None

    return {
        "selected_object_ids": selected_object_ids,
        "selected_knob_ids": selected_knob_ids,
        "active_knob_id": active_knob_id,
    }


def loaded_run_object_count_hint(
    details: dict[str, object],
    selected_object_ids=(),
    record_pv_ids=(),
) -> int:
    target_object_ids = [
        str(item).strip()
        for item in details.get("target_object_ids", [])
        if str(item).strip()
    ]
    if target_object_ids:
        return len(target_object_ids)
    selected = list(selected_object_ids)
    if selected:
        return len(selected)
    seen_ids = []
    for pv_id in record_pv_ids:
        if pv_id not in seen_ids:
            seen_ids.append(pv_id)
    return len(seen_ids)


def loaded_run_parameter_updates(details: dict[str, object], mode: RunMode | None) -> dict[str, object]:
    if mode == RunMode.TIMED_ACQUISITION:
        updates: dict[str, object] = {}
        if "shot_interval_sec" in details:
            updates["shot_interval_sec"] = float(details["shot_interval_sec"])
        if "stop_mode" in details:
            updates["stop_mode"] = str(details.get("stop_mode", "samples")).strip().lower() or "samples"
        if details.get("sample_count") is not None:
            updates["sample_count"] = int(details["sample_count"])
        if details.get("duration_sec") is not None:
            updates["duration_sec"] = float(details["duration_sec"])
        return updates

    if mode == RunMode.KNOB_SCAN:
        updates = {}
        if "settle_delay_sec" in details:
            updates["settle_delay_sec"] = float(details["settle_delay_sec"])
        if "shot_interval_sec" in details:
            updates["shot_interval_sec"] = float(details["shot_interval_sec"])
        if "sample_count_per_step" in details:
            updates["sample_count_per_step"] = int(details["sample_count_per_step"])
        if "restore_initial_value" in details:
            updates["restore_initial_value"] = bool(details["restore_initial_value"])
        scan_values = details.get("scan_values", [])
        if isinstance(scan_values, list) and scan_values:
            updates["manual_scan_values_text"] = ", ".join(f"{float(value):.6g}" for value in scan_values)
        return updates

    updates = {}
    if "settle_delay_sec" in details:
        updates["settle_delay_sec"] = float(details["settle_delay_sec"])
    if "shot_interval_sec" in details:
        updates["shot_interval_sec"] = float(details["shot_interval_sec"])
    if "sample_count_per_point" in details:
        updates["sample_count_per_point"] = int(details["sample_count_per_point"])
    if "num_points" in details:
        updates["num_points"] = int(details["num_points"])
    if "levels_per_knob" in details:
        updates["levels_per_knob"] = int(details["levels_per_knob"])
    if "restore_initial_values" in details:
        updates["restore_initial_values"] = bool(details["restore_initial_values"])
    sampling_method = str(details.get("sampling_method", "")).strip()
    if sampling_method:
        updates["sampling_method"] = sampling_method
    elif str(details.get("distribution", "")).strip():
        # Older runs only stored a distribution. Both historical choices reopen
        # as the single supported random method.
        updates["sampling_method"] = "uniform_random"

    knob_ranges = details.get("knob_ranges", [])
    if isinstance(knob_ranges, list) and knob_ranges:
        state = {}
        for item in knob_ranges:
            if not isinstance(item, dict):
                continue
            knob_id = str(item.get("knob_id", "")).strip()
            if not knob_id:
                continue
            state[knob_id] = {
                "enabled": True,
                "low": float(item.get("low", 0.0)),
                "high": float(item.get("high", 0.0)),
            }
        if state:
            updates["knob_state"] = state
    return updates
