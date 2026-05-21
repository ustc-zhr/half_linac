# GOTAcc

**GOTAcc** stands for **General Optimization Toolkit for Accelerator Applications**.

GOTAcc is a Python toolkit for accelerator optimization workflows. The current
codebase is centered on a unified `TaskConfig` pipeline that can drive offline
objective functions, EPICS-based online tuning, command-line execution, and a
PyQt5 desktop GUI.

## Current Capabilities

- Single-objective optimizers: BO, ConsBO, TuRBO, RCDS
- Multi-objective optimizers: MOBO, ConsMOBO, MGGPO, ConsMGGPO, MOPSO, NSGA-II
- Output-space constrained optimization through ConsBO, ConsMOBO, and ConsMGGPO
- Backend abstraction for offline callables and online EPICS evaluation
- Config loading from Python module paths, Python files, and YAML files
- PyQt5 GUI shell for task building, machine mapping, run monitoring, and result inspection

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
gotacc-run --config gotacc.configs.py_cfg.para_half
gotacc-gui
```

The `--config` argument accepts:

- Python module paths, for example `gotacc.configs.py_cfg.para_irfel`
- Python files, for example `src/gotacc/configs/py_cfg/para_irfel.py`
- YAML files, for example `src/gotacc/configs/yaml_cfg/irfel_bo.yaml`

Module execution is also supported:

```bash
python -m gotacc.runners.run_cli --config gotacc.configs.py_cfg.para_half
python -m gotacc.gui.main
python -m gotacc.runners.run_debug
```

If you use the local Conda environment used during development, activate it first:

```bash
conda activate gotacc_env
python -m gotacc.gui.main
```

## Optimizer Names

Supported task config optimizer names include:

```text
bo
consbo
turbo
rcds
mobo
consmobo
mggpo
consmggpo
mopso
nsga2
```

Aliases such as `constrained_bo`, `constrained_mobo`, and
`constrained_mggpo` are also accepted by the runner.

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

This convention is shared by `ConsBO`, `ConsMOBO`, and `ConsMGGPO`.
`ConsMGGPO` uses the same constrained objective interface while retaining the
population-based MG-GPO search controls such as `pop_size`, `evals_per_gen`,
`n_generations`, and `acq_mode`.

For online EPICS tasks, constrained optimizers call
`backend.evaluate_with_constraints()`. In the GUI, add constraint rows in Task
Builder and add matching `constraint` rows in Machine Setup -> PV Mapping. The
GUI will pass constraint PVs and `constraint_bounds` into the backend and
optimizer automatically.

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
│     ├─ configs/
│     │  ├─ py_cfg/
│     │  ├─ yaml_cfg/
│     │  └─ pv_lists/
│     ├─ gui/
│     ├─ interfaces/
│     ├─ runners/
│     ├─ utils/
│     └─ version.py
├─ examples/
├─ tests/
└─ docs/
```

## Included Examples

- `examples/demo_single_bo_sphere.py`
- `examples/demo_multi_mobo_zdt1.py`
- `examples/demo_epics_mock_single.py`
- GUI template: `EPICS / ConsMGGPO` for constrained multi-objective online setup

## Notes

- The package version is sourced from `gotacc.version.__version__`.
- GUI runtime writes theme and matplotlib cache files under `.cache/`.
- Online workflows require a reachable EPICS environment and `pyepics`.
- Constrained online workflows require objective PV mappings plus matching
  constraint PV mappings.
