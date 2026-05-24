from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

import fcntl


def _as_path(pathlike: str | os.PathLike[str]) -> Path:
    return Path(pathlike)


def _lock_path(jsonpath: str | os.PathLike[str]) -> Path:
    path = _as_path(jsonpath)
    return path.with_name(f".{path.name}.lock")


@contextmanager
def _exclusive_lock(jsonpath: str | os.PathLike[str]):
    lock_path = _lock_path(jsonpath)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _write_runtime_state_unlocked(jsonpath: str | os.PathLike[str], data: Any) -> None:
    path = _as_path(jsonpath)
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=4)
    os.replace(tmp_path, path)


def read_runtime_state(jsonpath: str | os.PathLike[str]) -> Any:
    path = _as_path(jsonpath)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_runtime_state(jsonpath: str | os.PathLike[str], data: Any) -> None:
    path = _as_path(jsonpath)
    with _exclusive_lock(path):
        _write_runtime_state_unlocked(path, data)


def ensure_runtime_state(
    jsonpath: str | os.PathLike[str],
    build_initial_state: Callable[[], Any],
) -> bool:
    path = _as_path(jsonpath)
    with _exclusive_lock(path):
        if path.exists():
            return False

        data = build_initial_state()
        _write_runtime_state_unlocked(path, data)
        return True


def update_runtime_state(
    jsonpath: str | os.PathLike[str],
    mutator: Callable[[Any], bool],
) -> tuple[Any, bool]:
    path = _as_path(jsonpath)
    with _exclusive_lock(path):
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)

        changed = bool(mutator(data))
        if changed:
            _write_runtime_state_unlocked(path, data)

        return data, changed
