from __future__ import annotations

import json
import math
from pathlib import Path


DEFAULT_MACHINE_TIMING = {
    "set_interval": 1.0,
    "sample_interval": 0.2,
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _preferences_store_path() -> Path:
    return _repo_root() / ".cache" / "gui_preferences.json"


def _valid_interval(value: object, fallback: float) -> float:
    try:
        interval = float(value)
    except (TypeError, ValueError):
        return fallback
    return interval if math.isfinite(interval) and interval >= 0 else fallback


def load_machine_timing_preferences(path: Path | None = None) -> dict[str, float]:
    target = path or _preferences_store_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        payload = {}
    timing = payload.get("machine_timing", {}) if isinstance(payload, dict) else {}
    if not isinstance(timing, dict):
        timing = {}
    return {
        key: _valid_interval(timing.get(key), fallback)
        for key, fallback in DEFAULT_MACHINE_TIMING.items()
    }


def save_machine_timing_preferences(
    set_interval: float,
    sample_interval: float,
    path: Path | None = None,
) -> dict[str, float]:
    target = path or _preferences_store_path()
    timing = {
        "set_interval": _valid_interval(
            set_interval,
            DEFAULT_MACHINE_TIMING["set_interval"],
        ),
        "sample_interval": _valid_interval(
            sample_interval,
            DEFAULT_MACHINE_TIMING["sample_interval"],
        ),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {}
    try:
        existing = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(existing, dict):
            payload.update(existing)
    except (OSError, ValueError, TypeError):
        pass
    payload["machine_timing"] = timing
    temporary = target.with_suffix(f"{target.suffix}.tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(target)
    return timing
