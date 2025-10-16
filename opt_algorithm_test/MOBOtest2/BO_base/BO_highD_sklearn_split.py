
# minimal_sklearn_ucb.py
# high dimensional version
import numpy as np
import time
import matplotlib.pyplot as plt
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, Matern, ConstantKernel as C
# from scipy.stats import norm
# from scipy import optimize
# from scipy.stats import qmc  
# import cma

from optimize_acquisition import *
from acquisition_function import *
from test_function import *

np.random.seed(100)

# ----- 主函数 -----
def run_bo(func="sphere", acq="ucb", kernel_type="rbf", acq_optimizer="ga", dim=25, n_init=20, n_iter=10):
    
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
    plt.plot(history_Y, 'o-', label='Objective Value')
    plt.plot(min_values, 'r--', label='Best Value')
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
    run_bo(func="rosenbrock", dim=dim,
                    acq="ucb", kernel_type="rbf", acq_optimizer="sobol",
                    n_init=min(1, 5*dim), n_iter=5)
    
    t1 = time.time()
    print(f"Total time: {t1-t0:.2f} seconds")

    # z=rosenbrock(np.array([[1,]*4]))
    # print(z)
