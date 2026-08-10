from __future__ import annotations

import numpy as np

from GOTAcc.src.gotacc.algorithms.single_objective.bo import BOOptimizer


def ackley_objective(x: np.ndarray) -> float:
    """
    Ackley function (standard minimization form) then negated for maximization.
    
    Standard Ackley: f(x) = -a * exp(-b * sqrt(1/d * sum(x_i^2))) 
                           - exp(1/d * sum(cos(c * x_i))) + a + exp(1)
    with a=20, b=0.2, c=2π.
    Global minimum at x = (0,...,0), f_min = 0.
    
    Here we return -f(x) so that maximizing this objective corresponds
    to minimizing the standard Ackley function.
    """
    x = np.asarray(x, dtype=float)
    d = len(x)
    a = 20.0
    b = 0.2
    c = 2.0 * np.pi
    
    sum_sq = np.sum(x ** 2)
    sum_cos = np.sum(np.cos(c * x))
    
    term1 = -a * np.exp(-b * np.sqrt(sum_sq / d))
    term2 = -np.exp(sum_cos / d)
    f = term1 + term2 + a + np.exp(1.0)
    
    # Return negative for maximization (BO internally minimizes by default)
    return float(-f)


def main():
    dim = 2
    # Ackley function is usually explored on [-32, 32] or [-5, 5],
    # but we keep the original bounds for consistency with the test setup.
    bounds = np.tile(np.array([[-2.0, 2.0]], dtype=float), (dim, 1))

    opt = BOOptimizer(
        func=ackley_objective,          # <-- changed to Ackley
        bounds=bounds,
        kernel_type="matern",
        gp_restarts=3,
        acq="ucb",
        acq_para=2.0,
        acq_para_kwargs={"beta_strategy": "inv_decay", "beta_lam": 0.01},
        acq_optimizer="optimize_acqf",
        acq_opt_kwargs={"num_restarts": 8, "raw_samples": 128, "n_candidates": 1024},
        n_init=5,
        n_iter=10,
        random_state=42,
        verbose=True,
    )

    opt.optimize()

    # Optional
    # opt.save_history()
    opt.plot_convergence()


if __name__ == "__main__":
    main()