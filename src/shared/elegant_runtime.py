from __future__ import annotations

import subprocess
from pathlib import Path


def run_elegant_input(
    ele_name: str,
    log_name: str,
    *,
    workdir: str | Path,
) -> Path:
    workdir_path = Path(workdir)
    log_path = Path(log_name)
    if not log_path.is_absolute():
        log_path = workdir_path / log_path

    try:
        with log_path.open("w", encoding="utf-8") as log_file:
            subprocess.run(
                ["elegant", ele_name],
                cwd=str(workdir_path),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                check=True,
            )
    except FileNotFoundError as exc:
        raise RuntimeError("elegant executable is not available in PATH.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"elegant {ele_name} failed; see {log_path}") from exc

    return log_path
