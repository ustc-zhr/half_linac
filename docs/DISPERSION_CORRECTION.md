# Dispersion Correction

The dispersion-correction application lives in `src/apps/dispersion_correction/`.
It measures horizontal effective dispersion with a two-sided energy perturbation,
builds a response matrix for symmetric quadrupole knobs, and applies bounded SVD
correction steps with snapshot restoration on abort or failure.

## Runtime configuration

Production runtime configuration comes from:

- `configs/machines/<machine>/apps/dispersion_correction.json`
- the selected machine's `control_backends/<backend>.json`

The JSON/YAML files under `tests/dispersion_correction/fixtures/` are regression
fixtures from the former standalone package. They are not a second production PV
configuration source.

IRFEL has the commissioned workflow skeleton described below. HALF exposes a
model-only `bl01` section: it can calculate an isolated Elegant design response,
but machine measurement and correction remain blocked until an energy actuator
and its `dp/p` calibration are commissioned.

HALF model analysis uses `configs/machines/half/apps/dispersion_correction.json`
and the shared Elegant model backend. Generated model files stay under
`runtime/model_backend/half/simulation/`; the calculation does not mutate the VM
lattice or write any PV.

## Current safety boundary

- IRFEL `vm` is unsupported and the Control Room disables the application in
  that backend.
- IRFEL `real` resolves BPM, quadrupole, and
  energy-knob channels from the machine profile. Its current actuator happens
  to be `KLY1_CH3_PHASE`, mapped to `IRFEL:IN-MW:KLY1:SET_PHASE`. Backend and
  write mode are selected by the machine profile rather than GUI controls.
- Measure, response, and correction operations all change the energy actuator and
  are therefore treated as write operations.
- The current RF-phase actuator has an independent readback at
  `IRFEL:IN-MW:KLY1:GET_CH3_PHASE`.
- The configured actuator-to-`dp/p` calibration and timing values still require
  onsite confirmation before the machine-profile write policy is enabled.

The measurement workflow itself only requests normalized `delta_p_over_p`.
The machine profile selects the physical actuator (RF phase, modulator voltage,
or another energy control), its unit, generic `set`/`readback` channels, and the
linear-relative `actuator_per_delta` conversion. Changing actuator type therefore
does not change the dispersion algorithm. The old `phase_set`, `phase_readback`,
and `phase_per_delta` fields remain accepted only as compatibility aliases for
standalone regression fixtures.

The current workflow contains `real_status: write_blocked` and
`write_control.real: blocked`, so the IRFEL real profile resolves to
`read_only`. The current `actuator_per_delta`, sampling interval, and settle time
are present, but the write policy remains the final independent commissioning
gate until those values are confirmed onsite.

In machine-profile mode the GUI derives selectable BPMs and quadrupoles from
the machine's native `bpm` and `quad` elements. The workflow file supplies only
the recommended default BPMs, two symmetric knob pairs, scan defaults, and hard
limits. Operators can select BPMs, choose the two devices in each symmetric
pair, and reduce scan/limit values for the current session without editing JSON
or entering PV names. A quadrupole cannot be used in two knob pairs, and the
response solve requires at least as many selected BPMs as knobs.

Before any enabled operation writes, the workflow performs a read-only live
preflight of the energy actuator, quadrupole setpoint/readback agreement, and all
target BPMs. A failed check prevents the first `caput`. The GUI exposes this as
`Check Connections` and shows `READY`, `UNCHECKED`, or `NOT READY` in the status
bar. The Readiness view lists the values it read and explicitly confirms that no
setpoint changed.

The GUI separates the operator workflow from model work:

- `Online` presents connection check, dispersion measurement, quadrupole-response
  measurement, and recommendation review in execution order. In read-only mode
  it explains why write-dependent actions are disabled instead of silently
  greying them out.
- `Dispersion`, `Q Response`, and `Correction` contain the corresponding online
  results.
- `Model / Import` contains Elegant comparison and external eta-x import. These
  actions never enable or perform machine writes.

The main configuration panel shows the energy step in both normalized `dp/p` and
physical actuator units. Less frequently changed sampling and solver parameters
are under `Advanced settings`.

For model-only sections, the GUI hides the energy-step input and states that
Elegant calculates dispersion directly without an energy scan. Standalone
offline measurement demonstrations instead label the step as simulated and make
clear that no backend or PV write occurs. Only online VM/REAL measurement
workflows show the configured physical energy actuator conversion.

The normal correction path is deliberately staged:

1. `Measure Dispersion` records the current beam-based effective dispersion.
2. `Measure Q Response` measures the response columns and records a fresh
   dispersion baseline.
3. `Review Recommendation` performs a pure bounded SVD calculation. It neither
   reads nor writes the backend. The Correction page shows measured and predicted
   dispersion at every BPM, predicted RMS, knob changes, and physical
   quadrupole targets when live readbacks are available.
4. `Apply & Remeasure` is a separate write action. It first shows the exact
   physical targets and PVs, requires explicit confirmation, reruns live
   preflight, and verifies that the quadrupoles still match the reviewed
   baseline. It applies the reviewed targets once and remeasures dispersion.
   Failed safety checks or insufficient improvement restore the pre-apply
   snapshot.

Changing the section, BPMs, knobs, energy step, measurement settings, solver
gain, or step limit discards staged measurements and recommendations. This
prevents an old response matrix or target preview from being applied after the
configuration changes. The former multi-iteration automatic correction remains
available on the Correction page as `Advanced: Automatic Loop`; it is not the
default operator path.

## Running and checking

Launch from the Control Room:

```bash
bash scripts/runMe
```

Run the focused tests:

```bash
source scripts/setup.sh
pytest -q tests/dispersion_correction
```

The CLI uses the active machine profile when `--config` is omitted. Standalone
fixtures can still be supplied explicitly for regression and read-only work,
but an external EPICS `--config` cannot authorize `measure` or `run` writes.

For the next real-machine step, start with operations that do not call `caput`:

```bash
source scripts/setup.sh
export HALF_LINAC_MACHINE_ID=irfel
export HALF_LINAC_CONTROL_BACKEND=real
python3 -m half_linac.src.apps.dispersion_correction.cli preflight
python3 -m half_linac.src.apps.dispersion_correction.cli status --json
```

`preflight` is configuration-only. `status` performs EPICS reads of the
configured BPM, quadrupole and energy-actuator PVs. Neither command writes.

Energy-knob calibration is managed as two distinct layers:

- The configured machine-profile value is displayed read-only and remains
  subject to the profile commissioning/write policy.
- `Open Calibration Editor` accepts points directly in an editable table. It
  supports measured energy (the GUI computes `(E-E0)/E0`) or direct `dp/p`,
  clipboard paste, a fit preview, residual/linearity checks, and runtime draft
  saving. No editor action writes a PV or modifies the machine profile.
- A draft that passes the point-count, two-sided coverage, fit quality,
  directional-slope, baseline, and target-range checks may be activated for the
  current GUI session after explicit confirmation. Activation invalidates all
  staged measurements and recommendations. `Restore Configured Calibration`
  removes the session override.

Drafts preserve raw points, fit diagnostics, actuator/unit, machine/backend, and
timestamps under
`src/apps/dispersion_correction/runtime/<machine>/<backend>/calibrations/`.
They are runtime evidence, not source configuration.

CSV remains an optional commissioning exchange format through the CLI:

```bash
python3 -m half_linac.src.apps.dispersion_correction.cli \
  calibrate-energy-knob --csv energy_knob.csv
```

The default columns are `actuator_value` and `delta_p_over_p`; `phase_deg`
remains accepted for legacy phase files. A confirmed calibration must still be
promoted deliberately into the machine profile rather than being written there
by the GUI.

Compare the HALF BL01 design optics without machine IO:

```bash
source scripts/setup.sh
export HALF_LINAC_MACHINE_ID=half
export HALF_LINAC_CONTROL_BACKEND=vm
python3 -m half_linac.src.apps.dispersion_correction.cli model-response --section bl01
```

The model report includes endpoint `D/D'` observables and the selected, design,
and design-reference K1 values for the configured correction quadrupoles. With
`Current snapshot` selected, the app reads the active VM or REAL K1 PVs, then
recomputes an isolated curve after restoring only the configured correction
quadrupoles to their lattice design values. This is a design-reference prediction,
not a beam-based correction recommendation, and it never writes a PV. The GUI
plots selected, full-design, and design-reference horizontal/vertical dispersion
curves. A lattice strip on the same longitudinal
axis shows horizontal/vertical bends, focusing/defocusing quadrupoles, BPMs,
RF elements, and the configured dispersion constraint locations. Hovering over
the strip reports element name, type, position, length, and applicable K1 or
bend angle. Quadrupoles use one color: positive K1 is drawn above the beamline
and negative K1 below it. WATCH/screens, correctors, and collimator apertures
are hidden because they do not change this first-order dispersion model; RF is
recognized from its Elegant element type rather than a `PRF` diagnostic name.
The report also compares selected and design-reference peak beta functions.
Actual correction remains a separate beam-based workflow: measure dispersion
with the calibrated energy knob, calculate a small quadrupole step, apply it
under backend limits, and remeasure.

## Commissioning steps still required

1. Run static preflight and read-only status checks against the real IOC.
2. Choose the physical energy actuator and measure its actuator-to-`dp/p`
   calibration, sign, valid range, and units.
3. Agree on the smallest permitted actuator perturbation and restoration tolerance.
4. Validate setpoint-only verification and rollback with fake EPICS before a real
   write smoke test.
5. Perform an explicitly approved `+actuator -> baseline -> -actuator -> baseline`
   smoke test while an operator observes machine protection and beam state.
6. Record the write-smoke outcome and keep `write_control`/`real_status` aligned
   with the accepted commissioning state.
