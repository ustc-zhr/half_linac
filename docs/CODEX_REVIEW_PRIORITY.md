# Codex Review Priority

这份文档用于指导你如何让 Codex 重新审查并逐步完善 `half_linac`。

目标不是一次性“全仓重构”，而是把高风险、高耦合、最容易影响 IOC / VM / GUI 联动的部分先审清楚。

## 审查前提

在让 Codex 动手之前，建议先保证三件事：

1. 工作树里与当前任务无关的改动尽量单独管理，避免 review 结果和历史改动混在一起
2. 先执行 `bash scripts/check.sh`
3. 先让 Codex 读 [AGENTS.md](../AGENTS.md) 和对应子目录 `AGENTS.md`

## 推荐审查顺序

### P0: 运行时配置与路径约束

优先文件：

- [`setup.py`](../setup.py)
- [`scripts/common.sh`](../scripts/common.sh)
- `src/softIOC/halflinac/iocBoot/ioctarget/envPaths`

为什么先看：

- 当前 `setup.py` 实际上是运行时配置文件，不是 Python 打包文件
- 路径依赖同时分散在 Python、shell、IOC boot 文件里
- 这类问题会污染几乎所有运行和审查结果

重点检查：

- 是否仍然依赖硬编码绝对路径
- `machine_type`、PV 前后缀、运行周期这类全局配置是否应进一步集中
- `envPaths` 的机器相关配置是否应该显式文档化，而不是隐含在代码里

### P1: IOC 与 JSON 同步链路

优先文件：

- [`src/softIOC/mainIOC.py`](../src/softIOC/mainIOC.py)
- [`src/softIOC/pv_server.py`](../src/softIOC/pv_server.py)

为什么先看：

- 这里控制 `json -> substitutions -> IOC -> PV -> json` 的核心闭环
- 一旦这里有竞态、异常处理缺失或命名规则不统一，后面所有上层应用都会受影响

重点检查：

- JSON 读写是否原子
- PV 更新和 JSON 回写是否可能形成意外循环
- `QUAD / BEND / COR / BPM / WATCH` 类型分支是否完整
- substitutions 生成逻辑是否把“模板”与“生成物”边界写清楚

### P2: virtual machine 与 elegant 编排

优先文件：

- [`src/virtual_machine/half_elegant/start_VM.py`](../src/virtual_machine/half_elegant/start_VM.py)
- [`src/virtual_machine/half_elegant/elegant_parser.py`](../src/virtual_machine/half_elegant/elegant_parser.py)
- [`src/virtual_machine/lattice_parser.py`](../src/virtual_machine/lattice_parser.py)

为什么先看：

- 这里负责 `lattice_ini.lte -> json -> lattice.lte / one.ele -> elegant -> PV` 这条主链
- 当前流程依赖轮询文件时间戳，且会刷新多个生成文件

重点检查：

- 文件变更检测是否可靠
- 异常后是否会留下半更新状态
- 生成文件与源文件边界是否清晰
- `elegant` 执行失败时是否有足够诊断信息

### P3: launcher 与 VM GUI 的进程生命周期

优先文件：

- [`src/apps/launcher/main.py`](../src/apps/launcher/main.py)
- [`src/virtual_machine/half_elegant/mainVM.py`](../src/virtual_machine/half_elegant/mainVM.py)

为什么先看：

- 这两个 GUI 都负责启动和回收多个长进程
- 这里的缺陷通常表现为重复启动、孤儿进程、按钮状态错误、关闭不彻底

重点检查：

- `Popen` 生命周期管理是否一致
- `SIGTERM / SIGKILL` 的退场逻辑是否足够
- “已启动但未 ready”的中间状态是否被正确处理
- GUI 按钮 enable/disable 是否和真实进程状态一致

### P4: 上层应用与优化模块

优先目录：

- `src/apps`
- `src/optimization`

为什么放在后面：

- 这些模块较多，而且很多依赖前面的 IOC / VM / PV 主链
- 如果底层边界没先收紧，后续 review 只会不停遇到“症状”而不是“根因”

重点检查：

- GUI 逻辑是否直接依赖 `sys.argv`
- 定时器、线程、子进程是否能正确停止
- 算法日志、模板文件、临时输出是否和源码混放
- 生成的 `gui.py` 与手写逻辑是否边界清晰

### P5: 仓库治理与可维护性

重点内容：

- 进一步清理已跟踪的生成物和运行产物
- 把误导性的文件命名逐步改正，例如根目录 `setup.py`
- 补最小自动化检查脚本
- 为高风险模块补更窄的 smoke test

## 建议让 Codex 使用的任务粒度

推荐的任务粒度是“单一子系统 + 单一目标”，不要一上来就要求全仓一起改。

好的任务例子：

- 只 review `src/softIOC`，找 JSON/PV 同步中的 bug 和风险，不改代码
- 只修复 `src/virtual_machine/half_elegant/start_VM.py` 的异常处理和日志问题
- 只清理 `src/apps/launcher/main.py` 的进程生命周期管理
- 只整理 `src/optimization` 中日志文件和源码的边界

不好的任务例子：

- “全面重构 half_linac”
- “把整个项目改现代化”
- “把所有问题一次性修完”

## 建议提示词

### 先做 review

```text
请先阅读根目录和相关子目录的 AGENTS.md，只对 src/softIOC 和 src/virtual_machine 做代码审查，不要改代码。重点找出：
1. JSON / PV / elegant 之间的数据流风险
2. 进程生命周期问题
3. 生成文件和源文件边界不清的问题
4. 缺失的最小验证
请按严重程度列出 findings，并给出文件和行号。
```

### 再做小范围修复

```text
基于上一次 review，只修复 src/softIOC/pv_server.py 和 src/softIOC/mainIOC.py 里最明确的两个问题。
要求：
1. 不改业务行为边界
2. 不启动长时间 IOC
3. 改完后运行仓库里最小可行的静态检查
```

### 再扩到 GUI

```text
请只审查 src/apps/launcher/main.py 和 src/virtual_machine/half_elegant/mainVM.py 的子进程管理与退出逻辑。
如果改代码，只做最小补丁，并说明还需要哪些手工 GUI 验证。
```

## 每轮结束时应该要求 Codex 回答的内容

每一轮任务结束时，最好要求 Codex 明确回答：

1. 改了什么
2. 运行了哪些验证
3. 哪些结论仍然依赖人工运行 IOC / GUI / elegant
4. 下一轮最值得处理的一个问题是什么
