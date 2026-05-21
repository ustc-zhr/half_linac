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
from gpytorch.mlls import ExactMarginalLogLikelihood
from gpytorch.kernels import ScaleKernel, RBFKernel, MaternKernel
from gpytorch.priors import GammaPrior
from gpytorch.constraints import Interval

from botorch.models import SingleTaskGP
from botorch.fit import fit_gpytorch_mll
from botorch.acquisition import UpperConfidenceBound, ExpectedImprovement, ProbabilityOfImprovement
from botorch.optim import optimize_acqf
from botorch.models.transforms import Normalize, Standardize

# For reproducibility
torch.set_default_dtype(torch.float64)

class BOOptimizer:
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
        verbose: bool = True
    ):
        """
        Optimized BoTorch-based sequential single-point Bayesian optimization.

        Parameters
        ----------
        func : callable
            Objective function that accepts numpy array of shape (n_samples, dim) or (dim,)
            and returns scalar or 1D array.
        bounds : np.ndarray (dim, 2)
            Lower and upper bounds per dimension.
        kernel_type : str, optional
            Kernel type: "rbf", "matern", "rbfwhite", "maternwhite"
        acq : str
            Acquisition function: "ucb", "ei", "pi"
        acq_optimizer : str
            Acquisition optimizer: "random", "sobol", "lbfgs"
        acq_para : float
            For UCB this is beta0; for EI/PI this can be xi (exploration parameter)
        n_init, n_iter : int
            Number of initial and iteration samples
        device, dtype: torch device/dtype
        random_state : int
            Random seed for reproducibility
        verbose : bool
            Whether to print progress information
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

        # history containers (numpy)
        self.history_X = np.zeros((0, self.dim))
        self.history_Y = np.zeros((0, 1))

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

    # --------------------
    # initial design
    # --------------------
    def _initialize_samples(self) -> Tuple[np.ndarray, np.ndarray]:
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
        
        # Evaluate objective function
        # Y = self._evaluate_function(X)# for batch
        Y = np.array([self.func(x) for x in X]).reshape(-1, 1) # for loop to avoid shape issues

        if self.verbose:
            print(f"使用{method_name}采样生成{self.n_init}个{self.dim}维初始点")

        return X.astype(float), Y.astype(float)
    
    def _evaluate_function(self, X: np.ndarray) -> np.ndarray:
        """Evaluate objective function with proper shape handling."""
        if X.ndim == 1:
            X = X.reshape(1, -1)
        
        Y = np.array([self.func(x) for x in X])
        return np.reshape(Y, (-1, 1))

    # --------------------
    # fit GP model
    # --------------------
    def _fit_model(self, X_np: np.ndarray, Y_np: np.ndarray):
        # BoTorch expects tensors (n x d) and (n x 1)
        train_x = self._to_torch(X_np)
        train_y = self._to_torch(Y_np)
        bounds_torch = self._get_bounds_tensor() # Normalize 中的 bounds 需要形状为 (2, d)，即每列对应一个 [min, max]

        # 如果用户提供了自定义 kernel，则使用自定义 kernel
        if self.kernel_type is not None:
            # Kernel base construction
            if self.kernel_type in ["rbf", "rbfwhite"]:
                base_kernel = RBFKernel(
                    ard_num_dims=self.dim,
                    lengthscale_prior=GammaPrior(3.0, 6.0), # 伽马先验
                    lengthscale_constraint=Interval(1e-3, 1e3), # 值域约束
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

            # Scale kernel (outputscale)
            self.covar_module = ScaleKernel(
                base_kernel,
                outputscale_prior=GammaPrior(2.0, 0.15),
                outputscale_constraint=Interval(1e-3, 1e3),
            )

            # Noise model: 即使 kernel_type 含 white，也只需通过 GaussianLikelihood 建模
            if 'white' in self.kernel_type:
                self.likelihood = gpytorch.likelihoods.GaussianLikelihood(
                    noise_prior=GammaPrior(1.1, 10.0),
                    noise_constraint=Interval(1e-6, 1e1),
                )
                self.model = SingleTaskGP(
                    train_x,
                    train_y,
                    covar_module=self.covar_module,
                    likelihood=self.likelihood,
                    input_transform=Normalize(self.dim, bounds=bounds_torch),
                    outcome_transform=Standardize(1)
                ).to(self.device)
            else:
                self.model = SingleTaskGP(
                    train_x,
                    train_y,
                    covar_module=self.covar_module,
                    input_transform=Normalize(self.dim, bounds=bounds_torch),
                    outcome_transform=Standardize(1)
                ).to(self.device)

        else:
            # 默认 Matern kernel
            self.model = SingleTaskGP(
                train_x,
                train_y,
                input_transform=Normalize(self.dim, bounds=bounds_torch), 
                outcome_transform=Standardize(1)
            ).to(self.device)
        
        # 通过边际似然最大化拟合 GP 超参数
        self.model.train()
        self.mll = ExactMarginalLogLikelihood(self.model.likelihood, self.model)
        fit_gpytorch_mll( # 拟合超参数（相当于 gpr.fit(...)）
            self.mll,
            max_attempts=self.gp_restarts, # 类似于重启次数
            pick_best_of_all_attempts=True
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
        if self.acq == "ucb":
            beta_val = acq_para if acq_para is not None else float(self.acq_para)
            acq = UpperConfidenceBound(self.model, beta=beta_val)
        elif self.acq == "ei":
            # best_f is required for EI
            if best_f is None:
                # infer from training data
                best_f = float(self.model.train_targets.max())
            acq = ExpectedImprovement(self.model, best_f=self._to_torch(np.array([best_f])), maximize=True, xi=float(self.acq_para))
        elif self.acq == "pi":
            if best_f is None:
                best_f = float(self.model.train_targets.max())
            acq = ProbabilityOfImprovement(self.model, best_f=self._to_torch(np.array([best_f])), maximize=True, tau=float(self.acq_para))
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
        X_np, Y_np = self._initialize_samples()
        # store history
        self.history_X = X_np.copy()
        self.history_Y = Y_np.copy()

        for it in range(self.n_iter):
            t0 = time.time()

            # 2) fit GP model
            self._fit_model(self.history_X, self.history_Y)

            # Prepare acquisition parameters
            if self.acq == "ucb":
                acq_para = self._beta_schedule(it, beta0=self.acq_para)
            else:
                acq_para = self.acq_para
            best_f = float(self.history_Y.max()) if self.acq in ["ei", "pi"] else None

            # 3) optimize acquisition -> get next x (torch tensor)
            acq = self._build_acquisition(acq_para=acq_para, best_f=best_f)
            x_next_t = self._optimize_acquisition(acq)

            # 4) Evaluate new point (function expects numpy)
            x_next_np = self._from_torch(x_next_t).reshape(-1)  # shape (d,)
            y_next = self._evaluate_function(x_next_np)

            # Update history
            self.history_X = np.vstack([self.history_X, x_next_np.reshape(1, -1)])
            self.history_Y = np.vstack([self.history_Y, y_next])

            # 打印每一代进度
            t1 = time.time()
            if self.verbose:
                print(f"iter {it+1:02d}: f(x)={float(y_next.ravel()[0]):.6f}, time={(t1-t0):.2f}s")
        # 打印总耗时
        if self.verbose:
                print(f'Total time: {time.time() - start_time:.2f} s')

    # --------------------
    # plotting/saving helpers
    # --------------------
    def plot_convergence(self, path: str = None):
        plt.figure(figsize=(12, 8))

        plt.subplot(211)
        min_values = np.maximum.accumulate(self.history_Y.ravel())
        plt.plot(self.history_Y.ravel(), 'o-', linewidth=1.5, label='objective value')
        plt.plot(min_values, 'r--', linewidth=1.5, label='best value')
        plt.xlabel('evaluations')
        plt.ylabel('Values')
        plt.title('Convergence curve')
        plt.legend()
        plt.grid(True)

        plt.subplot(212)
        for i in range(self.history_X.shape[1]):
            plt.plot(self.history_X[:, i], '.-', label=f'dim {i+1}')
        plt.xlabel('evaluations')
        plt.ylabel('Parameter values')
        plt.title('Parameter evolution')
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

        # output best
        max_idx = int(np.argmax(self.history_Y))
        x_best = self.history_X[max_idx]
        y_best = float(self.history_Y[max_idx, 0])
        print(f"Best value: f({x_best}) = {y_best}")
        return x_best, y_best




# --------------
# Example usage
# --------------
if __name__ == "__main__":
    t0 = time.time()

    # fuction for test
    from GOTAcc.tests.test_function_single import *
    dim = 10
    func_type = "rosenbrock" # "sphere", "rosenbrock", "ackley"
    func, bounds = setup_objective(func_type, dim=dim)

    opt = BOOptimizer(
        func=rosenbrock,
        bounds=bounds,
        kernel_type="matern",# "rbf", "matern", "rbfwhite", "maternwhite"
        gp_restarts=5,
        acq="ucb",
        acq_para=2.0,
        acq_para_kwargs={"beta_strategy": "inv_decay", "beta_lam": 0.01}, # "exp_decay" "inv_decay" "stage" "fixed"
        acq_optimizer="optimize_acqf", # ['random', 'sobol', 'optimize_acqf']  optimize_acqf为botorch自带的多起点优化器(默认基于L-BFGS-B)
        acq_opt_kwargs={"num_restarts": 8, "raw_samples": 256, "n_candidates": 8192}, # only for 'random' and 'sobol': 'n_candidates'
        n_init=5,
        n_iter=15,
        random_state=120
        )

    opt.optimize()

    # Results display
    opt.save_history()
    opt.plot_convergence()

    # Save 
    # opt.save_history()
    
