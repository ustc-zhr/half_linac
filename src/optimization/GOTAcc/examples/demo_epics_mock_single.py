from __future__ import annotations

import numpy as np

from GOTAcc.src.gotacc.algorithms.single_objective.bo import BOOptimizer


class MockSingleMachine:
    """
    A lightweight stand-in for Obj_EpicsIoc.

    It mimics the small subset of methods used by the single-objective runner:
    - init_knob_value()
    - evaluate_func(x)
    - set_best()
    - restore_initial()
    """

    def __init__(self):
        self._initial = np.array([0.0, 0.0, 0.0], dtype=float)
        self.history_x: list[np.ndarray] = []
        self.history_y: list[float] = []

    def init_knob_value(self) -> np.ndarray:
        return self._initial.copy()

    def evaluate_func(self, x: np.ndarray) -> float:
        x = np.asarray(x, dtype=float)
        # A smooth mock objective: maximize near [0.5, -0.3, 0.2]
        y = -float(np.sum((x - np.array([0.5, -0.3, 0.2])) ** 2))
        self.history_x.append(x.copy())
        self.history_y.append(y)
        print(f"[mock] x={x}, y={y:.6f}")
        return y

    def set_best(self):
        if not self.history_y:
            return None
        idx = int(np.argmax(self.history_y))
        best_x = self.history_x[idx]
        best_y = self.history_y[idx]
        print(f"[mock] set_best -> x={best_x}, y={best_y:.6f}")
        return best_x, best_y

    def restore_initial(self):
        print(f"[mock] restore_initial -> {self._initial}")
        return self._initial.copy()


def main():
    machine = MockSingleMachine()
    ini = machine.init_knob_value()
    relative_bounds = np.array(
        [
            [-1.0, 1.0],
            [-1.0, 1.0],
            [-1.0, 1.0],
        ],
        dtype=float,
    )
    bounds = np.column_stack([ini + relative_bounds[:, 0], ini + relative_bounds[:, 1]])

    opt = BOOptimizer(
        func=machine.evaluate_func,
        bounds=bounds,
        kernel_type="matern",
        gp_restarts=3,
        acq="ucb",
        acq_para=2.0,
        acq_para_kwargs={"beta_strategy": "inv_decay", "beta_lam": 0.01},
        acq_optimizer="optimize_acqf",
        acq_opt_kwargs={"num_restarts": 8, "raw_samples": 64, "n_candidates": 512},
        n_init=4,
        n_iter=8,
        random_state=7,
        verbose=True,
    )

    opt.optimize()
    opt.save_history()
    opt.plot_convergence()
    machine.set_best()


if __name__ == "__main__":
    main()