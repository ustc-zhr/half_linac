# IRFEL Machine Reference Sources

This directory contains source and migration material used to build the IRFEL machine profile.
It is not loaded at runtime.

- `control_system/irfel_pvlist.json`: original structured PV inventory used during the initial
  profile migration

The maintained runtime sources of truth are `../machine.json`, `../control_backends/*.json`, and
the selected files under `../apps/` and `../model_backends/`. A change to the reference inventory
does not update runtime configuration automatically.
