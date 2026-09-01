#!/usr/bin/env python3
"""Sync HALF real-machine PVs from half_linac into jitter_analysis config."""

from __future__ import annotations

import argparse
import json
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HALF_LINAC_ROOT = REPO_ROOT.parent / "half_linac"
DEFAULT_OUTPUT = REPO_ROOT / "configs" / "half_real_pvlist.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Sync configs/half_real_pvlist.json from "
            "half_linac/configs/machines/half machine and real backend files."
        )
    )
    parser.add_argument(
        "--half-linac-root",
        type=Path,
        default=DEFAULT_HALF_LINAC_ROOT,
        help=f"Path to half_linac checkout. Default: {DEFAULT_HALF_LINAC_ROOT}",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"PV library to update. Default: {DEFAULT_OUTPUT}",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not write; fail if the output file is not synchronized.",
    )
    args = parser.parse_args(argv)

    source_dir = args.half_linac_root / "configs" / "machines" / "half"
    output = args.output
    synced = sync_config(
        current=read_json(output),
        machine=read_json(source_dir / "machine.json"),
        real_backend=read_json(source_dir / "control_backends" / "real.json"),
    )
    serialized = json.dumps(synced, indent=2) + "\n"

    if args.check:
        current_text = output.read_text(encoding="utf-8")
        if current_text != serialized:
            print(f"{output} is not synchronized with {source_dir}", file=sys.stderr)
            return 1
        print(f"{output} is synchronized with {source_dir}")
        return 0

    output.write_text(serialized, encoding="utf-8")
    print(
        f"Synced {output}: "
        f"{len(synced['knobs'])} knobs, {len(synced['objects'])} objects, "
        f"{len(synced['presets'])} presets."
    )
    return 0


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sync_config(
    *,
    current: dict[str, Any],
    machine: dict[str, Any],
    real_backend: dict[str, Any],
) -> dict[str, Any]:
    data = deepcopy(current)
    elements = {str(element["id"]): element for element in machine["elements"]}
    channels = real_backend["channels"]

    sync_existing_knobs(data["knobs"], elements, channels)
    sync_existing_objects(data["objects"], elements, channels)
    ensure_rf_phase_knobs(data["knobs"], elements, channels)
    ensure_llrfpb_phase_object(data, elements, channels)
    rename_rf_phase_objects(data["objects"])
    return data


def sync_existing_knobs(
    knobs: list[dict[str, Any]],
    elements: dict[str, dict[str, Any]],
    channels: dict[str, dict[str, str]],
) -> None:
    for knob in knobs:
        element = find_element_for_item(knob["id"], elements)
        if not element:
            continue
        channel = channels.get(element["id"], {})
        if knob["id"].endswith("_current"):
            sync_knob_pv(knob, element, channel, "current_set", "current_readback")
        elif knob["id"].endswith("_voltage"):
            sync_knob_pv(knob, element, channel, "voltage_set", "voltage_readback")
        elif knob["id"].endswith("_phase_set"):
            sync_knob_pv(knob, element, channel, "phase_set", "phase_readback")


def sync_knob_pv(
    knob: dict[str, Any],
    element: dict[str, Any],
    channel: dict[str, str],
    write_key: str,
    readback_key: str,
) -> None:
    if write_key not in channel or readback_key not in channel:
        return
    knob["write_pv"] = channel[write_key]
    knob["readback_pv"] = channel[readback_key]
    limit = (element.get("limits") or {}).get(write_key)
    if limit:
        knob["limits"] = {"low": limit["low"], "high": limit["high"]}
        knob["unit"] = limit.get("unit", knob.get("unit", ""))


def sync_existing_objects(
    objects: list[dict[str, Any]],
    elements: dict[str, dict[str, Any]],
    channels: dict[str, dict[str, str]],
) -> None:
    for obj in objects:
        element = find_element_for_item(obj["id"], elements)
        if not element:
            continue
        expected = expected_object_read_pv(obj["id"], element, channels.get(element["id"], {}))
        if expected:
            obj["read_pv"] = expected


def expected_object_read_pv(
    object_id: str,
    element: dict[str, Any],
    channel: dict[str, str],
) -> str | None:
    if element.get("kind") == "bpm":
        if object_id.endswith("_x"):
            return channel.get("x")
        if object_id.endswith("_y"):
            return channel.get("y")
    suffix_to_key = {
        "_sigx": "sigx",
        "_sigy": "sigy",
        "_exposure": "exposure_time",
        "_peak_current": "peak_current",
        "_charge": "charge",
        "_setpoint": "setpoint",
        "_phase": "phase_readback",
    }
    for suffix, key in suffix_to_key.items():
        if object_id.endswith(suffix):
            return channel.get(key)
    return None


def ensure_rf_phase_knobs(
    knobs: list[dict[str, Any]],
    elements: dict[str, dict[str, Any]],
    channels: dict[str, dict[str, str]],
) -> None:
    existing = {knob["id"] for knob in knobs}
    for element_id, element in elements.items():
        if element.get("kind") != "rf":
            continue
        knob = build_rf_phase_knob(element, channels.get(element_id, {}))
        if knob and knob["id"] not in existing:
            knobs.append(knob)
            existing.add(knob["id"])


def build_rf_phase_knob(
    element: dict[str, Any],
    channel: dict[str, str],
) -> dict[str, Any] | None:
    if "phase_set" not in channel or "phase_readback" not in channel:
        return None
    element_id = element["id"]
    limit = (element.get("limits") or {}).get("phase_set") or {
        "low": -180,
        "high": 180,
        "unit": "deg",
    }
    return {
        "id": f"{slug(element_id)}_phase_set",
        "name": f"{element.get('display_name', element_id)} phase setpoint",
        "group": "rf_phase",
        "write_pv": channel["phase_set"],
        "readback_pv": channel["phase_readback"],
        "unit": limit.get("unit", "deg"),
        "access": "rw",
        "limits": {"low": limit.get("low", -180), "high": limit.get("high", 180)},
        "step_hint": 1.0,
        "settle": {
            "mode": "readback_tolerance",
            "delay_sec": 0.5,
            "readback_tolerance": 0.1,
            "max_wait_sec": 5,
        },
        "tags": unique_tags(element, "rf", "phase"),
        "note": (
            "PV and operating limits are imported from half_linac real control backend "
            "and machine definition; confirm the working point before writing."
        ),
    }


def ensure_llrfpb_phase_object(
    data: dict[str, Any],
    elements: dict[str, dict[str, Any]],
    channels: dict[str, dict[str, str]],
) -> None:
    element = elements.get("LLRFPB")
    channel = channels.get("LLRFPB", {})
    if not element or "phase_readback" not in channel:
        return
    object_ids = {obj["id"] for obj in data["objects"]}
    if "llrfpb_phase" not in object_ids:
        data["objects"].append(
            {
                "id": "llrfpb_phase",
                "name": "Prebuncher LLRF phase readback",
                "group": "rf_phase",
                "read_pv": channel["phase_readback"],
                "unit": "deg",
                "precision": 6,
                "kind": "scalar",
                "access": "ro",
                "analysis": {"jitter": True, "correlation": True, "spectrum": True},
                "value_reducer": "none",
                "capture_mode": "scalar",
                "tags": unique_tags(element, "rf", "phase"),
                "note": "PV is imported from half_linac real control backend.",
            }
        )
    for preset in data["presets"]:
        if preset.get("id") == "orbit_and_diagnostics":
            targets = preset.setdefault("targets", [])
            if "llrfpb_phase" not in targets:
                targets.append("llrfpb_phase")


def rename_rf_phase_objects(objects: list[dict[str, Any]]) -> None:
    for obj in objects:
        if obj["id"].startswith("llrf") and obj["id"].endswith("_phase"):
            obj["name"] = obj["name"].replace("phase setpoint", "phase readback")
        if obj["id"] == "llrfpb_phase":
            obj["name"] = "Prebuncher LLRF phase readback"


def find_element_for_item(item_id: str, elements: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    item_base = item_id
    for suffix in (
        "_peak_current",
        "_phase_set",
        "_current",
        "_voltage",
        "_setpoint",
        "_exposure",
        "_charge",
        "_phase",
        "_sigx",
        "_sigy",
        "_x",
        "_y",
    ):
        if item_base.endswith(suffix):
            item_base = item_base[: -len(suffix)]
            break
    for element in elements.values():
        if slug(element["id"]) == item_base:
            return element
    return None


def unique_tags(element: dict[str, Any], *extra: str) -> list[str]:
    return list(dict.fromkeys([*(element.get("tags") or []), *extra]))


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")


if __name__ == "__main__":
    raise SystemExit(main())
