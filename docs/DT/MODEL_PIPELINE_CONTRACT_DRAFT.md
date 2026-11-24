# HALF 模型链输入输出与 L01B 出口六维束流接口评审草案

2026-11-18 · 设计基线 `feature/digital-shadow-poc` @ `d3092d5` · 六维接口首版已通过合成验收

保留单次数字影子的请求、快照、ELEGANT 光学和归档接口。后续通过一个带参考截面、坐标约定和来源的六维束流载荷，串联 **IMPACT-T 或 NN → L01B 出口 → ELEGANT tracking**，Cheetah 后续接入。首版交换契约及独立 ELEGANT 单段验证见第 7 节。正式启用 L01B 前，须确认物理出口到指定版本格架的唯一映射。三屏重建继续延期。

## 1. 现有接口与复用方式

| 接口 | 当前输入和输出 | 拟复用与增量 |
| --- | --- | --- |
| [`ShadowRequest` / `Diagnostic`](../../src/shared/digital_shadow/models.py#L32) | `digital_shadow_request_v1` 包含 `model_backend`、`model_line`、`reference_element/edge`，束流为电子动能 `energy_mev` 及 `x_moments/y_moments`。每平面按位置方差、位置与斜率协方差、斜率方差排列，使用 SI 单位并要求正定。另含束流依据、来源、参与拟合的诊断键、待比较的屏幕 RMS `sigx/sigy` 及采集窗口、诊断时龄和计算超时。 | v1 继续作为数字影子光学请求。阶段输入复用已解析的段、诊断身份和执行限制。六维载荷作为显式输入，旧二维横向矩保持原表示和含义。 |
| [`MachineStateSnapshot` / `SnapshotEntry`](../../src/shared/machine_state.py#L90) | v1 原始证据。包含机器、控制后端、profile 签名、采集时段及一致性。逐项含设备/逻辑通道/PV、值和单位、源时间与接收时间、报警及质量。 | 沿用现有快照与 resolver，按段选取实际消费的输入。束流由单独证据提供。real/VM 的采集和工作点核验仍由各自的入口负责。 |
| [`ModelSnapshot` / `build_model_snapshot_from_machine_state()`](../../src/shared/machine_profile/model_snapshot.py#L59) | 从记录快照提取指定 `(element_id, field_name)`，校验身份、映射、质量、标量及单位，执行映射转换并保留来源。输出 `fields` 与 `lattice_overrides`。现可用 direct、scale_offset、polynomial。 | 复用快照与转换。局部四极铁模型字段 `K1` 使用 real `K1_total` 回读或 VM `K1`，单位 `1/m^2`。以后按实际需要增加 RF 幅相、弯铁角度、枪、线圈场和时序等映射，每项显式声明来源、有效域及标定。 |
| [`prepare_model()` / `predict()`](../../src/shared/digital_shadow/pipeline.py#L18) | 前者从配置格架、模板及请求得到解析状态与段内所需 K1。后者在每次运行目录写入评估输入，按观测位置调用 `get_map(twiss_only=True)`，返回 `(predictions_m, matrices)`。预测为两平面 RMS，矩阵是 ELEGANT 原生 6×6，横向计算取 2×2 块。准入只含漂移、四极铁、标记、诊断和校正元件，另检查耦合与色散。 | 直接保留现行入口及返回值。阶段输出可沿用预测键和值。六维 tracking 以独立能力接入，同名实例解析与原生坐标转换由该适配器承担。 |
| [`run_once()` / result / manifest](../../src/shared/digital_shadow/session.py#L32) | 输入是 context、原始快照、请求、输出根目录及可选 real/VM 证据或图片索引。依次校验、归一化、派生模型输入、预测和比较。返回结果及 `run_dir`。`result.json` 保存 `run_id`、`input_hash`、状态、比较行及传输矩阵。v1 manifest 保存模式、证据、依赖哈希、产物与失败。 | 保留会话、结果及归档读取。新增阶段结果只补模型请求身份、边界束流引用、预测与产物的来历。比较继续消费已绑定的观测定义、单位、质量和时窗。 |
| [`ElegantModelBackend` 与工厂](../../src/shared/machine_profile/model_backend.py#L989)、[`ElegantParser`](../../src/shared/elegant_backend/parser.py#L199)、[`run_elegant_input()`](../../src/shared/elegant_runtime.py#L8) | 工厂当前只构造 ELEGANT。后端提供矩阵、Twiss、能量/色散等应用接口。parser 可解析、序列化格架及控制文件，runner 在指定目录运行 ELEGANT 并报告超时。 | 保留现有应用工厂。tracking 复用 parser、runner 和 SDDS 能力，使用运行私有目录。 |

当前归档的 real SIZE 继续使用已确认的 mm 单位，PRF06/08 标高斯拟合 σ，PRF07/09 标 `unknown`。比较只有在定义兼容时才计算数值残差。图像比较单独使用归档后的 ROI RMS，数值残差需要已核验的 VM 工作点及合格的源图。模型输出可用性和诊断比较状态分别记录。 [标量比较](../../src/shared/digital_shadow/comparison.py#L10) · [图像比较](../../src/shared/digital_shadow/image_comparison.py#L82)

## 2. L01B 出口的位置证据

| 证据 | 对边界的约束 |
| --- | --- |
| HALF 模型后端默认 `ALL_MAIN`，以 `lattice_ini.lte` 为格架源。三条完整束线 `ALL_MAIN`、`ALL_ESA`、`ALL_DUMP` 都含 `… BPM07, XC08, YC08, DL00C, DA1, L1, DA1, DACC, DA1, L1, DA1, COLLI, CE02 …`。 | 用仓库 parser 只读展开，`L1` 为 `RFCW`，在三条线中均出现两次，分别为展开序列第 73、77 个元件（从 1 计）。`L2` 和 `L4` 也重复。当前格架无 `L01B` 定义。两次 `L1` 只是模型实例线索。 [模型配置](../../configs/machines/half/model_backends/simulation.elegant.json#L1) · [RF 定义及主线](../../src/virtual_machine/half_elegant/elegant/lattice_ini.lte#L400) · [ESA 线](../../src/virtual_machine/half_elegant/elegant/lattice_ini.lte#L431) · [DUMP 线](../../src/virtual_machine/half_elegant/elegant/lattice_ini.lte#L447) |
| 元件 `BL01B` 在格架为 `CSBEND`，在 profile 为 `bend`，展开时位于这两次 `L1` 上游。 | 排除该弯铁作为 L01B 加速管出口的映射。 [格架](../../src/virtual_machine/half_elegant/elegant/lattice_ini.lte#L362) · [机器 profile](../../configs/machines/half/machine.json#L1994) |
| `injector.sdds` 的 WATCH 元数据给出 `s=0`、`PreviousElementName=CCC`。`pre.bun` 给出 `s=165.14642 m`、`PreviousElementName=DMTB`。两份 `HALF_pre_1A_*.dat` 从原始 T2 逐粒子溯源到同一历史导出面，见下节。 | WATCH 信息给出 ELEGANT 模型位置。T2 给出历史 PARMELA 导出面。两份证据需通过几何基准对齐。 [已有文件检查](REAL_POC_PREPARATION.md#14-六维文件的来源和能量) |
| 数字影子入口要求元件名在所选线中唯一，后端按 `usedline.index()` 取首个同名实例。 | 未来按展开实例定位，可参考 workbench 的位置枚举。当前 `reference_element="L1"` 无法选中第二次出现。 [准入](../../src/shared/digital_shadow/pipeline.py#L18) · [矩阵切片](../../src/shared/machine_profile/model_backend.py#L615) · [实例枚举](../../src/virtual_machine/workbench_data.py#L21) |

公开设计说明描述了约 10 MeV 的聚束器出口及随后由两根加速管达到约 120 MeV 的预注入器。当前 ELEGANT 模板在两次 `L1` 上游以 `p_central=223.4028` 入射，对应动能约 113.65 MeV。模板所注 114.16 MeV 对应总能量。这提示应优先核对 ELEGANT 入口附近与预注入器出口的对应关系，复核从物理出口到 ELEGANT 输入面的漂移、坐标变换及筛选。第二次 `L1` 出口的早期推测缺少能量和设备顺序支持。 [NSRL 注入器说明](https://www.nsrl.ustc.edu.cn/41792/list.htm) · [IPAC 设计论文](https://proceedings.jacow.org/ipac2023/pdf/WEPA135.pdf) · [ELEGANT 模板](../../src/virtual_machine/half_elegant/elegant/one_ini.ele#L1)

### 历史导出面和粒子文件

兄弟工程 `half_autotune` 的 `docs/machines/half_injector.md` 记录了负责人确认的 accelerator 1/2 与 L01A/L01B 对应关系。同项目 2026-07-21 的 `docs/validation/parmela_elegant_handoff.md`（最近提交 `102c136`）将所给 1 nC PARMELA T2 导出面标为 **L01B 出口下游 10 cm**。保留的 `run10dump.acc` 在第二根加速管的末端耦合器后有 `DRIFT 10`。1 nC 和 0.522 nC 两份 T2 的原始头都给出 `z=929.9 cm`，对应历史 PARMELA 模型坐标中的名义出口约 `z=919.9 cm`。此前交接试验通过实际 SDDS 读入，仍将 T2 导出面与 ELEGANT `s=0` 的几何等价列为待确认。原始资料在兄弟工程的本地 `config/machines/half/references/`。

逐行对照 0.522 nC T2（SHA-256 `988a078acda54b163f1d3d2f903e5bbcf046ba9ef106c1f58f1f9a59d7824a43`）给出了仓库现有输入样本的来源。

| 仓库文件 | 可复现的历史变换 |
| --- | --- |
| `HALF_pre_1A_all.dat` | 同序对应全部 7,166 行。按该粒子群对四个横向坐标量中心化，位置由 cm 转 m，斜率由 mrad 转无量纲。 |
| `HALF_pre_1A_main.dat` | 同序对应相位窗 `−50° < phi < 20°` 后的 7,067 行中通过横向 6σ 筛选的 7,037 行。四个横向坐标量按筛选后的粒子群中心化。再套用后来脚本中的能量窗会得到 6,856 行。 |

两文件均用 `t=phi/(360×2856 MHz)` 和动能计算绝对 `p/(mc)`，逐列比较最大差异为位置 `5.0e-11 m`、斜率 `4.2e-11`、时间 `3.8e-18 s`、无量纲动量 `5.0e-6`。这些差异处于文本输出精度量级，支持两文件来自同一 T2 导出面。逐行复现验证了历史处理约定，物理坐标轴对齐、RF 相位符号和出口剩余电荷仍待核校。`injector.sdds` 的 120,985 粒子及其文件内电荷属于独立的 WATCH 样本。

原工作区 2023 年 IMPACT-T 说明和 `Half-Injector-ImpactT/lte.impt`（第 159–177 行）使用另一套名义坐标。该合成 `tmptws` 的 `zedge+L=8.7284344 m`，`ww` 在 `9.0 m`，名义间隔约 0.272 m。该间隔属于对应模型及版本。将来实现法兰处输出时需核对两套模型的原点、末端耦合器和采样面。

### ELEGANT 入口和几何核对条件

当前源格架 SHA-256 为 `1974e933b47370fe9deab7914f7195cf7b9de47f89340158360f35f58c46ee4c`。静态展开三条完整束线均得到以下模型坐标。 [格架入口定义](../../src/virtual_machine/half_elegant/elegant/lattice_ini.lte#L409)

| 参考面或元件 | 模型位置及用途 |
| --- | --- |
| `C#1 entrance` | `s=0`，束线切片索引 0，对应入口分布进入全部模型元件之前。 |
| `MALIN → CCC → W00` | 均为 `s=0`。默认 `C` 指定 `0.55 nC`，`CCC` 按相对平均到达时刻的绝对偏离筛除超过 25 ps 的粒子。WATCH 文件中的 `PreviousElementName=CCC` 对应经过筛选的模型状态。 |
| `DHOTGUN0A → ICT01 → BPM02` | 第一段漂移长 0.25 m，`ICT01` 和 `BPM02` 都放在模型 `s=0.25 m`。 |
| `BL01A` 和第一段 RFCW | `BL01A` 入口为 `s=0.50 m`。首次 `L1` 入口在 `s=16.449515 m`，第二次在 `19.703565 m`。 |

用本机 AutoCAD 对 `half_autotune/config/machines/half/references/` 下的 2025-07-02 注入器设备总图进行了只读核查。DWG SHA-256 为 `a63fb4580b89334ef878bbd2c64fd031065706e32d5f96c8cab6e17b23ea1f6b`。热阴极方案的 L01B、ICT01、BPM02、BL01A 引线位于同一入口区，能与带 `-P` 后缀的光阴极方案分开。图纸 `INSUNITS=4`（mm），顺束流方向为俯视 WCS x 减小。

| 图纸证据 | 读取结果 |
| --- | --- |
| L01B 引线 `11A772` | 标注指向加速管本体。下游真空几何含出口耦合及接续件，仍需指定哪一端面是接口基准。 |
| ICT01 引线 `11A6B1`、BPM02 引线 `11A6B2` | 两个箭头各指向不同的部件位置。其 x 投影相差约 66 mm，约在 BL01A 直线至弧线切点上游 491 mm 和 425 mm。数字表示引线端点，实际有效测量面需另行确认。 |
| BL01A 中心线切点 `119237`、尺寸 `130C53` | 所读转角为 0.304644 rad，与当前 ELEGANT 中 `BL01A` 的弯转角绝对值吻合。有效磁边界仍需与机械图和模型定义对齐。 |

这些图元确认了热阴极入口的拓扑。把 ICT01 或 BPM02 的引线端点直接当作有效测量面，再减去各自在 ELEGANT 中的 0.25 m，得到的模型零点与按 BL01A 切点和模型 0.50 m 推得的零点不同。正式绑定需要指定 L01B 出口法兰端面和 ELEGANT 对应的物理基准，并说明何处计入末端场与接续漂移。

可用 L01B 出口法兰到 ICT01 有效测量面的沿设计参考轨道距离减去 0.25 m，得到法兰到 ELEGANT `s=0` 的候选间隔，再用另一邻近设备复核。若该间隔确认恰为 0.10 m 且坐标轴和时钟对齐，则历史 T2 可作为该 ELEGANT 入口的离线粒子样本。正式 `L01B_EXIT` 指法兰面，需计入法兰至入口间的漂移或场区，并分别记录 `C` 赋电荷和 `CCC` 筛选的作用。

拟用 `L01B_EXIT` 作为物理截面 ID。一个 ELEGANT 绑定至少包括 `machine_id, lattice_sha256, line, element, occurrence, edge`。 `occurrence` 按该版本指定束线展开后从 1 数同名实例，`edge` 为 entrance 或 exit。解析后归档展开下标、相邻元件及 `s_m`（若已校核），用于复核。上游参考面也记录引擎原生位置和模型版本。启用前依据设备布局和场图确认同一出口平面、原点、束流方向、末端场归属及紧邻漂移/元件。上下游分段须各执行一次对应的场和漂移。未确认的绑定保留为空，调用请求指向 L01B 时在运行前报 `unresolved_boundary`。合成验收使用独立的测试截面 ID。

## 3. 六维束流载荷

下列约定用于 `BeamState6D` v1 合成验证。公共元数据描述同一束团在一个固定截面的前向过面分布。引擎在固定时刻输出的粒子状态需要通过本引擎的过面采样或经过验证的传播取得。

| 字段 | 候选约定 |
| --- | --- |
| `schema_version, boundary, frame` | 记录上节的边界身份及坐标版本。原点是设计参考轨道与截面的交点，`+s` 沿下游切向，`+y` 沿机器全局竖直向上在截面上的投影，`+x` 由右手系确定。各引擎的轴向、正负及参考轨道变换由适配器按校核的 frame 定义执行。 |
| `coordinates` | 顺序为 `[x, ux, y, uy, tau, delta]`，单位 `[m, 1, m, 1, m, 1]`。`ux=px/p_ref`、`uy=py/p_ref` 使用局部机械动量。`tau=c*(t-t_ref)`，正值表示较参考粒子晚到，`c=299792458 m/s`。`delta=(abs(p)-p_ref)/p_ref` 以总机械动量的模定义。向前粒子满足 `p_s>0`。 |
| `species, rest_mass_kg, particle_charge_c` | HALF 首版电子单物种，电荷带符号。适配器按物种校验质量与电荷。 |
| `reference` | `p_ref_mev_c > 0` 为参考粒子动量大小，另记参考粒子的选取依据。以 `E_ref = sqrt((p_ref*c)^2 + (m*c^2)^2)` 求总能量，`T_ref = E_ref - m*c^2` 为动能。其他单位或参考量在适配层换算。即使参考粒子由整束给定，也记录选择规则，允许 `mean(delta)` 非零。 |
| `timing, rf` | `t_ref_s` 为指定时间原点起算的参考粒子过面时刻。记录时钟/触发事件、束团标识和来源，采集的墙钟时间另存。每个供下游使用的 RF 参考含频率 Hz、参考粒子相位 rad、同步事件及相位标定版本。候选公共约定为有效加速电压 `V cos(phi)` 正峰处为零相，`phi(t)=phi_ref+2*pi*f*(t-t_ref)`，范围 `[-pi,pi)`。引擎和 LLRF 原生约定经标定映射。未知相位显式记为空，仅可交给无需该相位的阶段。 |
| `charge, loss` | `bunch_charge_c` 是过面存活束团的有符号总电荷。若有记录，损失摘要含对应输入截面、输入/存活宏粒子数、输入/存活电荷和损失电荷量。无重新采样、单物种且电荷守恒时，损失电荷量为 `abs(Q_in)-abs(Q_out)`，以其除以 `abs(Q_in)` 得损失率。注明统计覆盖、原因和位置（已知时）。未知损失记为未知，已知零损失需有证据。筛选和重采样另记为处理步骤。全损失返回终止状态和损失摘要，缺少可交接束流。 |
| `provenance` | 记录 `source_kind`（上游引擎、代理模型、外部文件、合成或测量）、来源模型/标定、工作点证据 ID/hash、生成阶段与载荷哈希。外部来源记录文件、页、筛选和单位变换。缺电荷或缺工作点证据的历史粒子资料仅用于与其已知元数据相符的测试或分析。 |

载荷二选一。JSON 保存上述公共字段及统计矩，粒子过多时使用带相对路径、格式版本、数组形状和 SHA-256 的文件引用。首版中立粒子文件建议用 NumPy NPZ 数组（`float64` 坐标/权重、可选 `uint64` ID，禁用 pickle）。ELEGANT 原生 SDDS 作为输入输出产物归档。

| `representation` | 内容、校验与转换 |
| --- | --- |
| `moments` | 长度 6 的均值 `mu` 与 6×6 的完整中心协方差 `Sigma`，允许所有横纵相关项。协方差单位为对应坐标单位的乘积。填入有限数，按坐标尺度检查对称和半正定，允许退化。输出记录覆盖的粒子群与统计约定。缺失的维度不能由现有两平面矩推出。 |
| `particles` | 非空 `N×6` 有限数组，逐行校验 `1+delta>0`、`(1+delta)^2 > ux^2+uy^2`。权重为正、无量纲，按存活粒子总权重归一化。第 i 个宏粒子携带有符号电荷 `Q*w_i/sum(w)`。有等权依据时可显式用等权配置。若提供 ID，应唯一并在无重采样追踪中保留。 |

以权重归一化的总体统计量从粒子计算 `mu=Σ(w_i*z_i)/Σw_i` 和 `Sigma=Σ[w_i*(z_i-mu)*(z_i-mu)^T]/Σw_i`。统计矩输入用于声明支持该坐标系及近似精度的矩计算。由统计矩采样须另行显式请求，并归档分布假设、算法和种子。首版 tracking 直接要求粒子输入。现有 `x_moments/y_moments` 仅经过原来的两平面传输。

## 4. 阶段输入输出及引擎适配

以下 `run_stage` 为拟新增的内部契约，用于第二种模型能力接入时。光学仍可沿现有 `prepare_model()` / `predict()` 执行，管线层可直接调用固定的两个阶段函数。

```text
run_stage(prepared_input, workdir) -> stage_result

prepared_input
  engine, mode/fidelity, segment(start, end), requested_outputs
  model_snapshot + frozen model/resource versions + seed/settings
  explicit beam_input at start + working_point reference

stage_result
  stage_input_hash, engine/mode, status, error/limitations
  predictions {diagnostic/location, observable, value, unit, definition}
  optional beams keyed by boundary, native matrices, artifacts, loss summary
```

准备时先解析段和所需参数，复用 `ModelSnapshot` 冻结数据，再校验试图消费的束流、空间边界、时钟、资源和请求能力。阶段输出引用可校验文件。相邻阶段须在相同物理截面及相同工作点条件下交接，存在明确坐标/参考粒子变换时需记录其版本。旧格式诊断统一映射回 `element/sigx|sigy` 的米单位预测，矩阵附引擎坐标元数据。缺输入、位置不可解析、不支持的表示/物理及依赖不全均在运行前拒绝。超时、取消、引擎错误和无存活束流给出阶段及原因，停止消费该阶段的后续计算。已成功的上游产物仍可追溯。诊断缺测或不兼容可产生带 `partial` 的会话归档。

| 阶段 | 消费与产出 | 可复用能力与接入条件 |
| --- | --- | --- |
| ELEGANT 局部光学（已有） | 消费 v1 参考动能、独立横向中心矩、段内 K1 和观测位置。输出两平面 RMS 与原生矩阵。 | 沿用耦合、色散和段类型检查。当前 `get_map` 的光学处理会归零 kick、清理偏移等误差并移除 RF wake 字段。模型精度按该光学模式解释。 [光学处理](../../src/shared/machine_profile/model_backend.py#L656) |
| ELEGANT 粒子 tracking（已实现仅下游） | 在 `ALL_MAIN` 的 `C#1 entrance` 接收模板束团、原生 SDDS 或六维粒子归档。输出屏幕总体 RMS 和原生 WATCH 文件。正式接口的段选择、六维出射载荷与损失摘要仍待接入。 | 复用 parser、runner 和束流源配置。独立跟踪在私有目录运行。现有 WATCH → SDDS 分段可作为后续分段参考，当前分段入口依赖 VM runtime。 [下游运行器](../../src/shared/digital_shadow/particle_model.py) · [束流源](../../src/virtual_machine/beam_source.py) · [分段先例](../../src/virtual_machine/lattice_usedline.py#L242) |
| IMPACT-T（后续上游） | 输入冻结的枪、发射初始条件、磁场/RF 参数及时序，消费版本化场图与数值设置。输出 L01B 过面粒子、损失及可导出的统计量。 | 经适配器转换原生动量、时间及截面。入口资料和物理边界独立验收。 |
| NN（后续上游选择） | 消费冻结的特征及其训练/标定归一化，检查适用域。输出所声明的 `moments` 或 `particles`，附权重模型、特征定义和训练版本，以及适用性信息。 | 若模型只提供统计量，仅交给接收统计量的阶段。 |
| Cheetah（后续下游选择） | 接收适配器已验证的表示，输出支持的界面束流或诊断，声明可用元件、集体效应和精度。 | 在接入时验证参考能量、纵向符号和适配能力。 |

拟用 ELEGANT 原生粒子 SDDS 命名列 `x,xp,y,yp,t,p`。在同一参考面和局部轴上，令 `P=p_ref*(1+delta)`，`p_s=sqrt(P^2-p_ref^2*(ux^2+uy^2))`，则 `xp=ux*p_ref/p_s`、`yp=uy*p_ref/p_s`、`t=t_ref+tau/c`（加上经核对的引擎时间原点偏移）、`p=P/(m*c)`（同单位制）。反向由 `p_s=P/sqrt(1+xp^2+yp^2)` 恢复横向动量及 `delta`。引擎输入 `p_central` 为参考 `βγ`，`p_central_mev` 为 MeV/c。输出使用实际跟踪的参考动量和时钟，特别是 RF 改变参考动量时。ELEGANT 的矩阵纵坐标有自身定义，公共统计矩的传输须另行验证坐标与参考量变换。 [APS SDDS 输入](https://ops.aps.anl.gov/manuals/elegant_latest/elegantsu79.html) · [参考动量](https://ops.aps.anl.gov/manuals/elegant_latest/elegantsu76.html) · [ELEGANT 纵坐标](https://ops.aps.anl.gov/manuals/elegant_latest/elegantse4.html)

初版导入采用单页/单束，保留到达时间和横向质心，显式关闭重中心、时间反号、筛选和抽样。WATCH 输出校核坐标列、截面、pass、粒子数及 ID。对权重或损失语义无法满足的配置直接拒绝，首个 ELEGANT 用例限定等权粒子。跟踪涉及电荷时，验证 ELEGANT 总电荷配置、输出电荷和公共有符号电荷的换算，包括集体效应所消费的值。RF 需要额外核对引擎 fiducialization 和相位基准。 [APS WATCH](https://ops.aps.anl.gov/manuals/elegant_latest/elegantsu269.html) · [APS CHARGE](https://ops.aps.anl.gov/manuals/elegant_latest/elegantsu152.html)

## 5. 追溯与最小重算

- **一次会话**沿用 `run_id`、原始快照 ID/hash、profile 签名、请求、证据和 `input_hash`。保存归一化/映射的 `ModelSnapshot`，并绑定束流参考截面和消费它的阶段。独立/历史束流保存它原有工作点及匹配证据，可用 `assumed` 或合成来源表达适用范围。工作点关联由证据决定，时间邻近本身不足以认证。当前 `input_hash` 含完整快照、请求、已解析状态、依赖和图片索引，`vm_input_version` 核验 VM 发布，图像对象 hash 用于完整性和去重。当前会话每次执行预测。 [会话哈希](../../src/shared/digital_shadow/session.py#L92) · [VM 核验](../../src/shared/digital_shadow/vm_capture.py#L88)
- **阶段计算**拟采用单独的 `stage_input_hash`。规范化串行化实际使用的模型字段及映射/标定版本、入口束流内容和物理元数据、起止截面及实例映射、请求输出、精度及随机种子、引擎/适配代码和会影响计算的依赖版本。格架、模板、场图、wake 文件、NN 权重、归一化等依赖按角色和文件内容哈希，固定到本次运行目录。需要随机过程时记录有效种子与数值设置；没有可验证依赖闭包或确定性时关闭阶段复用。文件字节 hash 供完整性校验，若将来需要跨文件封装复用，须为规范化内容定义稳定 hash。
- **上游变化**重算上游。只有结果束流及边界、参考量、时钟、质量要求均匹配时，才允许复用下游计算结果。下游独有参数、资源、精度或输出请求变化时，只重算对应下游阶段。跨快照复用前重新核验本次输入及来源并重绑归档。改动测量值、ROI、背景或显示单位时，若本次模型预测输入相同，只需重做比较。首版按整个阶段重算，先用测试调用计数核对，持久索引后置。
- **产物完整性**计算运行使用独立目录，先写临时结果，完成且校验完整后才发布可复用条目。失败、取消、丢文件或哈希不匹配的条目无法命中。命中须保留生成运行来源和产物校验信息，新会话仍生成自己的 manifest 及比较结果。

## 6. 审阅后最小改动清单

| 顺序 | 文件或入口 | 拟改动 |
| --- | --- | --- |
| 1. 契约及兼容 | `src/shared/digital_shadow/` 下独立的六维载荷模块（候选 `beam_state.py`）及定向测试 | 实现边界身份、单位和元数据校验、两个载荷及粒子转统计矩。保留 `models.py:ShadowRequest` 和 `digital_shadow_request_v1` 的语义与入口。 |
| 2. 计算与归档 | `pipeline.py`、`session.py` | 在确需组合时引入小型阶段入口，沿用快照派生、光学预测、比较和运行归档。v1 会话如需追加追溯字段，使用可选字段或由 manifest 的 `artifacts` 引用阶段 sidecar。既有 `digital_shadow_run_v1` 及 `result.json` 可继续读，旧档案无六维数据时浏览和回放按原能力处理。复用现有 worker/超时，阶段取消以作业取消语义为基线。 |
| 3. 后续最小 tracking | `src/shared/elegant_backend/` 下隔离的 tracking 或 SDDS 适配模块 | 从合成单段、等权单束、固定时钟起步，复用 parser、runner、束流源配置和命名列约定。纯辅助函数确有共用点时再提取。现存 `build_model_backend()`、光学调用方及 VM 源配置入口继续承担原有职责。 |
| 4. 正式 L01B 绑定 | `configs/machines/half/model_backends/simulation.elegant.json` 的模型配置，必要时 `lattice_ini.lte` 的源格架 | 审定后登记唯一实例/edge 和版本。现有边界可精确定位时只补配置。只有确定物理位置且现有边界无法表达时，才评审增加零长度 marker。本草案尚未赋值。 |

首批实现所需的机器状态与束流用离线夹具提供。通用任务调度、标定发布系统、持久计算缓存、完整 RF/弯铁映射和其他引擎接入均为后续工作。上述改动落在独立计算与模型配置，VM/IOC 同步及现场 PV 操作不属于该实施批次。

## 7. 离线验收计划

| 次序 | 输入与办法 | 通过条件 |
| --- | --- | --- |
| A. 旧链回归 | 用 `tests/test_digital_shadow.py`、`tests/test_machine_state.py`、`tests/test_digital_shadow_real.py`、`tests/test_digital_shadow_image_comparison.py` 的替身或注入读取器，外加现有 VM 示例归档。 | v1 请求/归档照常读取。real/VM 源与参数单位不串用，缺必需回读仍拒绝。光学矩阵、RMS、诊断行及失败归档符合现有断言。无法比较时数值残差为空。 |
| B. 六维 schema 与边界 | 合成右手前向截面，包括一条含重名元件的线。构造非零质心、横纵相关、早晚到达、非等权及退化协方差的粒子/统计矩夹具。 | 版本、实例、edge、方向和输入输出截面匹配。错误单位/时钟、无效参考粒子、非物理动量、错误协方差、权重或全损失各有明确处理。交换与派生统计保留横纵相关、时间符号、电荷、权重及来源。缺 L01B 绑定时正式请求被拒绝。 |
| C. SDDS 和 tracking | 先用人工生成的合成 SDDS 对检查页、列、单位、ID 和实际时间。经评审实施后，独立 ELEGANT 进程在临时目录跑恒等/漂移和一个明确损失的简单准直器。 | 粒子往返逐项一致。无场精确漂移以 `x_out=x+L*xp`、`y_out=y+L*yp`、`t_out=t+L*E/(p_s*c^2)` 校验，再按出口参考时刻恢复 `tau`。同时验证 RMS、存活 ID 及损失电荷。对小型、条件良好的 float64 转换夹具，可先用 `rtol=1e-12`，近零量按各坐标尺度设绝对容差。ELEGANT 数值用例另按固定版本和设置登记容差。 |
| D. 阶段失效与失败 | 用两个计数替身串联阶段，逐一改入口束流/边界、上游映射、下游独有 K1 或 wake、引擎/适配版本、标定、精度、种子、诊断请求和观测数据。注入取消、超时和缺失/损坏产物。 | 变更引发第 5 节指定的重算，旧会话仍可追溯。非计算观测变化复用预测，新比较绑定当前证据。失败记录保留阶段、类型、前序产物及原因，损坏产物不能复用。 |
| E. 正式物理交接 | 物理截面及 RF 约定审定后，使用可追溯的离线粒子基准，在重叠短段对比单段与分段传播，审阅末端场、相位和参考能量衔接。 | 以同一参考面及粒子群核对均值、协方差、能量/能散、到达时间、束长、电荷及损失。合格容差由基准数据、效应配置和精度评审确定。 |

A、B、C 的最小范围已通过。六维模块提供 `save_beam_state()`、`load_beam_state()` 和带格架身份检查的 `resolve_boundary()`。粒子载荷用带哈希的 NPZ，保留 uint64 ID，统计矩用 JSON。SDDS 适配器读写单页等权电子束，独立验收复用原有 ELEGANT runner。跟踪中的电荷由调用方配置，验收读回 WATCH 的实际电荷和存活粒子数。 [六维模块](../../src/shared/digital_shadow/beam_state.py) · [SDDS 适配](../../src/shared/elegant_backend/beam_sdds.py) · [验收夹具](../../tests/test_beam_state_6d.py)

在 `half_linac` 环境通过 98 项定向测试、3 个独立 ELEGANT 2022.1.0 工况以及 `bash scripts/check.sh`。ELEGANT 工况为零长度恒等、0.1 m 无场精确漂移和带准直器的 0.1 m 漂移，逐粒子位置、时间、存活 ID、RMS 和损失电荷符合断言。可在已配置 ELEGANT 的环境中重跑。

```bash
source scripts/setup.sh
HALF_ELEGANT_BEAM_INTEGRATION=1 python3 -m pytest -q tests/test_beam_state_6d.py
```

D 的阶段重算与 E 的正式物理交接仍待实施。首版独立验证使用合成格架与临时目录，模型运行未访问 EPICS。

本次设计核对已用仓库 parser 展开上述三条设计束线，读了既有 SDDS 文件头，并核对 `runs/digital_shadow_image_comparison/20261118/vm_example` 的主结果与 manifest 身份/状态及六项文件哈希。示例是 `QT01 entrance → PRF06/07/08/09`，四个模型 K1、八条标量比较和八条图像比较。验收表描述后续证据，当前核对仅使用既有产物和静态输入。

## 8. 待审阅决定

1. **物理映射和场边界**。由设备布局、场图或已核对过的模型给出 L01B 出口法兰、下游第一段及 ELEGANT 入口参考轨道的对应关系，确认末端场及两个参考面之间的段。为正式 ELEGANT 绑定和重叠段物理验收的前置项。
2. **交换约定**。确认机械动量与 `tau>0` 晚到、时间原点及 RF 正峰定义。对齐实际 LLRF/模型相位偏置、符号和 fiducialization，确认电子束电荷符号及首个等权 tracking 用例。坐标和载荷先用合成样例验证。
3. **正式基准与精度**。共同确定具有位置、参考能量/时钟、来源和损失说明的离线束流样例，及哪些集体效应由哪个阶段承担。现有 CCC/DMTB 文件、旧横向矩和 v1 VM 归档分别可提供格式或工程回归证据。

## 9. Digital Shadow 首版调用与验收

在 Digital Shadow 选择 **Offline particle model → Downstream only**。运行一次 `ALL_MAIN`，从模型 `C#1 entrance` 注入，在可用 WATCH 屏幕给出以质心为中心的总体横纵 RMS，单位 mm。历史记录可打开跟踪结果，也可从归档加载束流和快照，按当前模型配置启动一次新运行。归档中的原始格架、跟踪模板、已求值输入、资源、引擎哈希、日志和 WATCH 文件用于追溯。原局部光学、采集和比较入口仍沿用 `ShadowRequest`。

| 束流来源 | 入口行为 |
| --- | --- |
| Generated bunch | 采用现有 `one_ini.ele` 的束团、参考动量及随机种子。 |
| SDDS particle file | 在本次运行目录保存源文件副本。ELEGANT 直接读取原生坐标，保留时间符号和质心，不抽样。可选现有 `HALF_pre_1A_main.dat`，按既有模型入口使用。 |
| Saved six-dimensional particles | 校验机器、格架哈希、束线、元件实例与入口边界。要求等权电子粒子和明确总电荷。参考动量、粒子到达时间和电荷从归档转换。纯统计矩、边界不匹配的归档和非等权粒子都会被拒绝。 |

设计设置直接使用仓库格架。保存的 HALF 快照只覆盖 `ALL_MAIN` 所需的 63 个四极铁 K1。VM 文件取 K1，实机来源文件取已保存的 K1_total。RF、校正铁和其余设置采用当前设计。六维归档中的 RF 元数据保留供追溯，下游仍按 ELEGANT 模板的 RF 和 fiducialization 设置计算。现有 `CCC` 时间清理属于格架跟踪，独立于 SDDS 导入设置。

CLI 使用独立请求 schema。以下最小请求使用模板束团与设计设置。原生文件可令 `beam_source` 为 `sdds`，六维归档可令其为 `archived`，并添加 `beam_path`。快照需指定 `parameter_source: "snapshot"` 和 `snapshot_path`。路径相对请求 JSON 所在目录解析。

```json
{"schema":"digital_shadow_particle_request_v1","scope":"downstream","beam_source":"generated"}
```

```bash
source scripts/setup.sh
python3 scripts/run_digital_shadow.py run-model --request request.json --output-root runs/digital_shadow
```

首版以独立 ELEGANT 对模板、`HALF_pre_1A_main.dat` 和合成六维粒子归档分别进行工程验收，三次各得到 13 屏横纵 RMS。**Pre-injector only** 可用 P0 名义工作点独立运行 IMPACT-T，并导出 264 号原生过面。**Coupled** 用本次生成的过面粒子和保存的 VM 快照运行两段模型，采用 0 mm 名义补偿。L01B 出口到 ELEGANT 入口的实物轴向、时间和 RF 基准仍待核，见[交接说明](IMPACT_ELEGANT_DIGITAL_SHADOW_HANDOFF_20261119.md#6-p2-截面绑定核对)。
