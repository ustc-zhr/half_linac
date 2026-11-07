# 能量前馈预测验证

第一阶段只验证一个问题：保存的 jitter 数据能否对未来 1、2、5、10 秒的
`eny_energy_actual` 给出优于“当前值保持”的预测。工具不连接 EPICS、不写控制 PV，
也不会生成可以直接加载到反馈中的生产模型。

## 建议采集

在 jitter 的 Monitor 模式中至少选择：

- `eny_energy_actual`
- `llrf18_phase`、`llrf19_phase`
- LLRF18/19 的 CH2–CH6 幅度和相位
- `fct1_peak_current`、`ict01_charge` 至 `ict04_charge`

第一轮关闭能量反馈，先采集 30–60 分钟。确认能量 PV 的更新率、有效率和时间间隔后，
再在不同时间、不同稳定工作状态重复采集。正式判断应将整次 run、最好将不同日期的
run 留作测试。只有一次 run 时，工具会采用带 purge gap 的时间前后段划分，并在报告中
将其标为初筛结果。

## 运行

使用包含 h5py 和 scikit-learn 的 half_linac 环境：

```bash
source scripts/setup.sh
python3 scripts/validate_energy_prediction.py /path/to/jitter/runs
```

默认会在第一个输入路径下创建 `energy_prediction_analysis/`，其中包含：

- `report.md`：数据质量、模型指标与当前结论
- `run_audit.csv`：采样间隔、最大间断、目标有效率和相邻不变率
- `metrics.csv`：当前值保持、9 秒周期保持、历史均值、Ridge、梯度提升和小型 MLP 的指标
- `predictions.csv`：测试集逐点预测和残差
- `summary.json`：完整机器可读结果

多个不在同一父目录的 run 可以分别传入：

```bash
python3 scripts/validate_energy_prediction.py /path/run_a /path/run_b \
  --test-run run_b
```

自定义输入通道时重复使用 `--feature`。目标能量会自动加入输入历史：

```bash
python3 scripts/validate_energy_prediction.py /path/to/runs \
  --feature llrf18_phase --feature llrf19_phase --feature ict01_charge
```

## 判读

`skill_vs_persistence` 定义为：

```text
1 - MSE(model) / MSE(persistence)
```

大于零表示模型优于当前值保持。一次数据中的正 skill 还不足以接入控制；还要检查
`skill_vs_best_baseline`，它要求模型优于当前值、历史均值和周期保持三种简单基线。
默认周期为 9 秒，可通过 `--seasonal-period` 修改或设为 0 禁用。候选模型需要在跨日期
留出的 run 上复现，并在能量反馈中经过只读影子预测。jitter HDF5 当前记录的
是一次批量采集的时间，而不是每个设备的源时间戳，因此这一阶段首先验证秒级慢漂移，
不能据此宣称具备逐脉冲前馈能力。
