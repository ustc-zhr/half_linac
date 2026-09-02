"""Shared data contracts for adaptive energy tuning."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EnergyObservation:
    """One validated measurement at an actuator value."""

    actuator_value: float
    has_beam: bool
    brightness: float | None = None
    center_mm: float | None = None
    center_offset_mm: float | None = None
    valid_frames: int = 0
    total_frames: int = 0
    fit_method: str | None = None
    fit_r_squared: float | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def energy(self) -> float:
        """Compatibility alias used by existing tuner result dictionaries."""
        return self.actuator_value


@dataclass(frozen=True)
class EnergyTuneResult:
    """Final result returned by a future shared pipeline runner."""

    ok: bool
    actuator_value: float | None
    status: str
    message: str | None = None
    observations: tuple[EnergyObservation, ...] = ()
    diagnostics: dict[str, Any] = field(default_factory=dict)
