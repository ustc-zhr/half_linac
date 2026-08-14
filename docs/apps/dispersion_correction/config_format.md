# Configuration Format

The package accepts both JSON and YAML configuration files. JSON is the
preferred format for integration with `half_linac`; YAML files are retained for
local readability and backward compatibility.

Recommended IRFEL files:

- `tests/dispersion_correction/fixtures/irfel_achromat.json`: legacy read-only EPICS regression fixture.
- `tests/dispersion_correction/fixtures/irfel_achromat.mock.json`: deterministic offline IRFEL regression fixture.
- `tests/dispersion_correction/fixtures/achromat_mvp.example.json`: generic offline regression fixture.

Runtime configuration inside `half_linac` comes from
`configs/machines/<machine>/apps/dispersion_correction.json` plus the selected
control backend. The fixture PV maps above are not used by the Control Room.

The schema is the same for JSON and YAML:

- `backend`: backend type, mode, and optional PV/model settings.
- `energy_knob`: momentum perturbation request and optional RF phase
  calibration.
- `target_bpms`: correction BPMs used for residual RMS, response solving, and
  acceptance.
- `monitor_bpms`: optional diagnostic BPMs measured in the same energy scans
  and shown in plots, but excluded from the correction objective.
- `quadrupole_control`: backend-to-control mapping. Use the canonical labels
  `K1` and `current`; parsing is case-insensitive, while the GUI displays
  `K1 [1/m²]` or `A`.
- `knobs`: high-level symmetric correction knobs. The nested `scan` object
  contains the response `step`, cumulative relative `max_offset`, explicit
  `mode`, and control `unit`.
- `measurement`: horizontal-plane samples per step, final samples, and settle
  time after each machine setting change.
- `solver`: SVD, response-matrix update policy, and trial-step settings.
- `safety`: BPM orbit-change protection.

Machine-profile workflows may define multiple `sections`. Each section supplies
the small set of facts that cannot be inferred from element kinds alone:

- `id` and `display_name`;
- `model_entrance` and `model_exit`;
- recommended correction `target_bpms`, diagnostic `monitor_bpms`, and
  correction `knobs`;
- `target_dispersion_mm`, which defaults to zero for legacy configurations;
- `model_observables`, which defines model constraints by element and component
  (`dx`, `dxp`, `dy`, or `dyp`); position components use mm and angular
  components use mrad;
- `model_only`, which blocks machine measurement and correction while still
  permitting isolated Elegant response calculation.

For example, a horizontal achromat exit can be expressed as:

```json
"model_observables": [
  {"name": "BPM06 Dx", "element": "BPM06", "component": "dx", "target": 0.0},
  {"name": "BPM06 Dx'", "element": "BPM06", "component": "dxp", "target": 0.0}
]
```

`model_entrance` and `model_exit` bound the Elegant profile. Observable
elements must lie inside that interval. If `model_observables` is omitted, the
model keeps the legacy behavior of using horizontal dispersion at each
`target_bpms` entry.

The correction objective is the RMS residual `D_eff - target_dispersion` over
`target_bpms`, not unconditionally `D_eff -> 0` at every measured location.
`monitor_bpms` may therefore retain the nonzero dispersion expected inside a
bend without driving the solver. Existing configurations that omit
`monitor_bpms` retain their original behavior.

In Control Room mode, `target_bpms` and `knobs` are recommended defaults rather
than complete selectable lists. The GUI discovers BPM candidates from profile
elements with `kind: bpm` and a resolvable x channel, and quadrupole candidates
from `kind: quad` elements with same-unit setpoint/readback channels. The two
devices in each symmetric knob use fixed `+1` weights. Session scan and limit
values may be reduced in the GUI, but cannot exceed the profile defaults.
The control variable itself is fixed by the active backend, for example:

```json
"quadrupole_control": {
  "vm": "K1",
  "real": "current"
}
```

The preferred scan shape is:

```json
"scan": {
  "step": 1,
  "max_offset": 5,
  "mode": "relative",
  "unit": "1/m^2"
}
```

`step` is the symmetric response-measurement perturbation. `max_offset` is the
cumulative knob displacement allowed relative to the workflow snapshot. The
parser still accepts legacy `scan_step` and `limit` fields. IRFEL VM is
model-only, so its values are not used for a machine response scan.

For IRFEL electron beams in this MVP, the configured `energy_knob.delta` is
treated as `dp/p`; for the intended tens-of-MeV-plus operation range this is
also used as the practical `dE/E` value.

`solver.response_update` supports two policies:

- `once`: measure the response matrix in the first iteration and reuse it with
  the latest measured dispersion vector in later iterations.
- `every_iteration`: remeasure the response matrix before every correction
  solve.

The correction step uses normalized bounded least squares. For each knob,
`step_limit = knob.max_offset * solver.max_step_fraction`. The runtime model
retains the legacy internal attribute name `limit`; the solver normalizes the
knob variables by these step limits, applies the remaining cumulative bounds,
and targets `-solver.gain * D_eff`. `solver.regularization` penalizes normalized
knob usage, which selects a balanced solution when there are more knobs than
BPM constraints.
