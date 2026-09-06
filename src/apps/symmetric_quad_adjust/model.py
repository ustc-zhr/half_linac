from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class QuadPair:
    left: str
    right: str

    @property
    def elements(self) -> tuple[str, str]:
        return self.left, self.right


@dataclass
class CoalescingK1Queue:
    """Keep only the newest queued target for each magnet during rapid adjustment."""

    pending: dict[str, float] = field(default_factory=dict)
    inflight: dict[str, float] = field(default_factory=dict)

    @property
    def busy(self) -> bool:
        return bool(self.inflight)

    def enqueue(self, targets: Mapping[str, float]) -> None:
        self.pending.update(
            {name: _finite(value, f"{name} K1") for name, value in targets.items()}
        )

    def begin_next(self) -> dict[str, float]:
        if self.inflight or not self.pending:
            return {}
        self.inflight = dict(self.pending)
        self.pending.clear()
        return dict(self.inflight)

    def finish(self) -> dict[str, float]:
        completed = dict(self.inflight)
        self.inflight.clear()
        return completed

    def desired_values(self, observed: Mapping[str, float]) -> dict[str, float]:
        desired = {name: float(value) for name, value in observed.items()}
        desired.update(self.inflight)
        desired.update(self.pending)
        return desired

    def clear(self) -> None:
        self.pending.clear()
        self.inflight.clear()


def shifted_pair_targets(
    pair: QuadPair,
    values: Mapping[str, float],
    delta: float,
) -> dict[str, float]:
    """Apply the same K1 delta to both members while preserving their relation."""
    amount = _finite(delta, "K1 step")
    missing = [element for element in pair.elements if element not in values]
    if missing:
        raise ValueError("K1 is not available for: " + ", ".join(missing))
    return {
        element: _finite(values[element], f"{element} K1") + amount
        for element in pair.elements
    }


def single_target(element_id: str, value: float) -> dict[str, float]:
    element = str(element_id).strip()
    if not element:
        raise ValueError("Quadrupole name must not be empty.")
    return {element: _finite(value, f"{element} K1")}


def _finite(value: float, name: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite.")
    return number
