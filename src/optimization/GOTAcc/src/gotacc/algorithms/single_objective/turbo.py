import time
import os
import numpy as np
from scipy.stats import qmc
import matplotlib.pyplot as plt
from datetime import datetime
from typing import Optional, Tuple, Dict, Any, Callable, List

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
from botorch.acquisition import UpperConfidenceBound, ExpectedImprovement, ProbabilityOfImprovement, LogExpectedImprovement
from botorch.optim import optimize_acqf
from botorch.models.transforms import Normalize, Standardize
from botorch.utils.sampling import draw_sobol_samples

# For reproducibility
torch.set_default_dtype(torch.float64)

class TuRBOOptimizer:
    def __init__(
        self, 
        func: Callable,
        bounds: np.ndarray,
        kernel_type: Optional[str] = "matern",
        gp_restarts: int = 5,
        acq: str = "ei",
        acq_optimizer: str = "optimize_acqf",
        acq_opt_kwargs: Optional[Dict[str, Any]] = None,
        n_trust_regions: int = 1,
        success_tolerance: int = 3,
        failure_tolerance: int = 5,
        length_init: float = 0.8,
        length_min: float = 0.5**7,
        n_init: int = 10,
        n_iter: int = 50,
        device: str = "cpu",
        dtype: Optional[torch.dtype] = None,
        random_state: int = 0,
        verbose: bool = True
    ):
        """
        TuRBO optimizer using PyTorch/GPyTorch/BoTorch.

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
            Acquisition function: "ei", "ucb", "pi" (TuRBO typically uses EI)
        acq_optimizer : str
            Acquisition optimizer: "random", "sobol", "lbfgs"
        n_trust_regions : int
            Number of trust regions (1 for TuRBO-1, >1 for TuRBO-n)
        success_tolerance : int
            Number of consecutive successes before expanding trust region
        failure_tolerance : int  
            Number of consecutive failures before contracting trust region
        length_init : float
            Initial trust region length (relative to parameter range)
        length_min : float
            Minimum trust region length before restart
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
        self.acq_optimizer = acq_optimizer.lower()
        self.acq_opt_kwargs = acq_opt_kwargs or {}

        # TuRBO specific parameters
        self.n_trust_regions = n_trust_regions
        self.success_tolerance = success_tolerance
        self.failure_tolerance = failure_tolerance
        self.length_init = length_init
        self.length_min = length_min

        # Optimization parameters
        self.n_init = max(n_init, 1)
        self.n_iter = n_iter

        # Random state and device setup
        self.random_state = int(random_state)
        self._setup_random_state()
        self.device = torch.device(device if torch.cuda.is_available() and device == "cuda" else "cpu")
        self.dtype = dtype if dtype is not None else torch.get_default_dtype()

        # History containers (numpy)
        self.history_X = np.zeros((0, self.dim))
        self.history_Y = np.zeros((0, 1))
        
        # Trust region states
        self.trust_region_states = []
        
        # Model placeholders
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

    # --------------------
    # Trust region management
    # --------------------
    def _initialize_trust_regions(self):
        """Initialize trust region states."""
        self.trust_region_states = []
        for i in range(self.n_trust_regions):
            # Random initial center within bounds
            center = np.random.uniform(self.bounds[:, 0], self.bounds[:, 1])
            state = {
                'center': center,
                'length': self.length_init,
                'success_count': 0,
                'failure_count': 0,
                'best_value': -np.inf,
                'active': True
            }
            self.trust_region_states.append(state)

    def _get_trust_region_bounds(self, state: Dict) -> Tensor:
        """Get current trust region bounds as torch tensor."""
        center = state['center']
        length = state['length']
        
        # Calculate trust region bounds
        range_vec = self.bounds[:, 1] - self.bounds[:, 0]
        trust_lb = np.clip(center - length * range_vec / 2.0, 
                          self.bounds[:, 0], self.bounds[:, 1])
        trust_ub = np.clip(center + length * range_vec / 2.0, 
                          self.bounds[:, 0], self.bounds[:, 1])
        
        return self._to_torch(np.column_stack([trust_lb, trust_ub]).T)

    def _update_trust_region_state(self, state_idx: int, y_new: float, X: np.ndarray, Y: np.ndarray):
        """Update trust region state based on optimization progress."""
        state = self.trust_region_states[state_idx]
        global_best = np.max(Y)
        
        # Check if we found a better solution
        if y_new > state['best_value']:
            state['best_value'] = y_new
            # Update trust region center to current best point
            best_idx = np.argmax(Y)
            state['center'] = X[best_idx].copy()
            
            state['success_count'] += 1
            state['failure_count'] = 0
            
            # Check if we reached success threshold
            if state['success_count'] >= self.success_tolerance:
                # Expand trust region
                state['length'] = min(2.0 * state['length'], 1.0)
                state['success_count'] = 0
                if self.verbose:
                    print(f"Trust region {state_idx}: Expanded to {state['length']:.3f}")
        else:
            state['failure_count'] += 1
            state['success_count'] = 0
            
            # Check if we reached failure threshold
            if state['failure_count'] >= self.failure_tolerance:
                # Contract trust region
                state['length'] *= 0.5
                state['failure_count'] = 0
                if self.verbose:
                    print(f"Trust region {state_idx}: Contracted to {state['length']:.3f}")
                
                # Check if we need to restart
                if state['length'] < self.length_min:
                    # Restart trust region
                    state['center'] = np.random.uniform(self.bounds[:, 0], self.bounds[:, 1])
                    state['length'] = self.length_init
                    state['best_value'] = -np.inf
                    state['success_count'] = 0
                    state['failure_count'] = 0
                    if self.verbose:
                        print(f"Trust region {state_idx}: Restarted")

    # --------------------
    # Initial design
    # --------------------
    def _initialize_samples(self) -> Tuple[np.ndarray, np.ndarray]:
        """Generate initial samples using LHS or Sobol sequence."""
        if self.dim < 10: 
            # LHS for lower dimensions
            sampler = qmc.LatinHypercube(d=self.dim)
            sample = sampler.random(n=self.n_init)
            X = qmc.scale(sample, self.bounds[:, 0], self.bounds[:, 1])
            method_name = "LHS"
        else:
            # Sobol for higher dimensions
            engine = torch.quasirandom.SobolEngine(dimension=self.dim, scramble=True, seed=self.random_state)
            sample = engine.draw(self.n_init).numpy()
            X = qmc.scale(sample, self.bounds[:, 0], self.bounds[:, 1])
            method_name = "Sobol"
        
        # Evaluate objective function
        # Y = self._evaluate_function(X)# for batch
        Y = np.array([self.func(x) for x in X]).reshape(-1, 1) # for loop to avoid shape issues

        if self.verbose:
            print(f"Using {method_name} sampling to generate {self.n_init} {self.dim}-dimensional initial points")

        return X.astype(float), Y.astype(float)
    
    def _evaluate_function(self, X: np.ndarray) -> np.ndarray:
        """Evaluate objective function with proper shape handling."""
        if X.ndim == 1:
            X = X.reshape(1, -1)
        
        Y = np.array([self.func(x) for x in X])
        return np.reshape(Y, (-1, 1))

    # --------------------
    # GP model fitting
    # --------------------
    def _fit_model(self, X_np: np.ndarray, Y_np: np.ndarray):
        """Fit GP model using BoTorch."""
        train_x = self._to_torch(X_np)
        train_y = self._to_torch(Y_np)
        bounds_torch = self._get_bounds_tensor()

        # Kernel construction
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

        # Scale kernel
        self.covar_module = ScaleKernel(
            base_kernel,
            outputscale_prior=GammaPrior(2.0, 0.15),
            outputscale_constraint=Interval(1e-3, 1e3),
        )

        # Noise model
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
        
        # Fit GP hyperparameters
        self.model.train()
        self.mll = ExactMarginalLogLikelihood(self.model.likelihood, self.model)
        fit_gpytorch_mll(
            self.mll,
            max_attempts=self.gp_restarts,
            pick_best_of_all_attempts=True
        )
        self.model.eval()

    # --------------------
    # Acquisition function
    # --------------------
    def _build_acquisition(self, best_f: Optional[float] = None):
        """Build acquisition function."""
        if self.acq == "ucb":
            acq = UpperConfidenceBound(self.model, beta=0.1)  # Beta can be adjusted
        elif self.acq == "ei":
            # best_f is required for EI
            if best_f is None:
                # Infer from training data
                best_f = float(self.model.train_targets.max())
            # acq = ExpectedImprovement(self.model, best_f=self._to_torch(np.array([best_f])), maximize=True)
            acq = LogExpectedImprovement(self.model, best_f=self._to_torch(np.array([best_f])), maximize=True)
        elif self.acq == "pi":
            if best_f is None:
                best_f = float(self.model.train_targets.max())
            acq = ProbabilityOfImprovement(self.model, best_f=self._to_torch(np.array([best_f])), maximize=True)
        else:
            raise ValueError(f"Unknown acquisition {self.acq}")
        return acq

    def _optimize_acquisition(self, acq, trust_bounds: Tensor) -> Tensor:
        """Optimize acquisition function within trust region bounds."""
        if self.acq_optimizer == "random":
            # Sample random candidates within trust region
            ncand = self.acq_opt_kwargs.get("n_candidates", 5000)
            X_cand = torch.rand(ncand, self.dim, device=self.device, dtype=self.dtype)
            # Scale to trust region bounds
            lb, ub = trust_bounds[0], trust_bounds[1]
            X_cand = lb + (ub - lb) * X_cand
            with torch.no_grad():
                vals = acq(X_cand.unsqueeze(1))
                vals = vals.view(-1)
            best_idx = int(torch.argmax(vals).item())
            x_next = X_cand[best_idx:best_idx+1, :]
            return x_next

        elif self.acq_optimizer == "sobol":
            # Use Sobol sequence within trust region
            nraw = self.acq_opt_kwargs.get("n_candidates", 4096)
            engine = torch.quasirandom.SobolEngine(dimension=self.dim, scramble=True, seed=self.random_state + int(time.time()) % 10000)
            X_cand = engine.draw(nraw).to(device=self.device, dtype=self.dtype)
            # Scale to trust region bounds
            lb, ub = trust_bounds[0], trust_bounds[1]
            X_cand = lb + (ub - lb) * X_cand
            with torch.no_grad():
                vals = acq(X_cand.unsqueeze(1)).view(-1)
            best_idx = int(torch.argmax(vals).item())
            return X_cand[best_idx:best_idx+1, :]

        elif self.acq_optimizer == "optimize_acqf":
            # Use BoTorch's optimize_acqf with L-BFGS-B
            num_restarts = self.acq_opt_kwargs.get("num_restarts", 10)
            raw_samples = self.acq_opt_kwargs.get("raw_samples", 512)
            
            x_opt, best_val = optimize_acqf(
                acq_function=acq,
                bounds=trust_bounds,
                q=1,
                num_restarts=num_restarts,
                raw_samples=raw_samples,
                options=self.acq_opt_kwargs.get("options", None),
            )
            return x_opt.detach().view(1, self.dim)
        else:
            raise ValueError(f"Unknown acq_optimizer={self.acq_optimizer}")

    def _select_next_points(self, X: np.ndarray, Y: np.ndarray) -> Tuple[List[np.ndarray], List[int]]:
        """Select next points for each active trust region."""
        candidate_points = []
        candidate_states = []
        
        best_f = float(np.max(Y)) if self.acq in ["ei", "pi"] else None
        acq = self._build_acquisition(best_f)
        
        for i, state in enumerate(self.trust_region_states):
            if not state['active']:
                continue
                
            # Get current trust region bounds
            trust_bounds = self._get_trust_region_bounds(state)
            
            # Optimize acquisition within trust region
            x_next_t = self._optimize_acquisition(acq, trust_bounds)
            x_next_np = self._from_torch(x_next_t).reshape(-1)
            
            candidate_points.append(x_next_np)
            candidate_states.append(i)
        
        return candidate_points, candidate_states

    # --------------------
    # Main optimization flow
    # --------------------
    def optimize(self):
        """Run TuRBO optimization."""
        if self.verbose:
            print(f"\n=== Running TuRBO-{self.n_trust_regions} with BoTorch ===")
            print(f"Using device: {self.device}")
            print(f"Acquisition: {self.acq}, Optimizer: {self.acq_optimizer}")

        # 1) Initial samples and trust regions
        X_np, Y_np = self._initialize_samples()
        self._initialize_trust_regions()
        
        # Store history
        self.history_X = X_np.copy()
        self.history_Y = Y_np.copy()

        # 2) Initial model fitting
        self._fit_model(self.history_X, self.history_Y)

        # 3) Optimization loop
        for it in range(self.n_iter):
            t0 = time.time()

            # Select next points for each active trust region
            candidate_points, candidate_states = self._select_next_points(self.history_X, self.history_Y)
            
            # Evaluate all candidate points
            for x_next, state_idx in zip(candidate_points, candidate_states):
                y_next = self._evaluate_function(x_next)
                
                # Update dataset
                self.history_X = np.vstack([self.history_X, x_next.reshape(1, -1)])
                self.history_Y = np.vstack([self.history_Y, y_next])
                
                # Update trust region state
                self._update_trust_region_state(state_idx, float(y_next.ravel()[0]), 
                                               self.history_X, self.history_Y)
            
            # Update model with all data
            self._fit_model(self.history_X, self.history_Y)
            
            # Print progress
            t1 = time.time()
            if self.verbose:
                current_best = np.max(self.history_Y)
                active_regions = sum(s['active'] for s in self.trust_region_states)
                print(f"Iter {it+1:02d}: Best f(x)={current_best:.6f}, "
                      f"Active TR: {active_regions}, time: {t1-t0:.2f}s")

    # --------------------
    # Results and visualization
    # --------------------
    def plot_convergence(self, path: str = None):
        """Plot convergence curve and parameter evolution."""
        plt.figure(figsize=(12, 8))
        
        # Convergence curve
        plt.subplot(211)
        best_values = np.maximum.accumulate(self.history_Y.ravel())
        plt.plot(self.history_Y.ravel(), 'o-', linewidth=1.5, label='Objective value')
        plt.plot(best_values, 'r--', linewidth=1.5, label='Best value')
        plt.xlabel('Evaluations')
        plt.ylabel('Values')
        plt.title('TuRBO Convergence Curve')
        plt.legend()
        plt.grid(True)

        # Parameter evolution
        plt.subplot(212)
        for i in range(self.history_X.shape[1]):
            plt.plot(self.history_X[:, i], '.-', label=f'Dim {i+1}')
        plt.xlabel('Evaluations')
        plt.ylabel('Parameter values')
        plt.title('Parameter Evolution')
        plt.legend()
        plt.grid(True)

        # Parameter space projection (for 2D problems)
        if self.dim == 2:
            plt.figure(figsize=(6, 4))
            plt.scatter(self.history_X[:, 0], self.history_X[:, 1], 
                       c=range(len(self.history_X)), cmap='viridis')
            plt.colorbar(label='Iteration')
            plt.xlabel('Dim 1')
            plt.ylabel('Dim 2')
            plt.title('Parameter Space Projection')

        # 保存图片（如果指定了路径）
        if path is None:
            timestamp = datetime.now().strftime("%Y%m%d%H%M")
            path = f"save/turbo_{timestamp}.png"
        os.makedirs(os.path.dirname(path), exist_ok=True)  # 确保目录存在 自动创建目录
        plt.savefig(path)

        plt.tight_layout()
        plt.show()

    def save_history(self, path: str = None):
        if path is None:
            timestamp = datetime.now().strftime("%Y%m%d%H%M")
            path = f"save/turbo_{timestamp}.dat"
        os.makedirs(os.path.dirname(path), exist_ok=True)  # 确保目录存在 自动创建目录
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
            f.write(f"acq_optimizer: {self.acq_optimizer}\n")
            f.write(f"acq_opt_kwargs: {self.acq_opt_kwargs}\n")
            f.write(f"n_trust_regions: {self.n_trust_regions}\n")
            f.write(f"success_tolerance: {self.success_tolerance}\n")
            f.write(f"failure_tolerance: {self.failure_tolerance}\n")
            f.write(f"length_init: {self.length_init}\n")
            f.write(f"length_min: {self.length_min}\n")            
            f.write(f"n_init: {self.n_init}\n")
            f.write(f"n_iter: {self.n_iter}\n")

        # output best
        max_idx = int(np.argmax(self.history_Y))
        x_best = self.history_X[max_idx]
        y_best = float(self.history_Y[max_idx, 0])
        print(f"Best value: f({x_best}) = {y_best}")
        return x_best, y_best


# # Test functions compatible with numpy
# def sphere_function(x):
#     """Sphere test function."""
#     return np.sum(x**2)

# def rosenbrock_function(x):
#     """Rosenbrock test function."""
#     return np.sum(100.0 * (x[1:] - x[:-1]**2)**2 + (1 - x[:-1])**2)

# def ackley_function(x):
#     """Ackley test function."""
#     a = 20
#     b = 0.2
#     c = 2 * np.pi
#     d = len(x)
#     sum1 = np.sum(x**2)
#     sum2 = np.sum(np.cos(c * x))
#     term1 = -a * np.exp(-b * np.sqrt(sum1 / d))
#     term2 = -np.exp(sum2 / d)
#     return term1 + term2 + a + np.exp(1)

# def setup_objective(func_type, dim=2):
#     """Setup objective function and bounds."""
#     if func_type == "sphere":
#         func = sphere_function
#         bounds = np.array([[-5, 5]] * dim)
#     elif func_type == "rosenbrock":
#         func = rosenbrock_function
#         bounds = np.array([[-10, 10]] * dim)
#     elif func_type == "ackley":
#         func = ackley_function
#         bounds = np.array([[-5, 5]] * dim)
#     else:
#         raise ValueError(f"Unknown function type: {func_type}")
    
#     return func, bounds


if __name__ == "__main__":
    from test_function import *
    t0 = time.time()

    # Test configuration
    dim = 10
    func_type = "rosenbrock"
    func, bounds = setup_objective(func_type, dim=dim)

    # Create TuRBO optimizer
    turbo = TuRBOOptimizer(
        func=func,
        bounds=bounds,
        kernel_type="matern",
        gp_restarts=5,
        acq="ei",  # TuRBO typically uses EI
        acq_optimizer="sobol", # ['random', 'sobol', 'optimize_acqf']
        acq_opt_kwargs={"num_restarts": 8, "raw_samples": 512, "n_candidates": 8192},
        n_trust_regions=1,  # TuRBO-1
        success_tolerance=3,
        failure_tolerance=5,
        length_init=0.8,
        length_min=0.5**7,
        n_init=5,
        n_iter=5,
        random_state=99,
        verbose=True
    )

    # Run optimization
    turbo.optimize()

    # Display results
    print(f'Total time: {time.time() - t0:.2f} s')
    turbo.save_history()
    turbo.plot_convergence()
