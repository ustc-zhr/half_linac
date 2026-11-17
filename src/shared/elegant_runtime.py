from __future__ import annotations

import math
import subprocess
from pathlib import Path


def run_elegant_input(
    ele_name: str,
    log_name: str,
    *,
    workdir: str | Path,
    timeout_s: float | None = None,
) -> Path:
    if timeout_s is not None and (not math.isfinite(timeout_s) or timeout_s <= 0):
        raise ValueError("Elegant timeout must be positive and finite.")
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
                **({"timeout": timeout_s} if timeout_s is not None else {}),
            )
    except FileNotFoundError as exc:
        raise RuntimeError("elegant executable is not available in PATH.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"elegant {ele_name} failed; see {log_path}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"elegant {ele_name} timed out after {timeout_s:g}s; see {log_path}") from exc

    return log_path
