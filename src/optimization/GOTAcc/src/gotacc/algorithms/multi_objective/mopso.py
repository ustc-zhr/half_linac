import time
import numpy as np
from scipy.stats import qmc
import matplotlib.pyplot as plt
from datetime import datetime
from typing import Optional, Tuple, Dict, Any, Callable, List
import warnings
warnings.filterwarnings("ignore", message="To copy construct from a tensor")

import torch
from botorch.utils.multi_objective import is_non_dominated
from botorch.utils.multi_objective.hypervolume import Hypervolume

class MOPSOOptimizer:
    
    """
    MOPSO algorithm for multi-objective optimization
    """
    def __init__(
        self,
        func: Callable,
        bounds: np.ndarray,
        n_objectives: int = 2,
        pop_size: int = 100,
        n_generations: int = 100,
        ref_point: Optional[np.ndarray] = None,
        w: float = 0.4,  # inertia weight
        c1: float = 1.0,  # cognitive coefficient
        c2: float = 2.0,  # social coefficient
        mutation_prob: Optional[float] = None,
        archive_size: int = 100,
        maximize: bool = False,
        random_state: int = 0,
        verbose: bool = True
    ):
        self.func = func
        self.bounds = np.asarray(bounds, dtype=float)
        self.dim = self.bounds.shape[0]
        self.n_objectives = n_objectives
        self.pop_size = int(pop_size)
        self.n_generations = int(n_generations)
        self.w = float(w)
        self.c1 = float(c1)
        self.c2 = float(c2)
        self.mutation_prob = mutation_prob if mutation_prob is not None else 1.0 / self.dim
        self.archive_size = int(archive_size)
        self.maximize = maximize
        self.random_state = int(random_state)
        self.verbose = verbose

        # history
        self.history_X = np.zeros((0, self.dim))
        self.history_Y = np.zeros((0, self.n_objectives))

        # particle swarm
        self.positions = None
        self.velocities = None
        self.pbest_X = None
        self.pbest_Y = None
        self.archive_X = np.zeros((0, self.dim))
        self.archive_Y = np.zeros((0, self.n_objectives))

        self.hv_calc = None
        self.hypervolume_history = []
        self.ref_point = ref_point

        self._setup_random_state()

    def _setup_random_state(self):
        np.random.seed(self.random_state)

    def _evaluate_function(self, X: np.ndarray) -> np.ndarray:
        if X.ndim == 1:
            X = X.reshape(1, -1)
        Y = self.func(X)
        if Y.ndim == 1:
            Y = Y.reshape(-1, 1)
        Y = Y.reshape(-1, self.n_objectives)
        if self.maximize:
            return Y.astype(float)
        else:
            return (-Y).astype(float)

    def _initialize_population(self):
        sampler = qmc.LatinHypercube(d=self.dim)
        sample = sampler.random(n=self.pop_size)
        X = qmc.scale(sample, self.bounds[:, 0], self.bounds[:, 1])
        Y = self._evaluate_function(X)

        self.positions = X
        self.velocities = np.random.uniform(-1, 1, size=(self.pop_size, self.dim))  # initial velocities
        self.pbest_X = X.copy()
        self.pbest_Y = Y.copy()

        self.history_X = np.vstack([self.history_X, X])
        self.history_Y = np.vstack([self.history_Y, Y])

        # Initialize archive with non-dominated particles
        self._update_archive(X, Y)

    def _dominates(self, y1: np.ndarray, y2: np.ndarray) -> bool:
        return np.all(y1 >= y2) and np.any(y1 > y2)

    def _update_pbest(self, idx: int, new_X: np.ndarray, new_Y: np.ndarray):
        if self._dominates(new_Y, self.pbest_Y[idx]):
            self.pbest_X[idx] = new_X
            self.pbest_Y[idx] = new_Y
        elif not self._dominates(self.pbest_Y[idx], new_Y):
            # If incomparable, randomly choose
            if np.random.rand() < 0.5:
                self.pbest_X[idx] = new_X
                self.pbest_Y[idx] = new_Y

    def _update_archive(self, X: np.ndarray, Y: np.ndarray):
        combined_X = np.vstack([self.archive_X, X])
        combined_Y = np.vstack([self.archive_Y, Y])

        # Find non-dominated
        fronts = self._fast_nondominated_sort(combined_Y)
        nd_indices = fronts[0]  # First front is non-dominated

        self.archive_X = combined_X[nd_indices]
        self.archive_Y = combined_Y[nd_indices]

        # If archive exceeds size, use crowding distance to prune
        if len(self.archive_X) > self.archive_size:
            distances = self._crowding_distance(self.archive_Y, list(range(len(self.archive_Y))))
            sorted_indices = sorted(range(len(self.archive_Y)), key=lambda i: distances[i], reverse=True)
            self.archive_X = self.archive_X[sorted_indices[:self.archive_size]]
            self.archive_Y = self.archive_Y[sorted_indices[:self.archive_size]]

    def _select_leader(self) -> np.ndarray:
        # Select a leader from archive based on crowding distance (less crowded is better)
        if len(self.archive_X) == 0:
            return np.random.uniform(self.bounds[:, 0], self.bounds[:, 1])
        distances = self._crowding_distance(self.archive_Y, list(range(len(self.archive_Y))))
        # Invert distances for selection probability (higher distance -> higher prob)
        probs = np.array([distances[i] if distances[i] != float('inf') else 1e6 for i in range(len(self.archive_Y))])
        if np.sum(probs) == 0:
            probs = np.ones_like(probs)
        probs /= np.sum(probs)
        idx = np.random.choice(len(self.archive_X), p=probs)
        return self.archive_X[idx]

    def _fast_nondominated_sort(self, objs: np.ndarray) -> List[List[int]]:
        N = objs.shape[0]
        S = [[] for _ in range(N)]
        n = np.zeros(N, dtype=int)
        fronts = [[]]
        
        for p in range(N):
            p_dom_others = np.all(objs[p] >= objs, axis=1) & np.any(objs[p] > objs, axis=1)
            others_dom_p = np.all(objs >= objs[p], axis=1) & np.any(objs > objs[p], axis=1)
            
            S[p] = np.where(p_dom_others)[0].tolist()
            n[p] = np.sum(others_dom_p)
            
            if n[p] == 0:
                fronts[0].append(p)
        
        i = 0
        while fronts[i]:
            Q = []
            for p in fronts[i]:
                for q in S[p]:
                    n[q] -= 1
                    if n[q] == 0:
                        Q.append(q)
            i += 1
            fronts.append(Q)
            
        if len(fronts) > 0 and len(fronts[-1]) == 0:
            fronts.pop(-1)
            
        return fronts

    def _crowding_distance(self, objs: np.ndarray, indices: List[int]) -> Dict[int, float]:
        distances = {i: 0.0 for i in indices}
        if len(indices) == 0:
            return distances
        arr = objs[np.array(indices), :]
        n_obj = arr.shape[1]
        for m in range(n_obj):
            sorted_idx = np.argsort(arr[:, m])
            distances[indices[sorted_idx[0]]] = float('inf')
            distances[indices[sorted_idx[-1]]] = float('inf')
            f_min = arr[sorted_idx[0], m]
            f_max = arr[sorted_idx[-1], m]
            if f_max - f_min == 0:
                continue
            for k in range(1, len(indices) - 1):
                prev_f = arr[sorted_idx[k - 1], m]
                next_f = arr[sorted_idx[k + 1], m]
                distances[indices[sorted_idx[k]]] += (next_f - prev_f) / (f_max - f_min)
        return distances

    def _mutate(self, x: np.ndarray) -> np.ndarray:
        mask = np.random.rand(self.dim) < self.mutation_prob
        delta = np.random.uniform(-0.1, 0.1, size=self.dim) * (self.bounds[:, 1] - self.bounds[:, 0])
        x = x + mask * delta
        x = np.clip(x, self.bounds[:, 0], self.bounds[:, 1])
        return x

    def _set_reference_point(self):
        if self.ref_point is not None:
            self.ref_point = np.asarray(self.ref_point, dtype=float)
            if not self.maximize:
                self.ref_point = -self.ref_point
            print(f"内部参考点: {self.ref_point}")
            return
        # auto compute
        worst = np.min(self.history_Y, axis=0) if len(self.history_Y) > 0 else np.zeros(self.n_objectives)
        self.ref_point = worst - 0.1 * np.abs(worst)
        if self.verbose:
            print(f"自动设置参考点（内部最大化值）: {self.ref_point}")

    def _calculate_hypervolume(self, Y: np.ndarray) -> float:
        if self.hv_calc is None:
            ref_point_t = torch.tensor(self.ref_point, dtype=torch.float64)
            self.hv_calc = Hypervolume(ref_point=ref_point_t)
        Y_t = torch.tensor(Y, dtype=torch.float64)
        mask = is_non_dominated(Y_t)
        pareto = Y_t[mask]
        if len(pareto) == 0:
            return 0.0
        return float(self.hv_calc.compute(pareto))

    # def optimize(self):
    #     start_time = time.time()
    #     print(f"=== Running MOPSO ===")
        
    #     # Initialize population
    #     self._initialize_population()
        
    #     # Set reference point
    #     self._set_reference_point()
        
    #     # Track hypervolume
    #     ref_point_t = torch.tensor(self.ref_point, dtype=torch.float64)
    #     self.hv_calc = Hypervolume(ref_point=ref_point_t)
    #     self.hypervolume_history = [self._calculate_hypervolume(self.archive_Y)]
        
    #     for gen in range(self.n_generations):
    #         gen_t0 = time.time()
            
    #         for i in range(self.pop_size):
    #             # Select leader (gbest)
    #             gbest = self._select_leader()
                
    #             # Update velocity
    #             r1 = np.random.rand(self.dim)
    #             r2 = np.random.rand(self.dim)
    #             self.velocities[i] = (self.w * self.velocities[i] +
    #                                   self.c1 * r1 * (self.pbest_X[i] - self.positions[i]) +
    #                                   self.c2 * r2 * (gbest - self.positions[i]))
                
    #             # Update position
    #             self.positions[i] += self.velocities[i]
    #             self.positions[i] = np.clip(self.positions[i], self.bounds[:, 0], self.bounds[:, 1])
                
    #             # Mutate
    #             self.positions[i] = self._mutate(self.positions[i])
                
    #             # Evaluate
    #             new_Y = self._evaluate_function(self.positions[i])
                
    #             # Update pbest
    #             self._update_pbest(i, self.positions[i], new_Y[0])
                
    #             # Update archive
    #             self._update_archive(self.positions[i:i+1], new_Y)
            
    #         # Update history (add all new positions)
    #         new_X = self.positions.copy()
    #         new_Y = self._evaluate_function(new_X)
    #         self.history_X = np.vstack([self.history_X, new_X])
    #         self.history_Y = np.vstack([self.history_Y, new_Y])
    #         hv = self._calculate_hypervolume(self.archive_Y)
    #         self.hypervolume_history.append(hv)
            
    #         gen_t1 = time.time()
    #         if self.verbose:
    #             print(f"Gen {gen+1}/{self.n_generations}: hypervolume={hv:.6f}, time={(gen_t1-gen_t0):.2f}s")
        
    #     total_time = time.time() - start_time
    #     if self.verbose:
    #         print(f"MOPSO completed in {total_time:.2f}s")
    def optimize(self):
        start_time = time.time()
        print(f"=== Running MOPSO ===")
        
        # Initialize population
        self._initialize_population()
        
        # Set reference point
        self._set_reference_point()
        
        # Track hypervolume
        ref_point_t = torch.tensor(self.ref_point, dtype=torch.float64)
        self.hv_calc = Hypervolume(ref_point=ref_point_t)
        self.hypervolume_history = [self._calculate_hypervolume(self.archive_Y)]
        
        for gen in range(self.n_generations):
            gen_t0 = time.time()
            
            # 第一步：更新所有粒子的速度和位置
            for i in range(self.pop_size):
                # Select leader (gbest)
                gbest = self._select_leader()
                
                # Update velocity
                r1 = np.random.rand(self.dim)
                r2 = np.random.rand(self.dim)
                self.velocities[i] = (self.w * self.velocities[i] +
                                    self.c1 * r1 * (self.pbest_X[i] - self.positions[i]) +
                                    self.c2 * r2 * (gbest - self.positions[i]))
                
                # Update position
                self.positions[i] += self.velocities[i]
                self.positions[i] = np.clip(self.positions[i], self.bounds[:, 0], self.bounds[:, 1])
                
                # Mutate
                self.positions[i] = self._mutate(self.positions[i])
            
            # 第二步：一次性评估整个种群
            new_Y = self._evaluate_function(self.positions)  # shape: (pop_size, n_objectives)
            
            # 第三步：更新pbest和archive
            for i in range(self.pop_size):
                # Update pbest
                self._update_pbest(i, self.positions[i], new_Y[i])
            
            # Update archive
            self._update_archive(self.positions, new_Y)
            
            # Update history (add all new positions)
            self.history_X = np.vstack([self.history_X, self.positions])
            self.history_Y = np.vstack([self.history_Y, new_Y])
            
            # 计算超体积
            hv = self._calculate_hypervolume(self.archive_Y)
            self.hypervolume_history.append(hv)
            
            gen_t1 = time.time()
            if self.verbose:
                print(f"Gen {gen+1}/{self.n_generations}: hypervolume={hv:.6f}, time={(gen_t1-gen_t0):.2f}s")
    
        total_time = time.time() - start_time
        if self.verbose:
            print(f"MOPSO completed in {total_time:.2f}s")

    def get_pareto_front(self) -> Tuple[np.ndarray, np.ndarray]:
        # The archive holds the Pareto front
        pareto_X = self.archive_X.copy()
        pareto_Y = self.archive_Y.copy()
        if not self.maximize:
            pareto_Y = -pareto_Y
        return pareto_X, pareto_Y
    
    def plot_convergence(self):
        plt.figure(figsize=(15, 10))

        plt.subplot(2, 2, 1)
        evals = [self.pop_size + gen * self.pop_size for gen in range(len(self.hypervolume_history))]
        plt.plot(evals, self.hypervolume_history, 'o-', linewidth=2)
        plt.xlabel('Evaluations')
        plt.ylabel('Hypervolume')
        plt.title('Hypervolume Convergence')
        plt.grid(True)

        if self.n_objectives == 2:
            plt.subplot(2, 2, 2)
            pareto_X, pareto_Y = self.get_pareto_front()
            if self.maximize:
                all_Y = self.history_Y
            else:
                all_Y = -self.history_Y
            plt.scatter(all_Y[:, 0], all_Y[:, 1], alpha=0.3, label='All points')
            plt.scatter(pareto_Y[:, 0], pareto_Y[:, 1], color='red', s=50, label='Pareto front')
            plt.xlabel('Objective 1')
            plt.ylabel('Objective 2')
            plt.title('Pareto Front')
            plt.legend()
            plt.grid(True)

        plt.subplot(2, 2, 3)
        for i in range(self.dim):
            plt.plot(self.history_X[:, i], '.-', label=f'Dim {i+1}')
        plt.xlabel('Evaluations')
        plt.ylabel('Parameter values')
        plt.title('Parameter Evolution')
        plt.legend()
        plt.grid(True)

        plt.subplot(2, 2, 4)
        for i in range(self.n_objectives):
            if self.maximize:
                y_values = self.history_Y[:, i]
            else:
                y_values = -self.history_Y[:, i]
            plt.plot(y_values, '.-', label=f'Objective {i+1}')
        plt.xlabel('Evaluations')
        plt.ylabel('Objective values')
        plt.title('Objective Values Evolution')
        plt.legend()
        plt.grid(True)

        plt.tight_layout()
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
            path = f"mopso_{timestamp}.dat"
        
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
    
if __name__ == "__main__":
    def zdt1(X):
        if X.ndim == 1:
            X = X.reshape(1, -1)
        n = X.shape[1]
        f1 = X[:, 0]
        g = 1 + 9 / (n - 1) * np.sum(X[:, 1:], axis=1)
        h = 1 - np.sqrt(f1 / g)
        f2 = g * h
        return np.vstack([f1, f2]).T

    dim = 30
    bounds = np.tile([0.0, 1.0], (dim, 1))
    ref_point = np.array([1, 1])

    mopso = MOPSOOptimizer(
        func=zdt1,
        bounds=bounds,
        n_objectives=2,
        pop_size=100,
        n_generations=50,
        ref_point=ref_point,
        w = 0.4,  # inertia weight
        c1 = 1.0,  # cognitive coefficient
        c2 = 2.0,  # social coefficient
        random_state=111,
        maximize=False,
        verbose=True
    )

    mopso.optimize()
    
    # mopso.best()
    mopso.plot_convergence()

    # mopso.save_history()
