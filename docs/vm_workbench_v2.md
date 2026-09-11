# VM Workbench v2

The second version adds virtual magnet editing and one session baseline for both
HALF and IRFEL. It builds on v1 and keeps the existing beam-source, usedline,
segment and error configuration tools.

## Launch from an independent worktree

Activate the `half_linac` Python environment, change to the new worktree, and run:

```bash
source scripts/setup.sh
python src/virtual_machine/half_elegant/mainVM.py
```

Set `HALF_LINAC_MACHINE_ID=irfel` for IRFEL. The bootstrap/import bridge resolves
`half_linac` to this checkout even when its directory has a different name. It
rejects a process that has already loaded another checkout. Shell entrypoints
likewise prepend the local import bridge. `scripts/repo_python.py` is available
for tools/tests which manipulate `sys.path` themselves:

```bash
python scripts/repo_python.py -m unittest discover -s tests -p 'test_vm_*.py'
python scripts/smoke_gui_layouts.py virtual_machine virtual_machine_irfel
bash scripts/check.sh
```

A separate worktree isolates source and generated files; separate processes using
the same EPICS PV names still require separate CA server ports to run concurrently.
Runtime acceptance passed for IRFEL on loopback-only ports 17064/17065 and
HALF on ports 17074/17075, leaving the standard CA port untouched. Both tests
verified quadrupole/corrector changes, diagnostic publication and magnet restore,
and terminated the processes they started.

## Magnet editing

Select a quadrupole or corrector in Devices. The editor shows the live virtual PV
setting, model setting, pending value and configured limits. K1 uses m⁻²; Kick
uses mrad in the UI and rad in the VM PV. `Step` and +/− edit only the draft;
`Apply` performs the write. Drafts and custom steps are retained per magnet when
switching devices. `Discard` reloads the latest PV setting.

Selection recommends Twiss for quadrupoles and Orbit for correctors; subsequent
updates preserve the user's curve choice and the independent screen selector.
Repeated occurrences share one magnet PV. Read-only model details remain available.

All writes use `resolve_write_target(..., mode="vm")`, existing virtual softIOC
aliases and the existing PV → JSON callback. There is no direct magnet JSON write
or fallback to real-machine channels. Disconnected channels cannot be applied.
External changes invalidate a pending draft. Unconfigured limits are explicitly
labelled; finite-value validation still applies.

Transactions run off the Qt thread. The states are Writing PV, Waiting for Model,
Calculating and Applied. The PV write timeout is 2 s, model synchronization timeout
5 s, and calculation waits for its actual input version without a short timeout.
Configured VM tolerances take precedence; otherwise comparison uses rtol=1e-9 and
atol=1e-12 in internal units. A stale failure from another version does not fail a
new calculation. Session loss, external input changes and cancellation stop the
operation without automatic rollback or retry.

## Baseline and restore

`Set Baseline` captures only the latest successful result from the current VM
session after verifying that supported magnet PVs match its input. Result snapshots
now carry `input_state`, copied before GUI-specific sigma-output injection. Older
v1 result files remain viewable but cannot establish a baseline.

`Show Baseline` overlays dashed curves and compares Cx, Cy, σx and σy for the same
WATCH. The baseline remains compatible only when changes are confined to supported
magnet K1/KICK fields. Other input changes disable comparison/restoration until a
new baseline is captured. Machine configuration changes require restarting the
workbench so channel mappings/limits are reloaded. The baseline is session-local.

`Restore Magnets` previews changed magnets, current/target settings and units.
Confirmation triggers ordered PV writes with per-item checks and PV deduplication.
It restores neither beam source nor routing nor errors. Other configuration actions
are locked during an operation. Failure/cancellation stops remaining writes and
reports confirmed, incomplete and unexecuted items; already changed magnets are
not automatically rolled back. Completion requires the final input's simulation.

## Validation

Offline tests cover mapping, units, limits, missing KICK defaults, version and
session checks, external changes, synchronization timeout, restore interruption,
baseline compatibility, themes, draft preservation and independent diagnostics.
The existing v1 VM tests and GUI smokes remain part of the regression suite.
Short live acceptance uses an isolated virtual IOC, adjusts one quadrupole and
one corrector, checks actual Twiss/Orbit changes and diagnostic publication, and
restores both magnets before terminating owned processes. No real-machine channel
is used.

Beamline display uses an explicitly labelled schematic, with compressed long
 drifts and minimum spacing for thin devices. Physical positions and curve
markers remain unchanged. Scroll to zoom, drag to pan, click to select, and
hover for the occurrence name, type, physical position and length. `Fit Line`
restores the full route; `Zoom to Selection` shows the selected neighborhood.
Labels avoid overlap and cavity/arrow/break details appear as space permits.
Repeated names include occurrence numbers. Screen diagnostics remain independent.
