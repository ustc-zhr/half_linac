
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

np.random.seed(0)

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

# ----- Sobol采样优化器  -----
def optimize_acquisition_sobol(gpr, bounds, acq, beta, xi, minimize, Y_best,
                                n_candidates=1000):
    dim = bounds.shape[0]
    lb, ub = bounds[:,0], bounds[:,1]
    sampler = qmc.Sobol(d=dim, scramble=True, seed=0)
    Xcand_unit = sampler.random(n_candidates)
    Xcand = qmc.scale(Xcand_unit, bounds[:,0], bounds[:,1])
    vals = acquisition(gpr, Xcand, acq=acq, beta=beta, xi=xi,
                       minimize=minimize, Y_best=Y_best)
    best_idx = np.argmin(vals)
    return Xcand[best_idx].reshape(1, -1)


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
    else:
        raise ValueError(f"Unknown func={func}")

    # 初始数据
    X = np.random.uniform(bounds[:,0], bounds[:,1], size=(n_init, dim))
    Y = objective(X)

    # ---------- GPR 模型 ----------
    # 核函数
    if kernel_type == "rbf":
        kernel = C(1.0, (1e-3, 1e3)) * RBF(length_scale=np.ones(dim),
                                           length_scale_bounds=(1e-2, 1e2))
    elif kernel_type == "matern":
        kernel = C(1.0, (1e-3, 1e3)) * Matern(length_scale=np.ones(dim),
                                             length_scale_bounds=(1e-2, 1e2),
                                             nu=2.5)   # nu=2.5 常用，平滑但不过于僵硬
    else:
        raise ValueError(f"Unknown kernel_type={kernel_type}")
    
    # GPR
    kernel = C(1.0, (1e-3, 1e3)) * RBF(length_scale=np.ones(dim), length_scale_bounds=(1e-2, 1e2))# 给定合理的 length_scale_bounds，避免搜索过大范围：
    # kernel = C(0.5) * RBF(length_scale=np.ones(dim))
    gpr = GaussianProcessRegressor(kernel=kernel, alpha=1e-6, normalize_y=True, n_restarts_optimizer=5)

    # 初始拟合
    gpr.fit(X, Y.ravel())

    history_X = X.copy()
    history_Y = Y.copy()

    # ---------- BO 主循环 ----------
    for it in range(n_iter):
        Y_best = np.min(Y) if acq in ["ei", "pi"] else None
        # 生成候选点
        # 1. 随机采样
        # Xcand = np.random.uniform(bounds[:,0], bounds[:,1], size=(n_candidates, dim))
        # 2. Sobol
        # sampler = qmc.Sobol(d=dim, scramble=True, seed=0)
        # Xcand_unit = sampler.random(n_candidates)
        # Xcand = qmc.scale(Xcand_unit, bounds[:,0], bounds[:,1])

        
        # # 计算采集函数值
        # acq_vals = acquisition(gpr, Xcand, acq=acq, beta=0.1, xi=0.01, minimize=True, Y_best=Y_best)
        # idx = np.argmin(acq_vals)

        # # 观测下一个点
        # x_next = Xcand[idx:idx+1]
        # y_next = objective(x_next)

        # if 0:
        # # run local optimization from seeds
        #     k = 5
        #     seed_idx = np.argsort(acq_vals)[:k]  # argmin convention
        #     seeds = Xcand[seed_idx]
        #     #
        #     best_val = np.inf
        #     best_x = None
        #     for s in seeds:
        #         x_opt, val = optimize_acq_local(gpr, s, bounds, 
        #                                         acq_fn=lambda model, xx: acquisition(model, xx, acq=acq, beta=0.1, xi=0.01, minimize=True, Y_best=Y_best))
        #         if val < best_val:
        #             best_val = val
        #             best_x = x_opt

        #     x_next = best_x.reshape(1,-1)
        #     y_next = objective(x_next)


        if acq_optimizer == "random":
            x_next = optimize_acquisition_random(gpr, bounds, acq=acq,
                                                beta=1, xi=0.01,
                                                minimize=True, Y_best=Y_best,
                                                n_candidates=10000)
        elif acq_optimizer == "cmaes":
            x_next = optimize_acquisition_cma(gpr, bounds, acq=acq,
                                            beta=1, xi=0.01,
                                            minimize=True, Y_best=Y_best,
                                            popsize=200, max_iter=100)
        elif acq_optimizer == "ga":
            x_next = optimize_acquisition_ga(gpr, bounds, acq=acq,
                                            beta=1, xi=0.01, 
                                            minimize=True, Y_best=Y_best,
                                            popsize=200, generations=100)
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
    
    # 收敛曲线
    fig2, ax = plt.subplots(figsize=(8, 4))
    ax.semilogy(np.arange(len(history_Y)), history_Y, 'o-', label='Objective Value')
    # ax.axhline(y=0, color='r', linestyle='--', label='True Minimum (0)')
    ax.set_xlabel('Iteration')
    ax.set_ylabel('Objective Value')
    ax.set_title('Convergence Curve')
    ax.legend()
    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    # 可以自由组合：目标函数 × 采集函数
    # for func in ["sphere", "rosenbrock", "ackley"]:
        # for acq in ["ucb", "ei", "pi"]:

    run_highdim_bo(func="sphere", dim=12,
                    acq="ucb", kernel_type="rbf", acq_optimizer="ga",
                    n_init=50, n_iter=80)
