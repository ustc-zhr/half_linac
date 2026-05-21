from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Protocol

from PyQt5.QtCore import QThread

try:
    from ..workers.engine_worker import EngineWorker
except ImportError:  # pragma: no cover - local script fallback
    CURRENT_DIR = Path(__file__).resolve().parent
    GUI_ROOT = CURRENT_DIR.parent
    for path in (GUI_ROOT, GUI_ROOT / "workers"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    from engine_worker import EngineWorker


class RunSessionEvents(Protocol):
    def on_session_log(self, message: str) -> None: ...

    def on_session_warning(self, message: str) -> None: ...

    def on_session_status(self, payload: dict) -> None: ...

    def on_session_evaluation(self, payload: dict) -> None: ...

    def on_session_finished(self, payload: dict) -> None: ...

    def on_session_error(self, message: str) -> None: ...


class RunSession:
    def __init__(self, parent) -> None:
        self.parent = parent
        self._thread: Optional[QThread] = None
        self._worker: Optional[EngineWorker] = None

    @property
    def worker(self) -> Optional[EngineWorker]:
        return self._worker

    @property
    def thread(self) -> Optional[QThread]:
        return self._thread

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def has_worker(self) -> bool:
        return self._worker is not None

    def cleanup_if_idle(self) -> None:
        if self.is_running():
            return
        self._worker = None
        self._thread = None

    def request_pause(self) -> None:
        if self._worker is not None:
            self._worker.request_pause()

    def request_resume(self) -> None:
        if self._worker is not None:
            self._worker.request_resume()

    def request_stop(self) -> None:
        if self._worker is not None:
            self._worker.request_stop()

    def start(self, task: dict, *, events: RunSessionEvents) -> None:
        self.cleanup_if_idle()
        if self.is_running():
            raise RuntimeError("Run session is already active.")

        thread = QThread(self.parent)
        worker = EngineWorker(task)
        worker.moveToThread(thread)

        self._connect_worker_signals(worker, thread, events)

        self._thread = thread
        self._worker = worker
        thread.start()

    def _connect_worker_signals(
        self,
        worker: EngineWorker,
        thread: QThread,
        events: RunSessionEvents,
    ) -> None:
        thread.started.connect(worker.run)
        worker.sig_log.connect(events.on_session_log)
        worker.sig_warning.connect(events.on_session_warning)
        worker.sig_status.connect(events.on_session_status)
        worker.sig_evaluation.connect(events.on_session_evaluation)
        worker.sig_finished.connect(events.on_session_finished)
        worker.sig_error.connect(events.on_session_error)

        worker.sig_finished.connect(thread.quit)
        worker.sig_error.connect(lambda _msg: thread.quit())
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_finished_session)

    def _clear_finished_session(self) -> None:
        self._worker = None
        self._thread = None
