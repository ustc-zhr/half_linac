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
  - Current offline recommendation for ordinary BBA-1 presets:
    - Keep `QT04 + XC21 / BPM21->BPM22` as the first conservative X-plane preset.
    - Add the paired Y-plane preset `QT04 + YC21 / BPM21->BPM22` if vertical BBA-1 is needed.
    - Transfer-line candidates that do not cross accelerating structures or bends:
      - `QT04 + XC21/YC21 / BPM21->BPM22`
      - `QT12 + XC26/YC26 / BPM26->BPM27`
      - `QT13 + XC26/YC26 / BPM26->BPM27`
      - `QT14 + XC26/YC26 / BPM26->BPM27`
      - `QT15 + XC27/YC27 / BPM27->BPM28`
      - `QT16 + XC27/YC27 / BPM27->BPM28`
      - `QT18 + XC28/YC28 / BPM28->BPM29`
      - `QT19 + XC29/YC29 / BPM29->BPM30`
    - Treat candidates crossing `DACC`, `BC*`, `BH*`, ESA branches, or spectrometer/transport bends as special studies rather than ordinary BBA-1 presets.
  - Before adding these to `configs/machines/half/apps/bba.json`, confirm scan bounds and whether each preset should write model `K1` or real magnet current.

### 1a. Dispersion-Aware BBA Workflow

- Status: open
- Priority: medium
- Background:
  - Ordinary BBA-1 assumes BPM readings are dominated by betatron orbit.
  - In dispersive sections, BPM readings include `D * delta`, so random and correlated energy jitter can bias the fitted offset.
- Problem:
  - Multi-shot averaging can reduce random jitter, but it cannot remove systematic dispersion bias, slow energy drift, or scan-order-correlated energy changes.
  - Presets that use dispersive BPMs, such as dogleg/achromat BPMs, should not be mixed into the ordinary BBA-1 preset list.
- Follow-up:
  - Add a separate experimental workflow, for example `bba1_dispersion_corrected`, rather than extending ordinary BBA-1 presets.
  - Record an energy proxy or direct `delta` measurement with every scan point.
  - Support energy gating and/or `x_corrected = x_measured - D * delta` before fitting.
  - Prefer interleaved scan ordering to reduce slow drift.
  - Store both uncorrected and dispersion-corrected fit results in metadata for commissioning review.

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
  - IRFEL `energy_spectrum` has VM coverage and a partial real-machine integration.
  - The VM workflow uses `PRFESA` as the energy-spectrum image target and uses the IRFEL elegant model backend.
  - Verified in VM on 2026-06-01:
    - `PRFESA` VM image publication/display works.
    - Dispersion calculation works with the IRFEL elegant backend.
  - The coordinated real energy control is represented as `ESA_ENERGY.setpoint`, mapped
    to `IRFEL:AP:ENG:A3:ao`; it is used as the target-energy setpoint and reference energy.
  - Real-mode Auto Find scans the coordinated energy element over `0–65 MeV`, rather than
    writing BM03 directly, so BM03/QM19/QM20 remain under the existing linked control.
  - Operator feedback added precise `0.01 MeV` Target input, live A3 Target synchronization,
    editable Auto Find parameters, and a cooperative Stop action that restores the pre-scan A3 value.
  - IRFEL Auto Find defaults to minimizing the beam-center distance from calibrated
    `x_reference_mm`; the previous maximum-brightness objective remains selectable for comparison.
- Problem:
  - The independent energy readback corresponding to the A3 setpoint still needs confirmation.
  - The scan workflow cannot be meaningfully validated in VM and must be treated as a real-machine commissioning item.
  - The real backend still needs confirmed PVs, physics calibration, scan bounds, and rollback expectations before enabling operational use.
- Follow-up:
  - Confirm the real image PV for `PRFESA`, including array shape and pixel width.
  - Confirm whether `exposure_time`, `sigx`, and `sigy` are available from the real camera/diagnostics system.
  - Confirm the real spectrometer bend element and PV mapping for current readback/setpoint.
  - Confirm that `IRFEL:AP:ENG:A3:ao` is expressed in MeV and identify a separate readback PV if available.
  - Commission the configured `0–65 MeV` Auto Find range and confirm settling behavior.
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
  - Current VM acceptance is recorded in `docs/IRFEL_VM_ACCEPTANCE.md`.
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
  - Low priority: add a local fit quality gate for saturated, clipped, high-residual, or out-of-range PRF image fits before enabling real scans.
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

### 6. Emit Measure Latest Scan File Cleanup

- Status: partially addressed
- Priority: low
- Background:
  - `src/apps/emit_measure/runtime/<machine>/<backend>/latest/scanResults.txt` is the latest-scan working copy.
  - `src/apps/emit_measure/runtime/<machine>/<backend>/runs/<run_id>/scanResults.txt` is the timestamped archive store.
  - The current app writes both after a scan: the latest file supports immediate recalculation, while archive directories preserve history.
- Problem:
  - The two-file model is useful now but creates some conceptual overlap.
  - Removing the latest `scanResults.txt` too early would break current `Recalculate`, status display, and latest metadata paths.
- Follow-up:
  - Keep `scanResults.txt` for the current IRFEL VM bring-up phase.
  - Later, make `Recalculate` use the GUI scan-point table as its primary source.
  - Keep timestamped archives as the long-term storage and load/review path.
  - After table-driven recalculation is complete, downgrade `scanResults.txt` to a compatibility artifact or remove it.

### 7. Orbit Correct Global Damping Controls

- Status: open
- Priority: low
- Background:
  - `orbit_correct` global correction uses the active response matrix and SVD pseudo-inverse.
  - The global path now supports selected BPM rows with all correctors participating.
  - Current exposed global controls include `Global Max Iter`, `Corrector Limit`,
    `SVD Cutoff (%)`, and the active response matrix selection.
  - The SVD cutoff is a backend-configurable relative threshold; the GUI passes the
    operator-selected value to each global correction run.
- Problem:
  - If global correction overshoots, oscillates, or becomes sensitive to poorly conditioned response matrices, operators do not yet have dedicated global damping controls.
  - The existing `1-to-1 Gain` and `1-to-1 Max Step (%)` controls are intentionally scoped to one-to-one correction and should not be reused implicitly for global correction.
- Follow-up:
  - Add independent `Global Gain` for scaling the pseudo-inverse correction vector.
  - Add independent `Global Max Step (%)` to limit each corrector's per-iteration delta.
  - Use VM commissioning data to refine the per-backend SVD cutoff defaults.
  - Validate with IRFEL VM global correction before considering any real-mode use.

### 7a. Cross-App Actuator Range And Step Semantics

- Status: design decision pending
- Priority: medium
- Background:
  - Several write-capable apps expose actuator range, cumulative-change, scan-step,
    or per-iteration step controls with related but not yet uniform semantics.
  - Relevant workflows include at least `orbit_correct`, `dispersion_correction`,
    BBA, `emit_measure`, `energy_spectrum`, and `solenoid_centering`.
  - `dispersion_correction` currently distinguishes a cumulative knob limit relative
    to the workflow snapshot from a per-iteration `max_step_fraction`.
  - `orbit_correct` currently exposes an editable absolute corrector setpoint limit,
    while the backend-specific machine cap is also stored in app machine config.
- Design questions:
  - Define which bounds are machine-owned physical limits and which are
    operator-adjustable session limits.
  - Decide whether session limits use physical units, a fraction of the machine
    limit, or both depending on actuator type.
  - Define the baseline for cumulative limits: workflow-start setpoint, reviewed
    snapshot, design value, or another explicit reference.
  - Define whether per-step limits are fractions of the cumulative session range
    or independent values in physical units.
  - Specify behavior when a writable backend has no explicit machine limit,
    including which actions remain read-only and which writes must be blocked.
  - Standardize preflight display, saturation reporting, abort/restore behavior,
    runtime metadata, and naming across the affected apps.
- Follow-up:
  - Audit current range and step semantics in each affected app before changing
    configuration schemas or GUI labels.
  - Propose one shared conceptual model without forcing all actuator types into an
    identical storage format where their physics differs.
  - Keep existing behavior unchanged until the cross-app design is reviewed and
    an explicit migration and compatibility plan is approved.

### 8. Model Snapshot And Real-to-VM Mirroring

- Status: partially implemented
- Priority: high
- Background:
  - Real machine and virtual accelerator are control objects: they accept control writes and produce diagnostic readbacks.
  - The elegant model backend is a calculation object: it should calculate optics from an explicit machine-state snapshot.
  - Current model calculations start from configured design lattice files and apply explicit snapshot overrides for supported model fields.
- Problem:
  - For real-machine use, model-dependent apps such as `energy_spectrum`, `emit_measure`, and BBA auxiliary model checks need model parameters aligned with current machine state.
  - Treating VM state as the implicit source of truth would couple the model backend to a specific control object and could disturb an active VM session.
  - Some real PVs expose current while elegant expects `K1`, kick, bend angle, or another model-native quantity.
- Current implementation:
  - `energy_spectrum`, `emit_measure`, and BBA-2 model R12 calculations can consume model snapshot lattice overrides.
  - HALF and IRFEL model backend configs use `snapshot_mapping` to map logical control channels to model-native fields.
  - `emit_measure` scopes snapshot reads to the active model path for scan/recalculate and Twiss calculations.
  - `energy_spectrum` keeps its current optics snapshot cache at `latest/model_snapshot.json`; result metadata embeds the snapshot used for replay.
  - `energy_spectrum` timer refreshes, window initialization, theme changes, and colormap changes do not write result metadata; explicit model/result actions write `latest/metadata.json` and `runs/energy_result_<timestamp>/metadata.json` as appropriate.
  - Real-to-VM mirroring remains intentionally separate from model calculations.
- Follow-up:
  - TODO: Saved snapshot selection.
    - Add explicit UI or CLI selection for saved snapshot JSON files.
    - Validate machine/backend compatibility before applying a saved snapshot to model calculations.
    - Make replay mode visible in result metadata so archived recalculations are distinguishable from live snapshots.
  - TODO: Real-to-VM mirroring/debug.
    - Add an explicit debug/commissioning command that copies selected real-machine readbacks into the VM control object only when requested.
    - Keep mirroring separate from normal model calculations; model backends should continue to consume explicit snapshots rather than treating VM state as an implicit source of truth.
    - Record mirrored source, target PVs, timestamp, and conversion assumptions for review.
  - Require explicit unit/conversion definitions before using current-based magnet PVs as elegant `K1`, kick, or bend-angle values.
  - Extend snapshot mapping coverage only when a model calculation actually needs the extra field, or generate broad coverage from machine profile metadata.

### 9. Model Backend Runtime Workspace Separation

- Status: completed
- Priority: medium
- Background:
  - VM runtime and model backend calculations both use elegant, but they play different roles.
  - The VM is a control object with runtime state such as `halflinac.json`, `elegant/lattice.lte`, and `elegant/one.ele`.
  - The model backend is a calculation object that writes temporary model files such as `emit.json`, `emit.lte`, `emit.ele`, `esa.json`, `esa.lte`, and `esa.ele`.
  - The generated model working files can be rebuilt from `lattice_ini.lte`, `emit_ini.ele`, `esa_ini.ele`, configured line names, and explicit snapshot overrides.
- Problem:
  - Keeping model backend working files under `src/virtual_machine/<machine>_elegant/` visually coupled model calculations to the VM control object.
  - Runtime diffs could look like source changes when generated files were still tracked by git.
- Current implementation:
  - HALF and IRFEL model backend working files now use `runtime/model_backend/<machine>/simulation/{emit,energy}/`.
  - VM source assets such as `lattice_ini.lte`, `emit_ini.ele`, and `esa_ini.ele` remain in the VM elegant asset directory.
  - Generated model working files are ignored and untracked.
  - Template machine configs now describe model backend runtime output paths instead of app-local ESA output files.
- Follow-up:
  - Consider moving the remaining app-local ESA elegant orchestration into the shared model backend once the runtime boundary is stable.
