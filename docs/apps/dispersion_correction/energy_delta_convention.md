# Energy Delta Convention

This package uses one scalar energy perturbation in the dispersion formula:

```text
delta = dp / p
D_eff = dx / delta
```

For the IRFEL use case, the target electron beam energy is at least tens of
MeV. In this energy range the beam is ultra-relativistic enough for this MVP to
treat relative momentum and relative energy perturbations as equivalent:

```text
dp / p ~= dE / E
```

Therefore user-facing configuration does not require `beta` or an
`energy_delta_unit`. The configured `energy_knob.delta` is interpreted directly
as the dimensionless perturbation used by the algorithm.

When the actuator is an RF phase PV, the required site calibration is:

```text
phase offset -> delta
```

represented in JSON as:

```json
{
  "energy_knob": {
    "calibration": {
      "kind": "linear",
      "phase_per_delta": 2500.0
    }
  }
}
```

With `delta: 1.0e-4` and `phase_per_delta: 2500.0`, the two-sided measurement
uses `+0.25 deg` and `-0.25 deg` phase offsets.

The EPICS adapter exposes the RF actuator to the workflow in normalized delta
coordinates. It reads the phase setpoint/readback, divides by
`phase_per_delta`, and converts requested delta coordinates back to phase before
writing. The absolute calibration intercept is not required because the
workflow only applies offsets around a captured baseline and restores that
baseline afterward.
