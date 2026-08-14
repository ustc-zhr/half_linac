# 双弯段消色散一键调束第一版技术路线

## 1. 场景与目标

本技术路线面向一个典型弯段消色散场景：

```text
双弯铁 + 两对对称分布四极铁
```

例如：

```text
入口直线段
   |
 Q1L   B1   Q2L   ... 对称中心 ...   Q2R   B2   Q1R
   |
出口直线段
```

第一版本不追求完整恢复理论设计光学，也不先区分真色散和 beta 轨道伪色散，而是直接最小化能量扰动下的总有效色散：

\[
D_\mathrm{eff} = \frac{\partial x}{\partial \delta}
\]

其中：

\[
\delta = \frac{\Delta p}{p_0}
\]

本软件包面向的 IRFEL 电子束能量至少为几十 MeV。第一版工程实现中默认：

\[
\frac{\Delta p}{p} \simeq \frac{\Delta E}{E}
\]

因此配置文件中不再要求填写束流 \(\beta\) 或区分 `delta_p_over_p` /
`delta_e_over_e`；`energy_knob.delta` 直接作为算法使用的无量纲能量扰动。
如果实际执行器是 RF 相位 PV，则必须通过现场标定得到
`phase_per_delta`，再由软件换算所需的相位扰动。

真实机器上测到的有效色散可以理解为：

\[
D_\mathrm{eff}=D_\mathrm{true}+D_\beta
\]

第一版的工程目标是：

\[
\boxed{
\text{在当前工作点附近，降低弯段出口后 BPM 对能量扰动的轨道响应}
}
\]

即：

\[
\frac{\partial x_i}{\partial \delta} \rightarrow 0
\]

必要时也包括垂直方向：

\[
\frac{\partial y_i}{\partial \delta} \rightarrow 0
\]

---

## 2. 第一版设计原则

第一版应尽量简单、稳健、可回退。

### 第一版先做

```text
1. 直接测量 D_eff
2. 使用少量对称四极铁 knob
3. 建立实测响应矩阵
4. 使用 SVD / 伪逆求校正量
5. 小步应用
6. 变好则接受，变坏则回退
7. 加入基本机器保护
```

### 第一版暂不做

```text
1. 不分离真色散和 beta 轨道伪色散
2. 不拟合入口 x, x'
3. 不做完整 Twiss 在线匹配
4. 不做高阶色散校正
5. 不做贝叶斯优化或复杂黑盒优化
6. 不放开所有四极铁
7. 不自动调主弯铁
8. 不自动调 RF 相位和加速梯度
9. 不做复杂多目标权重优化
```

---

## 3. 优化变量选择

对于双弯铁 + 两对对称四极铁结构，第一版建议只使用两个对称 knob。

### Knob 1

\[
u_1: \Delta Q1_L = \Delta Q1_R
\]

### Knob 2

\[
u_2: \Delta Q2_L = \Delta Q2_R
\]

也就是保持左右对称，不放开四个独立四极铁。

对应实际四极铁变化为：

\[
\Delta \mathbf{k}
=
\begin{pmatrix}
\Delta Q1_L \\
\Delta Q2_L \\
\Delta Q2_R \\
\Delta Q1_R
\end{pmatrix}
=
\begin{pmatrix}
u_1 \\
u_2 \\
u_2 \\
u_1
\end{pmatrix}
\]

---

## 4. BPM 选择

第一版不要选太多 BPM，建议选择：

```text
弯段出口后 3~8 个 BPM
```

这些 BPM 用于评价能量变化后下游轨道是否稳定。

如果第一版只做水平消色散，则目标向量为：

\[
\mathbf{D}_\mathrm{eff}
=
\begin{pmatrix}
D_{x,1} \\
D_{x,2} \\
\vdots \\
D_{x,N}
\end{pmatrix}
\]

如果同时考虑垂直方向，则目标向量为：

\[
\mathbf{D}_\mathrm{eff}
=
\begin{pmatrix}
D_{x,1} \\
\vdots \\
D_{x,N} \\
D_{y,1} \\
\vdots \\
D_{y,N}
\end{pmatrix}
\]

第一版建议先只做水平，确认流程稳定后再加入垂直方向。

---

## 5. 能量扰动与 D_eff 测量

采用双边能量扰动：

\[
+\delta, \quad -\delta
\]

有效色散测量为：

\[
D_{x,i}^\mathrm{eff}
=
\frac{x_i(+\delta)-x_i(-\delta)}{2\delta}
\]

\[
D_{y,i}^\mathrm{eff}
=
\frac{y_i(+\delta)-y_i(-\delta)}{2\delta}
\]

### 推荐扰动幅度

\[
\delta \sim 10^{-4} \text{ 到 } 10^{-3}
\]

实际选择原则：

```text
1. 太小：BPM 噪声占主导
2. 太大：非线性、高阶色散、束损和 RF 副作用增大
3. 第一版优先选择机器可稳定重复的较小扰动
```

---

## 6. 第一版目标函数

第一版目标函数保持简单：

\[
F = \|\mathbf{D}_\mathrm{eff}\|^2 + \lambda_k \|\mathbf{u}-\mathbf{u}_0\|^2
\]

其中：

- \(\mathbf{D}_\mathrm{eff}\)：实测有效色散向量；
- \(\mathbf{u}\)：当前 knob 设置；
- \(\mathbf{u}_0\)：调束开始时的 knob 设置；
- \(\lambda_k\)：限制 knob 偏离初始工作点的正则化权重。

第一版也可以更简单，只用：

\[
F = \|\mathbf{D}_\mathrm{eff}\|^2
\]

但必须通过硬约束限制四极铁变化范围。

---

## 7. 硬约束与保护逻辑

第一版建议把机器保护作为硬约束，而不是复杂 penalty。

### 必须满足

```text
1. 四极铁变化量不超过限制
2. 参考能量轨道不能偏离太多
3. 束损不能超过阈值
4. 电荷 / 传输效率不能明显下降
5. BPM 数据必须有效
6. 每一步必须可回退
```

### 典型硬判断

```text
如果束损超过阈值：回退
如果电荷下降超过阈值：回退
如果参考轨道偏移过大：回退
如果四极铁超限：不允许应用
如果 D_eff 没变好：回退
```

---

## 8. 响应矩阵

定义响应矩阵：

\[
R_{ij}
=
\frac{\partial D_{\mathrm{eff},i}}{\partial u_j}
\]

其中：

- \(i\)：目标 BPM 信号编号；
- \(j\)：knob 编号；
- \(u_j\)：第 \(j\) 个对称四极铁 knob。

用中心差分测量：

\[
R_{ij}
\approx
\frac{D_{\mathrm{eff},i}(u_j+\Delta u_j)-D_{\mathrm{eff},i}(u_j-\Delta u_j)}{2\Delta u_j}
\]

对于两个 knob，响应矩阵为：

\[
\mathbf{R}
=
\begin{pmatrix}
\frac{\partial D_1}{\partial u_1} & \frac{\partial D_1}{\partial u_2} \\
\frac{\partial D_2}{\partial u_1} & \frac{\partial D_2}{\partial u_2} \\
\vdots & \vdots \\
\frac{\partial D_N}{\partial u_1} & \frac{\partial D_N}{\partial u_2}
\end{pmatrix}
\]

---

## 9. 校正量求解

线性近似下：

\[
\mathbf{D}_\mathrm{new}
\approx
\mathbf{D}_\mathrm{old}+\mathbf{R}\Delta\mathbf{u}
\]

希望：

\[
\mathbf{D}_\mathrm{new}\approx 0
\]

因此：

\[
\mathbf{R}\Delta\mathbf{u}=-\mathbf{D}_\mathrm{old}
\]

实现中使用归一化受限最小二乘求解，并由 gain 指定本轮目标改善比例：

\[
\mathbf{R}\Delta\mathbf{u}\approx-g\mathbf{D}_\mathrm{old}
\]

其中 (0<g\leq1)。每个 knob 同时满足单步和累计边界：

\[
|\Delta u_i|\leq f_\mathrm{step}L_i,
\qquad
|u_i-u_{i,\mathrm{start}}|\leq L_i
\]

这里 (L_i) 是 knob limit，(f_\mathrm{step}) 是最大单步比例。

---

## 10. 第一版主流程

```text
1. 保存当前机器状态

2. 测量初始 D_eff
   - 设能量为 +δ，测 BPM
   - 设能量为 -δ，测 BPM
   - 计算 D_eff

3. 对每个 knob 做 ±du 扫描
   - 测 D_eff(u_j + du)
   - 测 D_eff(u_j - du)
   - 得到响应矩阵 R 的一列

4. 用 SVD 求解校正量
   Δu = -R⁺ D_eff

5. 限制校正步长

6. 尝试小步应用
   u_trial = u_old + α Δu

7. 检查机器安全
   - 束损
   - 电荷
   - 轨道
   - 四极铁限值

8. 重新测量 D_eff

9. 若 D_eff 下降且机器状态正常，则接受

10. 否则回退

11. 迭代 2~5 次

12. 输出前后对比与最终设置
```

---

## 11. 伪代码：测量 D_eff

```python
def average_bpm(machine, bpm_list, samples_per_step, sample_interval_s):
    xs = []
    ys = []

    for index in range(samples_per_step):
        data = machine.read_bpm(bpm_list)
        xs.append(data.x)
        ys.append(data.y)
        if index + 1 < samples_per_step:
            sleep(sample_interval_s)

    x_avg = robust_average(xs)
    y_avg = robust_average(ys)

    return x_avg, y_avg
```

```python
def measure_deff(machine, energy_knob, delta, bpm_list, samples_per_step, use_vertical=False):
    """
    使用 +delta / -delta 双边能量扰动测量有效色散。
    """

    energy0 = energy_knob.get()

    # +delta
    energy_knob.set(energy0 + delta)
    machine.wait_stable()
    x_plus, y_plus = average_bpm(machine, bpm_list, samples_per_step)

    # -delta
    energy_knob.set(energy0 - delta)
    machine.wait_stable()
    x_minus, y_minus = average_bpm(machine, bpm_list, samples_per_step)

    # 恢复参考能量
    energy_knob.set(energy0)
    machine.wait_stable()

    # 计算有效色散
    dx_eff = (x_plus - x_minus) / (2.0 * delta)
    dy_eff = (y_plus - y_minus) / (2.0 * delta)

    if use_vertical:
        return concatenate([dx_eff, dy_eff])
    else:
        return dx_eff
```

---

## 12. 伪代码：响应矩阵测量

```python
def build_response_matrix(machine, energy_knob, knobs, config):
    """
    测量 R = dD_eff / dknob。
    """

    u0 = knobs.get()

    D0 = measure_deff(
        machine=machine,
        energy_knob=energy_knob,
        delta=config.delta_energy,
        bpm_list=config.target_bpms,
        samples_per_step=config.samples_per_step,
        use_vertical=config.use_vertical,
    )

    n_signal = len(D0)
    n_knob = len(u0)

    R = zeros((n_signal, n_knob))

    for j in range(n_knob):
        du = zero_vector_like(u0)
        du[j] = config.knob_scan_step[j]

        # +du
        knobs.set(u0 + du)
        machine.wait_stable()

        if not machine.is_safe():
            knobs.set(u0)
            machine.wait_stable()
            raise RuntimeError("Machine unsafe during +du scan")

        D_plus = measure_deff(
            machine=machine,
            energy_knob=energy_knob,
            delta=config.delta_energy,
            bpm_list=config.target_bpms,
            samples_per_step=config.samples_per_step,
            use_vertical=config.use_vertical,
        )

        # -du
        knobs.set(u0 - du)
        machine.wait_stable()

        if not machine.is_safe():
            knobs.set(u0)
            machine.wait_stable()
            raise RuntimeError("Machine unsafe during -du scan")

        D_minus = measure_deff(
            machine=machine,
            energy_knob=energy_knob,
            delta=config.delta_energy,
            bpm_list=config.target_bpms,
            samples_per_step=config.samples_per_step,
            use_vertical=config.use_vertical,
        )

        # 恢复
        knobs.set(u0)
        machine.wait_stable()

        # 中心差分
        R[:, j] = (D_plus - D_minus) / (2.0 * config.knob_scan_step[j])

    return R, D0
```

---

## 13. 伪代码：归一化受限最小二乘

```python
def solve_bounded_correction(R, D, gain, limits, max_step_fraction,
                             u_current, u_start, regularization):
    """
    求解 R du ~= -gain * D，并同时满足单步和累计边界。
    """

    step_limits = limits * max_step_fraction
    z = du / step_limits
    A = R @ diag(step_limits)

    # bounds 同时包含本轮最大步长和相对初始状态的剩余空间
    lower_du = maximum(-step_limits, u_start - limits - u_current)
    upper_du = minimum(+step_limits, u_start + limits - u_current)

    # 对归一化矩阵做 SVD，去除弱奇异方向
    U, S, Vt = svd(A, full_matrices=False)
    keep = S / max(S) > svd_cut
    A_reduced = U[:, keep].T @ A
    b_reduced = U[:, keep].T @ (-gain * D)

    # 正则项约束归一化 knob 使用量；适用于 knob 数多于 BPM 约束
    z = bounded_least_squares(
        A_reduced,
        b_reduced,
        bounds=(lower_du / step_limits, upper_du / step_limits),
        regularization=regularization,
    )
    return z * step_limits
```

---

## 14. 伪代码：第一版主程序

```python
def one_click_achromat_v1(machine, energy_knob, knobs, config):
    """
    双弯段消色散第一版：
    直接最小化 D_eff。
    """

    # 1. 保存初始状态
    state_start = machine.snapshot()
    u_start = knobs.get()

    # 2. 测初始 D_eff
    D_initial = measure_deff(
        machine=machine,
        energy_knob=energy_knob,
        delta=config.delta_energy,
        bpm_list=config.target_bpms,
        samples_per_step=config.samples_per_step,
        use_vertical=config.use_vertical,
    )

    F_initial = norm(D_initial) ** 2

    best_state = state_start
    best_u = u_start
    best_D = D_initial
    best_F = F_initial

    # 3. 迭代优化
    for iteration in range(config.max_iter):

        # 3.1 测响应矩阵
        R, D_current = build_response_matrix(
            machine=machine,
            energy_knob=energy_knob,
            knobs=knobs,
            config=config,
        )

        # 3.2 归一化受限最小二乘求校正量
        du_calc = solve_bounded_correction(
            R=R,
            D=D_current,
            gain=config.gain,
            limits=config.knob_limits,
            max_step_fraction=config.max_step_fraction,
            u_current=knobs.get(),
            u_start=u_start,
            regularization=config.regularization,
        )

        accepted = False

        # 3.3 应用受限候选步
        state_before = machine.snapshot()
        u_before = knobs.get()
        u_trial = u_before + du_calc

        # 应用试探设置
        knobs.set(u_trial)
        machine.wait_stable()

        # 安全检查
        if not machine.is_safe():
            machine.restore(state_before)
            break

        # 重新测量 D_eff
        D_trial = measure_deff(
            machine=machine,
            energy_knob=energy_knob,
            delta=config.delta_energy,
            bpm_list=config.target_bpms,
            samples_per_step=config.samples_per_step,
            use_vertical=config.use_vertical,
        )

        F_trial = norm(D_trial) ** 2

        # 接受条件：D_eff 下降且机器安全
        if F_trial < best_F:
            best_F = F_trial
            best_D = D_trial
            best_u = u_trial
            best_state = machine.snapshot()
            accepted = True

        # 否则回退
        else:
            machine.restore(state_before)
            machine.wait_stable()

        # 3.4 若这一轮没有接受任何步长，则停止
        if not accepted:
            break

        # 3.5 收敛判断
        if rms(best_D) < config.deff_tolerance:
            break

    # 4. 恢复到最优状态
    machine.restore(best_state)
    machine.wait_stable()

    # 5. 最终验证
    D_final = measure_deff(
        machine=machine,
        energy_knob=energy_knob,
        delta=config.delta_energy,
        bpm_list=config.target_bpms,
        samples_per_step=config.final_samples,
        use_vertical=config.use_vertical,
    )

    result = {
        "initial_D_eff_rms": rms(D_initial),
        "final_D_eff_rms": rms(D_final),
        "improvement": rms(D_initial) / rms(D_final),
        "initial_knobs": u_start,
        "final_knobs": best_u,
        "success": rms(D_final) < config.deff_tolerance,
    }

    return result
```

---

## 15. 推荐配置参数

```python
config = {
    # 能量扰动
    "delta_energy": 1e-4,   # 到 1e-3 之间选择

    # BPM
    "target_bpms": ["BPM01", "BPM02", "BPM03", "BPM04"],
    "use_vertical": False,

    # 每个状态的采样数、相邻样本间隔、最终采样数和设置后等待时间
    "samples_per_step": 5,
    "sample_interval_s": 0.1,
    "final_samples": 10,
    "settle_time_s": 1.0,

    # knob 扫描步长
    "knob_scan_step": [0.002, 0.002],  # 相对四极铁变化，例如 0.2%

    # SVD
    "svd_cut": 1e-3,

    # 每个 knob 相对初始状态的累计限制
    "knob_limits": [0.03, 0.03],

    # 每轮目标校正比例和单步比例
    "gain": 0.5,
    "max_step_fraction": 0.25,

    # 对归一化 knob 使用量的正则化
    "regularization": 1e-3,

    # 迭代次数
    "max_iter": 5,

    # 收敛阈值
    "deff_tolerance": 目标机器自定,
}
```

---

## 16. 第一版输出内容

一键调束结束后，应输出：

```text
1. 初始 RMS D_eff
2. 最终 RMS D_eff
3. 改善倍数
4. 每个目标 BPM 的 D_eff 前后对比
5. knob 初始值和最终值
6. knob 总变化量
7. 是否触及限值
8. 束损是否增加
9. 电荷 / 传输是否下降
10. 是否成功
```

示例：

```text
D_eff RMS:
    before: 120 mm
    after:   35 mm
    improvement: 3.4 x

Knob changes:
    Q1 symmetric knob: +0.42 %
    Q2 symmetric knob: -0.31 %

Reference orbit:
    max change: 0.18 mm

Transmission:
    before: 98.5 %
    after:  98.3 %

Status:
    Accepted
```

---

## 17. 第一版验收标准

第一版不要求完美消色散，只要求明显改善且机器状态不变坏。

建议验收条件：

\[
\frac{\mathrm{RMS}(D_\mathrm{eff})_\mathrm{before}}
{\mathrm{RMS}(D_\mathrm{eff})_\mathrm{after}}
> 2
\]

同时满足：

```text
1. 参考能量轨道变化小于阈值
2. 束损没有明显增加
3. 电荷 / 传输效率没有明显下降
4. 四极铁变化量在允许范围内
5. 最终状态可回退、可复现
```

---

## 18. 第一版风险与限制

第一版直接最小化总有效色散，有几个限制：

```text
1. 不能保证恢复真实一阶 achromat 光学
2. 不能区分真色散和 beta 轨道伪色散
3. 可能只是局部降低能量相关轨道响应
4. 如果目标 BPM 太少，可能只在局部压低 D_eff
5. 如果响应矩阵噪声大，SVD 解可能不稳定
6. 如果允许 knob 范围太大，可能跳到非设计分支
```

因此第一版必须限制在当前工作点附近：

\[
|u_i-u_{i,0}| < \Delta u_{i,\max}
\]

---

## 19. 第二版可以扩展的方向

第一版稳定后，可以逐步增加：

```text
1. 加入垂直 D_y_eff
2. 加入入口 BPM，用于识别 beta 轨道污染
3. 加入设计模型参考 k_design
4. 加入 Twiss / beta 约束
5. 加入束斑尺寸目标
6. 加入模型响应矩阵作为初值
7. 加入模型-实测混合响应矩阵
8. 加入黑盒优化做小范围精修
9. 加入高阶色散和 R56 相关指标
```

---

## 20. 总结

第一版一键消色散推荐定义为：

\[
\boxed{
\text{固定对称结构，只用两组对称四极铁 knob，}
\text{通过 } \pm\delta \text{ 能量扰动测 }D_\mathrm{eff},
\text{建立实测响应矩阵，用 SVD 小步迭代压低 }D_\mathrm{eff},
\text{全程加限幅、保护和回退。}
}
\]

其核心不是一次性找到完美设计解，而是先实现一个真实机器上可运行、可验证、可回退的最小闭环。
