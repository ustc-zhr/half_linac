from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Mapping


class ControlLockError(RuntimeError):
    """Raised when a machine control resource is already owned."""


class EnergyControlLock:
    """Process-safe lock for coordinated energy control operations."""

    def __init__(self, path: Path, metadata: Mapping[str, Any]):
        self.path = Path(path)
        self.metadata = dict(metadata)
        self._stream = None

    @classmethod
    def for_machine(cls, machine_id: str, metadata: Mapping[str, Any]):
        safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(machine_id))
        return cls(Path("/tmp") / f"half_linac_{safe_id}_energy_control.lock", metadata)

    def acquire(self) -> None:
        if self._stream is not None:
            return
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            stream.seek(0)
            owner = stream.read().strip()
            stream.close()
            detail = owner or "another control operation"
            raise ControlLockError(f"Energy control is busy: {detail}") from exc
        self._stream = stream
        payload = {
            "pid": os.getpid(),
            "started_at": time.time(),
            **self.metadata,
        }
        stream.seek(0)
        stream.truncate()
        json.dump(payload, stream, sort_keys=True)
        stream.flush()

    def release(self) -> None:
        if self._stream is None:
            return
        import fcntl

        try:
            fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
        finally:
            self._stream.close()
            self._stream = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.release()

