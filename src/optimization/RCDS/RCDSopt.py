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
        logging.FileHandler(st.rootpath+'/src/optimization/RCDS/RCDSopt.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class PowellOptimizer:
    def __init__(self, x0=None, vrange=None, knobs_list=None,
                 step=0.1, Dmat0=None, noise=None, tol=1e-6, maxIt=100, maxEval=15000,
                 obj_pvnames=None, obj_weights=None, obj_samples=None, obj_math=None):
        """
        参数:
        x0: 初始点 (numpy数组)
        vrange: 变量范围
        step: 初始扫描步长
        Dmat0: 初始方向集合
        tol: 收敛容差
        maxIt: 最大迭代次数
        maxEval: 最大函数演化次数
        obj_pv: 目标函数值PV名
        param_pvs: 参数PV名列表
        """
        self.x0 = x0
        self.vrange = vrange
        self.knobs_list = knobs_list

        self.step = step
        self.Dmat0 = Dmat0 
        self.noise = noise # 目标函数值的噪声
        self.tol = tol
        self.maxIt = maxIt
        self.maxEval = maxEval
        
        # EPICS相关
        self.obj_pvnames = obj_pvnames
        self.obj_weights = obj_weights
        self.obj_samples = obj_samples
        self.obj_math = obj_math

        self.cnt = 0# 用于记录目标函数评估次数
        self.data = []# 用于记录所有评估目标函数的数据

        self.init_knob_pv()

    def init_knob_pv(self) -> None:
        self.knobs_pvlist = []
        self.knobs_pvnames = []
        for knob in self.knobs_list:
            if 'C' in knob:
                # self.knobs_pvlist.append(PV(f"HALF:IN:COR:{knob}:ao"))
                self.knobs_pvnames.append(f"HALF:IN:COR:{knob}:ao")
            if 'Q' in knob:
                # self.knobs_pvlist.append(PV(f"HALF:IN:QUAD:{knob}:ao"))
                self.knobs_pvnames.append(f"HALF:IN:QUAD:{knob}:K1")

        print('rangebef:', self.vrange)
        for i, pvname  in enumerate(self.knobs_pvnames):
            self.vrange[i,:]+=float(caget(pvname))
        print('rangemod:', self.vrange)

    # def init_obj_pv(self) -> None:
    #     self.obj_pvlist = []
    #     for obj in self.knobs_list:
    #         self.obj_pvlist.append(PV(obj))
 

    def _epics_func(self, x):
        """评估目标函数"""
        # 设置参数PV
        caput_many(self.knobs_pvnames, x)
        time.sleep(8)
        
        # 获取目标函数值
        total = np.zeros((self.obj_samples, len(self.obj_pvnames)))
        for i in range(self.obj_samples):
            total[i, :] = caget_many(self.obj_pvnames)
            time.sleep(1)
        

        results = []
        for col, op in zip(total.T, self.obj_math):
            if op == 'mean':
                results.append(np.mean(col))
            elif op == 'std':
                results.append(np.std(col))

        return np.dot(results, self.obj_weights)

    def _record_data(self, x, obj_val):
        """记录优化过程中的数据"""
        self.data.append(np.concatenate((x, [obj_val])))
        self.cnt += 1
        # 将数据保存到文件
        np.savetxt('template.opt',
                  np.array(self.data),
                  fmt='%.6f',
                  delimiter=',')

    def powellmain(self):
        """Direction Set (Powell's) Methods"""
        Nvar = len(self.x0)
        
        def _wrapped_func(x_norm):#将归一化变量反归一化后进行目标函数评估
            x = self.vrange[:, 0] + (self.vrange[:, 1] - self.vrange[:, 0]) * x_norm
            obj_val = self._epics_func(x)
            self._record_data(x, obj_val)
            return obj_val
        
        # 初始化当前最优解
        f0 = _wrapped_func(self.x0)
        xm = self.x0.copy()
        fm = f0

        it = 0
        Dmat = self.Dmat0.copy()
        Npmin = 6# 线性扫描时的点数
        
        while it < self.maxIt:
            it += 1
            print('iteration:', it, 'fm:', fm)
            self.step /= 1.2 # 每次迭代步长缩小
            
            k = 0
            delt = 0.0
            
            for ii in range(Nvar):
                dv = Dmat[:, ii]
                x_start = xm.copy()
                f_start = fm
                
                # 括号搜索
                x1, f1, a1, a2, xflist = self._bracketmin(_wrapped_func, x_start, f_start, dv, self.step)
                
                # 线性扫描
                x1, f1 = self._linescan(_wrapped_func, x1, f1, dv, a1, a2, Npmin, xflist) # 返回的是拟合的最小值
                
                # 更新最大改进方向
                if fm - f1 > delt:
                    delt = fm - f1
                    k = ii
                
                fm = f1
                xm = x1.copy()
            
            # 生成共轭方向
            xt = 2*xm - self.x0
            ft = _wrapped_func(xt)
            
            # 方向替换条件
            if f0 <= ft or 2*(f0-2*fm+ft)*((f0-fm-delt)/(ft-f0))**2 >= delt:
                print("zz1")
                pass
            else:
                print("zz2")
                ndv = (xm - self.x0) / np.linalg.norm(xm - self.x0)
                dotp = np.zeros(Nvar)
                for jj in range(Nvar):
                    dotp[jj] = abs(np.dot(ndv, Dmat[:, jj]))
                
                if np.max(dotp) < 0.9:# 新方向足够不同
                    # 替换方向
                    if k < Nvar - 1:
                        Dmat[:, k:Nvar-1] = Dmat[:, k+1:Nvar]
                    Dmat[:, -1] = ndv
                    
                    # 在新方向搜索
                    dv = Dmat[:, -1]
                    x_start = xm.copy()
                    f_start = fm
                    x1, f1, a1, a2, xflist = self._bracketmin(_wrapped_func, x_start, f_start, dv, self.step)

                    x1, f1 = self._linescan(_wrapped_func, x1, f1, dv, a1, a2, Npmin, xflist)

                    fm = f1
                    xm = x1.copy()
            
            # 终止条件检查
            # if self.cnt > self.maxEval:
            #     print(f'terminated, reaching function evaluation limit: {self.cnt} > {maxEval}')
            #     break
            
            # if self.tol > 0 and 2.0*abs(f0-fm) < self.tol*(abs(f0)+abs(fm)):
            #     print(f'terminated: f0={f0:.2e}, fm={fm:.2e}, f0-fm={f0-fm:.2e}')
            #     break
            
            #更新初始点 以便下次迭代
            f0 = fm
            self.x0 = xm.copy()
        
        return xm, fm

    def _bracketmin(self, func, x0, f0, dv, step):

        if np.isnan(f0) or f0 is None:
            f0 = func(x0)

        
        # 存储所有评估点
        xflist = np.array([[0.0, f0]])
        
        fm = f0
        am = 0.0
        xm = x0.copy()
        
        step_init = step
        gold_r = 1.618034
        
        # 正向搜索
        alpha = step
        x1 = x0 + dv * alpha
        f1 = func(x1)

        xflist = np.vstack([xflist, [alpha, f1]])
        
        if f1 < fm:
            fm = f1
            am = alpha
            xm = x1.copy()
        
        # 继续正向扩展
        while f1 < fm + self.noise * 3:
            if abs(alpha) < 0.1:
                alpha *= (1.0 + gold_r)
            else:
                alpha += 0.1
            
            x1 = x0 + dv * alpha
            f1 = func(x1)

            xflist = np.vstack([xflist, [alpha, f1]])
            
            if np.isnan(f1):
                alpha /= (1.0 + gold_r)
                print('bracketmin: f1=NaN')
                break
            
            if f1 < fm:
                fm = f1
                am = alpha
                xm = x1.copy()
        
        a2 = alpha
        
        # 如果初始点不是最小值，则进行反向搜索
        if f0 > fm + self.noise * 3:
            a1 = 0.0
        else:
            # 反向搜索
            alpha = -step_init
            x2 = x0 + dv * alpha
            f2 = func(x2)

            xflist = np.vstack([xflist, [alpha, f2]])
            
            if f2 < fm:
                fm = f2
                am = alpha
                xm = x2.copy()
            
            # 继续反向扩展
            while f2 < fm + self.noise * 3:
                if abs(alpha) < 0.1:
                    alpha *= (1.0 + gold_r)
                else:
                    alpha -= 0.1
                
                x2 = x0 + dv * alpha
                f2 = func(x2)

                xflist = np.vstack([xflist, [alpha, f2]])
                
                if np.isnan(f2):
                    alpha /= (1.0 + gold_r)
                    print('bracketmin: f2=NaN')
                    break
                
                if f2 < fm:
                    fm = f2
                    am = alpha
                    xm = x2.copy()
            
            a1 = alpha
        
        # 确保a1 < a2
        if a1 > a2:
            a1, a2 = a2, a1
        
        # 调整相对最小值位置
        a1 -= am
        a2 -= am
        xflist[:, 0] -= am
        
        # 按alpha排序
        sort_idx = np.argsort(xflist[:, 0])
        xflist = xflist[sort_idx]
        
        # print(a1, a2)
        return xm, fm, a1, a2, xflist

    def _linescan(self, func, x0, f0, dv, alo, ahi, Np, xflist):

        if np.isnan(f0) or f0 is None:
            f0 = func(x0)

        
        # 确保有效区间
        if alo >= ahi:
            print(f"warning of linescan: alo({alo}) >= ahi({ahi}), the default value [-0.1 0.1] is used")
            alo, ahi = -0.1, 0.1
        
        # 创建计划要评估的扫描点
        delta = (ahi - alo) / (Np - 1) # Np个采样点
        alist = np.arange(alo, ahi + delta/2, delta)# 加delta/2以确保ahi被包含在内
        
        # 移除扫描点附近的点 减少不必要的函数评估
        if len(xflist) > 0:
            known_alphas = xflist[:, 0]
            mask = np.ones(len(alist), dtype=bool)
            for i, alpha in enumerate(alist):
                if np.min(np.abs(alpha - known_alphas)) <= delta / 2.0:
                    mask[i] = False
            alist = alist[mask]
        
        # 评估新点
        flist = np.zeros(len(alist))
        for i, alpha in enumerate(alist):
            flist[i] = func(x0 + dv * alpha)

        
        # 合并已知点和新增点
        if len(xflist) > 0:
            all_alphas = np.concatenate([alist, xflist[:, 0]])
            all_flist = np.concatenate([flist, xflist[:, 1]])
        else:
            all_alphas = alist
            all_flist = flist
        
        # 排序
        sort_idx = np.argsort(all_alphas) #从小到大排序
        all_alphas = all_alphas[sort_idx]
        all_flist = all_flist[sort_idx]
        
        # 找到当前最佳点
        min_idx = np.argmin(all_flist)
        fm = all_flist[min_idx]
        am = all_alphas[min_idx]
        xm = x0 + dv * am
        
        # 如果点数不足，直接返回
        if len(all_alphas) <= 5:
            return xm, fm
        
        # 二次拟合（使用self.outlier1d进行异常值处理）
        try:
            # 第一次拟合所有点
            p = np.polyfit(all_alphas, all_flist, 2)
            cfl = np.polyval(p, all_alphas)
            residuals = all_flist - cfl
            
            # 使用类方法self.outlier1d检测异常值
            _, inlier_indices, outlier_indices = self._outlier1d(residuals)
            # inlier_indices = []
            # outlier_indices = []
            
            # 如果有异常值，重新拟合
            if len(outlier_indices) > 0:
                print(f"检测到 {len(outlier_indices)} 个异常值，重新拟合")
                clean_alphas = all_alphas[inlier_indices]
                clean_flist = all_flist[inlier_indices]
                
                # 用正常点重新拟合
                p = np.polyfit(clean_alphas, clean_flist, 2)
                av = np.linspace(np.min(clean_alphas), np.max(clean_alphas), 101)
                yv = np.polyval(p, av)
            else:
                # 没有异常值，直接使用原始点
                av = np.linspace(np.min(all_alphas), np.max(all_alphas), 101)
                yv = np.polyval(p, av)
            
            # 找到拟合曲线最小值
            min_idx_fit = np.argmin(yv)
            alpha_min = av[min_idx_fit]
            x1 = x0 + dv * alpha_min
            f1 = yv[min_idx_fit]

            # 可视化（如果需要）
            # if 1:
            #     plt.figure(figsize=(10, 6))
            #     plt.plot(all_alphas, all_flist, 'bo', label='origin point')
            #     plt.plot(av, yv, 'r-', label='fit curve')
            #     if len(outlier_indices) > 0:
            #         plt.plot(all_alphas[outlier_indices], all_flist[outlier_indices], 'rx', 
            #                 markersize=10, label='outlier value')
            #     plt.plot(alpha_min, f1, 'g*', markersize=15, label='predicted minimum value')
            #     plt.xlabel('Alpha')
            #     plt.ylabel('function')
            #     plt.title('Linear scanning and fitting')
            #     plt.legend()
            #     plt.grid(True)
            #     plt.show()

        except (np.linalg.LinAlgError, ValueError) as e:
            print(f"二次拟合失败({str(e)})，改用线性插值")
            # 拟合失败时使用线性插值
            av = np.linspace(np.min(all_alphas), np.max(all_alphas), 101)
            yv = np.interp(av, all_alphas, all_flist)
            min_idx_fit = np.argmin(yv)
            alpha_min = av[min_idx_fit]
            x1 = x0 + dv * alpha_min
            f1 = yv[min_idx_fit]
        
        return x1, f1

    def _outlier1d(self, x):
        """
        从一维数据中移除异常值
        返回:
            clean_x: 去除异常值后的数据
            inlier_indices: 正常点的索引
            outlier_indices: 异常点的索引
        """
        if len(x) < 3:
            return x, np.arange(len(x)), []
        
        # 排序
        sorted_indices = np.argsort(x)
        y = x[sorted_indices]
        
        # 计算差分
        dy = np.diff(y)
        
        # 确定中心区域
        perlim = 0.25
        dnl = max(int(len(x) * perlim), 2)  # 下界索引
        upl = max(int(len(x) * (1 - perlim)), 3)  # 上界索引
        
        # 中心区域的平均差分
        center_dy = dy[dnl-1:upl-1]
        if len(center_dy) > 0:
            mean_dy = np.mean(center_dy)
        else:
            mean_dy = np.mean(dy)
        
        # 检测上界异常
        upcut = len(x)
        for i in range(upl-1, len(dy)):
            if dy[i] > 3 * mean_dy:
                upcut = i + 1
                break
        
        # 检测下界异常
        dncut = 0
        for i in range(dnl-2, -1, -1):
            if dy[i] > 3 * mean_dy:
                dncut = i + 1
                break
        
        # 确定正常点范围
        inlier_sorted_indices = np.arange(dncut, upcut)
        
        # 转换为原始索引
        inlier_indices = sorted_indices[inlier_sorted_indices]
        outlier_indices = np.setdiff1d(np.arange(len(x)), inlier_indices)
        clean_x = x[inlier_indices]
        
        return clean_x, inlier_indices, outlier_indices

    def plot_optimization_process(self):
        g_data_array = np.array(self.data)
        print(g_data_array)
        params = g_data_array[:, :-1]
        values = g_data_array[:, 2]

        min_idx = np.argmin(values)
        fm = values[min_idx]
        xm = params[min_idx]

        print(f'best of history @ evolution {min_idx}: x = {xm}\nfunction value: {fm}')

        
        # 创建收敛曲线图
        plt.figure(figsize=(10, 6))
        min_values = np.minimum.accumulate(values)
        plt.plot(values, 'b-', linewidth=1.5, label='Current function value')
        plt.plot(min_values, 'r--', linewidth=1.5, label='Historical minimum value')
 
        plt.xlabel('Number of function evaluations')
        plt.ylabel('function value')
        plt.title('Convergence curve')
        plt.legend()
        plt.grid(True)
        
        # 添加收敛信息
        conv_rate = np.zeros(len(values)-1)
        min_val = min_values[-1]
        for i in range(1, len(values)):
            if values[i-1] - min_val > 1e-10:
                conv_rate[i-1] = (values[i] - min_val) / (values[i-1] - min_val)
        
        start_idx = max(0, len(values)//2)
        avg_conv_rate = np.mean(conv_rate[start_idx:])
        
        plt.figtext(0.6, 0.8, f'Final function value: {values[-1]:.2e}\nAverage convergence rate <df/dcnt>: {avg_conv_rate:.2f}', 
                    fontsize=12, bbox=dict(facecolor='white', alpha=0.8))

        plt.tight_layout()
        plt.show()

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
            # obj
            obj_pvnames = sys.argv[8].split(',')
            obj_weights = [float(x) for x in sys.argv[9].split(',')]
            obj_samples = [int(x) for x in sys.argv[10].split(',')]
            obj_samples = max(obj_samples)
            obj_math = sys.argv[11].split(',')
            

            # 创建优化器实例
            x0 = 0.5*np.ones(len(knobs_list))
            vrange = np.array([knobs_minus, knobs_plus]).T
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
                step=step, Dmat0=Dmat0, noise = noise, tol=1e-8, maxIt=maxIt, maxEval=15000, 
                obj_pvnames=obj_pvnames, obj_weights=obj_weights, obj_samples=obj_samples, obj_math=obj_math 
            )

            
            # 执行Powell优化
            x_norm_opt, f_opt = optimizer.powellmain()

            x_opt = optimizer.vrange[:, 0] + (optimizer.vrange[:, 1] - optimizer.vrange[:, 0]) * x_norm_opt
            print("Optimization result:", x_opt, f_opt)
            print(f'Number of function evolutions: {optimizer.cnt}')
            print(f'time: {time.time() - start_time:.2f} s')

            # 绘制优化过程
            optimizer.plot_optimization_process()

        
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