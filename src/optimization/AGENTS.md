# AGENTS.md

## Scope
- Applies to `src/optimization/` and its subdirectories.

## Structure
- `mainOPT.py` is the optimization GUI entrypoint.
- Algorithm-specific code lives under `BO/`, `RCDS/`, and `Rsimplex/`.
- `template.opt` and per-algorithm logs or record files support experiments and runtime sessions.

## Editing Rules
- Keep algorithm code, GUI code, and experiment artifacts separate.
- Treat logs, figures, copied test files, and persisted optimizer state as artifacts unless the task explicitly targets them.
- Prefer deterministic changes that preserve current parameter naming and PV interaction patterns.

## Verification
- Use `python3 -m compileall src/optimization`.
- Algorithm correctness usually needs offline benchmarks or VM/manual runtime checks; state that explicitly if not run.
