#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

export HALF_LINAC_MACHINE_ID=irfel
export HALF_LINAC_CONTROL_BACKEND=vm
export HALF_MACHINE_ID="$HALF_LINAC_MACHINE_ID"
export HALF_CONTROL_BACKEND="$HALF_LINAC_CONTROL_BACKEND"

python3 - <<'PY'
from __future__ import annotations

from half_linac.src.shared.elegant_backend.publisher import build_vm_publish_plan
from half_linac.src.shared.machine_profile import (
    REAL_STATUS_COMMISSIONED,
    REAL_STATUS_NOT_SUPPORTED,
    REAL_STATUS_READ_ONLY,
    MachineProfileError,
    get_workflow,
    load_app_context,
    load_profile,
    real_commissioning_status,
    require_workflow_write_allowed,
    validate_machine_profile,
    workflow_writes_allowed,
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"IRFEL VM acceptance failed: {message}")


profile = load_profile()
require(profile.machine.id == "irfel", f"expected irfel profile, got {profile.machine.id!r}")
require("vm" in profile.control_backends, "IRFEL profile does not declare vm backend")

report = validate_machine_profile()
require(report.ok, report.format_text())

contexts = {
    app_name: load_app_context(app_name)
    for app_name in (
        "orbit_correct",
        "orbit_display",
        "beam_monitor",
        "energy_spectrum",
        "emit_measure",
        "bba",
    )
}
for app_name, context in contexts.items():
    require(context.machine.id == "irfel", f"{app_name} loaded machine {context.machine.id!r}")
    require(context.control_backend.name == "vm", f"{app_name} loaded backend {context.control_backend.name!r}")

plan = build_vm_publish_plan(profile)
require(len(plan.bpm_specs) == 10, f"expected 10 BPM publish specs, got {len(plan.bpm_specs)}")
require(
    len(plan.watch_image_specs) == 6,
    f"expected 6 watch-image publish specs, got {len(plan.watch_image_specs)}",
)
watch_targets = {(spec.target_element_id, spec.logical_channel) for spec in plan.watch_image_specs}
require(("PRF03", "image") in watch_targets, "beam monitor PRF03 image is missing from VM publish plan")
require(("PRFESA", "image") in watch_targets, "energy spectrum PRFESA image is missing from VM publish plan")

orbit_vm = contexts["orbit_correct"]
beam_vm = contexts["beam_monitor"]
energy_vm = contexts["energy_spectrum"]
emit_vm = contexts["emit_measure"]
require(workflow_writes_allowed(orbit_vm, "orbit"), "IRFEL VM orbit writes are unexpectedly blocked")
require(workflow_writes_allowed(beam_vm, "beam_monitor"), "IRFEL VM beam monitor writes are unexpectedly blocked")
require(workflow_writes_allowed(energy_vm, "energy_spectrum"), "IRFEL VM energy spectrum writes are unexpectedly blocked")
require(workflow_writes_allowed(emit_vm, "emit_measure"), "IRFEL VM emit measure writes are unexpectedly blocked")

real_contexts = {
    "orbit": load_app_context("orbit_correct", control_backend="real"),
    "beam_monitor": load_app_context("beam_monitor", control_backend="real"),
    "energy_spectrum": load_app_context("energy_spectrum", control_backend="real"),
    "emit_measure": load_app_context("emit_measure", control_backend="real"),
}
for workflow_name, context in real_contexts.items():
    require(
        workflow_writes_allowed(context, workflow_name),
        f"IRFEL real {workflow_name} writes are unexpectedly blocked",
    )
    try:
        require_workflow_write_allowed(context, workflow_name, "acceptance write probe")
    except MachineProfileError as exc:
        raise SystemExit(f"IRFEL VM acceptance failed: real {workflow_name} write probe failed: {exc}") from exc

bba_workflow = get_workflow(profile, "bba")
require(
    bba_workflow["standard"]["control_backends"] == ["vm"],
    "IRFEL standard BBA must stay VM-only",
)
require(
    bba_workflow["bba2"]["control_backends"] == ["vm"],
    "IRFEL BBA2 must stay VM-only",
)

expected_statuses = {
    "orbit_display": REAL_STATUS_READ_ONLY,
    "orbit_correct": REAL_STATUS_COMMISSIONED,
    "beam_monitor": REAL_STATUS_COMMISSIONED,
    "energy_spectrum": REAL_STATUS_COMMISSIONED,
    "emit_measure": REAL_STATUS_COMMISSIONED,
    "bba": REAL_STATUS_NOT_SUPPORTED,
}
for app_name, expected in expected_statuses.items():
    status = real_commissioning_status(profile, app_name)
    require(status == expected, f"{app_name} real_status expected {expected!r}, got {status!r}")

print("IRFEL VM acceptance passed.")
print(f"  apps: {', '.join(contexts)}")
print(f"  publish plan: {len(plan.bpm_specs)} BPM specs, {len(plan.watch_image_specs)} watch-image specs")
print("  real write policy: allowed for orbit, beam_monitor, energy_spectrum, emit_measure")
PY
