"""Device-independent actuator contracts for adaptive energy tuning."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol


class EnergyActuator(Protocol):
    """Minimal actuator surface required by tuning stages."""

    def read(self) -> float | None:
        """Return the current actuator value."""

    def set(self, value: float) -> None:
        """Move the actuator to ``value`` and wait until it is settled."""


class CallableEnergyActuator:
    """Adapter for existing read/set callables."""

    def __init__(self, read: Callable[[], float | None], set: Callable[[float], None]):
        self._read = read
        self._set = set

    def read(self) -> float | None:
        value = self._read()
        return None if value is None else float(value)

    def set(self, value: float) -> None:
        self._set(float(value))
