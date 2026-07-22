# EPICS Write And Restore Path

This document describes the adapter contract inherited from the standalone
implementation. The integrated IRFEL machine profile maps
`KLY1_CH3_PHASE.phase_set` to `IRFEL:IN-MW:KLY1:SET_PHASE` and blocks all
external writes at the application runtime layer until calibration and
commissioning are complete.

Real-machine writes require `backend.type: epics`, `backend.mode: write_enabled`,
a valid RF phase calibration, positive sampling/settle times, and successful
static and live preflights. The live preflight reads the phase setpoint,
quadrupole setpoints/readbacks, and every target BPM without writing. An
external CLI `--config` cannot grant EPICS write authority; that authority must
come from the selected machine profile.

## Energy knob

The workflow operates in normalized `delta = dp/p`. For a phase actuator, the
adapter converts between delta and phase with `calibration.phase_per_delta`.
Every write uses `phase_set` (or `set`) and verifies `phase_readback`,
`readback`, or the setpoint PV as a fallback.

## Quadrupole knobs

Each quadrupole mapping selects `control: current` or `control: k1`. Configured
knob values are cumulative offsets in that actuator's units from the first
machine snapshot. In current mode the adapter writes `current_set` and verifies
`current_readback`; in K1 mode it writes `K1_set`/`K1` and verifies
`K1_readback` or the K1 setpoint fallback. The current IRFEL configuration uses
current mode, so empirical response measurement does not require a K1/current
conversion.

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

The current IRFEL mapping has independent quadrupole current readbacks and an
independent RF phase readback at `IRFEL:IN-MW:KLY1:GET_CH3_PHASE`.

## Advanced defaults

Normal site configuration keeps `ca_timeout`,
`energy_knob_readback_tolerance`, and `quadrupole_readback_tolerance`. The
energy knob tolerance uses actuator units, which are degrees for the IRFEL RF
phase knob. Write timeout defaults to the CA timeout;
readback verification waits up to 2 s and polls every 0.05 s. The polling
interval and timeout remain optional advanced overrides. When quadrupole
control changes from current to K1, its configured tolerance must also change
from current units to K1 units.
