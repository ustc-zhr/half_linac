from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .types import RunStatus


@dataclass(slots=True)
class LogEvent:
    timestamp: datetime
    level: str
    message: str


@dataclass(slots=True)
class RunStatusEvent:
    timestamp: datetime
    status: RunStatus
    detail: str = ""
