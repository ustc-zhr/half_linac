# Changelog

## 1.3.0 - 2026-06-29

- Redesigned GOTAcc Studio around a compact half_linac-style control-room
  shell, including dark/light theme switching, a horizontal workspace status
  strip, simplified log panel, and tighter Run-page monitoring
- Simplified GUI task entry points around `New Task`, `Open Project`,
  `Save Project`, and `Export Task`, with new tasks defaulting to Online EPICS
  while preserving Offline mode switching in Task Builder
- Simplified Machine Setup for online tasks with a read-only EPICS PV check
  flow, compact PV mapping actions, and clearer selected-row table styling
- Made GUI task preview and validation side-effect free by deferring runtime
  directory creation until run or explicit export
- Aligned GUI worker error handling with runner restore behavior by attempting
  initial-state restore after unexpected run errors when configured
- Removed tracked GUI runtime cache state and expanded GUI/offscreen tests for
  task config preview, worker restore behavior, and the redesigned shell
- Moved machine task configs, GUI project drafts, and PV libraries under the
  top-level `config/` directory, keeping `gotacc.configs` focused on the config
  loading and validation API
- Set the GUI default work directory to `runs/` and ignored that directory as
  local runtime output
- Removed the GUI Template Library workflow and its external demo template to
  keep project save/export semantics focused on project JSON and TaskConfig YAML
- Renamed the canonical runner implementation to
  `gotacc.runners.task_runner` while keeping `gotacc.runners.optimize` as a
  compatibility import path

## 1.2.0 - 2026-06-27

- Added single-objective MG-GPO optimizers through `MGGPO-SO` and
  `ConsMGGPO-SO`, including runner wiring, GUI selection, validation aliases,
  and package exports
- Added EPICS output-constraint policy support with BPM zero-value guard
  handling and constraint bounds propagation through task configs
- Expanded the GUI task builder with dynamic MGGPO-SO parameters, constraint
  policy rows, online/offline setup separation, and template-library access
- Normalized q-acquisition batch handling for MOBO-style optimizers through
  `q_batch_size`
- Added repository-level agent and pre-push release-file sync instructions

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
