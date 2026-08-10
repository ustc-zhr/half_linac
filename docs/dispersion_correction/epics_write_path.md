# EPICS Write And Restore Path

This document describes the adapter contract inherited from the standalone
implementation. The integrated IRFEL machine profile maps
`MODULATOR_HV1.voltage_set` to `IRFEL:modulator1:HV_set:ao` and blocks all
external writes at the application runtime layer until the modulator-voltage
calibration and commissioning are complete.

Real-machine writes require `backend.type: epics`, `backend.mode: write_enabled`,
a valid modulator-voltage calibration, positive sampling/settle times, and
successful static and live preflights. The live preflight reads the HV setpoint,
quadrupole setpoints/readbacks, and every target BPM without writing. An
external CLI `--config` cannot grant EPICS write authority; that authority must
come from the selected machine profile.

## Energy knob

The workflow operates in normalized `delta = dp/p`. For the IRFEL modulator
actuator, the adapter converts between delta and voltage with the active
`calibration.actuator_per_delta` in kV per unit `dp/p`. Every write uses the
resolved `voltage_set` channel and verifies the independent
`voltage_readback`. The machine profile intentionally contains no default
calibration, so quantitative dispersion measurement remains blocked until a
validated calibration is activated in the GUI.

## Quadrupole knobs

Each quadrupole mapping selects `control: current` or `control: k1`. Configured
knob values are cumulative offsets in that actuator's units from the first
machine snapshot. In current mode the adapter writes `current_set` and verifies
`current_readback`; in K1 mode it writes `K1_set`/`K1` and verifies
`K1_readback` or the K1 setpoint fallback. The current IRFEL configuration uses
K1 mode for both VM and real backends, so empirical response measurement,
recommendations, and model snapshots all use `1/m^2`. The current real mapping
has no independent `K1_readback`; write verification therefore reads the same
K1 process-variable record used for the setpoint.

If one device in a multi-device knob write fails, already attempted devices are
written back to their pre-operation values. The raised error includes any
rollback failure.

## Snapshot and restore

A snapshot stores the normalized energy coordinate, physical quadrupole
actuator values, and logical knob values. Restore attempts both energy and quadrupole
restoration and reports every failed subsystem. All restored setpoints are
read back within the configured timeout and tolerance.

Exceptions, aborts, failed safety checks, rank-deficient response matrices, and
corrections that miss the final acceptance threshold all restore the initial
snapshot. Exception reporting does not perform another dispersion measurement
after restoration.

The current IRFEL correction mapping uses quadrupole K1 PVs and has an
independent modulator-voltage readback at `IRFEL:modulator1:HV:ai`.

## Advanced defaults

Normal site configuration keeps `ca_timeout`,
`energy_knob_readback_tolerance`, and `quadrupole_readback_tolerance`. The
energy knob tolerance uses actuator units, which are kV for the IRFEL
modulator-voltage knob. Write timeout defaults to the CA timeout;
readback verification waits up to 2 s and polls every 0.05 s. The polling
interval and timeout remain optional advanced overrides. When quadrupole
control changes from current to K1, its configured tolerance must also change
from current units to K1 units.
