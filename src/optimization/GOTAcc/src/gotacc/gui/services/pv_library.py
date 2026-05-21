from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class PVLibraryItem:
    name: str
    pv_name: str
    readback: str
    group: str
    note: str = ""


@dataclass(frozen=True)
class PVLibraryDocument:
    machine: str
    description: str
    knobs: tuple[PVLibraryItem, ...]
    objectives: tuple[PVLibraryItem, ...]
    source: str

def _default_readback(pv_name: str, role: str) -> str:
    if role == "knob" and pv_name.endswith(":ao"):
        return pv_name[:-2] + "ai"
    return pv_name


def _default_name_from_pv(pv_name: str) -> str:
    text = str(pv_name).strip()
    if not text:
        return ""
    parts = [part for part in text.split(":") if part]
    if text.endswith(":current:ao") and len(parts) >= 3:
        return parts[-3]
    if text.endswith(":current:ai") and len(parts) >= 3:
        return parts[-3]
    if len(parts) >= 2 and parts[-2] in {"WAV", "SET", "GET"}:
        return parts[-3] if len(parts) >= 3 else parts[-1]
    return parts[-1]


def _parse_item(raw: Any, *, role: str, index: int) -> PVLibraryItem:
    if not isinstance(raw, Mapping):
        raise ValueError(f"{role} entry {index} must be an object.")

    pv_name = str(raw.get("pv_name", "")).strip()
    if not pv_name:
        raise ValueError(f"{role} entry {index} is missing 'pv_name'.")

    name = str(raw.get("name", _default_name_from_pv(pv_name))).strip()
    if not name:
        name = _default_name_from_pv(pv_name)

    readback = str(raw.get("readback", _default_readback(pv_name, role))).strip() or _default_readback(pv_name, role)
    group = str(raw.get("group", "main" if role == "knob" else "metric")).strip()
    note = str(raw.get("note", "")).strip()

    return PVLibraryItem(
        name=name,
        pv_name=pv_name,
        readback=readback,
        group=group or ("main" if role == "knob" else "metric"),
        note=note,
    )


def _parse_list(payload: Any, *, role: str) -> tuple[PVLibraryItem, ...]:
    if payload is None:
        return ()
    if not isinstance(payload, list):
        raise ValueError(f"'{role}s' must be a list.")
    return tuple(_parse_item(item, role=role, index=i + 1) for i, item in enumerate(payload))


def load_pv_library_file(path: str | Path) -> PVLibraryDocument:
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    text = file_path.read_text(encoding="utf-8")

    if suffix == ".json":
        payload = json.loads(text)
    elif suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise RuntimeError("Loading YAML PV libraries requires PyYAML.") from exc
        payload = yaml.safe_load(text)
    else:
        raise ValueError("PV library must be a .json, .yaml or .yml file.")

    if not isinstance(payload, Mapping):
        raise ValueError("PV library file must contain a single object at the top level.")

    machine = str(payload.get("machine", file_path.stem)).strip() or file_path.stem
    description = str(payload.get("description", "")).strip()
    knobs = _parse_list(payload.get("knobs", []), role="knob")
    objectives = _parse_list(payload.get("objectives", []), role="objective")

    if not knobs and not objectives:
        raise ValueError("PV library file does not contain any 'knobs' or 'objectives' entries.")

    return PVLibraryDocument(
        machine=machine,
        description=description,
        knobs=knobs,
        objectives=objectives,
        source=file_path.name,
    )


def pv_library_summary_text(document: PVLibraryDocument) -> str:
    parts = [
        f"Machine: {document.machine}",
        f"Knobs: {len(document.knobs)}",
        f"Objectives: {len(document.objectives)}",
        f"Source: {document.source}",
    ]
    if document.description:
        parts.append(document.description)
    return "  |  ".join(parts)
