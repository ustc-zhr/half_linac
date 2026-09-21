"""Lazy GOTAcc vector adapter; measurement and restoration belong to the app."""
from __future__ import annotations


def optimize_currents(evaluate, low, high, initial, max_evaluations, *, initial_step=0.2):
    import numpy as np
    from .GOTAcc.src.gotacc.algorithms.single_objective.rcds import RCDSOptimizer

    low, high, initial = (np.asarray(values, dtype=float) for values in (low, high, initial))
    if low.ndim != 1 or high.shape != low.shape or initial.shape != low.shape or not low.size:
        raise ValueError('Bounds and initial values must be equal-length vectors.')
    if not all(np.all(np.isfinite(values)) for values in (low, high, initial)):
        raise ValueError('Bounds and initial values must be finite.')
    if np.any(high <= low) or np.any(initial < low) or np.any(initial > high):
        raise ValueError('Every initial value must lie inside its increasing bounds.')
    optimizer = RCDSOptimizer(
        func=lambda x: evaluate(tuple(float(value) for value in x)),
        bounds=np.column_stack((low, high)),
        x0=(initial - low) / (high - low),
        step=float(initial_step),
        maximize=False, maxEval=max_evaluations, maxIt=max_evaluations, verbose=False,
    )
    optimizer.optimize()
