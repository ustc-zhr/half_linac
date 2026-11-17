# HALF 数字影子 VM 工程验收

2026-11-17，基于 `feature/digital-shadow-poc` 的产品代码 `28668fc`，完成隔离 VM 工程验收，结论为通过。四个工作点、12 个 RMS 束宽比较均满足预先设定的 `|measured - predicted| < 1e-6 mm`，最大绝对残差为 `4.440892098500626e-16 mm`。该阈值验证当前线性模型链路的数值一致性，不是实机精度指标。

用户授权由代理执行 VM 验收。检查时本机没有运行中的 HALF VM 或 softIOC，因此使用临时 softIOC、真实 VM 计算循环和独立本机 CA 端口。没有启动 GUI、连接 real PV、改动原工作区或改变现有调束状态机和 EPICS 写入机制。本次新增可复验的测试和记录，没有修改产品逻辑。

## 1 验收环境与入口条件

自动验收入口为 [test_digital_shadow_acceptance.py](../../tests/test_digital_shadow_acceptance.py)。测试调用生产代码 `start_VM.main()`，只替换运行文件路径与所选束线，将格架、ELE、运行 JSON 和输出重定向到临时目录；实际使用原有 `one`、`cleanUp`、ELEGANT、VmPublisher 和 workbench 状态发布。没有模拟传输矩阵、屏幕宽度或 Ready 结果。

临时 IOC 按既有 VM 发布计划提供标量和图像通道，CA 自动广播关闭、地址限定 `127.0.0.1`，服务端与 repeater 使用独立端口。确认全部临时 PV 与 profile 的 real PV 集合无交集。测试故意继承 `HALF_LINAC_CONTROL_BACKEND=real`，验证 `run-vm` 仍固定使用 vm 映射。

采用现有格架中的两个非 RF、非弯转段，参考面均为指定四极铁 entrance。测试束流由 ELEGANT 生成，动能 2200 MeV、4096 个宏粒子、固定随机种子、零动量散布、一次横向输运；未开启集体效应或损失预测。每次从入口 `one.bun` 的实际 x/xp/y/yp 粒子计算总体协方差，作为独立预测的入口输入，没有用目标屏幕拟合。请求中的 `independent_measurement` 在这里仅指独立读取模拟入口粒子，provenance 明确记录合成仿真来源，不表示实机测量。

三个 QT02 点的入口协方差相同。QT02 是短段基准测试的便利选择；QT01 双四极铁、多屏测试同时验证参考点与屏幕可按现有格架选择。

## 2 工作点与诊断比较

表中束宽为粒子 RMS，单位 mm；预测与 VM 经 CA 读取的值在所列小数位一致。K1 单位为 `1/m^2`。

| 工作点 | 四极铁 K1 | 诊断 | sigx | sigy |
| --- | --- | --- | ---: | ---: |
| QT02 基准 | QT02=1.402247 | PRF06 | 2.103859593 | 3.391438829 |
| QT02 减弱 | QT02=1.252247 | PRF06 | 2.138029394 | 3.348480223 |
| QT02 增强 | QT02=1.552247 | PRF06 | 2.069726317 | 3.434442792 |
| QT01 多屏 | QT01=-1.315043；QT02=1.402247 | PRF06 | 2.945741910 | 1.996063283 |
| QT01 多屏 | 同上 | PRF07 | 1.868529056 | 1.133930911 |
| QT01 多屏 | 同上 | PRF08 | 0.943886091 | 1.305155563 |

每个工作点经过 VM 粒子跟踪与发布 → `run-vm` 一次 CA 采集 → 独立 ELEGANT 矩阵计算 → 诊断对应比较。QT02 强度增大时，屏幕的水平方向束宽减小、垂直方向束宽增大，预测与跟踪响应一致。四个工作点具有不同输入版本。

| 工程验收项 | 结果 |
| --- | --- |
| VM 计算、RMS 发布与 Ready 版本一致 | 四点通过；使用生产 VM 循环发布的状态与结果 |
| 同一次工作点的快照驱动独立模型 | 四点 complete；必要 K1 与对应 VM 输入匹配 |
| 只读采集与计算 | 四点采集前后所需 PV 的值和源时间戳相同，runtime JSON 字节相同 |
| 固定入口的强度扫描 | 三点入口协方差相同，两平面预测与跟踪响应通过断言 |
| 多四极铁、多屏 | QT01/QT02 两个输入、三个屏幕六个束宽全部对应 |
| 追溯完整性 | 四次采集运行及四次重放的 manifest 产物 SHA256 全部匹配 |
| 服务停止后的重放 | 四组通过；禁止 `PV/caget/caput/caget_many/caput_many`，比较结果和 input_hash 与原运行相同 |
| 进程清理 | 四个 VM worker 正常退出，临时 softIOC/repeater 已终止；原有 repeater 保持运行 |

采集器逐 PV 只读一次、不写 PV 的细粒度约束另由定向测试验证。本次运行前后的值和时间戳检查覆盖正常运行中有无可见写入副作用，不构成 CA 原子采样保证。

## 3 异常拒绝与回归

以下故障仅注入临时验收实例。每项 CLI 返回 1，且未建立成功计算目录。

| 故障 | 实际拒绝原因 |
| --- | --- |
| VM 状态为 Calculating | `VM did not become ready`，在指定等待时间内结束 |
| K1 PV 与已完成计算的输入相差 0.1 | `QT02/K1 does not match the completed VM result` |
| sigy 的 EGU 改为 A | `Missing or unusable VM sample PRF06/sigy` |

| 检查 | 结果 |
| --- | --- |
| 数字影子、VM 采集、PV 采样、VM workbench | 56 passed |
| 发布与 worktree 导入定向回归 | 10 passed，43 deselected |
| 新增完整 worker 自动验收 | 1 passed，21.45 s；含上述四点、三项故障和四次离线重放 |
| `bash scripts/check.sh` | 通过 |

未运行完整测试集。已知基线失败及早期验证见 [PoC 说明](DIGITAL_SHADOW_POC.md#6-验证与下一步)，本次没有修订其结论。

## 4 产物与复验

完整本地产物保存在专用 worktree 的 `runs/digital_shadow_acceptance/20261117/`，该目录已由既有 `.gitignore` 排除，不加入源码：

- `acceptance.json`：工作点、12 个比较结果、输入版本、故障原因和服务退出记录。
- `pytest.log`：本次完整 worker 验收日志。
- `vm_acceptance.tar.gz`：入口粒子、原始运行、快照、VM 版本证据、请求、独立模型输入输出、manifest、重放结果和日志。
- `SHA256SUMS`：归档校验。

归档 SHA256 为 `1bc0d675a809fa1ba0e008f1ce1e4604f3421fdec50645871df042ae6fad05ef`。归档内保留当时的绝对运行路径作为追溯信息；解压到新位置时，重放参数应指向新的实际文件路径。

在专用 worktree 根目录、已安装 ELEGANT/softIoc/caRepeater 和项目 Python 依赖的环境中执行：

```bash
source scripts/setup.sh
HALF_VM_SHADOW_INTEGRATION=1 python3 -m pytest -q -p no:cacheprovider \
  --basetemp=/tmp/half-shadow-acceptance-new \
  tests/test_digital_shadow_acceptance.py
```

使用新的 basetemp 目录保留每次结果。默认不设置该环境变量时测试跳过，不启动任何服务。测试程序只设置、故障注入和终止其自行创建的临时实例。

## 5 验收范围与下一阶段

本次证明现有 HALF 格架的受支持局部段可以完成 VM 状态驱动预测、诊断对应、异常拒绝和可追溯重放。两个计算均使用 ELEGANT，入口粒子与线性矩阵比较验证的是实现和数据链路一致性，不能作为独立物理引擎或实机模型准确度的交叉验证。

临时 IOC 的四极铁值由测试装置按 VM 输入初始化，未覆盖生产 `pv_server` 的 PV→运行 JSON 回调、完整 IOC DB、GUI 点击流程、RF/弯转/耦合/空间电荷、真实诊断图像处理或 real 采集。三个 QT02 点分别启动独立临时 worker，验证固定入口下的静态工作点，不代表持续会话中的在线变更验收。采样中版本变化已有定向测试，本次未用实时竞争故障替代它。

工程验收完成后，下一阶段先整理有来源的真实入口束流、诊断 RMS/单位/时间语义和带必要回读的样例快照，再安排获明确授权的真实机器一次只读采集。物理容差应依据诊断不确定度单独约定，不能沿用本次线性一致性阈值。现有 real/vm 后端继续独立，调束状态机与写入安全机制保持原样。
