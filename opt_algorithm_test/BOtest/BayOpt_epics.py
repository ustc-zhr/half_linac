from bayes_opt import BayesianOptimization
from bayes_opt import acquisition
import matplotlib.pyplot as plt
import numpy as np
import time
import logging
import sys
from epics import caget, PV, caput_many, caget_many

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
    
class TargetIoc:
    def __init__(self, obj_pvnames=None, obj_weights=None, obj_samples=None, obj_math=None, interval=None):
        # EPICS相关
        self.obj_pvnames = obj_pvnames
        self.obj_weights = obj_weights
        self.obj_samples = obj_samples
        self.obj_math = obj_math

        self.interval = interval

        self.data = []# 用于记录所有评估目标函数的数据

        self._init_knob_pv() #获得pv通道名
    
    def _init_knob_pv(self):
        """
        得到变量的pv名列表以及变量的绝对变化范围
        """
        self.knobs_pvlist = []
        self.knobs_pvnames = []
        for knob in self.knobs_list:
            if 'C' in knob:
                # self.knobs_pvlist.append(PV(f"HALF:IN:COR:{knob}:ao"))
                self.knobs_pvnames.append(f"HALF:IN:COR:{knob}:ao")
            if 'Q' in knob:
                # self.knobs_pvlist.append(PV(f"HALF:IN:QUAD:{knob}:ao"))
                self.knobs_pvnames.append(f"HALF:IN:QUAD:{knob}:K1")

    def init_knob_value(self):
        
        self.knobs_pvvalue = caget_many(self.knobs_pvnames)
        
        return self.knobs_pvvalue

    
    def evaluate_func(self, **dictparams):
        """评估目标函数"""
        # dict -> list/array
        x = [dictparams[k] for k in sorted(dictparams.keys())] 
        
        # pv输入参数
        caput_many(self.knobs_pvnames, x)
        time.sleep(self.interval)
        
        # 多次采样获取目标函数值
        total = np.zeros((self.obj_samples, len(self.obj_pvnames)))
        for i in range(self.obj_samples):
            total[i, :] = caget_many(self.obj_pvnames)
            time.sleep(self.interval)
        
        # 对多次采样的目标进行数学处理：平均或标准差
        results = []
        for col, op in zip(total.T, self.obj_math):
            if op == 'mean':
                results.append(np.mean(col))
            elif op == 'std':
                results.append(np.std(col))
        # 加上权重
        obj_val = np.dot(results, self.obj_weights)

        # 记录
        self._record_evaluate(x, obj_val)
        
        return obj_val
    
    def _record_evaluate(self, x, obj_val):
        """记录优化过程中的数据"""
        self.data.append(np.concatenate((x, [obj_val])))

        # 将数据保存到文件
        np.savetxt('../template.opt',
                  np.array(self.data),
                  fmt='%.6f')

# ---------------- 示例 ----------------
if __name__ == "__main__":
    start_time = time.time()

    try:
        logger.info(f"INPUT PARAMETERS: {sys.argv}")

        if sys.argv[1] == "start_opt":
                        # 得到输入参数并转换为对应数据格式
            # knobs
            knobs_list = sys.argv[2].split(',')
            knobs_minus = [float(x) for x in sys.argv[3].split(',')]
            knobs_plus = [float(x) for x in sys.argv[4].split(',')]
            # BO setup
            step = float(sys.argv[5])
            maxIt = int(sys.argv[6])
            noise = float(sys.argv[7])
            # obj
            interval = float(sys.argv[8])
            obj_pvnames = sys.argv[9].split(',')
            obj_weights = [float(x) for x in sys.argv[10].split(',')]
            obj_samples = [int(x) for x in sys.argv[11].split(',')]
            obj_samples = max(obj_samples)
            obj_math = sys.argv[12].split(',')

            # 建立与目标函数的通道
            objhub = TargetIoc(obj_pvnames=obj_pvnames, obj_weights=obj_weights, obj_samples=obj_samples, obj_math=obj_math, interval=interval)
            vrange = np.array([knobs_minus + objhub.init_knob_value, knobs_plus + objhub.init_knob_value]).T

            bounds =  {f"x{i+1}": tuple(row) for i, row in enumerate(vrange)}

            optimizer = BOOptimizer(
                func=objhub.evaluate_func,
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