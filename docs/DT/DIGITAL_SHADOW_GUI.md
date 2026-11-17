# HALF 数字影子 GUI

首版提供 HALF VM 一次采集、VM 快照离线重放、诊断比较与历史查看。real 快照和数字影子结果仅供查看；没有真实采集、实机重放、PV 写入、图像归档或多屏重建入口。

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
2. 保持 **Offline VM replay**，点击 **Run replay**。
3. 查看预测、测量、残差、状态和两平面束宽图；可展开完整快照、请求与结果。

示例入口为 assumed，测量值为合成数据，界面保留 SYNTHETIC 提示。complete 表示计算和比较条件满足，不表示物理精度通过；本版没有设置物理精度验收阈值。

表单能量为动能 MeV，发射度为**几何 mm·mrad**，不是归一化发射度。以 `A=εβ`、`B=−εα`、`C=ε(1+α²)/β` 转为 SI 协方差，现有 ShadowRequest JSON 格式保持不变。未编辑的导入协方差保持原精度；编辑一个平面不重算另一个平面。诊断单位、来源、fitted_diagnostics 和高级超时参数保留。

线路、参考元件和 VM 诊断由模型与 profile 推导。部分线路包含 RF/弯转，仍由现有 prepare_model 检查所选局部段，不代表整条线路均可用于首版预测。

## VM 一次采集

准备已有的虚拟 IOC 和更新后的 VM 计算进程，选择 **VM · capture once and predict**，显式点击 **Capture VM & predict**。GUI 不自动启动、重启或修改 VM。

VM runtime JSON 留空时使用本 worktree 的默认路径；VM 在其他工作区运行时，应选择该实例的 runtime JSON。现有后台核对 Ready、input/result version、PV 值与发布时间，不一致即失败，不通过重新采样拼接工作点。运行期间输入锁定，同一窗口只执行一个任务。

## 历史和归档

默认输出为 `runs/digital_shadow_gui/half/`，受既有 `.gitignore` 排除，可选择其他目录。每次创建唯一 job 目录：

| 内容 | 用途 |
| --- | --- |
| job.json | GUI 作业状态、时间、退出码、后台 run 目录 |
| request.json、machine_snapshot.json、可选 VM evidence | 冻结输入，不覆盖来源文件 |
| worker.pgid、stdout.log、stderr.log | 本任务进程组标记和日志 |
| shadow_<uuid>/ | 原后台的模型输入输出、result、manifest 和追溯文件 |

History 的 **Open archive** 支持既有数字影子 run 或 GUI job 目录。**Use VM inputs for replay** 自动载入请求、VM 快照和同目录证据，仍需显式运行。普通重放没有 VM 证据时继续执行原有诊断年龄限制。

**Open snapshot** 只读查看 MachineStateSnapshot。real 档案不能执行；历史发射度扫描 metadata、多屏 JSON 不自动转换。归档读取检查结果与 manifest 对应关系及所展示输入/结果的已记录哈希；坏档案显示错误，不可比较项保留空残差与原因。

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
