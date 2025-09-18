import numpy as np
import matplotlib.pyplot as plt
import time
from mpl_toolkits.mplot3d import Axes3D
# import math

class PowellOptimizer:
    def __init__(self):
        self.cnt = 0 # 用于记录目标函数评估次数
        self.data = [] # 用于记录所有评估目标函数的数据
        self.noise = 0
        self.vrange = None

    def record_data(self, x, obj_val):
        """记录优化过程中的数据"""
        # x已经是原始参数值，直接记录
        self.data.append(np.concatenate((x, [obj_val])))
        self.cnt += 1

    def powellmain(self, func, x0, step, Dmat0, tol=1e-6, maxIt=100, maxEval=15000):
        Nvar = len(x0)
        
        def wrapped_func(x_norm): #将归一化变量反归一化后进行目标函数评估
            # 反归一化
            x = self.vrange[:, 0] + (self.vrange[:, 1] - self.vrange[:, 0]) * x_norm
            obj_val = func(x)
            self.record_data(x, obj_val)
            return obj_val
            
        f0 = wrapped_func(x0)
        nf = 1
        
        # 当前最优解
        xm = x0.copy()
        fm = f0
        
        it = 0
        Dmat = Dmat0.copy()
        Npmin = 6
        
        while it < maxIt:
            it += 1
            step /= 1.2 # 每次迭代步长缩小
            
            k = 0
            delt = 0.0
            
            for ii in range(Nvar):
                dv = Dmat[:, ii]
                x_start = xm.copy()
                f_start = fm
                
                # 括号搜索
                x1, f1, a1, a2, xflist, ndf = self.bracketmin(wrapped_func, x_start, f_start, dv, step)
                nf += ndf
                print(f'iter {it}, dir {ii}: begin\t{self.cnt}, ', end='')
                
                # 线性扫描
                x1, f1, ndf = self.linescan(wrapped_func, x1, f1, dv, a1, a2, Npmin, xflist)
                print(f'end\t{self.cnt} : {f1}\n')
                nf += ndf
                
                # 更新最大改进方向
                if fm - f1 > delt:
                    delt = fm - f1
                    k = ii
                    print(f'iteration {it}, var {ii}: del = {delt} updated')
                
                fm = f1
                xm = x1.copy()
            
            # 生成共轭方向
            xt = 2*xm - x0
            ft = wrapped_func(xt)
            nf += 1
            
            # 方向替换条件
            if f0 <= ft or 2*(f0-2*fm+ft)*((f0-fm-delt)/(ft-f0))**2 >= delt:
                print(f'   , dir {k} not replaced: {f0<=ft}, {2*(f0-2*fm+ft)*((f0-fm-delt)/(ft-f0))**2 >= delt}')
            else:
                ndv = (xm - x0) / np.linalg.norm(xm - x0)
                dotp = np.zeros(Nvar)
                for jj in range(Nvar):
                    dotp[jj] = abs(np.dot(ndv, Dmat[:, jj]))
                
                if np.max(dotp) < 0.9:
                    print("here")
                    # 替换方向
                    if k < Nvar - 1:
                        Dmat[:, k:Nvar-1] = Dmat[:, k+1:Nvar]
                    Dmat[:, -1] = ndv
                    
                    # 在新方向搜索
                    dv = Dmat[:, -1]
                    x_start = xm.copy()
                    f_start = fm
                    x1, f1, a1, a2, xflist, ndf = self.bracketmin(wrapped_func, x_start, f_start, dv, step)
                    nf += ndf
                    
                    print(f'iter {it}, new dir {k}: begin\t{self.cnt}, ', end='')
                    x1, f1, ndf = self.linescan(wrapped_func, x1, f1, dv, a1, a2, Npmin, xflist)
                    print(f'end\t{self.cnt} : {f1}\n')
                    nf += ndf
                    fm = f1
                    xm = x1.copy()
                else:
                    print(f'    , skipped new direction {k}, max dot product {np.max(dotp)}')
            
            # 终止条件检查
            # if self.cnt > maxEval:
            #     print(f'terminated, reaching function evaluation limit: {self.cnt} > {maxEval}')
            #     break
            
            # if tol > 0 and 2.0*abs(f0-fm) < tol*(abs(f0)+abs(fm)):
            #     print(f'terminated: f0={f0:.2e}, fm={fm:.2e}, f0-fm={f0-fm:.2e}')
            #     break
            
            f0 = fm
            x0 = xm.copy()
        
        return xm, fm, nf    

    def bracketmin(self, func, x0, f0, dv, step):
        nf = 0
        if np.isnan(f0) or f0 is None:
            f0 = func(x0)
            nf += 1
        
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
        nf += 1
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
            nf += 1
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
            nf += 1
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
                nf += 1
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
        
        return xm, fm, a1, a2, xflist, nf

    def linescan(self, func, x0, f0, dv, alo, ahi, Np, xflist):
        nf = 0
        if np.isnan(f0) or f0 is None:
            f0 = func(x0)
            nf += 1
        
        # 确保有效区间
        if alo >= ahi:
            print(f"警告: alo({alo}) >= ahi({ahi})，使用默认值")
            alo, ahi = -0.1, 0.1
        
        # 创建扫描点
        delta = (ahi - alo) / (Np - 1)
        alist = np.arange(alo, ahi + delta/2, delta)
        
        # 移除已知点附近的点
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
            nf += 1
        
        # 合并已知点和新增点
        if len(xflist) > 0:
            all_alphas = np.concatenate([alist, xflist[:, 0]])
            all_flist = np.concatenate([flist, xflist[:, 1]])
        else:
            all_alphas = alist
            all_flist = flist
        
        # 排序
        sort_idx = np.argsort(all_alphas)
        all_alphas = all_alphas[sort_idx]
        all_flist = all_flist[sort_idx]
        
        # 找到当前最佳点
        min_idx = np.argmin(all_flist)
        fm = all_flist[min_idx]
        am = all_alphas[min_idx]
        xm = x0 + dv * am
        
        # 如果点数不足，直接返回
        if len(all_alphas) <= 5:
            return xm, fm, nf
        
        # 二次拟合（使用self.outlier1d进行异常值处理）
        try:
            # 第一次拟合所有点
            p = np.polyfit(all_alphas, all_flist, 2)
            cfl = np.polyval(p, all_alphas)
            residuals = all_flist - cfl
            
            # 使用类方法self.outlier1d检测异常值
            # _, inlier_indices, outlier_indices = self.outlier1d(residuals)
            inlier_indices = []
            outlier_indices = []
            
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
        
        return x1, f1, nf

    def outlier1d(self, x):
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

    def plot_optimization_process(self, vrange, func):
        g_data_array = np.array(self.data)
        print(g_data_array)
        params = g_data_array[:, :2]
        values = g_data_array[:, 2]
        
        # 创建3D曲面图
        fig = plt.figure(figsize=(15, 10))
        ax1 = fig.add_subplot(121, projection='3d')
        
        # 生成函数曲面
        x = np.linspace(vrange[0, 0], vrange[0, 1], 100)
        y = np.linspace(vrange[1, 0], vrange[1, 1], 100)
        X, Y = np.meshgrid(x, y)
        Z = np.zeros_like(X)
        for i in range(X.shape[0]):
            for j in range(X.shape[1]):
                Z[i,j] = func(np.array([X[i,j], Y[i,j]]))
        
        # 绘制曲面
        ax1.plot_surface(X, Y, Z, cmap='viridis', alpha=0.7, edgecolor='none')
        
        # 绘制优化路径
        ax1.plot(params[:, 0], params[:, 1], values, 'r.-', linewidth=1.5, markersize=8)
        
        # 标记关键点
        ax1.scatter(params[0, 0], params[0, 1], values[0], c='g', s=100, marker='o', label='start point')
        ax1.scatter(params[-1, 0], params[-1, 1], values[-1], c='r', s=100, marker='o', label='end point')
        ax1.scatter(1, 1, 0, c='k', s=150, marker='*', label='global minimum')
        
        ax1.set_xlabel('x')
        ax1.set_ylabel('y')
        ax1.set_zlabel('f(x,y)')
        ax1.set_title('Optimization Path')
        ax1.legend()
        
        # 创建等高线图
        ax2 = fig.add_subplot(122)
        
        # 绘制等高线
        contour = ax2.contourf(X, Y, np.log10(Z+1), 50, cmap='viridis')
        plt.colorbar(contour, ax=ax2)
        
        # 绘制优化路径
        ax2.plot(params[:, 0], params[:, 1], 'r.-', linewidth=1.5, markersize=8)
        ax2.scatter(params[0, 0], params[0, 1], c='g', s=100, marker='o')
        ax2.scatter(params[-1, 0], params[-1, 1], c='r', s=100, marker='o')
        ax2.scatter(1, 1, c='k', s=150, marker='*')
        
        ax2.set_xlabel('x')
        ax2.set_ylabel('y')
        ax2.set_title('Optimization Path(contour map)')
        
        # 创建收敛曲线图
        plt.figure(figsize=(10, 6))
        min_values = np.minimum.accumulate(values)
        plt.semilogy(values, 'b-', linewidth=1.5, label='Current function value')
        plt.semilogy(min_values, 'r--', linewidth=1.5, label='Historical minimum value')
        
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
        
        plt.figtext(0.6, 0.8, f'Final function value: {values[-1]:.2e}\nAverage convergence rate: {avg_conv_rate:.2f}', 
                    fontsize=12, bbox=dict(facecolor='white', alpha=0.8))
        
        plt.tight_layout()
        plt.show()




# 主程序
if __name__ == "__main__":
    start_time = time.time()
    
    def objfun(x):
        return (1 - x[0])**2 + 100 * (x[1] - x[0]**2)**2 #Rosenbrock函数 (最小值在(1,1)处, 值为0)
        # return -1*math.exp(-(x1*x1+x2*x2))+1

    # 创建优化器实例
    optimizer = PowellOptimizer()
    
    # 参数设置
    optimizer.vrange = np.array([[-2, 2], [-1, 3]])  # 参数范围
    Nvar = optimizer.vrange.shape[0]  # 目标函数的变量维度

    # x0 = np.random.rand(Nvar)# 生成随机初始点(归一化到[0,1]范围)
    x0 = [0.5, 0.5]
    start_point = optimizer.vrange[:, 0] + (optimizer.vrange[:, 1] - optimizer.vrange[:, 0]) * x0
    # start_point = [0.5, 0.2]
    dmat = np.eye(Nvar) # 初始方向集
    step = 0.1  # 初始步长
    
    # 执行Powell优化
    x1, f1, nf = optimizer.powellmain(objfun, x0, step, dmat, 1e-6, 10, 15000)
    
    # 反归一化最优解
    pm = optimizer.vrange[:, 0] + (optimizer.vrange[:, 1] - optimizer.vrange[:, 0]) * x1
    
    print("Initial postion:", start_point)
    print('\nOptimization result:')
    print(f'Optimal point (fit): x = {pm[0]:.6f}, y = {pm[1]:.6f}')
    print(f'Optimal function value (fit): {f1:.6e}')
    print(f'Number of function evolutions: {optimizer.cnt}')
    # print(f'time: {time.time() - start_time:.2f} s')
    
    # 绘制优化过程
    optimizer.plot_optimization_process(optimizer.vrange, objfun)