# HALF GUI Style Guide

本文档描述当前 `half_linac` 软件包的通用 GUI 风格，用于后续 Codex 或人工开发新的 PyQt 工具、补充现有功能时保持一致。

适用范围：

- `src/apps/` 下的 PyQt 操作员应用
- 与控制室、束流诊断、扫描、分析、优化相关的新窗口
- 需要迁移到统一 HALF 风格的旧 `.ui` 界面

参考实现：

- `src/apps/launcher/main.py`
- `src/apps/beam_monitor/main.py`
- `src/apps/energy_spectrum/main.py`
- `src/apps/bba/main.py`
- `src/apps/emit_measure/main.py`
- `src/apps/orbit_display/main.py`
- `src/apps/jitter/jitter_analysis/src/jitter_analysis/gui/theme.py`
- `src/apps/jitter/jitter_analysis/src/jitter_analysis/gui/main_window.py`

## 风格定位

HALF GUI 是面向操作员和束流调试的工作台界面，不是展示型网站或演示页。

核心气质：

- 克制、工程化、低干扰。
- 信息密度适中，优先支持长期盯屏、快速判断状态和重复操作。
- 视觉重点来自状态、数据和当前动作，不来自装饰性背景。
- 默认按 VM / offline / internal use 的安全边界设计；涉及实机写入时必须在界面上清楚表达状态和限制。

避免：

- 大面积彩虹按钮、营销式 hero、插画背景、渐变装饰球。
- 过多说明文字常驻在主界面。
- 日志窗口永远占用主要空间。
- 只靠颜色表达危险操作。

## 总体布局

首选结构是：

1. 顶部 summary/header
2. 状态条或运行摘要
3. 主工作区
4. 可折叠或可切换日志

### 顶部 Header

Header 用一个紧凑横条表达应用身份和全局动作。

建议：

- 左侧放应用名，字号约 `22-23px`，字重 `700`。
- 副标题只放短状态，例如 `Status: Development / Internal Use`、当前工作区或模式；不要写长篇帮助。
- 右侧放全局动作：主题切换、日志开关、关闭/停止类动作。
- Header 内边距约 `16px 12px`，整体圆角 `14px`。
- 主题切换按钮固定 `32x32px`，使用简单月亮/太阳符号或图标。

### 状态条

状态条是当前风格最稳定的组件，应优先复用。

结构：

- `QWidget#statusStrip`
- 内部多个 `QFrame#statusItem`
- item 之间使用 `QFrame#statusSeparator`
- 每个 item 内有短标题和粗体值

规范：

- 标题使用短全大写或短词：`MODE`、`ACTIVE`、`CORE`、`TOOLS`、`CONNECTION`、`CONFIG`、`CURRENT`、`PV`、`PROFILE`。
- 每个状态项左侧使用 `4px` 直线色条，不使用圆角胶囊作为主要状态表达。
- 默认项宽 `112-120px` 起步，内容过长时允许换行。
- 标题字号 `9-10px`，字重 `700`；值字号 `13-14px`，字重 `700`。
- 状态色只保留 `subtle / info / success / warning / danger`。

### 主工作区

根据功能选择两种布局：

- 控制台型：上方 summary，下面 2-3 个控制卡片或分组，适合 Control Room、Beam Monitor、Orbit Display。
- 分析工作台型：左侧配置/对象选择，右侧任务设置、运行、分析 tabs，适合 Jitter Analysis、BBA、Emit Measure。

建议：

- 根布局边距 `12px`，主区块间距 `8-10px`。
- 主要 panel/card 圆角 `14px`，边框 `1px`。
- 左侧配置栏最小宽度约 `390-400px`。
- 分隔器使用 `QSplitter`，禁止子控件折叠导致工作区消失。
- 图表是主内容时，应给图表更高 stretch，控制项保持紧凑。

## 主题系统

当前项目有两个主主题方向：

- `night_shift` / dark：控制室深色主题。
- `control_room` / light：低眩光暖中性色主题。

新 GUI 应至少支持这两种主题，结构和间距保持一致，只替换 token。

### 基础颜色

深色主题推荐 token：

```text
window_bg       #0f1519
panel_bg        #172027
section_bg      #172027
section_border  #24333d
input_bg        #10171c
text_primary    #e6edf2
text_muted      #99a9b5
focus/success   #45d0bc
warning         #e4b86f
danger          #ff6b6b 或 warning 色用于受限写入
```

浅色主题推荐 token：

```text
window_bg       #f2ede5 / #f2ede6
panel_bg        #fffaf3 / #fffdf9
section_bg      #fffdf9
section_border  #ddd4c7
input_bg        #fffdf8 / #fffdf9
text_primary    #102033 / #2d3940
text_muted      #6f6253 / #7c7368
focus/success   #2d7f6d
warning         #a97118
danger          #8f3d28
```

原则：

- 深色主题技术感强，但不要高饱和蓝紫。
- 浅色主题使用暖中性低眩光，不使用纯白蓝办公软件感。
- 同一屏不要引入过多强调色。常规强调使用青绿，风险或限制使用黄/琥珀，危险使用红棕。
- 图表颜色可以比控件更亮，但仍要和主题 token 对齐。

### 字体

统一使用：

```text
"IBM Plex Sans", "Source Han Sans SC", "Segoe UI", sans-serif
```

日志或原始数据使用：

```text
"JetBrains Mono", "Cascadia Mono", "Consolas", monospace
```

字号建议：

- 应用标题：`22-23px`
- panel 标题：`15px`
- 常规控件：`12-13px`
- 状态标题：`9-10px`
- 日志：`12px`

不要随窗口宽度缩放字体。需要适配时调整布局、换行或最小宽度。

## 控件规范

### Panel / Card / GroupBox

用途：

- `summaryPanel`：顶部摘要或 app header。
- `plotCard`：图表容器。
- `controlCard`：控制面板。
- `resultCard`：结果与读数。
- `workspaceFrame`：tabs 外层工作区。
- `QGroupBox[themeSection="main"]`：Jitter Analysis 风格的主分组。

样式：

- 背景使用 `panel_bg` 或 `section_bg`。
- 边框使用 `section_border`。
- 圆角 `14px`。
- 标题 `15px`、`700-800`。
- 卡片内部边距 `10-12px`。

避免在 panel 中再堆多层装饰卡片。需要分隔时优先用标题、间距、细线或 tabs。

### Button

默认按钮：

- 圆角 `12px`。
- 高度 `32-38px`。
- 字号 `12px`，字重 `700`。
- 横向 padding `10-12px`。

紧凑按钮：

- 属性可用 `compact="true"` 或 `compactControl="true"`。
- 高度 `28px` 左右。
- 用于 header、表格上方工具条、plot 控制。

角色化按钮：

- `role="primary"`：主要提交动作。
- `role="control"`：开始、应用、执行类动作。
- `role="diagnostic"`：检查、加载、浏览、选择类动作。
- `role="subtle"`：低权重辅助动作。
- `role="danger"`：停止、清空、删除、恢复前需要谨慎的动作。
- `role="statusAction"`：状态条内的紧凑动作，如 `Check EPICS`、`Start`、`Stop`。

按钮文字：

- 使用短动词短语：`Start`、`Stop`、`Check EPICS`、`Load PVs`、`Run Browser`。
- 不在按钮上写解释句。
- 危险动作除了颜色，还要使用明确动词，例如 `Stop`、`Clear Selection`、`Shutdown Apps`。

### Inputs

输入类控件：

- `QLineEdit`、`QComboBox`、`QSpinBox`、`QDoubleSpinBox` 使用同一输入背景和边框。
- 圆角 `10-12px`。
- 高度约 `28-32px`。
- focus 边框使用 `focus` 色。
- 下拉列表背景、选中项颜色必须跟随主题。

表单布局：

- 标签短而稳定，不写完整说明句。
- 多个相关数值使用网格或紧凑横排。
- 单位放 suffix 或相邻短 label，不放到长描述里。

### Tabs

Tabs 用于真正的工作模式或结果视图，不用于装饰。

规范：

- tab 圆角只在上方：`border-top-left/right-radius: 10px`。
- selected tab 背景与内容 panel 背景一致。
- tab 字号 `12px`，字重 `700`。
- 工作区主 tabs 可用较宽 `min-width: 96px`。
- 分析 tabs 可更紧凑，适合 `Response / Waveform / Sensitivity / Jitter / Correlation / Spectrum`。

### Logs

日志默认不占据主视觉空间。

建议：

- 使用 header 里的 `Log` 开关或折叠区。
- `QPlainTextEdit` 设置只读和最大 block 数。
- 日志字体使用 monospace，字号 `12px`。
- Placeholder 写诊断类别，不写操作说明，例如 `Warnings, caput results, timeout, disconnected PVs`。

## 图表规范

图表是诊断应用的主要内容，需要和控件主题同步。

建议：

- 图表容器使用 `plotCard` 或主题化 `analysisSection`。
- Matplotlib / pyqtgraph 背景使用 `plot_bg`，外层卡片使用 `plot_card_bg`。
- 网格线低对比：深色 `#2a3943`，浅色 `#ddd4c7`。
- 坐标轴和文字使用主题 text token。
- 主数据线使用青蓝或青绿，拟合线/告警线使用红或琥珀。
- 工具条按钮使用紧凑按钮样式，不使用原生突兀边框。

布局：

- 图表优先占据剩余空间。
- 图表标题使用 panel 标题，不在图内重复冗长标题。
- 图例只在多序列比较时显示；单序列图不强制显示图例。

## 文案与状态表达

界面文字应服务操作，不做教程。

建议：

- Header 副标题：短状态。
- 状态条：短标签 + 当前值。
- Banner：只在当前模式需要解释下一步、限制或错误时出现。
- Tooltip：用于图标按钮、紧凑按钮和高级参数。
- Dialog：用于确认危险动作或展示详细诊断。

状态 tone：

- `subtle`：等待、空状态、普通读数。
- `info`：检查、诊断、可继续。
- `success`：已连接、已完成、active 正常。
- `warning`：只读、写入被阻止、配置不完整、数据质量可疑。
- `danger`：停止、断连、失败、潜在破坏性操作。

实机相关表达：

- 默认不要把界面引导到 real-machine 写入。
- 如果存在实机模式或写入动作，必须显示 backend/profile/status。
- 写入受限时使用 warning tone，并禁用或降权相关按钮。

## 旧 UI 迁移规则

许多 `gui.py` 由 `.ui` 生成。迁移时：

- 优先在 `main.py` 或入口类中包裹、重排、设置 objectName 和 stylesheet。
- 避免大规模手改生成的 `gui.py`。
- 如果必须改 `.ui`，保持对应生成文件和行为一致。
- 旧 Catppuccin 式紫蓝深色样式不是新界面的目标风格；新实现应使用本文档的 dark/light token。

## 新 GUI 实施检查表

开发或让 Codex 生成新 GUI 时，至少检查：

- 是否有顶部 header，且标题、主题切换、日志入口位置一致。
- 是否有状态条或等价运行摘要，并使用左侧色条状态项。
- 是否支持 `night_shift` 和 `control_room` 两个主题。
- 是否使用统一字体族。
- 是否把主要动作分成 `diagnostic / control / danger / subtle / statusAction` 等角色。
- 是否避免常驻大日志、长说明文字和装饰性空白。
- 是否为图表、表格、输入框、tabs 设置了主题化样式。
- 是否在按钮禁用、运行中、失败、只读、写入阻止状态下有明确视觉反馈。
- 是否默认 VM/offline 安全，不主动切向实机写入。
- 是否运行了至少 `python3 -m compileall` 的窄范围检查；全仓 Python-only 改动优先运行 `bash scripts/check.sh`。

## Codex 调用提示词

后续需要实现新 GUI 或调整旧 GUI 时，可在任务中引用：

```text
请遵循 docs/platform/gui_style.md 的 HALF GUI 风格：
使用 PyQt/Fusion 风格，保留 night_shift 与 control_room 双主题；
采用顶部 header + 状态条 + 卡片/分组工作区结构；
按钮按 diagnostic/control/danger/subtle/statusAction 角色设置；
图表、tabs、输入框、日志区都要跟随主题；
默认 VM/offline 安全，不引导实机写入。
```

如果只做小功能补丁，应保持现有窗口结构，不为了风格统一重排无关区域。
