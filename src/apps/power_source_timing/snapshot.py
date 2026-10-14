"""Portable, strictly validated snapshots of RF power-source timing setpoints."""
from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


QUANTITIES = ("delay", "width")
UNIT = "us"
MAXIMUM_US = 1.0e9


def targets(runtime):
    return [
        (
            group.element_id,
            device,
            quantity,
            group.pv(device, f"{quantity}_set"),
        )
        for group in runtime.groups
        for device in group.devices
        for quantity in QUANTITIES
    ]


def make_snapshot(runtime, values):
    entries = targets(runtime)
    if len(values) != len(entries):
        raise ValueError("Incomplete timing AO snapshot")
    data = {
        "version": 1,
        "machine": runtime.context.machine.id,
        "backend": runtime.context.control_backend.name,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "parameters": [
            {
                "group": group,
                "device": device,
                "quantity": quantity,
                "pv": pv,
                "unit": UNIT,
                "value": value,
            }
            for (group, device, quantity, pv), value in zip(entries, values)
        ],
    }
    validate_snapshot(runtime, data)
    return data


def validate_snapshot(runtime, data):
    if (
        not isinstance(data, dict)
        or type(data.get("version")) is not int
        or data["version"] != 1
    ):
        raise ValueError("Unsupported timing snapshot format/version")
    if (
        data.get("machine") != runtime.context.machine.id
        or data.get("backend") != runtime.context.control_backend.name
    ):
        raise ValueError("Snapshot machine/backend does not match this application")
    datetime.fromisoformat(data["saved_at"])
    rows = data.get("parameters")
    if not isinstance(rows, list):
        raise ValueError("Missing timing parameters")

    found = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid timing parameter entry")
        key = (row.get("group"), row.get("device"), row.get("quantity"))
        if key in found:
            raise ValueError(f"Duplicate timing parameter: {key}")
        found[key] = row

    expected = targets(runtime)
    expected_keys = {
        (group, device, quantity) for group, device, quantity, _pv in expected
    }
    if set(found) != expected_keys:
        raise ValueError(
            "Snapshot timing parameter set does not match this application"
        )

    values = []
    for group, device, quantity, pv in expected:
        row = found[group, device, quantity]
        value = row.get("value")
        if row.get("pv") != pv or row.get("unit") != UNIT:
            raise ValueError(f"{group} {device} {quantity}: PV/unit mismatch")
        if (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or not runtime.minimum_us <= value <= MAXIMUM_US
        ):
            raise ValueError(
                f"{group} {device} {quantity}: invalid or out-of-range value"
            )
        values.append(float(value))
    return values


def load_snapshot(path):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(
        Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique_pairs
    )


def save_snapshot(path, data):
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as stream:
            temporary = stream.name
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
