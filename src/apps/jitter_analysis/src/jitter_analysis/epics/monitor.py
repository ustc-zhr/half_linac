from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class MonitorValue:
    pv_name: str
    value: float | int | str | None
    timestamp: datetime
    connected: bool
