import importlib

import pytest


CORE_MODULES = [
    "gotacc",
    "gotacc.configs.loader",
    "gotacc.configs.schema",
    "gotacc.configs.validators",
    "gotacc.interfaces.base",
    "gotacc.interfaces.factory",
    "gotacc.interfaces.epics",
    "gotacc.runners.task_runner",
    "gotacc.runners.optimize",
    "gotacc.runners.run_cli",
    "gotacc.gui.services.task_service",
]

TORCH_BACKED_MODULES = [
    "gotacc.algorithms.single_objective.bo",
    "gotacc.algorithms.single_objective.turbo",
    "gotacc.algorithms.single_objective.consbo",
    "gotacc.algorithms.single_objective.mggpo_so",
    "gotacc.algorithms.single_objective.consmggpo_so",
    "gotacc.algorithms.multi_objective.mobo",
    "gotacc.algorithms.multi_objective.consmobo",
    "gotacc.algorithms.multi_objective.mggpo",
    "gotacc.algorithms.multi_objective.consmggpo",
]


@pytest.mark.parametrize("module_name", CORE_MODULES)
def test_import_core_modules(module_name):
    importlib.import_module(module_name)


@pytest.mark.parametrize("module_name", TORCH_BACKED_MODULES)
def test_import_torch_backed_modules(module_name):
    pytest.importorskip("torch")
    pytest.importorskip("botorch")
    pytest.importorskip("gpytorch")
    importlib.import_module(module_name)
