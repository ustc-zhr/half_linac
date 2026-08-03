# IRFEL Solenoid Centering Temporary Test

Commissioning status: `commissioned` as of 2026-08-01. The temporary bundle
instructions below are retained for maintenance and recovery; normal operation
should use the complete `half_linac` installation.

This note records the temporary deployment path for testing the
`solenoid_centering` app on the IRFEL control-room machine before the full
`half_linac` package is installed there.

## Scope

This is a short-term test workflow. It copies only the files needed by the
solenoid-centering app, the shared machine-profile loader, and the IRFEL
machine configuration.

The app can first run a read-only preflight check. The preflight reads PVs and
validates planned scan ranges, but does not write any PV.

## Required Safety Configuration

The checked-in IRFEL profile contains operations-confirmed solenoid physical
limits for `SS02`, `MS01`, and `LS01`. Each preset also uses a solenoid and
corrector readback tolerance of `0.01`, a readback timeout of `5.0 s`, and a
poll interval of `0.1 s`.

Before a field scan, confirm the configured `low` and `high` current limits
for `SS02`, `MS01`, and `LS01`, and review this object in each corresponding
preset in `configs/machines/irfel/apps/solenoid_centering.json`:

```json
"readback_verification": {
  "solenoid_readback_tolerance": 0.01,
  "corrector_readback_tolerance": 0.01,
  "readback_timeout_s": 5.0,
  "poll_interval_s": 0.1
}
```

The selected HCOR and VCOR must also retain valid physical limits and
`current_readback` channels. The GUI records actual device selections,
preflight values, limits, PV names, baseline data, and quality result in each
runtime archive. A recommendation is actionable only when its BPM score
improves at least 5% relative to the baseline trajectory.

## Required Python Packages

CLI preflight and scan require:

```text
numpy
pyepics
```

The GUI additionally requires:

```text
PyQt5
matplotlib
```

## Build The Temporary Bundle

Run this on the development machine:

```bash
cd /home/zhanghaoran/gitproj

tar czf /tmp/irfel_solenoid_centering_bundle.tgz \
  half_linac/repo_bootstrap.py \
  half_linac/src/shared/machine_profile \
  half_linac/src/apps/solenoid_centering \
  half_linac/configs/machines/irfel/machine.json \
  half_linac/configs/machines/irfel/control_backends \
  half_linac/configs/machines/irfel/apps/solenoid_centering.json
```

Copy the bundle to the IRFEL control-room machine:

```bash
scp /tmp/irfel_solenoid_centering_bundle.tgz USER@IRFEL_HOST:/tmp/
```

## Unpack On The IRFEL Machine

Run this on the control-room machine:

```bash
mkdir -p ~/test_half_linac
cd ~/test_half_linac
tar xzf /tmp/irfel_solenoid_centering_bundle.tgz
```

## Read-Only Preflight

Run this first. It should not write any PV.

```bash
cd ~/test_half_linac/half_linac

export HALF_LINAC_MACHINE_ID=irfel
export HALF_LINAC_CONTROL_BACKEND=real

python3 src/apps/solenoid_centering/scan.py \
  --machine irfel \
  --control-backend real \
  --preset ms01_centering \
  --preflight-only
```

Expected behavior:

- Reads solenoid setpoint/readback PVs.
- Reads selected HCOR/VCOR setpoints.
- Reads selected BPM x/y PVs.
- Checks planned solenoid and corrector scan ranges against `machine.json`
  limits.
- Prints `READY` when the current preset is safe to run.
- Prints `NOT READY` and exits nonzero if a PV cannot be read or a planned
  range exceeds configured limits, readback verification is missing, or a
  setpoint/readback pair is not within its configured tolerance.

## Run The GUI

After the read-only preflight succeeds, the GUI can be launched with:

```bash
cd ~/test_half_linac/half_linac

export HALF_LINAC_MACHINE_ID=irfel
export HALF_LINAC_CONTROL_BACKEND=real

python3 src/apps/solenoid_centering/main.py
```

## Iterations And Limits

`max_iters` is the maximum coordinate-descent iteration count. The application
does not multiply the corrector span by this count and reject the resulting
worst-case envelope. Instead, each iteration regenerates HCOR and VCOR
candidates around the current best value and removes candidates outside the
configured physical limits before any write.

The scan records and displays one of these termination reasons:

- configured maximum iterations completed;
- converged because neither HCOR nor VCOR selected a different candidate;
- stopped because a best corrector value reached a physical limit;
- stopped because an axis had fewer than two in-limit candidates.

Boundary-limited and insufficient-candidate results are archived but cannot be
applied. A clipped first iteration is shown as `CLIPPED TO LIMIT` in preflight.
The legacy `max_rounds` config key remains readable, but new configs use
`max_iters`.

Recommended first GUI workflow:

1. Select `MS01 Centering`.
2. Confirm or adjust `HCOR`, `VCOR`, and `BPM` in the Devices section.
3. Keep `Slope score` for the established default behavior, or select
   `Trajectory length` to reproduce the metric used by the later IRFEL field
   test.
4. Click `Check PVs`.
5. If the preflight reports `READY`, run the first small scan.
6. Review the live XY trajectories, all BPM-vs-solenoid scans, and best score
   before applying the recommended corrector values. Apply is unavailable if
   the result does not clear the 5% baseline quality gate.
7. Confirm the Apply dialog only after verifying its PV names, limits,
   original/target deltas, and quality conclusion. Restore revalidates state
   and verifies every rollback write.

## Notes

- Do not copy only `src/apps/solenoid_centering/`; the app also needs the
  shared machine-profile code and IRFEL config.
- `Solenoid PV` is resolved from the preset's `SS02`, `MS01`, or `LS01`
  machine element and the selected control backend.
- The merged IRFEL presets include the field-tested `SS02` workflow using
  `HIC01`, `VIC01`, and `BPM01`.
- `HCOR`, `VCOR`, and `BPM` are preset defaults in the GUI, but they remain
  operator-selectable before running preflight or scan.
- Standard scan settings are owned by the machine-profile presets. Operator
  overrides are temporary and the complete effective settings are stored in
  each scan-result archive.
- The default `MS01` and `SS02` presets retain the field-tested ranges:
  solenoid +/-5 with 5 points and correctors +/-2 with 5 points. Operators
  should reduce these in the GUI when the current machine state requires a
  narrower preflight envelope.
