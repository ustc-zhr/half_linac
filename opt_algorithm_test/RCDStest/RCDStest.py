import numpy as np
from scipy.linalg import svd
import matplotlib.pyplot as plt

class RCDSOptimizer:
    def __init__(self, func, x0, delta0=1.0, tol=1e-6, max_iter=5,
                 ortho_thresh=0.9, cond_thresh=1e6, stagnation_thresh=3,
                 noise_level=None, bounds=None):
        """
        RCDS算法实现 (Robust Conjugate Direction Search)
        
        参数:
        func: 目标函数 (接受numpy数组作为输入)
        x0: 初始点 (numpy数组)
        delta0: 初始信赖域半径
        tol: 收敛容差
        max_iter: 最大迭代次数
        ortho_thresh: 正交化阈值(0-1)
        cond_thresh: 方向集条件数重置阈值
        stagnation_thresh: 停滞迭代次数阈值
        noise_level: 函数噪声水平估计
        bounds: 变量范围 [(min1,max1), (min2,max2), ...] 或None
        """
        self.func = func
        self.x = np.array(x0, dtype=float)
        self.dim = len(x0)
        self.delta = delta0
        self.tol = tol
        self.max_iter = max_iter
        self.ortho_thresh = ortho_thresh
        self.cond_thresh = cond_thresh
        self.stagnation_thresh = stagnation_thresh
        self.noise_level = noise_level
        self.bounds = bounds
        
        # 初始化方向集(单位矩阵)
        self.directions = np.eye(self.dim)
        self.f_history = [func(self.x)]
        self.x_history = [self.x.copy()]

    def solve(self):
        """执行优化过程"""
        stagnation_count = 0
        
        for iter in range(self.max_iter):
            x_start = self.x.copy()
            f_start = self.f_history[-1]
            
            # 1. 沿当前方向集搜索
            for i in range(self.dim):
                self._search_along_direction(i)
            
            # 2. 计算总位移向量
            delta_x = self.x - x_start
            # print(delta_x)
            delta_x_norm = np.linalg.norm(delta_x)
            # print(delta_x_norm)
            
            # 3. 方向集正交化处理
            d_new = self._orthogonalize_direction(delta_x)
            # print(d_new)
            # 4. 沿新方向搜索
            self._search_along_custom_direction(d_new)
            
            # 5. 更新信赖域半径
            self._update_trust_region(f_start)
            
            # 6. 检查收敛
            if self._check_convergence(f_start, delta_x_norm):
                break
                
            # 7. 智能重置方向集
            stagnation_count = self._smart_reset(f_start, stagnation_count)
        
        return self.x, self.f_history[-1]

    def _search_along_direction(self, dir_idx):
        """沿指定方向进行一维搜索"""
        d = self.directions[dir_idx]
        alpha_min, f_min = self._line_search(self.x, d)
        self.x = self._check_bounds(self.x + alpha_min * d)
        self.f_history.append(f_min)
        self.x_history.append(self.x.copy())

    def _search_along_custom_direction(self, direction):
        """沿自定义方向进行一维搜索"""
        norm = np.linalg.norm(direction)
        if norm < 1e-10:  # 如果方向向量太小
            direction = np.random.randn(self.dim)  # 使用随机方向
            direction /= np.linalg.norm(direction)
        else:
            direction /= norm  # 归一化
            
        alpha_min, f_min = self._line_search(self.x, direction)
        self.x += alpha_min * direction
        self.f_history.append(f_min)
        self.x_history.append(self.x.copy())
        
        # 更新方向集(替换最相关方向)
        self._update_direction_set(direction)

    def _line_search(self, x, d):
        """自适应一维搜索实现"""
        # 黄金分割法参数
        phi = (np.sqrt(5) - 1) / 2  # ≈0.618
        
        # 计算初始搜索区间
        if self.bounds is None:
            a, b = -self.delta, self.delta
        else:
            # 计算沿方向d的可行边界
            a, b = -self.delta, self.delta
            for i in range(self.dim):
                if d[i] > 1e-10:  # 正方向
                    upper = (self.bounds[i][1] - x[i]) / d[i]
                    b = min(b, upper)
                elif d[i] < -1e-10:  # 负方向
                    lower = (self.bounds[i][0] - x[i]) / d[i]
                    a = max(a, lower)
        
        # 初始三点
        alpha1 = a + (1 - phi) * (b - a)
        alpha2 = a + phi * (b - a)
        f1 = self.func(x + alpha1 * d)
        f2 = self.func(x + alpha2 * d)
        
        # 噪声自适应采样
        if self.noise_level:
            return self._noise_adaptive_search(x, d, a, b)
        
        # 标准黄金分割搜索
        for _ in range(50):  # 最多50次迭代
            if f1 < f2:
                b = alpha2
                alpha2, f2 = alpha1, f1
                alpha1 = a + (1 - phi) * (b - a)
                f1 = self.func(x + alpha1 * d)
            else:
                a = alpha1
                alpha1, f1 = alpha2, f2
                alpha2 = a + phi * (b - a)
                f2 = self.func(x + alpha2 * d)
            
            if abs(b - a) < self.tol:
                break
        
        alpha_min = (a + b) / 2
        return alpha_min, self.func(x + alpha_min * d)

    def _noise_adaptive_search(self, x, d, a, b):
        """噪声自适应一维搜索"""
        # 在搜索区间采样多个点
        n_samples = 10
        alphas = np.linspace(a, b, n_samples)
        values = np.array([self.func(x + alpha * d) for alpha in alphas])
        
        # 检测低置信区间(高噪声区域)
        noise_mask = self._detect_noisy_regions(values)
        
        # 选择最低点所在子区间
        min_idx = np.argmin(values)
        min_alpha = alphas[min_idx]
        
        # 收缩搜索区间到低噪声区域
        left = max(a, min_alpha - 0.3 * (b - a))
        right = min(b, min_alpha + 0.3 * (b - a))
        
        # 在子区间二次拟合
        sub_alphas = np.linspace(left, right, 5)
        sub_values = np.array([self.func(x + alpha * d) for alpha in sub_alphas])
        
        # 二次多项式拟合
        coeffs = np.polyfit(sub_alphas, sub_values, 2)
        
        # 计算二次函数最小值
        alpha_min = -coeffs[1] / (2 * coeffs[0]) if coeffs[0] > 0 else min_alpha
        
        # 确保在合理范围内
        alpha_min = np.clip(alpha_min, left, right)
        
        return alpha_min, self.func(x + alpha_min * d)

    def _detect_noisy_regions(self, values):
        """检测高噪声区域(简化实现)"""
        if not self.noise_level:
            return np.zeros_like(values, dtype=bool)
        
        # 计算滑动窗口标准差
        window_size = 3
        noise_mask = np.zeros(len(values), dtype=bool)
        
        for i in range(1, len(values)-1):
            window = values[i-1:i+2]
            std = np.std(window)
            if std < self.noise_level * 1.5:
                noise_mask[i] = True
                
        return noise_mask

    def _orthogonalize_direction(self, direction):
        """Gram-Schmidt正交化处理"""
        d_new = direction.copy().astype(float)
        
        # 逐步减去在各方向上的投影
        for i in range(self.dim):
            d = self.directions[i]
            projection = np.dot(d_new, d) / np.dot(d, d)
            d_new -= projection * d
            
        # 归一化新方向
        norm = np.linalg.norm(d_new)
        if norm > 1e-10:
            d_new /= norm
        
        return d_new

    def _update_direction_set(self, new_direction):
        """更新方向集(替换最相关方向)"""
        # 计算新方向与现有方向的相关性
        cosines = []
        for i in range(self.dim):
            cos_val = np.abs(np.dot(new_direction, self.directions[i]))
            cos_val /= np.linalg.norm(self.directions[i])
            cosines.append(cos_val)
        
        # 找到最相关的方向
        most_correlated_idx = np.argmax(cosines)
        
        # 仅当相关性超过阈值时替换
        if cosines[most_correlated_idx] > self.ortho_thresh:
            self.directions[most_correlated_idx] = new_direction

    def _update_trust_region(self, f_prev):
        """动态调整信赖域半径"""
        if len(self.f_history) < 3:
            return
        
        # 计算实际下降量
        actual_decrease = f_prev - self.f_history[-1]
        
        # 计算预期下降量(简化估计)
        expected_decrease = 0.5 * (f_prev - self.f_history[-2]) if len(self.f_history) > 2 else actual_decrease
        
        if expected_decrease > 0:
            ratio = actual_decrease / expected_decrease
            
            # 根据性能调整信赖域半径
            if ratio > 0.8:
                self.delta *= 2.0  # 扩大搜索范围
            elif ratio < 0.2:
                self.delta *= 0.5  # 缩小搜索范围
            
            # 限制最小和最大半径
            self.delta = max(min(self.delta, 10.0), 1e-5)

    def _check_bounds(self, x):
        """确保变量在边界范围内"""
        if self.bounds is None:
            return x
        return np.clip(x, [b[0] for b in self.bounds], [b[1] for b in self.bounds])

    def _check_convergence(self, f_prev, dx_norm):
        """检查收敛条件"""
        f_current = self.f_history[-1]
        f_change = abs(f_prev - f_current)
        x_change = dx_norm
        
        # 函数值变化或参数变化小于容差
        if f_change < self.tol and x_change < self.tol:
            return True
        return False

    def _smart_reset(self, f_prev, stagnation_count):
        """智能方向集重置策略"""
        f_current = self.f_history[-1]
        
        # 1. 检查停滞
        if abs(f_prev - f_current) < self.tol:
            stagnation_count += 1
        else:
            stagnation_count = 0
        
        # 2. 检查方向集条件数
        _, s, _ = svd(self.directions)
        cond_number = s[0] / s[-1] if s[-1] > 0 else np.inf
        
        # 3. 触发重置条件
        if stagnation_count >= self.stagnation_thresh or cond_number > self.cond_thresh:
            self.directions = np.eye(self.dim)  # 重置为单位矩阵
            stagnation_count = 0
            # 适度收缩信赖域
            self.delta = max(self.delta * 0.7, 1e-5)
        
        return stagnation_count

    def get_optimization_history(self):
        """返回优化历史"""
        return {
            'x': np.array(self.x_history),
            'f': np.array(self.f_history),
            'directions': np.array(self.directions.copy())
        }
    

if __name__=='__main__':
    # 定义目标函数(Rosenbrock函数)
    def rosenbrock(x):
        return (1 - x[0])**2 + 100 * (x[1] - x[0]**2)**2

    # 初始化优化器
    optimizer = RCDSOptimizer(
        func=rosenbrock,
        x0=np.array([-1.5, 1.5]),
        delta0=1.0,
        tol=1e-4,
        max_iter=5,  # 增加迭代次数
        noise_level=None,  # 无噪声环境
        bounds=[(-2, 2), (-1, 3)]  # 变量边界约束
    )

    # 执行优化
    x_opt, f_opt = optimizer.solve()
    print(f"Optimal point: {x_opt}, Optimal value: {f_opt}")

    # 获取优化历史
    history = optimizer.get_optimization_history()    

    # 绘制优化轨迹和收敛曲线
    plt.figure(figsize=(12, 5))
    
    # 子图1: 函数值变化
    plt.subplot(1, 2, 1)
    plt.plot(history['f'], 'b-', linewidth=2)
    plt.xlabel('Iteration')
    plt.ylabel('Function Value')
    plt.title('Convergence History')
    plt.yscale('log')
    plt.grid(True)
    
    # 子图2: 参数轨迹
    plt.subplot(1, 2, 2)
    x = np.linspace(-2, 2, 100)
    y = np.linspace(-1, 3, 100)
    X, Y = np.meshgrid(x, y)
    Z = rosenbrock([X, Y])
    plt.contour(X, Y, Z, levels=50, cmap='viridis')
    plt.plot(history['x'][:,0], history['x'][:,1], 'r.-')
    plt.plot(1, 1, 'g*', markersize=10)  # 标记最小值点
    plt.title('Optimization Path')
    plt.xlabel('x0')
    plt.ylabel('x1')
    plt.colorbar()
    
    plt.tight_layout()
    plt.show()

    # 打印重置信息
    print("\nOptimization Statistics:")
    print(f"Total iterations: {len(history['f'])}")
    print(f"Final function value: {f_opt:.6f}")
    print(f"Distance to minimum: {np.linalg.norm(x_opt - np.array([1,1])):.6f}")