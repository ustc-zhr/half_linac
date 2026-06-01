# Pending Issues

## Open

### 1. HALF Standard BBA Physical Mapping Audit

- Status: open
- Priority: medium
- Background:
  - `standard BBA` in this repo once carried over IRFEL-style quadrupole names such as `CQ1/CQ2/CQ3/CQ4/MQ1/MQ2/MQ11/MQ12`.
  - These names do not belong to HALF and have now been removed from machine config, backend config, and softIOC alias generation.
  - The config now uses real HALF quadrupole names such as `QL01/QL02/QL03/QL04/QL11/QL12`.
- Problem:
  - Although the wrong element names have been removed, the physical suitability of the current `standard BBA` scan set still needs a dedicated review.
  - We still need to confirm whether the current `quad + corrector + bpm1 + bpm2` combinations are the intended and operationally valid choices for HALF.
- Follow-up:
  - Review `configs/machines/half/apps/bba.json` against actual HALF optics and commissioning intent.
  - Confirm which standard BBA presets should remain, be renamed, or be deleted.
  - If needed, distinguish clearly between:
    - machine-native presets
    - migrated legacy presets kept only for compatibility

### 2. Energy Spectrum Auto Find Scan Trace

- Status: open
- Priority: low
- Background:
  - `energy_spectrum` Auto Find now runs in a background thread and updates the flag image step by step while scanning.
  - During commissioning it can still be useful to review how the scan moved through current points before selecting the final bend setting.
- Problem:
  - The GUI currently shows live progress, but it does not yet keep a structured trace of the scan.
  - Operators cannot yet review a compact history such as:
    - scanned current points
    - whether beam was detected at each point
    - score used by the tuner
- Follow-up:
  - Add an optional lightweight trace view or temporary table for Auto Find.
  - Prefer a simple operator-facing design first:
    - current
    - coarse/fine stage
    - beam yes/no
    - score
  - Keep this separate from the core tuner logic so the scan algorithm stays simple.

### 3. IRFEL Energy Spectrum Real Bring-up

- Status: open
- Priority: medium
- Background:
  - IRFEL `energy_spectrum` is currently brought up as a VM-only skeleton.
  - The VM workflow uses `PRFESA` as the energy-spectrum image target and uses the IRFEL elegant model backend.
  - Verified in VM on 2026-06-01:
    - `PRFESA` VM image publication/display works.
    - Dispersion calculation works with the IRFEL elegant backend.
  - Real-machine integration is intentionally deferred until site PVs and calibration constants are confirmed.
- Problem:
  - `configs/machines/irfel/apps/energy_spectrum.json` currently keeps VM-safe/default values and must not be treated as a validated real-machine setup.
  - The scan workflow cannot be meaningfully validated in VM and must be treated as a real-machine commissioning item.
  - The real backend still needs confirmed PVs, physics calibration, scan bounds, and rollback expectations before enabling operational use.
- Follow-up:
  - Confirm the real image PV for `PRFESA`, including array shape and pixel width.
  - Confirm whether `exposure_time`, `sigx`, and `sigy` are available from the real camera/diagnostics system.
  - Confirm the real spectrometer bend element and PV mapping for current readback/setpoint.
  - Confirm the current-to-energy conversion constants: magnet length, deflection angle, and field-per-current calibration.
  - Confirm safe scan range, step size, dwell time, stop condition, and post-scan restore behavior for the real bend.
  - Confirm whether the app should write a target energy/current setpoint PV in real mode, or only calculate and display it.
  - After those facts are confirmed, update `configs/machines/irfel/control_backends/real.json` and `configs/machines/irfel/apps/energy_spectrum.json`, then run a real-mode smoke test only with explicit approval.
