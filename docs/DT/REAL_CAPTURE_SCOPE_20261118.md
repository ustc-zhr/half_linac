# HALF 四极铁归一化强度接入与局部预测

2026-11-18，接入目标为 HALF profile 定义的全部 68 块四极铁的**归一化强度**。当前 66 块具备 K1/K1_total 映射；QDU01/02 的 K1_total 缺失，不能把原始电流归档算作这两块四极铁已接入 ELEGANT。默认工作流不采用电流兜底，也不以采集 PV 数量代替模型接入完成度。

已实现 real 一次只读采集器、显式 CLI/GUI 入口、无 PV 离线演练及新归档重放，并完成模拟读取器与 ELEGANT 验收。当前未连接实机，现场连接性和部署端诊断语义仍待具备条件后验证。PRF09 VM 映射已独立补齐；real PV、调束状态机和运行中的 IOC 保持不变。

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

## PRF09 VM 束宽映射

已按用户要求在 [vm.json](../../configs/machines/half/control_backends/vm.json) 为 PRF09 补齐两个通道，沿用现有 PRF 命名：`sigx → HALF:IN:FLAG:PRF09:sigx`，`sigy → HALF:IN:FLAG:PRF09:sigy`。real.json 的 H:SIZE/V:SIZE 映射保持不变。

实际调用链为：

```text
ELEGANT PRF09.out 粒子数据
  → _load_watch_rms_from_sdds() 可以计算 x/y 总体 RMS
  → build_vm_publish_plan() 仅为已配置的 VM sigx/sigy 建立发布项
  → publish_watch_rms() 按发布项写入虚拟标量 PV
```

补齐配置后，现有发布计划包含 PRF09 的 RMS 项，IOC 生成器自动生成对应的 SIGX/SIGY 记录及别名，无需改变产品发布器或模板。VM 标量仍是粒子总体 RMS、单位 mm，与真实图像高斯拟合 σ 的定义分开。

验证结果：43 项 VM/GUI 定向回归通过；2 项隔离 CA/ELEGANT 集成通过，GUI 用例覆盖 QT01→PRF09 四屏八项，使用现有 IOC 模板、实际 RMS 发布、一次采集及离线重放，最大束宽残差为 8.88e-16 mm；仓库静态检查通过。临时 IOC 使用随机 loopback 端口，当前服务和运行文件保持不变。

当前运行实例不会热加载 profile。实际使用时先停止 VM Runtime 和 softIOC，再依次启动 softIOC、VM Runtime，并重新打开 Digital Shadow；IOC DB 由现有流程重生成，无需重编译 IOC。新增通道改变 VM profile 签名，旧归档可继续查看；旧快照重放须使用原配置版本，或重新采集新快照，不修改旧快照签名。详见 [GUI 说明](DIGITAL_SHADOW_GUI.md)。

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

## 已实现的工程范围与现场待验证项

1. 已复用 CapturePoint、sample_capture_points 和 MachineStateSnapshot，实现显式 real 一次只读采集；默认只采可确认的归一化强度和四屏观测，QDU 缺项明确归档，不读电流冒充已接入。无关断连不先耗尽必要回读，所有分组共享原窗口。
2. 模型仅使用该次快照中所选段的有效 K1_total。必要回读缺失、单位非等价或质量不符时拒绝该模型；其他段缺项与局部模型可用性分开。原始 EGU 保留，等价单位转换只在派生路径完成。
3. 实机入口仍需明确来源；工程预测可使用 assumed 输入，不自动填入 VM 入口或默认能量。比较保留观测定义，不强行将 Gaussian/unknown 标为 RMS。
4. 已用模拟读取器完成离线验收，GUI 提供 NO PV 演练。原生读取仍需显式操作；本轮不执行实机采集。run-vm 保持拒绝 real，不增加设备设置、扫描、屏幕插拔或自动调束。操作、接口和验收结果见 [real 离线实现](REAL_CAPTURE_OFFLINE_20261118.md)。

## 检查记录

最新澄清和默认 140 项清单位于 [runs/digital_shadow_scope_clarification/20261118](../../runs/digital_shadow_scope_clarification/20261118/) 的 `scope_clarification.json`。原全状态 159 项盘点保留在 [全四极铁盘点](../../runs/digital_shadow_all_quadrupoles/20261118/)，仅表示曾找到这些状态映射，不是当前默认采集方案，也不证明 68 块已全部完成归一化强度接入。

局部结构及四屏 GUI 导入检查见 [扩大范围检查](../../runs/digital_shadow_expanded_scope/20261118/)。所有检查均为离线，只读代码和已有产物，PV 访问为 0。
