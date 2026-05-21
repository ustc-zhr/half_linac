# BoTorch-based Bayesian Optimization Optimizer
# author: Haoran Zhang
# date: 2025-11

import time
import os
import numpy as np
from scipy.stats import qmc
import matplotlib.pyplot as plt
from datetime import datetime
from typing import Optional, Tuple, Dict, Any, Callable

# pytorch / gpytorch / botorch imports
import torch
from torch import Tensor

import gpytorch
from gpytorch.mlls import ExactMarginalLogLikelihood, SumMarginalLogLikelihood
from gpytorch.kernels import ScaleKernel, RBFKernel, MaternKernel
from gpytorch.priors import GammaPrior
from gpytorch.constraints import Interval

from botorch.models import SingleTaskGP, ModelListGP
from botorch.fit import fit_gpytorch_mll
from botorch.acquisition import UpperConfidenceBound, LogExpectedImprovement, ProbabilityOfImprovement
from botorch.acquisition.analytic import LogConstrainedExpectedImprovement, LogProbabilityOfFeasibility
from botorch.optim import optimize_acqf
from botorch.models.transforms import Normalize, Standardize

# For reproducibility
torch.set_default_dtype(torch.float64)

class ConsBOOptimizer:
    def __init__(
        self, 
        func: Callable,
        bounds: np.ndarray,
        kernel_type: Optional[str] = "matern",
        gp_restarts: int = 5,
        acq: str = "ucb",
        acq_para: float = 3.0,
        acq_para_kwargs: Optional[Dict[str, Any]] = None,
        acq_optimizer: str = "optimize_acqf",
        acq_opt_kwargs: Optional[Dict[str, Any]] = None,
        n_init: int = 5,
        n_iter: int = 50,
        device: str = "cpu",
        dtype: Optional[torch.dtype] = None,
        random_state: int = 0,
        verbose: bool = True,
        constraint_bounds: Optional[list[Tuple[Optional[float], Optional[float]]]] = None,
    ):
        """
        Optimized BoTorch-based sequential single-point Bayesian optimization.

        Parameters
        ----------
        func : callable
            Objective function that accepts numpy array of shape (n_samples, dim) or (dim,)
            and returns scalar or 1D array. If `constraint_bounds` is provided,
            `func(x)` must return `(objective_value, constraint_values)`.
        bounds : np.ndarray (dim, 2)
            Lower and upper bounds per dimension.
        kernel_type : str, optional
            Kernel type: "rbf", "matern", "rbfwhite", "maternwhite"
        acq : str
            Acquisition function: "ucb", "ei", "pi". If `constraint_bounds`
            is provided, only "ei" is currently supported.
        acq_optimizer : str
            Acquisition optimizer: "random", "sobol", "lbfgs"
        acq_para : float
            For UCB this is beta0; for EI / PI it is currently unused.
        n_init, n_iter : int
            Number of initial and iteration samples
        device, dtype: torch device/dtype
        random_state : int
            Random seed for reproducibility
        verbose : bool
            Whether to print progress information
        constraint_bounds : list of (lower, upper), optional
            Output-space constraint bounds. Use `None` for an open side, e.g.
            `[(None, threshold)]` means constraint value must be <= threshold.
        """
        self.func = func
        self.bounds = np.asarray(bounds, dtype=float)
        self.dim = self.bounds.shape[0]
        self.verbose = verbose

        # Model configuration
        self.kernel_type = kernel_type.lower() if kernel_type else "matern"
        self.gp_restarts = gp_restarts

        # Acquisition configuration
        self.acq = acq.lower()
        self.acq_para = acq_para
        self.acq_para_kwargs = acq_para_kwargs or {}
        self.acq_optimizer = acq_optimizer.lower()
        self.acq_opt_kwargs = acq_opt_kwargs or {}

        # Optimization parameters
        self.n_init = max(n_init, 1)  # Ensure at least 1 initial point
        self.n_iter = n_iter

        # Random state and device setup
        self.random_state = int(random_state)
        self._setup_random_state()
        self.device = torch.device(device if torch.cuda.is_available() and device == "cuda" else "cpu")
        self.dtype = dtype if dtype is not None else torch.get_default_dtype()

        self.constraint_bounds = None
        if constraint_bounds:
            self.constraint_bounds = [(lb, ub) for lb, ub in constraint_bounds]
        self.has_constraints = self.constraint_bounds is not None
        self.n_constraints = len(self.constraint_bounds) if self.has_constraints else 0
        if self.has_constraints and self.acq != "ei":
            raise NotImplementedError("Constrained BO currently only supports acq='ei'.")

        # history containers (numpy)
        self.history_X = np.zeros((0, self.dim))
        self.history_Y = np.zeros((0, 1))
        self.history_C = np.zeros((0, self.n_constraints)) if self.has_constraints else None

        # model placeholder
        self.model = None # GP model
        self.mll = None # marginal log likelihood

    def _setup_random_state(self):
        """Setup random seeds for reproducibility."""
        np.random.seed(self.random_state)
        torch.manual_seed(self.random_state)
        if torch.cuda.is_available():
            torch.cuda.manual_seed(self.random_state)

    # --------------------
    # Tensor utilities
    # --------------------
    def _to_torch(self, X: np.ndarray) -> Tensor:
        return torch.tensor(X, device=self.device, dtype=self.dtype)

    def _from_torch(self, T: Tensor) -> np.ndarray:
        return T.detach().cpu().numpy()

    def _get_bounds_tensor(self) -> Tensor:
        """Get bounds as torch tensor in (2, dim) shape."""
        return self._to_torch(self.bounds.T)

    def _parse_function_output(self, output: Any) -> Tuple[float, Optional[np.ndarray]]:
        """Parse objective-only or objective-plus-constraints function output."""
        if not self.has_constraints:
            obj = np.asarray(output, dtype=float).reshape(-1)
            if obj.size != 1:
                raise ValueError("Unconstrained func(x) must return a scalar objective value.")
            return float(obj[0]), None

        if not isinstance(output, (tuple, list)) or len(output) != 2:
            raise ValueError(
                "When constraint_bounds is provided, func(x) must return "
                "(objective_value, constraint_values)."
            )

        obj_raw, cons_raw = output
        obj = np.asarray(obj_raw, dtype=float).reshape(-1)
        cons = np.asarray(cons_raw, dtype=float).reshape(-1)

        if obj.size != 1:
            raise ValueError("Objective output must be scalar.")
        if cons.size != self.n_constraints:
            raise ValueError(
                f"Expected {self.n_constraints} constraint values, got {cons.size}."
            )
        return float(obj[0]), cons.astype(float)

    def _evaluate_candidates(self, X: np.ndarray) -> Tuple[np.ndarray, Optional[np.ndarray]]:
        """Evaluate objective and optional constraints on candidate points."""
        if X.ndim == 1:
            X = X.reshape(1, -1)

        Y = np.zeros((X.shape[0], 1), dtype=float)
        C = np.zeros((X.shape[0], self.n_constraints), dtype=float) if self.has_constraints else None

        for i, x in enumerate(X):
            y_i, c_i = self._parse_function_output(self.func(x))
            Y[i, 0] = y_i
            if self.has_constraints:
                C[i, :] = c_i

        return Y, C

    def _get_constraint_dict(self) -> Dict[int, Tuple[Optional[float], Optional[float]]]:
        """Map constraint output indices for constrained analytic acquisitions."""
        return {i + 1: bounds for i, bounds in enumerate(self.constraint_bounds or [])}

    def _get_feasible_mask(self, C_np: Optional[np.ndarray] = None) -> np.ndarray:
        """Return a boolean mask of points satisfying all output constraints."""
        if not self.has_constraints:
            return np.ones(self.history_Y.shape[0], dtype=bool)

        C_np = self.history_C if C_np is None else np.asarray(C_np, dtype=float)
        mask = np.ones(C_np.shape[0], dtype=bool)
        for i, (lower, upper) in enumerate(self.constraint_bounds):
            if lower is not None:
                mask &= C_np[:, i] >= float(lower)
            if upper is not None:
                mask &= C_np[:, i] <= float(upper)
        return mask

    def _get_best_index(self, feasible_only: bool = True) -> Optional[int]:
        """Get the best observed index, optionally restricted to feasible points."""
        if self.history_Y.shape[0] == 0:
            return None

        if self.has_constraints and feasible_only:
            feasible_mask = self._get_feasible_mask()
            if not np.any(feasible_mask):
                return None
            feasible_indices = np.flatnonzero(feasible_mask)
            best_local_idx = int(np.argmax(self.history_Y[feasible_mask, 0]))
            return int(feasible_indices[best_local_idx])

        return int(np.argmax(self.history_Y[:, 0]))

    def _get_best_feasible_value(self) -> Optional[float]:
        """Get the best feasible objective value if one exists."""
        best_idx = self._get_best_index(feasible_only=True)
        if best_idx is None:
            return None
        return float(self.history_Y[best_idx, 0])

    # --------------------
    # initial design
    # --------------------
    def _initialize_samples(self) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]:
        """Generate initial samples using LHS or Sobol sequence."""
        if self.dim < 10: 
            # LHS works well for lower dimensions
            sampler = qmc.LatinHypercube(d=self.dim)
            sample = sampler.random(n=self.n_init)
            X = qmc.scale(sample, self.bounds[:, 0], self.bounds[:, 1])
            method_name = "LHS"
        else:
            # Use Sobol for higher dimensions
            engine = torch.quasirandom.SobolEngine(dimension=self.dim, scramble=True, seed=self.random_state)
            sample = engine.draw(self.n_init).numpy()
            X = qmc.scale(sample, self.bounds[:, 0], self.bounds[:, 1])
            method_name = "Sobol"
        
        # Evaluate objective / constraints
        Y, C = self._evaluate_candidates(X)

        if self.verbose:
            print(f"使用{method_name}采样生成{self.n_init}个{self.dim}维初始点")

        return X.astype(float), Y.astype(float), None if C is None else C.astype(float)
    
    def _evaluate_function(self, X: np.ndarray) -> np.ndarray:
        """Evaluate only the objective values for compatibility."""
        Y, _ = self._evaluate_candidates(X)
        return Y

    # --------------------
    # fit GP model
    # --------------------
    def _create_single_task_gp(self, train_x: Tensor, train_y: Tensor, bounds_torch: Tensor):
        """Create a SingleTaskGP with the configured kernel / likelihood."""
        if self.kernel_type is not None:
            if self.kernel_type in ["rbf", "rbfwhite"]:
                base_kernel = RBFKernel(
                    ard_num_dims=self.dim,
                    lengthscale_prior=GammaPrior(3.0, 6.0),
                    lengthscale_constraint=Interval(1e-3, 1e3),
                )
            elif self.kernel_type in ["matern", "maternwhite"]:
                base_kernel = MaternKernel(
                    nu=2.5,
                    ard_num_dims=self.dim,
                    lengthscale_prior=GammaPrior(3.0, 6.0),
                    lengthscale_constraint=Interval(1e-3, 1e3),
                )
            else:
                raise ValueError(f"Unknown kernel_type={self.kernel_type}")

            covar_module = ScaleKernel(
                base_kernel,
                outputscale_prior=GammaPrior(2.0, 0.15),
                outputscale_constraint=Interval(1e-3, 1e3),
            )

            if "white" in self.kernel_type:
                likelihood = gpytorch.likelihoods.GaussianLikelihood(
                    noise_prior=GammaPrior(1.1, 10.0),
                    noise_constraint=Interval(1e-6, 1e1),
                )
                return SingleTaskGP(
                    train_x,
                    train_y,
                    covar_module=covar_module,
                    likelihood=likelihood,
                    input_transform=Normalize(self.dim, bounds=bounds_torch),
                    outcome_transform=Standardize(1),
                ).to(self.device)

            return SingleTaskGP(
                train_x,
                train_y,
                covar_module=covar_module,
                input_transform=Normalize(self.dim, bounds=bounds_torch),
                outcome_transform=Standardize(1),
            ).to(self.device)

        return SingleTaskGP(
            train_x,
            train_y,
            input_transform=Normalize(self.dim, bounds=bounds_torch),
            outcome_transform=Standardize(1),
        ).to(self.device)

    def _fit_model(self, X_np: np.ndarray, Y_np: np.ndarray, C_np: Optional[np.ndarray] = None):
        train_x = self._to_torch(X_np)
        bounds_torch = self._get_bounds_tensor()

        if self.has_constraints:
            models = [self._create_single_task_gp(train_x, self._to_torch(Y_np), bounds_torch)]
            for i in range(self.n_constraints):
                models.append(
                    self._create_single_task_gp(
                        train_x,
                        self._to_torch(C_np[:, i:i+1]),
                        bounds_torch,
                    )
                )
            self.model = ModelListGP(*models)
            self.mll = SumMarginalLogLikelihood(self.model.likelihood, self.model)
        else:
            self.model = self._create_single_task_gp(train_x, self._to_torch(Y_np), bounds_torch)
            self.mll = ExactMarginalLogLikelihood(self.model.likelihood, self.model)

        self.model.train()
        fit_gpytorch_mll(
            self.mll,
            max_attempts=self.gp_restarts,
            pick_best_of_all_attempts=True,
        )
        self.model.eval()

    # --------------------
    # compute current beta according to strategy
    # --------------------
    def _beta_schedule(self, it, beta0=None):
        beta0 = beta0 if beta0 is not None else self.acq_para
        strategy = self.acq_para_kwargs.get("beta_strategy", "fixed")
        lam = self.acq_para_kwargs.get("beta_lam", 0.05)

        schedules = {
            "exp_decay": lambda: beta0 * np.exp(-lam * it),
            "inv_decay": lambda: beta0 / (1.0 + lam * it),
            "fixed": lambda: float(beta0)
        }
        
        return float(schedules.get(strategy, lambda: float(beta0))())

    # --------------------
    # acquisition builder
    # --------------------
    def _build_acquisition(self, acq_para: Optional[float] = None, best_f: Optional[float] = None) -> gpytorch.Module:
        """Build acquisition function."""
        if self.has_constraints:
            best_feasible = self._get_best_feasible_value()
            constraints = self._get_constraint_dict()

            if best_feasible is None:
                return LogProbabilityOfFeasibility(
                    model=self.model,
                    constraints=constraints,
                )

            return LogConstrainedExpectedImprovement(
                model=self.model,
                best_f=best_feasible,
                objective_index=0,
                constraints=constraints,
                maximize=True,
            )

        if self.acq == "ucb":
            beta_val = acq_para if acq_para is not None else float(self.acq_para)
            acq = UpperConfidenceBound(self.model, beta=beta_val)
        elif self.acq == "ei":
            # best_f is required for EI
            if best_f is None:
                # infer from training data
                best_f = float(self.model.train_targets.max())
            acq = LogExpectedImprovement(
                self.model,
                best_f=self._to_torch(np.array([best_f])),
                maximize=True,
            )
        elif self.acq == "pi":
            if best_f is None:
                best_f = float(self.model.train_targets.max())
            acq = ProbabilityOfImprovement(
                self.model,
                best_f=self._to_torch(np.array([best_f])),
                maximize=True,
            )
        else:
            raise ValueError(f"Unknown acquisition {self.acq}")
        return acq

    # --------------------
    # acquisition optimization (single point q=1)
    # --------------------
    def _optimize_acquisition(self, acq) -> Tensor:
        lb = self._to_torch(self.bounds[:, 0])
        ub = self._to_torch(self.bounds[:, 1])

        if self.acq_optimizer == "random":
            # sample many random candidates, score them, pick best
            ncand = self.acq_opt_kwargs.get("n_candidates", 5000)
            X_cand = torch.rand(ncand, self.dim, device=self.device, dtype=self.dtype)
            X_cand = lb + (ub - lb) * X_cand
            with torch.no_grad():
                vals = acq(X_cand.unsqueeze(1))  # returns (n_candidates, q=1)
                vals = vals.view(-1)
            best_idx = int(torch.argmax(vals).item())
            x_next = X_cand[best_idx:best_idx+1, :]
            return x_next

        elif self.acq_optimizer == "sobol":
            # use Sobol raw samples and pick best
            nraw = self.acq_opt_kwargs.get("n_candidates", 4096)
            engine = torch.quasirandom.SobolEngine(dimension=self.dim, scramble=True, seed=self.random_state + int(time.time()) % 10000)
            X_cand = engine.draw(nraw).to(device=self.device, dtype=self.dtype)
            X_cand = lb + (ub - lb) * X_cand
            with torch.no_grad(): # 临时禁用 PyTorch 的自动梯度计算
                vals = acq(X_cand.unsqueeze(1)).view(-1)
            best_idx = int(torch.argmax(vals).item())
            return X_cand[best_idx:best_idx+1, :] # 保持 [1, dim] 形状

        elif self.acq_optimizer == "optimize_acqf":
            # use botorch.optimize_acqf which does multi-start optimization
            num_restarts = int(self.acq_opt_kwargs.get("num_restarts", 10))
            raw_samples = int(self.acq_opt_kwargs.get("raw_samples", 512))
            # bounds as (2, d) tensor for optimize_acqf: lower then upper in rows
            bounds_t = torch.stack([lb, ub])
            # optimize_acqf returns (best_x), best_value
            x_opt, best_val = optimize_acqf(
                acq_function=acq,
                bounds=bounds_t,
                q=1,
                num_restarts=num_restarts,
                raw_samples=raw_samples,
                options=self.acq_opt_kwargs.get("options", None),
            )
            # x_opt shape (1, q, d) -> squeeze
            return x_opt.detach().view(1, self.dim)
        else:
            raise ValueError(f"Unknown acq_optimizer={self.acq_optimizer}")
        # 有待添加其他优化器...

    # --------------------
    # main optimize flow
    # --------------------
    def optimize(self):
        start_time = time.time()
        print(f"\n=== Running BO with BoTorch ===")
        print(f"Using device: {self.device}")
        print(f"Acquisition: {self.acq}, Optimizer: {self.acq_optimizer}")

        # 1) initial samples
        X_np, Y_np, C_np = self._initialize_samples()
        # store history
        self.history_X = X_np.copy()
        self.history_Y = Y_np.copy()
        if self.has_constraints:
            self.history_C = C_np.copy()

        for it in range(self.n_iter):
            t0 = time.time()

            # 2) fit GP model
            self._fit_model(self.history_X, self.history_Y, self.history_C)

            # Prepare acquisition parameters
            if self.acq == "ucb":
                acq_para = self._beta_schedule(it, beta0=self.acq_para)
            else:
                acq_para = self.acq_para
            best_f = float(self.history_Y.max()) if (self.acq in ["ei", "pi"] and not self.has_constraints) else None

            # 3) optimize acquisition -> get next x (torch tensor)
            acq = self._build_acquisition(acq_para=acq_para, best_f=best_f)
            x_next_t = self._optimize_acquisition(acq)

            # 4) Evaluate new point (function expects numpy)
            x_next_np = self._from_torch(x_next_t).reshape(-1)  # shape (d,)
            y_next, c_next = self._evaluate_candidates(x_next_np)

            # Update history
            self.history_X = np.vstack([self.history_X, x_next_np.reshape(1, -1)])
            self.history_Y = np.vstack([self.history_Y, y_next])
            if self.has_constraints:
                self.history_C = np.vstack([self.history_C, c_next])

            # 打印每一代进度
            t1 = time.time()
            if self.verbose:
                if self.has_constraints:
                    feasible = bool(self._get_feasible_mask(c_next)[0])
                    cons_str = np.array2string(c_next.ravel(), precision=6, separator=", ")
                    print(
                        f"iter {it+1:02d}: f(x)={float(y_next.ravel()[0]):.6f}, "
                        f"feasible={feasible}, c={cons_str}, time={(t1-t0):.2f}s"
                    )
                else:
                    print(f"iter {it+1:02d}: f(x)={float(y_next.ravel()[0]):.6f}, time={(t1-t0):.2f}s")
        # 打印总耗时
        if self.verbose:
                print(f'Total time: {time.time() - start_time:.2f} s')

    # --------------------
    # plotting/saving helpers
    # --------------------
    def plot_convergence(self, path: str = None):
        nrows = 3 if self.has_constraints else 2
        plt.figure(figsize=(12, 10 if self.has_constraints else 8))

        plt.subplot(nrows, 1, 1)
        plt.plot(self.history_Y.ravel(), 'o-', linewidth=1.5, label='objective value')
        if self.has_constraints:
            feasible_mask = self._get_feasible_mask()
            best_feasible = np.full(self.history_Y.shape[0], np.nan, dtype=float)
            current_best = -np.inf
            for i, y_val in enumerate(self.history_Y.ravel()):
                if feasible_mask[i]:
                    current_best = max(current_best, float(y_val))
                if np.isfinite(current_best):
                    best_feasible[i] = current_best
            plt.plot(best_feasible, 'r--', linewidth=1.5, label='best feasible value')
        else:
            best_values = np.maximum.accumulate(self.history_Y.ravel())
            plt.plot(best_values, 'r--', linewidth=1.5, label='best value')
        plt.xlabel('evaluations')
        plt.ylabel('Values')
        plt.title('Convergence curve')
        plt.legend()
        plt.grid(True)

        plt.subplot(nrows, 1, 2)
        for i in range(self.history_X.shape[1]):
            plt.plot(self.history_X[:, i], '.-', label=f'dim {i+1}')
        plt.xlabel('evaluations')
        plt.ylabel('Parameter values')
        plt.title('Parameter evolution')
        plt.legend()
        plt.grid(True)

        if self.has_constraints:
            plt.subplot(nrows, 1, 3)
            for i in range(self.n_constraints):
                plt.plot(self.history_C[:, i], '.-', linewidth=1.5, label=f'constraint {i+1}')
                lower, upper = self.constraint_bounds[i]
                if lower is not None:
                    plt.axhline(lower, linestyle='--', linewidth=1.2, color=f'C{i}', alpha=0.7)
                if upper is not None:
                    plt.axhline(upper, linestyle='--', linewidth=1.2, color=f'C{i}', alpha=0.7)
            plt.xlabel('evaluations')
            plt.ylabel('Constraint values')
            plt.title('Constraint evolution')
            plt.legend()
            plt.grid(True)

        if self.dim == 2:
            plt.figure(figsize=(6, 4))
            plt.scatter(self.history_X[:, 0], self.history_X[:, 1], c=range(len(self.history_X)))
            plt.colorbar(label='iteration')
            plt.xlabel('dim 1')
            plt.ylabel('dim 2')
            plt.title('Parameter space projection')

        # 保存图片（如果指定了路径）
        if path is None:
            timestamp = datetime.now().strftime("%Y%m%d%H%M")
            path = f"save/bo_{timestamp}.png"

        save_dir = os.path.dirname(path)
        if save_dir:
            os.makedirs(save_dir, exist_ok=True)

        plt.savefig(path)

        plt.tight_layout()
        plt.show()

    def save_history(self, path: str = None):
        if path is None:
            timestamp = datetime.now().strftime("%Y%m%d%H%M")
            path = f"save/bo_{timestamp}.dat"
        
        save_dir = os.path.dirname(path)
        if save_dir:
            os.makedirs(save_dir, exist_ok=True)

        if self.has_constraints:
            feasible_col = self._get_feasible_mask().astype(float).reshape(-1, 1)
            data = np.hstack([self.history_X, self.history_Y, self.history_C, feasible_col])
        else:
            data = np.hstack([self.history_X, self.history_Y])
        np.savetxt(path, data, fmt="%.6f")
        print(f"Saved history to {path}")

        # 保存元数据
        meta_path = path.replace('.dat', '_meta.txt')
        with open(meta_path, 'w') as f:
            f.write(f"bounds: {self.bounds}\n")
            f.write(f"kernel_type: {self.kernel_type}\n")
            f.write(f"gp_restarts: {self.gp_restarts}\n")
            f.write(f"acq: {self.acq}\n")
            f.write(f"acq_para: {self.acq_para}\n")
            f.write(f"acq_para_kwargs: {self.acq_para_kwargs}\n")
            f.write(f"acq_optimizer: {self.acq_optimizer}\n")
            f.write(f"acq_opt_kwargs: {self.acq_opt_kwargs}\n")
            f.write(f"n_init: {self.n_init}\n")
            f.write(f"n_iter: {self.n_iter}\n")
            f.write(f"constraint_bounds: {self.constraint_bounds}\n")

        # output best
        best_idx = self._get_best_index(feasible_only=self.has_constraints)
        if best_idx is None:
            print("No feasible point found.")
            return None, None

        x_best = self.history_X[best_idx]
        y_best = float(self.history_Y[best_idx, 0])
        if self.has_constraints:
            c_best = self.history_C[best_idx]
            print(f"Best feasible value: f({x_best}) = {y_best}, constraints = {c_best}")
        else:
            print(f"Best value: f({x_best}) = {y_best}")
        return x_best, y_best




# --------------
# Example usage
# --------------
if __name__ == "__main__":
    t0 = time.time()

    def constrained_demo(x):
        """
        Example constrained objective:
        - maximize a smooth 2D objective near (0.25, -0.15)
        - subject to two output constraints:
          c1(x) = x1 + x2 - 0.40 <= 0
          c2(x) = (x1 - 0.10)^2 + (x2 + 0.20)^2 - 0.60 <= 0
        """
        x = np.asarray(x, dtype=float).reshape(-1)
        obj = -((x[0] - 0.25) ** 2 + 0.7 * (x[1] + 0.15) ** 2)
        cons = np.array(
            [
                x[0] + x[1] - 0.40,
                (x[0] - 0.10) ** 2 + (x[1] + 0.20) ** 2 - 0.60,
            ],
            dtype=float,
        )
        return obj, cons

    bounds = np.array([
        [-1.0, 1.0],
        [-1.0, 1.0],
    ], dtype=float)

    opt = ConsBOOptimizer(
        func=constrained_demo,
        bounds=bounds,
        kernel_type="matern",# "rbf", "matern", "rbfwhite", "maternwhite"
        gp_restarts=5,
        acq="ei",# constrained mode currently supports only "ei"
        acq_para=2.0, # beta for ucb; currently unused for ei / pi
        acq_para_kwargs={"beta_strategy": "inv_decay", "beta_lam": 0.01}, # "exp_decay" "inv_decay" "stage" "fixed"
        acq_optimizer="optimize_acqf", # ['random', 'sobol', 'optimize_acqf']  optimize_acqf为botorch自带的多起点优化器(默认基于L-BFGS-B)
        acq_opt_kwargs={"num_restarts": 8, "raw_samples": 256, "n_candidates": 8192}, # only for 'random' and 'sobol': 'n_candidates'
        n_init=5,
        n_iter=12,
        random_state=120
        ,
        constraint_bounds=[
            (None, 0.0),  # c1(x) <= 0
            (None, 0.0),  # c2(x) <= 0
        ],
    )

    opt.optimize()

    # Results display
    opt.save_history()
    opt.plot_convergence()
    
