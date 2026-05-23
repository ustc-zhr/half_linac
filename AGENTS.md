# AGENTS.md

## Scope
- This file applies to the whole repository.
- More specific `AGENTS.md` files inside subdirectories override these rules for their scope.

## Project Map
- `src/apps/`: PyQt operator GUIs such as the Control Room app (`launcher/`), orbit correction, BBA, beam monitor, jitter, and energy spectrum.
- `src/shared/`: shared runtime helpers that are not owned by a single app.
- `src/optimization/`: optimization launcher entrypoints plus the vendored `GOTAcc/` package used for optimization workflows in this repo.
- `src/softIOC/`: EPICS IOC manager, PV server, templates, and IOC boot files.
- `src/virtual_machine/`: elegant-based virtual machine and lattice translators.
- `scripts/`: repo-local helper scripts for environment setup, checks, and common entrypoints.

## Working Rules
- Default to the smallest safe change.
- Preserve unrelated local changes in the worktree.
- Prefer repo-relative scripts under `scripts/` instead of absolute home-directory paths.
- Prefer `half_linac.runtime_config` for shared runtime/config imports.
- Treat `src/optimization/GOTAcc/` and `src/apps/jitter_analysis/` as externally maintained integration code: prefer launcher, wrapper, config, or compatibility fixes around them, and avoid large functional changes inside those vendored subtrees unless the user explicitly asks.
- Keep architectural or workflow notes short in `AGENTS.md`; deeper explanations belong in `docs/` or `README.md`.
- Prefer simple and easy-to-maintain machine-profile design: default to dynamic loading by `kind`, and only add `plane` or minimal tags when physically necessary. Avoid fine-grained app-specific role taxonomies unless the simpler approach clearly fails. Do not duplicate selectable element lists in app workflow config when they can be derived from machine-native element types.

## Safety Rules
- Default to offline analysis and VM-oriented workflows.
- Do not switch codepaths toward the real machine or live PV operations unless the user explicitly asks.
- Treat runtime artifacts as non-source unless the task is specifically about them: logs, shell histories, cached bytecode, generated lattice files, and daily elegant outputs.
- If a change affects PV naming, IOC DB generation, or VM/IOC synchronization, explain the impact before editing.

## Generated Or Derived Files
- `src/softIOC/halflinac/db/halflinac.substitutions` is generated from VM JSON by `pv_server.gen_substitution_file()`.
- `src/softIOC/halflinac/iocBoot/ioctarget/envPaths` is generated from `src/softIOC/halflinac/configure/RELEASE` during IOC rebuilds.
- `src/virtual_machine/half_elegant/halflinac.json`, `esa.json`, `elegant/lattice.lte`, and `elegant/one.ele` are generated or refreshed during VM/IOC workflows.
- Many GUI `gui.py` files are generated from paired `.ui` files. Prefer behavior edits in the app entrypoints unless the task is specifically about generated UI code.

## Standard Commands
- `source scripts/setup.sh`: export repo-local environment variables for Python entrypoints.
- `bash scripts/check.sh`: run fast static checks without starting IOC, elegant, or GUI processes.
- `bash scripts/build_ioc.sh`: rebuild the IOC application after changing `configure/RELEASE` or `iocBoot/ioctarget/envPaths`.
- `bash scripts/runMe`: start the Control Room GUI with repo-local path setup.
- `bash scripts/start_vm.sh`: start the VM manager.
- `bash scripts/start_ioc_manager.sh`: start the Python IOC manager.

## Verification
- For Python-only edits, prefer `bash scripts/check.sh`.
- For focused subtree edits, run `python3 -m compileall` on the narrowest relevant directory.
- Do not start long-running IOC or elegant processes unless runtime verification is explicitly required.
- If full verification requires EPICS, elegant, or a GUI session, state what was checked locally and what remains manual.
