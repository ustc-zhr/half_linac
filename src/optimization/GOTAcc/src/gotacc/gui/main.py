from __future__ import annotations

import os
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _candidate_qt_platform_paths() -> list[Path]:
    candidates: list[Path] = []

    executable = Path(sys.executable).resolve()
    current_env = executable.parents[1] if executable.parent.name == "bin" else None
    if current_env is not None:
        candidates.append(
            current_env / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages" / "PyQt5" / "Qt5" / "plugins" / "platforms"
        )
        candidates.append(current_env / "plugins" / "platforms")

    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix:
        candidates.append(
            Path(conda_prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages" / "PyQt5" / "Qt5" / "plugins" / "platforms"
        )
        candidates.append(Path(conda_prefix) / "plugins" / "platforms")

    home = Path.home()
    gotacc_env = home / "anaconda3" / "envs" / "gotacc_env"
    candidates.append(
        gotacc_env / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages" / "PyQt5" / "Qt5" / "plugins" / "platforms"
    )

    candidates.extend(
        [
            Path("/usr/lib/x86_64-linux-gnu/qt5/plugins/platforms"),
            Path("/usr/lib/qt5/plugins/platforms"),
        ]
    )

    deduped: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key not in seen:
            seen.add(key)
            deduped.append(path)
    return deduped


def _is_qt_platform_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    expected = ("libqoffscreen.so", "libqminimal.so", "libqxcb.so")
    return any((path / name).exists() for name in expected)


def _configure_runtime_environment() -> None:
    cache_dir = _repo_root() / ".cache" / "matplotlib"
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(cache_dir))

    if "QT_QPA_PLATFORM_PLUGIN_PATH" not in os.environ:
        for path in _candidate_qt_platform_paths():
            if _is_qt_platform_dir(path):
                os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(path)
                break

_configure_runtime_environment()

from PyQt5.QtWidgets import QApplication

try:
    from .theme import apply_theme
    from .views.main_window import MainWindow
except ImportError:
    from theme import apply_theme
    from views.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    apply_theme(app)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
