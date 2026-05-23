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
