from __future__ import annotations


def normalize_selection_for_available_pvs(
    selected_knob_ids,
    active_knob_id,
    selected_object_ids,
    available_knob_ids,
    available_object_ids,
) -> dict[str, object]:
    knob_ids = set(available_knob_ids)
    object_ids = set(available_object_ids)
    normalized_knob_ids = [
        knob_id for knob_id in selected_knob_ids if knob_id in knob_ids
    ]
    normalized_active_knob_id = active_knob_id if active_knob_id in knob_ids else None
    if normalized_active_knob_id not in normalized_knob_ids:
        normalized_active_knob_id = normalized_knob_ids[0] if normalized_knob_ids else None
    normalized_object_ids = [
        object_id for object_id in selected_object_ids if object_id in object_ids
    ]
    return {
        "selected_knob_ids": normalized_knob_ids,
        "active_knob_id": normalized_active_knob_id,
        "selected_object_ids": normalized_object_ids,
    }
