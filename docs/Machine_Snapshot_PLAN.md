# Machine Snapshot 简洁 Save/Restore 实现计划

## Summary

保留独立的 Machine Snapshot App，在现有采集、历史记录、比较和导出功能上增加直接 PV Restore。

第一版固定行为：

- 保存磁铁、高压、LLRF、时序的数值设定值，并附带可用的 readback。
- 不保存或恢复 `*_enable`。
- 恢复前显示保存值、当前值和差值，支持全选、仅变化项和部分选择。
- 恢复前自动保存所选 PV 的当前状态。
- 依次直接写入 setpoint PV；单项失败不停止其他项目。
- 写后立即回读同一个 setpoint PV确认，不等待独立 readback。
- 不实现 ramp、自动回滚、Dry Run、复杂限值或事务策略。

## Implementation Changes

### 1. 采集范围与快照

在共享快照模型中增加可推导的四类范围，不维护设备 ID 清单：

- 磁铁：`quad`、`corr`、`bend`、`solenoid` 的数值 setting。
- 高压：`voltage_set`。
- LLRF：`phase_set`、`amplitude_set`。
- 时序：所有 `*_delay_set`、`*_width_set`。
- 明确排除 `*_enable`、命令型 PV、数组和非数值 setting。

Capture 对话框改为四个默认勾选的范围；选择一个范围时同时采集其 setting 和对应 readback。现有快照 JSON 保持兼容，不强制升级 schema；范围可由元素类型和 logical channel 重新推导。

增加临时“Compare with Current”采集：使用基准快照所含 key 读取当前状态，仅作为比较对象，不自动写入历史。

### 2. Restore 模型与执行

在 `src/apps/machine_snapshot/restore.py` 新增 App 内部实现：

- `RestoreCandidate`：保存快照 entry、当前解析出的 PV、保存值、当前值、差值、可选状态及不可用原因。
- `RestoreItemResult`：记录 `success`、`failed`、`skipped`、目标值、回读值和错误。
- `RestoreResult`：汇总执行时间、成功/失败/跳过数量和各项结果。
- `build_restore_candidates()` 根据快照的 `element_id + logical_channel` 使用当前 machine profile 重新解析写 PV；不盲写快照中的旧 PV 名称。
- 只接受质量正常、有限数值、属于上述四类且不是 enable 的 setting；映射缺失或值无效时标记为 skipped。
- `RestoreWorker` 在 `QThread` 中顺序执行：
  1. `caput(..., wait=True, timeout=5)`。
  2. 回读同一个 setpoint PV。
  3. 使用 `math.isclose(rel_tol=1e-9, abs_tol=1e-9)`判断一致。
  4. 记录结果并继续下一项。
- Stop 只停止尚未执行的项目，不撤销已写项目。
- 不复用严格的 `shared/control_transaction.py`，避免引入独立 readback、限值和自动回滚要求。
- 执行前通过现有 `control_points` workflow 的 `write_control` 检查当前 backend；HALF/IRFEL 配置显式允许支持的 VM/real backend，不新增专用 workflow。

### 3. Restore UI 与历史记录

在现有 Machine Snapshot 主窗口增加 `Restore…`：

- 必须先选择一个历史或外部快照。
- 打开对话框后读取对应 setpoint PV 当前值。
- 表格显示：选择、子系统、设备、参数、保存值、当前值、差值、PV、状态。
- 提供 `Select All`、`Select Changed`、按四个子系统选择和清除选择。
- 默认选择所有“值发生变化且可写”的项目。
- 点击 Restore 时只确认一次，内容为机器、backend、快照名称和写入数量；real backend 明确显示 REAL。
- 执行期间显示进度；完成后显示成功、失败、跳过数量，失败明细保留在表格中。
- 单项失败继续处理其他项目。

正式写入前，对所有即将写入的 key 做一次定向采集并保存为普通历史快照：

```text
Before restore <source snapshot name> <timestamp>
```

读取失败的 key 从本次恢复中移除；其余项目继续执行。该快照以后可以像普通快照一样手动恢复，实现简单 Undo。

存储层增加 `restore_result.json`，记录源快照 ID、恢复前快照 ID、backend、时间和逐项结果；不建立复杂事务目录或自动 after snapshot。

### 4. 集成与文案

- Launcher 中 Machine Snapshot 从 `READ ONLY` 改为 `WRITE`，描述改为“Save, compare, and restore machine setpoints”。
- 保持 Machine Snapshot 为独立 App，不在 LLRF、时序或磁铁 App 中复制保存格式。
- 现有 `INTRINSIC_READ_ONLY_APP_NAMES` 语义改为中性的 commissioning-validation exemption，避免写功能仍被标记成只读。
- 不修改任何 PV 名称、IOC DB、VM JSON 或 lattice 生成流程。
- 实施时保留当前未提交的 LLRF 与 Launcher 修改，只对 Machine Snapshot 对应区域做最小补丁。

## Test Plan

- 四个子系统正确分类，`*_enable`、数组和非数值项被排除。
- Capture 默认选择四类数值设定，并仍能读取现有旧快照。
- Compare with Current 不创建历史文件。
- Restore 使用当前 profile 映射；旧 PV 名称变化时不写旧地址。
- 默认只选择发生变化且可写的项目，部分选择正确传递给 worker。
- 自动备份只覆盖将写项目；备份读取失败的项目不会被写入。
- Fake EPICS 客户端验证 caput 顺序、同 PV 回读和数值一致性。
- 单项连接、写入或回读失败不阻断后续项目。
- Stop 后剩余项目标记 skipped，已成功项目保持成功。
- VM 与 real 的 write-control 检查、real 确认信息正确。
- 更新现有“源码完全只读”测试为“Capture/Compare 路径不写 PV，只有 RestoreWorker 可写”。
- 运行：

```bash
python3 -m compileall src/apps/machine_snapshot src/shared
bash scripts/check.sh
```

## Assumptions and Defaults

- 保存值来自 setpoint PV 本身；readback 仅用于展示，不作为恢复源或成功条件。
- 第一版不恢复 enable、开关、状态和命令 PV。
- 默认恢复所有发生变化且成功解析的数值 setting。
- 回读不一致记为 failed，但不撤销写入，也不停止后续项目。
- real backend 仅保留一次普通确认，不要求输入额外确认文本。
- 自动恢复前快照是唯一 Undo 机制，不实现隐式回滚。
