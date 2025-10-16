from bayes_opt import BayesianOptimization
from bayes_opt import acquisition
import matplotlib.pyplot as plt
import numpy as np
import time
import logging
import sys

# configure the optimization log
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('BOopt.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class BOOptimizer:
    """
    基于bayesian-optimization 3.x 库实现的的BO优化类
    https://bayesian-optimization.github.io/BayesianOptimization/3.1.0/index.html
    """

    def __init__(self, func, bounds, acq: str = "EI", kappa: float = 2.576, xi: float = 0.0, random_state: int = None):
        """
        Parameters:
            func (callable): 目标函数 f(**params) -> float
            bounds (dict): 搜索空间，如 {"x": (-1, 1), "y": (0, 5)}
            acq (str): 采集函数名称，仅支持 "ei", "ucb", "poi"
            kappa, xi: 控制探索/利用权衡：
                - "UCB" 使用 kappa
                - "EI"/"PI" 使用 xi
            random_state (int): 随机种子
        """
        self.func = func
        self.bounds = bounds
        self.random_state = random_state
        if acq == "UCB":
            self.acquisition_function = acquisition.UpperConfidenceBound(kappa)
        
        if acq == "EI":
            self.acquisition_function = acquisition.ExpectedImprovement(xi)
        
        if acq == "PI":
            self.acquisition_function = acquisition.ProbabilityOfImprovement(xi)


        self.opt = BayesianOptimization(
            f=self.func,
            pbounds=self.bounds,
            acquisition_function = self.acquisition_function,
            random_state=self.random_state,
            verbose=0
        )

    def maximize(self, n_iter: int = 10, init_points: int = 5, save_path: str = None):
        """
        执行贝叶斯优化。

        :param n_iter: 优化迭代步数。
        :param init_points: 随机初始评估点数。
        :param save_path: 如果提供，将保存优化状态到指定文件路径。
        :return: 浏览器显示的当前最佳结果 dict (包含 "target" 和 "params")。
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
        
        # 计算每个步骤的最佳值（累积最大值）
        # best_targets = []
        # current_best = -np.inf
        # for target in all_targets:
        #     if target > current_best:
        #         current_best = target
        #     best_targets.append(current_best)
               
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
    


def test_function(**dictparams):
    # dict -> list/array
    x = [dictparams[k] for k in sorted(dictparams.keys())]  
    obj_val = -(x[0] - 2)**2 - (x[1] - 3)**2 + 10 # 在 (2,3) 处有最大值 10
    # 将数据保存到文件
    
    # add_value = np.concatenate([np.array([x]), np.array([y]), np.array([obj_val])])
    # print(add_value)
    # with open('templete.txt', 'a') as file:
    #     file.write(f"{x:.6f}, {y:.6f}, {obj_val:.6f}\n")
        

    return obj_val
# ---------------- 示例 ----------------
if __name__ == "__main__":
    start_time = time.time()

    try:

        bounds = {"x": (-5, 5), "y": (-5, 5)}

        optimizer = BOOptimizer(
            func=test_function,
            bounds=bounds,
            acq="UCB",
            xi=0.1,
            random_state=142
        )

        best = optimizer.maximize(n_iter=15, init_points=5, save_path="bo_state.json")
        print("Optimal:", best)
        print(f'time: {time.time() - start_time:.2f} s')

        # 绘制收敛曲线
        fig = optimizer.plot_convergence()

        # 若要继续优化或重载历史：
        # optimizer.load_state("bo_state.json")
        # print("Resumed history size:", len(optimizer.history))
        # print("Current best:", optimizer.best)

        
    except Exception as e:
        logger.error(f"错误: {e}", exc_info=True)