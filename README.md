# HALF Injector High-Level Applications

HALF 直线加速器上层物理应用软件仓库，包含：

- `EPICS softIOC`
- 基于 `elegant` 的 virtual machine
- PyQt 上层应用 GUI
- 在线优化算法与辅助工具

当前仓库同时包含源码、生成文件、实验运行产物和少量历史备份文件。为了让 Codex 或人工审查更高效，项目入口已经收敛到本文档、`AGENTS.md` 和 `docs/`。

运行时机器配置现在统一来自 `configs/machines/` 下的 machine profile。

## 仓库地图

- `src/apps/`: GUI 应用，包括 Control Room（目录当前仍为 `launcher`）、`orbit_correct`、`bba`、`beam_monitor`、`energy_spectrum` 等
- `src/shared/`: 跨多个 GUI / runtime 复用的共享辅助模块
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

如果 `environment.yml` 不能直接复用，请至少保证以下依赖可用：

- Python >=3.10，推荐 3.11；Python 3.9 或更低版本不能运行本仓库的部分应用
- PyQt5
- pyepics
- numpy / scipy / matplotlib / scikit-image
- pyqtgraph / pandas / h5py
- sdds Python 模块
- 系统可执行的 `elegant`
- 已安装并可运行的 EPICS base / `softIoc`

### 2. 修改本机相关配置

IOC 启动前，需要按本机环境检查或修改：

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
- [docs/SETUP_AND_RUN.md](docs/SETUP_AND_RUN.md): 安装、环境配置、运行方式
- [docs/DEVELOPMENT_LOG.md](docs/DEVELOPMENT_LOG.md): 历史开发记录
- [docs/CODEX_REVIEW_PRIORITY.md](docs/CODEX_REVIEW_PRIORITY.md): 重新审查和完善仓库时的优先级建议

## 推荐的 Codex 使用顺序

如果你想让 Codex 重新审查并逐步完善这套代码，建议按这个顺序发任务：

1. 先让 Codex 只做 review，不改代码，范围限定为 `src/softIOC` 和 `src/virtual_machine`
2. 再让 Codex 修复 review 中最明确、最小的一类问题，例如路径、进程生命周期、生成文件边界
3. 然后再审查 `src/apps` 和 `src/optimization`
4. 最后再做仓库清理，例如补测试、继续收紧历史命名痕迹、拆分运行产物

更细的顺序和提示词建议见 [docs/CODEX_REVIEW_PRIORITY.md](docs/CODEX_REVIEW_PRIORITY.md)。
