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
