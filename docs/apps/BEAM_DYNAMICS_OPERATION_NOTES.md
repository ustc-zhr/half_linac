# Beam Dynamics Operation Notes

## BBA And Twiss Across Energy-Changing Sections

BBA is generally not suitable across sections where the beam energy changes
significantly.

Beam-Based Alignment usually assumes that the observed orbit response is mainly
caused by quadrupole center offsets or calibration errors. In an accelerating or
otherwise energy-changing section, the measured orbit response can also include
energy-dependent dispersion terms:

```text
x = x_beta + D * delta
```

If the model and measurement procedure do not explicitly separate these effects,
BBA can mistake RF phase errors, energy gain changes, or dispersive orbit shifts
for alignment errors. This is especially risky near accelerating cavities, in
regions with changing energy spread, or wherever the dispersion is not negligible.

Prefer doing BBA in transport sections where the beam energy is approximately
constant. If BBA must cross an energy-changing section, split the analysis into
smaller sections with the appropriate reference energy, or use a model that
explicitly includes acceleration, dispersion, RF phase, and energy gain.

Twiss calculation can be done across energy-changing sections, but only when the
model includes the energy change correctly.

The Twiss parameters can be propagated through accelerating structures. The key
point is that magnetic rigidity changes with energy, so effective focusing
strengths also change. For a quadrupole:

```text
k is proportional to G / (B rho)
```

where `G` is the quadrupole gradient and `B rho` is the magnetic rigidity. A
constant-energy approximation is therefore not valid across a section with
significant acceleration.

In short:

```text
BBA: avoid sections with significant energy change when possible.
Twiss: valid across energy-changing sections only if the lattice/model includes
       the energy variation.
```

For this half-linac / IRFEL workflow, prefer placing BBA in same-energy transport
or achromat sections. Twiss calculation may run continuously from the injector to
the downstream line, but the elegant lattice should contain the correct cavity
energy gain and phase before the result is treated as operationally meaningful.
