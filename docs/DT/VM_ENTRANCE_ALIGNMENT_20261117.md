# HALF VM 工作点入口束流对齐验收

2026-11-17，使用用户完成的 VM 一次采集归档，为同一工作点提取 QT02 入口束流并完成独立 ELEGANT 离线重放。PRF06 的 X、Y 最大相对残差为 **0.001467%**，相较示例入口有明显改善。此次验证的是同一 ELEGANT 模型内的数据与计算一致性，不构成实机精度验收。

本次仅增加本地运行产物与文档。没有修改产品代码、原采集归档、VM 运行输入、调束状态机或 EPICS 写入机制；离线运行显式禁止 PV 访问，访问尝试为 0。

## 工作点与入口来源

源 GUI 作业为 `job_20261117_124935_5a2ea5ffbe`，模式 `run-vm`，状态 `complete`。VM session 为 `51274a68dc5f4632845523ffcdb2f1cc`，calculation 为 1，input/result version 均为 `51dcdf3962950513a01e2b7fd3fd62eba1db14fd63014ba75c8fa53ee39917c4`。

本次使用该计算保留的 `one.sig` 跟踪二阶矩与 `one.cen` 参考动量。QT02 紧接 DMTB，故 **DMTB 出口就是 QT02 入口**；两份 SDDS 的第 344 行（从 0 开始）均对应此处，s = 165.14642000000003 m。该处没有单独保存粒子文件，因此没有声称直接读取了 QT02 入口宏粒子。

入口协方差按以下方式读取，均为中心矩和 SI 单位：

```text
x_moments = (Sx², s12, Sxp²)
y_moments = (Sy², s34, Syp²)
E_kinetic = 0.51099895 × (sqrt(1 + pCentral²) − 1) MeV
```

已检查本地 ELEGANT 源码的 `sdds_support.c::dump_sigma()` 与 `compute_centroids.c`：s12、s34 由中心协方差输出，不需要再次减去束心乘积。

| 参数 | X | Y |
| --- | ---: | ---: |
| A | 2.216612335563751e-7 | 1.2317395612296825e-8 |
| B | 4.0461983666342694e-8 | -3.6218260607804366e-9 |
| C | 7.411193452927324e-9 | 1.589226589165098e-9 |
| β / m | 93.6509020900 | 4.84714926453 |
| α | -17.0950112020 | 1.42526326825 |
| 几何发射度 / mm·mrad | 0.00236688839733 | 0.00254116284440 |

参考 pCentral = 4298.404874854445 mc，动能为 **2195.969438216034 MeV**；原示例填写的是 2200 MeV。入口有 8768 个粒子，至 PRF06 数量不变，入口相对动量散布为 0.288361%。QT02 的 K1 = 1.402247 1/m²，沿用原采集快照。

新请求沿用现有 `independent_measurement` 枚举，表示入口来源独立于比较屏幕。Source 明确写入 `SYNTHETIC VM tracked central covariance`、计算版本和源文件 SHA256，说明它来自仿真，不能当作实机入口测量。PRF06 粒子只用于核对原诊断 RMS，未参与入口构造或拟合，`fitted_diagnostics` 为空。

## 比较结果与适用边界

残差定义为 VM 诊断减模型预测，束宽单位为 mm。

| PRF06 诊断 | 原示例预测 | 对齐后预测 | VM 诊断 | 对齐后残差 | 相对残差 |
| --- | ---: | ---: | ---: | ---: | ---: |
| X RMS | 1.73974734144 | 0.468582148074 | 0.468582376988 | 2.28914315e-7 | 0.0000488525% |
| Y RMS | 1.96343998444 | 0.103772052239 | 0.103770529900 | -1.52233893e-6 | -0.00146702% |

现有数字影子使用一阶、无耦合的投影横向矩阵；当前 VM 的 `default_order=3` 且动量散布非零。2026-11-18 已完成[同阶局部跟踪对照](VM_ORDER_COMPARISON_20261118.md)：一阶粒子跟踪与矩阵的最大束宽差为 5.55e-17 mm，三阶跟踪复现本表残差，零动量散布对照将阶次差异缩小至约 1e-10 mm。本工作点残差以动量散布参与的高阶效应为主。

此前隔离验收的 `1e-6 mm` 阈值针对零动量散布和一次输运，本表三阶 VM 与一阶预测的 Y 残差略高于该数值，因此不记录为该严格基准通过。后续同阶对照在原动量散布和零散布两组中均通过该阈值；本表原始数值保持不变，没有新增或反推物理容差。

## 版本与完整性核对

- 当前 workbench result 的完整内容哈希与原采集 evidence 中的 `vm_result_hash` 一致，session、calculation、输入版本及 Ready 状态一致。
- 跟踪 SDDS 的元件顺序、纵向位置，以及束宽、发散度、几何发射度和 tracked beta 全曲线，与原 workbench result 一致；源文件修改时间位于该次 VM 计算窗口内。
- PRF06 原始粒子的总体 RMS（ddof=0）与原采集诊断一致。
- 重放前后原采集归档和所用 VM 文件哈希不变；新运行的 14 个 manifest 产物哈希通过。
- GUI 无界面显示环境检查通过：History 能打开新运行，两项结果均为 compared；新请求导入后完整参数往返一致，没有启动任务或访问 PV。

原 VM 计算没有单独记录 `one.sig` 的历史哈希，本次绑定依据是同一 workbench 结果、文件时间、曲线和诊断核对；冻结产物已另存 SHA256，以供后续追溯。

## 产物与 GUI 使用

专用工作区内的完整本地产物位于 [runs/digital_shadow_vm_alignment/20261117_qt02](../../runs/digital_shadow_vm_alignment/20261117_qt02/)，该目录由既有规则忽略，不加入 Git：

- `request.json`：可直接通过 **Import request** 导入的入口请求。
- `alignment.json`：入口参数、版本、比较结果与原文件哈希。
- `source_run/`：原采集归档的副本，包含工作点快照和 VM evidence。
- `vm_source/`：本次冻结的跟踪 SDDS、PRF06 粒子、运行输入和 workbench 结果。
- `replays/shadow_6f89cbe5a29045baacf49bf0191c6fc6/`：新模型运行与比较结果。
- `align_vm.py`、`gui_validation.json`、`gui.png`、`SHA256SUMS`：提取执行脚本、GUI 检查记录和校验清单。

在 Digital Shadow 的 **History → Open archive** 中选择上述 `replays/shadow_6f89cbe5a29045baacf49bf0191c6fc6/`，即可查看新结果；点击 **Use VM inputs for replay** 可载入该归档的完整输入，再显式离线重放。不需要启动 VM，也无需改动当前运行中的工作点。

命令行复验可在专用工作区、现有 half_linac 环境下执行，输出使用新目录：

```bash
source scripts/setup.sh
python3 scripts/run_digital_shadow.py replay \
  --snapshot runs/digital_shadow_vm_alignment/20261117_qt02/source_run/machine_snapshot.json \
  --request runs/digital_shadow_vm_alignment/20261117_qt02/request.json \
  --vm-evidence runs/digital_shadow_vm_alignment/20261117_qt02/source_run/vm_capture_evidence.json \
  --output-root runs/digital_shadow_vm_alignment/recheck
```

这份入口请求仅绑定上述 VM 计算。其他工作点需重新核对入口来源，不能把这次提取参数默认为通用入口或实机入口。
