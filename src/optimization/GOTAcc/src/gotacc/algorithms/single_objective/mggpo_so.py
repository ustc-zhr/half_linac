import os
import time
import warnings
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import numpy as np
import torch
from botorch.fit import fit_gpytorch_mll
from botorch.models import ModelListGP, SingleTaskGP
from botorch.models.transforms import Normalize
from gpytorch.constraints import Interval
from gpytorch.kernels import MaternKernel, RBFKernel, ScaleKernel
from gpytorch.mlls import ExactMarginalLogLikelihood, SumMarginalLogLikelihood
from gpytorch.priors import GammaPrior
from scipy.stats import norm, qmc
from torch import Tensor

warnings.filterwarnings("ignore", message="To copy construct from a tensor")
torch.set_default_dtype(torch.float64)


class MGGPOSOOptimizer:
    """
    Single-objective MG-GPO style optimizer.

    This keeps the original candidate-generation structure from multi-objective
    MG-GPO:
    - mutation
    - SBX crossover
    - PSO-assisted offspring

    The multi-objective pieces are replaced with scalar counterparts:
    - scalar GP surrogate
    - scalar acquisition scoring (UCB / EI / PI)
    - elite selection by scalar objective plus optional constraint feasibility
    """

    def __init__(
        self,
        func: Callable,
        bounds: np.ndarray,
        n_objectives: int = 1,
        n_constraints: int = 0,
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
        w: float = 0.4,
        c1: float = 1.0,
        c2: float = 2.0,
        maximize: bool = False,
        device: str = "cpu",
        dtype: Optional[torch.dtype] = None,
        random_state: int = 0,
        verbose: bool = True,
        archive_size: Optional[int] = None,
    ):
        self.func = func
        self.bounds = np.asarray(bounds, dtype=float)
        self.dim = self.bounds.shape[0]
        if int(n_objectives) != 1:
            raise ValueError(
                f"MGGPOSOOptimizer is single-objective only, got n_objectives={n_objectives}."
            )
        self.n_objectives = 1
        self.n_constraints = int(n_constraints)
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

        self.acq_mode = str(acq_mode or "ucb").strip().lower()
        if self.acq_mode in {"ehvi", "qehvi", "qnehvi"}:
            self.acq_mode = "ei"
        self.ucb_beta = float(ucb_beta)
        self.ucb_beta_kwargs = ucb_beta_kwargs if ucb_beta_kwargs is not None else {}
        self.ref_point = None if ref_point is None else np.asarray(ref_point, dtype=float)

        self.model = None
        self.mll = None
        self.constraint_model = None
        self.constraint_mll = None

        self.history_X = np.zeros((0, self.dim))
        self.history_Y = np.zeros((0, 1))
        self.history_C = np.zeros((0, self.n_constraints)) if self.n_constraints > 0 else np.zeros((0, 0))
        self.history_status: List[str] = []
        self.history_feasible = np.zeros((0,), dtype=bool)
        self.history_raw: List[Dict[str, Any]] = []

        self.population_X = None
        self.population_Y = None
        self.population_C = None
        self.population_status = None
        self.population_feasible = None

        self.best_history: List[float] = []
        self.best_x_ = None
        self.best_y_ = None

        self.velocity = None
        self.pbest = None
        self.pbest_Y = None
        self.pbest_C = None
        self.gbest = None

        self.archive_X = None
        self.archive_Y = None
        self.archive_C = None
        if archive_size is None:
            self.archive_max_size = max(50, self.pop_size)
        else:
            self.archive_max_size = max(1, int(archive_size))
        self.leader_select_mode = "rank"
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

    def _is_feasible(self, c: Optional[np.ndarray]) -> bool:
        if self.n_constraints == 0:
            return True
        if c is None:
            return False
        c = np.asarray(c, dtype=float).reshape(-1)
        return bool(np.all(c <= 0))

    def _constraint_violation_scalar(self, C: np.ndarray) -> np.ndarray:
        if self.n_constraints == 0 or C.size == 0:
            return np.zeros(C.shape[0] if getattr(C, "ndim", 0) > 0 else 0)
        return np.sum(np.maximum(C, 0.0), axis=1)

    def _objective_scalar(self, y: np.ndarray) -> float:
        arr = np.asarray(y, dtype=float).reshape(-1)
        if arr.size != 1:
            raise ValueError(f"Single-objective optimizer expects scalar objective, got shape {arr.shape}.")
        return float(arr[0])

    def _compare_solution_quality(
        self,
        y_a: np.ndarray,
        c_a: Optional[np.ndarray],
        y_b: np.ndarray,
        c_b: Optional[np.ndarray],
    ) -> int:
        fa = self._is_feasible(c_a)
        fb = self._is_feasible(c_b)
        if fa and not fb:
            return 1
        if fb and not fa:
            return -1
        if fa and fb:
            ya = self._objective_scalar(y_a)
            yb = self._objective_scalar(y_b)
            if ya > yb + 1e-15:
                return 1
            if yb > ya + 1e-15:
                return -1
            return 0
        va = float(np.sum(np.maximum(np.asarray(c_a, dtype=float).reshape(-1), 0.0)))
        vb = float(np.sum(np.maximum(np.asarray(c_b, dtype=float).reshape(-1), 0.0)))
        if va < vb - 1e-15:
            return 1
        if vb < va - 1e-15:
            return -1
        return 0

    def _parse_objective_array(self, obj_raw: Any, n: int) -> np.ndarray:
        arr = np.asarray(obj_raw, dtype=float)
        if arr.ndim == 0:
            if n != 1:
                raise ValueError("Scalar objective output can only map to a single sample.")
            return arr.reshape(1, 1)
        if arr.ndim == 1:
            if arr.size == 1 and n == 1:
                return arr.reshape(1, 1)
            if arr.size == n:
                return arr.reshape(n, 1)
            raise ValueError(f"Cannot reshape objective output of shape {arr.shape} into ({n}, 1).")
        arr = arr.reshape(n, -1)
        if arr.shape[1] != 1:
            raise ValueError(
                f"Single-objective optimizer expects one objective column, got shape {arr.shape}."
            )
        return arr

    def _parse_constraint_array(self, cons_raw: Any, n: int) -> np.ndarray:
        if self.n_constraints == 0:
            return np.zeros((n, 0), dtype=float)
        if cons_raw is None:
            raise ValueError("n_constraints > 0 but evaluator output did not provide constraints.")
        arr = np.asarray(cons_raw, dtype=float)
        if arr.ndim == 0:
            if n != 1 or self.n_constraints != 1:
                raise ValueError(
                    f"Cannot reshape scalar constraint output into ({n}, {self.n_constraints})."
                )
            return arr.reshape(1, 1)
        if arr.ndim == 1:
            if arr.size == self.n_constraints and n == 1:
                return arr.reshape(1, self.n_constraints)
            if arr.size == n * self.n_constraints:
                return arr.reshape(n, self.n_constraints)
            raise ValueError(
                f"Cannot reshape constraint output of shape {arr.shape} into ({n}, {self.n_constraints})."
            )
        return arr.reshape(n, self.n_constraints)

    def _parse_eval_output(self, out: Any, n: int) -> Dict[str, Any]:
        if isinstance(out, dict):
            obj_raw = out["objective"] if "objective" in out else out.get("objectives", None)
            if obj_raw is None:
                raise KeyError("Structured evaluator output must provide 'objective' or 'objectives'.")
            obj = self._parse_objective_array(obj_raw, n)
            cons = self._parse_constraint_array(out.get("constraints", None), n)

            status = out.get("status", ["ok"] * n)
            if isinstance(status, np.ndarray):
                status = status.tolist()
            elif isinstance(status, str):
                status = [status] * n

            feasible = out.get("feasible", None)
            if feasible is None:
                feasible = np.all(cons <= 0, axis=1) if self.n_constraints > 0 else np.ones(n, dtype=bool)
            feasible = np.asarray(feasible, dtype=bool).reshape(n)

            raw = out.get("raw", [{} for _ in range(n)])
            if isinstance(raw, dict):
                raw = [raw]
            return {
                "objectives": obj,
                "constraints": cons,
                "status": list(status),
                "feasible": feasible,
                "raw": list(raw),
            }

        if self.n_constraints > 0 and isinstance(out, (tuple, list)) and len(out) == 2:
            obj_raw, cons_raw = out
            obj = self._parse_objective_array(obj_raw, n)
            cons = self._parse_constraint_array(cons_raw, n)
            feasible = np.all(cons <= 0, axis=1)
            return {
                "objectives": obj,
                "constraints": cons,
                "status": ["ok"] * n,
                "feasible": feasible,
                "raw": [{} for _ in range(n)],
            }

        obj = self._parse_objective_array(out, n)
        cons = self._empty_constraints(n)
        feasible = np.ones(n, dtype=bool)
        return {
            "objectives": obj,
            "constraints": cons,
            "status": ["ok"] * n,
            "feasible": feasible,
            "raw": [{} for _ in range(n)],
        }

    def _call_func_structured_one(self, x: np.ndarray) -> Dict[str, Any]:
        x = np.asarray(x, dtype=float).reshape(1, -1)
        try:
            out = self.func(x[0], return_details=True)
            return self._parse_eval_output(out, 1)
        except TypeError:
            out = self.func(x[0])
            return self._parse_eval_output(out, 1)
        except Exception:
            out = self.func(x[0])
            return self._parse_eval_output(out, 1)

    def _evaluate_batch(self, X: np.ndarray) -> Dict[str, Any]:
        if X.ndim == 1:
            X = X.reshape(1, -1)

        obj_rows = []
        con_rows = []
        status_rows: List[str] = []
        feasible_rows = []
        raw_rows: List[Dict[str, Any]] = []

        for row in X:
            details = self._call_func_structured_one(row)
            obj_rows.append(details["objectives"])
            con_rows.append(details["constraints"])
            status_rows.extend(details["status"])
            feasible_rows.append(details["feasible"])
            raw_rows.extend(details["raw"])

        Y = np.vstack(obj_rows).astype(float)
        C = np.vstack(con_rows).astype(float) if con_rows else self._empty_constraints(0)
        Y_internal = Y if self.maximize else -Y

        return {
            "objectives": Y,
            "objectives_internal": Y_internal,
            "constraints": C,
            "status": list(status_rows),
            "feasible": np.concatenate(feasible_rows) if feasible_rows else np.ones(0, dtype=bool),
            "raw": list(raw_rows),
        }

    def _sanitize_training_arrays(
        self,
        X_np: np.ndarray,
        Y_np: np.ndarray,
        C_np: Optional[np.ndarray] = None,
    ) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]:
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
            raise RuntimeError("No finite training rows available after NaN/Inf filtering.")
        return X_out, Y_out, C_out

    def _status_allows_gp(self, status: str) -> bool:
        return str(status) in {"ok", "physics_infeasible"}

    def _filter_training_rows_by_status(
        self,
        X_np: np.ndarray,
        Y_np: np.ndarray,
        C_np: Optional[np.ndarray] = None,
        status: Optional[List[str]] = None,
    ) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray], np.ndarray]:
        X_np = np.asarray(X_np, dtype=float)
        Y_np = np.asarray(Y_np, dtype=float)
        if status is None:
            status_arr = np.array(["ok"] * len(X_np), dtype=object)
        else:
            status_arr = np.asarray(status, dtype=object).reshape(-1)
            if len(status_arr) != len(X_np):
                raise ValueError(
                    f"status length mismatch: len(status)={len(status_arr)} vs len(X)={len(X_np)}"
                )

        keep_mask = np.array([self._status_allows_gp(s) for s in status_arr], dtype=bool)
        if not np.any(keep_mask):
            unique_status, counts = np.unique(status_arr.astype(str), return_counts=True)
            summary = ", ".join([f"{s}:{c}" for s, c in zip(unique_status, counts)])
            raise RuntimeError(
                "No GP-usable rows after status filtering. "
                f"Observed status counts: {summary}"
            )

        X_out = X_np[keep_mask]
        Y_out = Y_np[keep_mask]
        status_out = status_arr[keep_mask]

        C_out = None
        if C_np is not None:
            C_np = np.asarray(C_np, dtype=float)
            if C_np.size > 0:
                C_out = C_np[keep_mask]
            else:
                C_out = C_np.reshape(len(X_np), 0)[keep_mask]

        return X_out, Y_out, C_out, status_out

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
                    post = model_j.posterior(X_t[i : i + batch_size])
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
            for model_j in self.constraint_model.models:
                mu_list, std_list = [], []
                for i in range(0, len(X_t), batch_size):
                    post = model_j.posterior(X_t[i : i + batch_size])
                    mu_list.append(post.mean.view(-1))
                    std_list.append(torch.sqrt(post.variance.view(-1).clamp_min(1e-12)))
                mu = torch.cat(mu_list).cpu().numpy()
                std = torch.cat(std_list).cpu().numpy()
                z = (0.0 - mu) / np.maximum(std, 1e-12)
                probs.append(norm.cdf(z))
        probs = np.vstack(probs).T
        return np.prod(probs, axis=1)

    def _initialize_population(self):
        if self.dim < 50:
            sampler = qmc.LatinHypercube(d=self.dim, seed=self.random_state)
            sample = sampler.random(n=self.pop_size)
            X = qmc.scale(sample, self.bounds[:, 0], self.bounds[:, 1])
            method = "LHS"
        else:
            engine = torch.quasirandom.SobolEngine(
                dimension=self.dim, scramble=True, seed=self.random_state
            )
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

        velocity_range = (self.bounds[:, 1] - self.bounds[:, 0]) * 0.2
        self.velocity = np.random.uniform(-velocity_range, velocity_range, size=(self.pop_size, self.dim))
        self.pbest = self.population_X.copy()
        self.pbest_Y = self.population_Y.copy()
        self.pbest_C = self.population_C.copy() if self.population_C is not None else self._empty_constraints(self.pop_size)
        self._best_update()

        if self.verbose:
            print(f"初始化种群: {self.pop_size}, method={method}")

    def _evaluate_function(self, X: np.ndarray) -> np.ndarray:
        details = self._evaluate_batch(X)
        return details["objectives_internal"]

    def _get_training_data(self) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        if self.use_all_history_for_gp:
            X_train = self.history_X
            Y_train = self.history_Y
            C_train = self.history_C if self.n_constraints > 0 else self._empty_constraints(len(self.history_X))
            status_train = np.asarray(self.history_status, dtype=object)
        else:
            if self.gp_history_max is None:
                X_train = self.history_X
                Y_train = self.history_Y
                C_train = self.history_C if self.n_constraints > 0 else self._empty_constraints(len(self.history_X))
                status_train = np.asarray(self.history_status, dtype=object)
            else:
                n_keep = min(int(self.gp_history_max), len(self.history_X))
                X_hist = self.history_X[-n_keep:]
                Y_hist = self.history_Y[-n_keep:]
                C_hist = self.history_C[-n_keep:] if self.n_constraints > 0 else self._empty_constraints(n_keep)
                status_hist = np.asarray(self.history_status[-n_keep:], dtype=object)

                X_train = np.vstack([self.population_X, X_hist])
                Y_train = np.vstack([self.population_Y, Y_hist])
                C_train = (
                    np.vstack([self.population_C, C_hist])
                    if self.n_constraints > 0
                    else self._empty_constraints(len(X_train))
                )
                status_train = np.concatenate([np.asarray(self.population_status, dtype=object), status_hist])

        X_round = np.round(X_train, 12)
        _, unique_idx = np.unique(X_round, axis=0, return_index=True)
        unique_idx = np.sort(unique_idx)
        return X_train[unique_idx], Y_train[unique_idx], C_train[unique_idx], status_train[unique_idx]

    def _make_covar_module(self):
        if self.kernel_type == "rbf":
            base_kernel = RBFKernel(
                ard_num_dims=self.dim,
                lengthscale_prior=GammaPrior(3.0, 6.0),
                lengthscale_constraint=Interval(1e-3, 1e3),
            )
        else:
            base_kernel = MaternKernel(
                nu=2.5,
                ard_num_dims=self.dim,
                lengthscale_prior=GammaPrior(3.0, 6.0),
                lengthscale_constraint=Interval(1e-3, 1e3),
            )
        return ScaleKernel(
            base_kernel,
            outputscale_prior=GammaPrior(2.0, 0.15),
            outputscale_constraint=Interval(1e-6, 1e4),
        )

    def _fit_surrogate(
        self,
        X_np: np.ndarray,
        Y_np: np.ndarray,
        C_np: Optional[np.ndarray] = None,
    ):
        X_np, Y_np, C_np = self._sanitize_training_arrays(X_np, Y_np, C_np)
        train_x = self._to_torch(X_np)
        bounds_torch = self._bounds_tensor()

        train_y = self._to_torch(Y_np[:, :1])
        train_yvar = torch.full_like(train_y, 1e-6)
        self.model = SingleTaskGP(
            train_X=train_x,
            train_Y=train_y,
            train_Yvar=train_yvar,
            covar_module=self._make_covar_module(),
            input_transform=Normalize(d=self.dim, bounds=bounds_torch),
        ).to(self.device)
        self.model.train()
        self.mll = ExactMarginalLogLikelihood(self.model.likelihood, self.model)
        fit_gpytorch_mll(self.mll, max_attempts=self.gp_restarts, pick_best_of_all_attempts=True)
        self.model.eval()

        self.constraint_model = None
        self.constraint_mll = None
        if self.n_constraints > 0 and C_np is not None and C_np.size > 0:
            con_models = []
            for j in range(self.n_constraints):
                train_c = self._to_torch(C_np[:, j : j + 1])
                train_cvar = torch.full_like(train_c, 1e-6)
                model = SingleTaskGP(
                    train_X=train_x,
                    train_Y=train_c,
                    train_Yvar=train_cvar,
                    covar_module=self._make_covar_module(),
                    input_transform=Normalize(d=self.dim, bounds=bounds_torch),
                ).to(self.device)
                con_models.append(model)
            self.constraint_model = ModelListGP(*con_models)
            self.constraint_model.train()
            self.constraint_mll = SumMarginalLogLikelihood(self.constraint_model.likelihood, self.constraint_model)
            fit_gpytorch_mll(
                self.constraint_mll,
                max_attempts=self.gp_restarts,
                pick_best_of_all_attempts=True,
            )
            self.constraint_model.eval()
            for model_j in self.constraint_model.models:
                model_j.eval()

    def _rank_indices_by_quality(self, Y_pool: np.ndarray, C_pool: Optional[np.ndarray] = None) -> np.ndarray:
        Y_pool = np.asarray(Y_pool, dtype=float).reshape(-1, 1)
        C_pool = self._empty_constraints(len(Y_pool)) if C_pool is None else np.asarray(C_pool, dtype=float)
        if self.n_constraints == 0:
            return np.argsort(-Y_pool[:, 0]).astype(int)

        feasible_mask = np.all(C_pool <= 0, axis=1)
        feasible_idx = np.where(feasible_mask)[0]
        infeasible_idx = np.where(~feasible_mask)[0]

        ordered = []
        if len(feasible_idx) > 0:
            order_feasible = feasible_idx[np.argsort(-Y_pool[feasible_idx, 0])]
            ordered.append(order_feasible)
        if len(infeasible_idx) > 0:
            violation = self._constraint_violation_scalar(C_pool[infeasible_idx])
            order_infeasible = infeasible_idx[np.argsort(violation)]
            ordered.append(order_infeasible)
        if not ordered:
            return np.zeros((0,), dtype=int)
        return np.concatenate(ordered).astype(int)

    def _update_archive(self):
        if self.population_X is None or self.population_Y is None or len(self.population_X) == 0:
            return

        pop_C = self.population_C if self.population_C is not None else self._empty_constraints(len(self.population_X))
        if self.archive_X is None or self.archive_Y is None or len(self.archive_X) == 0:
            cand_X = self.population_X.copy()
            cand_Y = self.population_Y.copy()
            cand_C = pop_C.copy()
        else:
            archive_C = self.archive_C if self.archive_C is not None else self._empty_constraints(len(self.archive_X))
            cand_X = np.vstack([self.archive_X, self.population_X])
            cand_Y = np.vstack([self.archive_Y, self.population_Y])
            cand_C = np.vstack([archive_C, pop_C])

        order = self._rank_indices_by_quality(cand_Y, cand_C)
        cand_X = cand_X[order]
        cand_Y = cand_Y[order]
        cand_C = cand_C[order]

        if len(cand_X) > 1:
            rounded = np.round(cand_X, decimals=12)
            _, uniq_idx = np.unique(rounded, axis=0, return_index=True)
            uniq_idx = np.sort(uniq_idx)
            cand_X = cand_X[uniq_idx]
            cand_Y = cand_Y[uniq_idx]
            cand_C = cand_C[uniq_idx]

        keep = min(len(cand_X), self.archive_max_size)
        self.archive_X = cand_X[:keep].copy()
        self.archive_Y = cand_Y[:keep].copy()
        self.archive_C = cand_C[:keep].copy() if self.n_constraints > 0 else self._empty_constraints(keep)

    def _select_leader_from_archive(self) -> np.ndarray:
        if self.archive_X is None or self.archive_Y is None or len(self.archive_X) == 0:
            best_idx = int(self._rank_indices_by_quality(self.population_Y, self.population_C)[0])
            return self.population_X[best_idx].copy()

        n_archive = len(self.archive_X)
        if n_archive == 1 or self.leader_select_mode == "uniform":
            idx = np.random.randint(n_archive)
            return self.archive_X[idx].copy()

        order = self._rank_indices_by_quality(self.archive_Y, self.archive_C)
        ordered_X = self.archive_X[order]
        weights = 1.0 / np.arange(1, len(ordered_X) + 1, dtype=float)
        probs = weights / np.sum(weights)
        idx = np.random.choice(np.arange(len(ordered_X)), p=probs)
        return ordered_X[idx].copy()

    def _select_topk(
        self,
        X_pool: np.ndarray,
        Y_pool: np.ndarray,
        k: int,
        C_pool: Optional[np.ndarray] = None,
        return_indices: bool = False,
    ):
        C_pool = self._empty_constraints(len(X_pool)) if C_pool is None else np.asarray(C_pool, dtype=float)
        order = self._rank_indices_by_quality(Y_pool, C_pool)
        chosen = np.asarray(order[:k], dtype=int)
        if return_indices:
            return X_pool[chosen], Y_pool[chosen], C_pool[chosen], chosen
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
        return self._objective_scalar(a) > self._objective_scalar(b) + 1e-15

    def _is_equal_quality(self, a: np.ndarray, b: np.ndarray) -> bool:
        return abs(self._objective_scalar(a) - self._objective_scalar(b)) <= 1e-15

    def _best_update(self):
        if self.pbest is None or self.pbest_Y is None:
            self.pbest = self.population_X.copy()
            self.pbest_Y = self.population_Y.copy()
            self.pbest_C = self.population_C.copy() if self.population_C is not None else self._empty_constraints(self.pop_size)

        if self.pbest_C is None:
            self.pbest_C = self.population_C.copy() if self.population_C is not None else self._empty_constraints(self.pop_size)

        for i in range(self.pop_size):
            cmp = self._compare_solution_quality(
                self.population_Y[i],
                None if self.population_C is None else self.population_C[i],
                self.pbest_Y[i],
                None if self.pbest_C is None else self.pbest_C[i],
            )
            if cmp > 0:
                self.pbest[i] = self.population_X[i].copy()
                self.pbest_Y[i] = self.population_Y[i].copy()
                self.pbest_C[i] = (
                    self.population_C[i].copy()
                    if self.population_C is not None
                    else self._empty_constraints(1)[0]
                )
            elif cmp == 0 and np.random.rand() < self.incomparable_update_prob:
                self.pbest[i] = self.population_X[i].copy()
                self.pbest_Y[i] = self.population_Y[i].copy()
                self.pbest_C[i] = (
                    self.population_C[i].copy()
                    if self.population_C is not None
                    else self._empty_constraints(1)[0]
                )

        self._update_archive()
        self.gbest = self._select_leader_from_archive()

    def _pso_one_child(self, i: int, x: np.ndarray) -> np.ndarray:
        low = self.bounds[:, 0]
        high = self.bounds[:, 1]

        if self.velocity is None:
            velocity_range = (high - low) * 0.2
            self.velocity = np.random.uniform(-velocity_range, velocity_range, size=(self.pop_size, self.dim))

        if self.pbest is None or self.pbest_Y is None:
            self.pbest = self.population_X.copy()
            self.pbest_Y = self.population_Y.copy()
            self.pbest_C = self.population_C.copy() if self.population_C is not None else self._empty_constraints(self.pop_size)

        if self.archive_X is None or self.archive_Y is None:
            self._update_archive()

        if self.archive_X is None or len(self.archive_X) == 0:
            self._best_update()

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
        if self.m3 > 0:
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

        if not cand:
            return np.zeros((0, self.dim), dtype=float)

        cand = np.asarray(cand, dtype=float)
        if self.verbose:
            print(
                f"候选子代总数: {len(cand)} "
                f"(mutation={self.m1*self.pop_size}, crossover={self.m2*self.pop_size}, pso={self.m3*self.pop_size})"
            )
        return cand

    def _posterior_mean_std(self, X_candidates: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        X_t = self._to_torch(X_candidates)
        mu_list, std_list = [], []
        with torch.no_grad():
            batch_size = 1024
            for start in range(0, len(X_t), batch_size):
                post = self.model.posterior(X_t[start : start + batch_size])
                mu_list.append(post.mean.view(-1))
                std_list.append(torch.sqrt(torch.clamp(post.variance.view(-1), min=1e-16)))
        mu = torch.cat(mu_list).cpu().numpy()
        std = torch.cat(std_list).cpu().numpy()
        return mu, std

    def _best_internal_value(self) -> Optional[float]:
        if len(self.history_Y) == 0:
            return None
        if self.n_constraints > 0 and len(self.history_C) > 0:
            feas = np.all(self.history_C <= 0, axis=1)
            if np.any(feas):
                return float(np.max(self.history_Y[feas, 0]))
            return None
        return float(np.max(self.history_Y[:, 0]))

    def _normalize_scores(self, scores: np.ndarray) -> np.ndarray:
        scores = np.asarray(scores, dtype=float).reshape(-1)
        if scores.size == 0:
            return scores
        smin = float(np.min(scores))
        smax = float(np.max(scores))
        if smax <= smin + 1e-15:
            return np.ones_like(scores)
        return (scores - smin) / (smax - smin)

    def _score_candidates(self, X_candidates: np.ndarray, beta: float) -> np.ndarray:
        if self.model is None:
            return np.random.rand(len(X_candidates))

        mean, std = self._posterior_mean_std(X_candidates)
        ucb = mean + beta * std

        best_f = self._best_internal_value()
        if best_f is None:
            best_f = float(np.max(self.history_Y[:, 0])) if len(self.history_Y) > 0 else 0.0

        sigma = np.maximum(std, 1e-12)
        improvement = mean - best_f
        z = improvement / sigma
        ei = improvement * norm.cdf(z) + sigma * norm.pdf(z)
        ei = np.where(std <= 1e-12, np.maximum(improvement, 0.0), ei)
        pi = norm.cdf(z)

        mode = self.acq_mode
        if mode == "ei":
            scores = ei
        elif mode == "pi":
            scores = pi
        elif mode == "combine":
            scores = self._normalize_scores(ucb) + self._normalize_scores(ei)
        else:
            scores = ucb

        if self.n_constraints > 0 and self.constraint_model is not None:
            scores = scores * self._predict_feasibility_probability(X_candidates)
        return scores

    def _filter_candidates(self, X_candidates: np.ndarray, beta: float) -> np.ndarray:
        if len(X_candidates) == 0:
            return np.zeros((0, self.dim), dtype=float)

        rounded = np.round(X_candidates, 12)
        _, unique_idx = np.unique(rounded, axis=0, return_index=True)
        unique_idx = np.sort(unique_idx)
        X_candidates = X_candidates[unique_idx]

        if len(X_candidates) <= self.evals_per_gen:
            return X_candidates

        scores = self._score_candidates(X_candidates, beta=beta).reshape(-1, 1)
        predicted_C = (
            self._predict_constraint_means(X_candidates)
            if self.n_constraints > 0 and self.constraint_model is not None
            else self._empty_constraints(len(X_candidates))
        )
        X_sel, _, _ = self._select_topk(X_candidates, scores, self.evals_per_gen, predicted_C)
        return X_sel

    def _display_objectives(self, Y_internal: np.ndarray) -> np.ndarray:
        return Y_internal if self.maximize else -Y_internal

    def get_best_solution(self) -> Tuple[np.ndarray, float]:
        if len(self.history_X) == 0:
            raise RuntimeError("No evaluations available yet.")

        if self.n_constraints > 0 and len(self.history_C) > 0:
            feas = np.all(self.history_C <= 0, axis=1)
            if np.any(feas):
                feasible_idx = np.where(feas)[0]
                best_local = int(np.argmax(self.history_Y[feas, 0]))
                idx = int(feasible_idx[best_local])
            else:
                violation = self._constraint_violation_scalar(self.history_C)
                idx = int(np.argmin(violation))
        else:
            idx = int(np.argmax(self.history_Y[:, 0]))

        best_x = self.history_X[idx].copy()
        best_y = float(self._display_objectives(self.history_Y[idx : idx + 1])[0, 0])
        return best_x, best_y

    def optimize(self):
        t0 = time.time()
        if self.verbose:
            print("=== Running Single-objective MG-GPO-like optimizer ===")
            print(f"dim={self.dim}, pop_size={self.pop_size}")
            print(f"offspring setup: m1={self.m1}, m2={self.m2}, m3={self.m3}")
            print(f"acq_mode={self.acq_mode}")
            print(f"direction={'maximize' if self.maximize else 'minimize'}")
            print(f"device={self.device}")
            if self.ref_point is not None:
                print("ref_point is ignored in the single-objective variant.")

        self._initialize_population()
        x_best, y_best = self.get_best_solution()
        self.best_x_ = x_best.copy()
        self.best_y_ = float(y_best)
        self.best_history = [float(y_best)]

        for gen in range(self.n_generations):
            gen_start = time.time()
            if self.verbose:
                print(f"=== Generation {gen + 1}/{self.n_generations} ===")

            X_train, Y_train, C_train, status_train = self._get_training_data()
            n_total_before = len(X_train)
            X_train, Y_train, C_train, status_train = self._filter_training_rows_by_status(
                X_train, Y_train, C_train, status_train
            )
            if self.verbose:
                n_removed = n_total_before - len(X_train)
                print(f"GP 训练数据量: {len(X_train)} (status 过滤移除 {n_removed})")
            self._fit_surrogate(X_train, Y_train, C_train)

            offspring = self._create_offspring()
            if len(offspring) == 0:
                raise RuntimeError("No offspring were generated. At least one of m1/m2/m3 must be positive.")

            beta = self._beta_schedule(gen, self.ucb_beta)
            x_eval = self._filter_candidates(offspring, beta=beta)
            eval_details = self._evaluate_batch(x_eval)
            y_eval = eval_details["objectives_internal"]
            c_eval = eval_details["constraints"]

            pool_X = np.vstack([self.population_X, x_eval])
            pool_Y = np.vstack([self.population_Y, y_eval])
            pool_C = (
                np.vstack([self.population_C, c_eval])
                if self.n_constraints > 0
                else self._empty_constraints(len(pool_X))
            )
            pool_status = list(self.population_status) + list(eval_details["status"])

            self.population_X, self.population_Y, self.population_C, chosen = self._select_topk(
                pool_X,
                pool_Y,
                self.pop_size,
                pool_C,
                return_indices=True,
            )
            self.population_status = [pool_status[i] for i in chosen]
            if self.n_constraints > 0:
                self.population_feasible = np.all(self.population_C <= 0, axis=1)
            else:
                self.population_feasible = np.ones(len(self.population_X), dtype=bool)

            self.history_X = np.vstack([self.history_X, x_eval])
            self.history_Y = np.vstack([self.history_Y, y_eval])
            if self.n_constraints > 0:
                self.history_C = np.vstack([self.history_C, c_eval])
            self.history_status.extend(eval_details["status"])
            self.history_feasible = np.concatenate([self.history_feasible, eval_details["feasible"]])
            self.history_raw.extend(eval_details["raw"])

            self._best_update()
            x_best, y_best = self.get_best_solution()
            self.best_x_ = x_best.copy()
            self.best_y_ = float(y_best)
            self.best_history.append(float(y_best))

            if self.verbose:
                disp_y = self._display_objectives(y_eval)
                feas_count = int(np.sum(eval_details["feasible"])) if self.n_constraints > 0 else len(x_eval)
                print(
                    f"Gen {gen + 1}: 真实评估={len(x_eval)}, 本代可行点={feas_count}, "
                    f"sample_y={disp_y[0, 0] if len(disp_y) else 'N/A'}, best_y={y_best:.6f}, "
                    f"time={time.time() - gen_start:.2f}s"
                )

        if self.verbose:
            print(f"完成，总时间 {time.time() - t0:.2f}s")
            print(f"Best value: f({self.best_x_}) = {self.best_y_}")

        return self.best_x_.copy(), float(self.best_y_)

    def plot_convergence(self, path: Optional[str] = None):
        plt.figure(figsize=(14, 9))
        evals = [self.pop_size + i * self.evals_per_gen for i in range(len(self.best_history))]
        all_Y = self._display_objectives(self.history_Y).reshape(-1)
        running_best = np.maximum.accumulate(all_Y) if self.maximize else np.minimum.accumulate(all_Y)

        plt.subplot(2, 2, 1)
        plt.plot(evals, self.best_history, "o-", linewidth=2, label="best-so-far")
        plt.xlabel("Evaluations")
        plt.ylabel("Best objective")
        plt.title("Best-so-far Convergence")
        plt.grid(True)
        plt.legend()

        plt.subplot(2, 2, 2)
        plt.plot(all_Y, ".-", linewidth=1.5, label="objective value")
        plt.plot(running_best, "--", linewidth=1.5, label="running best")
        plt.xlabel("Evaluation index")
        plt.ylabel("Objective")
        plt.title("Objective Trace")
        plt.grid(True)
        plt.legend()

        plt.subplot(2, 2, 3)
        for i in range(self.dim):
            plt.plot(self.history_X[:, i], ".-", label=f"x{i + 1}")
        plt.xlabel("Evaluation index")
        plt.ylabel("Value")
        plt.title("Design variables")
        plt.grid(True)
        plt.legend()

        plt.subplot(2, 2, 4)
        if self.n_constraints > 0 and len(self.history_C) > 0:
            for j in range(self.n_constraints):
                plt.plot(self.history_C[:, j], ".-", label=f"c{j + 1}")
            plt.xlabel("Evaluation index")
            plt.ylabel("Constraint")
            plt.title("Constraint traces")
            plt.grid(True)
            plt.legend()
        else:
            plt.axis("off")
            plt.text(0.5, 0.5, "No output constraints", ha="center", va="center")

        plt.tight_layout()

        if path is None:
            timestamp = datetime.now().strftime("%Y%m%d%H%M")
            path = f"save/mggpo_so_{timestamp}.png"

        save_dir = os.path.dirname(path)
        if save_dir:
            os.makedirs(save_dir, exist_ok=True)

        plt.savefig(path)
        plt.show()

    def save_history(self, path: Optional[str] = None):
        if path is None:
            timestamp = datetime.now().strftime("%Y%m%d%H%M")
            path = f"save/mggpo_so_{timestamp}.dat"

        save_dir = os.path.dirname(path)
        if save_dir:
            os.makedirs(save_dir, exist_ok=True)

        Y_save = self._display_objectives(self.history_Y)
        data_parts = [self.history_X, Y_save]
        if self.n_constraints > 0:
            data_parts.append(self.history_C)
            data_parts.append(self.history_feasible.reshape(-1, 1).astype(int))
        data = np.hstack(data_parts)
        np.savetxt(path, data, fmt="%.8f")
        np.savetxt(path.replace(".dat", "_best.dat"), np.asarray(self.best_history), fmt="%.8f")
        print(f"Saved to {path}")


SingleObjectiveMGGPOOptimizer = MGGPOSOOptimizer
SMGGPOOptimizer = MGGPOSOOptimizer


# --------------
# Example usage
# --------------
if __name__ == "__main__":
    t0 = time.time()

    from GOTAcc.tests.test_function_single import setup_objective

    dim = 30
    func_type = "rosenbrock"  # "sphere", "rosenbrock", "ackley"
    func, bounds = setup_objective(func_type, dim=dim)

    opt = MGGPOSOOptimizer(
        func=func,
        bounds=bounds,
        n_objectives=1,
        n_constraints=0,
        kernel_type="matern",  # "rbf", "matern"
        gp_restarts=5,
        pop_size=80,
        acq_mode="ucb",  # "ucb", "ei", "pi", "combine"
        ucb_beta=2.0,
        ucb_beta_kwargs={"beta_strategy": "inv_decay", "beta_lam": 0.05},
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
        crossover_prob=1.0,
        w=0.4,
        c1=2.0,
        c2=2.0,
        maximize=True,
        device="cpu",
        random_state=120,
        verbose=True,
    )

    opt.optimize()

    # Results display
    # opt.save_history()
    opt.plot_convergence()

    print(f"Example finished in {time.time() - t0:.2f}s")
