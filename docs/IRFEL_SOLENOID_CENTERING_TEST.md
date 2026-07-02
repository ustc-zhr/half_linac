# IRFEL Solenoid Centering Temporary Test

This note records the temporary deployment path for testing the
`solenoid_centering` app on the IRFEL control-room machine before the full
`half_linac` package is installed there.

## Scope

This is a short-term test workflow. It copies only the files needed by the
solenoid-centering app, the shared machine-profile loader, and the IRFEL
machine configuration.

The app can first run a read-only preflight check. The preflight reads PVs and
validates planned scan ranges, but does not write any PV.

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
  range exceeds configured limits.

## Run The GUI

After the read-only preflight succeeds, the GUI can be launched with:

```bash
cd ~/test_half_linac/half_linac

export HALF_LINAC_MACHINE_ID=irfel
export HALF_LINAC_CONTROL_BACKEND=real

python3 src/apps/solenoid_centering/main.py
```

Recommended first GUI workflow:

1. Select `MS01 Centering`.
2. Confirm or adjust `HCOR`, `VCOR`, and `BPM` in the Devices section.
3. Click `Check PVs`.
4. If the preflight reports `READY`, run the first small scan.
5. Review the score and BPM-vs-solenoid plots before applying the recommended
   corrector values.

## Notes

- Do not copy only `src/apps/solenoid_centering/`; the app also needs the
  shared machine-profile code and IRFEL config.
- `Solenoid PV` currently comes from the preset because the solenoid setpoint
  PV is directly declared in `configs/machines/irfel/apps/solenoid_centering.json`.
- `HCOR`, `VCOR`, and `BPM` are preset defaults in the GUI, but they remain
  operator-selectable before running preflight or scan.
- The default IRFEL presets use small scan ranges intended for initial online
  testing.
