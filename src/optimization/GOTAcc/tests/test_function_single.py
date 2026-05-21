
import numpy as np

max_or_min = -1 # 1 for min  -1 for max
# ----- 目标函数 -----
def sphere(X):
    X = np.atleast_2d(X)
    # 全局极小值在原点
    return max_or_min*np.sum(X**2, axis=1).reshape(-1, 1)

# def rosenbrock(X):
#     """
#     Rosenbrock函数 - 香蕉函数
#     全局最小值: f(1,...,1) = 0
#     搜索范围: 通常 [-5, 10]^n
#     """
#     X = np.atleast_2d(X)

#     return -1*np.sum(100.0 * (X[:,1:] - X[:,:-1]**2.0)**2.0 + (1 - X[:,:-1])**2.0,
#                   axis=1).reshape(-1, 1)

def rosenbrock(X):
    X = np.array(X)
    is_1d = X.ndim == 1
    X = np.atleast_2d(X)
    
    result = max_or_min * np.sum(100.0 * (X[:, 1:] - X[:, :-1]**2.0)**2.0 + (1 - X[:, :-1])**2.0, axis=1) \
    + np.random.normal(0,0,size=X.shape[0]) * 0e-1  # 添加微小噪声以避免平坦区域
    
    if is_1d:
        return result[0]  # 返回标量或一维数组
    return result.reshape(-1, 1)  # 返回二维列向量

def ackley(X):
    """
    Ackley函数 - 高维多模态测试函数
    全局最小值: f(0,...,0) = 0
    搜索范围: 通常 [-32.768, 32.768]^n
    """
    X = np.atleast_2d(X)

    dim = X.shape[1]
    sum_sq = np.sum(X**2, axis=1)
    cos_sum = np.sum(np.cos(2 * np.pi * X), axis=1)
    return max_or_min*(-20 * np.exp(-0.2 * np.sqrt(sum_sq / dim))
            - np.exp(cos_sum / dim)
            + 20 + np.e).reshape(-1, 1)





def setup_objective(func_type, dim):
    """配置目标函数和搜索空间"""
    if func_type == "sphere":
        objective = sphere
        bounds = np.array([[-2, 2]] * dim)
    elif func_type == "rosenbrock":
        objective = rosenbrock
        bounds = np.array([[-2, 2]] * dim)
    elif func_type == "ackley":
        objective = ackley
        bounds = np.array([[-32.768, 32.768]] * dim)
    else:
        raise ValueError(f"未知目标函数: {func_type}")
    
    return objective, bounds

if __name__ == "__main__":
    # 测试生成边界字典
    dim=3
    func_type = "rosenbrock"
    func, bounds = setup_objective(func_type, dim=dim)
    print("Search bounds:", bounds)
    test_params = [1]*dim
    print("rosenbrock:", func(test_params))