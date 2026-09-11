"""Portable, strictly validated snapshots of LLRF AO setpoints."""
from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .profile_runtime import QUANTITIES


def targets(runtime):
    return [(group.element_id, name, group.quantities[name])
            for group in runtime.groups for name in QUANTITIES]


def make_snapshot(runtime, values):
    entries = targets(runtime)
    if len(values) != len(entries):
        raise ValueError("Incomplete AO snapshot")
    data = {
        "version": 1, "machine": runtime.context.machine.id,
        "backend": runtime.context.control_backend.name,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "parameters": [dict(device=device, quantity=name, pv=spec.setpoint_pv,
                            unit=spec.unit, value=value)
                       for (device, name, spec), value in zip(entries, values)],
    }
    validate_snapshot(runtime, data)
    return data


def validate_snapshot(runtime, data):
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
        raise ValueError("Unsupported snapshot format/version")
    if data.get("machine") != runtime.context.machine.id or data.get("backend") != runtime.context.control_backend.name:
        raise ValueError("Snapshot machine/backend does not match this application")
    datetime.fromisoformat(data["saved_at"])
    rows = data.get("parameters")
    if not isinstance(rows, list):
        raise ValueError("Missing parameters")
    found = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid parameter entry")
        key = (row.get("device"), row.get("quantity"))
        if key in found:
            raise ValueError(f"Duplicate parameter: {key}")
        found[key] = row
    expected = targets(runtime)
    if set(found) != {(device, name) for device, name, _ in expected}:
        raise ValueError("Snapshot parameter set does not match this application")
    values = []
    for device, name, spec in expected:
        row = found[device, name]
        value = row.get("value")
        if row.get("pv") != spec.setpoint_pv or row.get("unit") != spec.unit:
            raise ValueError(f"{device} {name}: PV/unit mismatch")
        if type(value) not in (int, float) or not math.isfinite(value) or not spec.low <= value <= spec.high:
            raise ValueError(f"{device} {name}: invalid or out-of-range value")
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
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique_pairs)


def save_snapshot(path, data):
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as stream:
            temporary = stream.name
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
