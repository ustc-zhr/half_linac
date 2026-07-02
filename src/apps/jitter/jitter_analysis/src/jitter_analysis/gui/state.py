from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.types import RunStatus


@dataclass(slots=True)
class AppState:
    config_path: str | None = None
    save_dir: str = "runs"
    selected_object_ids: list[str] = field(default_factory=list)
    selected_knob_ids: list[str] = field(default_factory=list)
    active_knob_id: str | None = None
    active_preset_id: str | None = None
    run_status: RunStatus = RunStatus.IDLE
