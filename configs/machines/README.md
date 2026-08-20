# Machine Profiles

This directory contains the machine distributions used by Accelerator HLA Platform. HALF,
IRFEL, and future accelerators are peer machine profiles; the historical repository name
`half_linac` does not limit the platform to HALF.

Each machine has one directory:

```text
configs/machines/<machine_id>/
  machine.json
  control_backends/
    vm.json
    real.json
  apps/
    orbit_correct.json
    beam_monitor.json
    bba.json
    emit_measure.json
    energy_spectrum.json
    virtual_machine.json
  model_backends/
    simulation.elegant.json
  calibrations/
    solenoids.json
  references/
    README.md
    control_system/
    epics_examples/
```

`_template/` is the recommended starting point for a new machine. It is intentionally
hidden from the runtime selector by the leading underscore.

## File Responsibilities

`machine.json` defines the machine itself:

- machine id, family, display name, and default control backend
- VM and softIOC runtime paths when the machine supports VM workflows
- machine-native element inventory
- each element's `kind`, order, optional `plane`, tags, limits, and logical channels

Keep PV names out of `machine.json`. Elements should describe logical device structure,
such as `BPM03.x`, `Q01.K1`, or `PRF01.image`.

`control_backends/*.json` maps logical channels to backend-specific PV names:

- `control_backends/vm.json` maps channels to VM or softIOC PVs
- `control_backends/real.json` maps the same logical channels to real-machine PVs

Backends may expose different channel subsets. For example, a real quadrupole may expose
current setpoint, current readback, writable `K1`/`K1_adj` physics channels, and a read-only
computed `K1_total`, while the VM may only expose `K1`. Application code should resolve
logical channels through the machine-profile loader instead of hardcoding PV names.
For correctors, keep backend units explicit. A real-machine backend may expose
`current_set` and `current_readback` in amperes, while a VM backend may expose `kick`
in radians when it writes the elegant `KICK` field. The resolver still treats legacy
`setpoint` as an alias for `current_set` when loading older profiles.

For bends, keep the same distinction. A real-machine backend may expose bend power-supply
`current_set` and `current_readback` in amperes, while a VM backend may expose `angle`
in radians when it writes the elegant `ANGLE` field. Do not treat VM bend angle as a
magnet current unless a separate current-to-angle calibration is defined.

Common logical channel meanings:

| Logical channel | Unit | Write policy | Typical backend | Meaning |
| --- | --- | --- | --- | --- |
| `x`, `y` | machine display unit, usually mm | read-only | `vm`, `real` | BPM transverse position |
| `image`, `esa_image` | waveform pixels | read-only | `vm`, `real` | profile-monitor image data |
| `sigx`, `sigy` | image-analysis size unit | read-only | `vm`, `real` | measured horizontal/vertical beam size |
| `exposure_time` | seconds | writable when supported | `vm`, `real` | profile-monitor camera exposure |
| `K1` | 1/m^2 | writable when mapped to a writable PV | `vm`, `real` | quadrupole normalized strength |
| `K1_adj` | 1/m^2 | writable | `real` | adjustable quadrupole strength trim |
| `K1_total` | 1/m^2 | read-only | `real` | computed total quadrupole strength |
| `current_set` | A | writable | `real` | power-supply current setpoint |
| `current_readback` | A | read-only | `real` | power-supply current readback |
| `voltage_set` | kV | writable | `vm`, `real` | modulator high-voltage setpoint |
| `voltage_readback` | kV | read-only | `vm`, `real` | modulator high-voltage readback |
| `current` | A | read-only | `real` | beam current measured by an ICT in macro-pulse/micro-pulse operation |
| `kick` | rad | writable | `vm` | corrector kick used by elegant `KICK` |
| `angle` | rad | writable | `vm` | bend angle used by elegant `ANGLE` |
| `charge` | backend-specific; CT apps normalize to nC | read-only | `vm`, `real` | integrated beam charge measured by an ICT |
| `peak_current` | A unless the PV supplies another EGU | read-only | `real` | peak beam current measured by an FCT |
| `setpoint` | backend-specific | legacy writable alias | legacy profiles | compatibility alias for `current_set` |
| `readback` | backend-specific | legacy read-only alias | legacy profiles | compatibility alias for `current_readback` |

When a physical quantity is not implemented by a backend, omit that backend mapping instead of
reusing another logical channel with a different unit. For example, do not put a real-machine
current PV under VM `kick`, and do not put a VM bend angle under real `current_set`.

Writable magnet endpoints are resolved strictly by the shared machine-profile resolver. Corrector,
bend, and solenoid channels are derived when the element kind and backend make the physical quantity
unambiguous. Quadrupole applications must select `current` or `K1`; the resolver then returns the PV
and the limit for the same logical channel. Missing channels fail validation and never fall back to a
channel with a different unit.

`real.json` is the source of truth for real-machine PV names. Keep it accurate because real
machine operation has no softIOC fallback.

`vm.json` is the interface between apps and the VM/softIOC backend. It does not need to mirror
the elegant lattice or the full real-machine PV set. It only needs to map the logical channels
that VM-mode apps use, and every PV named in `vm.json` should be provided by the softIOC or VM
runtime.

The elegant lattice may contain extra model-only elements such as drifts, markers, or internal
WATCH elements. Those do not need entries in `machine.json` or `vm.json` unless apps need to
select them or resolve PVs for them.

`apps/*.json` contains app workflow facts that cannot be inferred cleanly from the
machine element inventory:

- selected subsets when the full element list is not appropriate
- scan defaults, response wait times, limits, image geometry, and default flags
- write-control policy and real-machine commissioning status
- recommended presets for BBA, emittance, or solenoid-centering workflows

Do not duplicate broad selectable element lists in app configs when they can be
derived from element `kind`, `plane`, or minimal tags.
Prefer element ids plus logical channels over direct PV strings. Keep direct PVs in
app configs only for external controls that are not yet represented as machine elements.
See [`docs/platform/APP_WORKFLOW_CONFIG_PRINCIPLES.md`](../../docs/platform/APP_WORKFLOW_CONFIG_PRINCIPLES.md)
for the shared layout, scan, limit, derivation, and compatibility conventions used by
these workflow files.

`calibrations/*.json` contains tracked runtime calibration data that converts a physical
quantity into a machine-native setpoint. Keep original measurements in `references/`; do not
duplicate machine current limits in calibration files, because `machine.json` remains the
source of truth for those limits.

`model_backends/*.json` describes simulation or analysis model inputs used by
model-driven apps. App workflow files may select one of these backends by name.
For elegant backends, keep source assets and generated working files separate:

- `source_lattice` points to the tracked machine design lattice.
- `*_ini_ele` points to tracked, model-backend-owned elegant input templates.
- `optics_working_dir` and `energy_working_dir` point to ignored runtime workspaces
  under `runtime/model_backend/<machine>/<backend>/`.
- Generated files such as `optics.json`, `optics.lte`, `esa.json`, and `esa.lte`
  are runtime artifacts and should not be tracked.

Model snapshots may define field-level rules in `snapshot_mapping.defaults` when
the logical channel, units, and conversion are genuinely identical for every
element using that model field. Element-specific entries in
`snapshot_mapping.fields` take precedence. Current-based magnets or any channel
requiring calibration must keep an explicit element mapping and conversion.

`references/` is for machine-specific source references and migration material, not runtime
profile data. Use `references/control_system/` for raw PV lists or interface documents supplied
by a control-system group, and `references/epics_examples/` for IOC templates or substitutions
kept only as implementation examples. Add a small `references/README.md` that records provenance,
date, intended use, publication status, and which profile files were derived from each source.

Raw control-system files may expose a complete PV topology, writable controls, engineering
limits, or document metadata. Keep them ignored or in an access-controlled store unless their
owner has approved publication. Do not make applications read Word, Excel, substitutions, or
other reference files at runtime. The maintained runtime sources of truth remain `machine.json`,
`control_backends/*.json`, `apps/*.json`, and `model_backends/*.json`.

## Legacy `profile.json`

Do not add or maintain `profile.json` for active machines.

`profile.json` is the old single-file profile shape. The loader still has a fallback
for legacy tests and old machine directories, but active machine profiles should use
the directory layout above. When `machine.json` exists, it is the runtime entrypoint;
`profile.json` in the same directory would only create confusion.

## Smallest Useful Shapes

1. Orbit-only machine

- Keep `machine.json`
- Add at least one `control_backends/<backend>.json`
- Add `apps/orbit_correct.json` only when the inferred BPM/XCOR/YCOR lists or runtime
  defaults are not enough

2. Add VM support

- Add a `vm` control backend
- Fill `machine.json.runtime`
- Add `apps/virtual_machine.json` only when the VM GUI needs machine-specific usedline
  or segment choices

3. Add beam monitor

- Add flag elements with `image` channels
- Add `apps/beam_monitor.json`
- Set flag image geometry for each backend that should publish or read images

4. Add BBA or emit_measure

- Add the needed quad, corrector, BPM, or flag elements
- Add `apps/bba.json` or `apps/emit_measure.json`
- Add `model_backends/*.json` when analysis depends on elegant or another model

5. Add energy_spectrum

- Add the spectrum flag and bend elements
- Add any ESA quads or start-element tags required by the workflow
- Add `apps/energy_spectrum.json`
- Add or select a model backend when the workflow needs model files

## Design Direction

- Prefer dynamic selection by element `kind`
- Use `plane` only when physically needed, such as for correctors
- Use minimal tags for facts that cannot be represented by `kind` or `plane`
- Treat presets as defaults, not as the main definition of selectable elements
- Let machine configs grow app-by-app instead of requiring every app config up front
