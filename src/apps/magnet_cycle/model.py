"""Deterministic cycle state machine; no EPICS or Qt dependencies."""
from dataclasses import dataclass
import math


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
            raise ValueError("循环次数必须是正整数")
        for name in ('rate', 'hold', 'tolerance', 'stable', 'timeout', 'period'):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} 必须是有限正数")
        if self.timeout <= self.stable + self.hold:
            raise ValueError("超时必须大于稳定时间与保持时间之和")


def finite(value):
    if value is None:
        raise ValueError("电流读取失败")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("电流不是有限值")
    return value


class Cycle:
    def __init__(self, low, high, initial, readback, parameters, now):
        self.low, self.high, self.initial = map(finite, (low, high, initial))
        self.p = parameters
        if not self.low < self.high or not self.low <= self.initial <= self.high:
            raise ValueError("初始设定超出范围或范围无效")
        if abs(finite(readback) - self.initial) > self.p.tolerance:
            raise ValueError("启动前设定与读回尚未一致")
        self.targets = [self.low] + [v for _ in range(self.p.cycles) for v in (self.high, self.low)] + [self.initial]
        self.index = 0
        self.command = self.initial
        self.last_tick = now
        self.wait_since = None
        self.stable_since = None
        self.status = '准备'

    @property
    def done(self):
        return self.index == len(self.targets)

    def tick(self, now, setpoint, readback):
        """Return at most one bounded command. Caller must abort on write failure."""
        setpoint, readback = finite(setpoint), finite(readback)
        if not math.isclose(setpoint, self.command, rel_tol=1e-7, abs_tol=1e-6):
            raise RuntimeError("设定值被外部修改，已停止")
        if self.done:
            return None
        dt = min(max(now - self.last_tick, 0), self.p.period)
        self.last_tick = now
        target = self.targets[self.index]
        label = ('初始到下限' if self.index == 0 else
                 '恢复初始设定' if self.index == len(self.targets) - 1 else
                 f'循环 {(self.index + 1) // 2}/{self.p.cycles} ' + ('上限' if self.index % 2 else '下限'))
        if abs(readback - self.command) > self.p.tolerance:
            self.stable_since = None
            self.status = label + ' · 等待读回'
            self._check_wait(now)
            return None
        if self.command != target:
            self.wait_since = self.stable_since = None
            step = self.p.rate * dt
            if not step:
                return None
            self.command += max(-step, min(step, target - self.command))
            self.command = min(self.high, max(self.low, self.command))
            self.status = label + ' · 斜坡'
            return self.command
        self._check_wait(now)
        if self.stable_since is None:
            self.stable_since = now
        elapsed = now - self.stable_since
        self.status = label + (' · 稳定' if elapsed < self.p.stable else ' · 保持')
        hold = 0 if self.index == len(self.targets) - 1 else self.p.hold
        if elapsed >= self.p.stable + hold:
            self.index += 1
            self.wait_since = self.stable_since = None
            if self.done:
                self.status = '完成'
        return None

    def _check_wait(self, now):
        if self.wait_since is None:
            self.wait_since = now
        if now - self.wait_since > self.p.timeout:
            raise TimeoutError("读回跟踪或端点稳定超时")
