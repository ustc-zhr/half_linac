# Development Log

以下内容从旧版 `README.md` 中拆出，作为历史开发记录保留。

## 2025-11-13 Zhanghaoran

- 增加新功能：`energy spectrum`

## 2025-09-25 Zhanghaoran

- `optimization`
- 继续开发贝叶斯优化算法，针对高维情况下添加多种采集函数优化器以提高收敛性。`@opt_algorithm_test`

## 2025-09-18 Zhanghaoran

- `optimization`
- 实现多目标贝叶斯优化算法，并利用 ZDT1 函数进行测试

## 2025-09-11 Zhanghaoran

- `jitter`
- 开展能量反馈功能开发：添加自定义抖动统计功能

## 2025-08-28 Zhanghaoran

- `optimization`
- 实现贝叶斯优化算法 `BO` 及其 GUI 界面，并通过虚拟加速器进行了初步测试

## 2025-08-21 Zhanghaoran

- `optimization`
- 实现贝叶斯优化算法，并针对 Rosenbrock 函数进行测试

## 2025-07-31 Zhanghaoran

- 继续在线优化功能开发 `optimization`
- 实现 `Rsimplex` 优化算法及其 GUI 界面，并通过虚拟加速器进行了初步测试

## 2025-07-24 Zhanghaoran

- 继续在线优化功能开发 `optimization`
- 实现 Robust simplex 算法，并针对 Rosenbrock 函数进行测试
- 参考文献：PHYSICAL REVIEW ACCELERATORS AND BEAMS 21, 104601 (2018)

## 2025-07-17 Zhanghaoran

- 继续在线优化功能开发 `optimization`
- 实现 RCDS 优化算法 GUI 界面，并通过虚拟加速器进行了初步测试

## 2025-07-10 Zhanghaoran

- 开展在线优化功能开发 `src/optimization`
- 实现 robust conjugate direction search (RCDS)，并通过 Rosenbrock 函数进行了测试
- 参考文献：
- Nuclear Instruments and Methods in Physics Research A 726 (2013) 77–83
- W.H. Press, et al., Numerical Recipes, 3rd edition, Cambridge University Press, 2007

## 2025-06-26 Zhanghaoran

- `orbit corrrect`
- 添加 SVD 方法求解逆矩阵

## 2025-06-19 Zhanghaoran

- `orbit corrrect`
- 完善 global correction 功能

## 2025-06-12 Zhanghaoran

- `orbit corrrect`
- 添加任意定义目标轨道功能

## 2025-05-29 Zhanghaoran

- `beam monitor`
- 优化束斑分布拟合，提升拟合准确度
- `Virtual Machine`
- 添加关闭功能

## 2025-05-22 Zhanghaoran

- `Virtual Machine`
- 添加 Q 铁的强度 jitter 功能

## 2025-05-15 Zhanghaoran

- `BBA`
- BBA2 经 debug 已可正确运行
- `orbit correct`
- 添加了任意自选需校正的 BPM 功能，并测试了其在 one-by-one 校正方法下的正确性

## 2025-05-08 Zhanghaoran

- 给 `orbit corrrect` 添加独立 GUI，可自定义相关参数，如采样间隔、校正精度
- 增加校正停止和归零功能按钮

## 2025-04-29 Zhanghaoran

- 重新调整 launcher 的 GUI 布局
- 将与 VM 相关的功能 `start VM`、`start IOC`、`add error` 单独放在一个用户界面
- 静态误差可自定义

## 2025-04-24 Zhanghaoran

- 鉴于 BPM 数量较多，`orbit_display` 界面增加了选择显示一定范围 BPMs 的选项
- 添加按钮可查看所有 BPM 的实时读数

## 2025-04-17 Zhanghaoran

- 发射度测量界面增加了 `simply VM` 按钮，可根据所选取的 Q 铁和 FLAG 简化 lattice，加速虚拟加速器运行速度
- `full VM` 按钮可将 lattice 恢复到原始状态

## 2024-03 至 2025-04 Zhangshancai

- 内容待补充

## 2024-03-11 Libiaobin

- 以 `lattice_ini.lte` => `lattice.json` 作为输入文件，自动生成 `quad.template`、`bpm.template` 等 IOC 文件。后面如果需要修改元件名称和 PV 命名规则，直接修改 `lattice_ini.lte` 文件即可。见 `gen_substitution_file()`
- 以 `lattice.json` 文件作为中间媒介：
- 当 EPICS 修改了 quad 的 `K1` 值时，IOC 监测到 PV 值发生变化后会自动更新 `lattice.json` 文件。当前只添加了 QUAD。
- Elegant 每次循环运行时，都会重新读取 `lattice.json` 文件，生成 `lattice.lte`，然后运行。注意，`lattice_ini.lte` 文件本身没有改变。
