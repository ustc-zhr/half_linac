from __future__ import annotations

import numpy as np

from GOTAcc.src.gotacc.algorithms.single_objective.bo import BOOptimizer


def sphere_objective(x: np.ndarray) -> float:
    """
    Simple toy objective for smoke testing.

    Here we write it as a maximization problem:
        max  -sum((x - 0.2)^2)

    If your BO implementation assumes minimization internally,
    flip the sign here.
    """
    x = np.asarray(x, dtype=float)
    return float(-np.sum((x - 0.2) ** 2))


def main():
    dim = 3
    bounds = np.tile(np.array([[-2.0, 2.0]], dtype=float), (dim, 1))

    opt = BOOptimizer(
        func=sphere_objective,
        bounds=bounds,
        kernel_type="matern",
        gp_restarts=3,
        acq="ucb",
        acq_para=2.0,
        acq_para_kwargs={"beta_strategy": "inv_decay", "beta_lam": 0.01},
        acq_optimizer="optimize_acqf",
        acq_opt_kwargs={"num_restarts": 8, "raw_samples": 128, "n_candidates": 1024},
        n_init=5,
        n_iter=30,
        random_state=42,
        verbose=True,
    )

    opt.optimize()

    # Optional 
    opt.save_history()
    opt.plot_convergence()


if __name__ == "__main__":
    main()