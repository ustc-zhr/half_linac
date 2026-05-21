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

class NSGA2Optimizer:
    
    """
    NSGA-II algorithm for multi-objective optimization
    """
    def __init__(
        self,
        func: Callable,
        bounds: np.ndarray,
        n_objectives: int = 2,
        pop_size: int = 100,
        n_generations: int = 100,
        ref_point: Optional[np.ndarray] = None,
        crossover_prob: float = 0.9,
        mutation_prob: Optional[float] = None,
        crossover_eta: float = 20.0,
        mutation_eta: float = 20.0,
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
        self.crossover_prob = float(crossover_prob)
        self.mutation_prob = mutation_prob if mutation_prob is not None else 1.0 / self.dim
        self.crossover_eta = float(crossover_eta)
        self.mutation_eta = float(mutation_eta)
        self.maximize = maximize
        self.random_state = int(random_state)
        self.verbose = verbose

        # history
        self.history_X = np.zeros((0, self.dim))
        self.history_Y = np.zeros((0, self.n_objectives))
        self.population_X = None
        self.population_Y = None

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

        self.population_X = X
        self.population_Y = Y
        self.history_X = np.vstack([self.history_X, X])
        self.history_Y = np.vstack([self.history_Y, Y])

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

    def _sbx_crossover(self, parent1: np.ndarray, parent2: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        u = np.random.rand(self.dim)
        beta = np.empty(self.dim)
        mask = u <= 0.5
        beta[mask] = (2 * u[mask]) ** (1.0 / (self.crossover_eta + 1.0))
        beta[~mask] = (1.0 / (2.0 * (1.0 - u[~mask]))) ** (1.0 / (self.crossover_eta + 1.0))
        child1 = 0.5 * ((1 + beta) * parent1 + (1 - beta) * parent2)
        child2 = 0.5 * ((1 - beta) * parent1 + (1 + beta) * parent2)
        child1 = np.clip(child1, self.bounds[:, 0], self.bounds[:, 1])
        child2 = np.clip(child2, self.bounds[:, 0], self.bounds[:, 1])
        return child1, child2

    def _polynomial_mutation(self, x: np.ndarray) -> np.ndarray:
        x_new = x.copy()
        for i in range(self.dim):
            if np.random.rand() < self.mutation_prob:
                u = np.random.rand()
                if u < 0.5:
                    delta = (2 * u) ** (1.0 / (self.mutation_eta + 1.0)) - 1.0
                else:
                    delta = 1.0 - (2 * (1.0 - u)) ** (1.0 / (self.mutation_eta + 1.0))
                xl, xu = self.bounds[i, 0], self.bounds[i, 1]
                x_new[i] = x_new[i] + delta * (xu - xl)
                x_new[i] = np.clip(x_new[i], xl, xu)
        return x_new

    def _tournament_selection(self, tournament_size=2):
        selected = []
        for _ in range(self.pop_size):
            candidates = np.random.choice(len(self.population_X), tournament_size, replace=False)
            # 简单选择：随机选择
            winner = candidates[np.random.randint(tournament_size)]
            selected.append(winner)
        return np.array(selected)

    def _create_offspring(self):
        parents_idx = self._tournament_selection()
        offspring = []
        
        for i in range(0, len(parents_idx), 2):
            if i + 1 < len(parents_idx):
                p1_idx, p2_idx = parents_idx[i], parents_idx[i+1]
                p1, p2 = self.population_X[p1_idx], self.population_X[p2_idx]
                
                if np.random.rand() < self.crossover_prob:
                    c1, c2 = self._sbx_crossover(p1, p2)
                else:
                    c1, c2 = p1.copy(), p2.copy()
                
                c1 = self._polynomial_mutation(c1)
                c2 = self._polynomial_mutation(c2)
                
                offspring.extend([c1, c2])
        
        return np.array(offspring)

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

    def optimize(self):
        start_time = time.time()
        print(f"=== Running NSGA-II ===")
        
        # Initialize population
        self._initialize_population()
        
        # Set reference point
        self._set_reference_point()
        
        # Track hypervolume
        self.hypervolume_history = [self._calculate_hypervolume(self.history_Y)]
        
        for gen in range(self.n_generations):
            gen_t0 = time.time()
            
            # Create offspring
            offspring_X = self._create_offspring()
            offspring_Y = self._evaluate_function(offspring_X)
            
            # Combined population
            combined_X = np.vstack([self.population_X, offspring_X])
            combined_Y = np.vstack([self.population_Y, offspring_Y])
            
            # Non-dominated sorting and selection
            fronts = self._fast_nondominated_sort(combined_Y)
            new_pop_X, new_pop_Y = [], []
            
            for front in fronts:
                if len(new_pop_X) + len(front) <= self.pop_size:
                    new_pop_X.extend(combined_X[front])
                    new_pop_Y.extend(combined_Y[front])
                else:
                    # Use crowding distance for the last front
                    distances = self._crowding_distance(combined_Y, front)
                    sorted_front = sorted(front, key=lambda idx: distances[idx], reverse=True)
                    remaining = self.pop_size - len(new_pop_X)
                    new_pop_X.extend(combined_X[sorted_front[:remaining]])
                    new_pop_Y.extend(combined_Y[sorted_front[:remaining]])
                    break
            
            self.population_X = np.array(new_pop_X)
            self.population_Y = np.array(new_pop_Y)
            
            # Update history
            self.history_X = np.vstack([self.history_X, offspring_X])
            self.history_Y = np.vstack([self.history_Y, offspring_Y])
            hv = self._calculate_hypervolume(self.history_Y)
            self.hypervolume_history.append(hv)
            
            gen_t1 = time.time()
            if self.verbose:
                print(f"Gen {gen+1}/{self.n_generations}: hypervolume={hv:.6f}, time={(gen_t1-gen_t0):.2f}s")
        
        total_time = time.time() - start_time
        if self.verbose:
            print(f"NSGA-II completed in {total_time:.2f}s")

    def get_pareto_front(self) -> Tuple[np.ndarray, np.ndarray]:
        Y_t = torch.tensor(self.history_Y, dtype=torch.float64)
        nd_mask = is_non_dominated(Y_t)
        pareto_X = self.history_X[nd_mask.cpu().numpy()]
        pareto_Y = self.history_Y[nd_mask.cpu().numpy()]
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
            path: 保存路径。如果为None 自动生成带时间戳的文件名。
        Returns:
            None
        """
        if path is None:
            timestamp = datetime.now().strftime("%Y%m%d%H%M")
            path = f"bea_mobo_qehvi_{timestamp}.dat"
        
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

    nsga2 = NSGA2Optimizer(
        func=zdt1,
        bounds=bounds,
        n_objectives=2,
        pop_size=100,
        n_generations=10,
        ref_point=ref_point,
        random_state=120,
        maximize=False,
        verbose=True
    )

    nsga2.optimize()
    
    # nsga2.best()
    nsga2.plot_convergence()

    # nsga2.save_history()
    
