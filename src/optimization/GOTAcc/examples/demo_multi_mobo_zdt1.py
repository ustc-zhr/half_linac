from __future__ import annotations

import numpy as np

from GOTAcc.src.gotacc.algorithms.multi_objective.mobo import MOBOOptimizer


def zdt1(x: np.ndarray) -> np.ndarray:
    """
    Standard two-objective minimization benchmark.

    Returns:
        y = [f1, f2]
    """
    x = np.asarray(x, dtype=float)
    if x.ndim == 1:
        x = x.reshape(1, -1)

    n = x.shape[1]
    f1 = x[:, 0]
    g = 1.0 + 9.0 / (n - 1) * np.sum(x[:, 1:], axis=1)
    h = 1.0 - np.sqrt(f1 / g)
    f2 = g * h
    return np.column_stack([f1, f2])


def main():
    dim = 8
    bounds = np.tile(np.array([[0.0, 1.0]], dtype=float), (dim, 1))

    # Ref point should be worse than the expected Pareto front.
    ref_point = np.array([1.2, 6.0], dtype=float)

    opt = MOBOOptimizer(
        func=zdt1,
        bounds=bounds,
        n_objectives=2,
        ref_point=ref_point,
        kernel_type="maternwhite",
        gp_restarts=3,
        acq="qnehvi",
        acq_optimizer="optimize_acqf",
        acq_opt_kwargs={
            "num_restarts": 8,
            "raw_samples": 256,
            "qehvi_batch": 1,
        },
        n_init=12,
        n_iter=20,
        random_state=42,
        verbose=True,
    )

    opt.optimize()
    
    opt.save_history()
    opt.plot_convergence()


if __name__ == "__main__":
    main()