"""Design setpoint extraction and control-backend transfer planning.

This module deliberately has no EPICS or Qt dependency.  It only turns a
design lattice and machine-profile mappings into a validated, inspectable
plan.  Runtime code can then execute that plan against a selected control
backend.
"""

from __future__ import annotations

import math
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence

from half_linac.src.shared.machine_profile import MachineProfile, MachineProfileError
from half_linac.src.virtual_machine.lattice_parser import lattice_parser


@dataclass(frozen=True)
class DesignSetpoint:
    element_id: str
    kind: str
    field: str
    value: float
    source_path: Path


@dataclass(frozen=True)
class StagedSetpoint:
    element_id: str
    field: str
    target_value: float
    origin: str


@dataclass(frozen=True)
class TransferItem:
    element_id: str
    field: str
    design_value: float | None
    current_value: float | None
    target_value: float | None
    target_origin: str | None
    pv_name: str | None
    unit: str
    status: str
    message: str = ""


@dataclass(frozen=True)
class TransferPlan:
    target_backend: str
    items: tuple[TransferItem, ...]
    diagnostics: tuple[str, ...] = ()

    @property
    def blockers(self) -> tuple[TransferItem, ...]:
        return tuple(item for item in self.items if item.status == "blocked")

    @property
    def writable_items(self) -> tuple[TransferItem, ...]:
        return tuple(item for item in self.items if item.status == "ready")


def save_target_workspace(
    path: str | Path,
    *,
    machine_id: str,
    staged_setpoints: Sequence[StagedSetpoint],
) -> None:
    """Save staged VM Quad K1 targets without machine readback state."""
    destination = Path(path)
    payload = {
        "schema_version": "1",
        "machine": str(machine_id).strip(),
        "target_backend": "vm",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "setpoints": [
            {
                "element_id": item.element_id,
                "field": item.field,
                "target_value": item.target_value,
                "origin": item.origin,
            }
            for item in staged_setpoints
        ],
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def load_target_workspace(
    path: str | Path,
    *,
    expected_machine_id: str,
) -> tuple[StagedSetpoint, ...]:
    """Load and validate a VM Quad K1 target workspace."""
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read workspace {source}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != "1":
        raise ValueError("Unsupported or missing workspace schema_version.")
    machine_id = str(payload.get("machine", "")).strip()
    if machine_id != str(expected_machine_id).strip():
        raise ValueError(
            f"Workspace machine {machine_id!r} does not match {expected_machine_id!r}."
        )
    if str(payload.get("target_backend", "")).strip().lower() != "vm":
        raise ValueError("Only VM target workspaces are supported.")
    raw_setpoints = payload.get("setpoints")
    if not isinstance(raw_setpoints, list):
        raise ValueError("Workspace setpoints must be a list.")

    staged: list[StagedSetpoint] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(raw_setpoints):
        if not isinstance(raw, dict):
            raise ValueError(f"Workspace setpoint {index + 1} must be an object.")
        element_id = str(raw.get("element_id", "")).strip().upper()
        field = str(raw.get("field", "")).strip().upper()
        origin = str(raw.get("origin", "")).strip().lower()
        if not element_id or field != "K1":
            raise ValueError(
                f"Workspace setpoint {index + 1} must identify a Quad K1 element."
            )
        key = (element_id, field)
        if key in seen:
            raise ValueError(f"Duplicate workspace setpoint: {element_id}.{field}.")
        seen.add(key)
        if origin not in {"design", "current", "manual"}:
            raise ValueError(f"Invalid workspace origin for {element_id}.{field}: {origin!r}.")
        try:
            target_value = float(raw.get("target_value"))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Invalid workspace target for {element_id}.{field}."
            ) from exc
        if not math.isfinite(target_value):
            raise ValueError(f"Workspace target for {element_id}.{field} must be finite.")
        staged.append(StagedSetpoint(element_id, field, target_value, origin))
    return tuple(staged)


def extract_design_setpoints(
    lattice_path: str | Path,
    *,
    element_kind: str = "quad",
    field: str = "K1",
    line_name: str = "ALL_MAIN",
) -> tuple[DesignSetpoint, ...]:
    """Extract unique design fields from the expanded bootstrap usedline."""
    path = Path(lattice_path)
    parser = lattice_parser(str(path), line_name)
    lattice, usedline = parser.get_lattice_tracklinenameslist()
    expected_kind = element_kind.upper()
    values: list[DesignSetpoint] = []
    seen: set[str] = set()
    for element_name in usedline:
        element = lattice.get(element_name)
        if not element or str(element.get("TYPE", "")).upper() != expected_kind:
            continue
        element_id = str(element.get("NAME", element_name)).upper()
        if element_id in seen:
            continue
        seen.add(element_id)
        raw_value = element.get(field)
        if raw_value is None:
            raise ValueError(f"Design element {element_id} is missing required field {field}.")
        try:
            value = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid design value {element_id}.{field}: {raw_value!r}") from exc
        if not math.isfinite(value):
            raise ValueError(f"Design value {element_id}.{field} must be finite.")
        values.append(DesignSetpoint(element_id, element_kind, field, value, path))
    return tuple(values)


def build_transfer_plan(
    profile: MachineProfile,
    design_setpoints: Sequence[DesignSetpoint],
    *,
    target_backend: str = "vm",
    current_values: Mapping[str, float | None] | None = None,
    staged_setpoints: Sequence[StagedSetpoint] = (),
) -> TransferPlan:
    """Build a VM-only K1 plan from independent design, current, and target values."""
    backend = str(target_backend).strip().lower()
    if backend != "vm":
        raise MachineProfileError(
            f"Design setpoint transfer target {target_backend!r} is not implemented; only 'vm' is supported."
        )
    current_values = current_values or {}
    items: list[TransferItem] = []
    diagnostics: list[str] = []
    staged_by_key: dict[tuple[str, str], StagedSetpoint] = {}
    for staged in staged_setpoints:
        staged_key = (staged.element_id.upper(), staged.field.upper())
        if staged_key in staged_by_key:
            diagnostics.append(
                f"Duplicate staged setpoint skipped: {staged_key[0]}.{staged_key[1]}."
            )
            continue
        staged_by_key[staged_key] = staged
    seen: set[tuple[str, str]] = set()
    for setpoint in design_setpoints:
        key = (setpoint.element_id.upper(), setpoint.field.upper())
        if key in seen:
            diagnostics.append(f"Duplicate design setpoint skipped: {key[0]}.{key[1]}.")
            continue
        seen.add(key)
        try:
            element = profile.get_element(key[0])
        except MachineProfileError:
            diagnostics.append(f"Design element is not present in machine profile: {key[0]}.")
            items.append(
                TransferItem(
                    key[0], key[1], setpoint.value, None, None, None, None,
                    "1/m^2", "blocked", "Unknown machine element.",
                )
            )
            continue
        if element.kind != "quad" or key[1] != "K1":
            items.append(
                TransferItem(
                    key[0], key[1], setpoint.value, None, None, None, None,
                    "1/m^2", "blocked", "Only quad K1 is supported.",
                )
            )
            continue
        try:
            from half_linac.src.shared.machine_profile import resolve_write_target

            target = resolve_write_target(profile, key[0], quantity="K1", mode=backend, unit="1/m^2")
        except MachineProfileError as exc:
            items.append(
                TransferItem(
                    key[0], key[1], setpoint.value, None, None, None, None,
                    "1/m^2", "blocked", str(exc),
                )
            )
            continue
        current = current_values.get(key[0])
        try:
            normalized_current = float(current) if current is not None else None
        except (TypeError, ValueError):
            normalized_current = None
        if normalized_current is None or not math.isfinite(normalized_current):
            items.append(
                TransferItem(
                    key[0], key[1], setpoint.value, current, None, None,
                    target.pv_name,
                    "1/m^2", "blocked",
                    f"Current VM value is unavailable: {target.pv_name}",
                )
            )
            continue
        staged = staged_by_key.get(key)
        if staged is None:
            items.append(
                TransferItem(
                    key[0], key[1], setpoint.value, normalized_current,
                    None, None, target.pv_name, target.unit or "1/m^2",
                    "not_staged", "Target is not staged.",
                )
            )
            continue
        try:
            normalized_target = float(staged.target_value)
        except (TypeError, ValueError):
            normalized_target = math.nan
        origin = str(staged.origin).strip().lower()
        if not math.isfinite(normalized_target):
            items.append(
                TransferItem(
                    key[0], key[1], setpoint.value, normalized_current,
                    None, origin or None, target.pv_name,
                    target.unit or "1/m^2", "blocked",
                    "Target must be a finite number.",
                )
            )
            continue
        if target.machine_limit and not target.machine_limit.contains(normalized_target):
            items.append(
                TransferItem(
                    key[0], key[1], setpoint.value, normalized_current,
                    normalized_target, origin or None, target.pv_name,
                    target.unit or "1/m^2", "blocked",
                    f"Target is outside machine limit {target.machine_limit.describe()}.",
                )
            )
            continue
        if origin not in {"design", "current", "manual"}:
            items.append(
                TransferItem(
                    key[0], key[1], setpoint.value, normalized_current,
                    normalized_target, origin or None, target.pv_name,
                    target.unit or "1/m^2", "blocked",
                    f"Unknown target origin: {staged.origin!r}.",
                )
            )
            continue
        items.append(
            TransferItem(
                key[0], key[1], setpoint.value, normalized_current,
                normalized_target, origin, target.pv_name,
                target.unit or "1/m^2", "ready",
            )
        )
    return TransferPlan(backend, tuple(items), tuple(diagnostics))
