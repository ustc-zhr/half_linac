
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
# from acquisition_function import acquisition

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

        if acq_optimizer == "random":
            x_next = optimize_acquisition_random(gpr, bounds, acq=acq,
                                                beta=1, xi=0.01,
                                                minimize=True, Y_best=Y_best,
                                                n_candidates=10000)
        elif acq_optimizer == "sobol":
            x_next = optimize_acquisition_sobol(gpr, bounds, acq=acq,
                                                beta=1, xi=0.01,
                                                minimize=True, Y_best=Y_best,
                                                n_candidates=10000)
        elif acq_optimizer == "sobol_local":
            x_next = optimize_acquisition_sobol(gpr, bounds, acq=acq,
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
                    acq="ucb", kernel_type="rbf", acq_optimizer="cmaes",
                    n_init=50, n_iter=80)
