from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class TimedAcquisitionPlan:
    target_ids: list[str]
    sample_interval_sec: float
    sample_count: int
    timeout_sec: float


@dataclass(slots=True)
class KnobScanPlan:
    knob_id: str
    target_ids: list[str]
    scan_values: list[float]
    settle_delay_sec: float
    sample_count_per_step: int
    restore_initial_value: bool = True
    max_wait_sec: float = 3.0
    notes: str = ""
    per_step_interval_sec: float | None = None
