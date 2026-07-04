# Model Snapshot Design

## Purpose

Model-dependent applications need optics calculations that match a known machine
state. The model backend should not silently assume that its design lattice, the
virtual accelerator, or the real machine is the current source of truth.

This document defines the first implementation target for model snapshots.

## Roles

- `control_backend`
  - A control object that accepts writes and produces readbacks.
  - Current choices are `real` and `vm`.
  - Applications use EPICS PVs through the selected control backend.
- `model_snapshot`
  - A recorded machine-state view translated into model-native lattice fields.
  - Examples: `QE01.K1`, `BM03.ANGLE`, `XC01.KICK`.
  - Snapshot values are ready to write into the model lattice.
- `model_backend`
  - A calculation object.
  - It receives a design lattice plus explicit snapshot overrides, then computes
    matrices, Twiss values, dispersion, or related optics quantities.

The model backend must not need to know whether a snapshot came from the real
machine, the virtual accelerator, a saved file, or the design lattice.

## Snapshot Sources

Initial source names:

- `design`
  - Use the configured design lattice with no live PV read.
- `live_from_real`
  - Read selected real-machine PVs and convert them to model-native fields.
- `live_from_vm`
  - Read selected virtual-machine PVs and convert them to model-native fields.
- `saved`
  - Load a previously recorded snapshot.

The first implementation supports `live_from_real`, `live_from_vm`, `design`, and
loading a saved snapshot JSON file.

## Conversion Rules

Snapshot values must be model-native. If a PV exposes current, the snapshot
layer must convert current to the model field expected by elegant before the
model backend sees it.

The model backend configuration stores these rules under `snapshot_mapping`.
This is a mapping definition, not a saved runtime snapshot. It declares how a
requested model field such as `QE01.K1` is read from the selected control
backend and converted to the lattice value used by elegant.

Only fields requested by model-dependent workflows need to be listed. The
current implementation intentionally maps a focused subset of elements used by
`energy_spectrum`, `emit_measure`, and BBA model calculations. A complete
machine-wide mapping is a later extension and should be generated or derived
from machine profile metadata where possible, rather than duplicated by hand.

Initial conversion types:

- `direct`
  - `model_value = pv_value`
- `scale_offset`
  - `model_value = scale * pv_value + offset`
- `polynomial`
  - `model_value = c0 + c1*x + c2*x^2 + ...`

If a live source lacks an explicit conversion for a current-based or otherwise
ambiguous PV, the app must not label the calculation as live-model accurate.
It should either fail clearly or fall back to the design lattice with metadata
that records the fallback.

## Elegant Backend Contract

The elegant backend accepts field-level lattice overrides:

```python
{
    "QE01": {"K1": 1.23},
    "BM03": {"ANGLE": 0.05},
}
```

The backend writes these fields into its temporary lattice state before running
elegant. It does not read PVs and does not mutate VM runtime state.

## Real-To-VM Mirroring

Real-to-VM mirroring is a separate debug and commissioning feature. It may copy
real-machine state into the VM control object when explicitly requested, but it
is not required for model calculations.

This separation avoids hidden side effects:

- model calculations do not disturb an active VM session
- VM state is not treated as an implicit source of truth
- results can record exactly which snapshot source and conversion rules were used

## First Vertical Slice

The first implementation:

1. Added field-level overrides to `ElegantModelBackend`.
2. Added a shared `model_snapshot` module that reads configured fields and produces
   both lattice overrides and metadata.
3. Uses snapshots in `energy_spectrum`, `emit_measure`, and BBA-2 model R12
   calculations.
4. Keeps real-to-VM mirroring as a later debug-oriented feature.

The second slice adds a stable saved snapshot JSON schema plus latest snapshot
recording for `energy_spectrum` model calculations.

## Later TODO

- Add UI or CLI selection for saved snapshot files.
- Extend snapshot use to `emit_measure` and BBA.
- Add explicit real-to-VM mirroring/debug commands that copy selected real
  readbacks into the VM control object without becoming a hidden model
  dependency.
