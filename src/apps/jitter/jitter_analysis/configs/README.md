# 配置 JSON 说明

`configs/` 目录用于存放 GUI 读取的 PV 配置 JSON 文件。

当前默认文件是 [irfel_pvlist.json](irfel_pvlist.json)。GUI 启动时会优先自动加载这个文件名，对应逻辑在 [main_window.py](../src/jitter_analysis/gui/main_window.py)。如果你在 `configs/` 里新增了其他 JSON 文件，也可以在 GUI 中手动加载。

## 推荐流程

1. 复制 [irfel_pvlist_v2.example.json](irfel_pvlist_v2.example.json) 或一份已经可用的现有配置。
2. 另存为新的文件名，例如 `configs/my_machine_pvlist.json`。
3. 修改 `machine`、`groups`、`knobs`、`objects` 和 `presets`。
4. 在 GUI 中加载之前，先运行下面的校验命令。
5. 如果你希望 GUI 启动时自动加载新文件，需要把它命名为 `irfel_pvlist.json`，或者修改 [main_window.py](../src/jitter_analysis/gui/main_window.py) 里的默认路径。

## 顶层字段

每个配置文件都必须包含以下顶层 key：

- `schema_version`
- `machine`
- `defaults`
- `groups`
- `knobs`
- `objects`
- `presets`

这些字段由 [validator.py](../src/jitter_analysis/config/validator.py) 检查。缺任意一个都会加载失败。
加载器还会校验常用嵌套字段、重复 ID、正数采样参数、非负 settle 参数和 knob limits。建议在提交配置前运行文末的校验命令。

## 最小结构

```json
{
  "schema_version": "2.0",
  "machine": {
    "name": "My Machine",
    "facility": "My Facility",
    "description": "PV library for online acquisition"
  },
  "defaults": {
    "acquisition": {
      "shot_interval_sec": 0.2,
      "sample_count": 200,
      "timeout_sec": 1.0,
      "mode": "poll"
    },
    "scan": {
      "settle_mode": "fixed_delay",
      "settle_delay_sec": 0.5,
      "sample_count_per_step": 20,
      "restore_initial_value": true,
      "max_wait_sec": 3.0
    },
    "storage": {
      "format": "hdf5",
      "save_raw_data": true,
      "save_analysis_summary": true
    },
    "safety": {
      "confirm_before_write": true,
      "abort_on_disconnection": true
    }
  },
  "groups": [],
  "knobs": [],
  "objects": [],
  "presets": []
}
```

## groups 配置规则

`knobs` 和 `objects` 里的每个 `group` 都必须能在 `groups[].id` 里找到对应项，否则加载器会报错。

建议的 group 条目：

```json
{
  "id": "bpm_x",
  "label": "BPM X",
  "kind": "object",
  "color": "#20639b",
  "order": 60
}
```

注意：

- `id` 建议使用稳定的 `snake_case`。
- `kind` 对控制量组写 `knob`，对读变量组写 `object`。
- `order` 的语义是界面分组排序。当前代码里保留了这个字段，但还没有在所有界面位置都实际按它排序。

## knobs 配置规则

`knobs` 用来描述可写的控制 PV，也就是会参与扫描或写入的那些量。

当前加载器实际依赖这些字段：

- `id`
- `name`
- `group`
- `write_pv`
- `readback_pv`
- `unit`
- `access`
- `limits.low`
- `limits.high`
- `step_hint`
- `settle.mode`
- `settle.delay_sec`
- `settle.readback_tolerance`
- `settle.max_wait_sec`

示例：

```json
{
  "id": "hc01_current",
  "name": "HC01",
  "group": "steering_x",
  "write_pv": "IRFEL:PS:HC01:current:ao",
  "readback_pv": "IRFEL:PS:HC01:current:ai",
  "unit": "A",
  "access": "rw",
  "limits": {
    "low": -5.0,
    "high": 5.0
  },
  "step_hint": 0.05,
  "settle": {
    "mode": "fixed_delay",
    "delay_sec": 0.5,
    "readback_tolerance": 0.01,
    "max_wait_sec": 3.0
  },
  "tags": [
    "orbit",
    "horizontal"
  ],
  "note": "Optional operator note"
}
```

注意：

- `limits` 会在扫描时用于拦截越界写入。
- `step_hint` 会被 GUI 用来生成建议扫描范围。
- `readback_pv` 最好是数值型 PV，因为扫描逻辑会把它按 `float` 来读。
- 如果 `readback_pv` 没有在 `objects` 中显式出现，加载器会自动派生一个只读 object，ID 形如 `<knob_id>__readback`，并加上 `readback`、`knob_readback` tags。

## objects 配置规则

`objects` 用来描述只读采样 PV，也就是监测量、观测量和分析输入。

当前加载器实际依赖这些字段：

- `id`
- `name`
- `group`
- `read_pv`
- `unit`
- `precision`
- `kind`
- `access`
- `analysis.jitter`
- `analysis.correlation`
- `analysis.spectrum`

可选字段：

- `value_reducer`
- `capture_mode`
- `waveform_sample_interval_sec`

示例：

```json
{
  "id": "bpm01_x",
  "name": "BPM01 X",
  "group": "bpm_x",
  "read_pv": "IRFEL-BI:BPM01:BPM_PX2",
  "unit": "mm",
  "precision": 4,
  "kind": "scalar",
  "access": "ro",
  "value_reducer": "none",
  "analysis": {
    "jitter": true,
    "correlation": true,
    "spectrum": true
  },
  "tags": [
    "bpm",
    "orbit",
    "horizontal"
  ],
  "note": "Optional operator note"
}
```

注意：

- 默认情况下，采样逻辑会把 `result.value` 当成标量处理。
- `id` 必须在 `objects` 中唯一；`groups`、`knobs` 和 `presets` 也有同样的唯一 ID 要求。
- 如果不写 `capture_mode`，默认就是 `"scalar"`，也就是沿用现在的标量采样与 jitter/correlation/spectrum 链路。
- 如果某个 PV 返回的是 waveform/数组，但你只关心它的均值，可以给这个 object 加上 `"value_reducer": "mean"`。
- `value_reducer: "mean"` 会把返回数组里的有限数值先求平均，再作为这个 object 的单个采样值进入 jitter/correlation/spectrum。
- 如果不写 `value_reducer`，默认就是 `"none"`，也就是要求这个 PV 本身是数值型标量。
- 如果你要保存原始波形并启用新的 Waveform 分析页，需要设置 `"capture_mode": "waveform"`，并同时提供正数 `"waveform_sample_interval_sec"`。
- `capture_mode: "waveform"` 的 object 目前只在 Monitor 模式下参与波形采集与分析；Single Knob 和 Random Multi-Knob 仍然只支持标量 object。
- `capture_mode: "waveform"` 时，`value_reducer` 必须保持为 `"none"`。如果你既想保留原始波形，又想把同一 PV 的均值送进旧分析页，请在 `objects` 里为同一个 `read_pv` 配两个不同的 object。
- 字符串 PV 目前仍然不适合直接加入这套配置格式。

数组 PV 求均值示例：

```json
{
  "id": "bpm_profile_mean",
  "name": "BPM Profile Mean",
  "group": "bpm_x",
  "read_pv": "IRFEL:DIAG:BPM:PROFILE",
  "unit": "a.u.",
  "precision": 4,
  "kind": "waveform",
  "access": "ro",
  "value_reducer": "mean",
  "analysis": {
    "jitter": true,
    "correlation": true,
    "spectrum": true
  },
  "tags": [
    "bpm",
    "waveform",
    "mean"
  ],
  "note": "Use the waveform average as the scalar analysis input"
}
```

原始波形采集示例：

```json
{
  "id": "ps1_waveform",
  "name": "PS1 Waveform",
  "group": "radiation",
  "read_pv": "IRFEL:PS1:WAVE",
  "unit": "V",
  "precision": 4,
  "kind": "waveform",
  "access": "ro",
  "capture_mode": "waveform",
  "waveform_sample_interval_sec": 2.5e-9,
  "value_reducer": "none",
  "analysis": {
    "jitter": false,
    "correlation": false,
    "spectrum": false
  },
  "tags": [
    "waveform",
    "power_source"
  ],
  "note": "Saved into /waveforms and shown in the Waveform analysis tab"
}
```

## presets 配置规则

`presets` 不是必须非空，但顶层 `presets` 这个 key 必须存在，即使它的值是空列表 `[]`。

定时采样 preset 示例：

```json
{
  "id": "bpm_fast_jitter",
  "name": "BPM Fast Jitter",
  "mode": "timed_acquisition",
  "targets": [
    "bpm01_x",
    "bpm01_y"
  ],
  "shot_interval_sec": 0.1,
  "sample_count": 500
}
```

单 knob 扫描 preset 示例：

```json
{
  "id": "hc01_orbit_scan",
  "name": "HC01 Orbit Scan",
  "mode": "knob_scan",
  "knob_id": "hc01_current",
  "scan_values": [
    -0.2,
    -0.1,
    0.0,
    0.1,
    0.2
  ],
  "settle_delay_sec": 1.0,
  "sample_count_per_step": 20,
  "targets": [
    "bpm01_x",
    "bpm01_y"
  ]
}
```

注意：

- `preset.targets` 必须引用 `objects` 数组里已有的 `id`。
- `preset.knob_id` 必须引用 `knobs` 数组里已有的 `id`。

这些规则由 [validator.py](../src/jitter_analysis/config/validator.py) 检查。

## 自动派生的 readback object

如果某个 knob 定义了 `readback_pv`，加载器会自动为它派生一个 object，除非 `objects` 里已经存在相同的 `read_pv`。对应逻辑在 [loader.py](../src/jitter_analysis/config/loader.py)。

这个行为对 GUI 很方便，但有一个容易忽略的限制：

- 派生 object 是在 preset 校验之后才加进去的。
- 因此 `preset.targets` 不能直接引用派生出来的 readback id，除非你同时在 `objects` 里显式写了一份。

如果你希望 knob 的 readback 能被 preset 直接引用，建议把它作为普通 object 明确写入 `objects`。

## 命名建议

- `id` 要稳定，不要频繁改名。改了以后会影响保存的 preset 和界面选择状态。
- `knobs` 内部的 `id` 应唯一，`objects` 内部的 `id` 也应唯一。
- `name` 建议简短，GUI 列表和图上都会显示它。
- 能复用已有 `group` 就尽量复用，只有在引入新类别时再新增 group。

## 校验命令

在仓库根目录执行：

```bash
python -c 'import sys; sys.path.insert(0, "src"); from jitter_analysis.config.loader import load_config; load_config("configs/my_machine_pvlist.json"); print("OK")'
```

把 `configs/my_machine_pvlist.json` 替换成你的实际文件名即可。

## 常见失败原因

- 少了某个顶层必填 key。
- `group` 引用了一个没有在 `groups` 中定义的 id。
- `preset.targets` 引用了一个不在 `objects` 里的 id。
- `preset.knob_id` 引用了一个不在 `knobs` 里的 id。
- 配置里加入了不能转成 `float` 的非数值型 PV。
- 重复使用已有 `id`，导致 GUI 选择或绘图时发生冲突。
