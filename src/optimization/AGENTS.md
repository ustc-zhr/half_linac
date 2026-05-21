# AGENTS.md

## Scope
- Applies to `src/optimization/` and its subdirectories unless a deeper `AGENTS.md` exists.

## Structure
- `mainOPT.py` is the `half_linac` bridge entrypoint that launches the vendored GOTAcc GUI.
- `GOTAcc/` is a vendored external project copied into this repo and primarily maintained outside `half_linac`.
- `configs/` is the preferred place for `half_linac`-owned wrappers, local task configs, and future integration-layer overrides.

## Editing Rules
- Prefer the smallest integration change in `mainOPT.py`, `configs/`, or local docs before editing GOTAcc internals.
- Keep `half_linac`-specific machine config and wrapper logic outside `GOTAcc/` when practical, so vendored updates stay easy to sync.
- Avoid deep functional changes inside `GOTAcc/` unless the task explicitly targets GOTAcc development in this repo.
- Treat optimizer logs, saved runs, cache files, copied scripts, and record snapshots as runtime artifacts unless the task explicitly targets them.

## Verification
- Use `python3 -m compileall src/optimization`.
- For bridge-only changes, a narrow import or compile check is enough.
- Online optimization behavior still requires manual EPICS or VM validation; state clearly what was not run.
