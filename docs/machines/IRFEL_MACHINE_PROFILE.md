# IRFEL Machine Profile

This document explains the configuration files under `configs/machines/irfel/`
and how they work together.

## Directory Layout

`configs/machines/irfel/` contains the machine profile for IRFEL:

- `machine.json`
- `control_backends/real.json`
- `control_backends/vm.json`
- `apps/*.json`
- `model_backends/simulation.elegant.json`
- `references/control_system/irfel_pvlist.json`
- `references/README.md`

The profile loader uses the first four groups plus `model_backends/*.json`.
Files under `references/` are source or migration material, not the primary
runtime profile entrypoint.

## Machine Definition

`machine.json` defines what the IRFEL machine is:

- `machine.id`: `irfel`
- `machine.display_name`: `IRFEL`
- `machine.default_mode`: `real`
- `runtime`: paths for the VM manager, softIOC manager, runtime JSON, and
  elegant bootstrap files
- `elements`: the machine element inventory

At the time of writing, the profile defines 64 elements:

- 10 BPMs
- 20 correctors
- 20 quadrupoles
- 3 bends
- 4 solenoids
- 2 modulators
- 5 flags

Elements define logical device structure, not PV names. For example, a BPM
declares logical channels such as `x`, `y`, and `s`; a quadrupole declares
`k1` and `readback`; a flag declares image and profile channels.

## Control Backends

`control_backends/real.json` and `control_backends/vm.json` map the same
logical channels to different PV names.

For example, the logical channel `BPM03.x` resolves differently depending on
the selected backend:

- `real`: `IRFEL-BI:BPM03:BPM_PX2`
- `vm`: `IRFEL:VM:BPM:BPM03:X`

The important rule is:

```text
Application code asks for a logical channel, such as BPM03.x.
The machine profile resolves that logical channel to a real or VM PV.
```

This keeps application code from hardcoding PV names and lets the same app run
against either the real machine or the VM/softIOC backend.

Both IRFEL backend files currently cover the same 64 elements and 137 logical
channels.

## App Workflows

Files under `apps/` define app-specific workflow choices for IRFEL. They do
not define the whole machine. They answer questions such as:

- Which subset of elements should this app use?
- What is the default flag, line, or preset?
- Which backend is allowed to write?
- What is the current real-machine commissioning status?

### `apps/orbit_correct.json`

Defines the IRFEL orbit workflow:

- BPM subset used by orbit correction
- horizontal and vertical corrector subsets
- response matrix wait time per backend
- corrector upper limit per backend and unit
  - `vm`: `0.001 rad`
  - `real`: `5.0 A`
- `real_status`
  - `orbit_display`: `read_only`
  - `orbit_correct`: `write_blocked`
- `write_control`
  - `vm`: `allowed`
  - `real`: `blocked`

`orbit_display` and `orbit_correct` share the `orbit` workflow, but their real
commissioning statuses are different because display is read-only while
correction writes corrector setpoints.

### `apps/beam_monitor.json`

Defines the beam monitor workflow:

- default flag: `PRFESA`
- default image geometry per backend:
  - `vm`: `[360, 270]`, `0.02 mm/pixel`
  - `real`: `[1440, 1080]`, `0.02 mm/pixel`
- optional `by_flag` image geometry overrides for PRFs that differ from the default
- default profile method: `Gaussian fit`
- default background sampling: `5` frames at `1.0 s` intervals
- real commissioning status: `commissioned`
- VM and real writes are allowed

The View Controls card offers `Gaussian fit` and `RMS moments`; display limits do
not affect either calculation. Gaussian fit remains the default, while RMS moments
reports the intensity-weighted second moment and is intentionally sensitive to the
selected background and image window. The previous cumulative Delta X/Delta Y
coordinate shift has been removed.

Background references are stored independently for each machine, backend, and flag.
Loading a background does not enable subtraction automatically. When enabled, the
clipped background-subtracted image is used both for display and for profile analysis.
The app may write the currently selected method's `sigx/sigy` values or exposure time
in workflows that allow it; IRFEL real mode permits these writes.

### `apps/emit_measure.json`

Defines emittance measurement presets:

- quadrupole to scan
- flag to read
- scan range and step count
- model line used for analysis
- default preset
- Twiss quadrupoles

IRFEL real mode has passed the write smoke test and remains
`write_smoke_passed`; it is not yet marked fully commissioned. VM and real
writes are allowed by the workflow policy.

### `apps/solenoid_centering.json`

Defines the real-only solenoid-centering presets and their scan, limit,
readback-verification, and restoration settings. The IRFEL workflow is
`commissioned`, and real writes are allowed subject to static/live preflight and
the application safety checks.

### `apps/energy_spectrum.json`

Defines the energy spectrum workflow:

- spectrum flag element: `PRFESA`
- VM WATCH source: `PRFESA`
- bend element: `BM03`
- bend channel used for readback
- coordinated real energy element: `ESA_ENERGY.setpoint`
  (`IRFEL:AP:ENG:A3:ao`)
- ESA quadrupoles
- start element choices
- default energy
- model backend: `simulation`

IRFEL real mode uses the coordinated A3 energy control instead of applying the
HALF bend-current calibration. Auto Find scans `ESA_ENERGY.setpoint` over the
configured `0–65 MeV` range, so it preserves the coordinated BM03/QM19/QM20 control.
The Energy setpoint control reads A3 on startup, follows later A3 changes while it is not
being edited, and supports `0.01 MeV` input. The main Energy Tuning card keeps the
Energy setpoint, Auto Find method, a compact settings summary, Auto Find, Stop, and
a Settings button. Its settings dialog contains scan range, coarse/fine point counts,
settling time, frame counts, frame gap, center step, center tolerance, and maximum
center offset; Stop restores the pre-scan A3 value.

The Optics Model starts at `QM12` with a model-derived input preset:
`alpha_x=-2.26`, `beta_x=10 m`, and `emittance_x=102.81183 nm`. The Twiss values
come from the 36 MeV Elegant design input, while the geometric emittance is
calculated from the configured `36MeV_slitbeam_col.dat` particle distribution.
Selecting `QM12` again reloads this preset; operators can still edit the fields
afterward when measured values are available.

IRFEL defaults to `Peak brightness + fitted center`.
This method first reuses the noise-tolerant highest-brightness
search, then calculates the same one-dimensional x-projection center used by the GUI's
current `direct` or `Gauss fit` method. It tries one fixed A3 center step in each
direction, continues in the direction that reduces the center error, and performs
one interpolated measurement after crossing `x_reference_mm`. It does not apply an
additional 2D beam or Gaussian-quality gate; too few valid profile fits or failed
final verification restores the pre-scan value. Fine points require at least two of
three valid beam frames. Center-search points use at least two of three fitted frames,
while final verification uses at least three of five frames. The terminal log records
the energy, fitted center, offset, valid-frame count, and fit method for every center
measurement. Each Auto Find also writes an immediately flushed CSV event log under
`runtime/irfel/real/runs/`; it includes the scan configuration, all Coarse/Fine and
center events, Stop requests, restoration events, and the final status. These files
are ignored by Git, and only the most recent 500 Auto Find CSV logs are retained.
The Background card keeps only the subtraction toggle, current-background status,
and a Background button. Sampling, preview, Load Latest, Load File, and Save As live
in a separate dialog. A sampled background is saved automatically to
`runtime/irfel/real/latest/background.npy` with `background.json` metadata and is
loaded on the next startup without automatically enabling subtraction. Save As
defaults to the backend-scoped `runs/` directory. The sampling interval is
configurable in the dialog (default `1.00 s`); the first frame is acquired
immediately and the interval is applied between subsequent frames. `Closest to x reference` remains
available for direct comparison with the older connected-region center logic.

### `apps/dispersion_correction.json`

Defines the IRFEL achromat correction workflow:

- default target BPMs: `BPM09`, `BPM10`
- default symmetric quadrupole knobs: `QM13/QM16` and `QM14/QM15`
- energy perturbation and solver defaults
- real-only backend support

The Control Room disables this application in IRFEL VM mode. Real mode resolves
the BPM and quadrupole PVs plus
`KLY1_CH3_PHASE.phase_set -> IRFEL:IN-MW:KLY1:SET_PHASE`. Following completed
onsite acceptance, the workflow is marked `commissioned` and
`write_control.real` is `allowed`, so the application runs in `write_enabled`
mode. There is no independent RF phase readback; the configured phase-to-`dp/p`
calibration and positive real-machine timing values were included in the onsite
acceptance. The GUI derives alternative BPM and quadrupole
choices directly from machine-native element types and resolves their real PVs
through this profile; the defaults above are not a selectable-device whitelist.
Both the static/live preflights and the machine-profile write policy must pass
before a write operation.

### `apps/bba.json`

Defines IRFEL BBA presets for VM bring-up:

- standard BBA preset
- BBA2 preset
- allowed backend for both families: `vm`
- real commissioning status: `not_supported`

This means BBA is intentionally not enabled for IRFEL real mode yet.

### `apps/virtual_machine.json`

Defines VM line and segment choices:

- predefined usedlines:
  - `ALL_MAIN`
  - `ALL_ESA`
  - `ALL_DUMP`
- local segment definitions:
  - `main_segment`
  - `esa_segment`
  - `dump_segment`
- default usedline and segment choices

This is used by the VM GUI and runtime logic to select a simplified machine
section without hardcoding IRFEL-specific choices in application code.

## Model Backend

`model_backends/simulation.elegant.json` defines the elegant-based simulation
backend.

It contains paths for:

- working directory
- source runtime JSON
- source lattice
- shared optics/matrix elegant files
- energy-spectrum dispersion and Twiss files
- matrix and Twiss outputs
- line names used by the model

Model-driven apps currently select the default `simulation` backend through the
shared loader. App workflow files provide only calculation-specific facts such
as model line names; the model backend file defines how to run the model and
where its inputs and outputs live.

## Runtime Paths

The `runtime` section in `machine.json` connects the profile to executable
runtime components:

- VM root: `src/virtual_machine/irfel_elegant`
- VM UI entrypoint
- VM manager entrypoint
- runtime JSON: `src/virtual_machine/irfel_elegant/irfel.json`
- bootstrap lattice and `.ele` files
- softIOC root: `src/softIOC/irfel`
- softIOC substitutions file

These paths let common scripts such as `scripts/start_vm.sh` and
`scripts/start_ioc_manager.sh` start the correct IRFEL runtime without
hardcoded per-machine logic.

## Runtime Selection

Use the clearer environment variable names for new commands:

```bash
HALF_LINAC_MACHINE_ID=irfel
HALF_LINAC_CONTROL_BACKEND=vm
```

Legacy names are still accepted for compatibility:

```bash
HALF_MACHINE_ID
HALF_CONTROL_BACKEND
```

Resolution priority is:

1. `HALF_LINAC_MACHINE_ID`
2. legacy `HALF_MACHINE_ID`
3. default `half`

Backend selection follows the same pattern:

1. `HALF_LINAC_CONTROL_BACKEND`
2. legacy `HALF_CONTROL_BACKEND`
3. machine default backend

## Overall Logic

The IRFEL profile can be read as this dependency chain:

```text
machine.json
  defines machine identity, runtime paths, elements, and logical channels

control_backends/*.json
  maps each logical channel to a backend-specific PV name

apps/*.json
  defines app-specific element subsets, presets, defaults, write policy,
  and real commissioning status

model_backends/*.json
  defines simulation/model execution paths and outputs
```

At runtime:

```text
HALF_LINAC_MACHINE_ID=irfel
HALF_LINAC_CONTROL_BACKEND=vm
```

means:

- load the IRFEL machine profile
- resolve all logical channels through `control_backends/vm.json`
- keep app workflow choices from `apps/*.json`
- use IRFEL VM runtime paths from `machine.json`

Current safety posture:

- VM workflows are intended for repeatable offline validation.
- IRFEL real backend mappings are present and can be validated offline.
- Most real-mode write workflows remain blocked until site PVs, units, limits,
  and rollback expectations are confirmed.
- `orbit_display` is read-only in real mode.
- `bba` is not supported in IRFEL real mode yet.

## Useful Checks

Run the offline IRFEL VM acceptance check:

```bash
bash scripts/check_irfel_vm.sh
```

Run the short VM/IOC runtime smoke after VM-related changes:

```bash
bash scripts/smoke_irfel_vm_runtime.sh
```
