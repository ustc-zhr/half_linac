
from scipy.stats import norm
import numpy as np

# ----- 采集函数 -----
def acquisition(gpr, Xcand, acq="ucb", beta=2.0, xi=0.01, Y_best=None):
    mu, std = gpr.predict(Xcand, return_std=True)
    mu = mu.ravel()
    std = std.ravel() + 1e-12  # 避免除零

    if acq == "ucb":
        # Upper Confidence Bound
        return mu + beta * std

    elif acq == "ei":
        # Expected Improvement
        if Y_best is None:
            raise ValueError("EI requires Y_best (best observed value so far).")

        imp = mu - Y_best - xi
        Z = imp / std
        ei = imp * norm.cdf(Z) + std * norm.pdf(Z)
        return ei

    elif acq == "pi":
        if Y_best is None:
            raise ValueError("PI requires Y_best.")

        imp = mu - Y_best - xi
        Z = imp / std
        pi = norm.cdf(Z)
        return pi  # 越大越好 → 转换为 argmin
    else:
        raise ValueError(f"Unknown acquisition {acq}")

# 采用ucb采集函数时，beta的调度策略
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