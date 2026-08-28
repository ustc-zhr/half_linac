# GOTAcc 1.3.0 Controlled Acceptance

Date: 2026-08-27  
Branch: `policy-runtime-polish-phase14`  
Scope: GUI redesign, Machine Profile/PV Mapping workflow, online write guards,
and objective/constraint Policy workflow.

## Environment

- Python: `/home/zhanghaoran/anaconda3/envs/half_linac/bin/python`
- GUI platform: Qt offscreen
- EPICS: test doubles only; no IOC connection and no real `caput`

## Results

- Full repository suite: 89 passed.
- Controlled online-safety and Policy suite: 43 passed.
- Python source compilation: passed.
- CLI `--help` and package import/version check: passed (`1.3.0`).
- Local wheel build with pip/setuptools, without dependencies or build
  isolation: passed (`gotacc-1.3.0-py3-none-any.whl`).
- Offscreen `MainWindow` construction and final Policies/Templates navigation:
  passed.
- Bundled Python/YAML TaskConfig and PV-library loading: passed.

The controlled suite covers explicit Online Start/write authorization, frozen
run-task identity checks, cancellation without backend creation, restore paths,
Machine Profile version validation, named Policy target validation before any
write, FEL/BPM compatibility, Policy ordering controls, and Policy trigger event
routing.

## Observations

- Four full-suite warnings come from Matplotlib `tight_layout` in the reduced
  offscreen test window. They do not fail rendering or application tests.
- Matplotlib may use a temporary cache under `/tmp` when the user configuration
  directory is not writable in the controlled environment.

## Exclusions

- No real accelerator or production IOC was connected.
- No live PV read/write timing, network failure, or hardware interlock behavior
  was validated.
- Online safety is therefore not claimed from this acceptance alone.

## Conclusion

The branch is ready for operator review in a dedicated dry-run or virtual IOC
environment. A real-machine release still requires site-controlled acceptance
under the facility's authorization and restoration procedures.
