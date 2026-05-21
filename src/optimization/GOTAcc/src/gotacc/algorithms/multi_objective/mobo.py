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

# For reproducibility
torch.set_default_dtype(torch.float64)

class MOBOOptimizer:
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
        verbose: bool = True
    ):
        """
        Multi-objective Bayesian optimization using BoTorch.

        Parameters
        ----------
        func : callable
            Objective function that accepts numpy array of shape (n_samples, dim)
            and returns numpy array of shape (n_samples, n_objectives).
        bounds : np.ndarray (dim, 2)
            Lower and upper bounds per dimension.
        n_objectives : int
            Number of objective functions.
        kernel_type : str, optional
            Kernel type: "rbf", "matern", "rbfwhite", "maternwhite"
        acq : str
            Acquisition function: "ehvi", "qehvi", "qnehvi"
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

        # history containers (numpy)
        self.history_X = np.zeros((0, self.dim))
        self.history_Y = np.zeros((0, self.n_objectives))

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

    # --------------------
    # initial design
    # --------------------
    def _initialize_samples(self) -> Tuple[np.ndarray, np.ndarray]:
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
        
        # Evaluate objective function
        # Y = self._evaluate_function(X)# for batch
        Y = np.array([self._evaluate_function(x) for x in X]).reshape(-1, self.n_objectives) # for loop to avoid shape issues

        if self.verbose:
            print(f"使用{method_name}采样生成{self.n_init}个{self.dim}维初始点")

        return X.astype(float), Y.astype(float)
    
    def _evaluate_function(self, X: np.ndarray) -> np.ndarray:
        """Evaluate multi-objective function with proper shape handling."""
        if X.ndim == 1:
            X = X.reshape(1, -1)
        
        Y = self.func(X)
        if Y.ndim == 1:
            Y = Y.reshape(-1, 1)

        # 内部统一处理为最大化问题，所以最小化问题需要取负
        if self.maximize:
            return Y.reshape(-1, self.n_objectives)
        else:
            return -Y.reshape(-1, self.n_objectives)

    # --------------------
    # fit GP model for multi-objective
    # --------------------
    def _fit_model(self, X_np: np.ndarray, Y_np: np.ndarray):
        """Fit separate GP models for each objective."""
        train_x = self._to_torch(X_np)
        bounds_torch = self._get_bounds_tensor()
        
        models = []
        
        for i in range(self.n_objectives):
            train_y = self._to_torch(Y_np[:, i:i+1])
            
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

    # --------------------
    # acquisition builder for multi-objective
    # --------------------
    def _build_acquisition(self) -> gpytorch.Module:
        """Build multi-objective acquisition function."""
        ref_point_torch = self._to_torch(self.ref_point)
        
        if self.acq in ["ehvi", "qehvi"]:
            # 获取帕累托前沿用于 partitioning
            Y_torch = self._to_torch(self.history_Y)
            pareto_mask = is_non_dominated(Y_torch)
            pareto_Y = Y_torch[pareto_mask]
            
            # 为 EHVI 和 qEHVI 创建 partitioning
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
                    partitioning=partitioning
                )
        elif self.acq == "qnehvi":
            acq = qLogNoisyExpectedHypervolumeImprovement(
                model=self.model,
                ref_point=ref_point_torch,
                X_baseline=self._to_torch(self.history_X)
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

        elif self.acq_optimizer == "optimize_acqf":
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
    def _calculate_hypervolume(self, Y: np.ndarray) -> float:
        """Calculate hypervolume for given points."""
        Y_torch = self._to_torch(Y)
        ref_point_torch = self._to_torch(self.ref_point)
        
        hv = Hypervolume(ref_point=ref_point_torch)
        non_dominated_mask = is_non_dominated(Y_torch)
        pareto_Y = Y_torch[non_dominated_mask]
        
        if len(pareto_Y) == 0:
            return 0.0
        
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
        X_np, Y_np = self._initialize_samples()
        self.history_X = X_np.copy()
        self.history_Y = Y_np.copy()

        # 2) set reference point
        self._set_reference_point(Y_np)

        # 3) track hypervolume
        hypervolume_history = [self._calculate_hypervolume(self.history_Y)]

        for it in range(self.n_iter):
            t0 = time.time()

            # 4) fit GP model
            self._fit_model(self.history_X, self.history_Y)

            # 5) optimize acquisition -> get next x
            acq = self._build_acquisition()
            x_next_t = self._optimize_acquisition(acq)

            # 6) Evaluate new point
            x_next_np = self._from_torch(x_next_t)
            if len(x_next_np) > 1:  # 判断 x_next_np 的个数是否大于 1 (for qEHVI)
                y_next = np.array([self._evaluate_function(x) for x in x_next_np]).reshape(-1, self.n_objectives)  # 序列化调用_evaluate_function
            else:
                y_next = self._evaluate_function(x_next_np)  # 单个调用

            # Update history
            self.history_X = np.vstack([self.history_X, x_next_np])
            self.history_Y = np.vstack([self.history_Y, y_next])

            # Update hypervolume
            current_hv = self._calculate_hypervolume(self.history_Y)
            hypervolume_history.append(current_hv)

            # Print progress
            t1 = time.time()
            if self.verbose:
                # 显示原始目标值（不是内部转换后的值）
                if self.maximize:
                    display_y = y_next.ravel()
                else:
                    display_y = -y_next.ravel()  # 转换回原始值
                
                print(f"Iter {it+1:02d}: 目标值={display_y}, 超体积={current_hv:.6f}, 时间={(t1-t0):.2f}s")

            # 清理GPU缓存
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        self.hypervolume_history = np.array(hypervolume_history)

    # --------------------
    # plotting/saving helpers
    # --------------------
    def get_pareto_front(self) -> Tuple[np.ndarray, np.ndarray]:
        """Get Pareto front and corresponding solutions."""
        Y_torch = self._to_torch(self.history_Y)
        non_dominated_mask = is_non_dominated(Y_torch)
        pareto_X = self.history_X[non_dominated_mask.cpu().numpy()]
        pareto_Y = self.history_Y[non_dominated_mask.cpu().numpy()]
        
        # 返回原始目标值（不是内部转换后的值）
        if not self.maximize:
            pareto_Y = -pareto_Y  # 转换回原始值
        
        return pareto_X, pareto_Y

    def plot_convergence(self, path=None):
        """Plot convergence metrics."""
        plt.figure(figsize=(15, 10))
        
        # Hypervolume convergence
        plt.subplot(2, 2, 1)
        plt.plot(self.hypervolume_history, 'o-', linewidth=2)
        plt.xlabel('Evaluations')
        plt.ylabel('Hypervolume')
        plt.title('Hypervolume Convergence')
        plt.grid(True)

        # 显示所有点的原始目标值
        if self.maximize:
            all_Y = self.history_Y
        else:
            all_Y = -self.history_Y

        # Pareto front (for 2 or 3 objectives)
        if self.n_objectives == 2:
            plt.subplot(2, 2, 2)
            pareto_X, pareto_Y = self.get_pareto_front()
            
            # 显示所有点的原始目标值
            # if self.maximize:
            #     all_Y = self.history_Y
            # else:
            #     all_Y = -self.history_Y
            
            plt.scatter(all_Y[:, 0], all_Y[:, 1], alpha=0.3, label='All points')
            plt.scatter(pareto_Y[:, 0], pareto_Y[:, 1], color='red', s=50, label='Pareto front')
            plt.xlabel('Objective 1')
            plt.ylabel('Objective 2')
            plt.title('Pareto Front')

            # f1 = np.linspace(0, 1, 100)
            # f2 = 1 - np.sqrt(f1)
            # plt.plot(f1, f2, 'r-', label='True Pareto Front')

            plt.legend()
            plt.grid(True)


        elif self.n_objectives == 3:
            from mpl_toolkits.mplot3d import Axes3D
            plt.subplot(2, 2, 2, projection='3d')
            pareto_X, pareto_Y = self.get_pareto_front()
            
            # if self.maximize:
            #     all_Y = self.history_Y
            # else:
            #     all_Y = -self.history_Y
                
            plt.scatter(all_Y[:, 0], all_Y[:, 1], all_Y[:, 2], alpha=0.3, label='All points')
            plt.scatter(pareto_Y[:, 0], pareto_Y[:, 1], pareto_Y[:, 2], color='red', s=50, label='Pareto front')
            plt.xlabel('Objective 1')
            plt.ylabel('Objective 2')
            plt.zlabel('Objective 3')
            plt.title('Pareto Front')
            plt.legend()

        # Parameter evolution
        plt.subplot(2, 2, 3)
        for i in range(self.dim):
            plt.plot(self.history_X[:, i], '.-', label=f'Dim {i+1}')
        plt.xlabel('Evaluations')
        plt.ylabel('Parameter values')
        plt.title('Parameter Evolution')
        plt.legend()
        plt.grid(True)

        # Objective values evolution
        plt.subplot(2, 2, 4)
        for i in range(self.n_objectives):
            y_values = all_Y[:, i]
            plt.plot(y_values, '.-', label=f'Objective {i+1}')
        plt.xlabel('Evaluations')
        plt.ylabel('Objective values')
        plt.title('Objective Values Evolution')
        plt.legend()
        plt.grid(True)

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
            path = f"save/MOBO_{timestamp}.dat"
        
        # 保存历史数据（X和Y）
        if self.maximize:
            Y_save = self.history_Y
        else:
            Y_save = -self.history_Y
        data = np.hstack([self.history_X, Y_save])
        np.savetxt(path, data, fmt="%.6f")

        # 保存超体积历史
        hv_path = path.replace('.dat', '_hypervolume.dat')
        np.savetxt(hv_path, np.array(self.hypervolume_history), fmt="%.6f")

        # 计算并保存帕累托前沿
        Xp, Yp = self.get_pareto_front()
        pareto_path = path.replace('.dat', '_pareto.dat')
        pareto_data = np.hstack([Xp, Yp])
        np.savetxt(pareto_path, pareto_data, fmt="%.6f")

        # 打印结果
        print(f"Saved to {path}")
        print(f"Hypervolume history saved to {hv_path}")
        print(f"Pareto front saved to {pareto_path}")
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

# --------------
# Example usage with multi-objective test functions
# --------------
if __name__ == "__main__":
    
    # 定义多目标测试函数 (ZDT1 是最小化问题)
    def zdt1(X):
        """ZDT1 test function (2 objectives, minimization)."""
        if X.ndim == 1:
            X = X.reshape(1, -1)
        
        n = X.shape[1]
        f1 = X[:, 0]
        g = 1 + 9 / (n - 1) * np.sum(X[:, 1:], axis=1)
        h = 1 - np.sqrt(f1 / g)
        f2 = g * h
        
        return np.column_stack([f1, f2])
    
    # 设置测试问题
    dim = 10
    n_objectives = 2
    bounds = np.tile([0, 1], (dim, 1))
    
    # 参考点（对于最小化问题，参考点应该比所有可能的目标值都"差"，即更大）
    ref_point = np.array([1.0, 1.0])  # ZDT1 的理论帕累托前沿在 [0,1]x[0,1] 范围内
    
    t0 = time.time()

    opt = MOBOOptimizer(
        func=zdt1,
        bounds=bounds,
        n_objectives=n_objectives,
        kernel_type="matern", # "rbf", "matern", "rbfwhite", "maternwhite"
        gp_restarts=5,
        acq="qehvi",  # ["ehvi", "qehvi", "qnehvi"]
        acq_optimizer="optimize_acqf",
        acq_opt_kwargs={"num_restarts": 8, "raw_samples": 256, "qehvi_batch": 3},
        ref_point=ref_point,
        maximize=False,  # 明确指定为最小化问题
        n_init=5,
        n_iter=5,
        random_state=42,
        verbose=True
    )

    opt.optimize()

    # 结果显示
    print(f'\n总时间: {time.time() - t0:.2f} s')
    # opt.save_history()
    opt.plot_convergence()
