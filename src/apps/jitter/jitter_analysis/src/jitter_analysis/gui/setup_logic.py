from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Callable


def parse_optional_datetime(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def list_saved_setups(
    root_dir: str | Path,
    mode_display_name: Callable[[str], str],
) -> list[dict[str, object]]:
    root = Path(root_dir)
    if not root.exists():
        return []

    entries: list[dict[str, object]] = []
    for path in root.rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        if "task_mode" not in payload:
            continue

        saved_at = parse_optional_datetime(payload.get("saved_at"))
        entries.append(
            {
                "path": str(path.resolve()),
                "saved_at": saved_at,
                "saved_at_text": saved_at.strftime("%Y-%m-%d %H:%M:%S") if saved_at else path.stem,
                "task_mode": mode_display_name(str(payload.get("task_mode", ""))),
                "operator": str(payload.get("operator", "")),
                "object_count": len(payload.get("selected_object_ids", []) or []),
                "knob_count": len(payload.get("selected_knob_ids", []) or []),
                "save_dir": str(payload.get("save_dir", "")),
                "config_path": str(payload.get("config_path", "")),
                "notes": str(payload.get("notes", "")),
            }
        )

    entries.sort(
        key=lambda entry: (
            entry.get("saved_at") is not None,
            entry.get("saved_at") or datetime.min,
            str(entry.get("path", "")),
        ),
        reverse=True,
    )
    return entries
