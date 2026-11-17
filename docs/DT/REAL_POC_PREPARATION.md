# HALF 数字影子实机离线准备

检查基线为专用 worktree 的 `feature/digital-shadow-poc` 分支 `58cf829`；原工作区历史资料只读。本轮只更新准备文档与清单，没有修改产品代码。

2026-11-17，基于 `feature/digital-shadow-poc` 的 `58cf829`，完成现有资料、真实 PV 映射、模型段落和保存快照的离线检查。建议首个候选为 **QT01 entrance → PRF08**，使用已有 `EMIT_QT01_PRF08`，独立验证目标先限定 PRF08。该选择依据现有入口拟合资料和支持的模型路径，不固定沿用 VM 示例中的 QT02。

当前状态为 **输入未齐备，尚不能报告真实工作点重放或实机物理验收通过**。可检索资料中没有满足数字影子契约的 real MachineStateSnapshot；历史束宽全部采用 Gaussian fit，真实 SIZE PV 的 RMS 定义与单位仍待核实。本轮仅更新准备记录，未连接 real PV、运行 ELEGANT、修改采集/调束逻辑或写入机制。

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

### 1.1 可复用的入口资料

原工作区 `runs/0929/scan_20260929_171835_QT01_PRF07/metadata.json`，以及同日 `172537`、`172954`、`173441` 四个 QT01/PRF07 扫描，在 `fit_summary.leastSquares` 中两平面均标为 valid。现有 [matching_import.import_measurement()](../../src/apps/emit_measure/matching_import.py) 已能导入到 QT01 entrance，并转换几何发射度至 SI。无需另写一套历史拟合单位转换。

这些记录只是入口候选：束宽方法为 Gaussian fit，部分 Twiss 为显示精度值，扫描中四极铁快照来源为 K1 设置值，未提供同期 K1_total 的原始元数据。valid 表示现有拟合算法给出的状态，不能代替 RMS 定义、工作点稳定性、统计不确定度和上游状态的确认。

若使用 PRF07 扫描得到入口，必须把 `PRF07/sigx`、`PRF07/sigy` 列入 `fitted_diagnostics`，不再用它们作为独立验收目标。PRF08 需要独立、同期且未参与入口拟合的数据；不能直接把另一个时刻的多屏记录拼接成同一次机器快照。PRF06 可作为后续额外验证屏，但首个请求先只比较一个实际具有新鲜诊断的 PRF08。

若首个 real PoC 需要重新测量入口，应由现有经过授权的发射度流程完成；数字影子只消费经确认的结果。本轮不执行扫描、磁铁调节、屏幕插拔或改变采集流程。

### 1.2 多屏资料的边界

九个多屏档案的有效样本时间跨度约 231～412 秒，保存的是 `BeamSizeSample.timestamp_s`；现有 [MultiScreenWorkspace._accept_sample()](../../src/apps/emit_measure/multi_screen_workspace.py) 用本机接受样本时刻赋值，不是 EPICS 源时间戳。这些档案也缺少必要 K1_total、PV 报警状态和逐通道接收时间。

三屏重建全部使用 PRF06/07/08 时，再比较这三屏属于拟合回代。四屏重建若使用了 PRF09，也不能直接把 PRF09 作为独立验证。后续可用已有重建器做留出验证，但需明确拟合屏、验证屏、同期状态和输入质量，不能把原档案的拟合残差当作独立预测精度。

`quality.pv_sigx_mm/pv_sigy_mm` 是界面保存的交叉检查值。[MultiScreenWorkspace._read_optional_size_channel()](../../src/apps/emit_measure/multi_screen_workspace.py) 仅取数值，未读取 EGU、源时间与报警；字段名中的 mm 不构成部署端单位证明。

### 1.3 六维文件的来源和能量

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
| QT01 entrance → PRF08 | 支持；模型需 QT01、QT02 的 K1 | 有双平面有效的 PRF07 扫描入口候选，可留出 PRF08；优先准备 |
| QT02 entrance → PRF08 | 支持；仅需 QT02 的 K1 | VM 已验证，但找到的 real QT02 扫描至少一个平面 non_physical，暂不作为首选入口来源 |
| QL09 entrance → PRF03 | 支持；需 QL09、QL10 | 可复用现有示例结构；本次未找到合格 real 双平面入口与独立验证组合 |
| QL13 → PRF04 | 当前 `EMIT_QL13_PRF04` 不存在 | 历史 real 数据较多，但不能将旧模型线路名直接用于当前格架 |
| QL27 → PRF06 | 当前 PoC 拒绝 RFCW | 涉及 RF，加速段超出已验证的局部线性范围 |
| PRF06 exit → PRF09 | 支持；需 QT03、QT04 | 可作为后续多屏留出方案；现有档案欠缺必要机器状态，不能直接作本轮独立输入 |

首选路径实际展开为：

```text
QT01 → BPM20 → XC21 → YC21 → DMTB → QT02
     → DMTC1 → PRF06 → DMTC2 → PRF07 → DMTC3 → PRF08
```

检查复用 [prepare_model()](../../src/shared/digital_shadow/pipeline.py)，没有执行预测或启动 ELEGANT。当前范围为无耦合、无色散的横向线性二阶矩，不能由此认证轨道、空间电荷、非线性、RF、损失或图像测宽精度。

## 3 首个工作点的采集清单

下表 PV 来自当前 [real profile](../../configs/machines/half/control_backends/real.json)。映射存在不表示已连接或现场确认。

| 逻辑通道 | 真实 PV | 用途 |
| --- | --- | --- |
| QT01/K1 | `IN:MG:L002:QUAD:QT01:K1` | 保存设置值追溯 |
| QT01/K1_total | `IN:MG:L002:QUAD:QT01:K1:TOTAL` | 输入模型 QT01.K1 |
| QT02/K1 | `IN:MG:L002:QUAD:QT02:K1` | 保存设置值追溯 |
| QT02/K1_total | `IN:MG:L002:QUAD:QT02:K1:TOTAL` | 输入模型 QT02.K1 |
| PRF08/sigx | `IN:BD:PRF08:H:SIZE` | 独立水平 RMS，单位待确认 |
| PRF08/sigy | `IN:BD:PRF08:V:SIZE` | 独立垂直 RMS，单位待确认 |

K1 是设置、K1_total 是归一化强度回读、单位 `1/m^2` 的物理含义已经用户确认，继续沿用；不再用设置值替代缺失回读。找到的历史扫描模型字段共 755 条，来源 PV 均以 `:K1` 结束，没有 `:K1:TOTAL`，因此不满足这一输入要求。

每个实际采集通道需保留源 PV、原始 EGU、标量类型/count、源时间、带时区的 received_at、alarm status/severity、质量和失败原因；MachineStateSnapshot 还需正确的 machine/backend、profile signature、采集起止和一致性说明。模型只使用该次快照，不在计算中补读 PV。

PRF08 的 profile 图像标定为 1440×1080、0.0217 mm/pixel、flip_y=true，默认 ROI 为 x=250、y=40、900×1000。这些是现有配置值，需要确认对当时图像适用。它们不能证明 `H:SIZE/V:SIZE` 是 mm、总体 RMS 或高斯 σ。

本地 `references/control_system/` 的 2026-05-15 束测 PV 列表只描述水平/垂直“尺寸”和图像通道，没有给出 SIZE 的 RMS/FWHM 定义、单位或时间语义；位置和像素分辨率资料中 PRF08 的 0.0217 与当前配置一致。已查阅这些来源，仍不足以完成诊断定义确认，原始 Office 文件保持本地且不入库。

首个目标采用具有新鲜有效值的单屏，需确认屏幕状态、是否存在截断/饱和，以及其他屏是否影响传输。不要默认多块截束屏能同时给出同一时刻束流。若只能顺序测量，需独立的工作点稳定证据；原有秒级采集窗口不应为容纳历史多屏记录而直接放宽到数分钟。

## 4 已核实的接口约束和缺项

| 项目 | 当前状态 | 下一步需要的事实或小改动 |
| --- | --- | --- |
| 模型段与所需强度 | 已确认 QT01/02 | 复用路径推导，不另建设备列表体系 |
| 原始 real 快照 | 缺失 | 提供合格保存快照；或在单独授权后读取一次上述通道 |
| 入口 RMS 协方差 | 有 Gaussian 拟合候选，未核实 | 确认可用于总体 RMS 的独立入口资料、参考面、单位与不确定度 |
| 入口能量 | 历史记录 2200 MeV，当前未确认 | 给出来源、动能约定与有效工作点，不自动沿用默认值 |
| real SIZE 诊断 | PV 映射存在，物理定义未确认 | 确认总体 RMS/高斯 σ/FWHM、单位、ROI、背景、有效性与更新语义 |
| 训练/拟合与验证独立性 | 可明确划分 | 若入口取自 PRF07，验证用未参与拟合的 PRF08；填 fitted_diagnostics |
| 工作点稳定性 | 历史档案不足 | 入口测量到独立诊断期间的上游状态、能量及强度一致依据 |
| 单位别名 | 当前会拒绝 | 在派生映射中显式支持等价单位，并保留原始 EGU |
| real 一次采集入口 | 尚未实现 | 在审阅后增加显式只读入口，保持 vm 专用采集拒绝 real |

本地 IOC 参考 `quad.template` 使用 `m^-2`；与已确认的 `1/m^2` 物理等价，但 [build_model_snapshot_from_machine_state()](../../src/shared/machine_profile/model_snapshot.py) 当前按配置字符串严格核对。离线构造明确标为 synthetic 的测试夹具已复现：`Unit mismatch ... 'm^-2', expected '1/m^2'`。这是将来接入前需要处理的兼容性，不是当前部署 IOC 已确定返回该单位的声明。不能通过改写原始快照、删去单位检查或修改全局 profile 来隐瞒差异。

参考模板将 K1:TOTAL 写为多个归一化强度分量求和的 calc 记录。该本地参考不证明现场实现版本，也不能单凭它判定磁铁电流或束流已稳定；数字影子的 K1_total 选择仍按用户确认的物理语义处理。

现有 CLI 仅提供 `example/replay/run-vm`，真实采集授权仍未提供。本轮没有创建 `run-real`，也没有让 `run-vm` 接受 real 参数。

## 5 后续实现的最小范围

先补齐输入确认表，再审阅以下小改动：

1. 单次 real 采集：新增独立 `src/shared/digital_shadow/real_capture.py`，复用 CapturePoint、sample_capture_points 和 MachineStateSnapshot；仅按已选择段落采样，保存原始元数据，不设置、恢复或重复采样。
2. 单位处理：只在数字影子的快照派生路径加入明确的等价单位处理，保留原始单位及转换来源。覆盖 `m^-2 ↔ 1/m^2`，不接受 A 等非等价单位，不改变其他应用的 live K1 映射。
3. CLI：给 `scripts/run_digital_shadow.py` 增加显式 real 只读子命令，禁止从环境默认值自动切换；实际调用另行明确授权。现有 run-vm 的连接前隔离校验保持不变。
4. 入口资料适配：准备工具可复用现有 `import_measurement()` 的单位换算；共享数字影子运行层不反向依赖 GUI 应用。先确认来源与 RMS 定义，再生成可执行 ShadowRequest。
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
