# Machine Template

Copy this directory to `configs/machines/<machine_id>/` when starting a new machine profile.

This template is intentionally hidden from the runtime selector by the leading underscore in
`_template`.

## Smallest Useful Shapes

1. Orbit-only machine

- Keep:
  - `machine.json`
  - `control_backends/vm.json`
  - `control_backends/real.json`
- `apps/orbit_correct.json` is optional.
  If omitted, orbit BPM/XCOR/YCOR lists are inferred from machine element order.
  If present, it may also hold small runtime facts such as response wait time or corrector limit.

2. Add BBA later

- Add or edit `apps/bba.json`

3. Add emit_measure later

- Add or edit `apps/emit_measure.json`

4. Add beam_monitor later

- Add or edit `apps/beam_monitor.json`
- Set the shared flag image geometry for `vm` and `real`

5. Add energy_spectrum later

- Add or edit `apps/energy_spectrum.json`
- Add one flag with `esa_image`
- Add one bend with `current_set`
- Add the three ESA quads referenced by the app config

## Design Direction

- Prefer dynamic selection by element `kind`
- Use `plane` only when needed, such as for correctors
- Treat presets as default values, not as the main definition of selectable elements
- Let machine configs grow app-by-app instead of requiring every app config up front
