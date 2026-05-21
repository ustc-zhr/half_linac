import numpy as np
import os
import matplotlib.pyplot as plt
import time
from datetime import datetime


class RCDSOptimizer:
    def __init__(self, func, vrange,
                 x0, Dmat0, step, noise = 0,
                 tol=1e-6, maxIt=100, maxEval=15000):
        """
        参数:
        func: 目标函数
        x0: 初始点 (numpy数组)
        vrange: 初始信赖域半径
        step: 初始扫描步长
        Dmat0: 初始方向集合
        tol: 收敛容差
        maxIt: 最大迭代次数
        maxEval: 最大函数演化次数
        """
        # 输入参数
        self.func = func
        self.vrange = vrange

        self.x0 = x0
        self.Dmat0 = Dmat0
        self.step = step
        self.noise = noise # 目标函数值的噪声
        
        self.tol = tol
        self.maxIt = maxIt
        self.maxEval = maxEval

        # 
        self.cnt = 0# 用于记录目标函数评估次数
        self.history = []# 用于记录所有评估目标函数的数据
        

    def _record_data(self, x, obj_val):
        """记录优化过程中的数据"""
        self.history.append(np.concatenate((x, [obj_val])))
        self.cnt += 1

    def optimize(self):
        """Direction Set (Powell's) Methods"""
        Nvar = len(self.x0)
        
        def _wrapped_func(x_norm):#将归一化变量反归一化后进行目标函数评估
            x = self.vrange[:, 0] + (self.vrange[:, 1] - self.vrange[:, 0]) * x_norm
            obj_val = self.func(x)
            # obj_val *=(1+0.1*np.random.normal(0, 1)) # 添加随机量以作噪声测试
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
            t0 = time.time()
            print('iter:', it, 'f(x)=:', fm)
            it += 1
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
                pass
            else:
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
            if self.cnt > self.maxEval:
                print(f'terminated, reaching function evaluation limit: {self.cnt} > {self.maxEval}')
                break
            
            if self.tol > 0 and 2.0*abs(f0-fm) < self.tol*(abs(f0)+abs(fm)):
                print(f'terminated: f0={f0:.2e}, fm={fm:.2e}, f0-fm={f0-fm:.2e}')
                break
            
            #更新初始点 以便下次迭代
            f0 = fm
            self.x0 = xm.copy()
        
        # 打印进度
            t1 = time.time()
            print(f"iter {it+1:02d}: f(x)={fm:.4f}, time: {t1-t0:.2f}s")
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
            if 0:
                plt.figure(figsize=(10, 6))
                plt.plot(all_alphas, all_flist, 'bo', label='origin point')
                plt.plot(av, yv, 'r-', label='fit curve')
                if len(outlier_indices) > 0:
                    plt.plot(all_alphas[outlier_indices], all_flist[outlier_indices], 'rx', 
                            markersize=10, label='outlier value')
                plt.plot(alpha_min, f1, 'g*', markersize=15, label='predicted minimum value')
                plt.xlabel('Alpha')
                plt.ylabel('function')
                plt.title('Linear scanning and fitting')
                plt.legend()
                plt.grid(True)
                plt.show()

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


    def plot_convergence(self, testfunc=False, path: str = None):
        g_data_array = np.array(self.history)
        history_X = g_data_array[:, :-1]
        history_Y = g_data_array[:, -1]

        # 创建收敛曲线图
        plt.figure(figsize=(10, 6))
        min_values = np.minimum.accumulate(history_Y)
        plt.plot(history_Y, 'o-', linewidth=1.5, label='Current function value')
        plt.plot(min_values, 'r--', linewidth=1.5, label='Historical minimum value')
        
        plt.xlabel('evaluations')
        plt.ylabel('Values')
        plt.title('Convergence curve')
        plt.legend()
        plt.grid(True)


        if history_X.shape[1] == 2 and testfunc:      
            # 生成函数曲面
            x = np.linspace(self.vrange[0, 0], self.vrange[0, 1], 100)
            y = np.linspace(self.vrange[1, 0], self.vrange[1, 1], 100)
            X, Y = np.meshgrid(x, y)
            Z = np.zeros_like(X)
            for i in range(X.shape[0]):
                for j in range(X.shape[1]):
                    Z[i,j] = self.func(np.array([X[i,j], Y[i,j]]))
            
            # 创建等高线图
            fig, ax2 = plt.subplots(figsize=(10, 6))
            
            # 绘制等高线
            contour = ax2.contourf(X, Y, np.log10(Z+1), 50, cmap='viridis')
            cbar = plt.colorbar(contour, ax=ax2)
            cbar.set_label('Function Value: log10(Z+1)') 
            # 绘制优化路径
            ax2.plot(history_X[:, 0], history_X[:, 1], 'r.-', linewidth=1.5, markersize=8)
            # 标记关键点
            ax2.scatter(history_X[0, 0], history_X[0, 1],  c='g', s=100, marker='o', label='start point')
            ax2.scatter(history_X[-1, 0], history_X[-1, 1],  c='r', s=100, marker='o', label='end point')
            ax2.legend()

            ax2.set_xlabel('x')
            ax2.set_ylabel('y')
            ax2.set_title('Optimization Path(contour map)')

        # 保存图片（如果指定了路径）
        if path is None:
            timestamp = datetime.now().strftime("%Y%m%d%H%M")
            path = f"save/rcds_{timestamp}.png"
        os.makedirs(os.path.dirname(path), exist_ok=True)  # 确保目录存在 自动创建目录
        plt.savefig(path)

        plt.tight_layout()
        plt.show()   

    def save_history(self, path: str = None):
        if path == None:
            timestamp = datetime.now().strftime("%Y%m%d%H%M")  # 格式：20231012T1545
            path = f"save/rcds{timestamp}.dat"
        os.makedirs(os.path.dirname(path), exist_ok=True)  # 确保目录存在 自动创建目录
        # 确保数据为二维数组
        # g_data_array = np.array(self.history)
        # history_X = g_data_array[:, :2]
        # history_Y = g_data_array[:, 2]
        
        # # 合并数据并保存
        # data = np.hstack([history_X, history_Y])
        np.savetxt(path, self.history, fmt='%.6f')

if __name__ == "__main__":
    from test_function import *

    t0 = time.time()

    # fuction for test
    dim=2
    func_type = "rosenbrock_noisy" # ["sphere", "rosenbrock", "rosenbrock_noisy", "ackley"]
    objfun, vrange = setup_objective(func_type, dim=dim)

    # x0_start = np.random.rand(dim)# 生成随机初始点(归一化到[0,1]范围)
    x0_start = [0.5, 0.7]
    # vrange = np.array([[-2, 2], [-2, 2]])
    print(vrange)

    # sys.exit()
    # 创建优化器实例
    optimizer = RCDSOptimizer(
        func=objfun,
        x0=x0_start,
        vrange=vrange,
        step=0.2,
        Dmat0=np.eye(len(x0_start)),
        noise=0.1,
        maxIt=10
    )
    

    # 执行Powell优化
    x_norm_opt, f_opt = optimizer.optimize()
    # optimizer.optimize()

    x_opt = optimizer.vrange[:, 0] + (optimizer.vrange[:, 1] - optimizer.vrange[:, 0]) * x_norm_opt
    print("Optimization result:", x_opt, f_opt)
    print(f'time: {time.time() - t0:.2f} s')

    # 绘制优化过程
    optimizer.save_history()
    optimizer.plot_convergence(testfunc=True)