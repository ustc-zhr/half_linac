from __future__ import annotations

import math
import subprocess
from time import monotonic
from collections.abc import Mapping
from pathlib import Path


def run_elegant_input(
    ele_name: str,
    log_name: str,
    *,
    workdir: str | Path,
    timeout_s: float | None = None,
    env: Mapping[str, str] | None = None,
    stop_requested=None,
) -> Path:
    if timeout_s is not None and (not math.isfinite(timeout_s) or timeout_s <= 0):
        raise ValueError("Elegant timeout must be positive and finite.")
    workdir_path = Path(workdir)
    log_path = Path(log_name)
    if not log_path.is_absolute():
        log_path = workdir_path / log_path

    try:
        with log_path.open("w", encoding="utf-8") as log_file:
            command = ["elegant", ele_name]
            options = {"cwd": str(workdir_path), "stdout": log_file, "stderr": subprocess.STDOUT}
            if env is not None:
                options["env"] = env
            if stop_requested is None:
                if timeout_s is not None:
                    options["timeout"] = timeout_s
                subprocess.run(command, check=True, **options)
            else:
                _run_stoppable(command, options, timeout_s, stop_requested)
    except FileNotFoundError as exc:
        raise RuntimeError("elegant executable is not available in PATH.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"elegant {ele_name} failed; see {log_path}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"elegant {ele_name} timed out after {timeout_s:g}s; see {log_path}") from exc

    return log_path


def _run_stoppable(command, options, timeout_s, stop_requested):
    deadline = None if timeout_s is None else monotonic() + timeout_s
    if stop_requested():
        raise KeyboardInterrupt
    process = subprocess.Popen(command, **options)
    try:
        while True:
            if stop_requested():
                raise KeyboardInterrupt
            remaining = 0.1 if deadline is None else deadline - monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout_s)
            try:
                code = process.wait(timeout=min(0.1, remaining))
            except subprocess.TimeoutExpired:
                continue
            if stop_requested():
                raise KeyboardInterrupt
            if code:
                raise subprocess.CalledProcessError(code, command)
            return
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
