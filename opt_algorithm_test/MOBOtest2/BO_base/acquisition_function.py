
from scipy.stats import norm

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