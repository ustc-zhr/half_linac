"""Small constrained Bayesian-optimization adapter for emittance scans."""
from __future__ import annotations


def optimize_currents(evaluate, low, high, initial, max_evaluations, *,
                      constraint_bounds, initial_samples=None, exploration=0.01,
                      random_seed=0, seed_observations=()):
    """Minimize a scalar objective subject to modeled output constraints.

    ``evaluate(point)`` returns ``(objective, constraint_values)``. Constraint
    bounds use ``(lower, upper)`` pairs with ``None`` for an open side. Seed
    observations train the models but do not consume the evaluation budget.
    """
    import warnings

    import numpy as np
    from scipy.stats import norm, qmc
    from sklearn.exceptions import ConvergenceWarning
    from sklearn.gaussian_process import GaussianProcessRegressor
    from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel

    low, high, initial = (np.asarray(values, dtype=float) for values in (low, high, initial))
    if low.ndim != 1 or high.shape != low.shape or initial.shape != low.shape or not low.size:
        raise ValueError('Bounds and initial values must be equal-length vectors.')
    if not all(np.all(np.isfinite(values)) for values in (low, high, initial)):
        raise ValueError('Bounds and initial values must be finite.')
    if np.any(high <= low) or np.any(initial < low) or np.any(initial > high):
        raise ValueError('Every initial value must lie inside its increasing bounds.')
    bounds = tuple(tuple(value) for value in constraint_bounds)
    if not bounds:
        raise ValueError('Constrained BO requires at least one output constraint.')
    for lower, upper in bounds:
        if lower is not None and not np.isfinite(float(lower)):
            raise ValueError('Constraint bounds must be finite or None.')
        if upper is not None and not np.isfinite(float(upper)):
            raise ValueError('Constraint bounds must be finite or None.')
        if lower is not None and upper is not None and float(lower) >= float(upper):
            raise ValueError('Constraint lower bounds must be less than upper bounds.')

    budget = int(max_evaluations)
    if budget < 1:
        return
    dimension = low.size
    width = high - low
    initial_count = max(3, 2 * dimension + 1) if initial_samples is None else int(initial_samples)
    if initial_count < 3 or initial_count > budget + 1:
        raise ValueError('CBO initial samples must fit the seed plus evaluation budget.')
    exploration = float(exploration)
    if not np.isfinite(exploration) or exploration < 0:
        raise ValueError('CBO exploration must be finite and non-negative.')
    random_seed = int(random_seed)
    if random_seed < 0:
        raise ValueError('CBO random seed must be non-negative.')

    def normalized(physical):
        return np.clip((np.asarray(physical, dtype=float) - low) / width, 0.0, 1.0)

    def physical(point):
        return low + width * np.clip(np.asarray(point, dtype=float), 0.0, 1.0)

    def parse_output(output):
        if not isinstance(output, (tuple, list)) or len(output) != 2:
            raise ValueError('CBO evaluation must return (objective, constraint_values).')
        objective = float(output[0])
        constraints = np.asarray(output[1], dtype=float).reshape(-1)
        if not np.isfinite(objective) or constraints.size != len(bounds) or not np.all(np.isfinite(constraints)):
            raise ValueError('CBO received non-finite or incorrectly shaped outputs.')
        return objective, constraints

    points, objectives, constraints = [], [], []
    for point, objective, constraint_values in seed_observations:
        point = np.asarray(point, dtype=float)
        if point.shape != low.shape or np.any(point < low) or np.any(point > high):
            raise ValueError('CBO seed observation is outside optimization bounds.')
        objective, constraint_values = parse_output((objective, constraint_values))
        points.append(normalized(point))
        objectives.append(objective)
        constraints.append(constraint_values)

    evaluations = 0

    def sample(point):
        nonlocal evaluations
        objective, constraint_values = parse_output(
            evaluate(tuple(float(value) for value in physical(point)))
        )
        points.append(np.clip(np.asarray(point, dtype=float), 0.0, 1.0))
        objectives.append(objective)
        constraints.append(constraint_values)
        evaluations += 1

    if not points:
        sample(normalized(initial))

    design_count = min(max(0, initial_count - len(points)), budget - evaluations)
    if design_count:
        design = qmc.LatinHypercube(d=dimension, seed=random_seed).random(design_count)
        for point in design:
            sample(point)

    def feasible_mask(values):
        values = np.asarray(values, dtype=float)
        mask = np.ones(values.shape[0], dtype=bool)
        for index, (lower, upper) in enumerate(bounds):
            if lower is not None:
                mask &= values[:, index] >= float(lower)
            if upper is not None:
                mask &= values[:, index] <= float(upper)
        return mask

    def fit(values):
        kernel = (
            ConstantKernel(1.0, (1e-3, 1e3))
            * Matern(length_scale=np.full(dimension, 0.25),
                     length_scale_bounds=(0.03, 3.0), nu=2.5)
            + WhiteKernel(noise_level=1e-4, noise_level_bounds=(1e-8, 0.2))
        )
        model = GaussianProcessRegressor(
            kernel=kernel, normalize_y=True, n_restarts_optimizer=1,
            random_state=random_seed, alpha=1e-10,
        )
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', ConvergenceWarning)
            model.fit(np.asarray(points), np.asarray(values))
        return model

    candidate_engine = qmc.Sobol(d=dimension, scramble=True, seed=random_seed + 1)
    while evaluations < budget:
        x = np.asarray(points)
        y = np.asarray(objectives)
        c = np.asarray(constraints)
        candidates = candidate_engine.random(2048)
        objective_model = fit(y)
        constraint_models = [fit(c[:, index]) for index in range(len(bounds))]

        probability = np.ones(candidates.shape[0])
        for model, (lower, upper) in zip(constraint_models, bounds):
            mean, sigma = model.predict(candidates, return_std=True)
            sigma = np.maximum(sigma, 1e-12)
            if lower is not None and upper is not None:
                probability *= np.maximum(
                    0.0,
                    norm.cdf((float(upper) - mean) / sigma)
                    - norm.cdf((float(lower) - mean) / sigma),
                )
            elif lower is not None:
                probability *= norm.cdf((mean - float(lower)) / sigma)
            elif upper is not None:
                probability *= norm.cdf((float(upper) - mean) / sigma)

        feasible = feasible_mask(c)
        if np.any(feasible):
            mean, sigma = objective_model.predict(candidates, return_std=True)
            best = np.min(y[feasible])
            improvement = best - mean - exploration
            z = np.divide(improvement, sigma, out=np.zeros_like(improvement), where=sigma > 1e-12)
            expected_improvement = improvement * norm.cdf(z) + sigma * norm.pdf(z)
            expected_improvement[sigma <= 1e-12] = 0.0
            acquisition = expected_improvement * probability
        else:
            acquisition = probability

        separation = np.min(np.linalg.norm(candidates[:, None, :] - x[None, :, :], axis=2), axis=1)
        acquisition[separation < 1e-6] = -np.inf
        sample(candidates[int(np.argmax(acquisition))])
