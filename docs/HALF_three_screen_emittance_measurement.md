# HALF 直线加速器末端三屏法发射度测量：原理与实现路线讨论

## 1. 背景与目标

目前 `half_linac` 已经实现基于 **扫描四极铁（Quadrupole Scan）** 的横向发射度测量。

在 HALF 直线加速器末端，还存在三个 FLAG 靶，三个观测位置之间主要由：

- 正常四极铁（Quadrupole）
- 漂移段（Drift）

组成。

因此可以考虑利用三个 FLAG 在不同纵向位置测得的束斑尺寸，反演束流二阶矩矩阵，从而获得：

- 水平几何发射度 \(\varepsilon_x\)
- 垂直几何发射度 \(\varepsilon_y\)
- Twiss 参数 \((\alpha_x,\beta_x)\)
- Twiss 参数 \((\alpha_y,\beta_y)\)

该方法本质上属于：

> **Three-Screen Emittance Measurement / Multi-Screen Beam Matrix Reconstruction**

后续建议不要仅将其实现成“固定三屏法”，而是进一步设计成一个更通用的：

> **Multi-Screen Emittance / Beam Matrix Reconstruction App**

从而支持未来 3 个以上 screen、最小二乘拟合、误差分析和测量光学优化。

---

# 2. 发射度测量的本质

以水平平面为例，在参考位置 \(s_0\)，横向相空间二阶矩矩阵为

\[
\Sigma_0=
\begin{pmatrix}
\langle x^2\rangle & \langle xx'\rangle\\
\langle xx'\rangle & \langle x'^2\rangle
\end{pmatrix}
\]

定义

\[
A=\langle x^2\rangle
\]

\[
B=\langle xx'\rangle
\]

\[
C=\langle x'^2\rangle
\]

则

\[
\Sigma_0=
\begin{pmatrix}
A&B\\
B&C
\end{pmatrix}
\]

几何 RMS 发射度为

\[
\boxed{
\varepsilon_x
=
\sqrt{AC-B^2}
}
\]

因此，发射度测量的核心问题实际上是：

> **如何通过束斑尺寸测量反演得到 \(A,B,C\) 三个二阶矩。**

---

# 3. 与 Twiss 参数的关系

束流矩阵也可写为

\[
\Sigma_0
=
\varepsilon
\begin{pmatrix}
\beta & -\alpha\\
-\alpha & \gamma
\end{pmatrix}
\]

其中

\[
\gamma=\frac{1+\alpha^2}{\beta}
\]

因此

\[
A=\varepsilon\beta
\]

\[
B=-\varepsilon\alpha
\]

\[
C=\varepsilon\gamma
\]

反演得到 \(A,B,C\) 后：

\[
\varepsilon=\sqrt{AC-B^2}
\]

\[
\beta=\frac{A}{\varepsilon}
\]

\[
\alpha=-\frac{B}{\varepsilon}
\]

\[
\gamma=\frac{C}{\varepsilon}
\]

因此一次有效的三屏测量不仅能够得到发射度，也能够得到参考位置处的 Twiss 参数。

---

# 4. 单个 FLAG 提供的测量方程

假设从参考位置 \(s_0\) 到第 \(i\) 个 FLAG 的横向一阶传输矩阵为

\[
M_i=
\begin{pmatrix}
R_{11,i} & R_{12,i}\\
R_{21,i} & R_{22,i}
\end{pmatrix}
\]

束流矩阵传播满足

\[
\Sigma_i=M_i\Sigma_0M_i^T
\]

FLAG 测得的 RMS 束斑尺寸为

\[
\sigma_{x,i}^2=(\Sigma_i)_{11}
\]

展开可得

\[
\boxed{
\sigma_{x,i}^2
=
R_{11,i}^2A
+
2R_{11,i}R_{12,i}B
+
R_{12,i}^2C
}
\]

因此：

- 一个 FLAG 提供一个关于 \(A,B,C\) 的方程；
- \(A,B,C\) 有三个未知量；
- 理论上至少需要三个独立的束斑测量条件。

这就是三屏法的基本数学来源。

---

# 5. 三个 FLAG 的矩阵反演

对于三个 FLAG：

\[
\sigma_1,\sigma_2,\sigma_3
\]

分别对应三个不同的线性传输条件，则有

\[
\begin{pmatrix}
\sigma_1^2\\
\sigma_2^2\\
\sigma_3^2
\end{pmatrix}
=
\begin{pmatrix}
R_{11,1}^2 &
2R_{11,1}R_{12,1} &
R_{12,1}^2\\
R_{11,2}^2 &
2R_{11,2}R_{12,2} &
R_{12,2}^2\\
R_{11,3}^2 &
2R_{11,3}R_{12,3} &
R_{12,3}^2
\end{pmatrix}
\begin{pmatrix}
A\\
B\\
C
\end{pmatrix}
\]

记作

\[
\mathbf y=H\mathbf z
\]

其中

\[
\mathbf z=
\begin{pmatrix}
A\\B\\C
\end{pmatrix}
\]

如果 \(H\) 满秩：

\[
\operatorname{rank}(H)=3
\]

则理论上可以直接反演：

\[
\mathbf z=H^{-1}\mathbf y
\]

得到 \(A,B,C\)，进而计算

\[
\varepsilon_x,\alpha_x,\beta_x
\]

---

# 6. 如果第一个 FLAG 就是参考位置

如果选择 FLAG1 所在位置作为参考位置，则

\[
M_1=I
\]

因此

\[
R_{11,1}=1
\]

\[
R_{12,1}=0
\]

于是：

\[
\sigma_1^2=A
\]

即第一个 FLAG 直接给出了参考位置的

\[
\langle x^2\rangle
\]

FLAG2 和 FLAG3 再提供另外两个独立方程。

这种参考点选择通常比较直观，也有利于后续软件实现和结果展示。

---

# 7. 纯漂移三屏法

实际上，即使三个 FLAG 之间没有四极铁，仅有漂移段，也可以测量发射度。

漂移段传输矩阵为

\[
M(L)=
\begin{pmatrix}
1&L\\
0&1
\end{pmatrix}
\]

因此

\[
R_{11}=1
\]

\[
R_{12}=L
\]

代入束斑方程：

\[
\boxed{
\sigma_x^2(L)
=
A+2LB+L^2C
}
\]

因此 \(\sigma_x^2\) 关于位置 \(L\) 是一个二次函数。

三个不同位置：

\[
L_1,L_2,L_3
\]

对应三个束斑尺寸：

\[
\sigma_1,\sigma_2,\sigma_3
\]

即可拟合出

\[
A,\quad B,\quad C
\]

HALF 末端在三个 FLAG 之间还有四极铁，因此其光学条件比纯漂移三屏法更加丰富。

---

# 8. 三屏法与四极扫描法的统一理解

这一点对于 `half_linac` 的软件架构尤其重要。

## 8.1 四极扫描法

四极扫描通常采用：

\[
Q(K)
\rightarrow
\text{Drift}
\rightarrow
\text{Screen}
\]

改变四极铁强度：

\[
K_1,K_2,\ldots,K_N
\]

每个 \(K_i\) 对应一个不同的传输矩阵：

\[
R_{11}(K_i),R_{12}(K_i)
\]

因此得到

\[
\sigma_i^2
=
R_{11}(K_i)^2A
+
2R_{11}(K_i)R_{12}(K_i)B
+
R_{12}(K_i)^2C
\]

通过多个扫描点反演 \(A,B,C\)。

---

## 8.2 三屏法

三屏法中磁铁保持不变：

\[
K=\mathrm{const}
\]

但选择不同的观测位置：

\[
FLAG1,\ FLAG2,\ FLAG3
\]

因此自然获得不同的

\[
R_{11,i},R_{12,i}
\]

同样可以构造：

\[
\sigma_i^2
=
R_{11,i}^2A
+
2R_{11,i}R_{12,i}B
+
R_{12,i}^2C
\]

---

## 8.3 两种方法的数学本质

| 方法 | 如何产生不同的光学条件 |
|---|---|
| Quadrupole Scan | 固定 screen，改变 \(K\) |
| Three-Screen | 固定 magnet，改变 screen 位置 |
| Multi-Screen | 使用多个不同位置 |
| 一般 Beam Matrix Reconstruction | 任意多个独立的线性传输条件 |

因此两种方法可以统一为：

> **利用不同的一阶传输矩阵条件反演入口束流二阶矩矩阵。**

从软件架构角度，不建议完全独立实现两套核心算法，而应尽量共用：

- transport matrix calculation
- beam matrix reconstruction
- emittance/Twiss calculation
- fit validation
- uncertainty estimation

等底层模块。

---

# 9. 水平与垂直平面

如果末端束线没有明显的横纵向耦合元件，例如：

- solenoid
- skew quadrupole
- 大的 quadrupole roll
- 其他明显的 \(x-y\) coupling

则可以分别处理水平和垂直平面。

---

## 9.1 水平

\[
\sigma_{x,i}^2
=
R_{11,i}^2A_x
+
2R_{11,i}R_{12,i}B_x
+
R_{12,i}^2C_x
\]

得到

\[
\varepsilon_x,\alpha_x,\beta_x
\]

---

## 9.2 垂直

使用 4×4 横向矩阵中的

\[
R_{33},R_{34}
\]

则

\[
\sigma_{y,i}^2
=
R_{33,i}^2A_y
+
2R_{33,i}R_{34,i}B_y
+
R_{34,i}^2C_y
\]

得到

\[
\varepsilon_y,\alpha_y,\beta_y
\]

因此每个 FLAG 图像实际上可以同时提供：

\[
\sigma_x
\]

和

\[
\sigma_y
\]

理论上一次三屏测量可以同时获得两个平面的发射度与 Twiss 参数。

---

# 10. 三个 FLAG 并不意味着一定“测得好”

这是 HALF 实际应用前必须重点检查的问题。

三屏法要求：

\[
\operatorname{rank}(H)=3
\]

但仅仅满足满秩还不够。

如果三个光学条件非常接近，例如

\[
\frac{R_{12,1}}{R_{11,1}}
\approx
\frac{R_{12,2}}{R_{11,2}}
\approx
\frac{R_{12,3}}{R_{11,3}}
\]

则三个方程虽然数学上独立，但数值上可能高度相关。

此时测量矩阵

\[
H
\]

会出现较大的 condition number。

后果包括：

- 束斑尺寸小误差被严重放大；
- \(A,B,C\) 对测量噪声非常敏感；
- 发射度误差很大；
- 甚至可能出现

\[
AC-B^2<0
\]

即非物理解。

因此，判断 HALF 三屏系统是否适合发射度测量，不能只看“有三个 FLAG”，而应该实际计算：

\[
R_{11,i},R_{12,i}
\]

\[
R_{33,i},R_{34,i}
\]

以及测量矩阵的：

- rank
- singular values
- condition number

---

# 11. 光学条件优化

HALF 末端三个 FLAG 之间还有 Q 铁，因此如果当前机器设置下三屏反演条件较差，可以考虑：

> **不是扫描 Q 铁做发射度测量，而是在测量开始前调整 Q 铁，把三个 FLAG 对应的 phase-space projection 调整到更有利于反演的状态。**

也就是说可以设计两种工作模式：

### Mode A：Passive Three-Screen

保持当前机器 optics 不变，直接利用三个 FLAG 测量。

优点：

- 不改变机器状态；
- 快速；
- 适合 commissioning 中的快速检查。

### Mode B：Optimized Three-Screen

测量开始前根据模型优化末端 Q 铁，使三个 FLAG 的光学条件更加正交、condition number 更小。

优点：

- 更高测量精度；
- 更好的数值稳定性。

这种“测量 optics 优化”后续非常值得加入应用中。

---

# 12. 需要检查的物理前提

## 12.1 一阶线性光学

基础公式采用：

\[
\Sigma_i=M_i\Sigma_0M_i^T
\]

因此默认 FLAG 区间可以用一阶线性传输描述。

对于：

- quadrupole
- drift

通常是比较理想的。

但仍应检查是否存在：

- sextupole / higher-order effects
- 大振幅引起的非线性
- aperture clipping

---

## 12.2 色散影响

如果末端存在显著水平色散：

\[
D_x\neq0
\]

则 FLAG 测量的水平束斑不仅包含 betatron contribution。

简单情况下：

\[
\sigma_x^2
=
\sigma_{x,\beta}^2
+
D_x^2\sigma_\delta^2
\]

因此直接进行二维 \(x-x'\) 反演得到的可能不是纯 betatron emittance。

正式使用前应检查：

\[
D_x
\]

\[
D'_x
\]

以及束流能散：

\[
\sigma_\delta
\]

如果末端是接近 achromatic 的直线段，则问题较小。

---

## 12.3 空间电荷

标准三屏法假设 FLAG 之间主要服从单粒子线性输运。

如果末端：

- 束流能量较高；
- 峰值电流适中；
- 空间电荷效应很弱；

则这一近似通常成立。

但对于 HALF 的特殊高电荷或低能模式，需要通过模型验证空间电荷是否可以忽略。

---

## 12.4 shot-to-shot 稳定性

三个 FLAG 通常是 interceptive diagnostics，实际操作很可能是依次插入：

1. FLAG1
2. FLAG1 retract
3. FLAG2
4. FLAG2 retract
5. FLAG3

因此三个束斑通常不是同一个电子脉冲测得的。

测量期间需要监控：

- bunch charge
- beam energy
- RF amplitude
- RF phase
- orbit
- magnet current

如果束流条件发生显著漂移，三屏反演会产生额外系统误差。

因此后续应用建议保存每次 FLAG 测量时对应的 machine snapshot。

---

# 13. 三屏法相对于 Quadrupole Scan 的潜在优势

对于 commissioning 应用，三屏法具有明显优势。

## 13.1 不需要扫描磁铁

Quadrupole Scan：

\[
K_1\rightarrow K_2\rightarrow\cdots\rightarrow K_N
\]

每一步均需：

- 改变 Q 铁；
- 等待 magnet settle；
- 检查 orbit / transmission；
- 拍摄 screen。

三屏法：

\[
K=\mathrm{const}
\]

只需要依次读取三个 FLAG。

---

## 13.2 不改变当前机器 optics

这对运行阶段尤其有价值。

可以将其作为：

> **当前机器状态下的快速 emittance / Twiss diagnostic**

而不是一个侵入性较强的 optical scan。

---

## 13.3 降低大束斑和孔径风险

Quadrupole Scan 在部分 \(K\) 值下可能产生很大的束斑：

- 撞孔径；
- 超出 FLAG 可视范围；
- beam loss 增大。

三屏法避免持续扫描 Q 铁，可降低这种风险。

---

## 13.4 测量速度可能明显更快

如果 FLAG 插拔速度较快，三屏法可以非常适合：

- initial commissioning
- machine setup verification
- optics validation
- virtual accelerator model validation

---

# 14. 三屏法的主要局限

主要问题包括：

1. 三个 FLAG 的光学条件由装置几何位置决定；
2. 如果测量矩阵 condition number 太大，测量误差会严重放大；
3. 三次 screen 测量之间存在 shot-to-shot drift；
4. 水平色散可能污染水平发射度；
5. 只使用三个点时没有冗余信息，很难判断单个异常测量点；
6. 如果存在明显 \(x-y\) coupling，简单二维算法不再严格成立。

因此工程实现中不建议只做“3 点直接求逆”，而应尽可能提供更鲁棒的拟合和诊断功能。

---

# 15. 推荐的软件定位

建议新功能不要仅命名为：

> Three Screen Emittance

更推荐在软件内部将核心功能抽象为：

> **Beam Matrix Reconstruction**

具体 GUI/App 可以叫：

> **Multi-Screen Emittance Measurement**

这样可以自然兼容：

- 3-screen measurement
- 4-screen / N-screen measurement
- quadrupole scan
- 混合测量
- model-based diagnostics

其数学核心统一为：

\[
\mathbf y=H\mathbf z
\]

其中：

\[
\mathbf z=
(A,B,C)^T
\]

---

# 16. 三屏与多屏的求解方法

## 16.1 严格三屏

如果只有三个测量：

\[
H\in\mathbb R^{3\times3}
\]

可以直接：

\[
\mathbf z=H^{-1}\mathbf y
\]

但工程上不建议仅依赖显式矩阵求逆。

建议使用：

- `numpy.linalg.solve`
- SVD

等方式提高数值稳定性。

---

## 16.2 Multi-Screen

如果未来有：

\[
N>3
\]

个 measurement conditions，则

\[
H\in\mathbb R^{N\times3}
\]

变成过约束问题。

建议使用最小二乘：

\[
\mathbf z
=
\arg\min_{\mathbf z}
\|H\mathbf z-\mathbf y\|^2
\]

即：

\[
\mathbf z=H^+\mathbf y
\]

其中 \(H^+\) 为 pseudo-inverse。

---

## 16.3 加权最小二乘

如果每个 FLAG 束斑测量误差不同：

\[
\delta\sigma_i
\]

则推荐使用 weighted least squares：

\[
\mathbf z
=
\arg\min
(\mathbf y-H\mathbf z)^T
W
(\mathbf y-H\mathbf z)
\]

其中

\[
W=
\operatorname{diag}
\left(
\frac{1}{\delta y_1^2},
\dots,
\frac{1}{\delta y_N^2}
\right)
\]

这里：

\[
y_i=\sigma_i^2
\]

因此近似有：

\[
\delta y_i\approx 2\sigma_i\delta\sigma_i
\]

---

# 17. 物理约束

最终解必须满足：

\[
A>0
\]

\[
C>0
\]

\[
AC-B^2>0
\]

对应：

\[
\Sigma>0
\]

即协方差矩阵必须为 positive definite。

如果反演后得到：

\[
AC-B^2\le0
\]

则应认为：

> **当前测量不能产生一个物理有效的 beam matrix。**

这通常意味着：

- 测量噪声过大；
- optics 条件不合适；
- transport model 不准确；
- 输入 beam 在不同 FLAG 测量期间发生变化；
- 存在未考虑的 dispersion / coupling / space charge。

GUI 不应简单将其处理为计算异常，而应给出明确的 physics warning。

---

# 18. HALF 下一步最应该先做的事情

在真正写 App 之前，首先应回答：

> **HALF 末端三个 FLAG 的布局是否真的适合进行高质量三屏法发射度测量？**

因此第一阶段重点不是 GUI，而是 lattice / optics analysis。

建议：

## Step 1：确定三个 FLAG

获得：

- FLAG 名称
- longitudinal position \(s\)
- 对应 EPICS PV
- 图像获取方式
- pixel calibration

---

## Step 2：确定中间 lattice

列出 FLAG1 到 FLAG3 之间的：

- quadrupole
- drift
- 其他可能存在的 beamline elements

---

## Step 3：建立参考位置

推荐优先考虑：

\[
s_0=\mathrm{FLAG1}
\]

也可以根据 lattice 和现有 `half_linac` 模型结构选择其他位置。

---

## Step 4：计算 transport matrix

对每个 FLAG 获得：

### Horizontal

\[
R_{11,i},R_{12,i}
\]

### Vertical

\[
R_{33,i},R_{34,i}
\]

---

## Step 5：建立 measurement matrix

水平：

\[
H_x=
\begin{pmatrix}
R_{11,1}^2 & 2R_{11,1}R_{12,1} & R_{12,1}^2\\
R_{11,2}^2 & 2R_{11,2}R_{12,2} & R_{12,2}^2\\
R_{11,3}^2 & 2R_{11,3}R_{12,3} & R_{12,3}^2
\end{pmatrix}
\]

垂直：

\[
H_y=
\begin{pmatrix}
R_{33,1}^2 & 2R_{33,1}R_{34,1} & R_{34,1}^2\\
R_{33,2}^2 & 2R_{33,2}R_{34,2} & R_{34,2}^2\\
R_{33,3}^2 & 2R_{33,3}R_{34,3} & R_{34,3}^2
\end{pmatrix}
\]

---

## Step 6：检查 observability

至少计算：

```python
np.linalg.matrix_rank(H)
np.linalg.cond(H)
np.linalg.svd(H)
```

分别分析：

- horizontal
- vertical

---

## Step 7：扫描末端 Q 铁工作点

如果当前 optics 条件不好，可以使用 virtual accelerator / lattice model 扫描或优化末端 Q 铁：

目标例如：

\[
\min \operatorname{cond}(H_x)
\]

以及

\[
\min \operatorname{cond}(H_y)
\]

或综合：

\[
\min
\left[
w_x f(H_x)+
w_y f(H_y)
\right]
\]

同时加入：

- beam size limit
- aperture constraint
- magnet strength limit

从而获得推荐的：

> **Three-Screen Measurement Optics**

---

# 19. 后续 half_linac App 的建议功能

正式 App 可以分为以下模块。

## 19.1 Machine Setup

显示：

- three FLAGs
- quadrupoles
- current magnet settings
- beam energy
- transport matrix source

---

## 19.2 Optics Validation

显示：

- \(R_{11},R_{12}\)
- \(R_{33},R_{34}\)
- \(H_x\)
- \(H_y\)
- rank
- condition number
- singular values

并给出简单状态：

- Good
- Marginal
- Poor

---

## 19.3 FLAG Acquisition

依次：

1. insert FLAG
2. acquire image
3. calculate centroid
4. calculate RMS beam size
5. record timestamp
6. record machine snapshot
7. retract FLAG

---

## 19.4 Beam Size Analysis

每个 FLAG 输出：

- \(x_c\)
- \(y_c\)
- \(\sigma_x\)
- \(\sigma_y\)
- fit quality
- ROI
- background level
- saturation status

---

## 19.5 Beam Matrix Reconstruction

分别计算：

### X-plane

\[
A_x,B_x,C_x
\]

### Y-plane

\[
A_y,B_y,C_y
\]

---

## 19.6 Result

输出：

\[
\varepsilon_x,\quad
\alpha_x,\quad
\beta_x
\]

\[
\varepsilon_y,\quad
\alpha_y,\quad
\beta_y
\]

如果束流能量已知，可进一步输出 normalized emittance：

\[
\varepsilon_n
=
\beta_{\rm rel}\gamma_{\rm rel}\varepsilon
\]

---

## 19.7 Diagnostics

至少应提供：

- physical beam matrix check
- residual
- condition number
- beam stability
- dispersion warning
- image saturation warning
- aperture clipping warning

---

# 20. 与现有 Quadrupole Scan App 的代码复用

建议 Codex 首先审查现有 quadrupole scan 实现。

重点识别哪些模块可以抽象和复用：

### 可以共用的部分

- FLAG image acquisition
- image background subtraction
- centroid calculation
- RMS beam size calculation
- Gaussian fit（如果已有）
- beam energy acquisition
- emittance/Twiss calculation
- plotting
- result export
- EPICS machine snapshot
- matrix transport calculation

### 应新增的部分

- multiple FLAG sequence control
- screen-to-reference transport matrix
- multi-screen measurement matrix
- SVD / condition number
- physical covariance matrix validation
- multi-screen least squares
- measurement optics evaluation

最终理想结构可以是：

```text
BeamSizeMeasurement
        │
        ├── QuadScanMeasurement
        │
        └── MultiScreenMeasurement
                    │
                    ↓
         BeamMatrixReconstruction
                    │
                    ↓
          Emittance / Twiss
```

---

# 21. 推荐的开发顺序

后续与 Codex 落地时，建议按照以下顺序推进：

## Phase 1：只做离线物理验证

暂时不写 GUI。

输入：

- lattice
- magnet strengths
- beam energy
- three FLAG locations

输出：

- transport matrices
- \(H_x,H_y\)
- rank
- condition number
- theoretical reconstruction test

---

## Phase 2：Simulation Test

从 Elegant / Cheetah / existing virtual accelerator 给定：

\[
\Sigma_0
\]

向三个 FLAG 正向传播得到：

\[
\sigma_1,\sigma_2,\sigma_3
\]

再用三屏算法反演。

验证：

\[
\Sigma_{\rm reconstructed}
\approx
\Sigma_{\rm truth}
\]

以及：

\[
\varepsilon_{\rm reconstructed}
\approx
\varepsilon_{\rm truth}
\]

---

## Phase 3：加入测量误差

给：

\[
\sigma_i
\]

加入随机误差，例如：

- 1%
- 2%
- 5%

分析 reconstruction robustness。

重点研究：

\[
\text{condition number}
\]

与

\[
\Delta\varepsilon/\varepsilon
\]

之间的关系。

---

## Phase 4：寻找最佳 measurement optics

扫描/优化末端 Q 铁。

目标：

- condition number 小；
- 三个 FLAG beam size 合理；
- 不撞 aperture；
- x/y 两个平面都可测。

---

## Phase 5：接入在线 FLAG 图像

复用现有 quadrupole scan 中的图像处理模块。

---

## Phase 6：实现 App

最后再加入：

- state machine
- acquisition
- online reconstruction
- plots
- save/export
- machine snapshot

---

# 22. 当前结论

根据目前已知 HALF 末端结构：

> 三个 FLAG 之间主要由四极铁和漂移段组成。

因此，从物理原理上：

\[
\boxed{\text{HALF 末端三个 FLAG 可以用于三屏法发射度测量}}
\]

其本质与现有 Quadrupole Scan 完全一致：

\[
\boxed{
\text{不同线性传输条件}
+
\text{束斑尺寸测量}
\Rightarrow
\text{二阶束流矩阵反演}
}
\]

真正需要进一步验证的不是“能不能测”，而是：

\[
\boxed{
\text{HALF 当前三个 FLAG 的 optical conditioning 是否足够好}
}
\]

因此下一步最优先的任务应是：

> **从 HALF 实际 lattice 中读取三个 FLAG 及中间 Q 铁，计算三个 FLAG 相对于公共参考位置的 \(R_{11},R_{12},R_{33},R_{34}\)，进一步计算 \(H_x,H_y\) 的 rank、singular values 和 condition number。**

这一步完成后，再决定：

1. 当前 optics 是否可以直接三屏测量；
2. 是否需要设计专门的 three-screen measurement optics；
3. 如何将核心算法整合进 `half_linac`；
4. 如何复用已有 quadrupole scan 的图像和发射度分析模块。

---

# 23. 下一轮与 Codex 建议直接开展的任务

可以让 Codex 首先完成：

```text
1. 审查 half_linac 当前 quadrupole scan 发射度测量实现；
2. 找出束斑分析、传输矩阵、发射度/Twiss 计算等可复用模块；
3. 从 HALF lattice/configuration 中定位直线加速末端三个 FLAG；
4. 列出三个 FLAG 之间所有 Q 和 drift；
5. 计算 FLAG1 → FLAG1/2/3 的横向一阶传输矩阵；
6. 构造 Hx、Hy；
7. 输出 rank、SVD、condition number；
8. 判断当前机器 optics 是否适合 three-screen emittance measurement；
9. 暂时不要先写 GUI。
```

建议先完成这一轮物理和代码架构验证，再进入在线测量应用开发。
