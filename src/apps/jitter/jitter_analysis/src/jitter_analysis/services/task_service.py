from __future__ import annotations

from ..domain.types import RunStatus


class TaskService:
    def __init__(self) -> None:
        self.status = RunStatus.IDLE
        self.active_plan = None

    def start(self, plan) -> None:
        self.active_plan = plan
        self.status = RunStatus.RUNNING

    def stop(self) -> None:
        self.status = RunStatus.STOPPED

    def reset(self) -> None:
        self.active_plan = None
        self.status = RunStatus.IDLE
