from __future__ import annotations

import json
import os
import shutil
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from .validator import validate_config_dict


def config_data_from_text(source_text: str) -> dict[str, Any]:
    data = json.loads(source_text)
    if not isinstance(data, dict):
        raise ValueError("Config root must be a JSON object")
    return data


def prepare_edited_config(
    source_text: str,
    objects: list[dict[str, Any]],
    groups: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    data = deepcopy(config_data_from_text(source_text))
    data["objects"] = deepcopy(objects)
    if groups is not None:
        data["groups"] = deepcopy(groups)

    group_ids = {str(group.get("id", "")).strip() for group in data.get("groups", [])}
    missing_groups = {
        str(obj.get("group", "")).strip()
        for obj in objects
        if str(obj.get("group", "")).strip() and str(obj.get("group", "")).strip() not in group_ids
    }
    for group_id in sorted(missing_groups):
        data.setdefault("groups", []).append(
            {
                "id": group_id,
                "label": group_id.replace("_", " ").title(),
                "kind": "object",
                "color": "#607d8b",
                "order": 900,
            }
        )
    validate_config_dict(data)
    return data


def config_text(data: dict[str, Any]) -> str:
    validate_config_dict(data)
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def save_config_file(path: str | Path, data: dict[str, Any]) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = config_text(data)

    backup = target.with_name(target.name + ".bak")
    if target.exists():
        shutil.copy2(target, backup)

    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, target)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return text
