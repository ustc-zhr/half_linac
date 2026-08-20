# HALF Machine Reference Sources

This directory documents the source material used to build and review the HALF machine profile.
Reference files are provenance and migration inputs; they are not runtime configuration and are
not loaded by applications, the VM, or the IOC manager.

## Runtime Sources Of Truth

The maintained HALF runtime configuration is:

- `../machine.json`: machine elements and logical channels
- `../control_backends/real.json`: real-machine PV mappings
- `../control_backends/vm.json`: VM/softIOC PV mappings
- `../apps/*.json`: app-specific workflow facts
- `../model_backends/*.json`: model backend configuration

If a reference document and a runtime profile disagree, do not silently copy one over the other.
Confirm the current interface with the control-system owner, update the structured profile, and
record the decision in review or commissioning notes.

## Local Source Material

`solenoid/` contains original magnetic-measurement workbooks. The maintained runtime
translation is `../calibrations/solenoids.json`; update it deliberately when a measurement
is superseded, while retaining the source workbook for provenance.

The following subdirectories are intentionally ignored by Git because this repository is public:

- `control_system/`: original PV lists and interface documents supplied by the HALF control-system
  group, including the 2026-05-15 beam-diagnostics and magnet/physics-quantity lists
- `epics_examples/`: IOC substitutions and database templates retained as naming or implementation
  references

Do not force-add these files without confirming that the source owner permits public distribution
and that embedded Office metadata has been reviewed.

The current `halfSR.substitutions` example uses `VM:SR` naming and is a storage-ring/IOC reference;
it must not be treated as the authoritative HALF injector PV list.

## Updating The Profile

When new source material arrives:

1. Keep the unmodified original under the appropriate ignored subdirectory.
2. Record its source, received date, scope, and revision in this README without copying PV values.
3. Translate only confirmed elements and channels into `machine.json` and
   `control_backends/real.json`.
4. Validate the machine profile and review units, write permissions, and engineering limits.
5. Treat successful configuration or VM validation separately from real-machine commissioning.
