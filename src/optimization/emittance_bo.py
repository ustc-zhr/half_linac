"""Small, deterministic Bayesian-optimization adapter for emittance scans."""
from __future__ import annotations


def optimize_currents(evaluate, low, high, initial, max_evaluations, *,
                      initial_samples=None, exploration=0.01, random_seed=0):
    """Minimize an expensive scalar objective within independent bounds.

    The current machine point is always evaluated first.  A seeded Latin
    hypercube supplies the rest of the initial design, then a Matérn Gaussian
    process proposes one point at a time using expected improvement.
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
    budget = int(max_evaluations)
    if budget < 1:
        return

    width = high - low
    dimension = low.size
    initial_count = max(3, 2 * dimension + 1) if initial_samples is None else int(initial_samples)
    if initial_count < 3 or initial_count > budget:
        raise ValueError('BO initial samples must be between 3 and the evaluation budget.')
    exploration = float(exploration)
    if not np.isfinite(exploration) or exploration < 0:
        raise ValueError('BO exploration must be finite and non-negative.')
    random_seed = int(random_seed)
    if random_seed < 0:
        raise ValueError('BO random seed must be non-negative.')

    def physical(normalized):
        return low + width * np.clip(np.asarray(normalized, dtype=float), 0.0, 1.0)

    def sample(normalized):
        point = np.clip(np.asarray(normalized, dtype=float), 0.0, 1.0)
        value = float(evaluate(tuple(float(v) for v in physical(point))))
        if not np.isfinite(value):
            raise ValueError('Bayesian optimization received a non-finite objective value.')
        points.append(point)
        values.append(value)

    points, values = [], []
    sample((initial - low) / width)

    # Include the live point and a modest space-filling design before fitting.
    if initial_count > 1:
        design = qmc.LatinHypercube(d=dimension, seed=random_seed).random(initial_count - 1)
        for point in design:
            sample(point)

    candidate_engine = qmc.Sobol(d=dimension, scramble=True, seed=random_seed + 1)
    while len(values) < budget:
        x = np.asarray(points)
        y = np.asarray(values)
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
            model.fit(x, y)

        candidates = candidate_engine.random(2048)
        mean, sigma = model.predict(candidates, return_std=True)
        improvement = np.min(y) - mean - exploration
        z = np.divide(improvement, sigma, out=np.zeros_like(improvement), where=sigma > 1e-12)
        expected_improvement = improvement * norm.cdf(z) + sigma * norm.pdf(z)
        expected_improvement[sigma <= 1e-12] = 0.0
        # Do not spend a full scan on an already sampled current vector.
        separation = np.min(np.linalg.norm(candidates[:, None, :] - x[None, :, :], axis=2), axis=1)
        expected_improvement[separation < 1e-6] = -np.inf
        sample(candidates[int(np.argmax(expected_improvement))])
