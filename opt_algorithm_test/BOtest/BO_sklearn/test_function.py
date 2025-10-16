
import numpy as np

# ----- 目标函数 -----
def sphere(X):
    # 全局极小值在原点
    return np.sum(X**2, axis=1).reshape(-1, 1)

def rosenbrock(X):
    # 全局极小值在(1,1,...,1)
    return -1*np.sum(100.0 * (X[:,1:] - X[:,:-1]**2.0)**2.0 + (1 - X[:,:-1])**2.0,
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
