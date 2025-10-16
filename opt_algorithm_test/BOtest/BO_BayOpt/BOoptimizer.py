"""
Created on Thu Jun 20 14:23:28 2024

BayesianOptimization class for EPICS-based optimization test @ HLS2 Linac
    基于bayesian-optimization 3.1.0 库实现的的BO优化类
    https://bayesian-optimization.github.io/BayesianOptimization/3.1.0/index.html
"""

import matplotlib.pyplot as plt
import numpy as np

from bayes_opt import BayesianOptimization
from bayes_opt import acquisition





class BOOptimizer:
    """
    注意：
    - 该库的核函数默认使用Matern 2.5 核   如需更改 请使用该库底层依赖的scikit-learn
    """

    def __init__(self, func, bounds, acq: str = "EI", kernel_para: float = 2.576, random_state: int = 100):
        """
        Parameters:
            func (callable): 目标函数 f(**params) -> float
            bounds (dict): 搜索空间，如 {"x": (-1, 1), "y": (0, 5)}
            acq (str): 采集函数名称，仅支持 "ei", "ucb", "poi"
            kappa, xi: 控制探索/利用权衡：
                - "UCB" 使用 kappa [UCB = mean + kappa*std]
                - "EI"/"PI" 使用 xi
            random_state (int): 随机种子
        """
        self.func = func
        self.bounds = bounds
        self.random_state = random_state
        if acq == "UCB":
            self.acquisition_function = acquisition.UpperConfidenceBound(kernel_para)
        
        if acq == "EI":
            self.acquisition_function = acquisition.ExpectedImprovement(kernel_para)
        
        if acq == "PI":
            self.acquisition_function = acquisition.ProbabilityOfImprovement(kernel_para)


        self.opt = BayesianOptimization(
            f=self.func,
            pbounds=self.bounds,
            acquisition_function = self.acquisition_function,
            random_state=self.random_state,
            verbose=0 # control the output
        )

    def maximize(self, n_iter: int = 10, init_points: int = 5, save_path: str = None):
        """
        执行贝叶斯优化。
        params:
            n_iter: 优化迭代步数。
            init_points: 随机初始评估点数。
            save_path: 如果提供，将保存优化状态到指定文件路径。
        return: 
            显示的当前最佳结果 dict (包含 "target" 和 "params")。
        """
        self.opt.maximize(
            init_points=init_points,
            n_iter=n_iter,
        )
        result = self.opt.max
        if save_path:
            self.opt.save_state(save_path)

        return result

    def load_state(self, path: str):
        """
        从文件恢复之前保存的状态。
        """
        self.opt.load_state(path)

    @property
    def history(self):
        """
        返回所有 eval 历史，形式为 list of dicts: {"target", "params"}。
        """
        return self.opt.res

    @property
    def best(self):
        """
        返回当前最优结果 (same as self.opt.max)。
        """
        return self.opt.max

    def plot_convergence(self):
        """
        绘制优化过程的收敛曲线
        """
        if not self.history:
            raise ValueError("没有优化历史数据，请先运行 maximize() 方法")

        # 提取所有评估点的目标函数值
        all_targets = [res['target'] for res in self.history]
        print(all_targets)
               
        # 创建收敛曲线图
        plt.figure(figsize=(10, 6))
        min_values = np.maximum.accumulate(all_targets)
        plt.plot(all_targets, 'b-', linewidth=1.5, label='Current function value')
        plt.plot(min_values, 'r--', linewidth=1.5, label='Historical minimum value')
 
        plt.xlabel('Number of function evaluations')
        plt.ylabel('function value')
        plt.title('Convergence curve')
        plt.legend()
        plt.grid(True)
        
        plt.tight_layout()
        plt.show()
        # return fig
    


