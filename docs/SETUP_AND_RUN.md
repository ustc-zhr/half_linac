# Setup And Run

这份文档整理 Accelerator HLA Platform 的环境准备、机器相关配置和常用运行方式。`half_linac` 是当前保留的历史仓库和 Python 包名；HALF 与 IRFEL 是平台支持的机器实例。

## 1. Linux 环境

推荐使用 Linux 或 WSL。Windows 原生环境不适合作为这套仓库的主要运行环境。

### WSL

参考微软官方文档：

- https://learn.microsoft.com/zh-cn/windows/wsl/install

常见安装方式：

```powershell
wsl --install
wsl.exe --install Ubuntu
```

### Shell

项目不依赖特定 shell，但长期使用建议配好 `zsh` 或 `bash`。

## 2. EPICS Runtime Modes

先区分两种运行场景：

- 控制室只连接目标机器的实机 IOC：不需要在本机安装 EPICS Base，也不需要构建或启动本仓库的 `softIOC`。仍然需要 Python `pyepics`，GUI 通过 Channel Access 访问实机 PV。
- 本机运行 VM/softIOC：需要 EPICS Base、`softIoc` 可执行程序，以及本仓库 `src/softIOC/*` 的构建和启动流程。

控制室实机在线测试的最小 EPICS 检查是：

```bash
conda activate half_linac
python3 - <<'PY'
import epics
print("pyepics OK")
PY
```

如果控制室环境里有 EPICS 命令行工具，也可以用 `caget` 检查实机 PV；没有 `caget` 也不影响 GUI 使用 `pyepics`。

下面的 EPICS Base 安装步骤只适用于需要在本机跑 VM/softIOC 的场景。

## 3. EPICS Base For Local VM/softIOC

参考官方文档：

- https://docs.epics-controls.org/en/latest/getting-started/installation-linux.html

### 基础依赖

```bash
sudo apt install build-essential
sudo apt install libreadline-dev
```

### 示例安装流程

```bash
mkdir -p "$HOME/EPICS"
cd "$HOME/EPICS"
wget https://epics-controls.org/download/base/base-7.0.8.1.tar.gz
tar -xvf base-7.0.8.1.tar.gz
cd base-7.0.8.1
make
```

然后在 shell 配置文件中加入：

```bash
export EPICS_BASE=${HOME}/EPICS/epics-base
export EPICS_HOST_ARCH=$(${EPICS_BASE}/startup/EpicsHostArch)
export PATH=${EPICS_BASE}/bin/${EPICS_HOST_ARCH}:${PATH}
```

验证方式：

```bash
softIoc
```

如果环境正常，会进入 `epics>` 提示符。

## 4. Python 环境

当前仓库已经提供 [environment.yml](../environment.yml)。优先尝试：

```bash
conda env create -f environment.yml
conda activate half_linac
python3 --version
```

确认 `python3 --version` 显示 Python 3.10 或更高版本，推荐使用 Python 3.11。Python 3.9 或更低版本不支持本仓库中使用的 `dataclass(slots=True)` 和 `type | None` 类型写法，会导致 Jitter Analysis、BBA 等应用在导入阶段报错。

控制室已验证的 SDDS Python binding 安装方式是建好主环境后单独安装：

```bash
conda activate half_linac
conda install soliday::sdds
```

`environment.yml` 有意不直接声明 `soliday::sdds`，这样主环境求解不依赖额外 channel。需要运行 VM、model backend 或 energy spectrum 这类导入 `sdds.SDDS` 的流程时，再执行上面的后装命令。只做 orbit display、beam monitor、orbit correct 等实机 PV 在线测试时，可以先不安装 `sdds`。

如果你不想直接复用这份环境文件，至少需要这些依赖：

- Python >=3.10，推荐 3.11
- PyQt5
- pyepics
- numpy
- scipy
- matplotlib
- scikit-image
- pyqtgraph
- pandas
- h5py
- sdds Python 模块（仅 VM/model/energy-spectrum 等 SDDS 相关流程需要）

示例安装命令：

```bash
conda install -c conda-forge python=3.11 pyqt matplotlib scipy numpy scikit-image pyqtgraph pandas h5py
pip install pyepics
conda install soliday::sdds
```

注意：

- Energy Spectrum 代码中导入名是 `skimage`，实际安装包名是 `scikit-image`。
- `sdds` 建议使用 Robert Soliday 维护的 conda 包：
  <https://anaconda.org/soliday/sdds>。Anaconda 页面给出的安装命令是
  `conda install soliday::sdds`。在 Linux 上 conda 会选择该 channel 中
  支持当前平台的版本。

完成安装后，可以先做一次基础在线测试依赖自检：

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

如果已经安装了 `soliday::sdds`，再检查 SDDS 相关依赖：

```bash
python3 - <<'PY'
import sdds
print("sdds OK:", getattr(sdds, "__file__", "built-in"))
PY
```

## 5. elegant 与 SDDS

`elegant` 和 Python `sdds` 是两件不同的依赖：

- `elegant` 是外部可执行程序，模型后端和 VM 会通过命令行调用它。
- Python `sdds` 模块用于读取 elegant 输出的 `.mat`、`.twi` 等 SDDS 文件。

详细安装步骤见 [ELEGANT_INSTALL.md](ELEGANT_INSTALL.md)。

virtual machine 目录下的脚本会调用本机 `elegant` 可执行文件，例如：

- `src/virtual_machine/half_elegant/elegant/one`

最小验证命令：

```bash
which elegant
elegant
python3 -c "import sdds; print('sdds OK')"
```

如果这些命令失败，GUI 仍可做部分静态或只读操作，但 `Update eta`、
`Update optics`、`emit_measure` Twiss/recalculate、VM/elegant 相关流程会失败。

## 6. 克隆仓库

```bash
git clone https://git.ustc.edu.cn/zhanghaoran/half_linac.git
cd half_linac
```

## 7. 配置项目环境变量

不需要在 `.zshrc` / `.bashrc` 里手工追加 `PYTHONPATH`。仓库自带的 `scripts/` 启动脚本，以及主要 Python 入口文件，现在都会自行定位仓库根和导入路径。

如果你希望在当前 shell 里反复手动执行多个 Python 入口，再执行：

```bash
source scripts/setup.sh
```

这个脚本会：

- 导出 `HALF_LINAC_ROOT`
- 导出 `halflinac_ROOT`
- 把仓库父目录加入 `PYTHONPATH`

这只是一个方便的 shell 级捷径，不再是运行 `half_linac` 的前置条件。

## 8. 修改机器相关配置

当前最关键的机器相关文件是：

- `src/softIOC/halflinac/configure/RELEASE`

至少要检查：

- `EPICS_BASE`

这些路径目前不是自动发现的，而是显式写死在 `configure/RELEASE` 里。

`src/softIOC/halflinac/iocBoot/ioctarget/envPaths` 不是手工维护源文件，而是 `make rebuild` 生成的派生文件。不要把它当成长期配置入口。

如果你改了 `configure/RELEASE` 里的路径，不要直接启动 IOC，先在仓库根目录执行：

```bash
bash scripts/build_ioc.sh
```

原因是 `softIOC` 二进制会记住 build-time `TOP`，而 `envPaths` 也会在重建时一起刷新。只改运行时派生文件不能消掉旧路径，必须在当前仓库路径下重新构建一次。

## 9. 静态检查

在启动任何 GUI、IOC 或 VM 之前，先运行：

```bash
bash scripts/check.sh
```

这一步会：

- 对 `src/` 做 `compileall`
- 检查仓库内脚本的 shell 语法
- 不启动长进程

## 10. 常用运行命令

### 启动 Control Room

```bash
bash scripts/runMe
```

### 启动 Python 层 IOC 管理器

```bash
bash scripts/start_ioc_manager.sh
```

### 重建 IOC 工程

```bash
bash scripts/build_ioc.sh
```

### 启动 VM 管理器

```bash
bash scripts/start_vm.sh
```

### 直接启动 IOC 工程

```bash
cd src/softIOC/halflinac
./runMe
```

### 手动测试 `st.cmd`

```bash
cd src/softIOC/halflinac/iocBoot/ioctarget
./st.cmd
```

## 11. 常见注意事项

- 运行时机器配置现在统一来自 `configs/machines/` 下的 machine profile。
- 仓库中存在不少生成物和历史运行产物，不要默认把它们当成手工维护源码。
- GUI `gui.py` 文件很多是由 `.ui` 生成的；如果要改布局，最好同步考虑 `.ui` 文件。
- `real` 和 `vm` 模式是两套不同假设。默认优先在 `vm` 模式下验证。
- `softIOC` 如果打印 `IOC is booting with TOP ... but was built with TOP ...`，说明当前二进制不是在这份仓库路径下构建的；先检查 `configure/RELEASE`，再运行 `bash scripts/build_ioc.sh` 重新生成 `envPaths` 和二进制。
