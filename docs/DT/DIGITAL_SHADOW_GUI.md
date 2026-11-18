# HALF 数字影子 GUI

提供 HALF VM 一次采集、real 映射的离线演练、显式 real 只读采集、可选单帧图像归档、带来源的快照重放、诊断对应和历史查看。旧 real 档案没有来源证据时仍只读查看；没有 PV 写入或多屏重建入口。当前 real 链路仅完成模拟读取器离线验收，未连接实机。

## 启动

实现位于数字影子专用 worktree，尚未合并主工作区：

```bash
conda activate half_linac
cd /home/zhanghaoran/gitproj/half_linac-dt
bash scripts/runMe
```

在 Control Room 选择 HALF，点击 **VA & Optimization → Digital Shadow**。重复点击沿用现有置前/停止流程。入口使用启动器当前 Python 解释器和主题；IRFEL 下不可用。

也可在同一 worktree 单独执行：

```bash
python3 src/apps/digital_shadow/main.py --machine half
```

首版使用 Linux/WSL 的 POSIX 进程组及现有 PyQt5、Matplotlib、SDDS、ELEGANT 环境。默认离线；启动、编辑和查看历史不访问 PV。Control Room 选择 real 不会令数字影子自动连接 real。

## 离线演示与请求

1. 点击 **Load VM example**，生成新的合成 VM 快照并填入表单。
2. 保持 **Offline snapshot replay**，点击 **Run replay**。
3. 查看预测、测量、残差、状态和两平面束宽图；可展开完整快照、请求与结果。

示例入口为 assumed，测量值为合成数据，界面保留 SYNTHETIC 提示。complete 表示计算和比较条件满足，不表示物理精度通过；本版没有设置物理精度验收阈值。

若出现 `beam_provenance is required`，表示 Entrance beam 的 Source 为空。离线演示先点击 **Load VM example**，Source 会自动填入 SYNTHETIC 来源，再点击 Run replay；不需要先启动 VM。手动填写入口参数时，Source 应说明参数的实际来源。当前 GUI 已将空来源提示改为明确的字段名称和示例按钮指引，并自动定位到 Source；后台仍要求有效来源，不自动补造测量依据。

表单能量为动能 MeV，发射度为**几何 mm·mrad**，不是归一化发射度。以 `A=εβ`、`B=−εα`、`C=ε(1+α²)/β` 转为 SI 协方差，现有 ShadowRequest JSON 格式保持不变。未编辑的导入协方差保持原精度；编辑一个平面不重算另一个平面。诊断单位、来源、fitted_diagnostics 和高级超时参数保留。

线路、参考元件和 VM 诊断由模型与 profile 推导。部分线路包含 RF/弯转，仍由现有 prepare_model 检查所选局部段，不代表整条线路均可用于首版预测。

## VM 一次采集

准备已有的虚拟 IOC 和更新后的 VM 计算进程，选择 **VM · capture once and predict**，显式点击 **Capture VM & predict**。GUI 不自动启动、重启或修改 VM。

VM runtime JSON 留空时使用本 worktree 的默认路径；VM 在其他工作区运行时，应选择该实例的 runtime JSON。现有后台核对 Ready、input/result version、PV 值与发布时间，不一致即失败，不通过重新采样拼接工作点。运行期间输入锁定，同一窗口只执行一个任务。

## 历史和归档

采集模式可勾选 **Save one original frame per screen**，每屏保存一帧，原始类型加 NPZ 无损压缩。默认共享图片池上限 10 GiB，界面显示占用和磁盘余量；不自动删除，重放引用同一份图片。背景通过逐屏 JSON 映射选择。展开 **Saved images · ROI RMS [mm]** 查看独立结果；规则、CLI 和备份边界见[单帧图像归档](IMAGE_ARCHIVE_20261118.md)。默认离线重放不采图。

含图像的新结果可在表格上方切换 **SIZE / scalar diagnostics** 与 **Image ROI RMS**，图形随来源切换。已核验 VM 图像发布和工作点、且 ROI/边缘/像素条件满足时才显示图像差值；受限、real 或合成演练保留空残差。四屏验收示例可无需 VM 直接从 History 打开，见[图像 RMS 比较](IMAGE_COMPARISON_20261118.md)。旧归档仍可查看，新增比较需显式重放。

没有真机或 VM 时，可直接选择 **Offline real capture demo · NO PV**，点击 **Load real demo**，再点击 **Run offline real demo**。默认四屏请求走正式采集器、快照与 ELEGANT，仅用注入的合成读取器替换 PV。正常结果为预测完成、比较 partial：Gaussian/unknown 与模型 RMS 定义不兼容，残差留空。界面和归档明确标记 SYNTHETIC；详见 [real 离线实现与验收](REAL_CAPTURE_OFFLINE_20261118.md)。

**Real · capture once · READ ONLY** 为现场显式入口，只有点击 **Read real PVs & predict** 才会读取真实 PV；合成演示入口来源会被拒绝用于该模式。没有真机时使用上面的 NO PV 模式，不需要启动 IOC 或 VM。

默认输出为 `runs/digital_shadow_gui/half/`，受既有 `.gitignore` 排除，可选择其他目录。每次创建唯一 job 目录：

| 内容 | 用途 |
| --- | --- |
| job.json | GUI 作业状态、时间、退出码、后台 run 目录 |
| request.json、machine_snapshot.json、VM/real capture evidence | 冻结输入、原始单位和观测来源，不覆盖来源文件 |
| worker.pgid、stdout.log、stderr.log | 本任务进程组标记和日志 |
| shadow_<uuid>/ | 原后台的模型输入输出、result、manifest 和追溯文件 |

History 的 **Open archive** 支持既有数字影子 run 或 GUI job 目录。**Use archived inputs for replay** 自动载入请求、快照和同目录证据，仍需显式运行。新 real 归档需配套 real_capture_evidence.json，重放不再连接设备；旧 real 档案没有该证据时保持 view only。普通 VM 重放没有 VM 证据时继续执行原有诊断年龄限制。

**Open snapshot** 只读查看 MachineStateSnapshot。缺少 real_capture_evidence 的旧 real 档案不能执行；历史发射度扫描 metadata、多屏 JSON 不自动转换。归档读取检查结果与 manifest 对应关系及所展示输入/结果的已记录哈希；坏档案显示错误，不可比较项保留空残差与原因。

## 停止与退出

Stop、关闭窗口和启动器停止共用清理路径：终止本作业拥有的独立进程组，1 秒后强制清理仍存活的后代。关闭窗口等待清理完成，应用退出有强制清理兜底；不结束已有 VM 服务。

中断写入 GUI job 记录，原始产物保留。后台被终止留下的未完成 manifest 保持原样，GUI 展示 interrupted，不当作完成结果。现有调束状态机和 EPICS 写入机制保持不变。

## 验证记录

2026-11-17 完成：

| 检查 | 结果 |
| --- | --- |
| GUI、数字影子、采样、VM workbench、bootstrap 定向回归 | 78 passed；其中 GUI 20 项 |
| GUI/ELEGANT 生命周期集成 | 4 项通过：离线重放、Stop、关闭、实际 ManagedProcessGroup 停止；包含忽略 SIGTERM 的后代清理 |
| 私有 CA/VM CLI 和 GUI 集成 | 2 项通过；GUI 两平面残差均小于 1e-6 mm，归档后重放成功 |
| 仓库静态检查 | 通过 |
| WSLg 桌面 | 从 Control Room 打开窗口，并用合成 VM 快照完成 ELEGANT 重放；截图已检查 |

GUI 单元测试禁止 PV 访问，CA 集成使用临时 loopback 端口和 softIOC。未连接真实机器。默认集成测试跳过，复验需显式启用：

```bash
QT_QPA_PLATFORM=offscreen python3 -m pytest -q tests/test_digital_shadow_gui.py
HALF_VM_SHADOW_INTEGRATION=1 QT_QPA_PLATFORM=offscreen python3 -m pytest -q \
  tests/test_digital_shadow_gui_integration.py tests/test_digital_shadow_vm_integration.py
```

本地截图和验收产物保存在 `runs/digital_shadow_gui_acceptance/20261117/`。模型适用范围、缺失的实机资料及已知基线失败继续见 [PoC 说明](DIGITAL_SHADOW_POC.md) 和 [实机准备记录](REAL_POC_PREPARATION.md)。本次不宣称完整测试集全部通过。

## VM 工作点的入口束流对齐

用户完成实际 GUI 的 VM 一次采集后，已从同次计算的跟踪二阶矩提取 QT02 入口参数，并用原采集快照离线重放。PRF06 的 X、Y 相对残差分别约为 0.0000489% 和 -0.001467%，GUI 归档打开与请求导入检查通过。新请求、原始来源和重放结果保存在 `runs/digital_shadow_vm_alignment/20261117_qt02/`；具体数值、模型适用边界及 GUI 打开步骤见 [入口对齐验收](VM_ENTRANCE_ALIGNMENT_20261117.md)。该请求仅适用于记录中的同一次 VM 计算。

后续[同阶对照验收](VM_ORDER_COMPARISON_20261118.md)已确认一阶矩阵与一阶粒子跟踪一致，原微小残差以动量散布参与的高阶效应为主。此次验收仅增加离线产物和文档，GUI 行为及原结果保持不变。

## PRF09 VM 束宽接入

2026-11-18 已补齐 PRF09 的 VM sigx/sigy 映射，PV 分别为 `HALF:IN:FLAG:PRF09:sigx`、`HALF:IN:FLAG:PRF09:sigy`。现有 IOC 生成器和 VM RMS 发布器按 profile 自动纳入，未修改发布器、IOC 模板或写入安全机制。

已有运行实例需加载新配置：先停止 VM Runtime 和 softIOC，再依次点击 **Start softIOC**、**Start VM Runtime**，等待 Ready，并重新打开 Digital Shadow。无需重编译 IOC。需要四屏任务时可选择 `ALL_MAIN`、`QT01 entrance`，选取 PRF06/07/08/09 两平面诊断；入口参数仍需有明确来源，Load VM example 继续保留原有单段合成演示，不自动变成当前 VM 的入口。

新增通道会改变 VM profile 签名。历史归档可继续只读查看，旧快照在当前配置下重放会被现有签名检查拒绝；使用原版本配置重放或重新采集快照，不能改写历史签名绕过检查。

本次 43 项 VM/GUI 回归、2 项私有 CA/ELEGANT 集成和 `scripts/check.sh` 均通过。四屏 GUI 集成包含 8 项 compared，最大绝对残差 8.88e-16 mm，归档后重放成功；使用生产 IOC 模板和实际 ELEGANT 粒子数据，零动量散布、一次输运。临时服务仅监听随机 loopback 端口，当前 VM/IOC 运行文件未改动。产物保存在 `runs/digital_shadow_prf09_mapping/20261118/`。
