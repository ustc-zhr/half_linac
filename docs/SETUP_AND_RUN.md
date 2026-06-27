# Setup And Run

这份文档整理了当前 `half_linac` 仓库的环境准备、机器相关配置和常用运行方式。

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

## 2. EPICS

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

## 3. Python 环境

当前仓库已经提供 [environment.yml](../environment.yml)。优先尝试：

```bash
conda env create -f environment.yml
conda activate half_linac
python3 --version
```

确认 `python3 --version` 显示 Python 3.10 或更高版本，推荐使用 Python 3.11。Python 3.9 或更低版本不支持本仓库中使用的 `dataclass(slots=True)` 和 `type | None` 类型写法，会导致 Jitter Analysis、BBA 等应用在导入阶段报错。

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
- sdds Python 模块

示例安装命令：

```bash
conda install -c conda-forge python=3.11 pyqt matplotlib scipy numpy scikit-image pyqtgraph pandas h5py
pip install pyepics
conda install soliday::sdds
```

注意：Energy Spectrum 代码中导入名是 `skimage`，实际安装包名是 `scikit-image`。

完成安装后，可以先做一次 Python 依赖自检：

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
import sdds
print("Python environment OK:", sys.version)
PY
```

## 4. elegant 与 SDDS

virtual machine 目录下的脚本会调用本机 `elegant` 可执行文件，例如：

- `src/virtual_machine/half_elegant/elegant/one`

因此除了 Python `sdds` 包之外，你还需要保证系统命令行可以直接执行：

```bash
elegant
```

如果这条命令不可用，VM 相关流程只能做静态检查，不能做完整运行验证。

## 5. 克隆仓库

```bash
git clone https://git.ustc.edu.cn/zhanghaoran/half_linac.git
cd half_linac
```

## 6. 配置项目环境变量

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

## 7. 修改机器相关配置

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

## 8. 静态检查

在启动任何 GUI、IOC 或 VM 之前，先运行：

```bash
bash scripts/check.sh
```

这一步会：

- 对 `src/` 做 `compileall`
- 检查仓库内脚本的 shell 语法
- 不启动长进程

## 9. 常用运行命令

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

## 10. 常见注意事项

- 运行时机器配置现在统一来自 `configs/machines/` 下的 machine profile。
- 仓库中存在不少生成物和历史运行产物，不要默认把它们当成手工维护源码。
- GUI `gui.py` 文件很多是由 `.ui` 生成的；如果要改布局，最好同步考虑 `.ui` 文件。
- `real` 和 `vm` 模式是两套不同假设。默认优先在 `vm` 模式下验证。
- `softIOC` 如果打印 `IOC is booting with TOP ... but was built with TOP ...`，说明当前二进制不是在这份仓库路径下构建的；先检查 `configure/RELEASE`，再运行 `bash scripts/build_ioc.sh` 重新生成 `envPaths` 和二进制。
