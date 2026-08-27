# HALF Linac 调束辅助 App 需求分析与实现规划

> 目标：面向 HALF 注入器即将进入/已经部分进入在线调束阶段，系统梳理现有 `half_linac` 平台在 commissioning（初期调束、设备联调、故障定位、状态恢复）方面可能出现的高频需求，并作为后续与 Codex 讨论、设计和实现新 App 的需求文档。

---

## 1. 背景与总体判断

`half_linac` 当前已经具备较完整的束流物理高层应用框架，包括：

- Virtual Accelerator
- Machine Setpoints
- PV Diagnostics（原 PV Connection Check）
- Orbit Display
- Beam Monitor
- CT Monitor
- Jitter Analysis
- Energy Spectrum
- RF Phase Scan
- BBA
- Orbit Correction
- Solenoid Centering
- Solenoid Field Guide
- Emittance Measurement
- Dispersion Correction
- RF Power Source Timing
- HV Feedback
- Online Optimization

现阶段继续增加“高级束流物理算法”的边际收益，可能低于补齐 **commissioning utility / 调束辅助工具层**。

初期调束现场更常见的问题往往不是：

> “有没有更复杂的算法？”

而是：

> “现在机器哪里不对？”  
> “为什么昨天能出束，今天不行？”  
> “这个设备到底有没有执行设定？”  
> “束是从哪一段开始丢的？”  
> “这个校正铁/BPM/四极铁符号是不是反了？”  
> “RF 到底哪一级先异常？”  
> “我刚才这一轮扫描前的机器状态还能不能恢复？”

因此，建议将下一阶段开发重点放在：

> **Commissioning Tools / 调束工具箱**

特点：

- 功能小
- 现场高频
- 一次解决一个具体问题
- 强调状态可追踪
- 强调异常定位
- 强调安全恢复
- 尽量复用统一的扫描、快照、日志基础设施

---

# 2. 建议的 App 分类

建议逐步将 Launcher 中的应用重新组织为以下几类：

## 2.1 SYSTEM

负责系统和环境层：

- PV Diagnostics（Connection + SP/RB Watchdog）
- IOC / VM / Backend 状态
- Runtime / Machine Profile
- Process / App 状态

## 2.2 COMMISSIONING

负责首束、设备联调和调束辅助：

- Machine Snapshot / Compare / Restore
- Setpoint–Readback Watchdog
- Beamline Readiness Check
- Generic PV Scan
- Commissioning Recorder
- Magnet Polarity / Response Check
- Beam Threader
- RF Station Health
- Transmission / Loss Locator
- Magnet Cycling
- Accelerator Physics Calculator

## 2.3 DIAGNOSTICS

负责束流和设备诊断：

- Orbit Display
- Beam Monitor
- CT Monitor
- Jitter Analysis
- Energy Spectrum
- Diagnostic Health Check

## 2.4 BEAM CONTROL

负责束流反馈和校正：

- Orbit Correction
- Solenoid Centering
- Dispersion Correction
- HV / Energy Feedback
- Multi-Knob

## 2.5 PHYSICS

负责物理测量和高级算法：

- BBA
- Emittance
- RF Phase Scan
- Optimization
- Model–Machine Comparison
- Virtual Accelerator

---

# 3. 第一优先级 App

建议在大规模正式调束前优先考虑以下 8 个。

---

## 3.1 Machine Snapshot / Compare / Restore

### 目的

解决最常见的现场问题：

> “昨天束流很好，现在机器到底哪里变了？”

### 主要功能

保存一个机器状态快照：

- Magnet Setpoint
- Magnet Readback
- Corrector
- Solenoid
- RF amplitude
- RF phase
- RF forward / reflected / load power
- HV
- Timing
- Gun parameters
- Vacuum
- Selected diagnostics
- Orbit
- Charge / CT
- Operator note

支持保存多个命名状态，例如：

```text
2026-08-26_FirstBeam_BPM12
2026-08-27_LINAC01_Good
2026-08-28_Stable_800MeV
```

### Compare

显示：

| Device | Snapshot | Current | Delta | Status |
|---|---:|---:|---:|---|
| Q01 | 1.342 | 1.342 | 0 | OK |
| Q02 | -1.551 | -1.450 | +0.101 | WARNING |
| RF01 Phase | -20.3 deg | -18.6 deg | +1.7 deg | WARNING |

### Restore

不要默认“一键整机恢复”。

建议：

1. Compare
2. 用户选择需要恢复的设备
3. Preview
4. Safety check
5. Confirm
6. Write
7. Verify readback
8. Log transaction

### 重要原则

- Snapshot 本身必须默认只读
- Restore 必须可选项执行
- 必须支持 Dry Run
- 必须记录 before / after
- 必须支持恢复失败后的错误报告
- Real machine 写入必须显式确认

---

## 3.2 Setpoint–Readback Watchdog

### 目的

`PV Diagnostics` 的 `Connection` 页解决的是：

> PV 能不能连上？

该工具解决：

> 设备到底有没有按照 SP 执行？

### 检查内容

例如：

| Device | SP | RB | Delta | Status |
|---|---:|---:|---:|---|
| Q01 | 10.00 A | 10.01 A | 0.01 | OK |
| Q02 | 25.00 A | 21.34 A | 3.66 | ERROR |
| H01 | -0.50 A | -0.49 A | 0.01 | OK |

### 建议支持

- Absolute tolerance
- Relative tolerance
- Ramp timeout
- Stuck detection
- Oscillation detection
- SP/RB sign mismatch
- Readback missing
- PV disconnected
- Device not ready

### 筛选

```text
All
Quad
Dipole
Corrector
Solenoid
RF
Timing
HV
```

---

## 3.3 Beamline Readiness Check

### 目的

在首束、恢复出束、切换束线目标前，一键判断：

> 当前束线是否真的允许出束到指定位置？

### 用户选择目标位置

```text
BPM01
BPM05
LINAC01 Exit
LINAC02 Exit
Dump
```

### 自动检查

- Gun ready
- Timing ready
- RF station ready
- Vacuum valves
- Screen / Flag state
- Faraday cup
- Magnet PSU
- BPM availability
- CT availability
- MPS / permit
- Relevant interlock
- Required upstream devices

### 输出示例

```text
Electron Gun        READY
Timing              READY
RF01                READY
RF02                READY
Vacuum V03          OPEN
Screen SCR04        OUT
FC01                 OUT
Q01-Q08              READY
BPM01-BPM05         READY

Beam path: READY
```

异常时：

```text
SCR07 is inserted
LINAC02 RF not ready
V05 is closed
```

### 建议

实现时最好按“beam destination -> required devices”做配置驱动，而不是硬编码。

---

## 3.4 Generic PV Scan

### 目的

这是整个 commissioning 阶段性价比最高的工具之一。

避免以后每遇到一个扫描需求就单独写 App：

- 扫 solenoid 看 BPM
- 扫 corrector 看 BPM
- 扫 quad 看 screen
- 扫 RF phase 看 ICT
- 扫 RF amplitude 看 energy
- 扫 gun timing 看 charge
- 扫 trigger timing 看 waveform

### 输入

```text
Knob PV
Start
Stop
Step / N points
Settling time
Shots per point
```

### Measure

支持任意多个 readback：

```text
BPM01:X
BPM01:Y
BPM02:X
ICT01
Screen:sigma_x
RF:forward_power
```

### 建议能力

- 1D Scan
- 后续可扩展 2D Scan
- N-shot average
- Standard deviation
- Live plot
- CSV / JSON export
- Stop
- Emergency Abort
- Restore initial value
- Scan metadata
- Timestamp
- Operator note

### 核心原则

Generic Scan 应该成为后续这些 App 的公共底层：

- RF Phase Scan
- Solenoid Centering
- Magnet Polarity Check
- Quadrupole Scan
- Energy Scan
- Timing Scan

---

## 3.5 Commissioning Recorder

### 目的

把每天的调束从“靠记忆”变成“可追溯实验”。

### Session 概念

例如：

```text
2026-08-26 HALF First Beam
```

自动记录：

```text
11:20 session started
11:32 SOL01 adjusted
11:45 LINAC01 RF on
12:10 beam reached BPM18
```

并周期性或按事件保存：

- machine snapshot
- orbit
- charge
- RF
- magnets
- timing
- vacuum
- selected waveform / statistics

### Operator Note

允许快速输入备注：

```text
Beam first seen at BPM12
RF01 phase changed by +3 deg
SOL02 reduced due to beam size growth
```

### 与 Snapshot 联动

推荐：

```text
Session
  ├── snapshot_001
  ├── snapshot_002
  ├── scan_001
  ├── event_001
  └── notes.md / session.json
```

---

## 3.6 Magnet Polarity / Response Check

### 目的

新装置最容易出现的低级但致命问题之一：

- Corrector polarity 错
- BPM polarity 错
- Magnet power supply polarity 错
- Model sign convention 与 machine 不一致
- Calibration sign 错

### Beam-Based Test

例如对 HCOR01：

```text
I0 - dI
I0
I0 + dI
```

读取下游 BPM：

```text
BPM02 X
BPM03 X
BPM04 X
```

拟合：

```text
dX / dI
```

然后与模型比较：

```text
Measured sign: +
Model sign:    +

PASS
```

或：

```text
Measured sign: -
Model sign:    +

POLARITY MISMATCH
```

### 后续扩展

可进一步形成：

> Response Matrix Validator

---

## 3.7 Beam Threader / First Beam Assistant

### 目的

用于首次出束和束流恢复。

它和 Orbit Correction 的任务不同：

- Orbit Correction 假设束已经基本贯穿
- Beam Threading 解决“束还没送过去”

### 基本逻辑

```text
Gun
 ↓
BPM01
 ↓
BPM02
 ↓
BPM03
 ↓
...
```

检测：

```text
Last BPM with valid beam = BPM05
```

根据 beamline topology 找到：

```text
available correctors before BPM06:
HCOR03
VCOR03
```

允许：

- 手动 steering
- 小范围 corrector scan
- 最大化 downstream charge
- 将下一个 BPM X/Y 拉回中心
- 继续到下一个 BPM

### 推荐分层

```text
Beam Threading
    ↓
1-to-1 Orbit Correction
    ↓
Measured ORM
    ↓
Global Orbit Correction
```

---

## 3.8 RF Station Health / First-Fault Recorder

### 目的

现有 RF Power Source Timing 解决“时序怎么调”。

该 App 解决：

> 当前 RF station 整体健康吗？如果 Trip，到底谁先异常？

### 每个 RF station 显示

```text
Mod HV
HV charging
LLRF amplitude
LLRF phase
SSA
Klystron
Forward power
Reflected power
Load power
Vacuum
Interlock
Timing
Permit
```

### Last Trip / First Fault

例如：

```text
10:43:12.341  KLY output dropped
10:43:12.344  vacuum interlock
10:43:12.347  RF permit removed
```

核心价值：

> 能判断因果顺序，而不是只看到所有告警最终都亮红灯。

### 实现建议

需要支持：

- circular buffer
- high-rate / event-driven sampling
- timestamp normalization
- event freeze
- pre-trigger + post-trigger window

---

# 4. 第二优先级 App

---

## 4.1 Transmission / Loss Locator

### 目标

自动判断束从哪一段开始损失。

例如：

```text
ICT01   100 %
ICT02    99 %
ICT03    97 %
ICT04    55 %
ICT05    54 %
```

输出：

```text
Likely loss location:
ICT03 -> ICT04
```

### 建议支持

- Transmission efficiency
- Pulse-to-pulse statistics
- Threshold alarm
- Beam-on detection
- Relative normalization
- Trend view

---

## 4.2 Accelerator Physics Calculator

将 `Solenoid Field Guide` 的思路推广为统一工程量/物理量换算工具。

### 基础束流参数

输入：

```text
Kinetic Energy
```

计算：

- gamma
- beta
- momentum
- magnetic rigidity Bρ

### Quadrupole

```text
I <-> Gradient
Gradient <-> K1
I <-> K1
```

### Dipole

```text
I <-> integral(B dl)
integral(B dl) <-> bend angle
```

### Corrector

```text
I <-> kick
```

### Solenoid

```text
I <-> Bz
I <-> integral(Bz dz)
```

### 原则

所有 calibration 都应来自：

```text
configs/machines/<machine>/calibrations/
```

而不是写死在 GUI 中。

---

## 4.3 Beam Energy Profile

### 目的

沿束线显示：

```text
Design Energy
Estimated Energy
Measured Energy
```

例如：

```text
Gun
LINAC01
LINAC02
LINAC03
...
```

### 后续功能

可进一步实现：

> 根据当前 beam energy 自动 scale downstream lattice

需要特别注意：

- 不要未经确认自动写实机
- 首先提供 preview
- 输出 proposed lattice scaling
- 再由用户选择是否执行

---

## 4.4 Diagnostic Health Check

### 目的

区分：

> “束流有问题”

和

> “诊断设备本身有问题”

### BPM

检查：

- PV connectivity
- beam-synchronous update
- X/Y
- charge
- noise RMS
- stuck channel
- saturation
- pulse rate

### Screen

检查：

- camera connected
- frame rate
- background
- saturation
- ROI
- calibration

### CT / ICT / FCT

检查：

- baseline
- noise
- signal
- integral
- trigger
- saturation

输出：

```text
BPM01  GOOD
BPM02  GOOD
BPM03  SUSPECT: X noise high
BPM04  BAD: no beam-synchronous update
```

---

## 4.5 Response Matrix Validator

### 输入

- measured ORM
- model ORM

### 检查

- sign mismatch
- gain mismatch
- dead channel
- unexpected coupling
- missing response
- possible energy mismatch
- possible polarity error

例如：

```text
HCOR03 -> BPM06 X

Model:    +1.72 mm/A
Measured: -1.61 mm/A

SIGN ERROR
```

---

## 4.6 Magnet Cycling / Hysteresis Assistant

### 目的

标准化磁铁预循环和 normalization。

例如：

```text
0
 -> +Imax
 -> 0
 -> -Imax
 -> 0
 -> operating current
```

支持：

```text
Cycle selected
Cycle all quads
Cycle dipoles
Cycle correctors
Cycle one section
```

显示：

```text
SP
RB
Ramping
Settling
Complete
```

---

## 4.7 Multi-Knob Builder

### 目的

把多个设备组合为一个逻辑旋钮。

例如：

```text
Knob: LINAC energy scale

Q01 × scale
Q02 × scale
Q03 × scale
...
```

或 local bump：

```text
HCOR01 +0.5
HCOR02 -0.8
HCOR03 +0.3
```

或 injector coupled knob：

```text
SOL01
HCOR01
VCOR01
```

支持：

- Linear combination
- Scale factor
- Bounds
- Preview
- Restore
- Save knob definition

---

## 4.8 Correlation Finder

### 目的

寻找束流慢漂、抖动与机器参数之间的相关性。

目标 PV：

```text
BPM20:X
```

候选：

- RF phase
- RF amplitude
- HV
- magnet current
- temperature
- cooling water
- vacuum
- timing

输出：

```text
Strongest correlations:

LINAC02 phase       0.83
KLY02 water temp    0.71
HV02                0.68
```

后续可增加：

- time-lag correlation
- rolling correlation
- PCA
- anomaly detection

---

# 5. 最推荐先抽象的三个共享底层

与其每个 App 独立实现，建议先形成三个公共模块。

---

## 5.1 shared/snapshot/

负责：

- machine state collection
- snapshot serialization
- snapshot comparison
- restore plan
- safe restore
- transaction log

建议数据模型：

```python
Snapshot
SnapshotItem
SnapshotDiff
RestorePlan
RestoreItem
RestoreResult
```

需要支持：

```text
VM
REAL
```

并严格区分：

- read capability
- write capability
- restore capability

---

## 5.2 shared/scan_engine/

负责统一扫描逻辑：

```python
ScanKnob
ScanReadback
ScanPlan
ScanPoint
ScanResult
ScanAbort
```

统一处理：

- knob write
- settle
- acquire
- N-shot average
- timeout
- abort
- restore
- progress
- logging

后续 App 全部复用：

```text
Generic PV Scan
RF Phase Scan
Solenoid Centering
Magnet Polarity Check
Quad Scan
Timing Scan
Energy Scan
```

---

## 5.3 shared/commissioning_recorder/

负责：

- session creation
- event logging
- operator notes
- snapshots
- scan linkage
- fault records
- export

建议 Session 目录：

```text
commissioning_sessions/
└── 2026-08-26_first_beam/
    ├── session.json
    ├── notes.md
    ├── snapshots/
    ├── scans/
    ├── events/
    └── exports/
```

---

# 6. 所有实机 App 建议统一采用的安全工作流

任何涉及 real machine 写入的 App，尽量统一采用：

```text
READ CURRENT STATE
        ↓
BUILD PLAN
        ↓
VALIDATE LIMITS
        ↓
PREVIEW
        ↓
USER CONFIRM
        ↓
WRITE
        ↓
VERIFY READBACK
        ↓
LOG
```

如果异常：

```text
ABORT
   ↓
RESTORE / SAFE STATE
   ↓
VERIFY
   ↓
LOG ERROR
```

建议所有写入型 App 统一具备：

- Dry Run
- Preview
- Device limit check
- Machine Profile validation
- Real/VM backend distinction
- Abort
- Restore initial state
- Transaction log
- Timestamp
- Operator confirmation
- Readback verification

---

# 7. 代码结构建议

建议后续新增：

```text
src/apps/
├── machine_snapshot/
├── setpoint_readback_watchdog/
├── beamline_readiness/
├── generic_scan/
├── commissioning_recorder/
├── magnet_polarity_check/
├── beam_threader/
├── rf_station_health/
├── transmission_locator/
├── diagnostic_health/
├── accelerator_physics_calculator/
├── response_matrix_validator/
├── magnet_cycling/
└── multi_knob/
```

公共层：

```text
src/shared/
├── snapshot/
├── scan_engine/
├── commissioning_recorder/
├── device_health/
├── machine_state/
└── safety/
```

配置：

```text
configs/machines/half/apps/
configs/machines/half/calibrations/
configs/machines/half/commissioning/
```

可以考虑新增：

```text
configs/machines/half/commissioning/
├── beam_paths.json
├── readiness_rules.json
├── snapshot_channels.json
├── rf_stations.json
├── device_tolerances.json
└── scan_presets.json
```

---

# 8. 建议的实施顺序

## Phase 1：先补基础设施

优先：

```text
shared/snapshot
shared/scan_engine
shared/commissioning_recorder
```

然后实现：

```text
Machine Snapshot
Generic Scan
Commissioning Recorder
```

这三个会成为后续大量 App 的基础。

---

## Phase 2：解决首束和设备联调

实现：

```text
Setpoint–Readback Watchdog
Beamline Readiness Check
Magnet Polarity Check
Beam Threader
RF Station Health
```

目标：

> 服务 HALF 首束和早期 commissioning。

---

## Phase 3：提高调束效率

实现：

```text
Transmission / Loss Locator
Diagnostic Health
Accelerator Physics Calculator
Response Matrix Validator
Magnet Cycling
```

---

## Phase 4：模型驱动和智能化

后续与 Virtual Accelerator / Digital Shadow 联动：

```text
Beam Energy Profile
Model–Machine Residual Viewer
Automatic lattice scaling
Multi-Knob
Correlation Finder
Anomaly detection
Online model calibration
```

---

# 9. 如果当前只做 5 个

如果资源有限，建议最先做：

1. **Machine Snapshot / Compare / Restore**
2. **Generic PV Scan**
3. **Setpoint–Readback Watchdog**
4. **Beamline Readiness Check**
5. **Commissioning Recorder**

原因：

这 5 个并不依赖束流已经完全打通，且正式 commissioning 一开始就会高频使用。

其中：

> `Snapshot + Generic Scan + Recorder`

建议视为整个 commissioning 软件层的三个基础组件。

---

# 10. 与 Codex 讨论时建议重点考虑的问题

后续可以让 Codex 先不要立即编码，而是逐项分析以下问题。

## 10.1 当前代码复用情况

检查现有仓库中：

- Machine Setpoints
- RF Phase Scan
- Solenoid Centering
- Orbit Correction
- PV Diagnostics（Connection + SP/RB Watchdog）
- Machine Profile
- Runtime backend
- App launcher

哪些已有能力可以复用。

尤其关注：

```text
PV read/write abstraction
real/vm backend abstraction
transaction logging
restore mechanism
scan loop
threading
abort handling
machine profile mapping
limit validation
theme/UI components
```

---

## 10.2 是否已经存在重复扫描逻辑

重点检查：

```text
rf_phase_scan
solenoid_centering
bba
emit_measure
dispersion_correction
orbit_correct
```

如果存在多个独立 scan loop，应考虑抽取：

```text
shared/scan_engine/
```

避免后续 Generic Scan 再形成一套重复实现。

---

## 10.3 Snapshot 与 Machine Setpoints 的关系

建议重点讨论：

> Machine Snapshot 是否应该复用现有 setpoint_transfer 的 transaction / restore 机制？

不要简单复制代码。

Machine Setpoints 更偏：

```text
Design -> Target
```

Machine Snapshot 更偏：

```text
Historical machine state -> Current machine
```

二者在：

- validation
- execution
- restore
- transaction
- PV write

方面应该尽量共享。

---

## 10.4 Commissioning Session 数据格式

提前确定稳定数据格式。

建议：

```text
JSON metadata
CSV / parquet numeric data
Markdown operator notes
```

避免以后不同 App 各自产生完全不兼容的数据文件。

---

## 10.5 App 配置驱动

所有新 App 尽量避免 HALF-specific 硬编码。

目标：

```text
HALF
IRFEL
STCF-BTP
```

以后可以通过 machine profile / app config 接入。

推荐：

```text
src/apps/<app>
      +
configs/machines/<machine>/apps/<app>.json
```

---

# 11. 最终开发原则

整个 commissioning utility 层建议坚持以下原则：

### 1. 小而实用

一个 App 尽量回答一个清晰问题。

### 2. 配置驱动

不要把 HALF 的 PV 和设备关系写死在代码中。

### 3. Read-only 优先

诊断功能默认不产生写操作。

### 4. 写操作必须可预览

所有 real-machine write 尽量具备 Preview。

### 5. 可恢复

任何 scan / tuning 都尽可能保存 initial state。

### 6. 可追溯

所有操作记录：

```text
who / when / what / before / target / after / result
```

如果暂时没有用户身份系统，也至少记录：

```text
timestamp
hostname
machine
backend
app
operation
```

### 7. VM/REAL 共用逻辑

尽量先在 VM 验证工作流，再允许 REAL。

### 8. 不重复造 scan loop

扫描、快照、日志应成为共享基础设施。

### 9. 让现场需求快速组合

最终目标不是不断新增几十个孤立 App，而是让：

```text
Snapshot
+
Scan Engine
+
Recorder
+
Machine Profile
```

能够快速组合出新的 commissioning workflow。

---

# 12. 推荐的近期开发 Backlog

## P0

```text
[ ] shared/snapshot
[ ] Machine Snapshot / Compare / Restore
[ ] shared/scan_engine
[ ] Generic PV Scan
[ ] shared/commissioning_recorder
[ ] Commissioning Recorder
[ ] Setpoint–Readback Watchdog
[ ] Beamline Readiness Check
```

## P1

```text
[ ] Magnet Polarity / Response Check
[ ] Beam Threader
[ ] RF Station Health
[ ] Transmission / Loss Locator
[ ] Diagnostic Health Check
[ ] Accelerator Physics Calculator
[ ] Magnet Cycling Assistant
```

## P2

```text
[ ] Response Matrix Validator
[ ] Beam Energy Profile
[ ] Multi-Knob Builder
[ ] Correlation Finder
[ ] Model–Machine Residual Viewer
[ ] First-Fault / Trip Recorder
```

---

# 13. 建议 Codex 第一轮任务

建议把本文件交给 Codex 后，第一轮只做架构分析，不立即全面编码。

可以要求 Codex：

```text
请结合当前 half_linac 仓库代码，对本需求文档进行一次实现可行性分析。

重点：

1. 梳理现有 app 和 shared 模块中可以直接复用的能力；
2. 找出 rf_phase_scan、solenoid_centering、bba、emit_measure、
   dispersion_correction 等应用是否存在重复的扫描逻辑；
3. 评估抽象 shared/scan_engine 的最小改造方案；
4. 分析 Machine Snapshot 与现有 setpoint_transfer / restore / transaction
   机制之间如何复用；
5. 给出 shared/snapshot、shared/scan_engine、
   shared/commissioning_recorder 三个基础模块的数据结构和 API 草案；
6. 不要首先大规模重构现有稳定 App；
7. 所有 real-machine 写操作必须保留显式确认、限值检查、
   readback verification、abort 和 restore；
8. 优先保证 VM 可测试；
9. 最终给出一个按风险和依赖排序的 implementation plan。
```

---

## 结论

`half_linac` 下一阶段最值得补齐的不是更多彼此独立的高级束流算法，而是建立完整的 **commissioning utility layer**。

建议围绕以下四个核心能力构建：

```text
Machine Profile
      +
Snapshot
      +
Scan Engine
      +
Commissioning Recorder
```

在此基础上逐步形成：

```text
设备检查
→ 首束
→ 分段传输
→ 扫描调参
→ 状态比较
→ 状态恢复
→ 故障定位
→ 模型校验
→ 自动调束
```

最终使 `half_linac` 从“多个好用的调束 App”逐渐演化为：

> **一个面向新建直线加速器 commissioning、运行优化和模型驱动调束的统一 HLA 平台。**
