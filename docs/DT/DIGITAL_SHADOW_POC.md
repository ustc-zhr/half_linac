# HALF 数字影子 PoC

本阶段实现保存快照驱动现有 ELEGANT 的局部横向预测与诊断比较，以及 VM 专用的一次只读采集。命令行支持合成输入、离线重放和 `run-vm`；不提供 real 采集或 PV 写入入口。

真实快照中的 `K1_total` 是四极铁归一化强度回读，映射到模型 `K1`，单位均为 `1/m^2`。`K1` 设置值保留在原始快照中用于追溯，不作为回读缺失时的替代。VM 快照使用其 `K1` 通道。该选择仅作用于数字影子，不改变其他应用。

现有 Machine Snapshot GUI 的默认计划不包含 K1_total 和诊断 observations；旧快照不能假定满足输入要求。首版要求必要回读具有正确来源 PV、单位、标量类型与 OK 质量，且接收时间在采集窗口内。缺少必要回读时停止计算；诊断不可用时保留预测并将比较标为 unavailable。

## 1 离线演示

在专用 worktree 根目录执行。Python 环境需能导入项目现有依赖，并能找到 ELEGANT。每个 example 目录必须是新目录。

```bash
python3 scripts/run_digital_shadow.py example --output-dir /tmp/half-shadow-example
python3 scripts/run_digital_shadow.py replay \
  --snapshot /tmp/half-shadow-example/machine_snapshot.json \
  --request /tmp/half-shadow-example/request.json \
  --output-root /tmp/half-shadow-runs
```

示例使用既有 `EMIT_QL09_PRF03` 格架，入口为 QL09 entrance。所有设备和诊断数值均为合成数据，入口束流为假设，输出保留 `SYNTHETIC` 来源和 `beam_basis="assumed"`。快照的 real 标签仅选择保存数据的 PV 映射；不会连接机器。示例残差没有实机精度验证意义。

`replay` 每次创建新的 `shadow_<uuid>` 目录，不覆盖旧结果。运行成功且全部诊断可比较时退出 0，部分诊断不可比较时退出 2，输入或计算失败时退出 1。命令行参数用法错误也由 argparse 返回 2。

## 2 已有快照输入

原始输入使用现有 `MachineStateSnapshot` JSON 格式，包含 machine/backend、profile signature、采集起止时间、逐通道时间、单位、质量和数值。原有快照的默认采集范围不保证含有 K1_total 或诊断，缺失时必须补充采集方案，不能在重放时重新读取 PV。

必要四极铁回读必须为质量合格的有限标量，PV、单位和 profile 签名需与当前模型装配一致。整个快照中额外通道失败可以保留，但必要回读缺失、报警或不合格时计算失败。回读的 received_at 必须在采集窗口内；设备源时间戳可以代表长期未变化的值，不按当前墙钟判断历史快照过期。

诊断当前只支持 profile flag 上的 sigx/sigy，需明确声明为 RMS、单位 m 或 mm。其源时间须处于相对历史采集窗口的诊断有效期内，received_at 在窗口内。单位、映射、时间或质量不符合要求时，保存 unavailable 及原因，残差留空。

本阶段不采集图像，不推断未知 PV 的束宽定义，不修改既有快照比较器。

## 3 请求格式与入口束流

example 命令生成可编辑的请求文件，其结构如下。数值仅示意，不代表实际入口束流。

```json
{
  "schema": "digital_shadow_request_v1",
  "model_backend": "simulation",
  "model_line": "EMIT_QL09_PRF03",
  "reference_element": "QL09",
  "reference_edge": "entrance",
  "beam": {
    "energy_mev": 114.15,
    "x_moments": [0.000004, 0, 0.00000001],
    "y_moments": [0.000003, 0, 0.00000002],
    "basis": "assumed",
    "provenance": "示意入口束流，需替换为有来源的数据",
    "fitted_diagnostics": []
  },
  "diagnostics": [
    {"element": "PRF03", "channel": "sigx", "unit": "mm", "width_definition": "rms"},
    {"element": "PRF03", "channel": "sigy", "unit": "mm", "width_definition": "rms"}
  ],
  "max_capture_duration_s": 5,
  "diagnostic_max_age_s": 5,
  "model_timeout_s": 30
}
```

每个 moments 为 `(A, B, C)`，表示 `[[A, B], [B, C]]`，即位置方差、位置与斜率协方差、斜率方差；使用几何束流矩，SI 单位分别为 m²、m·rad、rad²，不是归一化发射度。两平面需正定。energy_mev 是该 reference 的电子动能，按 `sqrt(T(T+2mc²))/(mc²)` 转为 ELEGANT 的 p_central；冻结模板中不依赖原来的默认能量。

`basis` 为 assumed 或 independent_measurement，必须保存非空 provenance。使用某诊断拟合过入口束流时，将其 key 写入 fitted_diagnostics；程序拒绝把这些诊断作为独立预测比较。已有发射度归档的入口位置、能量、单位和数据时效需核实后转换，程序不自动声称不同测量属于同一工作点。

## 4 计算边界与接口

首版支持局部 DRIF、QUAD、MONI、WATCH、MARK、HKICK、VKICK；拒绝 RF、弯铁和其他未支持元件。reference 与观测端点必须是唯一 occurrence，所需四极铁也需唯一。矩阵出现横向耦合或纵向到横向的项时，拒绝用二维入口束流计算。

计算使用现有 ElegantModelBackend 和 transport_beam_moments，光学模式会清零部分误差并关闭 tracking。结果只表示无耦合线性横向预测，不包括完整轨道、空间电荷、wake、束流损失或完整六维 tracking。候选段落的 ELEGANT 矩阵计算不等于对实际机器模型的物理验证。

主要入口：

- `machine_profile.model_snapshot.build_model_snapshot_from_machine_state()`：验证并转换保存数据，不调用 EPICS；通道覆盖是会话局部参数。
- `digital_shadow.pipeline.prepare_model()`：推导所需四极铁并检查段落。
- `digital_shadow.pipeline.predict()`：冻结模型、隔离工作目录、计算矩阵和预测。
- `digital_shadow.comparison.compare_diagnostics()`：按身份、单位、定义与时间对齐。
- `digital_shadow.session.run_once()`：连接离线步骤并归档。

VM 的 `capture_vm_once()` 已实现；real 采集和通用多引擎调度尚未实现。本次没有增加新的机器状态管理器、改变 GUI 状态机或 EPICS 写入保护。

## 5 运行产物与追溯

每次运行保存：

| 文件 | 含义 |
| --- | --- |
| machine_snapshot.json | 完整原始快照，保留设置、回读和诊断 |
| request.json | 计算范围、独立入口束流和比较要求 |
| model_snapshot.json | 从本次快照派生的模型 K1、来源 PV 与转换 |
| inputs/evaluated_state.json | 求值后的设计格架和显式参考动量 |
| inputs/design.lte 与 template.ele | 本次独立模型输入 |
| model/ 下各目标目录 | ELEGANT 输入、输出和日志 |
| result.json | 传输矩阵、预测、实测、measured - predicted 残差与比较状态 |
| manifest.json | run、snapshot、输入哈希、引擎二进制哈希、实现文件哈希、产物哈希和失败原因 |

失败运行保留 failed manifest，不生成成功 result。每个目标只计算一次矩阵，两平面共用；相同输入可以重复运行，但不启用通用缓存。complete 只表示计算完成且诊断通过工程对齐条件，不表示模型精度达标。

## 6 验证与下一步

针对性测试检查回读选择、禁止 PV 访问、坏输入不回退、身份与质量校验、输入绑定、目录隔离、诊断不可比较、失败记录和超时。另使用合成输入完成既有 ELEGANT 的离线计算，并以独立硬边四极铁及漂移矩阵核对两平面结果。

2026-11-17 验证记录：

| 检查 | 结果 |
| --- | --- |
| `tests/test_digital_shadow.py` | 24 项通过；测试中禁止 EPICS PV 读写 |
| 模型、快照、采样相关定向回归 | 36 项通过，另有 2 个 subtest 通过；1 项已有失败 |
| `bash scripts/check.sh` | 通过，未启动 GUI、IOC 或仿真进程 |
| 合成输入 ELEGANT 冒烟计算 | complete，两平面预测及残差成功归档 |
| 独立解析矩阵核对 | 以 `rtol=atol=1e-10` 核对四极铁与漂移链，两平面通过 |

已有失败为 `tests/test_elegant_backend.py::ElegantBackendTests::test_full_twiss_profile_shoots_once_from_line_start`：测试要求返回对象与 mock 对象身份相同，而现有实现包装为含入口能量的 TwissProfileResult。该失败在未改动的 main 基线 `0b5051e` 上也已复现，本次没有修改该测试或对应全线 Twiss 方法。这不是完整测试集全部通过的声明。

真实机器验证仍需要入口束流资料和单位、RMS 定义、时间质量已确认的诊断快照，以及明确的 real 采集授权。本阶段的合成数值不用于实际调束。

## 7 VM 一次采集与比较

先用已有 profile 生成 `QT02 entrance → PRF06` 的示例请求。`--vm` 生成的快照仍是合成数据；`run-vm` 只使用其中的请求文件，重新从运行中的 VM 读取状态和诊断。

```bash
python3 scripts/run_digital_shadow.py example --vm --output-dir /tmp/half-shadow-vm-example
python3 scripts/run_digital_shadow.py run-vm \
  --machine half \
  --request /tmp/half-shadow-vm-example/request.json \
  --output-root /tmp/half-shadow-vm-runs
```

采集固定使用 `vm` 映射，忽略环境中的 real 默认选择，也没有 backend 参数可切换为 real。若任一所需 VM PV 同时出现在 real 映射中，连接前拒绝。采集器只复用 `sample_capture_points()` 的主动读取，每个不同 PV 读取一次，没有设值、恢复、重试采样或自动回退。

运行前，需有与选定 machine 对应的虚拟 softIOC 和更新后的 VM 计算进程。原 VM 发布器只发布图像；本次将原粒子坐标中关于质心的总体标准差 `std(ddof=0)` 转为 mm，写入 profile 已有的 sigx/sigy 虚拟通道。无有效粒子或 WATCH 被禁用时发布 NaN，避免保留上一轮有效值。PV 名称、IOC DB 和替换文件生成方式保持不变；IOC 无需为此重建。VM 计算进程需重启以加载新发布逻辑。

RMS 发布等待写入完成，VM 的 Ready 状态记录 `publication.rms`。采集读取 `.vm-workbench/status.json`、`result.json` 与 runtime JSON，核对同一 session、calculation、input_version 和 result_version；采集后再核对，期间变化即拒绝计算。采到的 K1 必须与该输入格架一致，sigx/sigy 必须与该结果中的粒子 RMS 一致，且诊断源时间处于该 VM 计算的发布窗口内。IOCs 未声明 EGU 时，用已知 VM 合约补充 `1/m^2` 和 mm 并在条目 detail 中记录；EGU 与合约冲突则拒绝，不覆盖。

VM 状态不变时，旧的诊断源时间仍可能有效。程序保留原时间戳，只有通过上述版本和值校验的证据，才按 VM 计算的时间窗口比较。没有证据的普通 replay 仍使用原来的诊断年龄限制。该一致性标记是 `vm_result_matched`，不是原子采样承诺。

若 VM 运行在另一个工作区，可以指定其 runtime JSON：

```bash
python3 scripts/run_digital_shadow.py run-vm \
  --machine half \
  --request /tmp/half-shadow-vm-example/request.json \
  --vm-runtime-json /path/to/running-vm/halflinac.json \
  --output-root /tmp/half-shadow-vm-runs \
  --ready-timeout 10 --capture-timeout 5
```

此路径必须对应所连接的虚拟 IOC/VM 实例。CLI 不自动启动或重启服务；VM 正在计算时有界等待 Ready，缺少 RMS 发布记录时提示更新 VM。采样中变化或不匹配时失败，不混合工作点，也不偷偷再次采样。完整采集成功后交给原有 `run_once()`，仅在新运行目录执行独立 ELEGANT 计算。

每次成功采集额外归档 `vm_capture_evidence.json`，包含被绑定快照的哈希、VM 输入、状态、所选 RMS、结果摘要哈希和 runtime 路径。该证据和原始快照可以在 VM 停止后重放：

```bash
python3 scripts/run_digital_shadow.py replay \
  --snapshot /path/to/shadow_run/machine_snapshot.json \
  --request /path/to/shadow_run/request.json \
  --vm-evidence /path/to/shadow_run/vm_capture_evidence.json \
  --output-root /tmp/half-shadow-vm-replay
```

示例请求的入口束流是假设值，与真实 VM 束流未必相同。complete 表示链路与数据对应关系通过，不要求残差为零。要验证物理精度，应使用独立入口束流资料；RMS 发布值是原粒子分布统计量，不是裁剪图像的高斯拟合宽度。

## 8 VM 联调验证记录

2026-11-17 新增 VM 采集、版本和值匹配、real/混用映射拒绝、单位冲突、老诊断的证据校验、RMS 发布及 worktree 导入回归测试。发现某些 PYTHONPATH 排列会将 sibling main 放在当前 worktree 前面，已修正 bootstrap 的路径优先级，保留拒绝已导入不同 checkout 的检查。

| 检查 | 结果 |
| --- | --- |
| 数字影子、VM 采集、采样与 VM workbench 测试 | 56 项通过 |
| 发布与 worktree 导入定向回归 | 10 项通过 |
| 隔离 Channel Access 与 ELEGANT 集成 | 1 项通过；临时 softIOC/caRepeater、独立 loopback 端口，完成后清理 |

隔离集成先运行 ELEGANT 分段粒子跟踪，使用实际入口宏粒子的协方差作为独立输入，通过 VmPublisher 发布目标屏幕的 RMS；然后由 `run-vm` 经真实 CA 读取，驱动独立矩阵计算并比较，两平面残差均小于 `1e-6 mm`。特意等待 6 秒再采集，验证稳定 VM 的老时间戳通过版本证据匹配，归档后离线重放也通过。测试没有操作已有 VM、没有连接 real PV，也没有启动完整 GUI。

默认测试不会启动服务。需要本地复验时，确保 PATH 中有 ELEGANT、softIoc、caRepeater，并显式运行：

```bash
source scripts/setup.sh
HALF_VM_SHADOW_INTEGRATION=1 python3 -m pytest -q tests/test_digital_shadow_vm_integration.py
```

## 9 VM 工程验收

2026-11-17 已由代理按用户授权完成完整 VM worker 的隔离验收：QT02 三个强度工作点，以及 QT01 到 PRF06/07/08 的多屏比较，12 个束宽全部通过；另验证未就绪、工作点不符、单位冲突拒绝，以及停止服务后的四次无 PV 访问重放。入口使用测试 VM 的实际输入宏粒子协方差，三个 QT02 点保持入口条件相同。

具体数值、复验命令、产物位置和验收边界见 [VM 工程验收记录](VM_ACCEPTANCE_20261117.md)。新增测试默认跳过，仅在显式启用 `HALF_VM_SHADOW_INTEGRATION=1` 时启动独立临时服务。该结论适用于受支持局部段的工程与线性数值一致性；真实入口束流、生产 PV→JSON 回调、完整 GUI 操作与实机模型精度另行验证。

## 10 实机离线准备

2026-11-17 已离线盘点原工作区的历史 real 扫描、多屏测量和五个粒子文件。首选候选为 QT01 entrance → PRF08，参考入口可研究既有 PRF07 扫描；PRF07 不能同时作为独立验证目标。现有资料缺少合格 real MachineStateSnapshot、K1_total 原始元数据及已确认的 RMS 诊断定义，因此没有生成真实重放结果。

资料、六个采集通道、`m^-2` 单位别名兼容性及后续最小修改见 [实机离线准备](REAL_POC_PREPARATION.md) 和 [输入准备表](REAL_POC_INPUTS.json)。准备表不是可执行请求，未确认的入口与诊断字段保留 null。本轮未接触 real PV，也未增加 real 采集入口。
