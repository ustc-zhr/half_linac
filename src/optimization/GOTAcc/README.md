# GOTAcc

**GOTAcc** stands for **General Optimization Toolkit for Accelerator Applications**.

GOTAcc is a Python toolkit for accelerator optimization workflows. The current
codebase is centered on a unified `TaskConfig` pipeline that can drive offline
objective functions, EPICS-based online tuning, command-line execution, and a
PyQt5 desktop GUI.

## Current Capabilities

- Single-objective optimizers: BO, ConsBO, TuRBO, RCDS, MGGPO-SO, ConsMGGPO-SO
- Multi-objective optimizers: MOBO, ConsMOBO, MGGPO, ConsMGGPO, MOPSO, NSGA-II
- Output-space constrained optimization through ConsBO, ConsMGGPO-SO, ConsMOBO,
  and ConsMGGPO
- Backend abstraction for offline callables and online EPICS evaluation
- Config loading from Python module paths, Python files, and YAML files
- PyQt5 GUI shell for task building, offline setup, machine mapping, run
  monitoring, and result inspection

## Installation

Core install:

```bash
pip install -e .
```

Optional extras:

```bash
pip install -e ".[yaml]"
pip install -e ".[gui]"
pip install -e ".[epics]"
pip install -e ".[full,dev]"
```

- `yaml`: enable YAML task config loading and YAML PV-library import
- `gui`: install the PyQt5-based GOTAcc Studio GUI
- `epics`: enable the online EPICS backend
- `full`: convenience extra for `yaml`, `gui`, and `epics`
- `dev`: local test and formatting tools

## Running GOTAcc

Installed entry points:

```bash
gotacc-run --config config/task_configs/python/para_half.py
gotacc-gui
```

The `--config` argument accepts:

- Python module paths, for example `my_package.tasks.para_irfel`
- Python files, for example `config/task_configs/python/para_irfel.py`
- YAML files, for example `config/task_configs/yaml/irfel_bo.yaml`

`src/gotacc/configs` is the configuration API package for loading, defining,
and validating `TaskConfig` objects. It is not a repository location for machine
task files; example task files live under `config/task_configs/`, and reusable
machine PV lists live under `config/pv_libraries/`.
GUI project drafts saved by `Save Project` live under `config/gui_projects/`.
Versioned Machine Profiles saved from Machine Setup live under
`config/machine_profiles/`.

Module execution is also supported:

```bash
python -m gotacc.runners.run_cli --config config/task_configs/python/para_half.py
python -m gotacc.gui.main
python examples/debug_irfel_runner.py
```

If you use the local Conda environment used during development, activate it first:

```bash
conda activate gotacc_env
python -m gotacc.gui.main
```

## GOTAcc Studio GUI

GOTAcc Studio is the PyQt5 GUI entry point for building tasks, checking online
machine mappings, running optimizers, and inspecting results:

```bash
gotacc-gui
```

The current GUI opens in a compact control-room style with dark mode enabled by
default. Use the top-right theme button to switch between dark and light modes,
and use the adjacent `Log` button to show or hide the bottom log panel.
The persistent status strip shows the current task, mode, algorithm, run phase,
and machine/backend state. Overview focuses on task readiness, the planned
evaluation budget, backend readiness, the latest run outcome, and recent
session activity rather than repeating those global fields.

The left-side quick actions are project-oriented:

- `New Task`: explicitly choose an Offline benchmark or Online EPICS task
- `Open Project` / `Save Project`: load or save the editable GUI project state

New-task defaults are mode-specific. A new Online EPICS task starts with empty
Variables, Objectives, Constraints, PV Mapping, and write links; load a Machine
Profile and use `Sync To Task` to create real signal rows. A new Offline task
starts as a runnable two-variable Rosenbrock benchmark with no placeholder
constraint. Switching the Mode field on an existing task never clears its rows.
Offline Task Builder tables provide explicit `Add Row` and `Remove Selected`
actions. Online tables hide `Add Row` because signal rows come from Machine
Profile/PV Mapping sync; `Remove Selected` remains available and a resulting
selection mismatch is reported in Machine Setup. Empty-state guidance is
displayed outside the serialized table data.

The Configure footer opens `Preview Task` for the normalized runnable
configuration. `Export TaskConfig` is available from that preview and writes a
standard YAML file for the runner, while project files retain the GUI editing
state.

Configure pages are mode-specific: `Machine Setup` is shown for Online EPICS
tasks, while `Offline Setup` is shown for Offline tasks. Machine Setup uses a
read-only EPICS PV check flow for connectivity verification; real online runs
still require a reachable EPICS environment and the `epics` extra.

## Machine Profiles

Machine Setup can save and open independent, versioned Machine Profile JSON
files. A profile owns the EPICS connection settings, safeguards, PV Mapping,
write links, policy bindings, and machine-scoped custom presets. Profile files
use the `gotacc.machine_profile` schema with an explicit `profile_id` and
version; unversioned or unsupported profile formats are rejected instead of
being guessed or silently converted.

Opening a profile changes Machine Setup only. `Sync To Task` shows the exact
knob, objective, and constraint names that will be added or removed and requires
explicit confirmation before changing Task Builder. Matching task rows preserve
their bounds, initial values, directions, sampling, and math settings. GUI
projects retain the selected profile reference together with a frozen machine
configuration snapshot, so a later profile-file edit cannot silently change a
prepared or running task.

For Online EPICS tasks, `Sync To Task` merges PV Mapping rows into Task Builder
by role and name. Existing bounds, initial values, and objective settings are
preserved; new knobs require explicit task setup, and task rows absent from the
current Mapping are removed. `Undo Sync` restores the previous rows. PV Mapping
changes invalidate the previous PV Check, which must match the current task
before an online run can start. The compact Mapping status distinguishes no
selected signals, a changed selection that needs sync, rows already synced to
Task Builder, incomplete knob setup, and configuration issues. `Sync To Task`
is enabled only when the selected role/name set actually differs from Task
Builder and the Mapping has no blocking issue.

Task Builder `Bounds` generates a row-by-row preview before enabling
`Apply Bounds`. Applying uses the previewed values without reading the machine
again; changing any bounds option invalidates the previous preview.

Every Online Start requires explicit operator authorization. Post-run machine
writes (`Restore Initial`, `Set Best`, and selected Pareto points) use the frozen
run-task snapshot, require confirmation, and are blocked when the current task
no longer matches that snapshot. Run controls intentionally provide `Start` and
`Stop` without Pause/Resume; Online abort handling follows the task's configured
restore-on-abort policy.

Preview and validation in the GUI do not create runtime output directories.
Runtime directories under `save/` are created only when starting a run or when
explicitly exporting a runnable task config.

## Optimizer Names

Supported task config optimizer names include:

```text
bo
consbo
turbo
rcds
mggpo_so
consmggpo_so
mobo
consmobo
mggpo
consmggpo
mopso
nsga2
```

Aliases such as `constrained_bo`, `constrained_mobo`, and
`constrained_mggpo` are also accepted by the runner. Single-objective MG-GPO
aliases include `smggpo`, `single_objective_mggpo`, and
`single_objective_consmggpo`.

## Output Constraints

Constrained optimizers use output-space constraints. The objective callable must
return both objective values and raw constraint values:

```python
def objective_with_constraints(X):
    # X shape: (n_samples, dim)
    objectives = ...
    constraints = ...
    return objectives, constraints
```

Constraint feasibility is configured with `constraint_bounds`:

```python
constraint_bounds = [
    (None, 0.8),  # c0 <= 0.8
    (0.2, None),  # c1 >= 0.2
]
```

This convention is shared by `ConsBO`, `ConsMGGPO-SO`, `ConsMOBO`, and
`ConsMGGPO`. `ConsMGGPO-SO` and `ConsMGGPO` use the same constrained objective
interface while retaining the population-based MG-GPO search controls such as
`pop_size`, `evals_per_gen`, `n_generations`, and `acq_mode`.

For online EPICS tasks, constrained optimizers call
`backend.evaluate_with_constraints()`. In the GUI, add constraint rows in Task
Builder and add matching `constraint` rows in Machine Setup -> PV Mapping. The
GUI will pass constraint PVs and `constraint_bounds` into the backend and
optimizer automatically. EPICS tasks can also define constraint policies such as
`bpm_guard` / `bpm_zero_guard` to replace all-zero BPM constraint samples with a
sentinel value derived from the configured constraint bounds.

## Policies

Machine Setup -> PV Mapping is the primary assignment point for objective and
constraint policies. The page uses a compact machine-signal list with a selected
signal detail panel. Role, name, PV, readback, group, and note are read-only
definitions from the selected PV library; the Mapping page changes the selected
signal set and manages policies rather than editing the library itself. The
first `Add Policy` action opens a target-aware Policy Template chooser with
plain-language behavior descriptions. Built-in and machine-specific templates
use their validated defaults directly; only `Custom Policy` opens the structured
Policy Editor. Once policies exist, `Manage N Policies` provides editing, enable/
disable, removal, additional assignment, and template saving. The Policy Editor
locks the target to that mapping row and edits conditions, match mode, and
action without requiring JSON. Its selectors use plain-language metric,
comparison, and action labels, while the saved configuration keeps the stable
registry vocabulary. A live behavior sentence and inline validation explain the
result before it is saved. Assigned templates open in a read-only Policy view;
`Customize Policy` explicitly creates an editable copy for that PV, and
cancelling customization leaves the template binding unchanged. Custom Policy
parameters include threshold, tolerance, and constraint-bound guidance. Saving
a Custom Policy as a reusable template remains an Advanced action. Machine
Setup -> Policies -> Templates shows the registry-backed catalog of reusable
policies. Machine projects store assignments
in the canonical `machine.policy_bindings` model; when a task is built, the GUI
compiles each stable target name to the backend's objective/constraint policy
list and current `target_col`. The declarative
`sample_guard` policy lets online tasks describe common signal-quality rules
without executing user-provided code.
Built-in templates provide the former FEL energy, zero-objective, and BPM
zero-signal behavior; bindings expand those templates to the common
`sample_guard` policy. Existing GUI projects with legacy objective/constraint
policy rows, and backend configs that name `fel_energy_guard`,
`zero_guard`, or `bpm_guard` directly remain supported. For example, an
objective binding can target the stable Task Builder objective name:

Machine-specific Policy Templates can be created from any existing binding with
`Save as Template`. They are stored in `machine.policy_presets`, appear alongside
built-in templates in the Policy Editor, and can be reused by other compatible PV
Mapping rows. Renaming preserves references through a stable internal preset ID.
Deleting a template preserves each assigned policy and converts those bindings to
standalone `Custom Policy` entries.

When one signal has multiple enabled policies, Policy Manager shows their
top-to-bottom execution order and exposes `Move Up` / `Move Down` only for that
case. The list order remains the canonical runtime order, so no extra priority
field is serialized. A triggered sample policy writes one concise before/after
message to the existing Run Events log without adding another diagnostics panel.

Policy validation stays in the normal workflow instead of adding another
always-visible editor panel. PV Mapping marks assigned policies as `Ready`,
`Issue`, or `Disabled`; issue tooltips and the existing Review Issues action
identify the affected signal and the required fix. Policy save, `Sync To Task`,
task validation, and run start share the same side-effect-free checks. In
particular, a constraint rule using `violate_bound` is not ready until its
matching Task Builder constraint defines a lower or upper bound. Quick Add
shows that setup requirement before the template is used without blocking the
operator from completing bounds after synchronization.

Deferred Policy ideas are tracked in [docs/TODO.md](docs/TODO.md).
The current offline/offscreen verification scope is recorded in
[docs/CONTROLLED_ACCEPTANCE.md](docs/CONTROLLED_ACCEPTANCE.md).

```python
"policy_bindings": [
    {
        "kind": "objective",
        "target": "fel_energy",
        "enabled": True,
        "preset": "fel_energy_guard",
        "policy": {
            "name": "sample_guard",
            "kwargs": {
                "target": "fel_energy",
                "conditions": [
                    {"metric": "mean_abs", "operator": "gt", "value": 1.0e6},
                    {"metric": "peak_to_peak", "operator": "lt", "value": 1.0e-6},
                ],
                "match": "any",
                "action": {"type": "replace", "value": 0.0},
            },
        },
    }
]
```

Supported metrics are `mean_abs`, `max_abs`, `peak_to_peak`, `mean`, `std`, and
`reduced`. Conditions use `gt`, `ge`, `lt`, `le`, `eq`, or `ne` and can be
combined with `any` or `all`. Objective actions support `replace` and
`add_offset`; constraint actions support `replace` and `violate_bound`.
Constraint bound violations are derived from the task's configured
`constraint_bounds`. A policy target is validated when the EPICS backend is
built, before evaluation can write machine setpoints. `target_col` remains
available for configurations that do not provide stable signal names.

## Repository Layout

```text
GOTAcc/
├─ CHANGELOG.md
├─ README.md
├─ pyproject.toml
├─ src/
│  └─ gotacc/
│     ├─ algorithms/
│     │  ├─ single_objective/
│     │  └─ multi_objective/
│     ├─ configs/        # Config API: loader, schema, validators
│     │  ├─ loader.py
│     │  ├─ schema.py
│     │  └─ validators.py
│     ├─ gui/
│     ├─ interfaces/
│     ├─ runners/
│     │  ├─ task_runner.py
│     │  └─ run_cli.py
│     ├─ utils/
│     └─ version.py
├─ examples/
│  ├─ demo_single_bo_ackley.py
│  ├─ demo_single_bo_sphere.py
│  ├─ demo_epics_mock_single.py
│  ├─ debug_irfel_runner.py
│  └─ demo_multi_mobo_zdt1.py
├─ config/
│  ├─ task_configs/
│  │  ├─ python/
│  │  └─ yaml/
│  ├─ gui_projects/
│  └─ pv_libraries/
├─ runs/
└─ tests/
```

## Included Examples And Configs

- `examples/demo_single_bo_sphere.py`
- `examples/demo_multi_mobo_zdt1.py`
- `examples/demo_epics_mock_single.py`
- `examples/debug_irfel_runner.py`
- `config/task_configs/python/para_half.py`
- `config/task_configs/python/para_irfel.py`
- `config/task_configs/yaml/irfel_bo.yaml`
- `config/pv_libraries/irfel_pvlist.json`
- GUI task builder support for `MGGPO-SO` and `ConsMGGPO-SO`

## Notes

- The package version is sourced from `gotacc.version.__version__`.
- GUI runtime may write local theme and matplotlib cache files under `.cache/`;
  this directory is ignored and should not be committed.
- GUI and CLI run outputs should go under `runs/`; this directory is ignored
  and should not be committed.
- Online workflows require a reachable EPICS environment and `pyepics`.
- Constrained online workflows require objective PV mappings plus matching
  constraint PV mappings.
