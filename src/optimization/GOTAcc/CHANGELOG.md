# Changelog

## 1.3.0 - 2026-08-27

- Redesigned GOTAcc Studio around a compact half_linac-style control-room
  shell, including dark/light theme switching, a horizontal workspace status
  strip, simplified log panel, and tighter Run-page monitoring
- Simplified GUI task entry points around `New Task`, `Open Project`,
  `Save Project`, and `Export TaskConfig`, with explicit Offline/Online task
  selection
- Simplified Machine Setup for online tasks with a read-only EPICS PV check
  flow, compact PV mapping actions, and clearer selected-row table styling
- Made PV Mapping sync preserve Task Builder parameters by role and name,
  remove rows absent from the current mapping with one-step undo, expose sync
  status, and reject ambiguous names or duplicate knob Setpoint PVs
- Bound read-only PV Check results to the current task mapping, invalidating
  stale checks after configuration changes and checking every required run PV
- Required explicit operator authorization for every Online Start and every
  post-run machine write, removing the optional write-confirmation setting
- Bound initial, best, and Pareto writeback values to a frozen run-task identity
  so changed PV mappings or task settings block stale-result writes
- Simplified run controls to Start and Stop, removing the incomplete GUI
  Pause/Resume workflow while preserving abort-and-restore handling
- Refined Task Builder, Run, Results, and Machine Setup layouts for clearer
  status hierarchy, aligned controls, and denser control-room use
- Reworked Bounds Tools around a row-by-row preview table and frozen apply
  plan, so applied bounds exactly match the reviewed source values
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
- Added a central policy registry used by backend construction, TaskConfig
  validation, and GUI policy discovery instead of duplicated policy whitelists
- Added declarative objective and constraint `sample_guard` policies with
  named signal targets, bounded condition/operator vocabularies, and
  pre-evaluation target validation
- Replaced raw JSON policy editing in Machine Setup with a structured Policy
  Editor and converted the FEL energy, zero-objective, and BPM guards into
  reusable presets that expand to `sample_guard`, while retaining legacy config
  compatibility
- Integrated objective and constraint policy assignment into individual PV
  Mapping rows, with stable-name target binding, multi-policy management, and a
  read-only Policy Templates library
- Redesigned PV Mapping as a compact signal list with a scrollable selected-row
  detail panel, policy-first actions, signal metadata, and a dedicated
  mapping-issue review action
- Replaced hidden objective/constraint policy tables with canonical
  `machine.policy_bindings`, including legacy project migration, stable target
  compilation through current PV Mapping order, and a registry-backed Policy
  Templates browser
- Added machine-scoped custom policy templates that can be saved from a binding,
  reused from the structured Policy Editor, renamed through stable IDs, and
  deleted without removing or changing existing bound policy behavior
- Added independent versioned Machine Profile files for mapping, safeguards,
  write links and policies; profile loading no longer changes Task Builder until
  an operator confirms the displayed synchronization diff
- Removed the retired hidden objective/constraint policy tabs and their legacy
  table-editor controller paths now that canonical policy bindings own the GUI
  workflow
- Replaced generic `x0`/`obj0`/`cons0` Online placeholders with mode-specific
  new-task initialization: Online tasks start empty for Machine Profile sync,
  while Offline tasks start as a runnable Rosenbrock benchmark without a fake
  constraint
- Added explicit Task Builder row actions and non-serialized empty-state hints,
  while preserving all table data when users merely switch the task mode
- Added shared, target-aware machine policy validation across Policy save,
  PV Mapping sync, task validation, and run start, with compact Ready/Issue/
  Disabled summaries and actionable constraint-bound diagnostics
- Made PV-library signal definitions read-only in PV Mapping and limited the
  workspace to signal selection, policy assignment, and task synchronization;
  sync status now distinguishes empty selection, changed selection, synced
  rows, incomplete knob setup, and actionable issues
- Hid manual `Add Row` actions from Online Task Builder tables while retaining
  them for Offline tasks, kept row removal available, and normalized the row
  toolbar buttons to compact fixed dimensions
- Added a preset-first Policy Quick Add flow with target/PV context,
  plain-language behavior descriptions, machine custom templates, and early
  constraint-bound guidance; empty Policy Manager dialogs and implicit FEL/BPM
  defaults were removed, while Custom Policy retains the structured editor
- Unified operator-facing terminology around Policy and Policy Template, and
  upgraded Policy Editor fields with readable labels, a live behavior summary,
  target/PV context, and inline validation while preserving registry/config keys
- Added an explicit template customization flow: assigned templates open
  read-only, `Customize Policy` creates a per-PV Custom Policy without mutating
  the template, parameter guidance is available in the editor, and reusable
  template saving is limited to Custom Policies under Advanced actions
- Finished the compact Policy workflow with visible list-based execution order,
  conditional `Move Up` / `Move Down` actions, concise trigger messages in the
  existing Run Events log, simplified Policies/Template navigation, and a
  documented deferred-work list instead of additional GUI complexity

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
