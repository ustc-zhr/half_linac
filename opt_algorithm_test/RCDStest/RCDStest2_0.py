import numpy as np
import matplotlib.pyplot as plt
import time
from mpl_toolkits.mplot3d import Axes3D  # 用于3D绘图，虽然不直接使用但必需

# 全局变量用于记录优化过程
g_cnt = 0
g_data = []
g_noise = 0
vrange = None  # 添加全局变量声明

def rosenbrock(x):
    global g_cnt, g_data, g_noise, vrange
    
    # 反归一化获取实际参数值
    p = vrange[:, 0] + (vrange[:, 1] - vrange[:, 0]) * x
    
    # Rosenbrock函数 (最小值在(1,1)处，值为0)
    a = 1
    b = 100
    x1, x2 = p[0], p[1]
    obj = (a - x1)**2 + b * (x2 - x1**2)**2
    
    # 记录数据
    g_cnt += 1
    g_data.append(np.concatenate((p, [obj])))
    
    # 显示进度
    if g_cnt % 10 == 0:
        print(f'评估 {g_cnt}: x={x1:.4f}, y={x2:.4f}, f={obj:.4f}')
    
    return obj

def bracketmin(func, x0, f0, dv, step, flag_plot='noplot'):
    global g_noise
    
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
    while f1 < fm + g_noise * 3:
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
    if f0 > fm + g_noise * 3:
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
        while f2 < fm + g_noise * 3:
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

def linescan(func, x0, f0, dv, alo, ahi, Np, xflist, flag_plot='noplot'):
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
    
    # 二次拟合
    try:
        p = np.polyfit(all_alphas, all_flist, 2)
        # 生成密集点用于拟合曲线
        av = np.linspace(np.min(all_alphas), np.max(all_alphas), 101)
        yv = np.polyval(p, av)
        
        # 找到拟合曲线最小值
        min_idx_fit = np.argmin(yv)
        alpha_min = av[min_idx_fit]
        x1 = x0 + dv * alpha_min
        f1 = yv[min_idx_fit]
    except:
        # 拟合失败时使用插值
        av = np.linspace(np.min(all_alphas), np.max(all_alphas), 101)
        yv = np.interp(av, all_alphas, all_flist)
        min_idx_fit = np.argmin(yv)
        alpha_min = av[min_idx_fit]
        x1 = x0 + dv * alpha_min
        f1 = yv[min_idx_fit]
    
    return x1, f1, nf

def outlier1d(x):
    if len(x) < 3:
        return x, np.arange(len(x)), []
    
    # 排序
    idx_sorted = np.argsort(x)
    y = x[idx_sorted]
    
    # 计算差分
    dy = np.diff(y)
    
    # 确定中心区域
    perlim = 0.25
    dnl = max(int(len(x) * perlim), 2)
    upl = max(int(len(x) * (1 - perlim)), 3)
    
    # 中心区域的平均差分
    stddy = np.mean(dy[dnl-1:upl-1])
    
    # 检测上界异常
    upcut = len(x)
    for i in range(upl, len(x)-1):
        if dy[i] > 3 * stddy:
            upcut = i + 1
            break
    
    # 检测下界异常
    dncut = 0
    for i in range(dnl-2, -1, -1):
        if dy[i] > 3 * stddy:
            dncut = i + 1
            break
    
    # 移除异常点
    keep_idx = np.concatenate([np.arange(dncut), np.arange(upcut, len(x))])
    if len(keep_idx) == 0:
        return x, np.arange(len(x)), []
    
    # 恢复原始索引
    inliers = idx_sorted[keep_idx]
    outliers = np.setdiff1d(np.arange(len(x)), inliers)
    
    return x[inliers], inliers, outliers

def powellmain(func, x0, step, Dmat0, tol=1e-6, maxIt=100, flag_plot='noplot', maxEval=1500):
    global g_cnt
    
    Nvar = len(x0)
    f0 = func(x0)
    nf = 1
    
    # 当前最优解
    xm = x0.copy()
    fm = f0
    
    it = 0
    Dmat = Dmat0.copy()
    Npmin = 6
    
    while it < maxIt:
        it += 1
        step /= 1.2
        
        k = 0
        delt = 0.0
        
        for ii in range(Nvar):
            dv = Dmat[:, ii]
            x_start = xm.copy()
            f_start = fm
            
            # 括号搜索
            x1, f1, a1, a2, xflist, ndf = bracketmin(func, x_start, f_start, dv, step, flag_plot)
            nf += ndf
            
            print(f'iter {it}, dir {ii}: begin\t{g_cnt}, ', end='')
            
            # 线性扫描
            x1, f1, ndf = linescan(func, x1, f1, dv, a1, a2, Npmin, xflist, flag_plot)
            nf += ndf
            print(f'end\t{g_cnt} : {f1}\n')
            
            # 更新最大改进方向
            if fm - f1 > delt:
                delt = fm - f1
                k = ii
                print(f'iteration {it}, var {ii}: del = {delt} updated')
            
            fm = f1
            xm = x1.copy()
        
        # 生成共轭方向
        xt = 2*xm - x0
        ft = func(xt)
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
                # 替换方向
                if k < Nvar - 1:
                    Dmat[:, k:Nvar-1] = Dmat[:, k+1:Nvar]
                Dmat[:, -1] = ndv
                
                # 在新方向搜索
                dv = Dmat[:, -1]
                x_start = xm.copy()
                f_start = fm
                x1, f1, a1, a2, xflist, ndf = bracketmin(func, x_start, f_start, dv, step, flag_plot)
                nf += ndf
                
                print(f'iter {it}, new dir {k}: begin\t{g_cnt}, ', end='')
                x1, f1, ndf = linescan(func, x1, f1, dv, a1, a2, Npmin, xflist, flag_plot)
                print(f'end\t{g_cnt} : {f1}\n')
                nf += ndf
                fm = f1
                xm = x1.copy()
            else:
                print(f'    , skipped new direction {k}, max dot product {np.max(dotp)}')
        
        # 终止条件检查
        if g_cnt > maxEval:
            print(f'terminated, reaching function evaluation limit: {g_cnt} > {maxEval}')
            break
        
        if tol > 0 and 2.0*abs(f0-fm) < tol*(abs(f0)+abs(fm)):
            print(f'terminated: f0={f0:.2e}, fm={fm:.2e}, f0-fm={f0-fm:.2e}')
            break
        
        f0 = fm
        x0 = xm.copy()
    
    return xm, fm, nf

def process_scandata(g_data, Nvar, vrange, flag_plot='noplot', isel=1):
    g_data_array = np.array(g_data)
    Ncnt = g_data_array.shape[0]
    
    # 找到最佳解
    obj_col = Nvar
    sorted_idx = np.argsort(g_data_array[:, obj_col])
    fm = g_data_array[sorted_idx[isel], obj_col]
    xm_actual = g_data_array[sorted_idx[isel], :Nvar]
    
    # 归一化
    xm = (xm_actual - vrange[:, 0]) / (vrange[:, 1] - vrange[:, 0])
    
    # 计算所有点到最佳点的距离
    distances = np.zeros(Ncnt)
    x_data = np.zeros((Ncnt, Nvar + 2))
    
    for ii in range(Ncnt):
        p_actual = g_data_array[ii, :Nvar]
        x_norm = (p_actual - vrange[:, 0]) / (vrange[:, 1] - vrange[:, 0])
        dist = np.linalg.norm(x_norm - xm)
        distances[ii] = dist
        x_data[ii] = np.concatenate([x_norm, [g_data_array[ii, obj_col], dist]])
    
    # 历史最佳解
    h_data = np.zeros_like(g_data_array)
    h_data[0] = g_data_array[0]
    
    for ii in range(1, Ncnt):
        h_data[ii] = h_data[ii-1]
        if g_data_array[ii, obj_col] < h_data[ii, obj_col]:
            h_data[ii] = g_data_array[ii]
    
    return g_data_array, xm, fm

def plot_optimization_process(g_data, vrange):
    g_data_array = np.array(g_data)
    params = g_data_array[:, :2]
    values = g_data_array[:, 2]
    
    # 创建3D曲面图
    fig = plt.figure(figsize=(15, 10))
    ax1 = fig.add_subplot(121, projection='3d')
    
    # 生成Rosenbrock函数曲面
    x = np.linspace(vrange[0, 0], vrange[0, 1], 100)
    y = np.linspace(vrange[1, 0], vrange[1, 1], 100)
    X, Y = np.meshgrid(x, y)
    Z = (1 - X)**2 + 100 * (Y - X**2)**2
    
    # 绘制曲面
    ax1.plot_surface(X, Y, Z, cmap='viridis', alpha=0.7, edgecolor='none')
    
    # 绘制优化路径
    ax1.plot(params[:, 0], params[:, 1], values, 'r.-', linewidth=1.5, markersize=8)
    
    # 标记关键点
    ax1.scatter(params[0, 0], params[0, 1], values[0], c='g', s=100, marker='o', label='起点')
    ax1.scatter(params[-1, 0], params[-1, 1], values[-1], c='r', s=100, marker='o', label='终点')
    ax1.scatter(1, 1, 0, c='k', s=150, marker='*', label='全局最小值')
    
    ax1.set_xlabel('x')
    ax1.set_ylabel('y')
    ax1.set_zlabel('f(x,y)')
    ax1.set_title('Rosenbrock函数优化路径')
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
    ax2.set_title('优化路径 (等高线图)')
    
    # 创建收敛曲线图
    plt.figure(figsize=(10, 6))
    min_values = np.minimum.accumulate(values)
    plt.semilogy(values, 'b-', linewidth=1.5, label='当前函数值')
    plt.semilogy(min_values, 'r--', linewidth=1.5, label='历史最小值')
    
    plt.xlabel('函数评估次数')
    plt.ylabel('函数值 (对数刻度)')
    plt.title('函数值收敛曲线')
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
    
    plt.figtext(0.6, 0.8, f'最终函数值: {values[-1]:.2e}\n平均收敛速率: {avg_conv_rate:.2f}', 
                fontsize=12, bbox=dict(facecolor='white', alpha=0.8))
    
    plt.tight_layout()
    plt.show()

# 主程序
if __name__ == "__main__":
    start_time = time.time()
    
    # 重置全局变量
    g_cnt = 0
    g_data = []
    g_noise = 0
    
    # 参数设置
    Nvar = 2  # Rosenbrock函数是二维的
    vrange = np.array([[-2, 2], [-1, 3]])  # 参数范围
    
    # 随机初始点
    p0 = vrange[:, 0] + np.random.rand(Nvar) * (vrange[:, 1] - vrange[:, 0])
    x0 = (p0 - vrange[:, 0]) / (vrange[:, 1] - vrange[:, 0])
    
    # 初始方向集
    dmat = np.eye(Nvar)
    step = 0.2  # 初始步长
    
    print("初始点:", x0)
    
    # 执行Powell优化
    x1, f1, nf = powellmain(rosenbrock, x0, step, dmat, 1e-6, 500, 'noplot', 500)
    
    # 处理扫描数据
    g_data_array, xm, fm = process_scandata(g_data, Nvar, vrange, 'noplot')
    
    # 反归一化最优解
    pm = vrange[:, 0] + (vrange[:, 1] - vrange[:, 0]) * x1
    
    print('\n优化结果:')
    print(f'最优解: x = {pm[0]:.6f}, y = {pm[1]:.6f}')
    print(f'函数值: {f1:.6e}')
    print(f'函数调用次数: {g_cnt}')
    print(f'计算时间: {time.time() - start_time:.2f} 秒')
    
    # 绘制优化过程
    plot_optimization_process(g_data, vrange)