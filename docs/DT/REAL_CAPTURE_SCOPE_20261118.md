# HALF 全机四极铁采集与局部预测接入

2026-11-18，按用户要求，将首轮 real 状态采集扩大为 **HALF profile 定义的全部 68 块四极铁**，另保留 PRF06～PRF09 四屏及沿途 BPM、校正铁电流和 ICT。四极铁原生状态共 136 个标量，加上局部观测 23 项，合计 **159 个不同 PV**。当前模型预测仍使用 QT01 entrance → PRF09 的局部段，采集范围与模型范围分开。

本轮完成实际模型结构、real PV 映射和 GUI 请求导入检查，更新接入方案与[准备清单](REAL_POC_INPUTS.json)。real 采集器、CLI 和 GUI 实机运行入口尚未实现；没有连接实机或修改产品代码、IOC、PV 映射及调束状态机。此前缺少历史原图、同期状态和独立入口测量的结论继续有效，不把这些历史资料作为工程接入开发的前提。

## 全机状态采集与局部模型范围

四极铁采集按 machine profile 的 `kind=quad` 动态推导，包含不在 ALL_MAIN 主线上的设备；不受当前参考元件和目标屏限制。当前预测复用模型线路 `ALL_MAIN`，仅截取以下局部段，不新增格架别名或硬编码设备列表：

```text
QT01 → BPM20 → XC21 → YC21 → DMTB → QT02 → DMTC1
     → PRF06 → DMTC2 → PRF07 → DMTC3 → PRF08 → DMTC4
     → QT03 → DMTD → QT04 → BPM21 → XC22 → YC22
     → DMTE → ICT02 → DMTE1 → PRF09
```

现有 [prepare_model()](../../src/shared/digital_shadow/pipeline.py) 接受该段和四屏，推导出的必要模型字段为 QT01、QT02、QT03、QT04 的 K1。该段没有 RF 或弯转；[ElegantModelBackend.get_map()](../../src/shared/machine_profile/model_backend.py) 按参考元件至目标屏截取局部线路，不要求计算 ALL_MAIN 的整个加速段。

| 纳入的通道 | 数量 | 本阶段用途 |
| --- | ---: | --- |
| QL01～QL27、QT01～QT36、QE01～QE03 / K1_total | 66 | 保存全机归一化回读；当前模型仅使用 QT01～QT04 四项 |
| 同上 66 块 / K1 | 66 | 保存设置值，供追溯，不替代缺失回读 |
| QDU01、QDU02 / current_set、current_readback | 4 | 原样保存电流，不假定已是归一化强度 |
| PRF06～PRF09 / sigx、sigy | 8 | 原始 H:SIZE、V:SIZE 观测，保留定义和质量 |
| BPM20、BPM21 / x、y、sum | 6 | 记录轨道和信号背景，不新增轨道预测 |
| XC21、YC21、XC22、YC22 / current_set、current_readback | 8 | 记录校正铁状态，不新增电流至 KICK 的模型转换 |
| ICT02 / charge | 1 | 记录电荷背景，不新增传输率或损失预测 |
| 合计 | **159** | 全机归档完整性、局部模型可用性和诊断质量分别判断 |

159 项均在现有 [real profile](../../configs/machines/half/control_backends/real.json) 中找到不同的 PV 映射，尚未核对现场连接性。准备清单保留按 `kind` 选择全机四极铁的规则；完整生成列表在 `all_quad_scope_review.json`，不在 app workflow 中复制一份设备名单。它们是审阅记录，不是可直接执行的 CLI 配置。

66 块四极铁具备 K1/K1_total 映射。QDU01/02 缺少 K1_total，且其 K1 与 current_set 指向同一个 `current:ao` PV，因此改用已有的 current_set/current_readback 保存原始状态，不重复采样别名、不把电流标作 1/m²。今后若要将 QDU 纳入强度驱动模型，还需明确归一化回读或电流至强度转换来源。

模型格架另含 QHL1～QHL4 四个 QUAD 定义，但它们不在 ALL_MAIN 主线，也未出现在机器设备 profile 中，当前没有可核对的 real PV 映射。其实际设备身份单列待确认，不自动当作额外实机设备，也不根据相似数值猜测与 QT 元件的别名关系。

模型当前预测仍是无耦合、无色散的横向线性 RMS 束宽。记录 BPM、校正铁和 ICT 状态不代表这些观测已经接入相应物理预测。

## 四屏定义与现有缺口

| 屏幕 | real H/V SIZE 映射 | 单位及定义 | 当前 RMS 比较 |
| --- | --- | --- | --- |
| PRF06 | 已存在 | 用户确认 mm、高斯拟合 σ | 定义不同，不给精度残差 |
| PRF07 | 已存在 | 待逐项确认，保留为 unknown | 不给精度残差 |
| PRF08 | 已存在 | 用户确认 mm、高斯拟合 σ | 定义不同，不给精度残差 |
| PRF09 | 已存在 | 待逐项确认，保留为 unknown | 不给精度残差 |

PRF07/09 的语义尚未确认不妨碍把它们纳入标量采集清单。采集时保存部署端原始 EGU、源时间、接收时间、报警和失败原因；不能由 H:SIZE 名称推断 mm 或 RMS。模型 RMS 与原始观测可按屏对应并排展示，Gaussian σ 或 unknown 的状态明确为不可作精度比较，不伪造残差。

当前 [vm profile](../../configs/machines/half/control_backends/vm.json) 缺少 **PRF09/sigx、PRF09/sigy** 映射，虽然其 VM 图像映射存在。现有 `run-vm` 的四屏标量采集因此尚不成立；本轮没有新增 PV 名、重生成 IOC DB 或重启 VM。若后续补齐该覆盖，须单独说明 PV、IOC 生成和发布同步影响并验证，不能把新增 real 清单当作已经完成 VM 接入。

四屏当前都没有在 profile 中提供可供本任务核对的插入/退出状态通道。一次读取四屏 SIZE 只能得到四项现有观测，不能证明来自同一束团或四屏均有效。截束屏不能默认同时测量下游束流；不操作屏幕，也不把顺序测量伪装成原子快照。

## 扩大后的实现边界

首轮实现拟覆盖全部 159 项标量，读取过程与模型计算分开。复用 `CapturePoint`、`sample_capture_points()` 和 `MachineStateSnapshot`，从全机 profile 按 kind 推导四极铁，从当前局部段推导附加观测，不再构建一套设备命名系统。

1. **real 一次只读采集。** 在独立的拟新增 `src/shared/digital_shadow/real_capture.py` 中组织采样，归档逐通道质量与采集窗口；新增显式 CLI/GUI 入口。保持 `run-vm` 的 real 拒绝逻辑，启动或环境默认值不自动连接 real。
2. **冻结工作点后预测。** 从全机快照中提取当前模型需要的四项有效 K1_total，派生到模型 K1；任一必要回读缺失、单位非等价或质量不符时不执行该模型。其他束线或 QDU 电流状态不完整时，保留全机归档缺项，不将其误判为当前局部模型不可用。`m^-2` 与 `1/m^2` 只在派生路径显式等价处理，原快照保留原始单位。
3. **保存诊断语义。** 将 real 原始宽度定义保存为独立的采集来源记录；不把 Gaussian/unknown 改标为现有 `ShadowRequest` 要求的 RMS。对 `comparison.py` 和 session 做必要的小改动，使模型 RMS 与不兼容观测仍可对应展示，同时保留空残差和原因。
4. **入口和能量有来源。** 实机独立入口仍缺失，工程预测可消费明确标为 assumed 的输入及来源；当前准备清单的真实数值仍为 null，不自动填入 2200 MeV 或 VM 粒子参数。不新增入口拟合或自动校准。
5. **先离线验收。** 使用注入的模拟 PV 读取器覆盖正常采集、超时、断连、坏报警、缺失必要回读、单位错误及旧观测。检查无写入、无隐式 real 连接、快照不变、四屏不兼容定义不产生精度残差，然后再安排明确授权的实机只读运行。

原始图像归档、RMS 重算、多屏重建和留出 PRF09 的物理验证仍是后续阶段。它们可复用现有图像和重建模块，但不是这次扩大标量工程接入范围的开发前置条件。RF、弯转、六维模型链、持续 Twin Session 和自动调束不在此阶段内。

## 本轮离线检查

首次局部检查实际调用 `prepare_model()`，确认四个必要四极铁字段，并核对 31 项局部状态；GUI 对含四屏、八个 RMS 目标的合成请求导入后完整参数往返一致，没有启动任务。随后按用户要求独立盘点全机 68 块四极铁，生成 136 项四极铁原生状态和 23 项局部观测，共 159 个不同 PV；模型所需四项回读均属于该全机采集集合。

该 GUI 请求仅是结构和导入夹具，Source 明确标为 SYNTHETIC，不含真实入口依据。PRF09 能通过现有导入保留逻辑显示，不表示它已经有 VM 标量映射，也不能把该请求与旧的 QT02 单四极快照混用。

局部结构和 GUI 检查产物位于 [runs/digital_shadow_expanded_scope/20261118](../../runs/digital_shadow_expanded_scope/20261118/)，受既有忽略规则排除：

- `scope_review.json`：实际路径、31 项 PV、必要字段和已知缺口。
- `inspect_scope.py`：实际执行的检查脚本，禁止 PV 访问。
- `scope_synthetic_request.json`：仅供结构与 GUI 导入检查的合成夹具。
- `SHA256SUMS`：产物校验清单。

最新的全四极铁盘点位于 [runs/digital_shadow_all_quadrupoles/20261118](../../runs/digital_shadow_all_quadrupoles/20261118/)，其中 `all_quad_scope_review.json` 保存全量 159 项、QDU 语义限制和 QHL 身份缺口，`inspect_all_quads.py` 为实际执行脚本，`SHA256SUMS` 保存校验清单。

实际 PV 访问为 0，ELEGANT 运行次数为 0。本次没有重复运行已经完成的物理一致性验收，也没有宣称 real 采集功能已实现。
