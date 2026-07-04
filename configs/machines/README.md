# Machine Profiles

This directory contains runtime machine profiles. Each machine has one directory:

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
  other/
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
| `kick` | rad | writable | `vm` | corrector kick used by elegant `KICK` |
| `angle` | rad | writable | `vm` | bend angle used by elegant `ANGLE` |
| `setpoint` | backend-specific | legacy writable alias | legacy profiles | compatibility alias for `current_set` |
| `readback` | backend-specific | legacy read-only alias | legacy profiles | compatibility alias for `current_readback` |

When a physical quantity is not implemented by a backend, omit that backend mapping instead of
reusing another logical channel with a different unit. For example, do not put a real-machine
current PV under VM `kick`, and do not put a VM bend angle under real `current_set`.

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

`model_backends/*.json` describes simulation or analysis model inputs used by
model-driven apps. App workflow files may select one of these backends by name.

`other/` is for source references and migration material, not runtime profile data.

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
