from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from PyQt5.QtCore import QThread, pyqtSignal

from half_linac.src.shared.machine_profile.resolver import resolve_write_target
from half_linac.src.shared.machine_profile.write_control import require_workflow_write_allowed
from half_linac.src.shared.machine_state import MachineStateSnapshot, SampleQuality, StateClass, subsystem_capture_group


@dataclass
class RestoreCandidate:
    entry: Any
    pv_name: str | None
    saved_value: float | None
    current_value: float | None
    delta: float | None
    writable: bool
    selected: bool = False
    unavailable_reason: str = ""


@dataclass(frozen=True)
class RestoreItemResult:
    key: str
    status: str
    target_value: float | None
    readback_value: float | None
    error: str = ""


@dataclass(frozen=True)
class RestoreResult:
    started_at: str
    finished_at: str
    source_snapshot_id: str
    backend: str
    items: tuple[RestoreItemResult, ...]

    @property
    def success_count(self): return sum(i.status == "success" for i in self.items)
    @property
    def failed_count(self): return sum(i.status == "failed" for i in self.items)
    @property
    def skipped_count(self): return sum(i.status == "skipped" for i in self.items)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def build_restore_candidates(snapshot: MachineStateSnapshot, profile, backend: str,
                             read: Callable[[str], Any]) -> tuple[RestoreCandidate, ...]:
    candidates = []
    for entry in snapshot.entries:
        if entry.state_class != StateClass.SETTING or entry.logical_channel.casefold().endswith("_enable"):
            continue
        if subsystem_capture_group(entry.element_kind, entry.logical_channel) is None or entry.quality != SampleQuality.OK:
            continue
        saved = _number(entry.value)
        if saved is None:
            candidates.append(RestoreCandidate(entry, None, None, None, None, False, False, "invalid saved value")); continue
        try:
            target = resolve_write_target(profile, entry.element_id, logical_channel=entry.logical_channel, mode=backend)
            current = _number(read(target.pv_name))
            reason = "" if current is not None else "current value unavailable"
            candidates.append(RestoreCandidate(entry, target.pv_name, saved, current,
                None if current is None else saved - current, True, current is not None and not math.isclose(saved, current, rel_tol=1e-9, abs_tol=1e-9), reason))
        except Exception as exc:
            candidates.append(RestoreCandidate(entry, None, saved, None, None, False, False, str(exc)))
    return tuple(candidates)


class RestoreWorker(QThread):
    progress = pyqtSignal(object, int, int)
    finished_result = pyqtSignal(object)

    def __init__(self, context, candidates: Iterable[RestoreCandidate], parent=None, *, caput=None, caget=None):
        super().__init__(parent); self.context = context; self.candidates = tuple(candidates)
        self.caput = caput; self.caget = caget; self._stop = threading.Event()

    def request_stop(self): self._stop.set()

    def run(self):
        started = datetime.now(timezone.utc).isoformat(); results = []
        try:
            require_workflow_write_allowed(self.context, "control_points", "Machine snapshot restore")
        except Exception as exc:
            results = [RestoreItemResult(c.entry.key, "skipped", c.saved_value, None, str(exc)) for c in self.candidates]
        else:
            if self.caput is None or self.caget is None:
                from epics import caput, caget
                self.caput, self.caget = caput, caget
            total = len(self.candidates)
            for index, candidate in enumerate(self.candidates, 1):
                if self._stop.is_set():
                    results.append(RestoreItemResult(candidate.entry.key, "skipped", candidate.saved_value, None, "stopped"))
                elif not candidate.selected or not candidate.writable or candidate.pv_name is None:
                    results.append(RestoreItemResult(candidate.entry.key, "skipped", candidate.saved_value, None, candidate.unavailable_reason or "not selected"))
                else:
                    try:
                        self.caput(candidate.pv_name, candidate.saved_value, wait=True, timeout=5)
                        actual = _number(self.caget(candidate.pv_name))
                        ok = actual is not None and math.isclose(actual, candidate.saved_value, rel_tol=1e-9, abs_tol=1e-9)
                        results.append(RestoreItemResult(candidate.entry.key, "success" if ok else "failed", candidate.saved_value, actual, "" if ok else "setpoint readback mismatch"))
                    except Exception as exc:
                        results.append(RestoreItemResult(candidate.entry.key, "failed", candidate.saved_value, None, f"{type(exc).__name__}: {exc}"))
                self.progress.emit(results[-1], index, total)
        self.finished_result.emit(RestoreResult(started, datetime.now(timezone.utc).isoformat(), self.context.profile.machine.id, self.context.control_backend.name, tuple(results)))
