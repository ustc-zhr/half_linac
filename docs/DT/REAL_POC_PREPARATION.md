# HALF 数字影子实机离线准备

历史资料检查基线为专用 worktree 的 `feature/digital-shadow-poc` 分支 `58cf829`；原工作区历史资料只读。2026-11-18 按用户要求，已将首轮 real 工程接入扩大为 **QT01 entrance → PRF09，四块四极铁、四屏及沿途标量状态共 31 项**，并完成当前模型与 PV 映射的离线检查。当前接入范围、缺口和实施计划以[扩大范围方案](REAL_CAPTURE_SCOPE_20261118.md)及准备清单为准，没有修改产品代码或连接实机。

2026-11-17，用户确认 PRF06/08 SIZE PV 为 **mm 单位的高斯拟合 σ**，无独立 QT01 入口束流测量资料，也没有 PRF06/07/08/09 原始图像及同期机器状态。因此，QT01 入口比较和 PRF06 出口重建后留出 PRF09 比较均暂缓实机执行；不再将补交这些历史资料作为当前工作的前提。

当前结论为 **现有历史资料不足以完成实机物理验收**，不是后端不可用。已确认的高斯 σ 不能直接充当总体 RMS；缺少入口、原图与同期状态时，不能靠换参考点、拼接其他日期数据或使用 VM 粒子分布补成真实机器验证。离线开发仍可继续，当前优先实现扩大的 real 标量工程接入，原图归档和多屏重建作为后续物理验收路径，不要求用户补交不存在的历史资料。

[输入准备表](REAL_POC_INPUTS.json) 是供审阅的清单，**不是 CLI 请求文件或已经实现的配置接口**。其中未确认的数值保持 null，不能直接交给 `replay`。

## 1 资料检查结果

离线搜索原工作区 `runs/`、`src/apps/machine_snapshot/runtime/`、`src/apps/emit_measure/runtime/` 和 `runtime/` 内的 JSON，并查看现有 profile、模型格架及本地物理参考。以下数量为文件数；内容 SHA256 去重后单独列出，不能将 latest 副本或重新保存的记录解释为独立实验。

| 资料 | 本地发现 | 对首个 real PoC 的作用与限制 |
| --- | --- | --- |
| 标记 real 的四极扫描 `emit_scan_v2` | 196 文件，106 个不同文件内容 | 有拟合和模型参数来源；全部 `local_fit`、`Gaussian fit`，不是原始机器快照 |
| 标记 real 的多屏 `emit_multi_screen_v1` | 9 文件，8 个不同文件内容 | 有屏幕尺寸、时间和部分有效协方差；全部 Gaussian fit，均无 model_snapshot |
| 原始 MachineStateSnapshot | 2 文件，均为 vm | 不含 K1_total 或 sigx/sigy，不能当作 real 输入 |
| ELEGANT 六维粒子文件 | 5 个已检查 | 可用于模拟输入研究；没有与所选 real 工作点建立绑定 |
| PARMELA 和 IMPACT-T 资料 | 转换脚本、输入、场图及历史说明 | 可追溯模型来源；不能直接等同于同期 QT01 入口实测束流 |

完整路径、文件 SHA256、拟合状态和模型结构检查保存在本地 `runs/digital_shadow_readiness/20261117/input_inventory.json`，不将原始测量数据或本地参考附件加入源码。

档案统计覆盖原工作区 `runs/`、`src/apps/machine_snapshot/runtime/`、`src/apps/emit_measure/runtime/`、`runtime/` 中的 JSON 文件。文件数量和不同内容数量均不是独立物理测量次数；latest 副本、重算和重复归档需要区分。

### 1.1 历史入口拟合资料

原工作区 `runs/0929/scan_20260929_171835_QT01_PRF07/metadata.json`，以及同日 `172537`、`172954`、`173441` 四个 QT01/PRF07 扫描，在 `fit_summary.leastSquares` 中两平面均标为 valid。现有 [matching_import.import_measurement()](../../src/apps/emit_measure/matching_import.py) 已能导入到 QT01 entrance，并转换几何发射度至 SI。无需另写一套历史拟合单位转换。

这些记录是高斯测宽反演资料，不是用户已确认存在的独立 QT01 入口测量。部分 Twiss 为显示精度值，扫描中四极铁快照来源为 K1 设置值，未提供同期 K1_total 元数据。valid 表示现有拟合算法给出的状态，不能代替总体 RMS、工作点稳定性和统计不确定度的确认。本轮不将其提升为首个实机 PoC 的已知入口。

今后若重新评估 PRF07 扫描入口，仍需把 `PRF07/sigx`、`PRF07/sigy` 列入 `fitted_diagnostics`，不能再作为独立验证。保留的未来多屏方案以 PRF06/07/08 的六个 RMS 尺寸为拟合来源，只验证未参与重建的 PRF09；用户已确认该方案所需的历史输入不存在，当前不执行重建。

若首个 real PoC 需要重新测量入口，应由现有经过授权的发射度流程完成；数字影子只消费经确认的结果。本轮不执行扫描、磁铁调节、屏幕插拔或改变采集流程。

### 1.2 多屏资料的边界

九个多屏档案的有效样本时间跨度约 231～412 秒，保存的是 `BeamSizeSample.timestamp_s`；现有 [MultiScreenWorkspace._accept_sample()](../../src/apps/emit_measure/multi_screen_workspace.py) 用本机接受样本时刻赋值，不是 EPICS 源时间戳。这些档案也缺少必要 K1_total、PV 报警状态和逐通道接收时间。

三屏重建全部使用 PRF06/07/08 时，再比较这三屏属于拟合回代。四屏重建若使用了 PRF09，也不能直接把 PRF09 作为独立验证。后续可用已有重建器做留出验证，但需明确拟合屏、验证屏、同期状态和输入质量，不能把原档案的拟合残差当作独立预测精度。

`quality.pv_sigx_mm/pv_sigy_mm` 是界面保存的交叉检查值。[MultiScreenWorkspace._read_optional_size_channel()](../../src/apps/emit_measure/multi_screen_workspace.py) 仅取数值，未读取 EGU、源时间与报警；字段名中的 mm 不构成部署端单位证明。

用户现已确认 PRF06/08 的单位和高斯 σ 定义；PRF07/09 的 SIZE 定义尚未逐项确认。上述旧读取方式仍不能提供源时间或质量证明。

### 1.3 原始图像和可复用的处理

对本次候选档案所在 `runs/0929/`、`runs/1003/` 及 `src/apps/emit_measure/runtime/` 的检查，没有找到可重算的原始束流帧；仅发现一张界面截图及保存的宽度/质量记录。现有扫描导出 `scanResults.txt/metadata.json`，多屏档案保存尺寸和处理设置，不保存本组原始粒子或原始束流图像。背景文件也不能替代束流帧。

用户进一步确认没有 PRF06/07/08/09 原始图像及同期机器状态，这一项现标为“已确认不存在”，不再标为“等待用户提供”。后续如采用多屏方案，需要在未来测量时重新归档完整数据。

现有 [fit_beam_image()](../../src/shared/beam_diagnostics/image_fit.py) 支持 Gaussian fit、RMS moments 和 Robust RMS moments，GUI 已有 Projection RMS 选择。重算时应明确背景、ROI、阈值、裁剪、饱和及方法版本，评估相机展宽与有限窗口偏差；不能把 Robust RMS 或 ROI 内 RMS 自动标为全分布 RMS。Gaussian σ 与 RMS 仅在满足相应分布和观测条件时一致，不能由现有标量单独恢复总体二阶矩。现有扫描元数据校验也明确拒绝在没有原图时切换宽度方法。

### 1.4 六维文件的来源和能量

| 文件 | 最后一页粒子数 | 文件中的位置线索 | 由 p 推导的平均动能 MeV |
| --- | ---: | --- | ---: |
| `injector.sdds` | 120985 | `PreviousElementName=CCC` | 113.6034 |
| `HALF_pre_1A_main.dat` | 7037 | 未声明唯一物理参考面 | 113.7711 |
| `HALF_pre_1A_all.dat` | 7166 | 未声明唯一物理参考面 | 113.4608 |
| `HALFpre_1A_main_n30000.dat` | 21669 | 未声明唯一物理参考面 | 113.6468 |
| `pre.bun` | 8370 | `PreviousElementName=DMTB` | 2196.1254 |

文件位于 `src/virtual_machine/half_elegant/elegant/`。按 SDDS 命名列读取 x/xp/y/yp/p，协方差按质心和 `ddof=0` 计算；文件名中的 n30000 不用于推断实际粒子数。上述能量使用 `mc²(√(1+p²)−1)`，不是总能量。

原工作区的 `references/preinjector_parmela/.../readdumpTAPE2.m` 明确执行相位窗口、远端粒子筛选及 cm→m、mrad→rad 转换，并生成同名 HALFpre 文件；不能只凭文件名断言目前的文件逐字来源相同。IMPACT-T `partcl.data` 是输入资料，不能当作已计算的 L01B 出口分布。

SDDS 的 CCC/DMTB 是格架标识，尚未绑定为已确认的 L01B 出口或当前 real 入口参考面。粒子文件、历史扫描的 2200 MeV 和 UI 默认能量不能互相替代；ShadowRequest 的 `energy_mev` 当前按动能解释。真实工作点需另外给出能量来源、动能/总能量约定和适用时间。

## 2 首个候选段与替代选择

| 候选 | 当前结构检查 | 选择依据 |
| --- | --- | --- |
| QT01 entrance → PRF09 | 支持；需 QT01～QT04 的 K1 | 当前首轮工程接入范围；四屏原始标量与辅助状态均归档，暂不作实机精度验收 |
| PRF06 exit → PRF09 | 支持；需 QT03、QT04 的 K1 | 未来测量候选；历史原图和同期状态已确认不存在，当前不执行 |
| QT01 entrance → PRF08 | 支持；模型需 QT01、QT02 的 K1 | 无独立 QT01 入口资料；历史高斯拟合不直接代替，暂缓 |
| QT02 entrance → PRF08 | 支持；仅需 QT02 的 K1 | VM 已验证，但找到的 real QT02 扫描至少一个平面 non_physical，暂不作为首选入口来源 |
| QL09 entrance → PRF03 | 支持；需 QL09、QL10 | 可复用现有示例结构；本次未找到合格 real 双平面入口与独立验证组合 |
| QL13 → PRF04 | 当前 `EMIT_QL13_PRF04` 不存在 | 历史 real 数据较多，但不能将旧模型线路名直接用于当前格架 |
| QL27 → PRF06 | 当前 PoC 拒绝 RFCW | 涉及 RF，加速段超出已验证的局部线性范围 |

保留供未来测量审阅的路径实际展开为：

```text
PRF06 → DMTC2 → PRF07 → DMTC3 → PRF08 → DMTC4
      → QT03 → DMTD → QT04 → BPM21 → XC22 → YC22
      → DMTE → ICT02 → DMTE1 → PRF09
```

使用已有 `ALL_MAIN` 路径，参考面为 PRF06 exit。设计格架中 PRF06/07/08 之间只有两个 5.5 m 漂移；已有 `diagnose_measurement_matrix()` 对距离 0、5.5、11 m 的测量矩阵返回 rank=3、condition_number≈12.34。三屏原则上可确定每个平面三个二阶矩，但自由度为零，不能用其自身拟合残差验收；需重复样本、不确定度、正定性和留出屏验证。这只是结构可辨识检查，没有使用假造的实测束宽。

检查复用 [prepare_model()](../../src/shared/digital_shadow/pipeline.py)，没有执行预测或启动 ELEGANT。当前范围为无耦合、无色散的横向线性二阶矩，不能由此认证轨道、空间电荷、非线性、RF、损失或图像测宽精度。

## 3 后续图像重建测量的数据清单

下表为 PRF06 出口重建候选段所需的四极铁状态，来自当前 [real profile](../../configs/machines/half/control_backends/real.json)。当前首轮工程采集另包含 QT01/02、四屏和辅助状态，完整 31 项见[扩大范围方案](REAL_CAPTURE_SCOPE_20261118.md)。映射存在不表示已连接或现场确认。

本节是未来归档设计要求，不是向用户索要现有历史文件，也不构成执行实机操作的授权。

| 逻辑通道 | 真实 PV | 用途 |
| --- | --- | --- |
| QT03/K1 | `IN:MG:L002:QUAD:QT03:K1` | 保存设置值追溯 |
| QT03/K1_total | `IN:MG:L002:QUAD:QT03:K1:TOTAL` | 输入模型 QT03.K1 |
| QT04/K1 | `IN:MG:L002:QUAD:QT04:K1` | 保存设置值追溯 |
| QT04/K1_total | `IN:MG:L002:QUAD:QT04:K1:TOTAL` | 输入模型 QT04.K1 |

另需 PRF06/07/08/09 的原始图像，PV 按现有 profile 为 `IN:BD:<屏幕>:image1:ArrayData`。前三屏用于入口重建，PRF09 只用于独立比较；需保存原始帧、图像源时间、采集时间、像素标定、背景、ROI 和处理版本。图像应作为单独产物保存，不改变既有 MachineStateSnapshot 排除数组的契约。

派生的图像 RMS 必须记录实际 image PV、帧哈希和处理来源，不能冒用原高斯 SIZE PV 的来源身份，或把原始快照里的高斯 σ 改标为 rms。当前 comparison 只识别 profile 的 sigx/sigy 标量 PV，接入图像 RMS 需要小规模扩展诊断来源接口，尚未实现。

K1 是设置、K1_total 是归一化强度回读、单位 `1/m^2` 的物理含义已经用户确认，继续沿用；不再用设置值替代缺失回读。找到的历史扫描模型字段共 755 条，来源 PV 均以 `:K1` 结束，没有 `:K1:TOTAL`，因此不满足这一输入要求。

每个实际采集通道需保留源 PV、原始 EGU、标量类型/count、源时间、带时区的 received_at、alarm status/severity、质量和失败原因；MachineStateSnapshot 还需正确的 machine/backend、profile signature、采集起止和一致性说明。模型只使用该次快照，不在计算中补读 PV。

PRF08 的 profile 图像标定为 1440×1080、0.0217 mm/pixel、flip_y=true，默认 ROI 为 x=250、y=40、900×1000。这些是现有配置值，需要确认对当时图像适用。用户已确认该 SIZE 值是 mm 单位的高斯 σ，图像标定不消除其与总体 RMS 的定义差异。

本地 `references/control_system/` 的 2026-05-15 束测 PV 列表只描述水平/垂直“尺寸”和图像通道；PRF06/08 的单位和定义现以用户确认作为来源，时间语义等仍需核实。原始 Office 文件保持本地且不入库。

首个目标采用具有新鲜有效值的单屏，需确认屏幕状态、是否存在截断/饱和，以及其他屏是否影响传输。不要默认多块截束屏能同时给出同一时刻束流。若只能顺序测量，需独立的工作点稳定证据；原有秒级采集窗口不应为容纳历史多屏记录而直接放宽到数分钟。

## 4 已核实的接口约束和缺项

| 项目 | 当前状态 | 下一步需要的事实或小改动 |
| --- | --- | --- |
| 模型段与所需强度 | 当前工程段需 QT01～QT04；PRF06 出口重建候选需 QT03/04 | 复用路径推导，不另建设备列表体系 |
| 原始 real 快照 | 该组同期状态已确认不存在 | 未来测量时归档，不补造历史快照 |
| 入口 RMS 协方差 | 无独立 QT01 测量，历史多屏重建条件也不具备 | 暂不进行实机精度比较；保留未来重建方案 |
| 入口能量 | 历史记录 2200 MeV，当前未确认 | 给出来源、动能约定与有效工作点，不自动沿用默认值 |
| real SIZE 诊断 | PRF06/08 已确认 mm、高斯 σ | 不再追问这两项；当前 RMS pipeline 不直接减法比较 |
| 独立图像 RMS 诊断 | 处理能力存在，原图已确认不存在 | 先在 VM 验证未来归档与派生来源，不声称已重算实机图像 |
| 训练/拟合与验证独立性 | 可明确划分 | PRF06/07/08 仅用于重建，PRF09 不参与拟合；填 fitted_diagnostics |
| 工作点稳定性 | 该组同期状态已确认不存在 | 未来逐屏采集前后状态并单独判定稳定性 |
| 单位别名 | 当前会拒绝 | 在派生映射中显式支持等价单位，并保留原始 EGU |
| real 一次采集入口 | 尚未实现 | 在审阅后增加显式只读入口，保持 vm 专用采集拒绝 real |

本地 IOC 参考 `quad.template` 使用 `m^-2`；与已确认的 `1/m^2` 物理等价，但 [build_model_snapshot_from_machine_state()](../../src/shared/machine_profile/model_snapshot.py) 当前按配置字符串严格核对。离线构造明确标为 synthetic 的测试夹具已复现：`Unit mismatch ... 'm^-2', expected '1/m^2'`。这是将来接入前需要处理的兼容性，不是当前部署 IOC 已确定返回该单位的声明。不能通过改写原始快照、删去单位检查或修改全局 profile 来隐瞒差异。

参考模板将 K1:TOTAL 写为多个归一化强度分量求和的 calc 记录。该本地参考不证明现场实现版本，也不能单凭它判定磁铁电流或束流已稳定；数字影子的 K1_total 选择仍按用户确认的物理语义处理。

现有 CLI 仅提供 `example/replay/run-vm`，真实采集授权仍未提供。本轮没有创建 `run-real`，也没有让 `run-vm` 接受 real 参数。

## 5 后续实现的最小范围

当前下一步为 **QT01→PRF09 的 real 一次只读标量工程接入**，先离线验收，不依赖已有实机图像。复用现有 MachineStateSnapshot、标量采样和模型 pipeline，纳入 31 个标量；当前采集器和实机 CLI/GUI 入口尚未实现。范围和逐项验收条件见[扩大范围方案](REAL_CAPTURE_SCOPE_20261118.md)。

未来数据包应绑定机器/后端、逐帧源时间与接收时间、每屏采集前后的设置和回读、图像标定、ROI、背景及处理版本和产物哈希。多屏顺序测量需要独立稳定性证据，不合并伪称一次原子快照；数据不完整、状态变化、诊断定义不兼容时不输出物理验收通过。

后续图像归档可先在 VM 验证原始高斯 σ 与派生 RMS 来源分开、拟合屏不参与独立验收，以及坏数据和工作点变化能被识别。原图作为单独旁路产物保存，不重复开发已有测宽或重建算法。既有 VM 工程验收已通过，不重复运行原有用例来代替新增能力的验收。

首轮 real 工程接入拟采用以下小改动：

未来取得原始图像时，入口处理复用 `image_fit.py` 的 RMS 处理、`multi_screen.py` 的 optics/重建和 `beam_matrix.py` 的 rank/正定性检查，保存原始帧与派生 RMS 的来源。当前工程预测可使用来源明确的 assumed 入口，不自动沿用 VM 参数或默认能量。

1. 单次 real 采集：新增独立 `src/shared/digital_shadow/real_capture.py`，复用 CapturePoint、sample_capture_points 和 MachineStateSnapshot；按 QT01→PRF09 推导四块四极铁、四屏和辅助状态共 31 项，保存原始元数据，不设置、恢复或重复采样。
2. 单位处理：只在数字影子的快照派生路径加入明确的等价单位处理，保留原始单位及转换来源。覆盖 `m^-2 ↔ 1/m^2`，不接受 A 等非等价单位，不改变其他应用的 live K1 映射。
3. CLI：给 `scripts/run_digital_shadow.py` 增加显式 real 只读子命令，禁止从环境默认值自动切换；实际调用另行明确授权。现有 run-vm 的连接前隔离校验保持不变。
4. 诊断适配：小改 `digital_shadow/comparison.py` 与 session 的诊断输入，原始 Gaussian SIZE、unknown 定义和模型 RMS 分开保存并按屏展示；不兼容项保留空残差。PRF07/09 定义保持未确认，不标成 RMS。未来图像 RMS 需有独立来源，不能冒用 SIZE PV；当前二阶矩不足以确定非高斯束流的高斯拟合 σ。
5. 定向验证：必要回读缺失、单位非等价、质量/时间/来源不符即失败；诊断不可比较不伪造残差。验证原始快照不变、无 PV 写入和离线重放不连接控制系统。

不新增通用 MachineStateManager、不改现有状态机、图像流程或写 PV 安全机制，不在本阶段引入 IMPACT-T/NN 或持续校准。

## 6 本轮交付与验收结果

本地 `runs/digital_shadow_readiness/20261117/` 保存 `input_inventory.json`、`inspect_inputs.py` 和检查日志。脚本禁止 EPICS 的 PV/caget/caput 等入口；实际 PV 访问 0 次，模型计算 0 次。

完成的离线校验包括六条候选线路的结构检查、五个六维文件的命名列与有限值检查，以及下列既有契约验证：

- 历史扫描通过已有导入器得到 QT01 entrance，保留 Gaussian 来源限制。
- `emit_scan_v2` 直接作为 MachineStateSnapshot 会被拒绝，没有伪造兼容快照。
- 入口拟合所用诊断同时作为验证目标会被拒绝。
- 强度单位 `m^-2` 与当前 `1/m^2` 字符串差异会被拒绝，保留待修正记录。

这些是准备与接口检查，不是新增一轮 ELEGANT 精度测试。已有 VM 验收保持见 [VM 验收记录](VM_ACCEPTANCE_20261117.md)。后续满足输入条件后才生成正式请求与真实快照重放结果；真实机器的一次只读连接仍等待明确授权。

用户确认后，已更新输入表的 mm/gaussian_sigma 定义和“无独立 QT01 入口”状态，并完成新参考段的纯结构 rank 检查。原始 `input_inventory.json` 保留当次盘点结果，不改写历史证据；本次方案调整以本文与准备表为准。

后续用户确认四屏原图及同期状态不存在，已将历史实机物理验收标为无法由现有资料完成。当前没有待用户补交这些历史文件的请求；扩大的 real 工程接入先做离线开发和验收，连接 real PV 和安排新测量仍需另行授权。
