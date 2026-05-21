# Changelog

## 1.1.0 - 2026-04-17

- Added online-ready constrained MG-GPO support through `ConsMGGPO`, using
  output constraint values and `constraint_bounds` consistent with ConsBO and
  ConsMOBO
- Wired ConsMGGPO into the runner, task service, engine worker, GUI algorithm
  selection, validators, templates, and package exports
- Standardized ConsMGGPO convergence plotting and history saving around the
  ConsMOBO-style artifacts, including feasible Pareto data and constraint
  summaries
- Added GUI support for inspecting Pareto-front solutions after multi-objective
  runs and writing a selected feasible Pareto point back to an online EPICS
  machine
- Updated documentation for optimizer names, output-space constraints, online
  constrained workflows, and the `gotacc_env` environment

## 1.0.0 - 2026-04-09

- Consolidated runnable entry points around `gotacc.runners.run_cli` and the
  PyQt5 GUI in `gotacc.gui.main`
- Standardized the documented repository layout on the current
  `algorithms / configs / interfaces / runners / gui` structure
- Aligned packaging metadata with the repository version, current console
  scripts, and optional extras for YAML, GUI, and EPICS support
- Refreshed ignore rules for runtime caches and generated output directories

## 0.1.0

- Initial public alpha-stage repository cleanup
- Renamed `algorithms/multi_objectivce/` to `algorithms/multi_objective/`
- Added package `__init__.py` files
- Simplified `pyproject.toml` to match current public repository layout
- Added minimal `examples/` and `tests/`
- Updated README to reflect actual public structure
