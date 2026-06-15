# IRFEL VM Acceptance Record

Last updated: 2026-06-15

This document records the current IRFEL virtual-machine acceptance state. It is a VM-stage record only. It must not be treated as evidence that IRFEL `real` mode is commissioned or safe for machine writes.

## Scope

- Machine profile: `irfel`
- Backend: `vm`
- Runtime path: `configs/machines/irfel` plus `src/virtual_machine/irfel_elegant`
- IOC path: `src/softIOC/irfel`
- Current policy: keep IRFEL `real` writes blocked unless a dedicated commissioning step explicitly changes that policy.

## Verified Checks

The following checks passed on 2026-06-15:

- `bash scripts/check.sh`
- `bash scripts/check_irfel_vm.sh`
- `bash scripts/smoke_irfel_vm_runtime.sh`

The runtime smoke verified:

- IRFEL IOC manager reported reachable softIOC PVs.
- IRFEL VM manager started and stayed up until the smoke timeout.
- VM BPM data publication completed.
- VM flag image data publication completed.
- IOC manager stayed up while the VM manager was running.

## App Status

| App | VM status | Evidence |
| --- | --- | --- |
| `beam_monitor` | usable | Operator tested in IRFEL VM; PRF image display and local image fitting work. |
| `emit_measure` | usable | Operator tested in IRFEL VM; local PRF image fitting, scan, archive metadata, and fit diagnostics work. |
| `energy_spectrum` | VM skeleton usable | Profile/model acceptance passes; `PRFESA` VM image publication and dispersion calculation were previously verified in VM. Real scan behavior is not commissioned. |
| `orbit_display` | profile accepted | VM profile and channel validation pass. |
| `orbit_correct` | profile accepted | VM profile and channel validation pass. Real correction remains blocked. |
| `bba` | VM-only profile accepted | IRFEL BBA workflows remain VM-only; real status is `not_supported`. |

## Current VM Behavior Notes

- `Reload Initial Lattice` rewrites the runtime JSON and, when the IOC is running, syncs VM writable PVs for quadrupoles, correctors, and bends.
- `emit_measure` default IRFEL VM preset `emit_qm12_prf04` uses `QM12 -> PRF04`, model line `ALL_DUMP`, and scan range `25.0..35.0`.
- `emit_measure` uses local PRF image fitting for beam sizes in IRFEL VM.
- `emit_measure` stores timestamped scan archives under `src/apps/emit_measure/runtime/scans/irfel/vm`.
- `src/apps/emit_measure/scanResults.txt` remains the latest-scan working copy for current recalculation workflow.
- VM publishing currently provides BPM coordinates and flag images. Publishing `sigx/sigy` directly from VM runtime remains a separate TODO.

## Real-Mode Boundary

IRFEL `real` mode is intentionally not commissioned by this VM acceptance record.

Before enabling any real write path, the following must be confirmed on site:

- Authoritative PV names and units for quadrupoles, correctors, bends, BPMs, and flags.
- Whether magnet write PVs use current, angle, integrated strength, or model-style `K1`.
- Safe scan bounds, step sizes, dwell times, and restore behavior.
- Flag image shape and pixel calibration for each real camera.
- Whether real diagnostics publish trusted `sigx/sigy`, or whether apps must use local image fitting.
- Explicit operator approval for each real write smoke test.

Use `docs/PENDING_ISSUES.md` item `IRFEL Real Commissioning Checklist` for the real bring-up checklist.
