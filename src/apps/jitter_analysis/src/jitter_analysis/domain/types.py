from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class RunMode(str, Enum):
    TIMED_ACQUISITION = "timed_acquisition"
    KNOB_SCAN = "knob_scan"
    MULTI_KNOB_RANDOM = "multi_knob_random"


class RunStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"
    COMPLETED = "completed"


@dataclass(slots=True)
class SampleRecord:
    pv_id: str
    value: float
    timestamp: datetime
    connected: bool = True
    severity: int = 0
    status: int = 0
    step_index: int | None = None
    batch_index: int | None = None


@dataclass(slots=True)
class ScanStepRecord:
    step_index: int
    target_value: float
    readback_value: float | None
    started_at: datetime
    settled_at: datetime | None = None
    samples: list[SampleRecord] = field(default_factory=list)


@dataclass(slots=True)
class MultiKnobStepRecord:
    step_index: int
    target_values: dict[str, float]
    readback_values: dict[str, float | None]
    started_at: datetime
    settled_at: datetime | None = None
    samples: list[SampleRecord] = field(default_factory=list)


@dataclass(slots=True)
class RunMetadata:
    run_id: str
    mode: RunMode
    created_at: datetime
    operator: str = ""
    machine: str = ""
    config_path: str = ""
    notes: str = ""


@dataclass(slots=True)
class RunResult:
    metadata: RunMetadata
    status: RunStatus
    samples: list[SampleRecord] = field(default_factory=list)
    steps: list[ScanStepRecord | MultiKnobStepRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    details: dict[str, object] = field(default_factory=dict)
