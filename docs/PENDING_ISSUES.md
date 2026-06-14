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

### 4. VM Flag Scalar Publisher

- Status: open
- Priority: medium
- Background:
  - `beam_monitor` can fit the selected flag image and write `sigx/sigy` back to profile-defined PVs.
  - IRFEL temporarily uses this app-side publication path for `PRF03`, so `emit_measure` can read `IRFEL:VM:FLAG:PRF03:sigx/sigy` after beam monitor is running and refreshing `PRF03`.
- Problem:
  - This depends on the beam monitor GUI being open, connected, and refreshing the same flag that `emit_measure` reads.
  - VM runtime itself still only publishes BPM coordinates and flag images.
- Follow-up:
  - Add `sigx/sigy` scalar specs to `VmPublishPlan`.
  - Compute flag scalar outputs from the same WATCH data or image-derived profile used by VM image publishing.
  - Publish scalars from `VmPublisher` so measurement apps do not depend on `beam_monitor` being open.
  - Keep units consistent with `emit_measure` expectations, currently millimeters.

### 5. IRFEL Real Commissioning Checklist

- Status: open
- Priority: high
- Background:
  - IRFEL VM workflows have been brought up incrementally for orbit, beam monitor, energy spectrum, and emit measurement.
  - IRFEL `real` backend entries exist so the profile can be validated offline, but most real-machine behavior has not been verified on site.
  - The current rule is: VM verification is not evidence that the same app is safe or physically correct in `real` mode.
- Offline acceptance entrypoint:
  - Run `bash scripts/check_irfel_vm.sh` before changing IRFEL profile wiring or app workflow config.
  - Run `bash scripts/smoke_irfel_vm_runtime.sh` for a short VM/IOC startup smoke after VM-related changes.
- Problem:
  - Operators can select `machine=irfel` and `backend=real`, but some real PVs, units, safety limits, and calibration constants are still placeholders or unconfirmed.
  - Without an explicit checklist, it is easy to treat config completeness as commissioning completeness.
- General real-mode facts to confirm:
  - Confirm the authoritative IRFEL PV list and whether each PV is read-only, setpoint, or readback.
  - Confirm write permission expectations for correctors, quadrupoles, bends, camera exposure, and any restore operations.
  - Confirm physical units for every writable magnet PV: current, angle, integrated strength, or normalized `K1`.
  - Confirm safe limits, step sizes, dwell times, and restore behavior before any app writes to real PVs.
  - Confirm whether each app should be allowed to write in first real tests or should start in read-only observation mode.
- `control_backends/real.json` checklist:
  - Verify BPM `x/y/s` PV names and units for every BPM used by orbit display or orbit correction.
  - Verify corrector setpoint/readback PV names, sign convention, response unit, and safe range.
  - Verify quadrupole PV names and whether the app is writing physical current or an elegant-style focusing strength.
  - Verify bend PV names for energy-spectrum use, including current setpoint/readback and rollback path.
  - Verify flag/camera image PVs, array shape, pixel width, and whether `sigx/sigy/exposure_time` are provided by real diagnostics.
- `orbit_display` and `orbit_correct` checklist:
  - Run read-only BPM display smoke first.
  - Confirm BPM ordering matches the physical beamline and profile element order.
  - Measure or load a response matrix whose BPM/corrector dimensions match the selected profile subset.
  - Confirm corrector sign convention before enabling global correction.
  - Confirm `response_wait_s_by_backend.real` and `corrector_upperlimit_rad` are suitable for IRFEL hardware.
- `beam_monitor` checklist:
  - Confirm each real flag image PV resolves and has the configured `pixel_shape`.
  - Confirm pixel calibration and `pixel_width_mm`.
  - Confirm behavior when a selected flag is not in the active usedline or has no beam image.
  - Keep fitted `sigx/sigy` publication blocked in IRFEL real mode until a deliberate write test is approved.
- `emit_measure` checklist:
  - Confirm the real scan PV for each quadrupole preset and whether scan values are current or model `K1`.
  - Confirm the selected flag provides reliable `sigx/sigy` during a scan.
  - Confirm safe scan bounds, sample count, dwell time, and restore-to-initial behavior for each preset.
  - Confirm the model line used for each preset matches the real beam path during measurement.
  - Confirm archived scan metadata is reviewed before using `Recalculate` for real data.
- `energy_spectrum` checklist:
  - Complete the dedicated `IRFEL Energy Spectrum Real Bring-up` item above before operational real-mode scans.
  - Confirm spectrometer bend calibration, dispersion model, target flag image, and safe scan range.
  - Confirm whether auto-find may write the bend setpoint or must remain advisory in first real tests.
- `bba` checklist:
  - Decide whether IRFEL BBA is in scope for the first real commissioning round.
  - If in scope, define machine-native BBA presets rather than reusing HALF or legacy IRFEL names blindly.
  - Confirm quad/corrector/BPM combinations, scan bounds, and rollback behavior before enabling writes.
- Acceptance criteria:
  - Each app has one of these explicit `real_status` values for IRFEL real: `not_supported`, `read_only`, `write_blocked`, `write_smoke_passed`, or `commissioned`.
  - Real-mode app configs and `control_backends/real.json` are updated only after the corresponding site facts are confirmed.
  - Any real-mode write test is run deliberately, with a known rollback path and operator approval.
