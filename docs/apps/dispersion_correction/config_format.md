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

The GUI uses one **Correction** entry point. Its settings dialog edits the
maximum iterations (`solver.max_iter`), gain, maximum step fraction, response
update policy, and minimum improvement. One iteration performs a single
measure → solve → apply → verify cycle; larger values repeat the same workflow
without reviewing each recommendation. Rejected trials retain the existing
automatic rollback behavior. Abort, History, and Restore Initial remain
available under their existing backend and safety conditions. Restore Initial
restores the saved quadrupole values before the latest successful correction,
not a full-machine configuration.
In History, selecting **Initial measurement** exposes **Restore Initial…** for
that run. Restoration requires saved device values, a known current state, and
an EPICS backend with writing enabled; it checks the existing limits and
verifies dispersion afterward. Initial tables prefer saved absolute quadrupole
values. Runs without device snapshots explicitly show relative knob offsets
and cannot restore physical magnets from those offsets.

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

- `once`: keep the selected response throughout the run. Without a saved
  response, measure it in the first iteration. Always solve against fresh
  dispersion measurements.
- `every_iteration`: remeasure before each solve; when a saved response is
  explicitly selected, use it for the first iteration and remeasure from the
  second iteration onward. Both policies apply to single-plane and joint
  correction.

**Measure Q Response…** scans the current Q knobs and automatically saves a
versioned JSON record under the app's `runtime/<machine>/<backend>/responses/`
directory (`standalone/offline` for the demo). Responses measured during a
correction run are also saved. Each record contains the matrix, timestamp,
full run configuration, scan baseline measurement, quadrupole snapshot,
energy setting, and singular values. Correction reports reference the response
timestamp. Temporary scan settings are restored before saving.

In **Correction → Q response source**, choose **Measure a new response** or a
saved measurement. A saved response never supplies the current dispersion:
the workflow reads the current operating point and measures dispersion again
before solving. Incompatible machine/backend channels, section, BPM order,
plane, knob weights/units, or energy knob/calibration are blocked. Malformed
matrices and responses without usable SVD modes are also rejected. Changes
to solver gain, step limits, and scan step do not change the matrix definition.
The dialog shows saved versus latest known quadrupole and energy settings;
fresh readings are logged at execution. This does not establish that the rest
of the machine optics is unchanged. Operating-point differences have no
invented numerical acceptance threshold: remeasure when optics or energy
changes make the response unreliable. Older reports without the response
record and operating-point snapshot are not offered for reuse.

The correction step uses normalized bounded least squares. For each knob,
`step_limit = knob.max_offset * solver.max_step_fraction`. The runtime model
retains the legacy internal attribute name `limit`; the solver normalizes the
knob variables by these step limits, applies the remaining cumulative bounds,
and targets `-solver.gain * D_eff`. `solver.regularization` penalizes normalized
knob usage, which selects a balanced solution when there are more knobs than
BPM constraints.
