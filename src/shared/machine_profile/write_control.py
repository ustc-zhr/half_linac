from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .models import AppContext, MachineProfile, MachineProfileError, normalize_mode
from .resolver import get_workflow


WRITE_ALLOWED = "allowed"
WRITE_BLOCKED = "blocked"


def workflow_write_policy(
    target: MachineProfile | AppContext,
    workflow_name: str,
    mode: str | None = None,
) -> str:
    profile = target.profile if isinstance(target, AppContext) else target
    fallback_mode = target.control_backend.name if isinstance(target, AppContext) else profile.machine.default_mode
    normalized_mode = normalize_mode(mode or fallback_mode, "mode")
    workflow = get_workflow(profile, workflow_name)
    raw_policy = workflow.get("write_control", WRITE_ALLOWED)

    if isinstance(raw_policy, Mapping):
        raw_value = raw_policy.get(normalized_mode, raw_policy.get("default", WRITE_ALLOWED))
    else:
        raw_value = raw_policy

    return _normalize_write_policy(raw_value, f"workflows.{workflow_name}.write_control")


def workflow_writes_allowed(
    target: MachineProfile | AppContext,
    workflow_name: str,
    mode: str | None = None,
) -> bool:
    return workflow_write_policy(target, workflow_name, mode) == WRITE_ALLOWED


def require_workflow_write_allowed(
    target: MachineProfile | AppContext,
    workflow_name: str,
    operation: str,
    mode: str | None = None,
) -> None:
    if workflow_writes_allowed(target, workflow_name, mode):
        return

    profile = target.profile if isinstance(target, AppContext) else target
    fallback_mode = target.control_backend.name if isinstance(target, AppContext) else profile.machine.default_mode
    normalized_mode = normalize_mode(mode or fallback_mode, "mode")
    raise MachineProfileError(
        f"{operation} is blocked for {profile.machine.display_name} {normalized_mode!r} mode "
        f"by workflows.{workflow_name}.write_control."
    )


def _normalize_write_policy(value: Any, location: str) -> str:
    if isinstance(value, bool):
        return WRITE_ALLOWED if value else WRITE_BLOCKED

    text = str(value).strip().lower().replace("_", "-").replace(" ", "-")
    if text in {"allowed", "allow", "enabled", "enable", "write", "write-allowed", "yes", "true"}:
        return WRITE_ALLOWED
    if text in {"blocked", "block", "disabled", "disable", "read-only", "readonly", "no", "false"}:
        return WRITE_BLOCKED

    raise MachineProfileError(
        f"{location} must be {WRITE_ALLOWED!r} or {WRITE_BLOCKED!r}, got {value!r}."
    )
