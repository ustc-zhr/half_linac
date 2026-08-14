# Accelerator HLA Platform

配置驱动的多机器电子加速器高层应用平台。当前仓库保留历史名称
`half_linac`，但 HALF 只是 `configs/machines/half/` 下的一套 machine profile；
IRFEL 和后续机器通过同一套 profile 机制接入。

平台主要包含：

- EPICS softIOC 与 IOC 管理脚本
- 基于 `elegant` 的 virtual machine 和模型后端
- PyQt 操作员 GUI
- 在线优化、轨道校正、束流诊断、发射度、能谱、BBA、色散校正等应用

## 快速开始

推荐 Linux 或 WSL 环境。

```bash
git clone https://git.ustc.edu.cn/zhanghaoran/half_linac.git
cd half_linac
bash scripts/install_env.sh --check
```

默认安装会同时安装 Python `sdds`，并检查外部 `elegant` 命令是否可用，因为
VM、model backend、energy spectrum 等主要流程都依赖它们。Elegant 的系统安装
方式见 [docs/getting_started/ELEGANT_INSTALL.md](docs/getting_started/ELEGANT_INSTALL.md)。

如果已经有可用的 Python/Conda 环境，也可以直接使用已有入口：

```bash
conda env create -f environment.yml
conda activate half_linac
bash scripts/check.sh
bash scripts/runMe
```

如果只连接已有 IOC、确定不运行任何模型计算，可以安装轻量环境：

```bash
bash scripts/install_env.sh --core-only --check
```

`elegant` 是外部命令行程序，不随 Python 环境自动安装。本机运行 VM、model
backend 或相关模型计算时，需要同时具备 `elegant` 和 Python `sdds`；只连接
已有实机 IOC、且不做模型计算时通常不需要安装它们。运行仓库 softIOC 则需要
EPICS Base。详细步骤见安装文档。

## 常用命令

```bash
bash scripts/check.sh              # 快速静态检查
bash scripts/runMe                 # 启动 Control Room GUI
bash scripts/start_vm.sh           # 启动 virtual machine 管理器
bash scripts/start_ioc_manager.sh  # 启动 Python IOC 管理器
bash scripts/build_ioc.sh          # 重建 softIOC
```

脚本会自行定位仓库路径。只有在当前 shell 里反复手动运行多个 Python 入口时，
才需要：

```bash
source scripts/setup.sh
```

## 仓库地图

- `configs/machines/`: HALF、IRFEL 等 machine profile
- `src/apps/`: PyQt 操作员应用
- `src/shared/`: machine profile、模型、运行状态和进程管理等共享模块
- `src/optimization/`: 在线优化入口与算法集成
- `src/softIOC/`: IOC 工程、PV 同步和 IOC 管理器
- `src/virtual_machine/`: lattice 解析、VM 管理和 elegant 运行目录
- `scripts/`: 仓库内统一脚本入口
- `docs/`: 安装、配置、设计和应用说明

## 文档

- [docs/README.md](docs/README.md): 文档索引
- [docs/getting_started/SETUP_AND_RUN.md](docs/getting_started/SETUP_AND_RUN.md): 详细安装和运行说明
- [docs/archive/PROJECT_OVERVIEW_FULL.md](docs/archive/PROJECT_OVERVIEW_FULL.md): README 详细版归档
- [configs/machines/README.md](configs/machines/README.md): machine profile 结构
- [AGENTS.md](AGENTS.md): 仓库级开发和安全规则

## 运行边界

默认按 VM / offline 工作流理解和验证代码。不要把生成文件、日志、缓存或运行
产物当成手工维护源码；涉及实机写入、PV 命名、IOC DB 生成或 VM/IOC 同步的
改动需要先明确运行影响。
