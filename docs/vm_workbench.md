# VM Workbench

The HALF and IRFEL VM GUIs share an English observation workbench. Launch the
Virtual Accelerator from Control Room as before. Start softIOC, then the VM.
`Process Running` describes the IOC supervisor process; it is not a PV readiness
claim. `Ready` requires a completed Elegant calculation from this GUI session.

Select devices in the searchable list or beamline. The beamline supports mouse
wheel zoom and toolbar pan/home. Selection identifies a usedline occurrence,
including repeated elements. Device parameters are read-only. Beam source,
usedline, segment and error controls are in separate sidebar tabs. A segment
handoff continues to lock beam-source editing.

The center **Beam Parameters** plot offers Orbit (mm), beta functions (m),
dispersion (m), tracked RMS Beam Size (mm), Orbit Angle and RMS Divergence (mrad),
and the following parameters:

- Emittance (mm·mrad): normalized or geometric projected RMS X/Y emittance,
  with an optional removal of dispersion contributions. Defaults to normalized,
  with dispersion included.
- Kinetic Energy (MeV): electron kinetic energy evaluated at the mean momentum
  `pCentral * (1 + Cdelta)`, approximating mean kinetic energy for narrow spreads.
- RMS Bunch Length (ps): RMS bunch duration `St`.
- Relative Momentum Spread (%): `100 * Sdelta / (1 + Cdelta)`, normalized to
  local mean momentum; this is not an exact relative energy spread.
- Transmission (%): surviving macroparticles relative to the first recorded
  position, reflecting only losses represented by the simulation model.

All curves support baseline overlays. Existing `.cen`, `.sig`, and `.twi` files
are each loaded once per collection, with no extra tracking or particle analysis.
Changing the plot selection uses the stored snapshot. New parameters become
available after restarting the updated VM runtime and completing a calculation.

The independent WATCH selector shows coordinate histograms
and population RMS/centroids in mm. Missing, disabled, ambiguous repeated WATCH,
and stale output files have explicit unavailable messages. Twiss is never used
as a substitute for tracked RMS size.

Input changes mark the previous result out of date. A failed run retains the last
successful result, and a diagnostic publication failure is shown separately from
simulation success. Expand Show Log for subprocess output and failure details.
Closing the window stops only processes started by that window, asynchronously.

## Observation contract

`common/start_VM.py` hashes the canonical live runtime JSON and freezes each new
input into `.vm-workbench/input.json` next to that JSON. The frozen input enables
`run_setup.sigma = %s.sig`; the live configuration and PV mappings are unchanged.
Changes arriving during a calculation are coalesced into the next latest version.
A failed version is retried after a new input change or VM restart.

`.vm-workbench/status.json` is atomically replaced and includes session UUID,
process ID, calculation number, phase, input/result versions, last success UTC
time, elapsed seconds, error, and per-category publication success. The GUI
passes `HALF_VM_SESSION` to its child and never interprets another session as ready.

`.vm-workbench/result.json` contains a complete bounded plotting snapshot:
occurrence-indexed elements, named curves with units, and WATCH histograms and
statistics. Each snapshot includes the session, calculation and input version.
The GUI reads it on a worker thread and validates it against status before use.
These runtime artifacts and generated `.sig` outputs are ignored by Git.

## Verification

Run `python3 -m unittest discover -s tests -p 'test_vm_workbench*.py'` with the
repository's `half_linac` Python environment to include offscreen GUI checks.
Also run `python3 -m compileall src/virtual_machine`, `bash scripts/check.sh`, and
`python3 scripts/smoke_gui_layouts.py virtual_machine virtual_machine_irfel`.
No test starts an IOC, Elegant or a live PV operation. Runtime acceptance still
requires a manual VM session: adjust a beam setting, verify the pending/ready
transition and refreshed curves/screens, and check the existing consumer apps
receive their diagnostic PVs. Beam Size becomes available after a new successful
run produces sigma output.
