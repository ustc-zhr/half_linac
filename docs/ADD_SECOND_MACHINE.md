# Add A Second Machine

## Goal

This document describes the smallest practical path for adding a second machine to the
current machine-profile system.

The target is not “fully configure every app at once”. The recommended path is:

1. Add machine-native elements.
2. Add `real` / `vm` backend PV mappings.
3. Bring up the simplest apps first.
4. Add measurement and model-driven apps later.

This follows the current project direction:

- app logic is shared
- machine config is native to each machine
- selectable candidates are derived from machine element types whenever possible
- presets are only defaults, not the main source of selectable candidates

## Current File Layout

For a new machine `irfel`, the directory should eventually look like this:

```text
configs/machines/irfel/
  machine.json
  control_backends/
    real.json
    vm.json
  apps/
    orbit_correct.json
    beam_monitor.json
    bba.json
    emit_measure.json
    energy_spectrum.json
    virtual_machine.json
  model_backends/
    simulation.elegant.json
```

`configs/machines/_template/` is the recommended starting point.

`apps/virtual_machine.json` is optional. Only add it if you want the VM control GUI to expose
machine-specific simplified start/end candidates instead of using the generic fallback:

- start candidates = all `quad` elements
- end candidates = all `flag` elements with logical channel `image`

## What Can Be Extracted From `irfel_pvlist.json`

Source file:

- [configs/machines/irfel/irfel_pvlist.json](/home/zhanghaoran/gitproj/half_linac/configs/machines/irfel/irfel_pvlist.json:1)

This file is not already in our machine-profile schema, but it contains enough structured
information to build the first machine-profile version for IRFEL.

### Directly Useful Groups

The following groups can be mapped almost directly:

- `steering_x`
  - `HC01` ... `HC07`
  - `HIC01`, `HIC02`
  - `MSHC`
- `steering_y`
  - `VC01` ... `VC07`
  - `VIC01`, `VIC02`
  - `MSVC`
- `quadrupole`
  - `QM01` ... `QM20`
- `bend`
  - `BM01`, `BM02`, `BM03`
- `bpm_x`
  - `BPM01_x` ... `BPM10_x`
- `bpm_y`
  - `BPM01_y` ... `BPM10_y`
- `bpm_s`
  - `BPM01_s` ... `BPM10_s`
- `esa`
  - `IRFEL:BD:ESA:H:E`
  - `IRFEL:BD:ESA:H:Sigma:Centroid`

### Direct IRFEL Examples

Examples extracted from the file:

- Horizontal corrector:
  - `HC01 -> IRFEL:PS:HC01:current:ao`
- Vertical corrector:
  - `VC01 -> IRFEL:PS:VC01:current:ao`
- Quadrupole:
  - `QM01 -> IRFEL:PS:QM01:current:ao`
- Bend:
  - `BM01 -> IRFEL:PS:BM01:current:ao`
- BPM X:
  - `BPM01.x -> IRFEL-BI:BPM01:BPM_PX2`
- BPM Y:
  - `BPM01.y -> IRFEL-BI:BPM01:BPM_PY2`
- BPM S:
  - `BPM01.s -> IRFEL-BI:BPM01:BPM_S`
- ESA energy readback:
  - `IRFEL:BD:ESA:H:E`

## What This File Does Not Fully Provide

`irfel_pvlist.json` is already enough for first-pass orbit and display apps, but it does not
fully define everything needed for all apps.

You will still need manual decisions for:

- machine element order
  - especially if names alone are not the exact beamline order
- image screen / flag PVs
  - needed by `beam_monitor`, `emit_measure`, `energy_spectrum`
- exposure-time PVs
  - needed if camera control is expected
- model backend files
  - needed by `bba` and `emit_measure`
- energy-spectrum image and bend-to-energy model details
  - needed by `energy_spectrum`

So the clean strategy is: do not try to enable every app from this one file alone.

## Recommended Rollout Order

### Stage 1: Orbit Display / Orbit Correct

These are the best first apps for IRFEL because the PV list already contains:

- BPM X
- BPM Y
- horizontal correctors
- vertical correctors

At minimum, prepare:

- `machine.json`
- `control_backends/real.json`
- `apps/orbit_correct.json`

You only need `control_backends/vm.json` once you actually want VM-mode channel mappings.

### Stage 2: Beam Monitor

Only do this after you identify real image-screen PVs and image geometry:

- `flag image PV`
- `exposure time PV` if needed
- image pixel shape
- pixel width

Then add:

- `apps/beam_monitor.json`
- corresponding `flag` elements and channels

### Stage 3: BBA / Emit Measure

Only do this after both are true:

- you know which `quad` / `corr` / `bpm` or `flag` elements should be used
- you have the model backend inputs ready

Then add:

- `apps/bba.json`
- `apps/emit_measure.json`
- `model_backends/simulation.elegant.json`

### Stage 4: Energy Spectrum

Only do this after you identify:

- which image screen is the energy-spectrum flag
- which bend PV is being scanned
- whether direct energy readback is used
- bend-current to energy conversion parameters or direct energy PV behavior

Then add:

- `apps/energy_spectrum.json`

### Stage 5: VM GUI

Only do this after you want the VM control window to present machine-specific simplified
segment choices.

Then optionally add:

- `apps/virtual_machine.json`

## Smallest Useful `machine.json` For IRFEL

For the first IRFEL pass, use simple native element kinds:

- `bpm`
- `corr`
- `quad`
- `bend`

And only add `plane` where physically needed:

- `corr` with `plane: "x"` or `plane: "y"`

Example shape:

```json
{
  "schema_version": "1",
  "machine": {
    "id": "irfel",
    "family": "linac",
    "display_name": "IRFEL",
    "default_mode": "real"
  },
  "elements": [
    {
      "id": "BPM01",
      "kind": "bpm",
      "display_name": "BPM01",
      "order": 1,
      "tags": ["orbit"],
      "limits": {},
      "logical_channels": ["x", "y", "s"]
    },
    {
      "id": "HC01",
      "kind": "corr",
      "display_name": "HC01",
      "order": 101,
      "plane": "x",
      "tags": ["orbit"],
      "limits": {},
      "logical_channels": ["setpoint", "readback"]
    },
    {
      "id": "VC01",
      "kind": "corr",
      "display_name": "VC01",
      "order": 201,
      "plane": "y",
      "tags": ["orbit"],
      "limits": {},
      "logical_channels": ["setpoint", "readback"]
    },
    {
      "id": "QM01",
      "kind": "quad",
      "display_name": "QM01",
      "order": 301,
      "limits": {},
      "logical_channels": ["k1", "readback"]
    },
    {
      "id": "BM01",
      "kind": "bend",
      "display_name": "BM01",
      "order": 401,
      "limits": {},
      "logical_channels": ["current_set", "current_readback"]
    }
  ]
}
```

## Which Entrypoints Already Derive Candidates Dynamically

These apps already derive most selectable candidates from machine-profile element kinds and
channels:

- `orbit_display`
- `orbit_correct`
- `beam_monitor`
- `bba`
- `emit_measure`
- `energy_spectrum`

The main extra VM-specific file is:

- `apps/virtual_machine.json`

That file is only needed if the default `quad` -> `flag(image)` fallback is not the simplified
segment list you want to expose in the VM control GUI.

## Minimal `real.json` Mapping Strategy

Map machine-profile logical channels to real PVs directly.

Examples from `irfel_pvlist.json`:

```json
{
  "backend": "real",
  "channels": {
    "BPM01": {
      "x": "IRFEL-BI:BPM01:BPM_PX2",
      "y": "IRFEL-BI:BPM01:BPM_PY2",
      "s": "IRFEL-BI:BPM01:BPM_S"
    },
    "HC01": {
      "setpoint": "IRFEL:PS:HC01:current:ao",
      "readback": "IRFEL:PS:HC01:current:ai"
    },
    "VC01": {
      "setpoint": "IRFEL:PS:VC01:current:ao",
      "readback": "IRFEL:PS:VC01:current:ai"
    },
    "QM01": {
      "k1": "IRFEL:PS:QM01:current:ao",
      "readback": "IRFEL:PS:QM01:current:ai"
    },
    "BM01": {
      "current_set": "IRFEL:PS:BM01:current:ao",
      "current_readback": "IRFEL:PS:BM01:current:ai"
    }
  }
}
```

Note:

- For IRFEL quadrupoles, the underlying PV name is current-based.
- It is still acceptable to expose that machine channel through logical name `k1` if the app
  expects a quad-control channel there.
- Keep the logical interface stable for the app; let the PV naming remain machine-native.

## Minimal `apps/orbit_correct.json`

For IRFEL, the first useful app file is likely `apps/orbit_correct.json`.

If the machine order is already reliable, you can either:

- write the explicit arrays
- or omit them and let the workflow be inferred by order

Explicit form is safer for the first machine bring-up:

```json
{
  "bpms": ["BPM01", "BPM02", "BPM03"],
  "xcors": ["HC01", "HC02", "HC03"],
  "ycors": ["VC01", "VC02", "VC03"],
  "response_wait_s_by_backend": {
    "vm": 8,
    "real": 0.5
  },
  "corrector_upperlimit_rad": 0.001
}
```

## What To Fill First For IRFEL

If the immediate goal is “make a second machine run in the current framework with the least
manual work”, the first recommended subset is:

1. `BPM01` ... `BPM10`
2. `HC01` ... `HC07`, `HIC01`, `HIC02`, `MSHC`
3. `VC01` ... `VC07`, `VIC01`, `VIC02`, `MSVC`
4. `QM01` ... `QM20`
5. `BM01` ... `BM03`

This is enough to build a meaningful first IRFEL profile for:

- `orbit_display`
- `orbit_correct`

And it gives you a clean base for later adding:

- `bba`
- `emit_measure`
- `beam_monitor`
- `energy_spectrum`

## Practical Build Order For IRFEL

Use this exact order:

1. Copy `_template` into `configs/machines/irfel/`
2. Keep `irfel_pvlist.json` in the same directory as the raw source
3. Fill `machine.json` with:
   - BPMs
   - correctors
   - quads
   - bends
4. Fill `control_backends/real.json` from the PV list
5. Create a temporary `control_backends/vm.json`
6. Fill `apps/orbit_correct.json`
7. Verify:
   - `orbit_display`
   - `orbit_correct`
8. Only then continue to image-screen and model-based apps

## Recommended Principle While Building IRFEL

Do not try to translate the whole `irfel_pvlist.json` into machine-profile schema in one pass.

Instead:

- keep the raw PV list as the source reference
- extract only what the next app actually needs
- let the machine profile grow app-by-app

That matches the current repo design much better than a one-shot “convert everything first”
approach.
