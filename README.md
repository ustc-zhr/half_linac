# Accelerator HLA Platform

面向多装置的电子加速器高层应用平台。平台通过 machine profile 将机器配置、控制后端和应用工作流解耦，使同一套高层应用能够服务于 HALF、IRFEL 以及后续接入的加速器装置。

平台包含：

- `EPICS softIOC`
- 基于 `elegant` 的 virtual machine
- PyQt 上层应用 GUI
- 在线优化算法与辅助工具

当前 `half_linac` 是仓库和 Python 包的历史名称，本阶段继续保留，以避免破坏已有导入、启动脚本、环境变量和控制室部署。HALF 不再代表平台的适用边界，而是 `configs/machines/half/` 中的一套机器配置；IRFEL 则由 `configs/machines/irfel/` 描述。

当前仓库同时包含源码、生成文件、实验运行产物和少量历史备份文件。为了让 Codex 或人工审查更高效，项目入口已经收敛到本文档、`AGENTS.md` 和 `docs/`。

运行时机器配置现在统一来自 `configs/machines/` 下的 machine profile。

## 平台定位

平台当前定位为：

> 配置驱动、面向多装置、贯通实机与虚拟机、集成加速器模型并具有写入安全治理能力的电子加速器高层应用平台。

这里的“通用”是加速器领域内的通用，而不是任意工业控制系统的通用。HALF 和 IRFEL 两套 machine profile 已通过配置、应用加载和 VM 相关校验；其中实机 commissioning 证据目前来自 IRFEL，HALF 尚未完成真实机器验证。控制侧以 EPICS 为主，模型侧以 elegant 为主；对储存环、其他粒子类型、其他控制协议和模型引擎的支持仍属于后续扩展范围。

平台按三层理解：

- Platform Core：machine profile、逻辑通道解析、运行时选择、模型接口、进程管理和写入安全策略
- Application Suite：轨道、束流图像、BBA、发射度、能谱、色散校正等高层应用
- Machine Profiles：HALF、IRFEL 及后续机器的设备清单、PV 映射、工作流参数和运行资源

更完整的产品边界、术语和演进原则见 [docs/PLATFORM_POSITIONING.md](docs/PLATFORM_POSITIONING.md)。

## 仓库地图

- `configs/machines/`: HALF、IRFEL 等机器的 profile、控制后端、应用工作流和模型后端配置
- `src/apps/`: 可由机器能力选择启用的 GUI 应用，包括 Control Room（目录当前仍为 `launcher`）、`orbit_correct`、`dispersion_correction`、`bba`、`beam_monitor`、`energy_spectrum` 等
- `src/shared/`: 平台核心共享模块，包括 machine profile、模型运行、运行状态、进程与窗口管理
- `src/optimization/`: 在线优化 GUI 与 BO / RCDS / Rsimplex 算法
- `src/softIOC/`: IOC 管理脚本、PV 同步逻辑、IOC 工程文件
- `src/virtual_machine/`: lattice 解析、VM 管理、`elegant` 运行目录
- `scripts/`: 仓库内统一脚本入口
- `docs/`: 安装说明、开发记录、Codex 审查优先级

## 快速开始

### 1. 准备环境

建议先阅读 [docs/SETUP_AND_RUN.md](docs/SETUP_AND_RUN.md)。

最短路径如下：

```bash
conda env create -f environment.yml
conda activate half_linac
python3 --version
bash scripts/runMe
```

说明：`scripts/` 下的启动脚本和主要 Python 入口现在都会自行定位仓库，不需要你先在 `.zshrc` / `.bashrc` 里手工追加 `PYTHONPATH`。`source scripts/setup.sh` 只在你想反复手动执行多个 `python3 src/...` 入口时才有帮助。

如果 `environment.yml` 不能直接复用，控制室只连接目标机器的实机 IOC 时至少需要：

- Python >=3.10，推荐 3.11；Python 3.9 或更低版本不能运行本仓库的部分应用
- PyQt5
- pyepics
- numpy / scipy / matplotlib / scikit-image
- pyqtgraph / pandas / h5py

以下依赖只在本机运行 VM、model backend、energy spectrum 或仓库 softIOC 时需要：

- sdds Python 模块
- 系统可执行的 `elegant`
- 已安装并可运行的 EPICS Base / `softIoc`

### 2. 选择机器并检查本地运行依赖

Control Room 会从 `configs/machines/` 发现可用机器，并根据所选 machine profile 判断应用、控制后端和模型后端是否可用。新增机器请从 `configs/machines/_template/` 开始，具体步骤见 [docs/ADD_SECOND_MACHINE.md](docs/ADD_SECOND_MACHINE.md)。

如果只在控制室连接实机 IOC，不需要构建或启动本仓库的 `softIOC`，也不需要修改下面这些 IOC 构建配置。本机启动仓库 softIOC 前，需要按本机环境检查或修改：

- `src/softIOC/halflinac/configure/RELEASE`

这里真正应该改的是 `EPICS_BASE` 这类构建配置。`src/softIOC/halflinac/iocBoot/ioctarget/envPaths` 是 `make rebuild` 生成的派生文件，不应该作为手工维护入口。

如果你改了 `configure/RELEASE` 里的路径，下一步不要直接启动 IOC，而是先执行：

```bash
bash scripts/build_ioc.sh
```

否则 `softIOC` 很可能继续沿用旧的 build-time `TOP`，启动时出现 `IOC is booting with TOP ... but was built with TOP ...` 警告。

### 3. 先做静态检查

```bash
bash scripts/check.sh
```

这一步不会启动 GUI、IOC 或 `elegant` 长进程，只做快速静态验证。

涉及 GUI 布局或共享运行上下文时，在已激活 `environment.yml` 环境后补充运行：

```bash
python3 scripts/smoke_gui_layouts.py
```

该命令使用 HALF/VM 配置和 Qt offscreen 平台逐个构造主要 operator GUI，不启动 IOC、
`elegant` 或实机控制流程。若当前 Python 缺少匹配的 Qt offscreen 插件，脚本会在创建
窗口前报告应切换 Conda 环境。

### 4. 常用启动方式

```bash
bash scripts/runMe
```

```bash
bash scripts/start_ioc_manager.sh
```

```bash
bash scripts/build_ioc.sh
```

```bash
bash scripts/start_vm.sh
```

说明：

- `scripts/runMe` 启动 Control Room GUI
- `scripts/build_ioc.sh` 在当前仓库路径下重建 `softIOC`
- `scripts/start_ioc_manager.sh` 启动 Python 层 IOC 管理器
- `scripts/start_vm.sh` 启动 virtual machine 管理器

## 运行边界

- 默认按 `VM / offline` 模式理解和审查代码
- `src/virtual_machine/half_elegant/halflinac.json`
- `src/virtual_machine/half_elegant/esa.json`
- `src/virtual_machine/half_elegant/elegant/lattice.lte`
- `src/virtual_machine/half_elegant/elegant/one.ele`
- `src/softIOC/halflinac/db/halflinac.substitutions`

这些文件可能由程序生成或刷新。除非任务明确要求，否则优先修改它们的生成逻辑，而不是手工改生成物。

## 文档导航

- [AGENTS.md](AGENTS.md): 仓库级 agent 规则与标准命令
- [docs/PLATFORM_POSITIONING.md](docs/PLATFORM_POSITIONING.md): 平台定位、边界、架构分层与历史命名策略
- [configs/machines/README.md](configs/machines/README.md): machine profile 结构和配置职责
- [docs/ADD_SECOND_MACHINE.md](docs/ADD_SECOND_MACHINE.md): 新机器接入的最小路径
- [docs/SETUP_AND_RUN.md](docs/SETUP_AND_RUN.md): 安装、环境配置、运行方式
- [docs/DEVELOPMENT_LOG.md](docs/DEVELOPMENT_LOG.md): 历史开发记录
- [docs/DISPERSION_CORRECTION.md](docs/DISPERSION_CORRECTION.md): 色散校正架构、运行边界与 commissioning 清单
- [docs/CODEX_REVIEW_PRIORITY.md](docs/CODEX_REVIEW_PRIORITY.md): 重新审查和完善仓库时的优先级建议

## 推荐的 Codex 使用顺序

如果你想让 Codex 重新审查并逐步完善这套代码，建议按这个顺序发任务：

1. 先让 Codex 只做 review，不改代码，范围限定为 `src/softIOC` 和 `src/virtual_machine`
2. 再让 Codex 修复 review 中最明确、最小的一类问题，例如路径、进程生命周期、生成文件边界
3. 然后再审查 `src/apps` 和 `src/optimization`
4. 最后再做仓库清理，例如补测试、继续收紧历史命名痕迹、拆分运行产物

更细的顺序和提示词建议见 [docs/CODEX_REVIEW_PRIORITY.md](docs/CODEX_REVIEW_PRIORITY.md)。
