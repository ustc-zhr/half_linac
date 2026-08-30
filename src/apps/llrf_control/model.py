from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class CoalescingWriteQueue:
    pending: dict[str, float] = field(default_factory=dict)
    requested: dict[str, float] = field(default_factory=dict)
    inflight: tuple[str, float] | None = None

    @property
    def busy(self) -> bool:
        return self.inflight is not None or bool(self.pending)

    def enqueue(self, quantity: str, value: float) -> None:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Write request must be finite.")
        self.pending[quantity] = number
        self.requested[quantity] = number

    def begin_next(self) -> tuple[str, float] | None:
        if self.inflight is not None or not self.pending:
            return None
        quantity = next(iter(self.pending))
        self.inflight = (quantity, self.pending.pop(quantity))
        return self.inflight

    def finish(self) -> tuple[str, float] | None:
        completed = self.inflight
        self.inflight = None
        return completed

    def cancel_pending(self, quantity: str) -> None:
        self.pending.pop(quantity, None)
        if self.inflight is None or self.inflight[0] != quantity:
            self.requested.pop(quantity, None)

    def expected_values(self, quantity: str) -> tuple[float, ...]:
        values = []
        if self.inflight is not None and self.inflight[0] == quantity:
            values.append(self.inflight[1])
        if quantity in self.pending:
            values.append(self.pending[quantity])
        return tuple(values)

    def acknowledge(self, quantity: str, value: float) -> None:
        requested = self.requested.get(quantity)
        if requested is None or self.expected_values(quantity):
            return
        if math.isclose(float(value), requested, abs_tol=1e-12):
            self.requested.pop(quantity, None)

    def fail(self, quantity: str, value: float) -> None:
        if quantity not in self.pending and math.isclose(
            self.requested.get(quantity, math.nan), value, abs_tol=1e-12
        ):
            self.requested.pop(quantity, None)

    def clear(self) -> None:
        self.pending.clear()
        self.requested.clear()
        self.inflight = None
