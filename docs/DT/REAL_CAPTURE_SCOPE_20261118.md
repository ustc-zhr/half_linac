# HALF 四极铁归一化强度接入与局部预测

2026-11-18，接入目标为 HALF profile 定义的全部 68 块四极铁的**归一化强度**。当前 66 块具备 K1/K1_total 映射；QDU01/02 的 K1_total 缺失，不能把原始电流归档算作这两块四极铁已接入 ELEGANT。默认工作流不采用电流兜底，也不以采集 PV 数量代替模型接入完成度。

当前 real 采集器和 CLI/GUI 实机运行入口仍未实现。本轮仅修正方案和来源记录，没有修改产品代码、PV/IOC 配置、状态机或连接实机。

## 全机强度输入与局部模型

接入目标按 machine profile 的 `kind=quad` 动态推导，不受当前模型段限制。四极铁在 ELEGANT 中消费的是 K1，单位 1/m²；real 设备状态由 K1_total 回读驱动模型，K1 设置只用于追溯。

| 默认采集内容 | 已映射 PV 数 | 用途 |
| --- | ---: | --- |
| QL01～QL27、QT01～QT36、QE01～QE03 / K1_total | 66 | 归一化回读，模型按需选取 |
| 上述 66 块 / K1 | 66 | 归一化设置值追溯，不替代回读 |
| PRF06～PRF09 / sigx、sigy | 8 | 真实束宽，保留单位和测宽定义 |
| 合计 | **140** | 不含电流通道 |

QDU01/02 保留在 68 块接入目标中，但标为**归一化输入未接入**。[real.json](../../configs/machines/half/control_backends/real.json) 中，它们的 K1 与 current_set 指向同一个 `current:ao` PV，且没有 K1_total。检查到的模型快照默认转换为 `direct`，未找到在该 profile/shared 模型输入链中为 QDU 生效的电流至 K1 换算。后续需找到归一化 PV，或有来源的标定及换算，才能完成这两块的模型输入。

此前列出的原始电流、BPM、校正铁及 ICT 是可读状态盘点，不应全部塞入当前束宽模型的默认采集。当前先使用归一化强度和屏幕观测；确有轨道、传输率或稳定性分析需求时再增加相应观测。

当前局部预测仍为 `ALL_MAIN` 中的 QT01 entrance → PRF09，现有 `prepare_model()` 推导所需回读为 QT01～QT04 四项。全机快照提供状态，模型按段选择子集。当前模型仍限定为无 RF、无弯转的无耦合横向线性 RMS 输运，全机强度接入不等于全线物理预测已验证。

## PRF07 和 PRF09 的单位

用户于 2026-11-18 确认 PRF07/09 与其他 PRF 单位相同，按 **mm** 更新，不再列为单位缺口。PRF06/08 的高斯拟合 σ 定义沿用此前明确确认；本次单位确认不自动作为其他测宽算法或 RMS 定义的证据。

原始宽度、源时间、接收时间、报警和原始 EGU 均需保存。模型 RMS 与高斯拟合 σ 或算法定义尚未确认的观测分别标识；定义不兼容时不输出精度残差。无独立入口测量和历史原图的事实保持不变，不重复索要不存在的资料。

## PRF09 缺少 VM 束宽映射的含义

这是 [vm.json](../../configs/machines/half/control_backends/vm.json) 的软件配置缺项。PRF08 有 image、exposure_time、sigx、sigy；PRF09 只有 image、exposure_time，缺少 sigx/sigy 两个键。real.json 中 PRF09 的 H:SIZE/V:SIZE 映射已存在。

实际调用链为：

```text
ELEGANT PRF09.out 粒子数据
  → _load_watch_rms_from_sdds() 可以计算 x/y 总体 RMS
  → build_vm_publish_plan() 仅为已配置的 VM sigx/sigy 建立发布项
  → publish_watch_rms() 按发布项写入虚拟标量 PV
```

实际检查中，冻结的 PRF09.out 含 8768 个粒子；现有发布计划没有 PRF09 的 RMS 发布项。缺的是虚拟通道的配置及发布连接，**不是 ELEGANT 不会计算 PRF09 束宽，也不是缺少真实 PRF09 通道**。

后续补齐时需同步验证 IOC DB 生成、PV 别名和 VM 发布，不能仅写两个字符串就宣称已运行。本轮没有修改 PV、生成 IOC DB 或重启服务；此 VM 缺项不是全机 real 强度采集的前提。

## QHL1 至 QHL4 的具体位置

当前模型配置的 source_lattice 为 [lattice_ini.lte](../../src/virtual_machine/half_elegant/elegant/lattice_ini.lte)，其第 42～45 行是：

```text
QHL1: QUAD,L=0.3,K1=2.308226
QHL2: QUAD,L=0.3,K1=-1.975459
QHL3: QUAD,L=0.3,K1=2.832848
QHL4: QUAD,L=0.3,K1=-2.158739
```

每个名字在当前文件中仅出现这一次定义，没有被当前任何 LINE 引用；生成的 ALL_MAIN 也没有这些元件。旧文件 `lattice_ini_before20260717.lte` 和 `lattice_ini_origin.lte` 中曾有线路引用，当前主线使用 QT05～QT12。

它们应记为**当前未使用的历史格架定义**，不计作四块新增真实设备，不作为当前接入阻塞项。本轮不删除定义，也不猜测或新增真实 PV 映射。

## 下一阶段的最小实现

1. 复用 CapturePoint、sample_capture_points 和 MachineStateSnapshot，实现显式 real 一次只读采集；默认只采可确认的归一化强度和四屏观测，QDU 缺项明确归档，不读电流冒充已接入。
2. 模型仅使用该次快照中所选段的有效 K1_total。必要回读缺失、单位非等价或质量不符时拒绝该模型；其他段缺项与局部模型可用性分开。原始 EGU 保留，等价单位转换只在派生路径完成。
3. 实机入口仍需明确来源；工程预测可使用 assumed 输入，不自动填入 VM 入口或默认能量。比较保留观测定义，不强行将 Gaussian/unknown 标为 RMS。
4. 先用模拟 PV 读取器离线验收，再安排明确授权的实机采集。run-vm 保持拒绝 real，不增加设备设置、扫描、屏幕插拔或自动调束。

## 检查记录

最新澄清和默认 140 项清单位于 [runs/digital_shadow_scope_clarification/20261118](../../runs/digital_shadow_scope_clarification/20261118/) 的 `scope_clarification.json`。原全状态 159 项盘点保留在 [全四极铁盘点](../../runs/digital_shadow_all_quadrupoles/20261118/)，仅表示曾找到这些状态映射，不是当前默认采集方案，也不证明 68 块已全部完成归一化强度接入。

局部结构及四屏 GUI 导入检查见 [扩大范围检查](../../runs/digital_shadow_expanded_scope/20261118/)。所有检查均为离线，只读代码和已有产物，PV 访问为 0。
