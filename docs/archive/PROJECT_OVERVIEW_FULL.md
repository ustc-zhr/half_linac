# Accelerator HLA Platform

> Archive note: this is the previous long-form README kept for historical
> context. For current navigation, start from [docs/README.md](../README.md)
> and the root [README.md](../../README.md).

面向多装置的电子加速器高层应用平台。平台通过 machine profile 将机器配置、控制后端和应用工作流解耦，使同一套高层应用能够服务于 HALF、IRFEL 以及后续接入的加速器装置。

平台包含：

- `EPICS softIOC`
- 基于 `elegant` 的 virtual machine
- PyQt 上层应用 GUI
- 在线优化算法与辅助工具

当前 `half_linac` 是仓库和 Python 包的历史名称，本阶段继续保留，以避免破坏已有导入、启动脚本、环境变量和控制室部署。HALF 不再代表平台的适用边界，而是 `configs/machines/half/` 中的一套机器配置；IRFEL 则由 `configs/machines/irfel/` 描述。

当前仓库同时包含源码、生成文件、实验运行产物和少量历史备份文件。项目入口已经收敛到根目录 `README.md`、`AGENTS.md` 和 `docs/`。

运行时机器配置现在统一来自 `configs/machines/` 下的 machine profile。

## 平台定位

平台当前定位为：

> 配置驱动、面向多装置、贯通实机与虚拟机、集成加速器模型并具有写入安全治理能力的电子加速器高层应用平台。

这里的“通用”是加速器领域内的通用，而不是任意工业控制系统的通用。HALF 和 IRFEL 两套 machine profile 已通过配置、应用加载和 VM 相关校验；其中实机 commissioning 证据目前来自 IRFEL，HALF 尚未完成真实机器验证。控制侧以 EPICS 为主，模型侧以 elegant 为主；对储存环、其他粒子类型、其他控制协议和模型引擎的支持仍属于后续扩展范围。

平台按三层理解：

- Platform Core：machine profile、逻辑通道解析、运行时选择、模型接口、进程管理和写入安全策略
- Application Suite：轨道、束流图像、BBA、发射度、能谱、色散校正等高层应用
- Machine Profiles：HALF、IRFEL 及后续机器的设备清单、PV 映射、工作流参数和运行资源

更完整的产品边界、术语和演进原则见 [PLATFORM_POSITIONING.md](../platform/PLATFORM_POSITIONING.md)。

## 仓库地图

- `configs/machines/`: HALF、IRFEL 等机器的 profile、控制后端、应用工作流和模型后端配置
- `src/apps/`: 可由机器能力选择启用的 GUI 应用，包括 Control Room（目录当前仍为 `launcher`）、`orbit_correct`、`dispersion_correction`、`bba`、`beam_monitor`、`energy_spectrum` 等
- `src/shared/`: 平台核心共享模块，包括 machine profile、模型运行、运行状态、进程与窗口管理
- `src/optimization/`: 在线优化 GUI 与 BO / RCDS / Rsimplex 算法
- `src/softIOC/`: IOC 管理脚本、PV 同步逻辑、IOC 工程文件
- `src/virtual_machine/`: lattice 解析、VM 管理、`elegant` 运行目录
- `scripts/`: 仓库内统一脚本入口
- `docs/`: 安装、配置、设计和应用说明

## 快速开始

### 1. 准备环境

建议先阅读 [SETUP_AND_RUN.md](../getting_started/SETUP_AND_RUN.md)。根目录 README 只保留常用安装路径；EPICS Base、elegant 和离屏 GUI 测试的细节以该文档为准。

推荐使用 Linux 或 WSL。Windows 原生环境不适合作为主要运行环境。

先克隆仓库并进入项目根目录：

```bash
git clone https://github.com/ustc-zhr/half_linac.git
cd half_linac
```

创建并激活 Conda 环境：

```bash
conda env create -f environment.yml
conda activate half_linac
python3 --version
```

确认 `python3 --version` 显示 Python 3.10 或更高版本，推荐 3.11。仓库默认环境文件当前使用 Python 3.11。

激活环境后先做一次基础依赖检查：

```bash
python3 - <<'PY'
import sys
assert sys.version_info >= (3, 10), sys.version
import PyQt5
import epics
import h5py
import matplotlib
import numpy
import pandas
import pyqtgraph
import scipy
import skimage
print("Basic Python environment OK:", sys.version)
PY
```

说明：`scripts/` 下的启动脚本和主要 Python 入口现在都会自行定位仓库，不需要你先在 `.zshrc` / `.bashrc` 里手工追加 `PYTHONPATH`。`source scripts/setup.sh` 只在你想反复手动执行多个 `python3 src/...` 入口时才有帮助：

```bash
source scripts/setup.sh
```

推荐直接使用统一安装入口。它默认安装 Python `sdds`，并检查外部 `elegant`
命令，因为 VM、model backend、energy spectrum 等主要流程都依赖它们：

```bash
bash scripts/install_env.sh --check
```

如果只连接已有 IOC、确定不做模型计算，可以使用轻量安装：

```bash
bash scripts/install_env.sh --core-only --check
```

如果只连接实机 IOC，不需要本机安装 EPICS Base，也不需要构建本仓库的 `softIOC`。如果需要在本机启动仓库 softIOC，则需要先安装 EPICS Base，并保证 `softIoc` 可执行：

```bash
softIoc
```

成功时会进入 `epics>` 提示符。退出后再继续配置和构建本仓库 softIOC。EPICS Base 的安装示例见 [SETUP_AND_RUN.md](../getting_started/SETUP_AND_RUN.md#3-epics-base-for-local-vmsoftioc)。

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

手动安装依赖的示例命令：

```bash
conda create -n half_linac python=3.11
conda activate half_linac
conda install -c conda-forge pyqt pyqtwebengine pyopengl pyqtgraph numpy scipy matplotlib scikit-image scikit-learn pandas h5py pyyaml requests psutil tqdm pytest bayesian-optimization
pip install pyepics
conda install soliday::sdds
```

其中 `conda install soliday::sdds` 只对 VM/model/energy-spectrum 等 SDDS 相关流程必需。

### 2. 选择机器并检查本地运行依赖

Control Room 会从 `configs/machines/` 发现可用机器，并根据所选 machine profile 判断应用、控制后端和模型后端是否可用。新增机器请从 `configs/machines/_template/` 开始，具体步骤见 [ADD_SECOND_MACHINE.md](../machines/ADD_SECOND_MACHINE.md)。

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

## 附录：elegant 与 SDDS 安装

当前 VM、model backend、energy spectrum 以及部分 optics/Twiss 更新流程依赖 APS `elegant` 软件。只运行 Control Room GUI 并连接已有实机 IOC 时，可以先不安装 `elegant`；需要本机运行 VM 或模型计算时，必须保证当前 shell 能找到 `elegant`，并且 Python 环境能导入 `sdds`。

官方入口：

- APS Accelerator Operations and Physics Software: [https://www.aps.anl.gov/Accelerator-Operations-Physics/Software](https://www.aps.anl.gov/Accelerator-Operations-Physics/Software)
- Python `sdds` conda package: [https://anaconda.org/soliday/sdds](https://anaconda.org/soliday/sdds)
- 仓库详细说明：[ELEGANT_INSTALL.md](../getting_started/ELEGANT_INSTALL.md)

需要区分三类依赖：

- `elegant`: 外部命令行程序，VM 和模型计算会直接调用
- SDDS Toolkit: APS 发布的 SDDS 命令行工具和底层库
- Python `sdds`: 仓库读取 `.mat`、`.twi` 等 elegant 输出文件时使用的 Python 模块

### 1. 优先使用控制室共享环境

如果控制室机器已有环境模块或共享软件栈，优先使用已有安装：

```bash
module avail elegant
module load elegant
which elegant
elegant
```

如果没有 `module` 命令，但维护人员已经把 elegant 安装在 `/opt`、`/usr/local` 或共享软件目录下，把对应 `bin` 目录加入 `PATH`：

```bash
export PATH=/path/to/elegant/bin:$PATH
which elegant
```

建议把最终的 `PATH` 设置放到控制室环境加载脚本中，不要写进仓库源码。

### 2. 从 APS 包安装

先确认系统和架构：

```bash
cat /etc/os-release
uname -m
```

然后从 APS 软件页面下载与系统匹配的包，通常至少包括：

- `SDDSToolKit-...<os>...x86_64.rpm`
- `elegant-...<os>...x86_64.rpm`

RHEL、Fedora 或 openSUSE 可使用 RPM 包管理器安装：

```bash
sudo dnf install ./SDDSToolKit-*.rpm
sudo dnf install ./elegant-*.rpm
```

较老的 RHEL/CentOS 系统可使用：

```bash
sudo yum install ./SDDSToolKit-*.rpm
sudo yum install ./elegant-*.rpm
```

Ubuntu/Debian 可按 APS 页面说明使用 `alien` 转换安装：

```bash
sudo apt update
sudo apt install alien
sudo alien -i SDDSToolKit-*.rpm
sudo alien -i elegant-*.rpm
```

如果 control-room 主机有严格的软件安装策略，应由本地维护人员安装到共享软件目录，而不是直接全局安装。

### 3. 安装 Python sdds

在 `half_linac` Conda 环境中安装：

```bash
conda activate half_linac
conda install soliday::sdds
```

`environment.yml` 有意不直接声明 `soliday::sdds`，避免额外 channel 影响主环境
求解。推荐的 `scripts/install_env.sh` 会在主环境创建后自动安装它；只有明确使用
`--core-only` 时才跳过。

验证 Python 模块：

```bash
python3 - <<'PY'
import sdds
print("Python sdds OK:", getattr(sdds, "__file__", "built-in"))
PY
```

### 4. 最终验证

启动 VM 或模型相关 GUI 功能前，在同一个 shell 中检查：

```bash
which elegant
elegant
python3 -c "import sdds; print('sdds OK')"
bash scripts/check.sh
```

如果 `which elegant` 没有输出，说明 `elegant` 不在 `PATH`。如果 GUI 能打开但 `Update eta`、`Update optics`、发射度 Twiss/recalculate 或 VM 相关流程失败，优先在启动 GUI 的同一个 shell 中重新检查 `which elegant` 和 `python3 -c "import sdds"`。

## 文档导航

- [../../AGENTS.md](../../AGENTS.md): 仓库级 agent 规则与标准命令
- [../README.md](../README.md): docs 索引
- [../platform/PLATFORM_POSITIONING.md](../platform/PLATFORM_POSITIONING.md): 平台定位、边界、架构分层与历史命名策略
- [../../configs/machines/README.md](../../configs/machines/README.md): machine profile 结构和配置职责
- [../machines/ADD_SECOND_MACHINE.md](../machines/ADD_SECOND_MACHINE.md): 新机器接入的最小路径
- [../getting_started/SETUP_AND_RUN.md](../getting_started/SETUP_AND_RUN.md): 安装、环境配置、运行方式
- [../getting_started/ELEGANT_INSTALL.md](../getting_started/ELEGANT_INSTALL.md): elegant、SDDS Toolkit 和 Python sdds 安装说明
- [../apps/DISPERSION_CORRECTION.md](../apps/DISPERSION_CORRECTION.md): 色散校正架构、运行边界与 commissioning 清单
