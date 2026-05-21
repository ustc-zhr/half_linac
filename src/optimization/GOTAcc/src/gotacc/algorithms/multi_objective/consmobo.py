# 支持qEHVI
import time
import numpy as np
from scipy.stats import qmc
import matplotlib.pyplot as plt
from datetime import datetime
from typing import Optional, Tuple, Dict, Any, Callable, List, Union
import json
import warnings
warnings.filterwarnings("ignore", message="To copy construct from a tensor")

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
from botorch.acquisition.multi_objective import (
    ExpectedHypervolumeImprovement,
    qExpectedHypervolumeImprovement,
    qLogExpectedHypervolumeImprovement,
    qNoisyExpectedHypervolumeImprovement,
    qLogNoisyExpectedHypervolumeImprovement
)
from botorch.optim import optimize_acqf
from botorch.models.transforms import Normalize, Standardize
from botorch.utils.multi_objective import is_non_dominated  #默认是用于最大化问题的
from botorch.utils.multi_objective.hypervolume import Hypervolume
from botorch.utils.multi_objective.box_decompositions.non_dominated import FastNondominatedPartitioning
from botorch.acquisition.multi_objective.objective import IdentityMCMultiOutputObjective

# For reproducibility
torch.set_default_dtype(torch.float64)

class ConsMOBOOptimizer:
    def __init__(
        self, 
        func: Callable,
        bounds: np.ndarray,
        n_objectives: int = 2,
        kernel_type: Optional[str] = "matern",
        gp_restarts: int = 5,
        acq: str = "ehvi",
        acq_optimizer: str = "optimize_acqf",
        acq_opt_kwargs: Optional[Dict[str, Any]] = None,
        n_init: int = 5,
        n_iter: int = 50,
        ref_point: Optional[np.ndarray] = None,
        maximize: bool = False,  # 明确指定优化方向，默认为最小化
        device: str = "cpu", # cuda
        dtype: Optional[torch.dtype] = None,
        random_state: int = 0,
        verbose: bool = True,
        constraint_bounds: Optional[list[Tuple[Optional[float], Optional[float]]]] = None,
    ):
        """
        Multi-objective Bayesian optimization using BoTorch.

        Parameters
        ----------
        func : callable
            Objective function that accepts numpy array of shape (n_samples, dim).
            Without constraints, it returns numpy array of shape
            (n_samples, n_objectives). With `constraint_bounds`, it must return
            `(objective_values, constraint_values)`.
        bounds : np.ndarray (dim, 2)
            Lower and upper bounds per dimension.
        n_objectives : int
            Number of objective functions.
        kernel_type : str, optional
            Kernel type: "rbf", "matern", "rbfwhite", "maternwhite"
        acq : str
            Acquisition function: "ehvi", "qehvi", "qnehvi". With constraints,
            only "qehvi" and "qnehvi" are currently supported.
        ref_point : np.ndarray, optional
            Reference point for hypervolume calculation. If None, will be inferred.
        maximize : bool, optional
            Whether to maximize the objectives (True) or minimize (False). Default is False.
        n_init, n_iter : int
            Number of initial and iteration samples
        device, dtype: torch device/dtype
        random_state : int
            Random seed for reproducibility
        verbose : bool
            Whether to print progress information
        constraint_bounds : list of (lower, upper), optional
            Output-space constraint bounds. Use `None` for an open side.
        """
        self.func = func
        self.bounds = np.asarray(bounds, dtype=float)
        self.dim = self.bounds.shape[0]
        self.n_objectives = n_objectives
        self.verbose = verbose
        self.maximize = maximize  # 存储优化方向

        # Model configuration
        self.kernel_type = kernel_type.lower() if kernel_type else "matern"
        self.gp_restarts = gp_restarts

        # Acquisition configuration
        self.acq = acq.lower()
        self.acq_optimizer = acq_optimizer.lower()
        self.acq_opt_kwargs = acq_opt_kwargs or {}

        # Reference point for hypervolume
        self.hv_calc = None
        self.ref_point = ref_point

        # Optimization parameters
        self.n_init = max(n_init, 1)
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
        self.n_outputs = self.n_objectives + self.n_constraints
        if self.has_constraints and self.acq not in ["qehvi", "qnehvi"]:
            raise NotImplementedError(
                "Constrained MOBO currently only supports acq='qehvi' or acq='qnehvi'."
            )

        # history containers (numpy)
        self.history_X = np.zeros((0, self.dim))
        self.history_Y = np.zeros((0, self.n_objectives))
        self.history_C = np.zeros((0, self.n_constraints)) if self.has_constraints else None

        # model placeholder
        self.model = None
        self.mll = None

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

    def _reshape_output_array(
        self, values: Any, n_rows: int, n_cols: int, name: str
    ) -> np.ndarray:
        """Reshape model outputs to a stable 2D array."""
        arr = np.asarray(values, dtype=float)
        if arr.size != n_rows * n_cols:
            raise ValueError(
                f"{name} output size mismatch: expected {n_rows * n_cols}, got {arr.size}."
            )
        return arr.reshape(n_rows, n_cols)

    def _parse_function_output(
        self, output: Any, n_rows: int
    ) -> Tuple[np.ndarray, Optional[np.ndarray]]:
        """Parse objective-only or objective-plus-constraints function output."""
        if not self.has_constraints:
            Y = self._reshape_output_array(output, n_rows, self.n_objectives, "Objective")
            return Y, None

        if not isinstance(output, (tuple, list)) or len(output) != 2:
            raise ValueError(
                "When constraint_bounds is provided, func(X) must return "
                "(objective_values, constraint_values)."
            )

        obj_raw, cons_raw = output
        Y = self._reshape_output_array(obj_raw, n_rows, self.n_objectives, "Objective")
        C = self._reshape_output_array(cons_raw, n_rows, self.n_constraints, "Constraint")
        return Y, C

    def _get_display_objectives(self, Y_np: np.ndarray) -> np.ndarray:
        """Convert internal objective values back to user-facing orientation."""
        return Y_np if self.maximize else -Y_np

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

    def _get_constraint_callables(self) -> list[Callable[[Tensor], Tensor]]:
        """Build BoTorch-style feasibility callables where negative means feasible."""
        callables = []
        for i, (lower, upper) in enumerate(self.constraint_bounds or []):
            output_idx = self.n_objectives + i
            if lower is not None:
                callables.append(
                    lambda samples, idx=output_idx, lb=float(lower): lb - samples[..., idx]
                )
            if upper is not None:
                callables.append(
                    lambda samples, idx=output_idx, ub=float(upper): samples[..., idx] - ub
                )
        return callables

    def _get_feasible_objective_data(
        self,
        Y_np: Optional[np.ndarray] = None,
        C_np: Optional[np.ndarray] = None,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Return objective data restricted to feasible points."""
        Y_np = self.history_Y if Y_np is None else np.asarray(Y_np, dtype=float)
        if not self.has_constraints:
            return np.ones(Y_np.shape[0], dtype=bool), Y_np

        mask = self._get_feasible_mask(C_np)
        return mask, Y_np[mask]

    # --------------------
    # initial design
    # --------------------
    def _initialize_samples(self) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]:
        """Generate initial samples using LHS or Sobol sequence."""
        if self.dim < 10: 
            sampler = qmc.LatinHypercube(d=self.dim)
            sample = sampler.random(n=self.n_init)
            X = qmc.scale(sample, self.bounds[:, 0], self.bounds[:, 1])
            method_name = "LHS"
        else:
            engine = torch.quasirandom.SobolEngine(dimension=self.dim, scramble=True, seed=self.random_state)
            sample = engine.draw(self.n_init).numpy()
            X = qmc.scale(sample, self.bounds[:, 0], self.bounds[:, 1])
            method_name = "Sobol"
        
        # Evaluate objective / constraints
        Y, C = self._evaluate_candidates(X)

        if self.verbose:
            print(f"使用{method_name}采样生成{self.n_init}个{self.dim}维初始点")

        return X.astype(float), Y.astype(float), None if C is None else C.astype(float)
    
    def _evaluate_candidates(self, X: np.ndarray) -> Tuple[np.ndarray, Optional[np.ndarray]]:
        """Evaluate objective and optional constraints point-by-point."""
        if X.ndim == 1:
            X = X.reshape(1, -1)

        Y = np.zeros((X.shape[0], self.n_objectives), dtype=float)
        C = np.zeros((X.shape[0], self.n_constraints), dtype=float) if self.has_constraints else None

        for i, x in enumerate(X):
            output = self.func(x.reshape(1, -1))
            y_i, c_i = self._parse_function_output(output, n_rows=1)
            Y[i, :] = y_i.reshape(-1)
            if self.has_constraints:
                C[i, :] = c_i.reshape(-1)

        # 内部统一处理为最大化问题，所以最小化问题需要取负
        if not self.maximize:
            Y = -Y

        return Y, C

    def _evaluate_function(self, X: np.ndarray) -> np.ndarray:
        """Evaluate only the objective values for compatibility."""
        Y, _ = self._evaluate_candidates(X)
        return Y

    # --------------------
    # fit GP model for multi-objective
    # --------------------
    def _fit_model(
        self,
        X_np: np.ndarray,
        Y_np: np.ndarray,
        C_np: Optional[np.ndarray] = None,
    ):
        """Fit separate GP models for each objective and optional constraint."""
        train_x = self._to_torch(X_np)
        bounds_torch = self._get_bounds_tensor()
        
        models = []

        output_blocks = [np.asarray(Y_np, dtype=float)]
        if self.has_constraints:
            if C_np is None:
                raise ValueError("Constraint data is required when constraint_bounds is set.")
            output_blocks.append(np.asarray(C_np, dtype=float))
        train_outputs = np.hstack(output_blocks)

        for i in range(train_outputs.shape[1]):
            train_y = self._to_torch(train_outputs[:, i:i+1])

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

                if 'white' in self.kernel_type:
                    likelihood = gpytorch.likelihoods.GaussianLikelihood(
                        noise_prior=GammaPrior(1.1, 10.0),
                        noise_constraint=Interval(1e-6, 1e1),
                    )
                    model = SingleTaskGP(
                        train_x,
                        train_y,
                        covar_module=covar_module,
                        likelihood=likelihood,
                        input_transform=Normalize(self.dim, bounds=bounds_torch),
                        outcome_transform=Standardize(1)
                    ).to(self.device)
                else:
                    model = SingleTaskGP(
                        train_x,
                        train_y,
                        covar_module=covar_module,
                        input_transform=Normalize(self.dim, bounds=bounds_torch),
                        outcome_transform=Standardize(1)
                    ).to(self.device)
            else:
                model = SingleTaskGP(
                    train_x,
                    train_y,
                    input_transform=Normalize(self.dim, bounds=bounds_torch),
                    outcome_transform=Standardize(1)
                ).to(self.device)
            
            models.append(model)
        
        # Use SumMarginalLogLikelihood for ModelListGP        
        self.model = ModelListGP(*models)
        self.mll = SumMarginalLogLikelihood(self.model.likelihood, self.model)
        
        # Fit all models
        fit_gpytorch_mll(self.mll,
            max_attempts=self.gp_restarts,
            pick_best_of_all_attempts=True
        )

        self.model.eval()
        # for model in self.model.models:
        #     model.eval()

    # --------------------
    # Set reference point for hypervolume
    # --------------------
    def _set_reference_point(self, Y_np: np.ndarray):
        """Set reference point for hypervolume calculation."""
        if self.ref_point is None: 
            # 已在目标评估时处理过优化方向 这里只需要最小值
            nadir_point = np.min(Y_np, axis=0)
            self.ref_point = nadir_point - 0.1 * np.abs(nadir_point)
            if self.verbose:
                print(f"自动设置参考点为: {self.ref_point}")
        else:
            self.ref_point = np.asarray(self.ref_point)
            if not self.maximize:
                self.ref_point = -self.ref_point  # 输入参考点按照最小化问题设置时，参考点需要取负
            if self.verbose:
                print(f"内部参考点: {self.ref_point}")

    def _get_output_objective(self) -> Optional[IdentityMCMultiOutputObjective]:
        """Project model outputs onto objective outputs when constraints are present."""
        if not self.has_constraints:
            return None
        return IdentityMCMultiOutputObjective(
            outcomes=list(range(self.n_objectives)),
            num_outcomes=self.n_outputs,
        )

    def _get_feasible_pareto_data(
        self,
    ) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]:
        """Return feasible non-dominated design points and objectives."""
        feasible_mask, feasible_Y = self._get_feasible_objective_data()
        if feasible_Y.shape[0] == 0:
            empty_X = np.zeros((0, self.dim))
            empty_Y = np.zeros((0, self.n_objectives))
            empty_C = np.zeros((0, self.n_constraints)) if self.has_constraints else None
            return empty_X, empty_Y, empty_C

        feasible_X = self.history_X[feasible_mask]
        feasible_C = self.history_C[feasible_mask] if self.has_constraints else None
        pareto_mask = is_non_dominated(self._to_torch(feasible_Y)).cpu().numpy()
        pareto_X = feasible_X[pareto_mask]
        pareto_Y = feasible_Y[pareto_mask]
        pareto_C = feasible_C[pareto_mask] if feasible_C is not None else None
        return pareto_X, pareto_Y, pareto_C

    # --------------------
    # acquisition builder for multi-objective
    # --------------------
    def _build_acquisition(self) -> gpytorch.Module:
        """Build multi-objective acquisition function."""
        ref_point_torch = self._to_torch(self.ref_point)
        objective = self._get_output_objective()
        constraints = self._get_constraint_callables() if self.has_constraints else None
        
        if self.acq in ["ehvi", "qehvi"]:
            if self.has_constraints and self.acq == "ehvi":
                raise NotImplementedError("Constrained MOBO does not support analytic EHVI.")

            # 获取可行帕累托前沿用于 partitioning
            _, pareto_Y_np, _ = self._get_feasible_pareto_data()
            pareto_Y = self._to_torch(pareto_Y_np)
            
            partitioning = FastNondominatedPartitioning(
                ref_point=ref_point_torch, Y=pareto_Y)
            
            if self.acq == "ehvi":
                acq = ExpectedHypervolumeImprovement(
                    model=self.model,
                    ref_point=ref_point_torch,
                    partitioning=partitioning
                )
            else:  # qehvi
                acq = qLogExpectedHypervolumeImprovement(
                    model=self.model,
                    ref_point=ref_point_torch,
                    partitioning=partitioning,
                    objective=objective,
                    constraints=constraints,
                )
        elif self.acq == "qnehvi":
            acq = qLogNoisyExpectedHypervolumeImprovement(
                model=self.model,
                ref_point=ref_point_torch,
                X_baseline=self._to_torch(self.history_X),
                objective=objective,
                constraints=constraints,
            )
        else:
            raise ValueError(f"Unknown multi-objective acquisition {self.acq}")
        
        return acq

    # --------------------
    # acquisition optimization : maximizer of acquisition
    # --------------------
    def _optimize_acquisition(self, acq) -> Tensor:
        lb = self._to_torch(self.bounds[:, 0])
        ub = self._to_torch(self.bounds[:, 1])

        # 获取批次大小（对于qEHVI）
        batch_size = 1
        if 'q' in self.acq:
            batch_size = int(self.acq_opt_kwargs.get("qehvi_batch", 1))
            if batch_size < 1:
                batch_size = 1

        if self.acq_optimizer == "random":
            ncand = self.acq_opt_kwargs.get("n_candidates", 5000)
            X_cand = torch.rand(ncand, self.dim, device=self.device, dtype=self.dtype)
            X_cand = lb + (ub - lb) * X_cand
            with torch.no_grad():
                vals = acq(X_cand.unsqueeze(1))
                vals = vals.view(-1)
            best_idx = int(torch.argmax(vals).item())
            x_next = X_cand[best_idx:best_idx+batch_size, :]
            return x_next

        elif self.acq_optimizer == "sobol":
            nraw = self.acq_opt_kwargs.get("n_candidates", 4096)
            engine = torch.quasirandom.SobolEngine(dimension=self.dim, scramble=True, seed=self.random_state + int(time.time()) % 10000)
            X_cand = engine.draw(nraw).to(device=self.device, dtype=self.dtype)
            X_cand = lb + (ub - lb) * X_cand
            with torch.no_grad():
                vals = acq(X_cand.unsqueeze(1)).view(-1)
            best_idx = int(torch.argmax(vals).item())
            return X_cand[best_idx:best_idx+batch_size, :]

        elif self.acq_optimizer in ["optimize_acqf", "lbfgs"]:
            num_restarts = int(self.acq_opt_kwargs.get("num_restarts", 10))
            raw_samples = int(self.acq_opt_kwargs.get("raw_samples", 512))
            bounds_t = torch.stack([lb, ub])
            
            x_opt, best_val = optimize_acqf(
                acq_function=acq,
                bounds=bounds_t,
                q=batch_size,
                num_restarts=num_restarts,
                raw_samples=raw_samples,
                options=self.acq_opt_kwargs.get("options", None),
            )
            return x_opt.detach()
        else:
            raise ValueError(f"Unknown acq_optimizer={self.acq_optimizer}")

    # --------------------
    # Calculate hypervolume
    # --------------------
    def _calculate_hypervolume(
        self,
        Y: np.ndarray,
        C: Optional[np.ndarray] = None,
    ) -> float:
        """Calculate feasible hypervolume for given points."""
        _, feasible_Y = self._get_feasible_objective_data(Y_np=Y, C_np=C)
        if len(feasible_Y) == 0:
            return 0.0

        Y_torch = self._to_torch(feasible_Y)
        ref_point_torch = self._to_torch(self.ref_point)
        
        hv = Hypervolume(ref_point=ref_point_torch)
        non_dominated_mask = is_non_dominated(Y_torch)
        pareto_Y = Y_torch[non_dominated_mask]
        return hv.compute(pareto_Y)

    # --------------------
    # main optimize flow
    # --------------------
    def optimize(self):
        print(f"\n=== Running Multi-objective BO with BoTorch ===")
        print(f"目标数量: {self.n_objectives}")
        print(f"优化方向: {'最大化' if self.maximize else '最小化'}")
        print(f"使用设备: {self.device}")
        print(f"采集函数: {self.acq}, 优化器: {self.acq_optimizer}")

        # 1) initial samples
        X_np, Y_np, C_np = self._initialize_samples()
        self.history_X = X_np.copy()
        self.history_Y = Y_np.copy()
        if self.has_constraints:
            self.history_C = C_np.copy()

        # 2) set reference point
        self._set_reference_point(Y_np)

        # 3) track hypervolume
        hypervolume_history = [self._calculate_hypervolume(self.history_Y, self.history_C)]

        for it in range(self.n_iter):
            t0 = time.time()

            # 4) fit GP model
            self._fit_model(self.history_X, self.history_Y, self.history_C)

            # 5) optimize acquisition -> get next x
            acq = self._build_acquisition()
            x_next_t = self._optimize_acquisition(acq)

            # 6) Evaluate new point
            x_next_np = self._from_torch(x_next_t)
            y_next, c_next = self._evaluate_candidates(x_next_np)

            # Update history
            self.history_X = np.vstack([self.history_X, x_next_np])
            self.history_Y = np.vstack([self.history_Y, y_next])
            if self.has_constraints:
                self.history_C = np.vstack([self.history_C, c_next])

            # Update hypervolume
            current_hv = self._calculate_hypervolume(self.history_Y, self.history_C)
            hypervolume_history.append(current_hv)

            # Print progress
            t1 = time.time()
            if self.verbose:
                display_y = self._get_display_objectives(y_next)
                if self.has_constraints:
                    feasible_next = self._get_feasible_mask(c_next).astype(int)
                    feasible_total = int(np.sum(self._get_feasible_mask()))
                    print(
                        f"Iter {it+1:02d}: 目标值={display_y.ravel()}, "
                        f"约束={c_next.ravel()}, feasible={feasible_next}, "
                        f"可行超体积={current_hv:.6f}, 可行点数={feasible_total}, "
                        f"时间={(t1-t0):.2f}s"
                    )
                else:
                    print(
                        f"Iter {it+1:02d}: 目标值={display_y.ravel()}, "
                        f"超体积={current_hv:.6f}, 时间={(t1-t0):.2f}s"
                    )

            # 清理GPU缓存
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        self.hypervolume_history = np.array(hypervolume_history)

    # --------------------
    # plotting/saving helpers
    # --------------------
    def get_pareto_front(self) -> Tuple[np.ndarray, np.ndarray]:
        """Get feasible Pareto front and corresponding solutions."""
        pareto_X, pareto_Y, _ = self._get_feasible_pareto_data()
        return pareto_X, self._get_display_objectives(pareto_Y)

    def plot_convergence(self, path: Optional[str] = None):
        """Plot convergence metrics."""
        has_constraints = self.has_constraints
        fig = plt.figure(figsize=(16, 14) if has_constraints else (15, 10))
        nrows, ncols = (3, 2) if has_constraints else (2, 2)
        all_Y = self._get_display_objectives(self.history_Y)
        feasible_mask = self._get_feasible_mask() if has_constraints else np.ones(len(self.history_Y), dtype=bool)

        ax1 = fig.add_subplot(nrows, ncols, 1)
        ax1.plot(self.hypervolume_history, 'o-', linewidth=2)
        ax1.set_xlabel('Evaluations')
        ax1.set_ylabel('Feasible Hypervolume' if has_constraints else 'Hypervolume')
        ax1.set_title('Hypervolume Convergence')
        ax1.grid(True)

        pareto_X, pareto_Y = self.get_pareto_front()
        if self.n_objectives == 2:
            ax2 = fig.add_subplot(nrows, ncols, 2)
            if has_constraints:
                infeasible_mask = ~feasible_mask
                if np.any(infeasible_mask):
                    ax2.scatter(
                        all_Y[infeasible_mask, 0],
                        all_Y[infeasible_mask, 1],
                        alpha=0.3,
                        color='gray',
                        label='Infeasible points',
                    )
                if np.any(feasible_mask):
                    ax2.scatter(
                        all_Y[feasible_mask, 0],
                        all_Y[feasible_mask, 1],
                        alpha=0.4,
                        color='tab:blue',
                        label='Feasible points',
                    )
            else:
                ax2.scatter(all_Y[:, 0], all_Y[:, 1], alpha=0.3, label='All points')

            if len(pareto_Y) > 0:
                ax2.scatter(
                    pareto_Y[:, 0],
                    pareto_Y[:, 1],
                    color='red',
                    s=50,
                    label='Feasible Pareto front' if has_constraints else 'Pareto front',
                )
            ax2.set_xlabel('Objective 1')
            ax2.set_ylabel('Objective 2')
            ax2.set_title('Pareto Front')
            ax2.legend()
            ax2.grid(True)
        elif self.n_objectives == 3:
            ax2 = fig.add_subplot(nrows, ncols, 2, projection='3d')
            if has_constraints:
                infeasible_mask = ~feasible_mask
                if np.any(infeasible_mask):
                    ax2.scatter(
                        all_Y[infeasible_mask, 0],
                        all_Y[infeasible_mask, 1],
                        all_Y[infeasible_mask, 2],
                        alpha=0.3,
                        color='gray',
                        label='Infeasible points',
                    )
                if np.any(feasible_mask):
                    ax2.scatter(
                        all_Y[feasible_mask, 0],
                        all_Y[feasible_mask, 1],
                        all_Y[feasible_mask, 2],
                        alpha=0.4,
                        color='tab:blue',
                        label='Feasible points',
                    )
            else:
                ax2.scatter(all_Y[:, 0], all_Y[:, 1], all_Y[:, 2], alpha=0.3, label='All points')

            if len(pareto_Y) > 0:
                ax2.scatter(
                    pareto_Y[:, 0],
                    pareto_Y[:, 1],
                    pareto_Y[:, 2],
                    color='red',
                    s=50,
                    label='Feasible Pareto front' if has_constraints else 'Pareto front',
                )
            ax2.set_xlabel('Objective 1')
            ax2.set_ylabel('Objective 2')
            ax2.set_zlabel('Objective 3')
            ax2.set_title('Pareto Front')
            ax2.legend()
        else:
            ax2 = fig.add_subplot(nrows, ncols, 2)
            ax2.axis('off')
            ax2.text(0.05, 0.5, 'Pareto plot only supports 2 or 3 objectives.', fontsize=12)

        ax3 = fig.add_subplot(nrows, ncols, 3)
        for i in range(self.dim):
            ax3.plot(self.history_X[:, i], '.-', label=f'Dim {i+1}')
        ax3.set_xlabel('Evaluations')
        ax3.set_ylabel('Parameter values')
        ax3.set_title('Parameter Evolution')
        ax3.legend()
        ax3.grid(True)

        ax4 = fig.add_subplot(nrows, ncols, 4)
        for i in range(self.n_objectives):
            ax4.plot(all_Y[:, i], '.-', label=f'Objective {i+1}')
        ax4.set_xlabel('Evaluations')
        ax4.set_ylabel('Objective values')
        ax4.set_title('Objective Values Evolution')
        ax4.legend()
        ax4.grid(True)

        if has_constraints:
            ax5 = fig.add_subplot(nrows, ncols, 5)
            for i in range(self.n_constraints):
                color = f'C{i % 10}'
                ax5.plot(self.history_C[:, i], '.-', color=color, label=f'Constraint {i+1}')
                lower, upper = self.constraint_bounds[i]
                if lower is not None:
                    ax5.axhline(lower, linestyle='--', color=color, alpha=0.6)
                if upper is not None:
                    ax5.axhline(upper, linestyle='--', color=color, alpha=0.6)
            ax5.set_xlabel('Evaluations')
            ax5.set_ylabel('Constraint values')
            ax5.set_title('Constraint Evolution')
            ax5.legend()
            ax5.grid(True)

            ax6 = fig.add_subplot(nrows, ncols, 6)
            feasible_float = feasible_mask.astype(float)
            ax6.step(np.arange(len(feasible_float)), feasible_float, where='mid')
            ax6.set_xlabel('Evaluations')
            ax6.set_ylabel('Feasible')
            ax6.set_title('Feasibility History')
            ax6.set_ylim(-0.1, 1.1)
            ax6.set_yticks([0.0, 1.0])
            ax6.grid(True)

        plt.tight_layout()
        if path is None:
            timestamp = datetime.now().strftime("%Y%m%d%H%M")
            path = f"save/mobo_{timestamp}.png"
        plt.savefig(path)
        plt.show()

    def save_history(self, path: Optional[str] = None):
        """
        保存优化历史、超体积历史以及帕累托前沿数据。
        Args:
            path: 保存路径。如果为None，自动生成带时间戳的文件名。
        Returns:
            None
        """
        if path is None:
            timestamp = datetime.now().strftime("%Y%m%d%H%M")
            prefix = "ConsMOBO" if self.has_constraints else "MOBO"
            path = f"save/{prefix}_{timestamp}.dat"
        
        # 保存历史数据（X和Y）
        Y_save = self._get_display_objectives(self.history_Y)
        data_cols = [self.history_X, Y_save]
        if self.has_constraints:
            feasible_col = self._get_feasible_mask().astype(float).reshape(-1, 1)
            data_cols.extend([self.history_C, feasible_col])
        data = np.hstack(data_cols)
        np.savetxt(path, data, fmt="%.6f")

        # 保存超体积历史
        hv_path = path.replace('.dat', '_hypervolume.dat')
        np.savetxt(hv_path, np.array(self.hypervolume_history), fmt="%.6f")

        # 计算并保存帕累托前沿
        Xp, Yp = self.get_pareto_front()
        _, _, Cp = self._get_feasible_pareto_data()
        pareto_path = path.replace('.dat', '_pareto.dat')
        if self.has_constraints:
            pareto_data = (
                np.hstack([Xp, Yp, Cp])
                if len(Xp) > 0
                else np.zeros((0, self.dim + self.n_objectives + self.n_constraints))
            )
        else:
            pareto_data = (
                np.hstack([Xp, Yp])
                if len(Xp) > 0
                else np.zeros((0, self.dim + self.n_objectives))
            )
        np.savetxt(pareto_path, pareto_data, fmt="%.6f")

        # 打印结果
        print(f"Saved to {path}")
        print(f"Hypervolume history saved to {hv_path}")
        print(f"Pareto front saved to {pareto_path}")
        if self.has_constraints:
            feasible_total = int(np.sum(self._get_feasible_mask()))
            print(f"Found {len(Xp)} feasible Pareto solutions. Total feasible evaluations: {feasible_total}")
        else:
            print(f"Found {len(Xp)} Pareto solutions:")
        for i, (x, y) in enumerate(zip(Xp, Yp)):
            print(f"{i+1}: x={x}, y={y}")
    
        # 保存元数据
        meta_path = path.replace('.dat', '_meta.txt')
        with open(meta_path, 'w') as f:
            f.write(f"bounds: {self.bounds}\n")
            f.write(f"n_objectives: {self.n_objectives}\n")
            f.write(f"kernel_type: {self.kernel_type}\n")
            f.write(f"gp_restarts: {self.gp_restarts}\n")
            f.write(f"acq: {self.acq}\n")
            f.write(f"acq_optimizer: {self.acq_optimizer}\n")
            f.write(f"acq_opt_kwargs: {self.acq_opt_kwargs}\n")
            f.write(f"ref_point: {self.ref_point}\n")
            f.write(f"maximize: {self.maximize}\n")
            f.write(f"n_init: {self.n_init}\n")
            f.write(f"n_iter: {self.n_iter}\n")
            f.write(f"constraint_bounds: {self.constraint_bounds}\n")
            f.write(f"n_constraints: {self.n_constraints}\n")
            if self.has_constraints:
                f.write(f"feasible_evaluations: {int(np.sum(self._get_feasible_mask()))}\n")



# --------------
# Example usage with constrained multi-objective test functions
# --------------
if __name__ == "__main__":

    def constrained_zdt1(X):
        """Constrained ZDT1-like test function (2 objectives, minimization)."""
        if X.ndim == 1:
            X = X.reshape(1, -1)

        n = X.shape[1]
        f1 = X[:, 0]
        g = 1 + 9 / (n - 1) * np.sum(X[:, 1:], axis=1)
        h = 1 - np.sqrt(f1 / g)
        f2 = g * h

        c1 = X[:, 0] + X[:, 1]             # c1 <= 0.8
        c2 = X[:, 0]                       # c2 >= 0.2

        objectives = np.column_stack([f1, f2])
        constraints = np.column_stack([c1, c2])
        return objectives, constraints

    dim = 6
    n_objectives = 2
    bounds = np.tile([0, 1], (dim, 1))
    ref_point = np.array([1.2, 6.0])

    t0 = time.time()

    opt = ConsMOBOOptimizer(
        func=constrained_zdt1,
        bounds=bounds,
        n_objectives=n_objectives,
        kernel_type="matern",
        gp_restarts=5,
        acq="qehvi",  # constrained mode currently supports ["qehvi", "qnehvi"]
        acq_optimizer="optimize_acqf",
        acq_opt_kwargs={"num_restarts": 8, "raw_samples": 128, "qehvi_batch": 1},
        ref_point=ref_point,
        maximize=False,
        n_init=10,
        n_iter=30,
        random_state=42,
        verbose=True,
        constraint_bounds=[
            (None, 0.8),  # c1 <= 0.8
            (0.2, None),  # c2 >= 0.2
        ],
    )

    opt.optimize()

    print(f'\n总时间: {time.time() - t0:.2f} s')
    # 删除 constraint_bounds 并让 func 只返回 objectives，即可回到无约束模式。
    opt.save_history()
    opt.plot_convergence()
