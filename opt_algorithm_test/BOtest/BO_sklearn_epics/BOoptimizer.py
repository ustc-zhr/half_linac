import numpy as np
import time
import matplotlib.pyplot as plt
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, Matern, ConstantKernel as C

from optimize_acquisition import *
from acquisition_function import *
from test_function import *

np.random.seed(100)
class BOOptimizer:
    def __init__(self, func=None, bounds=None, 
                 kernel_type="rbf", acq="ucb", acq_para=3, acq_optimizer="ga", 
                 n_init=10, n_iter=50):
        """
        初始化贝叶斯优化器
        
        参数:
            func: 目标函数 
            acq: 采集函数 ["ucb", "ei", "pi"] 
            kernel_type: 核函数 ["rbf", "matern"]
            acq_optimizer: 优化器 ["random", "sobol", "cmaes", "ga"]
            n_init: 初始采样点数
            n_iter: 优化迭代次数
        """
        self.func = func
        self.bounds = bounds

        self.kernel_type = kernel_type
        self.acq = acq
        self.acq_para = acq_para
        self.acq_optimizer = acq_optimizer

        self.dim = bounds.shape[0]
        self.n_init = n_init
        self.n_iter = n_iter
        
        # 初始化历史记录
        self.history_X = []
        self.history_Y = []
        self.gpr = None
        
    
    def _initialize_samples(self):
        """生成初始样本点"""
        X = np.random.uniform(self.bounds[:, 0], self.bounds[:, 1], 
                             size=(self.n_init, self.dim))
        Y = self.func(X)
        return X, Y
    
    def _setup_gpr(self):
        """配置高斯过程回归模型"""
        if self.kernel_type == "rbf":
            kernel = C(1.0, (1e-3, 1e3)) * RBF(length_scale=np.ones(self.dim),
                                               length_scale_bounds=(1e-2, 1e6))
        elif self.kernel_type == "matern":
            kernel = C(1.0, (1e-3, 1e3)) * Matern(length_scale=np.ones(self.dim),
                                                  length_scale_bounds=(1e-2, 1e6),
                                                  nu=2.5) # nu=2.5 常用，平滑但不过于僵硬
        else:
            raise ValueError(f"Unknown kernel_type={self.kernel_type}") 
        
        self.gpr = GaussianProcessRegressor(kernel=kernel,alpha=1e-6,
            normalize_y=True, n_restarts_optimizer=5)
    
    def optimize(self):
        """执行贝叶斯优化主流程"""
        print(f"\n=== 运行BO ({self.func}, acq={self.acq}) ===")
        
        # 初始化
        X, Y = self._initialize_samples()
        self._setup_gpr()
        self.gpr.fit(X, Y.ravel())
        
        self.history_X = X.copy()
        self.history_Y = Y.copy()
        
        # 优化循环
        for it in range(self.n_iter):
            t0 = time.time()
            
            # 获取下一个采样点
            Y_best = np.max(Y) if self.acq in ["ei", "pi"] else None
            beta = beta_schedule(it, beta0=self.acq_para, strategy="inv_decay", lam=0.05)
            x_next = self._optimize_acquisition(Y_best, beta)
            y_next = self.func(x_next)
            
            # 更新数据集
            X = np.vstack([X, x_next])
            Y = np.vstack([Y, y_next])
            self.history_X = np.vstack([self.history_X, x_next])
            self.history_Y = np.vstack([self.history_Y, y_next])
            
            # 更新模型
            self.gpr.fit(X, Y.ravel())
            
            # 打印进度
            t1 = time.time()
            print(f"Iter {it+1:02d}: f(x)={y_next.ravel()[0]:.4f}, time: {t1-t0:.2f}s")
    
    def _optimize_acquisition(self, Y_best, beta):
        """优化采集函数"""
        # 这里应实现您的optimize_acquisition_*函数逻辑
        # 示例伪代码:
        if self.acq_optimizer == "random":
            x_next = optimize_acquisition_random(self.gpr, self.bounds, acq=self.acq,
                                                beta=beta, xi=0.01,
                                                Y_best=Y_best,
                                                n_candidates=3000)
        elif self.acq_optimizer == "sobol":
            x_next = optimize_acquisition_sobol(self.gpr, self.bounds, acq=self.acq,
                                                beta=beta, xi=0.01,
                                                 Y_best=Y_best,
                                                n_candidates=3000)
        elif self.acq_optimizer == "sobol_local":
            x_next = optimize_acquisition_sobol(self.gpr, self.bounds, acq=self.acq,
                                                beta=beta, xi=0.01,
                                                Y_best=Y_best,
                                                n_candidates=3000, k=5)
        elif self.acq_optimizer == "cmaes":
            x_next = optimize_acquisition_cma(self.gpr, self.bounds, acq=self.acq,
                                            beta=beta, xi=0.01,
                                            Y_best=Y_best,
                                            popsize=min(100, 10*self.dim), max_iter=200)
        elif self.acq_optimizer == "ga":
            x_next = optimize_acquisition_ga(self.gpr, self.bounds, acq=self.acq,
                                            beta=beta, xi=0.01, 
                                            Y_best=Y_best,
                                            popsize=min(100, 10*self.dim), generations=200)
        else:
            raise ValueError(f"Unknown acq_optimizer={self.acq_optimizer}")
        
        return x_next
    
    def plot_convergence(self):
        """绘制优化过程的收敛曲线"""

        # 收敛曲线
        plt.figure(figsize=(12, 4))
        plt.subplot(121)
        min_values = np.maximum.accumulate(self.history_Y)
        plt.plot(self.history_Y, 'o-', linewidth=1.5, label='objective value')
        plt.plot(min_values, 'r--', linewidth=1.5, label='best value')

        plt.xlabel('evaluations')
        plt.ylabel('Values')
        plt.title('Convergence curve')
        plt.legend()
        plt.grid(True)
        
        # 参数空间投影（2D时）
        if self.dim == 2:
            plt.subplot(122)
            plt.scatter(self.history_X[:,0], self.history_X[:,1], 
                       c=range(len(self.history_X)))
            plt.colorbar(label='iteration')
            plt.xlabel('dim 1')
            plt.ylabel('dim 2')
        
        plt.tight_layout()
        plt.show()
       

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
    t0 = time.time()
    dim=2
    func, bounds = setup_objective("rosenbrock", dim=dim)
    print(type(bounds))
    print(bounds)
    # 定义BO优化器参
    # bo = BOOptimizer(
    #     func=func,
    #     bounds=bounds,
    #     acq="ucb",
    #     kernel_type="rbf",
    #     acq_optimizer="sobol",
    #     n_init=min(1, 5*dim),
    #     n_iter=50
    # )
    
    

    # bo.optimize()
    # print(f'time: {time.time() - t0:.2f} s')
    
    # bo.plot_convergence()
