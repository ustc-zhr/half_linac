# Debian 13 控制室创建 HALF Linac 桌面快捷启动

本文用于在 Debian 13 控制室电脑上，为 `half_linac` 创建桌面快捷启动，并确保 Launcher、子 App 和 Elegant 使用同一套运行环境。

## 1. 确认仓库和 Conda 环境

进入仓库目录：

```bash
cd /half_hla/half_linac
```

确认 Conda 环境和 PyQt5：

```bash
conda env list
conda activate half_linac
which python
python -c "import PyQt5; print('PyQt5 OK')"
```

确认 Elegant 的 RPN 定义文件：

```bash
echo "$RPN_DEFNS"
test -r "$RPN_DEFNS" && echo "RPN definitions OK"
```

如果 `RPN_DEFNS` 只在终端中存在，检查其来源：

```bash
grep -R "RPN_DEFNS" \
  ~/.bashrc \
  ~/.bash_profile \
  ~/.profile \
  ~/.zshrc \
  /etc/profile \
  /etc/profile.d \
  /etc/bash.bashrc \
  2>/dev/null
```

桌面快捷方式不会自动读取交互式 shell 的 `~/.zshrc`，因此关键环境变量必须写入启动脚本。

## 2. 创建启动脚本

```bash
mkdir -p ~/.local/bin ~/.local/state/half_linac
nano ~/.local/bin/start-half-linac
```

写入以下内容，并按实际电脑修改 `REPO_DIR`、`CONDA_BASE` 和 `RPN_DEFNS`：

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/half_hla/half_linac"
CONDA_BASE="/home/opi-acc/anaconda3"
LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/half_linac"

mkdir -p "$LOG_DIR"
cd "$REPO_DIR"

# 初始化 Conda
source "$CONDA_BASE/etc/profile.d/conda.sh"
conda activate half_linac

# 加载仓库环境
source "$REPO_DIR/scripts/common.sh"

# Elegant 所需的 RPN 定义文件
export RPN_DEFNS="/home/opi-acc/.defns.rpn"

# 记录启动环境，便于排查桌面启动问题
{
    echo "=== $(date) ==="
    echo "python=$(command -v python)"
    echo "python3=$(command -v python3)"
    echo "elegant=$(command -v elegant)"
    echo "RPN_DEFNS=${RPN_DEFNS:-<unset>}"
} >>"$LOG_DIR/control-room.log"

exec python "$REPO_DIR/src/apps/launcher/main.py" \
    >>"$LOG_DIR/control-room.log" 2>&1
```

添加执行权限：

```bash
chmod +x ~/.local/bin/start-half-linac
```

## 3. 先从终端测试启动脚本

先不要直接测试桌面图标：

```bash
~/.local/bin/start-half-linac
```

检查日志：

```bash
tail -n 100 ~/.local/state/half_linac/control-room.log
```

应看到类似内容：

```text
python=/home/.../envs/half_linac/bin/python
python3=/home/.../envs/half_linac/bin/python3
elegant=/usr/bin/elegant
RPN_DEFNS=/home/opi-acc/.defns.rpn
```

如果显示 `RPN_DEFNS=<unset>`，说明启动脚本中的路径或环境变量配置不正确。

## 4. 创建应用菜单快捷方式

```bash
mkdir -p ~/.local/share/applications
nano ~/.local/share/applications/half-linac.desktop
```

写入：

```ini
[Desktop Entry]
Type=Application
Name=HALF Linac Control Room
Comment=Start HALF Linac Control Room
Exec=/home/opi-acc/.local/bin/start-half-linac
Icon=applications-system
Terminal=false
Categories=Science;Utility;
StartupNotify=true
```

修改权限并刷新应用菜单：

```bash
chmod +x ~/.local/share/applications/half-linac.desktop
update-desktop-database ~/.local/share/applications 2>/dev/null || true
```

之后可在 Debian 应用菜单搜索 `HALF Linac Control Room`。

## 5. 创建桌面图标

```bash
cp ~/.local/share/applications/half-linac.desktop ~/Desktop/
chmod +x ~/Desktop/half-linac.desktop
```

如果桌面显示“未信任的启动器”：

```bash
gio set ~/Desktop/half-linac.desktop metadata::trusted true
```

## 6. 验证 App 和 Elegant

通过桌面图标启动 Launcher，测试：

1. 打开 `Dispersion Correction`。
2. 点击 `Design Model`。
3. 确认 Elegant 模型正常生成。

如果 Elegant 报错，查看：

```bash
tail -n 120 /half_hla/half_linac/runtime/model_backend/half/simulation/optics/optics.log
```

## 7. 常见问题

### Launcher 能打开，但 App 无法启动

通常是 App 子进程使用了系统 Python。确认启动脚本包含：

```bash
source "$CONDA_BASE/etc/profile.d/conda.sh"
conda activate half_linac
```

并检查：

```bash
command -v python
command -v python3
```

两者都应指向 `.../envs/half_linac/bin/`。

### 终端能运行，桌面图标不能运行

终端会加载 `~/.zshrc` 或 `~/.bashrc`，桌面快捷方式通常不会。应把 `RPN_DEFNS` 等关键变量显式写入启动脚本。

### Design Model 报 `RPN_DEFNS environment variable undefined`

确认：

```bash
command -v elegant
echo "$RPN_DEFNS"
test -r "$RPN_DEFNS" && echo "RPN definitions OK"
```

启动脚本必须包含有效路径，例如：

```bash
export RPN_DEFNS="/home/opi-acc/.defns.rpn"
```

### 查看完整启动日志

```bash
tail -n 100 ~/.local/state/half_linac/control-room.log
```

该日志记录 Python、Elegant 和 `RPN_DEFNS` 的实际路径，是排查桌面启动问题的首选位置。
