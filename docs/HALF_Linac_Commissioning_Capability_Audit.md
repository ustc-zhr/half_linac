# HALF Linac Commissioning 能力复用与依赖审计

> 审计日期：2026-08-27
>
> 审计对象：`docs/HALF_Linac_Commissioning_App_Planning.md` 中提出的 commissioning utility 需求
>
> 审计范围：现有 `src/apps/`、`src/shared/`、HALF machine profile、运行记录约定与相关测试
> 本文只做能力与依赖审计，不改变现有 App 行为，不授权任何实机写入。

## 1. 结论摘要

当前仓库已经具备一部分可靠的 commissioning 基础能力，但这些能力分布在不同 App 中，成熟度和安全语义不一致。

可以直接复用的平台能力包括：

- machine profile 元素、逻辑通道与 real/vm PV 解析
- writable target、单位和物理限值解析
- workflow 级写入策略与实机 commissioning 状态
- app runtime 的 `machine/backend/latest/runs` 路径约定
- model snapshot 的保存、加载、来源记录与单位转换
- Machine Setpoints 中的计划预览、初值捕获、逐项写入、读回验证、冲突检测和逆序恢复
- PV Diagnostics（原 PV Connection Check）的 profile 全量 PV 枚举、批量连接测试和 CSV 导出
- CT Monitor 的带时间戳事件队列、同枪配对、stale/mismatch 判断和滚动统计
- RF Power Source Timing 的 setpoint/readback 联动、批量写队列和波形时间戳缓存

当前不存在、不能假定已经具备的能力包括：

- 通用的“机器运行状态快照”；现有 `model_snapshot` 只服务模型输入，Machine Setpoints 只服务四极铁 K1
- 可跨设备类型使用的控制事务；现有 setpoint transaction 假设写 PV 同时可作为验证读回 PV
- 全平台统一的 scan engine
- 全平台统一的 run/session 记录格式
- 通用设备互斥锁；当前只有能量控制专用锁
- HALF 的 beam permit、MPS、真空阀、屏/法拉第杯插入状态、设备 ready/fault 等 readiness 数据
- 能表达束线分支、目的地和上下游依赖的 commissioning topology
- 跨 CT、BPM、相机、LLRF 的统一 pulse ID 或 shot timestamp 契约

因此，近期不建议直接建立三个大型目录 `shared/snapshot/`、`shared/scan_engine/`、`shared/commissioning_recorder/`，也不建议把现有稳定扫描 App 迁入一个新框架。推荐先完成一个 Machine State Snapshot 纵向切片，再从第二个使用者中验证最小共享抽象。

## 2. 审计方法和判定标准

每个候选需求按以下四类归档：

- **新增 App**：现有界面无法自然承载，且需求回答一个独立现场问题。
- **扩展现有 App**：已有 App 拥有主要数据源、交互和运行语义，新建 App 会重复建设。
- **共享后台能力**：主要价值是向多个 App 提供数据模型、执行或记录能力，不应先做成独立 GUI。
- **暂缓**：关键现场事实、PV、物理标定或安全流程尚未确定，当前实现会产生虚假确定性或高维护成本。

复用等级：

- **直接复用**：现有 API 与语义基本满足需求。
- **复用概念与测试**：现有实现提供可靠模式，但接口或适用范围不足，需小范围提炼。
- **只作参考**：实现位于 app-owned 或外部维护子树，不应直接形成平台依赖。

## 3. 现有平台能力盘点

| 能力 | 现有位置 | 可复用程度 | 边界 |
|---|---|---|---|
| 机器元素和逻辑通道 | `src/shared/machine_profile/models.py`、`resolver.py` | 直接复用 | 只包含 profile 已建模的元素和通道 |
| 写目标与物理限值 | `resolver.resolve_write_target()`、`limits.LimitRange` | 直接复用 | 部分设备没有独立 setpoint/readback 语义；无配置时 limit 可为空 |
| workflow 写入策略 | `machine_profile/write_control.py` | 直接复用 | 新 workflow 必须显式接入，不能只依赖 GUI 确认 |
| 实机 commissioning 状态 | `machine_profile/commissioning.py` | 直接复用 | 当前映射只包含已有 App；新增 App 需要登记 workflow 名称 |
| App runtime 路径 | `machine_profile/app_runtime.py` | 直接复用 | 只统一目录和 run id，没有统一 metadata schema |
| 原子 JSON runtime state | `src/shared/runtime_state.py` | 直接复用 | 适合小型状态文件，不是时间序列数据库 |
| Model snapshot | `machine_profile/model_snapshot.py` | 复用数据来源与序列化思路 | 内容是 model-native lattice field，不能冒充机器控制快照 |
| Setpoint plan | `src/shared/setpoint_transfer.py` | 复用概念与测试 | 当前限定 Quad K1 和 design/current/manual target |
| Setpoint execute/restore | `src/apps/setpoint_transfer/execution.py` | 复用概念与测试 | app-owned；固定即时读回、固定 tolerance、写 PV 与验证 PV 相同 |
| PV 批量连接测试 | `src/shared/pv_connection.py` | 直接复用 | 只判断 Channel Access 连接，不读取数值、alarm、freshness 或 SP/RB 关系 |
| 同枪 CT 配对 | `src/apps/ct_monitor/model.py` | 复用概念与测试 | 当前是两通道配对；仍是 app-owned |
| RF 波形缓存 | `src/apps/power_source_timing/epics_client.py` | 复用概念 | 只覆盖已配置 RF timing 波形，不是通用 shot bus |
| 能量控制互斥 | `machine_profile/control_lock.py` | 只作专用实现参考 | 锁只覆盖 coordinated energy control，不应扩展名称后假装是全局设备锁 |
| 多旋钮映射 | `dispersion_correction/knobs.py` | 只作参考 | 只表达色散校正的对称四极旋钮和累计变化语义 |
| 通用 1D 扫描雏形 | `src/apps/jitter/jitter_analysis/src/jitter_analysis/acquisition/scan_executor.py` | 只作参考 | `jitter_analysis` 为外部维护集成代码；不依赖当前 machine-profile 写入治理 |

### 3.1 HALF 当前 profile 能覆盖什么

HALF profile 当前有 286 个元素：

| kind | 数量 | 主要逻辑通道 |
|---|---:|---|
| BPM | 43 | `x`, `y` |
| Corrector | 96 | `kick`, `current_set`, `current_readback` |
| Quadrupole | 68 | `K1`, `K1_adj`, `K1_total`, `current_set`, `current_readback` |
| RF | 21 | 多数为 `phase_set`；少量 timing/waveform |
| Modulator | 20 | HV、LLRF/SSA/KLY delay/width/enable 和波形 |
| Flag | 16 | image、exposure、sigx/sigy |
| Bend | 9 | angle、current_set、current_readback |
| Solenoid | 5 | current_set、current_readback |
| CT/Energy | 5/3 | charge/peak current、energy setpoint |

这足以支持 Snapshot 第一版、SP/RB Watchdog、CT 全线传输图和部分 RF health；不足以直接实现可信的 Beamline Readiness、Beam Mode 和 First-Fault 因果判断。

HALF profile 中目前没有发现通用的：

- `permit` / `mps`
- valve open/closed
- device ready/fault/interlock
- screen inserted/retracted
- pulse ID
- repetition rate / pulse width / beam mode
- radiation、vacuum、water、temperature

Readiness App 在这些事实补齐前只能叫“configured channel availability”，不能显示“Beam path READY”。

## 4. 写入、恢复与记录能力审计

### 4.1 Machine Setpoints 已经解决的部分

`src/apps/setpoint_transfer/execution.py` 已具备：

- 执行前逐 PV 可读检查
- 全部选中项初值捕获
- 逐项写入与即时验证
- 部分成功时结构化错误
- Apply 后状态冲突检测
- 逆序恢复
- JSON transaction 和 JSONL execution log

GUI 还提供写入明细预览、实机 `REAL` 二次确认和恢复冲突提示。

这些模式应作为 Machine Snapshot Restore 的起点，但不能直接把 `execution.py` 当通用事务层，因为：

1. 它位于 `src/apps/setpoint_transfer/`，属于一个 App。
2. `TransferItem` 固定为 Quad K1。
3. `PvClient` 只有 `read(pv_name)` / `write(pv_name)`，无法表达独立 setpoint 和 readback PV。
4. 验证是写后立即读一次，不支持 ramp、settle、poll timeout 或 alarm 状态。
5. tolerance 是一次调用的全局标量，不支持每通道单位和容差。
6. 执行失败后不会自动恢复已经成功写入的项；恢复由 GUI 后续操作决定。
7. Machine Setpoints 没有使用通用 workflow commissioning status，不能原样成为新实机工作流的授权依据。

结论：**复用数据流、交互、异常类型和测试，第二阶段再把通用部分提炼为 shared control transaction；不要复制，也不要直接泛化现有 TransferItem。**

### 4.2 Snapshot 必须分层

新的 machine state snapshot 至少要区分：

| 类别 | 示例 | 是否允许进入 RestorePlan |
|---|---|---|
| `restorable_state` | magnet/RF/timing setpoint | 是，但必须有明确写目标、限值和验证策略 |
| `readback_state` | current/HV/timing readback | 否，仅用于验证和比较 |
| `beam_observation` | orbit、charge、beam size | 否 |
| `environment` | vacuum、temperature、water | 否 |
| `derived` | transmission、energy、Twiss | 否 |

不能根据“一个 PV 能 caput”自动判定它可恢复；restorability 必须来自 machine profile 和 workflow policy。

### 4.3 运行记录目前只有目录约定，没有统一事件契约

已有 App 多数使用 `runtime/<machine>/<backend>/latest` 与 `runs`，但具体格式仍不一致：

- RF Phase Scan：CSV 逐事件 flush + 同名 JSON 总结
- Emit Measure：`scanResults.txt` + `metadata.json`
- BBA：多个文本文件 + metadata
- Dispersion Correction：latest/run metadata 和报告
- Solenoid Centering：仍使用 app-local `scans/scan_*.json` 与 `latest_result.json`
- HV Feedback：CSV logger + 参数快照
- Orbit Correction：progress、matrix、active response 分开保存

因此 Commissioning Recorder 不应尝试解析所有历史私有格式。应先定义一个小型跨 App “run envelope”，允许 artifact link 指向原始 App 文件。

建议 envelope 最少包含：

```text
schema_version
run_id / session_id
machine / backend / app / workflow
started_at / finished_at / status
operator_note / hostname
initial_state_ref / final_state_ref / restore_status
config_summary
artifacts[]
error
```

## 5. 扫描实现审计

### 5.1 结论

现有扫描共有“初值、计划点、写入、等待、采样、停止、恢复、记录”这些表面共性，但物理状态机差异显著。当前适合共享的是小型原语，不适合共享一个带算法语义的总 Scan Engine。

| 扫描 | 已有强项 | 当前缺口 | 审计结论 |
|---|---|---|---|
| Solenoid Centering | 完整 preflight、限值交集、SP/RB polling、state drift、防并发人工改值、停止、恢复验证、失败归档 | 实现较大且紧耦合专用评分/坐标下降 | 可作为安全事务语义的主要参考，不整体抽取 |
| Dispersion Correction | machine interface snapshot/restore、live preflight、beam safety、review baseline、cancel、rollback、质量门 | 强依赖能量调制、对称 knob 和色散物理 | 保持 app-owned，只复用 transaction/run envelope |
| RF Phase Scan | 可注入 read/set/match 回调、可取消等待、finally 恢复、restore error 结构化、逐点日志 | phase/energy 各自的验证由外层实现，依赖嵌套 energy matching | 保持专用 scanner；可复用 cancellable wait/result status |
| Orbit ORM | profile 限值、平均采样、finally 恢复、progress 和 matrix archive | 只测 `original -> +d -> original`；恢复不验证，失败只写日志且不改变调用结果；停止依赖 signal | 优先加固后扩展为 polarity/response validator |
| Emit Measure | profile 限值、grid/adaptive scan、协作停止、图像质量门、结果 metadata | 使用裸 `caput`；写后不验证；恢复无结果检查；错误/恢复状态没有统一记录 | 不先迁移；未来接入 shared control point |
| BBA | profile 限值、协作停止、finally 恢复、模型 snapshot、archive | `_safe_put` 不检查 put 状态和 readback；恢复无验证；BBA-1/2 自有数据格式 | 不先迁移；先解决物理 mapping 和写入验证 |
| Jitter Knob Scan | 已有通用 1D plan、limit、settle、采样、初值恢复 | 外部维护子树；未接 machine profile workflow/commissioning policy；恢复不验证 | 只参考，不作为平台 shared 依赖 |

### 5.2 最小共享原语建议

第一轮只考虑三个小型、dependency-light 的 shared 单文件模块，是否最终新增应由首个纵向切片验证：

1. `control_point`：描述 element/channel、setpoint PV、readback PV、unit、machine limit、tolerance 和 settle policy。
2. `control_transaction`：capture、validate plan、write-and-poll、abort、rollback、restore verification 和结构化结果。
3. `run_record`：生成 run envelope、latest/run 路径和 artifact references。

暂时不要抽取：

- 点生成算法
- 自适应搜索
- 扫描排序
- 物理质量门
- beam safety 判据
- App progress payload
- App 专用 result dataclass

这些仍由各 App 拥有。

## 6. 候选需求逐项分类

| 需求 | 主分类 | 可复用能力 | 前置依赖/阻塞点 | 建议状态 |
|---|---|---|---|---|
| Machine Snapshot / Compare | 新增 App + 小型共享能力 | resolver、PV endpoint、app runtime、model snapshot 序列化思路 | machine state schema、采集质量状态、restorable 分类 | **立即开始：第一纵向切片** |
| Partial Restore | 共享后台能力，Snapshot UI 调用 | setpoint execution 模式、limits、write policy | 独立 SP/RB、polling、per-channel tolerance、rollback policy、commissioning status | Snapshot Compare 完成后实施 |
| Setpoint–Readback Watchdog | 扩展 PV Diagnostics | endpoint 枚举、表格/筛选/导出、logical channel 命名 | SP/RB 配对规则、单位、容差、stuck/oscillation 时间窗 | **高优先级，复用 Snapshot collector** |
| Beamline Readiness | 新增 App | profile loader、Launcher runtime selector | permit/MPS/valve/screen/FC/ready/fault PV；destination topology；现场验收规则 | **暂缓编码，先做现场事实清单** |
| Generic PV Scan | 暂缓；未来新 App | scan 原语、jitter 实现参考 | control transaction、run envelope、real 写入白名单、named presets | VM-only prototype 也应晚于 transaction；Real 禁止 arbitrary PV |
| Commissioning Recorder | 共享后台能力 + Launcher session 控件 | app runtime、runtime_state、现有 App artifacts | run envelope、session id 传播、事件 API、日志保留策略 | 第二阶段；不先做复杂独立 GUI |
| Magnet Polarity / Response Check | 扩展 Orbit Correction | ORM 测量、model backend、corrector/BPM profile | ORM 恢复验证、±d 测量、单位一致、模型 ORM、质量判据 | **加固 ORM 后实施** |
| Beam Threader | 新增 App | Orbit Display、CT/Beam Monitor、corrector resolver | topology、beam-present 判据、上游 corrector 关联、安全步长、目标位置 | Readiness facts + Transmission Map 后实施 |
| RF Station Health | 新增只读 App或扩展 Timing | timing group、waveform monitor、HV buffer/logger | RF station 聚合配置、permit/interlock/fault/vacuum/power PV、freshness threshold | 可先做只读 v1；配置未确认项显示 Unknown |
| First-Fault Recorder | 暂缓/共享事件服务 | waveform timestamp、DataBuffer、CSV logger | 统一时钟、设备原始时间戳质量、event trigger、pre/post buffer、采样率 | 不与 RF Health v1 捆绑 |
| Transmission / Loss Locator | 扩展 CT Monitor | MonitorStore、ShotPairer、stale/mismatch、趋势图 | N 路同枪配对、CT 物理顺序/分支、beam-on threshold | **可立即设计，是低风险高收益项** |
| Trigger / Shot Sync Check | 新增只读小 App或 Diagnostic Health 子页 | CT timestamp 状态、RF waveform snapshot | BPM/camera timestamp 或 pulse ID、跨系统 tolerance | 先做 capability report；不能用客户端接收时间冒充同枪证据 |
| Diagnostic Health Check | 新增 App + 共享采集 | PV connection、CT sample status、shared image fit/background | BPM sum/noise/saturation 通道、相机 frame timestamp、设备阈值 | 分设备族逐步接入，不做一次性全诊断平台 |
| Accelerator Physics Calculator | 共享计算模块 + 上下文入口 | solenoid calibration、model snapshot conversions、dispersion physics | quad/bend/corrector calibration 完整性和来源元数据 | 低优先级；不要先做大而全独立 App |
| Beam Energy Profile | 暂缓/后续新增 view | Energy Spectrum、RF Phase Scan、model snapshot、elegant backend | 每段 measured energy 来源、RF gain、跨加速段 Twiss/energy 处理仍有 backlog | 暂缓自动 scaling；可先做 read-only design/known measurement view |
| Response Matrix Validator | 扩展 Orbit Correction | measured ORM archive、model backend | model ORM 生成、单位/设备顺序、噪声置信区间 | 与 Polarity Check 合并为同一能力 |
| Magnet Cycling | 新增 App | resolver、limits、未来 control transaction | 电源厂商/磁测认可 cycle recipe、ramp rate、ready/fault、abort safe state | **暂缓，先收集正式工艺** |
| Multi-Knob Builder | 暂缓；未来共享能力 + UI | `SymmetricKnobSet` 概念、timing linked shift | 通用权重/单位模型、组合限值、原子性/rollback、互斥 | transaction 稳定后再设计 |
| Correlation Finder | 扩展 Jitter Analysis/外围 wrapper | Jitter timed acquisition、correlation/spectrum、PV library | machine-profile 接入边界、lag/clock quality、run metadata | 不在 core 重写；遵守外部维护边界 |
| Beam Mode / Power Ramp Guide | 新增只读/advisory App | Launcher、readiness 结果、snapshot | charge/rep-rate/pulse-width/mode PV、MPS/RP 规则、目的地功率限值 | 暂缓编码，先由运行/MPS/辐射安全确认规则 |

## 7. 依赖关系和实施顺序

推荐依赖链：

```text
HALF 现场事实/PV 清单
        │
        ├── Machine State Snapshot v1（只读 capture + compare）
        │       │
        │       ├── control point / transaction 最小提炼
        │       ├── Partial Restore
        │       └── SP/RB Watchdog
        │
        ├── CT Monitor N 路采集
        │       ├── Transmission / Loss Map
        │       └── Trigger / Shot Sync capability report
        │
        ├── run envelope + session_id
        │       └── Commissioning Recorder
        │
        └── topology + readiness rules
                ├── Beamline Readiness
                ├── Beam Threader
                └── Beam Mode / Power Ramp Guide

加固 Orbit ORM
        └── Polarity / Response Matrix Validator

control transaction + 两个真实使用者
        └── 再评估 Generic Scan / Multi-Knob / Magnet Cycling
```

### Phase 0：先补现场事实，不写控制逻辑

需要与控制/MPS/RF/真空/诊断负责人确认：

- 每个设备族的 setpoint、独立 readback、ready、fault、interlock PV
- beam permit/MPS 状态与是否允许 HLA 只读展示
- screen、flag、FC、dump、阀门状态语义
- CT/BPM/camera/LLRF 的时间戳来源、pulse ID 和刷新行为
- 束线目的地、分支和每个目的地的 required upstream devices
- 磁铁 cycling 正式工艺、ramp rate 和中止安全状态
- 束流模式、脉宽、重复频率、电荷与目的地功率限制

这些是 machine-native facts。应优先放入 machine profile 元素、逻辑通道和少量 app workflow 配置，不建立细粒度 app-specific role 列表，也不重复维护可由 `kind` 和物理顺序推导的设备清单。

### Phase 1：Machine State Snapshot 只读纵向切片

第一版建议只完成：

1. 从 HALF profile 按 `kind` 派生可采集元素。
2. 读取 configured setpoint/readback/diagnostic channels。
3. 保存 machine/backend/time/host/channel quality。
4. 将条目分类为 restorable/readback/observation/environment/derived。
5. 加载两个快照或 snapshot vs current，显示 delta 和 unavailable/stale。
6. 保存到标准 app runtime `latest` 与 `runs/<run_id>`。
7. 不写实机，不提供 Restore 按钮。

验收重点不是 GUI，而是：schema 稳定、跨 machine/backend 校验、断连/NaN/单位不一致处理和可测试的 diff。

### Phase 2：Partial Restore 和 Watchdog

在 Snapshot schema 经一次现场使用验证后：

- 提炼 shared control point/transaction。
- 只允许 restore snapshot 中明确标为 restorable 的条目。
- build plan 与 execute 分离。
- 逐通道显示 current/target/limit/tolerance/readback PV。
- real 模式必须走 workflow write policy、commissioning status、显式确认和 audit。
- 部分失败必须输出 applied/verified/rolled_back/restore_failed/not_executed。
- Watchdog 复用相同 SP/RB 描述，不再另建 PV 配对逻辑。

Phase 2 于 2026-08-27 启动。已新增 dependency-light 的
`src/shared/control_point.py` 与 `src/shared/control_transaction.py`：控制点派生、
SP/RB tolerance 判定、real-only restore plan、write-and-poll、取消、逆序 rollback
和结构化结果均已有离线测试。共享层不会猜测缺失的安全参数。

当前 profile 事实仍阻塞 App 功能开放：HALF real profile 可派生 198 组独立 SP/RB，
但没有设备族/通道级 readback tolerance；HALF VM 的 `K1`/`kick`/`angle` 写通道没有同量纲
物理限值和独立 readback 映射。因此 Snapshot 不显示可执行 Restore，PV Connection
Check 也不能把 connection success 冒充 SP/RB match。下一步必须先由设备/运行方确认：

- 各设备族/通道的 readback tolerance 与 settle/poll timeout。配置采用全局、设备族、
  设备族/通道、单设备例外四级覆盖，不要求逐 PV 填写。
- VM `K1`、`kick`、`angle` 的物理限值。
- Watchdog 与 Partial Restore 只服务 real backend；VM 保留 Snapshot 只读
  capture/compare，不配置 control points，也不实现 Restore。

控制参数统一放在机器级共享 workflow
`configs/machines/<machine>/apps/control_points.json`，按 backend 配置。Snapshot
Restore 与 PV Connection Watchdog 均调用 `collect_control_points(profile, backend)`，
由 shared resolver 自动应用上述覆盖优先级；App 配置不重复保存 tolerance。

Watchdog 的只读批量采样由 `sample_watchdog()` 提供，连接失败、NaN、缺少 RB 或
缺少 tolerance 均保留为结构化状态，不会把连接成功误报为 SP/RB match。该结果已
接入 PV Diagnostics 的独立 `SP/RB Watchdog` 标签页，支持手动检查、停止和
CSV 导出。HALF real 已加入 provisional 设备族草案：corrector/solenoid `0.01 A`、
quadrupole/bend `0.05 A`、modulator HV `20 V`，统一 settle `0.3 s`、timeout
`5 s`；这些是联调起点，不是现场验收值，需按电源分辨率和稳态噪声手动修订。

### Phase 3：Transmission 与 Session

- CT Monitor 从一对 CT 扩为按 beam path 排序的多 CT transmission map。
- 保留 shot pairing、stale、alarm 和 gap semantics。
- 定义 run envelope；各 App 只发布 run/event/artifact reference。
- Launcher 增加轻量 Start Session、Note、Stop/Export，不做第二套 App launcher。

### Phase 4：设备响应与首束工具

- 先修正 ORM restore verification 和结构化失败状态。
- 用 `-d, baseline, +d` 或漂移鲁棒顺序测量 response。
- 将 sign/gain/dead/coupling/model residual 作为 Orbit Correction 的验证页。
- topology 和 beam-present 判据得到现场确认后，再实现 Beam Threader。

## 8. 当前不建议做的事情

- 不要创建允许 real 模式任意输入 PV 的 Generic Scan。
- 不要默认“一键整机恢复”。
- 不要把 model snapshot 扩名后直接当 machine snapshot。
- 不要把 Machine Setpoints 的 app-owned execution 复制到新 App。
- 不要一次性迁移 BBA、Emit、RF Phase、Solenoid 和 Dispersion 的 scan loop。
- 不要在缺少 permit/valve/interlock PV 时显示“Beam path READY”。
- 不要用 Python 客户端接收时间替代 EPICS timestamp/pulse ID 来证明同枪。
- 不要把 First-Fault 与普通低速 health dashboard 混为同一实时性承诺。
- 不要先建立 `configs/machines/half/commissioning/` 下的大量重复设备列表；先确认哪些事实不能从 machine-native element kind、order、plane、tags 和 channels 推导。

## 9. 第一项建议实施任务

下一项实现建议定为：

> **Machine State Snapshot v1：只读 Capture / Load / Compare，不含 Restore。**

原因：

- 可直接复用最多的现有 profile/runtime 能力。
- 不依赖束流已经打通。
- 不产生实机写入风险。
- 会迫使 machine state schema、通道质量、单位和分类先稳定下来。
- 后续 Partial Restore、Watchdog、Recorder 和 Beamline Readiness 都能复用其采集结果。

该纵向切片完成并经过 VM/离线测试后，再决定是否建立 `src/shared/machine_state.py` 和 `src/apps/machine_snapshot/`；不建议在 API 尚未由实现验证前先创建一组空的 shared package。

## 10. 审计后的 Backlog

### Ready

```text
[x] Machine State Snapshot v1: read-only capture/load/compare
[x] Define snapshot schema and entry categories
[x] Add machine/backend/schema compatibility validation
[x] Extend CT Monitor to N-channel transmission map
[ ] Audit Orbit ORM restore and failure propagation
```

Phase 1 于 2026-08-27 完成验收：`bash scripts/check.sh` 通过，Snapshot
聚焦离线测试 16 项通过，并完成 VM capture/load/compare smoke test。v1 保持只读，
不包含 Restore 或任何 EPICS 写入路径。

### Needs one shared vertical slice

```text
[x] Minimal control point / transaction core (real-only execution gate)
[ ] Machine Snapshot partial restore
[x] PV Diagnostics: SP/RB Watchdog tab (provisional tolerance draft)
[ ] Run envelope and session_id
[ ] Commissioning Recorder launcher integration
[ ] Polarity / Response Matrix Validator
```

### Needs onsite facts

```text
[ ] Beamline Readiness
[ ] Beam Threader topology and beam-present rules
[ ] RF Station Health signal inventory
[ ] Trigger / Shot Sync contract
[ ] Magnet Cycling recipes
[ ] Beam Mode / Power Ramp rules
```

### Deferred

```text
[ ] Real arbitrary-PV Generic Scan
[ ] General Multi-Knob Builder
[ ] First-Fault recorder before clock/timestamp validation
[ ] Automatic downstream lattice scaling
[ ] New core Correlation Finder duplicating Jitter Analysis
```

## 11. Verification evidence

本次审计使用的主要证据：

- `src/shared/machine_profile/resolver.py`
- `src/shared/machine_profile/limits.py`
- `src/shared/machine_profile/write_control.py`
- `src/shared/machine_profile/commissioning.py`
- `src/shared/machine_profile/app_runtime.py`
- `src/shared/machine_profile/model_snapshot.py`
- `src/shared/runtime_state.py`
- `src/shared/pv_connection.py`
- `src/shared/setpoint_transfer.py`
- `src/apps/setpoint_transfer/execution.py`
- `src/apps/pv_connection_check/main.py`
- `src/apps/ct_monitor/model.py`
- `src/apps/power_source_timing/`
- `src/apps/rf_phase_scan/phase_energy_scan.py`
- `src/apps/solenoid_centering/scan.py`
- `src/apps/orbit_correct/findresponse.py`
- `src/apps/emit_measure/main.py`
- `src/apps/bba/main.py`
- `src/apps/dispersion_correction/workflow.py`
- `configs/machines/half/machine.json`
- `configs/machines/half/control_backends/real.json`
- `configs/machines/half/apps/*.json`
- `tests/test_setpoint_transfer.py`
- `tests/test_pv_connection.py`
- `tests/test_ct_monitor.py`
- `tests/test_machine_limits.py`
- `tests/test_machine_profile.py`
- `tests/test_rf_phase_scan.py`
- `tests/test_solenoid_centering.py`
- `tests/dispersion_correction/`

审计期间没有启动 IOC、VM、elegant 或 GUI，也没有连接或写入实机 PV。
