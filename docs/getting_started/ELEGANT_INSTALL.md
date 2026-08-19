# Debian 13 安装 elegant / Pelegant

本文介绍在 Debian 13（代号 `trixie`）上安装 APS 官方 `elegant`、`Pelegant` 及 SDDS ToolKit 的完整流程。

## 0. 确认系统版本

```bash
cat /etc/os-release
```

确认看到类似：

```text
VERSION_ID="13"
VERSION_CODENAME=trixie
```

## 1. 安装基础依赖

`Pelegant` 并行运行需要 MPI 环境，这里使用 Open MPI。

```bash
sudo apt update
sudo apt install -y \
  curl alien rpm tcsh \
  openmpi-bin libopenmpi-dev \
  libgsl-dev libpng-dev libreadline-dev
```

| 软件包 | 用途 |
| --- | --- |
| `alien` | 将 APS 提供的 RPM 包转换为 Debian 包并安装 |
| `rpm` | 查看 RPM 包信息 |
| `tcsh` | 部分 APS/OAG 工具可能需要 |
| `openmpi-bin`、`libopenmpi-dev` | `Pelegant` 并行运行环境 |
| `libgsl-dev` | 数值计算库 |
| `libpng-dev` | 图形和绘图相关依赖 |
| `libreadline-dev` | 命令行交互相关依赖 |

## 2. 创建安装目录

```bash
mkdir -p ~/software/elegant_install_debian13
cd ~/software/elegant_install_debian13
```

## 3. 下载 APS 官方包

```bash
curl -LO https://ops.aps.anl.gov/downloads/SDDSToolKit-5.11-1.debian.13.x86_64.rpm
curl -LO https://ops.aps.anl.gov/downloads/elegant-2026.3.0-1.debian.13.openmpi.x86_64.rpm
```

可选：查看 RPM 包包含的文件。

```bash
rpm -qpl SDDSToolKit-5.11-1.debian.13.x86_64.rpm | less
rpm -qpl elegant-2026.3.0-1.debian.13.openmpi.x86_64.rpm | less
```

## 4. 安装 SDDS ToolKit

```bash
sudo alien -i SDDSToolKit-5.11-1.debian.13.x86_64.rpm
```

检查：

```bash
which sddsplot
which sddsprocess
which sddsprintout
```

看到 `/usr/bin/sddsplot`、`/usr/bin/sddsprocess`、`/usr/bin/sddsprintout` 等路径，说明 SDDS 工具链安装成功。

## 5. 安装 elegant / Pelegant

```bash
sudo alien -i elegant-2026.3.0-1.debian.13.openmpi.x86_64.rpm
```

检查：

```bash
which elegant
which Pelegant
which elegantRingAnalysis
```

通常应看到 `/usr/bin/elegant`、`/usr/bin/Pelegant` 和 `/usr/bin/elegantRingAnalysis`。

## 6. 配置 RPN_DEFNS

```bash
cd ~
curl -LO https://ops.aps.anl.gov/downloads/defns.rpn
mv -f defns.rpn ~/.defns.rpn
```

zsh 用户执行：

```bash
cat >> ~/.zshrc << 'EOF'

# elegant / SDDS
export RPN_DEFNS=$HOME/.defns.rpn
export HOST_ARCH=linux-x86_64
export EPICS_HOST_ARCH=linux-x86_64
EOF
source ~/.zshrc
```

bash 用户将上面的 `~/.zshrc` 和 `source ~/.zshrc` 替换为 `~/.bashrc` 和 `source ~/.bashrc`。

检查：

```bash
echo "$RPN_DEFNS"
```

应输出类似 `/home/你的用户名/.defns.rpn` 的路径。

## 7. 检查动态库依赖

```bash
ldd "$(which elegant)" | grep "not found"
ldd "$(which Pelegant)" | grep "not found"
```

没有任何输出即表示依赖完整。若出现缺失库，补装依赖后重新检查：

```bash
sudo apt install -y \
  openmpi-bin libopenmpi-dev \
  libgsl-dev libpng-dev libreadline-dev
ldd "$(which elegant)" | grep "not found"
ldd "$(which Pelegant)" | grep "not found"
```

## 8. 验证 elegant 单机版

```bash
elegant
elegant your_input.ele
```

直接输入 `elegant` 能进入提示或显示版本信息，即表示安装成功。

## 9. 验证 Pelegant 并行版

```bash
mpirun --version
mpirun -np 2 Pelegant your_input.ele
```

普通 lattice 计算或小粒子数跟踪使用 `elegant your_input.ele`；大粒子数、多误差种子或多工况扫描可使用 `mpirun -np 4 Pelegant your_input.ele`。

## 一键安装命令

以下命令默认当前 shell 为 zsh；bash 用户按第 6 节说明替换 shell 配置文件。

```bash
sudo apt update
sudo apt install -y \
  curl alien rpm tcsh \
  openmpi-bin libopenmpi-dev \
  libgsl-dev libpng-dev libreadline-dev
mkdir -p ~/software/elegant_install_debian13
cd ~/software/elegant_install_debian13
curl -LO https://ops.aps.anl.gov/downloads/SDDSToolKit-5.11-1.debian.13.x86_64.rpm
curl -LO https://ops.aps.anl.gov/downloads/elegant-2026.3.0-1.debian.13.openmpi.x86_64.rpm
sudo alien -i SDDSToolKit-5.11-1.debian.13.x86_64.rpm
sudo alien -i elegant-2026.3.0-1.debian.13.openmpi.x86_64.rpm
cd ~
curl -LO https://ops.aps.anl.gov/downloads/defns.rpn
mv -f defns.rpn ~/.defns.rpn
cat >> ~/.zshrc << 'EOF'

# elegant / SDDS
export RPN_DEFNS=$HOME/.defns.rpn
export HOST_ARCH=linux-x86_64
export EPICS_HOST_ARCH=linux-x86_64
EOF
source ~/.zshrc
which elegant
which Pelegant
which sddsplot
ldd "$(which elegant)" | grep "not found"
ldd "$(which Pelegant)" | grep "not found"
elegant
```

## `alien -i` 失败时的手动解包方案

如果出现类似 Debian 11 的 `dh_usrlocal` 报错，可绕过 `alien` 的打包步骤：

```bash
sudo apt install -y rpm2cpio cpio
cd ~/software/elegant_install_debian13
mkdir -p elegant_manual_extract
cd elegant_manual_extract
rpm2cpio ../elegant-2026.3.0-1.debian.13.openmpi.x86_64.rpm | cpio -idmv
sudo cp -a usr/bin/* /usr/local/bin/
```

然后验证：

```bash
which elegant
which Pelegant
elegant
```

该方式同样会安装 `elegant` 和 `Pelegant`，只是绕开了 `alien` 的 Debian 打包步骤。
