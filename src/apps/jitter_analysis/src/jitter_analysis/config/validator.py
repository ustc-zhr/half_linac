from __future__ import annotations

from typing import Any


REQUIRED_TOP_LEVEL_KEYS = {
    "schema_version",
    "machine",
    "defaults",
    "groups",
    "knobs",
    "objects",
    "presets",
}


def validate_config_dict(data: dict[str, Any]) -> dict[str, Any]:
    missing = REQUIRED_TOP_LEVEL_KEYS.difference(data)
    if missing:
        missing_keys = ", ".join(sorted(missing))
        raise ValueError(f"Missing required config keys: {missing_keys}")

    group_ids = {item["id"] for item in data["groups"]}
    for knob in data["knobs"]:
        if knob["group"] not in group_ids:
            raise ValueError(f"Unknown group for knob {knob['id']}: {knob['group']}")
    for obj in data["objects"]:
        if obj["group"] not in group_ids:
            raise ValueError(f"Unknown group for object {obj['id']}: {obj['group']}")

    knob_ids = {item["id"] for item in data["knobs"]}
    object_ids = {item["id"] for item in data["objects"]}
    for preset in data["presets"]:
        target_ids = set(preset.get("targets", []))
        unknown_targets = target_ids.difference(object_ids)
        if unknown_targets:
            raise ValueError(
                f"Unknown preset targets for {preset['id']}: {', '.join(sorted(unknown_targets))}"
            )
        knob_id = preset.get("knob_id")
        if knob_id and knob_id not in knob_ids:
            raise ValueError(f"Unknown preset knob for {preset['id']}: {knob_id}")

    return data
