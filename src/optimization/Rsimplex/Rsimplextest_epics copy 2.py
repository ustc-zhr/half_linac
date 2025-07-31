import numpy as np
import matplotlib.pyplot as plt
import time
from mpl_toolkits.mplot3d import Axes3D
import math
from epics import caput_many, PV, caget_many, caget
import logging
import sys

import half_linac.setup as st

# configure the optimization log
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(st.rootpath+'/src/optimization/Rsimplex/Rsimplexopt.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class RSimplex:
    """
    鲁棒单纯形算法(Robust Simplex Algorithm)
    Ref: PHYSICAL REVIEW ACCELERATORS AND BEAMS 21, 104601 (2018)
    """
    
    def __init__(self, func, start_point=None, vrange=None, knobs_list=None,
                 step_size=0.1, noise=None, M1=1.4, M2=2.0, max_samples=3, rebuild_fraction=0.6, interval=None,
                 tol=1e-6, max_iters=1000, max_evaluations=1000,
                 obj_pvnames=None, obj_weights=None, obj_samples=None, obj_math=None):
        """
        初始化鲁棒单纯形优化器
        
        参数:
        func: 目标函数 (需最小化)
        noise_level: 函数评估的噪声标准差
        M1: 统计显著性比较参数
        M2: 重建单纯形的阈值参数
        max_evaluations: 最大函数评估次数
        max_samples: 单点最大采样次数
        rebuild_fraction: 重建单纯形的步长比例
        """
        self.func = func
        self.start_point = start_point
        self.vrange = vrange
        self.knobs_list = knobs_list if knobs_list is not None else []

        self.step_size = step_size
        self.sigma = noise
        self.M1 = M1
        self.M2 = M2
        self.max_samples = max_samples
        self.rebuild_frac = rebuild_fraction
        self.interval = interval

        self.tol = tol
        self.max_iters = max_iters
        self.max_evals = max_evaluations

        # EPICS相关
        self.obj_pvnames = obj_pvnames if obj_pvnames is not None else []
        self.obj_weights = obj_weights
        self.obj_samples = obj_samples 
        self.obj_math = obj_math
        self.use_epics = len(self.obj_pvnames) > 0 and len(self.knobs_list) > 0
        
        # 优化过程记录
        self.history = {
            'evaluations': 0,
            'eval_values': [],
            'eval_point': [],
            'iterations': 0,
            'iter_values': [],
            'simplex_sizes': [],
            'actions': [] 
        }

    def _epics_func(self, x):
        # 设置参数PV
        for i, pvname in enumerate(self.knobs_list):
            pv = PV(pvname)
            pv.put(x[i])
            
        time.sleep(0.5)  

        pv = PV(self.obj_pvnames)
        total = -1*pv.get(self.obj_pvnames)
        time.sleep(0.5) 

        return total

    def _normalized_evaluate(self, x_norm, save_path='template.opt'):
        """
        评估目标函数，并记录评估次数
        
        参数:
        x_norm: 归一化的输入向量
        save_path: 历史数据保存路径(可选)
        
        返回:
        目标函数值
        """
        x_real = self.vrange[:, 0] + (self.vrange[:, 1] - self.vrange[:, 0]) * x_norm
        obj_value = self._epics_func(x_real) if self.use_epics else self.func(x_real)

        self.history['evaluations'] += 1
        self.history['eval_point'].append(x_real)
        self.history['eval_values'].append(obj_value)

        try:
            # 将历史数据转换为结构化数组并保存
            points = np.array(self.history['eval_point'])
            values = np.array(self.history['eval_values'])

            np.savetxt(save_path,
                  np.concatenate([points, values.reshape(-1,1)], axis=1),
                  fmt='%.6f')
            
        except Exception as e:
            print(f"保存优化历史数据失败: {str(e)}")
                
        return obj_value
    
    def simplexmain(self):
        """
        执行Rsimplex优化过程
        """

        simplex = self._initialize_simplex(self.start_point, self.step_size)
        f_values = []
        f_stds = []
        sample_counts = []
        
        # 初始评估
        for point in simplex:
            f, std, count = self._sample_point(point) # sample 1次
            f_values.append(f)
            f_stds.append(std)
            sample_counts.append(count)
        
        best_value = min(f_values)
        self.history['iter_values'].append(best_value)
        self.history['simplex_sizes'].append(self._simplex_size(simplex))
        
        iteration = 0
        rebuild_count = 0
        
        while self.history['evaluations'] < self.max_evals:
            
            # 1. 排序顶点
            sorted_indices = np.argsort(f_values)
            simplex = simplex[sorted_indices]
            f_values = [f_values[i] for i in sorted_indices]
            f_stds = [f_stds[i] for i in sorted_indices]
            sample_counts = [sample_counts[i] for i in sorted_indices]
            
            # 计算顶点值极差
            f_range = f_values[-1] - f_values[0]
            
            # 检查收敛
            if f_range < self.tol:
                print('结果收敛')
                break

            iteration += 1
            print("迭代: ", iteration)
            self.history['iterations'] += 1
            
            
            # 2. 确定最大顶点组G_max
            G_max = []
            # print("all f_values:" ,f_values)
            for i in range(len(simplex)-1, 0, -1):
                # 检查是否与最大值足够接近
                # print("顶点组G_max: ", i)
                # print('input:', f_values[i], f_stds[i], f_values[-1], f_stds[-1])
                comp = self._compare(f_values[i], f_stds[i], 
                                    f_values[-1], f_stds[-1])
                if comp == 0:  # 无法区分
                    G_max.append(i)
                else:
                    break
            
            # 添加最大值顶点
            # G_max.append(len(simplex)-1)
            # print(G_max)
            if len(G_max) > 4:  # 限制组大小
                G_max = G_max[:4]
            
            vertex_replaced = False
            
            # 3. 对G_max中每个顶点尝试操作
            for vertex_idx in G_max:
                if vertex_replaced:
                    break
                
                # 计算质心（排除当前顶点）
                Xc = self._compute_centroid(simplex, vertex_idx)
                
                # 反射点
                Xr = 2 * Xc - simplex[vertex_idx]
                fr, stdr, countr = self._sample_point(Xr)
                
                # 尝试反射/扩张
                comp_best = self._compare(fr, stdr, f_values[0], f_stds[0])
                if comp_best == -1:  # fr < f1
                    # 扩张点
                    Xe = 3 * Xc - 2 * simplex[vertex_idx]
                    fe, stde, counte = self._sample_point(Xe)
                    
                    # 比较反射点和扩张点
                    comp_exp = self._compare(fr, stdr, fe, stde)
                    if comp_exp == -1:  # fr < fe
                        new_point = Xr
                        new_f = fr
                        new_std = stdr
                        action = "expansion (reflection)"
                    elif comp_exp == 1:  # fr > fe
                        new_point = Xe
                        new_f = fe
                        new_std = stde
                        action = "expansion (expansion)"
                    else:  # 无法确定
                        # 取中点
                        Xm = (Xr + Xe) / 2
                        fm, stdm, countm = self._sample_point(Xm)
                        new_point = Xm
                        new_f = fm
                        new_std = stdm
                        action = "expansion (midpoint)"
                    
                    # 替换顶点
                    simplex[vertex_idx] = new_point
                    f_values[vertex_idx] = new_f
                    f_stds[vertex_idx] = new_std
                    vertex_replaced = True
                    self.history['actions'].append(action)
                
                else:
                    # 比较fr和fn
                    comp_fn = self._compare(fr, stdr, f_values[-2], f_stds[-2])
                    if comp_fn == -1:  # fr < fn
                        # 使用反射点替换
                        simplex[vertex_idx] = Xr
                        f_values[vertex_idx] = fr
                        f_stds[vertex_idx] = stdr
                        vertex_replaced = True
                        self.history['actions'].append("reflection")
                    else:
                        # 收缩操作
                        if comp_fn == 1:  # fr > fn
                            # 内收缩
                            Xic = (Xc + simplex[vertex_idx]) / 2
                            fic, stdic, countic = self._sample_point(Xic)
                            
                            # 比较内收缩点
                            comp_ic = self._compare(fic, stdic, f_values[vertex_idx], f_stds[vertex_idx])
                            if comp_ic == -1:  # fic < f_vertex
                                simplex[vertex_idx] = Xic
                                f_values[vertex_idx] = fic
                                f_stds[vertex_idx] = stdic
                                vertex_replaced = True
                                self.history['actions'].append("inner contraction")
                        else:  # 无法确定
                            # 外收缩
                            Xoc = (Xc + Xr) / 2
                            foc, stdoc, countoc = self._sample_point(Xoc)
                            
                            # 二次拟合
                            points = [
                                simplex[vertex_idx],  # α = -1
                                (simplex[vertex_idx] + Xc) / 2,  # α = -0.5 (内收缩点)
                                Xc,  # α = 0
                                Xoc,  # α = 0.5 (外收缩点)
                                Xr    # α = 1
                            ]
                            values = [
                                f_values[vertex_idx],
                                f_values[vertex_idx] if len(points) < 2 else self._normalized_evaluate(points[1]),
                                self._normalized_evaluate(Xc),
                                foc,
                                fr
                            ]
                            stds = [
                                f_stds[vertex_idx],
                                f_stds[vertex_idx],
                                self.sigma,
                                stdoc,
                                stdr
                            ]
                            
                            a, b, c = self._quadratic_fit(points, values, stds)
                            
                            if a is not None and b is not None:
                                # 计算内收缩点改进量
                                delta_ic = (3/4)*a - (1/2)*b
                                
                                # 计算外收缩点改进量
                                delta_oc = (3/4)*a + (1/2)*b
                                
                                if b > 0 and delta_ic > self.M1 * 0.7 * self.sigma:
                                    simplex[vertex_idx] = points[1]  # 内收缩点
                                    f_values[vertex_idx] = values[1]
                                    vertex_replaced = True
                                    self.history['actions'].append("quadratic inner contraction")
                                elif b < 0 and delta_oc > self.M1 * 0.7 * self.sigma:
                                    simplex[vertex_idx] = points[3]  # 外收缩点
                                    f_values[vertex_idx] = values[3]
                                    vertex_replaced = True
                                    self.history['actions'].append("quadratic outer contraction")
            
            # 4. 如果没有顶点被替换
            if not vertex_replaced:
                # 检查是否需要压缩或重建
                if f_range < self.M2 * self.sigma:
                    # 重建单纯形
                    best_idx = np.argmin(f_values)
                    best_point = simplex[best_idx]
                    simplex = self._rebuild_simplex(best_point, step_size)
                    
                    # 重新评估所有点
                    f_values = []
                    f_stds = []
                    for point in simplex:
                        f, std, count = self._sample_point(point)
                        f_values.append(f)
                        f_stds.append(std)
                    
                    rebuild_count += 1
                    self.history['actions'].append(f"rebuild simplex ({rebuild_count})")
                else:
                    # 压缩操作
                    best_idx = np.argmin(f_values)
                    for i in range(len(simplex)):
                        if i != best_idx:
                            simplex[i] = (simplex[i] + simplex[best_idx]) / 2
                            f, std, count = self._sample_point(simplex[i])
                            f_values[i] = f
                            f_stds[i] = std
                    self.history['actions'].append("shrink")
            
            # 记录最佳值和单纯形大小
            current_best = min(f_values)
            if current_best < best_value:
                best_value = current_best
            self.history['iter_values'].append(best_value)
            self.history['simplex_sizes'].append(self._simplex_size(simplex))
            
            # 检查评估次数限制
            if self.history['evaluations'] >= self.max_evals or self.history['iterations'] >= self.max_iters:
                break
        
        # 找到最佳点
        best_idx = np.argmin(f_values)
        return simplex[best_idx], self.history

  


    
    def _sample_point(self, x, min_samples=3):
        """
        对点进行多次采样取平均以减少噪声影响
        自适应采样直到满足统计显著性条件或达到最大采样次数
        """
        samples = []
        # 初始采样
        for _ in range(min_samples):
            samples.append(self._normalized_evaluate(x))
        
        # 计算当前平均值和标准差
        f_mean = np.mean(samples)
        f_std = np.std(samples, ddof=1) if len(samples) > 1 else self.sigma
        
        # 如果样本标准差小于噪声水平，使用噪声水平
        std_est = max(f_std, self.sigma)
        
        return f_mean, std_est, len(samples)
    
    def _compare(self, f1, std1, f2, std2):
        """
        统计比较两个函数值
        返回:
        -1: f1 < f2
         0: 无法确定
         1: f1 > f2
        """
        diff = f1 - f2
        # std_diff = np.sqrt(std1**2 + std2**2)
        std_diff = np.sqrt(2/3)*self.sigma
        # print("diff", diff, 'std_diff', std_diff)
        # 统计显著性检验
        if abs(diff) > self.M1 * std_diff:
            return -1 if diff < 0 else 1
        return 0
    
    def _quadratic_fit(self, points, values, stds):
        """在反射线上进行二次曲线拟合以指导决策"""
        # 确保有足够的点进行拟合
        if len(points) < 3:
            return None, None, None
        
        # 准备拟合数据
        alphas = np.array([-1, -0.5, 0, 0.5, 1])[:len(points)]
        weights = 1 / (np.array(stds[:len(points)]) + 1e-10)
        
        # 加权最小二乘拟合
        A = np.vstack([alphas**2, alphas, np.ones(len(alphas))]).T
        coeffs, residuals, _, _ = np.linalg.lstsq(A * weights[:, None], 
                                                np.array(values) * weights, 
                                                rcond=None)
        # coeffs, residuals, _, _ = np.linalg.lstsq(A, 
        #                                         np.array(values), 
        #                                         rcond=None)
        
        a, b, c = coeffs
        return a, b, c
    
    def _initialize_simplex(self, start, step_size):
        """构建初始单纯形"""
        n = len(start)
        simplex = [start]
        
        # 沿每个坐标轴方向构建初始点
        for i in range(n):
            point = start.copy()
            point[i] += step_size
            # print(point)
            simplex.append(point)
        
        return np.array(simplex)
    
    def _compute_centroid(self, simplex, exclude_idx):
        """计算排除指定顶点后的质心"""
        mask = np.ones(len(simplex), dtype=bool)
        mask[exclude_idx] = False
        return np.mean(simplex[mask], axis=0)
    
    def _rebuild_simplex(self, best_point, step_size):
        """围绕当前最优点重建单纯形"""
        n = len(best_point)
        new_simplex = [best_point]
        
        # 使用重建比例缩放步长
        step = step_size * self.rebuild_frac
        
        # 构建新单纯形
        for i in range(n):
            point = best_point.copy()
            point[i] += step
            new_simplex.append(point)
        
        return np.array(new_simplex)
    
    def _simplex_size(self, simplex):
        """计算单纯形的大小（顶点间最大距离）"""
        distances = []
        for i in range(len(simplex)):
            for j in range(i+1, len(simplex)):
                distances.append(np.linalg.norm(simplex[i] - simplex[j]))
        return max(distances) if distances else 0
    
        # 更简洁的实现（但效率相同）
        # from scipy.spatial.distance import pdist
        # return np.max(pdist(simplex)) if len(simplex) > 1 else 0

    def plot_optimization_process(self, best_point, history):
        # 可视化优化过程
        plt.figure(figsize=(12, 8))
        
        # 最佳值变化
        plt.subplot(2, 2, 1)
        plt.plot(history['iter_values'])
        plt.title('Best Function Value History')
        plt.xlabel('Iteration')
        plt.ylabel('Function Value')
        plt.yscale('log')
        plt.grid(True)
        
        # 单纯形大小变化
        plt.subplot(2, 2, 2)
        plt.plot(history['simplex_sizes'])
        plt.title('Simplex Size History')
        plt.xlabel('Iteration')
        plt.ylabel('Size')
        plt.yscale('log')
        plt.grid(True)
        
        # 操作类型分布
        plt.subplot(2, 2, 3)
        actions = history['actions']
        unique_actions, counts = np.unique(actions, return_counts=True)
        plt.bar(unique_actions, counts)
        plt.title('Action Distribution')
        plt.xlabel('Action Type')
        plt.ylabel('Count')
        plt.xticks(rotation=45)
        
        # 最终位置
        plt.subplot(2, 2, 4)
        print(best_point)
        plt.plot(best_point, '*-')
        plt.title('Optimized Parameters')
        plt.xlabel('Parameter Index')
        plt.ylabel('Value')
        plt.grid(True)
        
        plt.tight_layout()
        # plt.savefig('rsimplex_optimization.png', dpi=300)
        # plt.show()

        plt.figure(figsize=(10, 6))
        plt.plot(history['eval_values'])
        plt.title('Function evaluation History')
        plt.xlabel('Number of function evaluations')
        plt.ylabel('Function Value')
        plt.yscale('log')
        plt.grid(True)
        plt.show()


def rosenbrock(x, noise_level=0.0):
    """
    Rosenbrock函数(香蕉函数)
    全局最小值在(1,1,...,1), 值为0
    
    参数:
    x: 输入向量
    noise_level: 添加的高斯噪声标准差
    
    返回:
    Rosenbrock函数值 + 高斯噪声
    """
    n = len(x)
    result = 0
    for i in range(n-1):
        result += 100 * (x[i+1] - x[i]**2)**2 + (1 - x[i])**2
    
    # 添加高斯噪声
    if noise_level > 0:
        result += np.random.normal(0, noise_level)
    
    return result


# 测试RSimplex算法
if __name__ == "__main__":
    np.random.seed(42)

    # param_pvs = ['IRFEL:PS:HC05:current:ao',
    # 				      'IRFEL:PS:HC06:current:ao',
    # 				      'IRFEL:PS:HC07:current:ao',
    # 				      'IRFEL:PS:VC05:current:ao',
    # 				      'IRFEL:PS:VC06:current:ao',
    # 				      'IRFEL:PS:VC07:current:ao'
    # 				      ]
    # start_point = []
    # for i, pvname  in enumerate(param_pvs):
    #     start_point[i]+=epics.caget(pvname)

    
    # 参数设置
    dim = 6  # 问题维度
    noise_level = 0.0  # 噪声水平
    start_point = 0.5*np.ones(dim)  # 初始点(0,0,...,0)
    step_size = 0.08  # 初始步长
    vrange = np.array([[-5, 5], [-5, 5], [-5, 5], [-5, 5], [-5, 5], [-5, 5]])

    # 创建优化器
    optimizer = RSimplex(lambda x: rosenbrock(x, noise_level), 
                         start_point = start_point,
                         step_size = step_size,
                         vrange=vrange,
                         noise=noise_level, 
                         max_iters=1000, max_evaluations=1500)
    
    # 执行优化
    best_point, history = optimizer.simplexmain()
    
    # 输出结果
    print(f"\n优化结果:")
    print(f"迭代次数: {history['iterations']}")
    print(f"函数评估次数: {history['evaluations']}")
    print(f"找到的最优解: {best_point}")
    print(f"最优解的函数值: {rosenbrock(best_point):.6f}")
    print(f"历史最佳值: {min(history['iter_values']):.6f}")
    print(f"最终单纯形大小: {history['simplex_sizes'][-1]:.6f}")
    # print(history['allpoint'])
    
    # 结果绘图
    optimizer.plot_optimization_process(best_point, history)


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
            # RCDS setup
            step = float(sys.argv[5])
            maxIt = int(sys.argv[6])
            noise = float(sys.argv[7])
            interval = float(sys.argv[8])
            # obj
            obj_pvnames = sys.argv[9].split(',')
            obj_weights = [float(x) for x in sys.argv[10].split(',')]
            obj_samples = [int(x) for x in sys.argv[11].split(',')]
            obj_samples = max(obj_samples)
            obj_math = sys.argv[12].split(',')
            

            # 创建优化器实例
            x0 = 0.5*np.ones(len(knobs_list)) #归一化的初始点
            vrange = np.array([knobs_minus, knobs_plus]).T #基于初始点的探索空间加减范围(未归一化的)
            Dmat0 = np.eye(len(knobs_list))

            print('paras. for RCDS:')
            print(x0)
            print(vrange)
            print(step)
            print(Dmat0)
            print(knobs_list)
            print(obj_pvnames)
            
            optimizer = PowellOptimizer(
                x0=x0, vrange=vrange, knobs_list=knobs_list,
                step=step, Dmat0=Dmat0, noise = noise, interval = interval, tol=1e-8, maxIt=maxIt, maxEval=15000, 
                obj_pvnames=obj_pvnames, obj_weights=obj_weights, obj_samples=obj_samples, obj_math=obj_math 
            )

            
            # 执行Powell优化
            x_norm_opt, f_opt = optimizer.powellmain()

            x_opt = optimizer.vrange[:, 0] + (optimizer.vrange[:, 1] - optimizer.vrange[:, 0]) * x_norm_opt
            print("Optimization result:", x_opt, f_opt)
            print(f'Number of function evolutions: {optimizer.cnt}')
            print(f'time: {time.time() - start_time:.2f} s')

            # 绘制优化过程
            # optimizer.plot_optimization_process()

        
        # elif sys.argv[1] == "cor_off":
        #     # target_BPMlist = sys.argv[2].split(',')
        #     corrector = OrbitCorrector()
        #     corrector.reset_cor()
        
        # elif sys.argv[1] == "cor_recover":
        #     # target_BPMlist = sys.argv[2].split(',')
        #     corrector = OrbitCorrector()
        #     corrector.cor_recover()
    
    except Exception as e:
        logger.error(f"错误: {e}", exc_info=True)