import time
import warnings
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Tuple, Dict, Any, List

import matplotlib.pyplot as plt
import numpy as np
import torch
from botorch.fit import fit_gpytorch_mll
from botorch.models import ModelListGP, SingleTaskGP
from botorch.models.transforms import Normalize, Standardize
from botorch.utils.multi_objective import is_non_dominated
from botorch.utils.multi_objective.hypervolume import Hypervolume
from botorch.utils.multi_objective.box_decompositions.non_dominated import FastNondominatedPartitioning
from botorch.acquisition.multi_objective import ExpectedHypervolumeImprovement

from gpytorch.constraints import Interval
from gpytorch.kernels import MaternKernel, RBFKernel, ScaleKernel
from gpytorch.likelihoods import FixedNoiseGaussianLikelihood
from gpytorch.mlls import SumMarginalLogLikelihood
from gpytorch.priors import GammaPrior

from scipy.stats import qmc, norm
from torch import Tensor

warnings.filterwarnings("ignore", message="To copy construct from a tensor")
torch.set_default_dtype(torch.float64)
# 增加了pso生成子代
# 增加了约束处理
# 更新了pso生成子代方式，更符合经典方法
# 增加acq_mode选线：ucb/ehvi/combine(ucb+ehvi)

class MultiObjectiveMGGPO:
    """
    Constrained MG-GPO optimizer.

    With output constraints, pass ``constraint_bounds`` and make ``func(X)``
    return ``(objective_values, constraint_values)``, matching ConsBO and
    ConsMOBO. The number of constraints is inferred from ``constraint_bounds``.
    """

    def __init__(
        self,
        func: Callable,
        bounds: np.ndarray,
        n_objectives: int = 2,
        constraint_bounds: Optional[List[Tuple[Optional[float], Optional[float]]]] = None,
        kernel_type: str = "rbf",
        gp_restarts: int = 5,
        pop_size: int = 80,
        acq_mode: str = "ucb",
        ref_point: Optional[np.ndarray] = None,
        ucb_beta: float = 2.0,
        ucb_beta_kwargs: Optional[Dict[str, Any]] = None,
        m1: int = 20,
        m2: int = 20,
        m3: int = 0,
        evals_per_gen: Optional[int] = None,
        n_generations: int = 50,
        use_all_history_for_gp: bool = False,
        gp_history_max: Optional[int] = None,
        mutation_eta: float = 20.0,
        crossover_eta: float = 20.0,
        mutation_prob: Optional[float] = None,
        crossover_prob: Optional[float] = 0.5,
        w: float = 0.4, # inertia weight
        c1: float = 1.0,# cognitive coefficient
        c2: float = 2.0,# social coefficient
        maximize: bool = False,
        device: str = "cpu",
        dtype: Optional[torch.dtype] = None,
        random_state: int = 0,
        verbose: bool = True,
    ):
        self.func = func
        self.bounds = np.asarray(bounds, dtype=float)
        self.dim = self.bounds.shape[0]
        self.n_objectives = int(n_objectives)
        self.constraint_bounds = (
            [(lb, ub) for lb, ub in constraint_bounds]
            if constraint_bounds
            else None
        )
        self.n_constraints = len(self.constraint_bounds) if self.constraint_bounds is not None else 0
        self.has_constraints = self.n_constraints > 0
        self.kernel_type = kernel_type.lower()
        self.gp_restarts = int(gp_restarts)

        self.pop_size = int(pop_size)
        self.evals_per_gen = int(evals_per_gen) if evals_per_gen is not None else int(pop_size)
        self.n_generations = int(n_generations)
        self.m1 = int(m1)
        self.m2 = int(m2)
        self.m3 = int(m3)

        self.mutation_eta = float(mutation_eta)
        self.crossover_eta = float(crossover_eta)
        self.mutation_prob = float(mutation_prob) if mutation_prob is not None else 1.0 / self.dim
        self.crossover_prob = float(crossover_prob)
        self.w = float(w)
        self.c1 = float(c1)
        self.c2 = float(c2)

        self.maximize = bool(maximize)
        self.use_all_history_for_gp = bool(use_all_history_for_gp)
        self.gp_history_max = gp_history_max
        self.device = torch.device(device if torch.cuda.is_available() and device == "cuda" else "cpu")
        self.dtype = dtype if dtype is not None else torch.get_default_dtype()
        self.random_state = int(random_state)
        self.verbose = bool(verbose)

        # acquisition config
        self.acq_mode = acq_mode.lower()
        self.ucb_beta = float(ucb_beta)
        self.ucb_beta_kwargs = ucb_beta_kwargs if ucb_beta_kwargs is not None else {}
        self.ref_point = None if ref_point is None else np.asarray(ref_point, dtype=float)

        self.model = None
        self.mll = None
        self.constraint_model = None
        self.constraint_mll = None
        self.hv_calc = None

        self.history_X = np.zeros((0, self.dim))
        self.history_Y = np.zeros((0, self.n_objectives))
        self.history_C = np.zeros((0, self.n_constraints)) if self.n_constraints > 0 else np.zeros((0, 0))
        self.history_status: List[str] = []
        self.history_feasible = np.zeros((0,), dtype=bool)
        self.history_raw: List[Dict[str, Any]] = []
        self.population_X = None
        self.population_Y = None
        self.population_C = None
        self.population_status = None
        self.population_feasible = None
        self.hypervolume_history = []

        # PSO-assisted offspring state (archive-based MOPSO-like)
        self.velocity = None
        self.pbest = None
        self.pbest_Y = None
        self.gbest = None   # 仅保留作调试/日志用途；真实 leader 在每个粒子更新时单独采样

        # external archive for MOPSO-style leader selection
        self.archive_X = None
        self.archive_Y = None
        self.archive_max_size = max(50, self.pop_size)
        self.leader_select_mode = "crowding"   # "crowding" or "uniform"
        self.incomparable_update_prob = 0.1

        self._setup_random_state()

    def _setup_random_state(self):
        np.random.seed(self.random_state)
        torch.manual_seed(self.random_state)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(self.random_state)

    def _to_torch(self, x: np.ndarray) -> Tensor:
        return torch.tensor(x, device=self.device, dtype=self.dtype)

    def _from_torch(self, x: Tensor) -> np.ndarray:
        return x.detach().cpu().numpy()

    def _bounds_tensor(self) -> Tensor:
        return self._to_torch(self.bounds.T)

    def _empty_constraints(self, n: int) -> np.ndarray:
        return np.zeros((n, self.n_constraints), dtype=float)

    def _get_feasible_mask(self, C_np: Optional[np.ndarray] = None) -> np.ndarray:
        """Return rows satisfying all configured output-space constraints."""
        if self.n_constraints == 0:
            if C_np is None:
                n = self.history_Y.shape[0]
            else:
                arr = np.asarray(C_np)
                n = arr.shape[0] if arr.ndim > 1 else 1
            return np.ones(n, dtype=bool)

        C_np = self.history_C if C_np is None else np.asarray(C_np, dtype=float)
        C_np = C_np.reshape(-1, self.n_constraints)
        mask = np.ones(C_np.shape[0], dtype=bool)
        for i, (lower, upper) in enumerate(self.constraint_bounds or []):
            if lower is not None:
                mask &= C_np[:, i] >= float(lower)
            if upper is not None:
                mask &= C_np[:, i] <= float(upper)
        return mask

    def _is_feasible(self, c: Optional[np.ndarray]) -> bool:
        if self.n_constraints == 0:
            return True
        if c is None:
            return False
        c = np.asarray(c, dtype=float).reshape(1, self.n_constraints)
        return bool(self._get_feasible_mask(c)[0])

    def _constraint_violation_scalar(self, C: np.ndarray) -> np.ndarray:
        if self.n_constraints == 0 or C.size == 0:
            return np.zeros(C.shape[0] if getattr(C, 'ndim', 0) > 0 else 0)
        C = np.asarray(C, dtype=float).reshape(-1, self.n_constraints)
        violation = np.zeros(C.shape[0], dtype=float)
        for i, (lower, upper) in enumerate(self.constraint_bounds or []):
            if lower is not None:
                violation += np.maximum(float(lower) - C[:, i], 0.0)
            if upper is not None:
                violation += np.maximum(C[:, i] - float(upper), 0.0)
        return violation

    def _compare_solution_quality(self, y_a: np.ndarray, c_a: Optional[np.ndarray], y_b: np.ndarray, c_b: Optional[np.ndarray]) -> int:
        fa = self._is_feasible(c_a)
        fb = self._is_feasible(c_b)
        if fa and not fb:
            return 1
        if fb and not fa:
            return -1
        if fa and fb:
            if self._is_better(y_a, y_b):
                return 1
            if self._is_better(y_b, y_a):
                return -1
            return 0
        va = float(self._constraint_violation_scalar(np.asarray(c_a, dtype=float).reshape(1, -1))[0])
        vb = float(self._constraint_violation_scalar(np.asarray(c_b, dtype=float).reshape(1, -1))[0])
        if va < vb - 1e-15:
            return 1
        if vb < va - 1e-15:
            return -1
        return 0

    def _reshape_output_array(
        self,
        values: Any,
        n_rows: int,
        n_cols: int,
        name: str,
    ) -> np.ndarray:
        arr = np.asarray(values, dtype=float)
        if arr.size != n_rows * n_cols:
            raise ValueError(
                f"{name} output size mismatch: expected {n_rows * n_cols}, got {arr.size}."
            )
        return arr.reshape(n_rows, n_cols)

    def _normalize_aux_list(self, values: Any, n: int, default: Any) -> List[Any]:
        if values is None:
            return [default for _ in range(n)]
        if isinstance(values, np.ndarray):
            values = values.tolist()
        if isinstance(values, list):
            if len(values) == n:
                return values
            if n == 1:
                return [values]
        return [values for _ in range(n)]

    def _parse_eval_output(self, out: Any, n: int) -> Dict[str, Any]:
        """Normalize objective-only or objective-plus-constraints output."""
        if isinstance(out, dict):
            obj = self._reshape_output_array(out["objectives"], n, self.n_objectives, "Objective")
            cons = (
                self._reshape_output_array(
                    out.get("constraints", self._empty_constraints(n)),
                    n,
                    self.n_constraints,
                    "Constraint",
                )
                if self.n_constraints > 0
                else self._empty_constraints(n)
            )
            status = self._normalize_aux_list(out.get("status"), n, "ok")
            feasible = out.get("feasible", None)
            if feasible is None:
                feasible = self._get_feasible_mask(cons)
            feasible = np.asarray(feasible, dtype=bool).reshape(n)
            raw = self._normalize_aux_list(out.get("raw"), n, {})
            return {
                "objectives": obj,
                "constraints": cons,
                "status": status,
                "feasible": feasible,
                "raw": raw,
            }

        if self.n_constraints > 0:
            if not isinstance(out, (tuple, list)) or len(out) != 2:
                raise ValueError(
                    "When constraint_bounds is provided, func(X) must return "
                    "(objective_values, constraint_values)."
                )
            obj_raw, cons_raw = out
            obj = self._reshape_output_array(obj_raw, n, self.n_objectives, "Objective")
            cons = self._reshape_output_array(cons_raw, n, self.n_constraints, "Constraint")
            feasible = self._get_feasible_mask(cons)
            return {
                "objectives": obj,
                "constraints": cons,
                "status": ["ok" if f else "infeasible" for f in feasible],
                "feasible": feasible,
                "raw": [{} for _ in range(n)],
            }

        Y = np.asarray(out, dtype=float)
        if Y.ndim == 1:
            Y = Y.reshape(-1, self.n_objectives)
        Y = Y.reshape(n, self.n_objectives)
        C = self._empty_constraints(n)
        feasible = np.ones(n, dtype=bool)
        return {
            "objectives": Y,
            "constraints": C,
            "status": ["ok"] * n,
            "feasible": feasible,
            "raw": [{} for _ in range(n)],
        }

    def _call_func_structured(self, X: np.ndarray) -> Dict[str, Any]:
        if X.ndim == 1:
            X = X.reshape(1, -1)
        n = X.shape[0]
        out = self.func(X)
        return self._parse_eval_output(out, n)

    def _evaluate_batch(self, X: np.ndarray) -> Dict[str, Any]:
        details = self._call_func_structured(X)
        Y = details['objectives'].astype(float)
        if not self.maximize:
            Y = -Y
        details['objectives_internal'] = Y
        return details

    def _sanitize_training_arrays(self, X_np: np.ndarray, Y_np: np.ndarray, C_np: Optional[np.ndarray] = None) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]:
        X_np = np.asarray(X_np, dtype=float)
        Y_np = np.asarray(Y_np, dtype=float)
        mask = np.all(np.isfinite(X_np), axis=1) & np.all(np.isfinite(Y_np), axis=1)
        C_out = None
        if C_np is not None:
            C_np = np.asarray(C_np, dtype=float)
            if C_np.size > 0:
                mask = mask & np.all(np.isfinite(C_np), axis=1)
                C_out = C_np[mask]
            else:
                C_out = C_np.reshape(len(X_np), 0)[mask]
        X_out = X_np[mask]
        Y_out = Y_np[mask]
        if len(X_out) == 0:
            raise RuntimeError('No finite training rows available after NaN/Inf filtering.')
        return X_out, Y_out, C_out

    def _predict_constraint_means(self, X_candidates: np.ndarray) -> np.ndarray:
        if self.n_constraints == 0 or self.constraint_model is None:
            return np.zeros((len(X_candidates), 0), dtype=float)
        X_t = self._to_torch(X_candidates)
        means = []
        batch_size = 1000
        with torch.no_grad():
            for model_j in self.constraint_model.models:
                mu_list = []
                for i in range(0, len(X_t), batch_size):
                    post = model_j.posterior(X_t[i:i+batch_size])
                    mu_list.append(post.mean.view(-1))
                means.append(torch.cat(mu_list).cpu().numpy())
        return np.vstack(means).T

    def _predict_feasibility_probability(self, X_candidates: np.ndarray) -> np.ndarray:
        n = len(X_candidates)
        if self.n_constraints == 0 or self.constraint_model is None:
            return np.ones(n, dtype=float)
        X_t = self._to_torch(X_candidates)
        probs = []
        batch_size = 1000
        with torch.no_grad():
            for j, model_j in enumerate(self.constraint_model.models):
                mu_list, std_list = [], []
                for i in range(0, len(X_t), batch_size):
                    post = model_j.posterior(X_t[i:i+batch_size])
                    mu_list.append(post.mean.view(-1))
                    std_list.append(torch.sqrt(post.variance.view(-1).clamp_min(1e-12)))
                mu = torch.cat(mu_list).cpu().numpy()
                std = torch.cat(std_list).cpu().numpy()
                std = np.maximum(std, 1e-12)
                lower, upper = (self.constraint_bounds or [])[j]
                if lower is not None and upper is not None:
                    p = norm.cdf((float(upper) - mu) / std) - norm.cdf((float(lower) - mu) / std)
                elif lower is not None:
                    p = 1.0 - norm.cdf((float(lower) - mu) / std)
                elif upper is not None:
                    p = norm.cdf((float(upper) - mu) / std)
                else:
                    p = np.ones_like(mu)
                probs.append(np.clip(p, 0.0, 1.0))
        probs = np.vstack(probs).T
        return np.prod(probs, axis=1)

    def _initialize_population(self):
        # Keep Sobol for medium/high dimensions; this is fine and usually stronger than pure random.
        if self.dim < 50:
            sampler = qmc.LatinHypercube(d=self.dim, seed=self.random_state)
            sample = sampler.random(n=self.pop_size)
            X = qmc.scale(sample, self.bounds[:, 0], self.bounds[:, 1])
            method = "LHS"
        else:
            engine = torch.quasirandom.SobolEngine(dimension=self.dim, scramble=True, seed=self.random_state)
            sample = engine.draw(self.pop_size).cpu().numpy()
            X = qmc.scale(sample, self.bounds[:, 0], self.bounds[:, 1])
            method = "Sobol"

        eval_details = self._evaluate_batch(X)
        Y = eval_details["objectives_internal"]
        C = eval_details["constraints"]
        self.population_X = X
        self.population_Y = Y
        self.population_C = C
        self.population_status = list(eval_details["status"])
        self.population_feasible = eval_details["feasible"].copy()
        self.history_X = X.copy()
        self.history_Y = Y.copy()
        self.history_C = C.copy()
        self.history_status = list(eval_details["status"])
        self.history_feasible = eval_details["feasible"].copy()
        self.history_raw = list(eval_details["raw"])

        # Initialize PSO state
        velocity_range = (self.bounds[:, 1] - self.bounds[:, 0]) * 0.2
        self.velocity = np.random.uniform(-velocity_range, velocity_range, size=(self.pop_size, self.dim))
        self.pbest = self.population_X.copy()
        self.pbest_Y = self.population_Y.copy()
        self._best_update()   # also initializes archive
        if self.verbose:
            print(f"初始化种群: {self.pop_size}, method={method}")

    def _evaluate_function(self, X: np.ndarray) -> np.ndarray:
        details = self._evaluate_batch(X)
        return details["objectives_internal"]

    def _get_training_data(self):
        if self.use_all_history_for_gp:
            X_train = self.history_X
            Y_train = self.history_Y
            C_train = self.history_C if self.n_constraints > 0 else self._empty_constraints(len(X_train))
        else:
            if self.gp_history_max is None:
                X_train = self.history_X
                Y_train = self.history_Y
                C_train = self.history_C if self.n_constraints > 0 else self._empty_constraints(len(X_train))
            else:
                n_keep = min(int(self.gp_history_max), len(self.history_X))
                X_train = np.vstack([self.population_X, self.history_X[-n_keep:]])
                Y_train = np.vstack([self.population_Y, self.history_Y[-n_keep:]])
                C_hist = self.history_C[-n_keep:] if self.n_constraints > 0 else self._empty_constraints(n_keep)
                C_train = np.vstack([self.population_C, C_hist]) if self.n_constraints > 0 else self._empty_constraints(len(X_train))

        X_round = np.round(X_train, 12)
        _, unique_idx = np.unique(X_round, axis=0, return_index=True)
        unique_idx = np.sort(unique_idx)

        return X_train[unique_idx], Y_train[unique_idx], C_train[unique_idx]

    def _fit_surrogate(self, X_np: np.ndarray, Y_np: np.ndarray, C_np: Optional[np.ndarray] = None):
        X_np, Y_np, C_np = self._sanitize_training_arrays(X_np, Y_np, C_np)
        train_x = self._to_torch(X_np)
        bounds_torch = self._bounds_tensor()
        obj_models = []

        for j in range(self.n_objectives):
            train_y = self._to_torch(Y_np[:, j:j + 1])
            train_yvar = torch.full_like(train_y, 1e-6)

            if self.kernel_type == "rbf":
                base_kernel = RBFKernel(ard_num_dims=self.dim, lengthscale_prior=GammaPrior(3.0, 6.0), lengthscale_constraint=Interval(1e-3, 1e3))
            else:
                base_kernel = MaternKernel(nu=2.5, ard_num_dims=self.dim, lengthscale_prior=GammaPrior(3.0, 6.0), lengthscale_constraint=Interval(1e-3, 1e3))

            covar_module = ScaleKernel(base_kernel, outputscale_prior=GammaPrior(2.0, 0.15), outputscale_constraint=Interval(1e-6, 1e4))

            model = SingleTaskGP(train_X=train_x, train_Y=train_y, train_Yvar=train_yvar, covar_module=covar_module, input_transform=Normalize(d=self.dim, bounds=bounds_torch)).to(self.device)
            obj_models.append(model)

        self.model = ModelListGP(*obj_models)
        self.mll = SumMarginalLogLikelihood(self.model.likelihood, self.model)
        fit_gpytorch_mll(self.mll, max_attempts=self.gp_restarts, pick_best_of_all_attempts=True)
        self.model.eval()
        for m in self.model.models:
            m.eval()

        self.constraint_model = None
        self.constraint_mll = None
        if self.n_constraints > 0 and C_np is not None and C_np.size > 0:
            con_models = []
            for j in range(self.n_constraints):
                train_c = self._to_torch(C_np[:, j:j + 1])
                train_cvar = torch.full_like(train_c, 1e-6)
                if self.kernel_type == "rbf":
                    base_kernel = RBFKernel(ard_num_dims=self.dim, lengthscale_prior=GammaPrior(3.0, 6.0), lengthscale_constraint=Interval(1e-3, 1e3))
                else:
                    base_kernel = MaternKernel(nu=2.5, ard_num_dims=self.dim, lengthscale_prior=GammaPrior(3.0, 6.0), lengthscale_constraint=Interval(1e-3, 1e3))
                covar_module = ScaleKernel(base_kernel, outputscale_prior=GammaPrior(2.0, 0.15), outputscale_constraint=Interval(1e-6, 1e4))
                model = SingleTaskGP(train_X=train_x, train_Y=train_c, train_Yvar=train_cvar, covar_module=covar_module, input_transform=Normalize(d=self.dim, bounds=bounds_torch)).to(self.device)
                con_models.append(model)
            self.constraint_model = ModelListGP(*con_models)
            self.constraint_mll = SumMarginalLogLikelihood(self.constraint_model.likelihood, self.constraint_model)
            fit_gpytorch_mll(self.constraint_mll, max_attempts=self.gp_restarts, pick_best_of_all_attempts=True)
            self.constraint_model.eval()
            for m in self.constraint_model.models:
                m.eval()

    def _fast_nondominated_sort(self, objs: np.ndarray) -> List[List[int]]:
        n = objs.shape[0]
        dominates = [[] for _ in range(n)]
        dominated_count = np.zeros(n, dtype=int)
        fronts = [[]]

        for p in range(n):
            p_dom = np.all(objs[p] >= objs, axis=1) & np.any(objs[p] > objs, axis=1)
            q_dom_p = np.all(objs >= objs[p], axis=1) & np.any(objs > objs[p], axis=1)
            dominates[p] = np.where(p_dom)[0].tolist()
            dominated_count[p] = int(np.sum(q_dom_p))
            if dominated_count[p] == 0:
                fronts[0].append(p)

        i = 0
        while i < len(fronts) and len(fronts[i]) > 0:
            nxt = []
            for p in fronts[i]:
                for q in dominates[p]:
                    dominated_count[q] -= 1
                    if dominated_count[q] == 0:
                        nxt.append(q)
            if nxt:
                fronts.append(nxt)
            i += 1
        return fronts

    def _crowding_distance(self, objs: np.ndarray, indices: List[int]) -> Dict[int, float]:
        dist = {i: 0.0 for i in indices}
        if len(indices) == 0:
            return dist
        if len(indices) <= 2:
            for i in indices:
                dist[i] = float("inf")
            return dist

        arr = objs[np.array(indices)]
        m = arr.shape[1]
        for k in range(m):
            order = np.argsort(arr[:, k])
            dist[indices[order[0]]] = float("inf")
            dist[indices[order[-1]]] = float("inf")
            fmin = arr[order[0], k]
            fmax = arr[order[-1], k]
            if fmax <= fmin:
                continue
            for i in range(1, len(indices) - 1):
                prev_f = arr[order[i - 1], k]
                next_f = arr[order[i + 1], k]
                dist[indices[order[i]]] += (next_f - prev_f) / (fmax - fmin)
        return dist
    
    def _dominates(self, a: np.ndarray, b: np.ndarray) -> bool:
        # internal convention is maximization
        return bool(np.all(a >= b) and np.any(a > b))

    def _incomparable(self, a: np.ndarray, b: np.ndarray) -> bool:
        return bool((not self._dominates(a, b)) and (not self._dominates(b, a)))

    def _update_archive(self):
        """
        External archive update:
        archive <- nondominated(archive U current population)
        if oversized, keep sparse points by crowding distance
        """
        if self.population_X is None or self.population_Y is None or len(self.population_X) == 0:
            return

        pop_C = self.population_C if self.population_C is not None else self._empty_constraints(len(self.population_X))
        if self.archive_X is None or self.archive_Y is None or len(self.archive_X) == 0:
            cand_X = self.population_X.copy()
            cand_Y = self.population_Y.copy()
            cand_C = pop_C.copy()
        else:
            archive_C = (
                self.archive_C
                if hasattr(self, "archive_C") and self.archive_C is not None
                else self._empty_constraints(len(self.archive_X))
            )
            cand_X = np.vstack([self.archive_X, self.population_X])
            cand_Y = np.vstack([self.archive_Y, self.population_Y])
            cand_C = np.vstack([archive_C, pop_C])

        # 只保留可行解入 archive；若当前一个可行解都没有，则 archive 直接清空/返回
        if self.n_constraints > 0:
            feas = self._get_feasible_mask(cand_C)
            if not np.any(feas):
                self.archive_X = np.zeros((0, self.dim))
                self.archive_Y = np.zeros((0, self.n_objectives))
                self.archive_C = self._empty_constraints(0)
                return
            cand_X = cand_X[feas]
            cand_Y = cand_Y[feas]
            cand_C = cand_C[feas]

        if len(cand_Y) == 0:
            self.archive_X = np.zeros((0, self.dim))
            self.archive_Y = np.zeros((0, self.n_objectives))
            self.archive_C = self._empty_constraints(0)
            return

        fronts = self._fast_nondominated_sort(cand_Y)
        if not fronts or len(fronts[0]) == 0:
            self.archive_X = np.zeros((0, self.dim))
            self.archive_Y = np.zeros((0, self.n_objectives))
            self.archive_C = self._empty_constraints(0)
            return

        nd_idx = np.array(fronts[0], dtype=int)

        new_X = cand_X[nd_idx].copy()
        new_Y = cand_Y[nd_idx].copy()
        new_C = cand_C[nd_idx].copy()

        # remove duplicates
        if len(new_X) > 1:
            rounded = np.round(new_X, decimals=12)
            _, uniq_idx = np.unique(rounded, axis=0, return_index=True)
            uniq_idx = np.sort(uniq_idx)
            new_X = new_X[uniq_idx]
            new_Y = new_Y[uniq_idx]
            new_C = new_C[uniq_idx]

        # truncate by crowding distance
        if len(new_X) > self.archive_max_size:
            idx_all = list(range(len(new_Y)))
            d = self._crowding_distance(new_Y, idx_all)
            sorted_idx = sorted(idx_all, key=lambda idx: d[idx], reverse=True)
            keep = np.array(sorted_idx[:self.archive_max_size], dtype=int)
            new_X = new_X[keep]
            new_Y = new_Y[keep]
            new_C = new_C[keep]

        self.archive_X = new_X
        self.archive_Y = new_Y
        self.archive_C = new_C if self.n_constraints > 0 else self._empty_constraints(len(new_X))

    def _select_leader_from_archive(self) -> np.ndarray:
        """
        Select one leader from archive.
        Prefer sparse-region solutions using crowding distance.
        """
        if self.archive_X is None or self.archive_Y is None or len(self.archive_X) == 0:
            fronts = self._fast_nondominated_sort(self.population_Y)
            if fronts and len(fronts[0]) > 0:
                idx = np.random.choice(fronts[0])
                return self.population_X[idx].copy()
            best_idx = int(np.argmax(np.sum(self.population_Y, axis=1)))
            return self.population_X[best_idx].copy()

        nA = len(self.archive_X)
        if nA == 1 or self.leader_select_mode == "uniform":
            idx = np.random.randint(nA)
            return self.archive_X[idx].copy()

        idx_all = list(range(nA))
        d = self._crowding_distance(self.archive_Y, idx_all)
        cd = np.array([d[i] for i in idx_all], dtype=float)

        finite_mask = np.isfinite(cd)
        if np.any(finite_mask):
            finite_max = np.max(cd[finite_mask])
            cd = np.where(np.isinf(cd), finite_max + 1.0, cd)
        else:
            cd = np.ones_like(cd)

        probs = cd + 1e-12
        probs = probs / np.sum(probs)
        idx = np.random.choice(np.arange(nA), p=probs)
        return self.archive_X[idx].copy()
    
    def _select_by_nondom_crowding(self, X_pool: np.ndarray, Y_pool: np.ndarray, k: int, C_pool: Optional[np.ndarray] = None):
        C_pool = self._empty_constraints(len(X_pool)) if C_pool is None else C_pool
        if self.n_constraints > 0:
            feasible_mask = self._get_feasible_mask(C_pool)
            selected_idx: List[int] = []
            feasible_idx = np.where(feasible_mask)[0]
            infeasible_idx = np.where(~feasible_mask)[0]
            if len(feasible_idx) > 0:
                fronts = self._fast_nondominated_sort(Y_pool[feasible_idx])
                for front_local in fronts:
                    front = feasible_idx[np.array(front_local, dtype=int)].tolist()
                    if len(selected_idx) + len(front) <= k:
                        selected_idx.extend(front)
                    else:
                        d = self._crowding_distance(Y_pool, front)
                        sorted_front = sorted(front, key=lambda idx: d[idx], reverse=True)
                        selected_idx.extend(sorted_front[: k - len(selected_idx)])
                        break
            if len(selected_idx) < k and len(infeasible_idx) > 0:
                viol = self._constraint_violation_scalar(C_pool[infeasible_idx])
                order = np.argsort(viol)
                need = k - len(selected_idx)
                selected_idx.extend(infeasible_idx[order[:need]].tolist())
            chosen = np.array(selected_idx[:k], dtype=int)
        else:
            fronts = self._fast_nondominated_sort(Y_pool)
            chosen = []
            for front in fronts:
                if len(chosen) + len(front) <= k:
                    chosen.extend(front)
                else:
                    d = self._crowding_distance(Y_pool, front)
                    sorted_front = sorted(front, key=lambda idx: d[idx], reverse=True)
                    chosen.extend(sorted_front[: k - len(chosen)])
                    break
            chosen = np.array(chosen[:k], dtype=int)
        return X_pool[chosen], Y_pool[chosen], C_pool[chosen]

    def _beta_schedule(self, it: int, beta0: Optional[float] = None) -> float:
        beta0 = self.ucb_beta if beta0 is None else float(beta0)
        strategy = self.ucb_beta_kwargs.get("beta_strategy", "scale_decay")
        lam = float(self.ucb_beta_kwargs.get("beta_lam", 0.85))
        if strategy == "fixed":
            return beta0
        if strategy == "scale_decay":
            return beta0 * (lam ** it)
        if strategy == "exp_decay":
            return beta0 * np.exp(-lam * it)
        if strategy == "inv_decay":
            return beta0 / (1.0 + lam * it)
        return beta0

    def _polynomial_mutation(self, x: np.ndarray) -> np.ndarray:
        y = x.copy()
        xl = self.bounds[:, 0]
        xu = self.bounds[:, 1]
        eta = self.mutation_eta

        for i in range(self.dim):
            if np.random.rand() > self.mutation_prob:
                continue
            if xu[i] <= xl[i]:
                continue
            delta1 = (y[i] - xl[i]) / (xu[i] - xl[i])
            delta2 = (xu[i] - y[i]) / (xu[i] - xl[i])
            rnd = np.random.rand()
            mut_pow = 1.0 / (eta + 1.0)
            if rnd <= 0.5:
                xy = 1.0 - delta1
                val = 2.0 * rnd + (1.0 - 2.0 * rnd) * (xy ** (eta + 1.0))
                deltaq = val ** mut_pow - 1.0
            else:
                xy = 1.0 - delta2
                val = 2.0 * (1.0 - rnd) + 2.0 * (rnd - 0.5) * (xy ** (eta + 1.0))
                deltaq = 1.0 - val ** mut_pow
            y[i] = y[i] + deltaq * (xu[i] - xl[i])
            y[i] = min(max(y[i], xl[i]), xu[i])
        return y

    def _sbx_one_child(self, parent1: np.ndarray, parent2: np.ndarray) -> np.ndarray:
        c1 = parent1.copy()
        xl = self.bounds[:, 0]
        xu = self.bounds[:, 1]
        eta = self.crossover_eta
        eps = 1e-14

        for i in range(self.dim):
            if np.random.rand() > self.crossover_prob:
                continue
            x1, x2 = parent1[i], parent2[i]
            if abs(x1 - x2) <= eps:
                continue
            if x1 > x2:
                x1, x2 = x2, x1
            rand = np.random.rand()

            beta = 1.0 + 2.0 * (x1 - xl[i]) / (x2 - x1)
            alpha = 2.0 - beta ** (-(eta + 1.0))
            if rand <= 1.0 / alpha:
                betaq = (rand * alpha) ** (1.0 / (eta + 1.0))
            else:
                betaq = (1.0 / (2.0 - rand * alpha)) ** (1.0 / (eta + 1.0))
            child_a = 0.5 * ((x1 + x2) - betaq * (x2 - x1))

            beta = 1.0 + 2.0 * (xu[i] - x2) / (x2 - x1)
            alpha = 2.0 - beta ** (-(eta + 1.0))
            if rand <= 1.0 / alpha:
                betaq = (rand * alpha) ** (1.0 / (eta + 1.0))
            else:
                betaq = (1.0 / (2.0 - rand * alpha)) ** (1.0 / (eta + 1.0))
            child_b = 0.5 * ((x1 + x2) + betaq * (x2 - x1))

            child_a = min(max(child_a, xl[i]), xu[i])
            child_b = min(max(child_b, xl[i]), xu[i])
            c1[i] = child_a if np.random.rand() < 0.5 else child_b
        return c1

    def _is_better(self, a: np.ndarray, b: np.ndarray) -> bool:
        return self._dominates(a, b)

    def _is_equal_quality(self, a: np.ndarray, b: np.ndarray) -> bool:
        # kept for compatibility with original naming;
        # mathematically this means mutually non-dominated / incomparable
        return self._incomparable(a, b)

    def _best_update(self):
        if self.pbest is None or self.pbest_Y is None:
            self.pbest = self.population_X.copy()
            self.pbest_Y = self.population_Y.copy()
            self.pbest_C = self.population_C.copy() if self.population_C is not None else self._empty_constraints(self.pop_size)

        if not hasattr(self, 'pbest_C') or self.pbest_C is None:
            self.pbest_C = self.population_C.copy() if self.population_C is not None else self._empty_constraints(self.pop_size)

        for i in range(self.pop_size):
            cmp = self._compare_solution_quality(self.population_Y[i], None if self.population_C is None else self.population_C[i], self.pbest_Y[i], None if self.pbest_C is None else self.pbest_C[i])
            if cmp > 0:
                self.pbest[i] = self.population_X[i].copy()
                self.pbest_Y[i] = self.population_Y[i].copy()
                self.pbest_C[i] = self.population_C[i].copy() if self.population_C is not None else self._empty_constraints(1)[0]
            elif cmp == 0 and np.random.rand() < self.incomparable_update_prob:
                self.pbest[i] = self.population_X[i].copy()
                self.pbest_Y[i] = self.population_Y[i].copy()
                self.pbest_C[i] = self.population_C[i].copy() if self.population_C is not None else self._empty_constraints(1)[0]

        self._update_archive()
        self.gbest = self._select_leader_from_archive()

    def _pso_one_child(self, i: int, x: np.ndarray) -> np.ndarray:
        low = self.bounds[:, 0]
        high = self.bounds[:, 1]

        if self.velocity is None:
            velocity_range = (high - low) * 0.2
            self.velocity = np.random.uniform(
                -velocity_range, velocity_range,
                size=(self.pop_size, self.dim)
            )

        if self.pbest is None or self.pbest_Y is None:
            self.pbest = self.population_X.copy()
            self.pbest_Y = self.population_Y.copy()

        if self.archive_X is None or self.archive_Y is None:
            self._update_archive()

        if self.archive_X is None or len(self.archive_X) == 0:
            self._best_update()

        # each particle selects its own leader from archive
        leader = self._select_leader_from_archive()

        r1 = np.random.rand(self.dim)
        r2 = np.random.rand(self.dim)
        self.velocity[i] = (
            self.w * self.velocity[i]
            + self.c1 * r1 * (self.pbest[i] - x)
            + self.c2 * r2 * (leader - x)
        )

        v_max = (high - low) * 0.2
        self.velocity[i] = np.clip(self.velocity[i], -v_max, v_max)

        y = x + self.velocity[i]
        return np.clip(y, low, high)

    def _create_offspring(self) -> np.ndarray:
        cand = []
        self._best_update()
        for i, x in enumerate(self.population_X):
            for _ in range(self.m1):
                cand.append(self._polynomial_mutation(x))
            for _ in range(self.m2):
                partner_idx = np.random.randint(0, self.pop_size)
                while self.pop_size > 1 and partner_idx == i:
                    partner_idx = np.random.randint(0, self.pop_size)
                cand.append(self._sbx_one_child(x, self.population_X[partner_idx]))
            for _ in range(self.m3):
                cand.append(self._pso_one_child(i, x))
        cand = np.asarray(cand, dtype=float)
        if self.verbose:
            print(f"候选子代总数: {len(cand)} (mutation={self.m1*self.pop_size}, crossover={self.m2*self.pop_size}, pso={self.m3*self.pop_size})")
        return cand

    def _filter_by_ucb_nondocrowd(self, X_candidates: np.ndarray, beta: float, fold: int=1) -> np.ndarray:
        if self.model is None:
            idx = np.random.choice(len(X_candidates), size=self.evals_per_gen, replace=False)
            return X_candidates[idx]

        X_t = self._to_torch(X_candidates)
        means, stds = [], []
        with torch.no_grad():
            bs = 1024
            for start in range(0, len(X_t), bs):
                post = self.model.posterior(X_t[start:start + bs])
                means.append(post.mean)
                stds.append(torch.sqrt(torch.clamp(post.variance, min=1e-16)))
        mean = torch.cat(means, dim=0)
        std = torch.cat(stds, dim=0)
        ucb_np = self._from_torch(mean + beta * std)

        if self.n_constraints > 0:
            feas_prob = self._predict_feasibility_probability(X_candidates)
            candidate_k = min(len(X_candidates), max(fold*self.evals_per_gen, 3*self.evals_per_gen))
            order = np.argsort(-feas_prob)
            pre_idx = order[:candidate_k]
            X_pre = X_candidates[pre_idx]
            Y_pre = ucb_np[pre_idx]
            C_pre = self._predict_constraint_means(X_pre)
            X_sel, _, _ = self._select_by_nondom_crowding(X_pre, Y_pre, fold*self.evals_per_gen, C_pre)
            return X_sel
        X_sel, _, _ = self._select_by_nondom_crowding(X_candidates, ucb_np, fold*self.evals_per_gen)
        return X_sel
    
    def _filter_by_ehvi(self, X_candidates: np.ndarray) -> np.ndarray:
        """
        filter offspring using EHVI acquisition.
        returns top-k candidates with highest EHVI values.
        """
        if self.model is None:
            idx = np.random.choice(len(X_candidates), self.evals_per_gen, replace=False)
            return X_candidates[idx, :]
        try:
            acq = self._build_acquisition()

            # Evaluate acquisition on candidates
            X_t = self._to_torch(X_candidates)
            with torch.no_grad():
                # EHVI expects input shape (batch_shape, 1, d)
                vals = acq(X_t.unsqueeze(1)).view(-1)
            scores = self._from_torch(vals)
            if self.n_constraints > 0:
                feas_prob = self._predict_feasibility_probability(X_candidates)
                scores = scores * feas_prob
            top_idx = np.argsort(-scores)[:self.evals_per_gen]
            return X_candidates[top_idx, :]
        except Exception as e:
            if self.verbose:
                print(f"Acquisition evaluation failed: {e}")
            return self._filter_by_ucb_nondocrowd(X_candidates, beta=self.ucb_beta)
    
    def _candidate_reference_set_for_acquisition(self) -> np.ndarray:
        """Return a stable history subset for EHVI partitioning."""
        Y_hist = self.history_Y
        if len(Y_hist) == 0:
            return np.zeros((0, self.n_objectives), dtype=float)

        if self.n_constraints > 0 and len(self.history_C) > 0:
            feas = self._get_feasible_mask(self.history_C)
            if np.any(feas):
                return Y_hist[feas]

            # No feasible history yet: fall back to least-violating rows instead of all history.
            viol = self._constraint_violation_scalar(self.history_C)
            order = np.argsort(viol)
            keep = min(max(5, self.evals_per_gen), len(order))
            return Y_hist[order[:keep]]

        return Y_hist

    def _build_acquisition(self):
        """
        Build the EHVI acquisition function.
        """
        if self.model is None:
            raise RuntimeError("Surrogate model not built yet")

        # reference point (torch)
        ref_point_t = self._to_torch(self.ref_point)

        # pareto Y from history (torch) -- use internal maximization coords
        Y_hist = self._candidate_reference_set_for_acquisition()
        Y_t = self._to_torch(Y_hist)
        pareto_mask = is_non_dominated(Y_t)
        pareto_Y = Y_t[pareto_mask]

        if pareto_Y.shape[0] == 0:
            # if no pareto yet fallback to using history min
            pareto_Y = Y_t

        # build partitioning from pareto
        if pareto_Y.shape[0] == 0:
            pareto_Y = Y_t
        try:
            partitioning = FastNondominatedPartitioning(ref_point=ref_point_t, Y=pareto_Y)
            acq = ExpectedHypervolumeImprovement(
                model=self.model,
                ref_point=ref_point_t,
                partitioning=partitioning,
            )
        except Exception as e:
            # fallback to mu+ksigma scoring
            if self.verbose:
                print(f"EHVI partitioning failed: {e}")
            return None

        return acq
    def _set_reference_point(self):
        if self.ref_point is not None:
            self.ref_point = self.ref_point if self.maximize else -self.ref_point
            if self.verbose:
                print(f"内部参考点: {self.ref_point}")
            return
        Y_hist = self.history_Y
        if self.n_constraints > 0 and len(self.history_C) > 0:
            feas = self._get_feasible_mask(self.history_C)
            if np.any(feas):
                Y_hist = Y_hist[feas]
        worst = np.min(Y_hist, axis=0) if len(Y_hist) > 0 else np.zeros(self.n_objectives)
        self.ref_point = worst - 0.1 * np.maximum(np.abs(worst), 1.0)
        if self.verbose:
            print(f"自动内部参考点: {self.ref_point}")

    def _calculate_hypervolume(self, Y: np.ndarray, C: Optional[np.ndarray] = None) -> float:
        if self.hv_calc is None:
            self.hv_calc = Hypervolume(ref_point=self._to_torch(self.ref_point))
        if self.n_constraints > 0 and C is not None and C.size > 0:
            feas = self._get_feasible_mask(C)
            Y = Y[feas]
        if len(Y) == 0:
            return 0.0
        Y_t = self._to_torch(Y)
        nd = is_non_dominated(Y_t)
        pareto = Y_t[nd]
        if pareto.numel() == 0:
            return 0.0
        return float(self.hv_calc.compute(pareto))
    
    # --------------------
    # main loop
    # --------------------
    def optimize(self):
        t0 = time.time()
        if self.verbose:
            print("=== Running MG-GPO-like optimizer ===")
            print(f"n_objectives={self.n_objectives}, dim={self.dim}, pop_size={self.pop_size}")
            print(f"offspring setup: m1={self.m1}, m2={self.m2}, m3={self.m3}")
            print(f"direction={'maximize' if self.maximize else 'minimize'}")
            print(f"device={self.device}")

        # 1) initial population
        self._initialize_population()

        # 2) set reference point
        self._set_reference_point()

        # 3) track initial hypervolume
        self.hv_calc = Hypervolume(ref_point=self._to_torch(self.ref_point))
        self.hypervolume_history = [self._calculate_hypervolume(self.history_Y, self.history_C)]

        for gen in range(self.n_generations):
            gen_start = time.time()
            if self.verbose:
                print(f"=== Generation {gen + 1}/{self.n_generations} ===")

            # fit surrogate with selected population
            X_train, Y_train, C_train = self._get_training_data()
            if self.verbose:
                print(f"GP 训练数据量: {len(X_train)}")
            self._fit_surrogate(X_train, Y_train, C_train)

            # b) create offspring
            offspring = self._create_offspring()
            
            # select high score offspring for true evaluation by acquisition (ucb / EHVI / qEHVI) 
            if self.acq_mode == "ehvi":
                x_eval = self._filter_by_ehvi(offspring)
            elif self.acq_mode == "ucb":  # ucb + nondom crowding
                beta = self._beta_schedule(gen, self.ucb_beta)
                x_eval = self._filter_by_ucb_nondocrowd(offspring, beta=beta)
            elif self.acq_mode == "combine":
                beta = self._beta_schedule(gen, self.ucb_beta)
                x_eval0 = self._filter_by_ucb_nondocrowd(offspring, beta=beta, fold=2)
                x_eval = self._filter_by_ehvi(x_eval0)
            eval_details = self._evaluate_batch(x_eval)
            y_eval = eval_details["objectives_internal"]
            c_eval = eval_details["constraints"]

            # Elite update G_n <- select from G_{n-1} union F_n
            pool_X = np.vstack([self.population_X, x_eval])
            pool_Y = np.vstack([self.population_Y, y_eval])
            pool_C = np.vstack([self.population_C, c_eval]) if self.n_constraints > 0 else self._empty_constraints(len(pool_X))
            self.population_X, self.population_Y, self.population_C = self._select_by_nondom_crowding(pool_X, pool_Y, self.pop_size, pool_C)
            if self.n_constraints > 0:
                self.population_feasible = self._get_feasible_mask(self.population_C)

            # update history
            self.history_X = np.vstack([self.history_X, x_eval])
            self.history_Y = np.vstack([self.history_Y, y_eval])
            if self.n_constraints > 0:
                self.history_C = np.vstack([self.history_C, c_eval])
            self.history_status.extend(eval_details["status"])
            self.history_feasible = np.concatenate([self.history_feasible, eval_details["feasible"]])
            self.history_raw.extend(eval_details["raw"])
            hv = self._calculate_hypervolume(self.history_Y, self.history_C)
            self.hypervolume_history.append(hv)

            if self.verbose:
                disp_y = y_eval if self.maximize else -y_eval
                feas_count = int(np.sum(eval_details["feasible"])) if self.n_constraints > 0 else len(x_eval)
                print(
                    f"Gen {gen + 1}: 真实评估={len(x_eval)}, 本代可行点={feas_count}, "
                    f"sample_y={disp_y[0] if len(disp_y) else 'N/A'}, HV={hv:.6f}, "
                    f"time={time.time() - gen_start:.2f}s"
                )

        if self.verbose:
            print(f"完成，总时间 {time.time() - t0:.2f}s")

    def _get_display_objectives(self, Y_np: np.ndarray) -> np.ndarray:
        """Convert internal maximization objectives back to user-facing orientation."""
        return Y_np if self.maximize else -Y_np

    def _get_feasible_pareto_data(
        self,
    ) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]:
        """Return feasible non-dominated design points, objectives, and constraints."""
        if len(self.history_Y) == 0:
            empty_X = np.zeros((0, self.dim))
            empty_Y = np.zeros((0, self.n_objectives))
            empty_C = np.zeros((0, self.n_constraints)) if self.has_constraints else None
            return empty_X, empty_Y, empty_C

        if self.has_constraints:
            feasible_mask = self._get_feasible_mask(self.history_C)
            feasible_X = self.history_X[feasible_mask]
            feasible_Y = self.history_Y[feasible_mask]
            feasible_C = self.history_C[feasible_mask]
        else:
            feasible_X = self.history_X
            feasible_Y = self.history_Y
            feasible_C = None

        if len(feasible_Y) == 0:
            empty_X = np.zeros((0, self.dim))
            empty_Y = np.zeros((0, self.n_objectives))
            empty_C = np.zeros((0, self.n_constraints)) if self.has_constraints else None
            return empty_X, empty_Y, empty_C

        pareto_mask = is_non_dominated(self._to_torch(feasible_Y)).cpu().numpy()
        pareto_X = feasible_X[pareto_mask]
        pareto_Y = feasible_Y[pareto_mask]
        pareto_C = feasible_C[pareto_mask] if feasible_C is not None else None
        return pareto_X, pareto_Y, pareto_C

    def get_pareto_front(self) -> Tuple[np.ndarray, np.ndarray]:
        """Get feasible Pareto front and corresponding solutions."""
        pareto_X, pareto_Y, _ = self._get_feasible_pareto_data()
        return pareto_X, self._get_display_objectives(pareto_Y)

    def plot_convergence(self, path: Optional[str] = None):
        """Plot convergence metrics in the same style as ConsMOBO."""
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

        _, pareto_Y = self.get_pareto_front()
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
            path = f"save/mggpo_{timestamp}.png"
        path_obj = Path(path)
        if path_obj.parent != Path("."):
            path_obj.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(path)
        plt.show()

    def save_history(self, path: Optional[str] = None):
        """
        Save optimization history, hypervolume history, Pareto front, and metadata.
        """
        if path is None:
            timestamp = datetime.now().strftime("%Y%m%d%H%M")
            prefix = "ConsMGGPO" if self.has_constraints else "MGGPO"
            path = f"save/{prefix}_{timestamp}.dat"

        path_obj = Path(path)
        if path_obj.parent != Path("."):
            path_obj.parent.mkdir(parents=True, exist_ok=True)

        Y_save = self._get_display_objectives(self.history_Y)
        data_cols = [self.history_X, Y_save]
        if self.has_constraints:
            feasible_col = self._get_feasible_mask().astype(float).reshape(-1, 1)
            data_cols.extend([self.history_C, feasible_col])
        data = np.hstack(data_cols)
        np.savetxt(path, data, fmt="%.6f")

        hv_path = path.replace('.dat', '_hypervolume.dat')
        np.savetxt(hv_path, np.asarray(self.hypervolume_history), fmt="%.6f")

        Xp, Yp = self.get_pareto_front()
        _, _, Cp = self._get_feasible_pareto_data()
        pareto_path = path.replace(".dat", "_pareto.dat")
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

        meta_path = path.replace('.dat', '_meta.txt')
        with open(meta_path, 'w') as f:
            f.write(f"bounds: {self.bounds}\n")
            f.write(f"n_objectives: {self.n_objectives}\n")
            f.write(f"kernel_type: {self.kernel_type}\n")
            f.write(f"gp_restarts: {self.gp_restarts}\n")
            f.write(f"acq_mode: {self.acq_mode}\n")
            f.write(f"ref_point: {self.ref_point}\n")
            f.write(f"maximize: {self.maximize}\n")
            f.write(f"pop_size: {self.pop_size}\n")
            f.write(f"evals_per_gen: {self.evals_per_gen}\n")
            f.write(f"n_generations: {self.n_generations}\n")
            f.write(f"m1: {self.m1}\n")
            f.write(f"m2: {self.m2}\n")
            f.write(f"m3: {self.m3}\n")
            f.write(f"ucb_beta: {self.ucb_beta}\n")
            f.write(f"ucb_beta_kwargs: {self.ucb_beta_kwargs}\n")
            f.write(f"use_all_history_for_gp: {self.use_all_history_for_gp}\n")
            f.write(f"gp_history_max: {self.gp_history_max}\n")
            f.write(f"mutation_eta: {self.mutation_eta}\n")
            f.write(f"crossover_eta: {self.crossover_eta}\n")
            f.write(f"mutation_prob: {self.mutation_prob}\n")
            f.write(f"crossover_prob: {self.crossover_prob}\n")
            f.write(f"w: {self.w}\n")
            f.write(f"c1: {self.c1}\n")
            f.write(f"c2: {self.c2}\n")
            f.write(f"constraint_bounds: {self.constraint_bounds}\n")
            f.write(f"n_constraints: {self.n_constraints}\n")
            if self.has_constraints:
                f.write(f"feasible_evaluations: {int(np.sum(self._get_feasible_mask()))}\n")


ConsMGGPOOptimizer = MultiObjectiveMGGPO


if __name__ == "__main__":
    def constrained_zdt1(X):
        """Constrained ZDT1-like test function for minimization."""
        if X.ndim == 1:
            X = X.reshape(1, -1)

        n = X.shape[1]
        f1 = X[:, 0]
        g = 1 + 9 / (n - 1) * np.sum(X[:, 1:], axis=1)
        h = 1 - np.sqrt(f1 / g)
        f2 = g * h

        c1 = X[:, 0] + X[:, 1]  # c1 <= 0.8
        c2 = X[:, 0]            # c2 >= 0.2

        objectives = np.column_stack([f1, f2])
        constraints = np.column_stack([c1, c2])
        return objectives, constraints

    dim = 30
    bounds = np.tile([0.0, 1.0], (dim, 1))

    opt = MultiObjectiveMGGPO(
        func=constrained_zdt1,
        bounds=bounds,
        n_objectives=2,
        constraint_bounds=[
            (None, 0.8),
            (0.2, None),
        ],
        kernel_type="rbf",
        gp_restarts=5,
        pop_size=80,
        acq_mode="ucb",  # 'ehvi', 'ucb', 'combine'
        ref_point=np.array([1, 1]),
        ucb_beta=3.0,
        ucb_beta_kwargs={"beta_strategy": "scale_decay", "beta_lam": 0.85},
        m1=20,
        m2=20,
        m3=10,
        evals_per_gen=80,
        n_generations=50,
        use_all_history_for_gp=False,
        gp_history_max=160,
        mutation_eta=20.0,
        crossover_eta=20.0,
        mutation_prob=0.5,
        crossover_prob=1,
        w=0.4,   # inertia weight
        c1=3.0,  # cognitive coefficient
        c2=3.0,  # social coefficient
        maximize=False,
        random_state=100,
        verbose=True,
    )
    opt.optimize()
    opt.plot_convergence()
    # opt.save_history()
