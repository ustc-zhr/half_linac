"""Deterministic cycle state machine; no EPICS or Qt dependencies."""
from dataclasses import dataclass
import math


READY = 'Ready'
COMPLETE = 'Complete'


@dataclass(frozen=True)
class Parameters:
    cycles: int = 3
    rate: float = 1.0
    hold: float = 2.0
    tolerance: float = 0.05
    stable: float = 1.0
    timeout: float = 30.0
    period: float = 0.2

    def __post_init__(self):
        if isinstance(self.cycles, bool) or not isinstance(self.cycles, int) or self.cycles < 1:
            raise ValueError("Cycles must be a positive integer.")
        for name in ('rate', 'hold', 'tolerance', 'stable', 'timeout', 'period'):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name.capitalize()} must be finite and greater than zero.")
        if self.timeout <= self.stable + self.hold:
            raise ValueError("Timeout must exceed the sum of stable time and endpoint hold.")


def finite(value):
    if value is None:
        raise ValueError("Could not read current.")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("Current must be finite.")
    return value


class Cycle:
    def __init__(self, low, high, initial, readback, parameters, now):
        self.low, self.high, self.initial = map(finite, (low, high, initial))
        self.p = parameters
        if not self.low < self.high:
            raise ValueError("The cycle upper endpoint must exceed the lower endpoint.")
        if not self.low <= self.initial <= self.high:
            raise ValueError(f"Initial setpoint {self.initial} A is outside the cycle range [{self.low}, {self.high}] A.")
        if abs(finite(readback) - self.initial) > self.p.tolerance:
            raise ValueError("The initial setpoint and readback differ by more than the tolerance.")
        self.targets = [self.low] + [v for _ in range(self.p.cycles) for v in (self.high, self.low)] + [self.initial]
        self.index = 0
        self.command = self.initial
        self.last_tick = now
        self.wait_since = None
        self.stable_since = None
        self.status = READY

    @property
    def done(self):
        return self.index == len(self.targets)

    def tick(self, now, setpoint, readback):
        """Return at most one bounded command. Caller must abort on write failure."""
        setpoint, readback = finite(setpoint), finite(readback)
        if not math.isclose(setpoint, self.command, rel_tol=1e-7, abs_tol=1e-6):
            raise RuntimeError("The setpoint changed outside Magnet Cycle.")
        if self.done:
            return None
        dt = min(max(now - self.last_tick, 0), self.p.period)
        self.last_tick = now
        target = self.targets[self.index]
        if self.index == 0:
            label = 'Go to lower'
        elif self.index == len(self.targets) - 1:
            label = 'Restore initial'
        else:
            endpoint = 'Upper' if self.index % 2 else 'Lower'
            label = f'Cycle {(self.index + 1) // 2}/{self.p.cycles} · {endpoint}'
        if abs(readback - self.command) > self.p.tolerance:
            self.stable_since = None
            self.status = label + ' · Waiting for readback'
            self._check_wait(now)
            return None
        if self.command != target:
            self.wait_since = self.stable_since = None
            step = self.p.rate * dt
            if not step:
                return None
            self.command += max(-step, min(step, target - self.command))
            self.command = min(self.high, max(self.low, self.command))
            self.status = label + ' · Ramping'
            return self.command
        self._check_wait(now)
        if self.stable_since is None:
            self.stable_since = now
        elapsed = now - self.stable_since
        self.status = label + (' · Settling' if elapsed < self.p.stable else ' · Holding')
        hold = 0 if self.index == len(self.targets) - 1 else self.p.hold
        if elapsed >= self.p.stable + hold:
            self.index += 1
            self.wait_since = self.stable_since = None
            if self.done:
                self.status = COMPLETE
        return None

    def _check_wait(self, now):
        if self.wait_since is None:
            self.wait_since = now
        if now - self.wait_since > self.p.timeout:
            raise TimeoutError("Readback tracking or settling timed out.")
