
# minimal_sklearn_ucb.py
# high dimensional version
import numpy as np
import time
import matplotlib.pyplot as plt
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, Matern, ConstantKernel as C
from scipy.stats import norm
from scipy import optimize
from scipy.stats import qmc  
import cma

from optimize_acquisition import *
from acquisition_function import *
from test_function import *

np.random.seed(0)
# ----- optimize_acquisition -----
def optimize_acq_local(gpr, x0, bounds, acq_fn, minimize=True):
    # x0: 1D seed, bounds: array shape (d,2)
    d = x0.size
    lb = bounds[:,0]
    ub = bounds[:,1]

    def obj(x):
        # objective for scipy.minimize (we keep argmin convention in your acquisition)
        return acq_fn(gpr, x.reshape(1,-1))

    # use L-BFGS-B with numeric grad (scipy will approximate jacobian)
    res = optimize.minimize(obj, x0, bounds=optimize.Bounds(lb, ub),
                            method='L-BFGS-B', options={'maxiter':200})
    return res.x, res.fun

# ----- 随机采样优化器  -----
def optimize_acquisition_random(gpr, bounds, acq, beta, xi, minimize, Y_best,
                                n_candidates=1000):
    dim = bounds.shape[0]
    lb, ub = bounds[:,0], bounds[:,1]
    Xcand = np.random.uniform(lb, ub, size=(n_candidates, dim))
    vals = acquisition(gpr, Xcand, acq=acq, beta=beta, xi=xi,
                       minimize=minimize, Y_best=Y_best)
    best_idx = np.argmin(vals)
    return Xcand[best_idx].reshape(1, -1)

# ----- Sobol采样优化器  -----[低差异序列（Quasi-Monte Carlo）的一种]
def optimize_acquisition_sobol(gpr, bounds, acq, beta, xi, minimize, Y_best,
                                n_candidates=1000):
    dim = bounds.shape[0]
    lb, ub = bounds[:,0], bounds[:,1]

    # ---- 自动修正 n_candidates 为 2^k ----
    # n_pow2 = 1 << (n_candidates - 1).bit_length()   # 向上取最近的 2^k
    # if n_pow2 != n_candidates:
    #     print(f"[Sobol] n_candidates={n_candidates} 调整为 {n_pow2} (2^k)")
    #     n_candidates = n_pow2
    
    # Sobol 序列采样
    sampler = qmc.Sobol(d=dim, scramble=True, seed=0)
    Xcand_unit = sampler.random(n_candidates)
    Xcand = qmc.scale(Xcand_unit, bounds[:,0], bounds[:,1])
    
    # 计算采集函数值
    vals = acquisition(gpr, Xcand, acq=acq, beta=beta, xi=xi,
                       minimize=minimize, Y_best=Y_best)
    
    # 找到最优点
    best_idx = np.argmin(vals)
    return Xcand[best_idx].reshape(1, -1)


# ----- Sobol+局部优化采样优化器  -----
def optimize_acquisition_sobol_local(gpr, bounds, acq, beta, xi, minimize, Y_best,
                                n_candidates=1000, k=5):
    dim = bounds.shape[0]
    lb, ub = bounds[:,0], bounds[:,1]

    # ---- 自动修正 n_candidates 为 2^k ----
    # n_pow2 = 1 << (n_candidates - 1).bit_length()   # 向上取最近的 2^k
    # if n_pow2 != n_candidates:
    #     print(f"[Sobol] n_candidates={n_candidates} 调整为 {n_pow2} (2^k)")
    #     n_candidates = n_pow2

    sampler = qmc.Sobol(d=dim, scramble=True, seed=0)
    Xcand_unit = sampler.random(n_candidates)
    Xcand = qmc.scale(Xcand_unit, bounds[:,0], bounds[:,1])
    vals = acquisition(gpr, Xcand, acq=acq, beta=beta, xi=xi,
                       minimize=minimize, Y_best=Y_best)
    best_idx = np.argmin(vals)

    # 选取前 k 个最优点作为局部优化的起点
    # k = 5
    seed_idx = np.argsort(vals)[:k]  # argmin convention
    seeds = Xcand[seed_idx]
    #
    best_val = np.inf
    best_x = None
    for s in seeds:
        x_opt, val = optimize_acq_local(gpr, s, bounds, 
                                        acq_fn=lambda model, xx: acquisition(model, xx, acq=acq, beta=beta, xi=xi, minimize=True, Y_best=Y_best))
        if val < best_val:
            best_val = val
            best_x = x_opt

    return best_x.reshape(1,-1)


# ----- CMA-ES 优化器 -----
def optimize_acquisition_cma(gpr, bounds, acq, beta, xi, minimize, Y_best,
                             popsize=20, max_iter=100):
    dim = bounds.shape[0]
    lb, ub = bounds[:,0], bounds[:,1]
    x0 = 0.5 * (lb + ub)
    sigma0 = 0.25 * (ub - lb).mean()

    def obj(xx):
        xx = np.clip(xx, lb, ub)
        return float(acquisition(gpr, xx.reshape(1,-1),
                                 acq=acq, beta=beta, xi=xi,
                                 minimize=minimize, Y_best=Y_best))

    es = cma.CMAEvolutionStrategy(x0.tolist(), sigma0,
                                  {'popsize': popsize, 'bounds': [lb.tolist(), ub.tolist()]})
    best_x, best_val = None, np.inf
    for _ in range(max_iter):
        Xs = es.ask()
        vals = [obj(np.array(xx)) for xx in Xs]
        es.tell(Xs, vals)
        es.disp()
        idx = int(np.argmin(vals))
        if vals[idx] < best_val:
            best_val = vals[idx]
            best_x = np.array(Xs[idx])
    return best_x.reshape(1,-1)

# ----- GA 优化器 -----
def optimize_acquisition_ga(gpr, bounds, acq, beta, xi, minimize, Y_best,
                            popsize=50, generations=100, crossover_rate=0.8, mutation_rate=0.1):
    dim = bounds.shape[0]
    lb, ub = bounds[:,0], bounds[:,1]
    pop = np.random.uniform(lb, ub, size=(popsize, dim))

    def fitness(X):
        return np.array([acquisition(gpr, x.reshape(1,-1),
                                     acq=acq, beta=beta, xi=xi,
                                     minimize=minimize, Y_best=Y_best)
                         for x in X]).ravel()

    for gen in range(generations):
        fit = fitness(pop)
        # 选择（锦标赛）
        idx = np.random.randint(0, popsize, size=(popsize, 2))
        winners = np.where(fit[idx[:,0]] < fit[idx[:,1]], idx[:,0], idx[:,1])
        parents = pop[winners]

        # 交叉
        offspring = parents.copy()
        for i in range(0, popsize, 2):
            if np.random.rand() < crossover_rate and i+1 < popsize:
                cp = np.random.randint(1, dim)
                offspring[i, cp:], offspring[i+1, cp:] = \
                    offspring[i+1, cp:].copy(), offspring[i, cp:].copy()

        # 变异
        mutation = np.random.rand(*offspring.shape) < mutation_rate
        offspring = np.clip(offspring + mutation * np.random.normal(0, 0.1, offspring.shape),
                            lb, ub)
        pop = offspring

    final_fit = fitness(pop)
    best_idx = np.argmin(final_fit)
    return pop[best_idx].reshape(1,-1)
# ----- acquisition_function -----
# ----- 采集函数 -----
def acquisition(gpr, Xcand, acq="ucb", beta=2.0, xi=0.01, minimize=True, Y_best=None):
    mu, std = gpr.predict(Xcand, return_std=True)
    mu = mu.ravel()
    std = std.ravel() + 1e-12  # 避免除零

    if acq == "ucb":
        # Lower Confidence Bound for minimization
        if minimize:
            return mu - beta * std
        else:
            return mu + beta * std

    elif acq == "ei":
        if Y_best is None:
            raise ValueError("EI requires Y_best (best observed value so far).")
        if minimize:
            imp = Y_best - mu - xi
        else:
            imp = mu - Y_best - xi
        Z = imp / std
        ei = imp * norm.cdf(Z) + std * norm.pdf(Z)
        return -ei if minimize else -ei  # 统一 argmin 策略

    elif acq == "pi":
        if Y_best is None:
            raise ValueError("PI requires Y_best.")
        if minimize:
            imp = Y_best - mu - xi
        else:
            imp = mu - Y_best - xi
        Z = imp / std
        pi = norm.cdf(Z)
        return -pi  # 越大越好 → 转换为 argmin

    else:
        raise ValueError(f"Unknown acquisition {acq}")
    
def beta_schedule(it, beta0=1.0, strategy=None, decay=0.05, X_size=10000, delta=0.1, lam=0.1):
    if strategy == "theory":  # GP-UCB
        return 2 * np.log((X_size * np.pi**2 * it**2) / (6 * delta))
    elif strategy == "exp_decay":  # 指数衰减
        return beta0 * np.exp(-lam * it)
    elif strategy == "inv_decay":  # 1/(1+λt) 衰减
        return beta0 / (1.0 + lam * it)
    elif strategy == "stage":  # 分阶段
        if it < 10:
            return 5.0
        elif it < 30:
            return 2.0
        else:
            return 0.5
    else:
        return beta0  # 固定值

# ----- test_function -----
# ----- 目标函数 -----
def sphere(X):
    # 全局极小值在原点
    return np.sum(X**2, axis=1).reshape(-1, 1)

def rosenbrock(X):
    # 全局极小值在(1,1,...,1)
    return np.sum(100.0 * (X[:,1:] - X[:,:-1]**2.0)**2.0 + (1 - X[:,:-1])**2.0,
                  axis=1).reshape(-1, 1)

def ackley(X):
    # 全局极小值在原点
    dim = X.shape[1]
    sum_sq = np.sum(X**2, axis=1)
    cos_sum = np.sum(np.cos(2 * np.pi * X), axis=1)
    return (-20 * np.exp(-0.2 * np.sqrt(sum_sq / dim))
            - np.exp(cos_sum / dim)
            + 20 + np.e).reshape(-1, 1)

def test1(x):
    return -np.sin(3*x[0]) - x[0]**2 +0.7*x[0] + np.cos(2*x[1]) + x[1]**2 - 0.5*x[1]


# ----- 主函数 -----
def run_highdim_bo(func="sphere", acq="ucb", kernel_type="rbf", acq_optimizer="ga", dim=25, n_init=20, n_iter=10):
    
    print(f"\n=== Running BO ({func}, acq={acq}) in {dim}-D space ===")

    # 选择目标函数和定义域
    if func == "sphere":
        objective = sphere
        bounds = np.array([[-2, 2]] * dim)
    elif func == "rosenbrock":
        objective = rosenbrock
        bounds = np.array([[-2, 2]] * dim)
    elif func == "ackley":
        objective = ackley
        bounds = np.array([[-32.768, 32.768]] * dim)
    elif func == "test1":
        objective = ackley
        bounds = np.array([[-2, 2]] * dim)
    else:
        raise ValueError(f"Unknown func={func}")

    # 初始数据
    X = np.random.uniform(bounds[:,0], bounds[:,1], size=(n_init, dim))
    Y = objective(X)

    # ---------- GPR 模型 ----------
    # 核函数
    if kernel_type == "rbf":
        kernel = C(1.0, (1e-3, 1e3)) * RBF(length_scale=np.ones(dim),
                                           length_scale_bounds=(1e-2, 1e6))
    elif kernel_type == "matern":
        kernel = C(1.0, (1e-3, 1e3)) * Matern(length_scale=np.ones(dim),
                                             length_scale_bounds=(1e-2, 1e6),
                                             nu=2.5)   # nu=2.5 常用，平滑但不过于僵硬
    else:
        raise ValueError(f"Unknown kernel_type={kernel_type}") 
    # GPR
    gpr = GaussianProcessRegressor(kernel=kernel, alpha=1e-6, normalize_y=True, n_restarts_optimizer=5)

    # 初始拟合
    gpr.fit(X, Y.ravel())

    history_X = X.copy()
    history_Y = Y.copy()

    # ---------- BO 主循环 ----------
    for it in range(n_iter):
        t0 = time.time()
        Y_best = np.min(Y) if acq in ["ei", "pi"] else None
        
        beta0 = 3.0
        beta = beta_schedule(it, beta0=beta0, strategy="inv_decay", lam=0.05)

        if acq_optimizer == "random":
            x_next = optimize_acquisition_random(gpr, bounds, acq=acq,
                                                beta=beta, xi=0.01,
                                                minimize=True, Y_best=Y_best,
                                                n_candidates=3000)
        elif acq_optimizer == "sobol":
            x_next = optimize_acquisition_sobol(gpr, bounds, acq=acq,
                                                beta=beta, xi=0.01,
                                                minimize=True, Y_best=Y_best,
                                                n_candidates=3000)
        elif acq_optimizer == "sobol_local":
            x_next = optimize_acquisition_sobol(gpr, bounds, acq=acq,
                                                beta=beta, xi=0.01,
                                                minimize=True, Y_best=Y_best,
                                                n_candidates=3000, k=5)
        elif acq_optimizer == "cmaes":
            x_next = optimize_acquisition_cma(gpr, bounds, acq=acq,
                                            beta=beta, xi=0.01,
                                            minimize=True, Y_best=Y_best,
                                            popsize=min(100, 10*dim), max_iter=200)
        elif acq_optimizer == "ga":
            x_next = optimize_acquisition_ga(gpr, bounds, acq=acq,
                                            beta=beta, xi=0.01, 
                                            minimize=True, Y_best=Y_best,
                                            popsize=min(100, 10*dim), generations=200)
        else:
            raise ValueError(f"Unknown acq_optimizer={acq_optimizer}")

        y_next = objective(x_next)
        print(f"Iter {it+1:02d}: x_next = {x_next.ravel()}, f(x_next)={y_next.ravel()[0]:.4f}, ")

        # 更新训练集
        X = np.vstack([X, x_next])
        Y = np.vstack([Y, y_next])

        history_X = np.vstack([history_X, x_next])
        history_Y = np.vstack([history_Y, y_next])

        # 拟合
        gpr.fit(X, Y.ravel())

        t1 = time.time()
        print(f"time: {t1-t0:.2f} seconds")
    
    
    plot_optimization_process(history_X, history_Y)


def plot_optimization_process(history_X, history_Y):
    plt.figure(figsize=(12, 4))
    
    # 收敛曲线
    plt.subplot(121)
    min_values = np.minimum.accumulate(history_Y)
    plt.semilogy(history_Y, 'o-', label='Objective Value')
    plt.semilogy(min_values, 'r--', label='Best Value')
    plt.xlabel('Iteration')
    plt.ylabel('Value')
    plt.legend()
    
    # 参数空间投影（适用于2D）
    if history_X.shape[1] == 2:
        plt.subplot(122)
        plt.scatter(history_X[:,0], history_X[:,1], c=range(len(history_X)))
        plt.colorbar(label='Iteration')
        plt.xlabel('Dim 1')
        plt.ylabel('Dim 2')
    
    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    # 目标测试函数 ["sphere", "rosenbrock", "ackley"]
    # 采集函数 ["ucb", "ei", "pi"]
    # 采集函数优化器 ["random", "sobol", "cmaes", "ga"]
    # 核函数 ["rbf", "matern"]
    # 维度 dim
    # 初始点数 n_init
    # 迭代次数 n_iter
    t0 = time.time()

    dim = 2
    run_highdim_bo(func="rosenbrock", dim=dim,
                    acq="ucb", kernel_type="rbf", acq_optimizer="sobol",
                    n_init=min(1, 5*dim), n_iter=50)
    
    t1 = time.time()
    print(f"Total time: {t1-t0:.2f} seconds")

    # z=rosenbrock(np.array([[1,]*4]))
    # print(z)
