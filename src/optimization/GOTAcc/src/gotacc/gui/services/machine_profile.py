from __future__ import annotations

import copy
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


MACHINE_PROFILE_SCHEMA = "gotacc.machine_profile"
MACHINE_PROFILE_VERSION = 1
MACHINE_PROFILE_FIELDS = (
    "ca_address",
    "restore_on_abort",
    "readback_check",
    "readback_tol",
    "set_interval",
    "sample_interval",
    "write_timeout",
    "write_policy",
    "mapping",
    "write_links",
    "policy_bindings",
    "policy_presets",
)


def _profile_slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")
    return normalized or "machine"


@dataclass(frozen=True)
class MachineProfile:
    profile_id: str
    name: str
    machine: dict[str, Any]
    version: int = MACHINE_PROFILE_VERSION

    @classmethod
    def create(
        cls,
        name: str,
        machine: Mapping[str, Any],
        *,
        profile_id: str | None = None,
    ) -> "MachineProfile":
        display_name = str(name).strip()
        if not display_name:
            raise ValueError("Machine profile name cannot be empty.")
        if not isinstance(machine, Mapping):
            raise ValueError("Machine profile 'machine' must be a mapping.")
        stable_id = str(profile_id or "").strip().lower()
        if not stable_id:
            stable_id = f"{_profile_slug(display_name)}-{uuid.uuid4().hex[:8]}"
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", stable_id):
            raise ValueError(
                "Machine profile ID must use lowercase letters, digits, '.', '_' or '-'."
            )
        machine_data = {
            field: copy.deepcopy(machine[field])
            for field in MACHINE_PROFILE_FIELDS
            if field in machine
        }
        for field in ("mapping", "write_links", "policy_bindings", "policy_presets"):
            value = machine_data.setdefault(field, [])
            if not isinstance(value, list):
                raise ValueError(f"Machine profile field {field!r} must be a list.")
        return cls(profile_id=stable_id, name=display_name, machine=machine_data)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MachineProfile":
        if not isinstance(payload, Mapping):
            raise ValueError("Machine profile document must be a mapping.")
        if payload.get("schema") != MACHINE_PROFILE_SCHEMA:
            raise ValueError(
                f"Unsupported machine profile schema: {payload.get('schema')!r}."
            )
        try:
            version = int(payload.get("version", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("Machine profile version must be an integer.") from exc
        if version != MACHINE_PROFILE_VERSION:
            raise ValueError(
                f"Unsupported machine profile version {version}; "
                f"expected {MACHINE_PROFILE_VERSION}."
            )
        profile_id = str(payload.get("profile_id", "")).strip()
        if not profile_id:
            raise ValueError("Machine profile ID cannot be empty.")
        profile = cls.create(
            str(payload.get("name", "")),
            payload.get("machine", {}) or {},
            profile_id=profile_id,
        )
        return cls(
            profile_id=profile.profile_id,
            name=profile.name,
            machine=profile.machine,
            version=version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": MACHINE_PROFILE_SCHEMA,
            "version": self.version,
            "profile_id": self.profile_id,
            "name": self.name,
            "machine": copy.deepcopy(self.machine),
        }


def load_machine_profile(path: str | Path) -> MachineProfile:
    profile_path = Path(path)
    with profile_path.open("r", encoding="utf-8") as stream:
        payload = json.load(stream)
    return MachineProfile.from_dict(payload)


def save_machine_profile(profile: MachineProfile, path: str | Path) -> Path:
    profile_path = Path(path)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    with profile_path.open("w", encoding="utf-8") as stream:
        json.dump(profile.to_dict(), stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return profile_path
