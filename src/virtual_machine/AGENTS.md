# AGENTS.md

## Scope
- Applies to `src/virtual_machine/` and its subdirectories.

## Structure
- `common/mainVM.py` contains the shared VM GUI behavior.
- `common/start_VM.py` watches JSON changes and refreshes elegant outputs.
- `common/full_VM.py`, `common/simply_VM.py`, and `common/transfer_ESAline.py` contain shared VM usedline helper entrypoints.
- `common/err_gene_VM.py` contains the shared VM error helper implementation; machine wrappers may keep it disabled until their elegant control sections are ready.
- machine-specific `*/mainVM.py` files are thin entrypoint wrappers.
- machine-specific `*/start_VM.py`, `*/full_VM.py`, `*/simply_VM.py`, `*/transfer_ESAline.py`, and enabled `*/err_gene_VM.py` files are thin entrypoint wrappers.
- `half_elegant/VMgui.py` is the generated Qt UI used by the shared VM GUI.
- `elegant_parser.py` and `lattice_parser.py` are the primary translation logic between lattice descriptions and runtime files.

## Editing Rules
- Treat `halflinac.json`, `esa.json`, `elegant/lattice.lte`, `elegant/one.ele`, daily logs, and simulation outputs as generated/runtime files unless the task explicitly targets them.
- Prefer changing parser or orchestration code over hand-editing generated elegant files.
- Do not switch machine mode or live PV behavior without explicit user direction.

## Verification
- Use `python3 -m compileall src/virtual_machine`.
- Full runtime verification requires elegant and EPICS access and should be treated as manual unless requested.
