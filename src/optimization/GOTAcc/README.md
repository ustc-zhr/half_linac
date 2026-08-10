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

The left-side quick actions are project-oriented:

- `New Task`: create a new Online EPICS task by default; switch `Mode` in Task
  Builder when an Offline benchmark task is needed
- `Open Project` / `Save Project`: load or save the editable GUI project state
- `Export Task`: write a runnable `TaskConfig` YAML for the runner

Configure pages are mode-specific: `Machine Setup` is shown for Online EPICS
tasks, while `Offline Setup` is shown for Offline tasks. Machine Setup uses a
read-only EPICS PV check flow for connectivity verification; real online runs
still require a reachable EPICS environment and the `epics` extra.

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
- `config/pv_libraries/irfel.json`
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
