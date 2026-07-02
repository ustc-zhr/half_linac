import importlib

import numpy as np
import pytest

from test_function_single import setup_objective


def test_setup_objective_returns_callable_and_bounds():
    func, bounds = setup_objective("rosenbrock", dim=3)

    assert callable(func)
    assert bounds.shape == (3, 2)
    assert np.isclose(float(func([1, 1, 1])), 0.0)


def test_torch_backed_optimizers_are_importable_when_dependencies_exist():
    pytest.importorskip("torch")
    pytest.importorskip("botorch")
    pytest.importorskip("gpytorch")

    single_objective = importlib.import_module("gotacc.algorithms.single_objective")

    assert hasattr(single_objective, "BOOptimizer")
    assert hasattr(single_objective, "TuRBOOptimizer")
