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

2. Add BBA later

- Add or edit `apps/bba.json`

3. Add emit_measure later

- Add or edit `apps/emit_measure.json`

## Design Direction

- Prefer dynamic selection by element `kind`
- Use `plane` only when needed, such as for correctors
- Treat presets as default values, not as the main definition of selectable elements
- Let machine configs grow app-by-app instead of requiring every app config up front
