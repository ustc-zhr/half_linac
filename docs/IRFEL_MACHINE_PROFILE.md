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

- default flag: `PRF03`
- default image geometry per backend:
  - `vm`: `[360, 270]`, `0.02 mm/pixel`
  - `real`: `[360, 270]`, `0.02 mm/pixel`
- optional `by_flag` image geometry overrides for PRFs that differ from the default
- real commissioning status: `write_blocked`
- VM writes are allowed, real writes are blocked

The app may write fitted `sigx/sigy` values or exposure time in workflows that
allow it. For IRFEL real mode these writes remain blocked.

### `apps/emit_measure.json`

Defines emittance measurement presets:

- quadrupole to scan
- flag to read
- scan range and step count
- model line used for analysis
- default preset
- Twiss quadrupoles

IRFEL real mode is currently `write_blocked`; VM mode is allowed.

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

### `apps/dispersion_correction.json`

Defines the IRFEL achromat correction workflow:

- default target BPMs: `BPM09`, `BPM10`
- default symmetric quadrupole knobs: `QM13/QM16` and `QM14/QM15`
- energy perturbation and solver defaults
- real-only backend support

The Control Room disables this application in IRFEL VM mode. Real mode resolves
the BPM and quadrupole PVs plus
`KLY1_CH3_PHASE.phase_set -> IRFEL:IN-MW:KLY1:SET_PHASE`, but remains
in application mode `read_only` because `write_control.real` is explicitly
blocked. There is no independent RF phase readback, and the phase-to-`dp/p`
calibration and positive real-machine timing values are configured but still
require onsite confirmation. The GUI derives alternative BPM and quadrupole
choices directly from machine-native element types and resolves their real PVs
through this profile; the defaults above are not a selectable-device whitelist.
Both the static/live preflights and the machine-profile write policy must pass
before a write test.

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
- emit-measurement elegant files
- energy-spectrum elegant files
- matrix and Twiss outputs
- line names used by the model

App workflow files can refer to this backend by name, for example
`"model_backend": "simulation"` in `apps/energy_spectrum.json`.

The app says which model backend it wants; the model backend file says how to
run that model and where its inputs and outputs live.

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
