# AGENTS.md

## Scope
- Applies to `src/virtual_machine/` and its subdirectories.

## Structure
- `half_elegant/start_VM.py` watches JSON changes and refreshes elegant outputs.
- `half_elegant/mainVM.py` and `VMgui.py` drive the VM GUI flow.
- `elegant_parser.py` and `lattice_parser.py` are the primary translation logic between lattice descriptions and runtime files.

## Editing Rules
- Treat `halflinac.json`, `esa.json`, `elegant/lattice.lte`, `elegant/one.ele`, daily logs, and simulation outputs as generated/runtime files unless the task explicitly targets them.
- Prefer changing parser or orchestration code over hand-editing generated elegant files.
- Do not switch machine mode or live PV behavior without explicit user direction.

## Verification
- Use `python3 -m compileall src/virtual_machine`.
- Full runtime verification requires elegant and EPICS access and should be treated as manual unless requested.
