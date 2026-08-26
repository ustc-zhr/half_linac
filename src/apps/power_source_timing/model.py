from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping


DEVICES = ("hv", "llrf", "ssa", "kly")
WAVEFORM_DEVICES = DEVICES + ("pickup",)
QUANTITIES = ("delay", "width")
ValueKey = tuple[str, str]


def _finite(value: float, name: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite.")
    return number


@dataclass
class TimingValues:
    minimum_us: float = 0.0
    devices: tuple[str, ...] = DEVICES
    target: dict[ValueKey, float] = field(default_factory=dict)
    setpoint: dict[ValueKey, float] = field(default_factory=dict)
    readback: dict[ValueKey, float] = field(default_factory=dict)
    enabled: dict[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        normalized = tuple(str(device).strip().lower() for device in self.devices)
        if not normalized:
            raise ValueError("At least one timing device is required.")
        if len(set(normalized)) != len(normalized):
            raise ValueError("Timing devices must be unique.")
        unsupported = [device for device in normalized if device not in DEVICES]
        if unsupported:
            raise ValueError(f"Unsupported timing device: {unsupported[0]!r}.")
        self.devices = normalized

    def sync_setpoint(
        self,
        device: str,
        quantity: str,
        value: float,
        *,
        follow_target: bool,
    ) -> None:
        self._validate_key(device, quantity)
        number = _finite(value, f"{device} {quantity} setpoint")
        key = (device, quantity)
        self.setpoint[key] = number
        if follow_target or key not in self.target:
            self.target[key] = number

    def sync_readback(self, device: str, quantity: str, value: float) -> None:
        self._validate_key(device, quantity)
        self.readback[(device, quantity)] = _finite(
            value, f"{device} {quantity} readback"
        )

    def set_enabled(self, device: str, enabled: bool) -> None:
        self._validate_device(device)
        self.enabled[device] = bool(enabled)

    def request_value(
        self, device: str, quantity: str, value: float
    ) -> dict[ValueKey, float]:
        self._validate_key(device, quantity)
        number = _finite(value, f"{device} {quantity} target")
        if number < self.minimum_us:
            raise ValueError(
                f"{device.upper()} {quantity} target {number:g} us is below "
                f"{self.minimum_us:g} us."
            )
        key = (device, quantity)
        self.target[key] = number
        return {key: number}

    def shift_one(
        self, device: str, quantity: str, delta_us: float
    ) -> dict[ValueKey, float]:
        self._validate_key(device, quantity)
        key = (device, quantity)
        if key not in self.target:
            raise ValueError(f"{device.upper()} {quantity} target is not available yet.")
        return self.request_value(device, quantity, self.target[key] + float(delta_us))

    def shift_group_delay(self, delta_us: float) -> dict[ValueKey, float]:
        missing = [
            device for device in self.devices if (device, "delay") not in self.target
        ]
        if missing:
            raise ValueError(
                "Delay targets are not available yet: "
                + ", ".join(device.upper() for device in missing)
            )
        targets = {
            (device, "delay"): self.target[(device, "delay")] + float(delta_us)
            for device in self.devices
        }
        below = {
            device: value
            for (device, _quantity), value in targets.items()
            if value < self.minimum_us
        }
        if below:
            detail = ", ".join(
                f"{device.upper()}={value:g}" for device, value in below.items()
            )
            raise ValueError(f"Delay target below {self.minimum_us:g} us: {detail}.")
        self.target.update(targets)
        return targets

    def matches(self, device: str, quantity: str, tolerance_us: float) -> bool | None:
        key = (device, quantity)
        if key not in self.target or key not in self.readback:
            return None
        return abs(self.readback[key] - self.target[key]) <= float(tolerance_us)

    def _validate_device(self, device: str) -> None:
        if device not in DEVICES:
            raise ValueError(f"Unsupported timing device: {device!r}.")
        if device not in self.devices:
            raise ValueError(
                f"Timing device {device!r} is not available in the selected group."
            )

    def _validate_key(self, device: str, quantity: str) -> None:
        self._validate_device(device)
        if quantity not in QUANTITIES:
            raise ValueError(f"Unsupported timing quantity: {quantity!r}.")


@dataclass
class CoalescingWriteQueue:
    pending: dict[ValueKey, float] = field(default_factory=dict)
    inflight: dict[ValueKey, float] = field(default_factory=dict)

    def enqueue(self, values: Mapping[ValueKey, float]) -> None:
        self.pending.update({key: float(value) for key, value in values.items()})

    @property
    def busy(self) -> bool:
        return bool(self.inflight)

    @property
    def active_keys(self) -> set[ValueKey]:
        return set(self.inflight) | set(self.pending)

    def begin_next(self) -> dict[ValueKey, float]:
        if self.inflight or not self.pending:
            return {}
        self.inflight = dict(self.pending)
        self.pending.clear()
        return dict(self.inflight)

    def finish(self) -> dict[ValueKey, float]:
        completed = dict(self.inflight)
        self.inflight.clear()
        return completed

    def clear(self) -> None:
        self.pending.clear()
        self.inflight.clear()
