# Energy Feedback 回读监管审查与优化记录

## 1. 文档目的

本文记录 2026-11-06 对 `half_linac` 仓库中写入确认、物理回读、安全监管和失败回滚机制的审查结论，作为后续现场测试与架构讨论的统一依据。

本次工作的直接目标是解决 Energy Feedback 测试过程中因回读管理过于复杂而出现的误 HOLD 和长时间等待，同时不降低现场设备的物理安全保护。

## 2. 问题背景

现场归档 `feedback_20261106_174859.7z` 显示：

- 控制器已经能够产生并执行有效修正；
- 运行中有 9 次 `APPLIED` 和 1 次正常的量化 `NOOP`；
- 另外 2 次动作因为“等待新的物理回读事件”超时而失败；
- 失败后的回滚也因为同样的回读新鲜度条件而被判定失败；
- 高压回读的源时间戳或 monitor 事件存在约 26～31 秒的空窗；
- 同期未出现 RF/HV 安全参考、FCT、硬限位或会话限位触发的 HOLD。

因此，当时的主要故障不是控制方向或安全门禁，而是写后确认同时依赖了物理值、monitor 回调和时间戳推进。设备值可能已经更新，但只要 monitor 事件没有及时到达，动作仍会被判定失败。

## 3. 审查范围与结论

### 3.1 Energy Feedback 原有路径

原写入路径串联了以下同步条件：

1. 记录写前物理回读与时间戳；
2. 等待 Channel Access put 完成；
3. 等待独立物理回读进入数值容差；
4. 要求回读源时间戳或本地 monitor 接收时间推进；
5. 任一条件失败则回滚全部命令；
6. 回滚再次执行同一套物理值和时间戳验证。

这套机制把“命令是否发出”“设备是否到位”和“monitor 是否产生新事件”绑定成一个事务。最后一项不是设备安全事实，却能够否决前两项，因此容易产生误失败。

### 3.2 HV Feedback

HV Feedback 的控制路径更简单：

- 写入路径确认 put 是否成功；
- HV 设定值与回读偏差、RF 幅相和幅度比例由持续安全检查独立监管；
- 每次自动修正前重新检查安全状态。

该结构说明安全监管可以和写入传输确认分层，但 HV Feedback 本身没有提供 Energy Feedback 所需的多执行器事务回滚能力，不能直接照搬。

### 3.3 手动控制应用

LLRF 等手动控制应用通常执行 put，并持续显示物理回读。它们不要求每次小步操作都等待新的 monitor 时间戳，因此交互阻塞较少。

### 3.4 扫描和运动类应用

磁铁扫描、相位扫描、螺线管和时序扫描等流程必须保留严格到位检查，因为下一扫描点在物理上依赖前一步已经完成。这些流程不应跟随 Energy Feedback 一刀切地放宽。

## 4. 方案比较

### 4.1 方案 A：只确认 put

优点是路径最短，不受任何回读更新影响。

缺点是无法确认设备是否真正到达目标；在连接超时或设备拒绝动作时，put 的返回状态也可能不足以证明物理状态。因此本次没有采用。

### 4.2 方案 B：物理值加 monitor 新鲜度

这是原方案。它能够证明收到一个新事件，但 monitor 事件延迟不等于设备未到位，现场结果已经证明它会造成误 HOLD 和误回滚失败。因此不再采用。

### 4.3 方案 C：主动 GET 加物理值容差

这是最终采用的方案：

- put 完成后使用主动 Channel Access GET，绕过 monitor 缓存；
- 只判断连接、有限数值和物理值是否进入配置容差；
- 不要求源时间戳或 monitor 接收时间推进；
- 超时仍然 HOLD，并对已经尝试写入的执行器执行回滚；
- 回滚使用同样的主动 GET 和物理值容差确认。

它去除了与安全无关的事件时序耦合，同时保留了物理到位确认。

## 5. 统一后的三层模型

### 5.1 连续显示层

测量、趋势图和普通状态显示继续使用 monitor 缓存，以降低 Channel Access 请求负担。

### 5.2 动作前安全层

每次闭环动作前，对闭环安全输入执行一次主动 GET，然后检查：

- FCT 束流存在；
- HV 设定值与物理回读偏差；
- `machine.json` 中的 HV 绝对设备限值（配置时）；
- RF 相位相对参考的漂移；
- 单步限制和会话累计限制；
- 必需 PV 的连接与有限数值。

任何安全条件失败仍然 fail-closed，不允许写入。

### 5.3 写后物理确认层

写入后只轮询独立物理回读是否进入配置的 `write_verification.readback_tolerance_kv`（运行时展开为执行器 `verify_tolerance`），轮询使用主动 GET。该层不再把时间戳推进作为额外门槛。

多执行器事务只回滚本次已经尝试写入的执行器，并按相反顺序恢复，避免对尚未执行的设备产生额外写入。

## 6. 已实施改动

### 6.1 EPICS 客户端

`src/apps/energy_feedback/client.py` 的 `read_many()` 增加 `fresh=True` 选项：

- 默认值保持 monitor-backed 读取；
- `fresh=True` 使用 `use_monitor=False` 执行主动 CA GET。

### 6.2 闭环安全输入

`src/apps/energy_feedback/bpm_dispersion.py` 中的 `read_closed_loop_inputs()` 改为主动读取，确保每次控制动作的 RF/HV/FCT 判断不依赖旧 monitor 缓存。

### 6.3 写入与回滚

`src/apps/energy_feedback/writer.py` 已完成以下整理：

- 删除写前回读基线和时间戳推进判断；
- 写后使用主动 GET 检查物理值；
- 保留数值容差和超时；
- 失败时只回滚已经尝试的命令；
- 回滚按反序执行并主动确认物理值；
- 运行详情明确标记 `active readback verified` 或 `active-readback verify timeout`。

### 6.4 界面与说明

界面将失败原因改为 `write/active-readback failure`，手动动作状态使用 `Manual write confirmed`。应用 README 已加入三层监管模型和扫描类应用的边界说明。

### 6.5 IRFEL 配置收敛

`configs/machines/irfel/apps/energy_feedback.json` 已按单一事实来源整理：

- HV 设定值/回读接线从 Energy Feedback 唯一执行器自动推导；
- ACC1/Buncher 相位元素和 HV 设定值/回读容差由 Energy Feedback 自身配置；
- 不再读取或展开 `hv_feedback.json`；
- Energy Feedback 只保留自身采用的 RF 相位漂移覆盖值；
- `controller.ki` 是唯一默认增益来源，单执行器的 `max_step` 是唯一默认步长来源；
- 必须保持开启的有效 PV 检查、故障 HOLD 和参考捕获改为代码规则，不再伪装成可选配置；
- 写后轮询间隔、命令精度和普通运行时参数使用固定默认值，配置中只显式保留现场需要评估的 5 秒到位超时；
- GUI 参数范围使用 `[minimum, maximum]` 紧凑写法，显示标签、精度和单执行器隐藏系数由解析器展开。

截至初次回读监管整理，解析后的运行配置仍保留原有生效值，没有删除动作前门禁、物理到位确认或回滚保护。后续现场审阅决定见第 11 节。

## 7. 初次回读整理中保持不变的安全保护

初次回读监管整理没有删除或放宽以下保护：

- 独立物理回读数值验证；
- `machine.json` 中配置的执行器机器硬限位；
- 单步与会话累计限制；
- HV 设定值与物理回读偏差；
- RF 相位漂移保护；
- FCT 束流存在检查；
- 失败 HOLD、自动停环和事务回滚；
- 能量控制互斥锁；
- 写权限白名单。

初次回读监管整理没有修改 PV 名称、IOC 数据库、写权限或任何现场生效阈值，也没有执行现场 PV 写入。第 11 节记录了随后删除 RF 幅度相关门禁的独立审阅决定。

## 8. 预期现场行为

如果设备物理回读已经进入容差，但源时间戳或 monitor 事件长时间没有推进：

- 旧实现：写入超时、进入 HOLD，回滚也可能被判定失败；
- 新实现：主动 GET 读取当前物理值，正常判定 `APPLIED`。

如果主动 GET 得到的物理值确实没有进入容差：

- 新实现仍然超时 HOLD；
- 已尝试的执行器被回滚；
- 回滚后的物理值仍需通过主动 GET 确认。

因此，本次改动只消除 monitor 事件时序造成的误失败，不会把真实的设备未到位当作成功。

## 9. 验证结果

- Energy Feedback、IRFEL FIR 和 BPM Energy Feedback 相关测试共运行 81 项：68 项通过，13 项按可选运行环境预期跳过；
- 紧凑 IRFEL 配置展开后的 PV、控制值和安全阈值回归测试通过；
- `bash scripts/check.sh` 通过；
- HALF 和 IRFEL 机器配置校验通过；
- IRFEL VM acceptance 通过；
- 增加了“主动 GET 且源时间戳不推进仍可正确确认”的回归测试；
- 增加了安全输入必须使用主动读取的回归测试。

## 10. 后续讨论项

以下内容本次没有继续调整，建议结合下一轮现场结果讨论：

1. `write_verified` 日志字段是否改名为 `write_confirmed`，以及如何兼容已有 CSV 分析脚本；
2. IRFEL 当前 5 秒 `write_verification.timeout_s` 与 `write_verification.readback_tolerance_kv` 是否需根据现场到位延迟继续调整；
3. 是否记录每次写入的物理到位延迟、主动 GET 次数和最终误差，作为现场诊断指标；
4. 是否将“主动读取并等待到位”抽成共享组件，供能量反馈和确有物理顺序依赖的扫描应用复用；
5. 是否需要在 GUI 中分别显示“put 已完成”“物理值已到位”和“安全门正常”，避免继续使用一个状态概括三层含义；
6. 下一轮归档中应重点核对：主动读取延迟、物理回读误差、回滚是否发生，以及控制器振荡是否仍存在。

在没有新的现场证据前，不建议进一步取消写后的物理值确认。

## 11. 现场审阅决定

### 11.1 RF 幅度门禁

2026-11-06 后续审阅决定从 Energy Feedback 删除以下两项写前门禁：

- ACC1 幅度相对参考范围；
- Buncher/ACC1 幅度比例漂移。

Energy Feedback 不再解析这两个阈值，也不再主动读取或捕获 RF 幅度 PV。
RF 相位漂移门和 HV 设定值/回读偏差等其他保护保持不变。
上述调整不影响独立 HV Feedback 应用自身的幅度和幅度比例保护。

### 11.2 源时间戳与 BPM 样本身份

IRFEL Energy Feedback 已删除 `bpm_timestamp_policy`、
`source_timestamp_policy` 和 `max_timestamp_skew_s` 配置及对应的 EPICS 源
时间戳门禁。源时间戳只保留为诊断和日志数据。

为避免定时采样反复使用同一份 monitor 缓存，BPM monitor 回调的本地事件
标识作为独立的 `sample_token`。只有新的 BPM monitor 事件才能推进 FIR
统计窗口；重复事件被忽略，但不再根据事件年龄或各 PV 源时间戳偏差判定
HOLD。动作前 FCT、RF/HV 主动读取只检查连接、读取错误和有限数值。

### 11.3 HV 绝对限值归属

Energy Feedback 已删除应用配置中的 `operating_range`，也不再继承
`hv_feedback/kly1` 的 `hv_range_kv` 作为本应用门禁。绝对 HV 限值只读取
`machine.json` 中 `MODULATOR_HV1.voltage_set` 的设备限值；机器配置未提供
该限值时，Energy Feedback 不施加软件绝对限幅。

自动反馈仍保留单步 `0.02 kV` 和相对会话起点 `±0.2 kV` 的累计限制。
手动目标不使用自动单步和会话累计限制，但仍需操作员确认；机器绝对限值
存在时必须满足该限值，并始终执行写后主动物理回读验证、失败 HOLD 和回滚。
独立 HV Feedback 的绝对范围及原有安全保护不受此调整影响。

Energy Feedback 同时删除了 `safety.source` 配置继承。HV 接线直接从本应用
唯一控制执行器推导，RF 相位元素和 HV 回读偏差由本应用配置明确给出；运行时
不再读取 `hv_feedback.json`。

### 11.4 本轮验证

- Energy Feedback、IRFEL FIR、BPM Energy Feedback 和 HV Feedback 聚焦测试
  共运行 104 项，全部通过，其中 13 项按可选运行环境预期跳过；
- 新增源时间戳仅诊断、重复 BPM monitor 事件不推进窗口、机器限值存在时
  必须执行、机器限值缺失时不设软件绝对限幅，以及手动目标不使用自动
  步长/会话限幅的回归测试；
- `bash scripts/check.sh`、HALF/IRFEL 机器配置校验、IRFEL VM acceptance 和
  `git diff --check` 均通过；
- 本轮未启动 IOC/VM，未连接或写入真实 PV。
