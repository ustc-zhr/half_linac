# AGENTS.md

## Scope
- Applies to `src/apps/` and its subdirectories unless a deeper `AGENTS.md` exists.

## Structure
- Each app keeps runtime logic in `main.py` or `main*.py`.
- Paired `gui.py` files are generated from `.ui` files by PyQt tools.
- Shared plotting helpers usually live in `mplwidget.py`.

## Editing Rules
- Prefer changing behavior in the app entry script instead of large manual edits to generated `gui.py`.
- If a UI change requires editing generated Python, keep the paired `.ui` file in mind and avoid broad formatting churn.
- Keep subprocess launch behavior consistent with the launcher: `shell=False`, explicit working directories, and clean shutdown where possible.
- Cross-app helpers should live in `src/shared/` instead of `src/apps/` when they are not owned by one specific app.

## Runtime Notes
- Most apps assume the repository parent is on `PYTHONPATH`; use the scripts under `/scripts` to launch them.
- Some apps have both VM and real-machine modes. Default to VM-safe assumptions unless the task explicitly targets live operation.
- Text logs, cached data, copied files, and backup UI files under app directories are usually artifacts, not source of truth.

## Verification
- Use `python3 -m compileall src/apps`.
- GUI behavior and EPICS interactions usually require manual smoke testing.
