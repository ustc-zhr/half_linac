from __future__ import annotations

import math
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Protocol

from half_linac.src.shared.machine_profile import (
    AppContext,
    MachineProfileError,
    WriteTarget,
    get_workflow,
    require_workflow_write_allowed,
    resolve_channel,
    resolve_write_target,
)


WORKFLOW_NAME = "solenoid_field_guide"


class ScalarIO(Protocol):
    def read(self, pv_name: str) -> float: ...

    def write(self, pv_name: str, value: float) -> None: ...


@dataclass(frozen=True)
class VerificationConfig:
    tolerance_a: float
    timeout_s: float
    poll_interval_s: float


@dataclass(frozen=True)
class CurrentControl:
    target: WriteTarget
    readback_pv: str


@dataclass(frozen=True)
class ApplyResult:
    status: str
    element_id: str
    requested_current: float
    readback_current: float | None
    message: str

    @property
    def succeeded(self) -> bool:
        return self.status == "applied"


class EpicsScalarIO:
    def __init__(self, timeout_s: float = 2.0):
        from epics import PV

        self._pv_type = PV
        self._pvs = {}
        self.timeout_s = timeout_s

    def _pv(self, pv_name: str):
        if pv_name not in self._pvs:
            self._pvs[pv_name] = self._pv_type(pv_name, connection_timeout=self.timeout_s)
        pv = self._pvs[pv_name]
        if not pv.wait_for_connection(timeout=self.timeout_s):
            raise TimeoutError(
                f"PV backend is not connected: {pv_name} "
                f"(timeout {self.timeout_s:g} s)"
            )
        return pv

    def read(self, pv_name: str) -> float:
        value = self._pv(pv_name).get(timeout=self.timeout_s)
        if value is None:
            raise ValueError(f"Failed to read PV: {pv_name}")
        value = float(value)
        if not math.isfinite(value):
            raise ValueError(f"PV {pv_name} returned non-finite value: {value!r}")
        return value

    def write(self, pv_name: str, value: float) -> None:
        status = self._pv(pv_name).put(float(value), wait=True, timeout=self.timeout_s)
        if status is False:
            raise ValueError(f"Failed to write PV {pv_name} to {value:g} A.")


def verification_config(context: AppContext) -> VerificationConfig:
    workflow = get_workflow(context.profile, WORKFLOW_NAME)
    raw = workflow.get("readback_verification")
    if not isinstance(raw, dict):
        raise MachineProfileError(
            "workflows.solenoid_field_guide.readback_verification must be an object."
        )
    try:
        config = VerificationConfig(
            tolerance_a=float(raw["tolerance_a"]),
            timeout_s=float(raw["timeout_s"]),
            poll_interval_s=float(raw["poll_interval_s"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MachineProfileError(
            "Invalid solenoid field guide readback verification configuration."
        ) from exc
    if config.tolerance_a <= 0 or config.timeout_s <= 0 or config.poll_interval_s <= 0:
        raise MachineProfileError("Solenoid readback verification values must be positive.")
    return config


def prepare_current_write(
    context: AppContext,
    element_id: str,
    current: float,
) -> CurrentControl:
    require_workflow_write_allowed(
        context,
        WORKFLOW_NAME,
        f"Set {element_id} current",
    )
    current = float(current)
    if not math.isfinite(current):
        raise MachineProfileError("Recommended current must be finite.")
    target = resolve_write_target(context, element_id, quantity="current", unit="A")
    if not target.machine_limit.contains(current):
        raise MachineProfileError(
            f"{element_id} current {current:g} A is outside machine limit "
            f"{target.machine_limit.describe()}."
        )
    readback_pv = resolve_channel(context, element_id, "current_readback")
    return CurrentControl(target=target, readback_pv=readback_pv)


def apply_current(
    context: AppContext,
    element_id: str,
    current: float,
    *,
    io: ScalarIO | None = None,
    config: VerificationConfig | None = None,
) -> ApplyResult:
    try:
        control = prepare_current_write(context, element_id, current)
        verification = config or verification_config(context)
    except (MachineProfileError, TypeError, ValueError) as exc:
        return ApplyResult("rejected", element_id, float(current), None, str(exc))

    if io is None:
        return _apply_epics_subprocess(control, element_id, current, verification)

    scalar_io = io
    return _apply_with_io(control, element_id, current, scalar_io, verification)


def _apply_with_io(
    control: CurrentControl,
    element_id: str,
    current: float,
    scalar_io: ScalarIO,
    verification: VerificationConfig,
) -> ApplyResult:
    try:
        scalar_io.write(control.target.pv_name, current)
    except Exception as exc:
        return ApplyResult(
            "failed", element_id, current, None, f"Setpoint write failed: {exc}"
        )

    deadline = time.monotonic() + verification.timeout_s
    last_readback = None
    while True:
        try:
            last_readback = scalar_io.read(control.readback_pv)
        except Exception as exc:
            return ApplyResult(
                "failed", element_id, current, last_readback, f"Readback failed: {exc}"
            )
        delta = abs(last_readback - current)
        if delta <= verification.tolerance_a:
            return ApplyResult(
                "applied",
                element_id,
                current,
                last_readback,
                f"Applied and verified within {verification.tolerance_a:g} A.",
            )
        if time.monotonic() >= deadline:
            return ApplyResult(
                "mismatch",
                element_id,
                current,
                last_readback,
                f"Readback {last_readback:g} A differs by {delta:g} A after "
                f"{verification.timeout_s:g} s (tolerance {verification.tolerance_a:g} A).",
            )
        time.sleep(min(verification.poll_interval_s, max(0.0, deadline - time.monotonic())))


def _apply_epics_subprocess(
    control: CurrentControl,
    element_id: str,
    current: float,
    verification: VerificationConfig,
) -> ApplyResult:
    script = str(__file__).replace("current_control.py", "epics_apply.py")
    command = [
        sys.executable,
        script,
        control.target.pv_name,
        control.readback_pv,
        f"{current:.17g}",
        f"{verification.tolerance_a:.17g}",
        f"{verification.timeout_s:.17g}",
        f"{verification.poll_interval_s:.17g}",
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=verification.timeout_s + 5.0,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ApplyResult(
            "failed", element_id, current, None,
            "EPICS helper timed out; backend may be disconnected.",
        )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown EPICS error"
        return ApplyResult("failed", element_id, current, None, detail)
    try:
        payload = json.loads(completed.stdout)
        return ApplyResult(
            str(payload["status"]),
            element_id,
            current,
            payload.get("readback_current"),
            str(payload["message"]),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        return ApplyResult("failed", element_id, current, None, f"Invalid EPICS helper result: {exc}")
