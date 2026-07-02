from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .models import AppContext, MachineProfile, MachineProfileError
from .resolver import get_workflow


REAL_STATUS_NOT_SUPPORTED = "not_supported"
REAL_STATUS_READ_ONLY = "read_only"
REAL_STATUS_WRITE_BLOCKED = "write_blocked"
REAL_STATUS_WRITE_SMOKE_PASSED = "write_smoke_passed"
REAL_STATUS_COMMISSIONED = "commissioned"

REAL_COMMISSIONING_STATUSES = (
    REAL_STATUS_NOT_SUPPORTED,
    REAL_STATUS_READ_ONLY,
    REAL_STATUS_WRITE_BLOCKED,
    REAL_STATUS_WRITE_SMOKE_PASSED,
    REAL_STATUS_COMMISSIONED,
)

REAL_COMMISSIONING_WORKFLOWS_BY_APP = {
    "orbit_correct": "orbit",
    "orbit_display": "orbit",
    "beam_monitor": "beam_monitor",
    "energy_spectrum": "energy_spectrum",
    "bba": "bba",
    "emit_measure": "emit_measure",
    "solenoid_centering": "solenoid_centering",
}

_REAL_STATUS_LABELS = {
    REAL_STATUS_NOT_SUPPORTED: "NOT SUPPORTED",
    REAL_STATUS_READ_ONLY: "READ ONLY",
    REAL_STATUS_WRITE_BLOCKED: "WRITE BLOCKED",
    REAL_STATUS_WRITE_SMOKE_PASSED: "WRITE SMOKE PASSED",
    REAL_STATUS_COMMISSIONED: "COMMISSIONED",
}

_REAL_STATUS_TONES = {
    REAL_STATUS_NOT_SUPPORTED: "warning",
    REAL_STATUS_READ_ONLY: "subtle",
    REAL_STATUS_WRITE_BLOCKED: "warning",
    REAL_STATUS_WRITE_SMOKE_PASSED: "success",
    REAL_STATUS_COMMISSIONED: "success",
}

_REAL_STATUS_ALIASES = {
    "unsupported": REAL_STATUS_NOT_SUPPORTED,
    "not-supported": REAL_STATUS_NOT_SUPPORTED,
    "not-supported-real": REAL_STATUS_NOT_SUPPORTED,
    "read-only": REAL_STATUS_READ_ONLY,
    "readonly": REAL_STATUS_READ_ONLY,
    "read-only-smoke-passed": REAL_STATUS_READ_ONLY,
    "blocked": REAL_STATUS_WRITE_BLOCKED,
    "write-blocked": REAL_STATUS_WRITE_BLOCKED,
    "write-disabled": REAL_STATUS_WRITE_BLOCKED,
    "write-smoke": REAL_STATUS_WRITE_SMOKE_PASSED,
    "write-smoke-passed": REAL_STATUS_WRITE_SMOKE_PASSED,
    "commissioned": REAL_STATUS_COMMISSIONED,
}


def real_commissioning_status(
    target: MachineProfile | AppContext,
    app_name: str | None = None,
) -> str:
    profile = target.profile if isinstance(target, AppContext) else target
    resolved_app_name = app_name or (target.app_name if isinstance(target, AppContext) else "")
    if not resolved_app_name:
        raise MachineProfileError("app_name is required when resolving real commissioning status.")

    workflow_name = real_commissioning_workflow_name(resolved_app_name)
    workflow = get_workflow(profile, workflow_name)
    raw_status = workflow.get("real_status")
    location = f"workflows.{workflow_name}.real_status"
    if raw_status is None:
        raise MachineProfileError(f"{location} is required for app {resolved_app_name!r}.")

    if isinstance(raw_status, Mapping):
        raw_value = raw_status.get(resolved_app_name, raw_status.get("default"))
        if raw_value is None:
            raise MachineProfileError(
                f"{location} must include {resolved_app_name!r} or a 'default' entry."
            )
        location = f"{location}.{resolved_app_name}"
    else:
        raw_value = raw_status

    return normalize_real_commissioning_status(raw_value, location)


def real_commissioning_workflow_name(app_name: str) -> str:
    try:
        return REAL_COMMISSIONING_WORKFLOWS_BY_APP[app_name]
    except KeyError as exc:
        supported = ", ".join(sorted(REAL_COMMISSIONING_WORKFLOWS_BY_APP))
        raise MachineProfileError(
            f"Unsupported app_name {app_name!r} for real commissioning status. "
            f"Expected one of: {supported}."
        ) from exc


def normalize_real_commissioning_status(value: Any, location: str = "real_status") -> str:
    text = str(value).strip().lower().replace("_", "-").replace(" ", "-")
    status = _REAL_STATUS_ALIASES.get(text)
    if status is not None:
        return status

    supported = ", ".join(REAL_COMMISSIONING_STATUSES)
    raise MachineProfileError(f"{location} must be one of: {supported}. Got {value!r}.")


def real_commissioning_status_label(status: str) -> str:
    return _REAL_STATUS_LABELS[normalize_real_commissioning_status(status)]


def real_commissioning_status_tone(status: str) -> str:
    return _REAL_STATUS_TONES[normalize_real_commissioning_status(status)]
