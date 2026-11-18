# HALF 最初目标与当前能力差距

核对日期：2026-11-18。代码基线为专用工作区 `half_linac-dt`、分支 `feature/digital-shadow-poc` 的 `ba85ab3`。本轮只核对源码及既有验收记录，更新文档，不执行物理计算、PV 访问或新功能实现。

最初的架构审查与目标设计报告已经交付；后续实现了单次快照驱动的局部 ELEGANT 数字影子及 VM/离线验收。最初建议的“一次真实快照 → 模型预测 → 同工作点实测比较”仍待现场验收，完整多引擎虚拟加速器和数字孪生尚未完成。工程可运行、VM 验收和真实机器物理验证分别计状态。

## 当前决定

**三屏重建记录为后续可选功能，暂不实现，也不作为后续架构工作的前置条件。** 用户于 2026-11-18 明确要求延期。

候选仍保留 PRF06/07/08 图像 RMS 重建 PRF06 exit 横向二阶矩、PRF09 留出验证。既有 `beam_matrix.reconstruct_transverse_beam_matrices()`、`emit_measure.multi_screen.reconstruct_multi_screen_measurement()` 可复用；这不是新增算法缺口，也不是当前六维接口的替代品。未来是否实施、数据与验收条件另行确定；不重新索要已确认不存在的历史原图。

当前物理链顺序按最初任务：**IMPACT-T / NN → L01B 出口六维束流接口 → ELEGANT，未来支持 Cheetah**。旧交接说明里的 Cheetah 优先路线保留为历史背景，不覆盖当前顺序。本轮不要求实现 IMPACT-T、NN 或 Cheetah。

## 按最初五项任务核对

| 最初任务 | 已完成 | 剩余内容与当前判断 |
| --- | --- | --- |
| 一 现有代码架构 | [初始审查](ARCHITECTURE_REVIEW.md)已有实际路径、职责、依赖、PV/VM/模型调用链、副作用、快照和缓存分析 | 报告基于初始基线，新增数字影子实现的当前差异以本文和交付记录为准；主体并不存在统一 RealBackend/VirtualBackend/TaskEngine 类，不应补造这些抽象 |
| 二 真实—虚拟协同评估 | 控制源与模型配置独立，现有应用已有局部 real 状态驱动模型；新 session 可从冻结快照派生输入并独立计算 | real 采集器已离线验证，现场连接、实际 EGU/时间与真实诊断对应未验收；无法仅凭 PV 构造完整束流输入 |
| 三 最小侵入目标架构 | 已复用两类快照、映射、模型后端；一次会话、诊断比较、归档和 GUI 已实现 | 通用多引擎请求/结果与持续会话契约尚未实现；不需要先新建通用 MachineStateManager 或重写调束状态机 |
| 四 多引擎接口预留 | 原报告已完成扩展适用性评估及设计建议；模型配置有 engine 字段，VM 有 ELEGANT SDDS 束流源基础 | 评估交付已完成，接口实现属后续：L01B 唯一边界、六维交换、引擎适配与串联、依赖重算和计算缓存仍缺；设计建议不等于可调用接口 |
| 五 审查交付与实施计划 | 当前/目标架构图、调用链、差距、分阶段计划、风险和首阶段文件建议已交付；后续实现与验收另有记录 | 第一阶段工程链完成并经 VM 验收，实机目标尚未现场验收；后续多引擎契约需单独评审，不从本次回查直接进入实现 |

## 已有模块和实际边界

| 能力 | 当前代码证据 | 判断 |
| --- | --- | --- |
| 设备身份与双后端 PV 映射 | `MachineProfile`、`ElementConfig.id`、`resolver.resolve_channel()` | 已有，不重复开发 |
| 原始与模型快照 | `machine_state.MachineStateSnapshot`、`model_snapshot.ModelSnapshot`、`build_model_snapshot_from_machine_state()` | 已有；数字影子从原始快照派生模型输入，不补读 PV |
| 单次实机源采集 | `real_capture.build_real_capture_plan()`、`capture_real_once()`、`normalized_real_input()` | 已实现并注入读取器验证，原生 real 连接仍需显式操作，未做现场验收 |
| VM 工作点对应 | `vm_capture.capture_vm_once()`、`verify_vm_evidence()` | 已验证版本、完成状态、发布窗口及采集一致性；不等于实机同束团采集 |
| 一次数字影子会话 | `session.run_once()` | 功能已实现为函数；没有 DigitalShadowSession 类不构成功能缺失 |
| 局部 ELEGANT 模型链 | `pipeline.prepare_model()`、`predict()`、`ElegantModelBackend` | 已有单引擎薄编排；并非多引擎 ModelPipeline 已完成 |
| SIZE 与图像诊断比较 | `comparison.compare_diagnostics()`、`image_comparison.compare_image_diagnostics()` | 已有独立来源、单位、质量和限制；当前有效图像残差限于已核验 VM |
| 图像与背景保存 | `ImageCapture`、`ImageStore`、image_index | 已有无损压缩、去重和容量限制；原图对象去重不是物理计算结果缓存 |
| 异步运行与失败归档 | `ShadowJobController`、worker 进程组、`run_elegant_input(timeout_s)`、failed manifest | 单作业异步、停止和超时已具备；通用多引擎调度尚缺 |
| 追溯与重放 | snapshot/evidence、请求、冻结模型输入、input_hash、文件/引擎/实现依赖哈希、manifest | 已有较完整的单次追溯；没有统一持久计算缓存、校准版本发布和依赖闭包管理 |

当前 `ShadowRequest` 只有动能和两组独立横向二阶矩 `x_moments/y_moments`。`pipeline.SUPPORTED_TYPES` 限定漂移、四极铁、监测/屏幕、标记和校正元件；`predict()` 删除 tracking 输入并拒绝耦合/色散矩阵。它消费模型 6×6 传输矩阵中的横向块，**不能因此认定已经支持六维束流交换或全线预测**。

## 主线尚缺的能力

### 完整状态到物理模型输入

`real_capture` 动态检查 68 块四极铁，其中 66 块有有效 K1/K1_total 映射。当前默认四屏采集为 132 项归一化状态与 8 项束宽；QDU01/02 缺归一化回读，K1 与电流通道重合，不能用电流兜底。当前 QT01→PRF09 局部模型仅消费 QT01～QT04 回读。

`model_snapshot.apply_snapshot_conversion()` 已支持 direct、scale_offset、polynomial，但完整注入器仍缺弯铁/校正铁状态到角度或 kick、RF 幅相、共用功率源耦合、枪高压/发射、螺线管场、时序、参考能量与入口束流来源的完整映射。已有局部标定可以复用，完整需求应按模型物理依赖核对，不能以已采 PV 数量替代覆盖率。

### L01B 唯一边界和六维束流契约

当前配置未给出可直接确认的唯一 L01B 加速管出口。`lattice_ini.lte` 使用重复的 L1/L2/L4 RF 定义；`BL01B` 是 CSBEND，不是该加速管的接口名。需结合物理资料确定位置、occurrence 和 entrance/exit，不能猜测替代。

六维契约尚需明确坐标、单位、参考粒子能量/动量、时间与 RF 相位约定、粒子种类、电荷、权重、损失、六维均值/协方差或粒子分布、边界身份及来源版本。现有 `virtual_machine.beam_source` 支持 bunched_beam/sdds_beam 配置，可作为适配基础，不是公共 BeamState6D 实现。

### 统一模型契约与引擎适配

`build_model_backend()` 目前只构造 ElegantModelBackend，数字影子也显式要求 elegant。尚无可切换 IMPACT-T/NN/Cheetah 的主链适配器、统一 beam 输入输出和分阶段执行契约。

可保留现有 ELEGANT 光学路径，后续明确完整六维追踪适配器的职责，并复用 parser、SDDS 输入和运行器。不能仅改一个类名或新增空基类就认定统一模型链完成。本轮无需安装或实现其他引擎；外部独立研究/安装状态不由“尚未接入本链”推定不存在。

### 依赖重算与计算缓存

现有请求可选择局部段和诊断，其他应用也有局部矩阵复用；尚无按上游引擎、边界束流和参数变化自动失效的通用依赖机制。`session.run_once()` 每次仍调用 predict，没有持久计算结果命中/复用接口。

后续需把阶段输入、束流边界、模型/标定版本、引擎与外部资源、精度和随机种子纳入缓存身份及失效规则。现有 input_hash、独立目录、manifest 和 VM input_version 可复用；外部场图等资源的完整依赖闭包与统一校准版本仍未建立。

### 持续数字影子和 Twin Session

当前完成的是显式触发的一次采集、一次计算和一次比较。持续状态同步、工作点漂移判据、过期结果处理、跨阶段取消、全局作业队列及长期会话生命周期尚未形成统一实现。

这些能力应沿用现有读取器、QProcess/worker 和失败记录逐步增加，不依赖切换启动器的 real/vm 目标，也无需接管虚拟 IOC 状态或现有调束状态机。

### 实机验证与模型校准

原生 real 采集尚未现场验收；独立入口束流、诊断时间/屏幕状态、相机和处理适用性、工作点稳定性与物理误差容差尚未建立。用户已确认的 mm 单位、K1/K1_total 语义继续沿用，不再作为待用户重复确认项。

真实 SIZE 高斯 σ 与模型 RMS 不直接作精度减法；real 图像 RMS 当前也不生成有效残差。后续现场验收需有适用数据和明确操作授权，缺少硬件不妨碍继续接口设计。

通用 ModelCalibration、可辨识参数选择、独立验证集、模型/标定版本发布及回退、模型辅助调束尚未实现。已有能量标定和束流重建算法属于可复用能力，不代表通用在线校准已完成。

## 建议的下一步与验收

优先做**现有 ModelPipeline 输入输出契约与 L01B 六维边界的设计评审**，本次不开始代码实现：

1. 对齐已有 ShadowRequest、ModelSnapshot、session/result/manifest，逐字段列出可复用项与必要增量，避免平行数据体系。
2. 列出 L01B 的位置证据和未定事项，审阅六维坐标及单位；未确定边界不填假位置。
3. 定义 ELEGANT 光学、后续 tracking 与多引擎适配器的能力差异、阶段输入输出和失败语义；拟新增 API 明确标为设计。
4. 明确快照、束流、模型/标定/资源版本如何绑定，以及局部重算和缓存命中/失效的最小验收用例。

验收交付为一份可评审的契约、现有接口对照表、最小变更文件清单及离线验证计划；经审阅后再实施。真实连接、IMPACT-T/NN 开发、三屏重建和调束闭环不作为这次契约评审的隐含任务。

当前实现仍在专用工作区/分支，尚未合并到原工作区。最近 GUI 修正有 121 项定向回归与静态检查，图像比较阶段有 9 项隔离集成记录；这些不代替实机验收，也不是完整仓库所有测试通过的声明。
