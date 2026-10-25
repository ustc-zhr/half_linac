"""Configured EPICS access for energy feedback.

Writes are limited to keys resolved from the machine profile. Source
timestamps come from Channel Access metadata rather than the polling clock.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Protocol

from .models import PVReading


class PVClient(Protocol):
    def read_many(self, keys: Iterable[str]) -> dict[str, PVReading]:
        """Read configured signals without changing machine state."""


class WritablePVClient(PVClient, Protocol):
    def write(
        self,
        key: str,
        value: float,
        *,
        wait: bool = True,
        timeout_s: float = 1.0,
    ) -> bool:
        """Write one configured setpoint key and report Channel Access success."""


class EpicsPVClient:
    def __init__(
        self,
        pv_names: Mapping[str, str],
        *,
        connection_timeout_s: float = 2.0,
        writable_keys: Iterable[str] = (),
    ) -> None:
        try:
            from epics import PV  # type: ignore
        except ImportError as exc:
            raise RuntimeError("pyepics is required for real energy monitoring.") from exc

        self.pv_names = dict(pv_names)
        self.writable_keys = frozenset(str(key) for key in writable_keys)
        unknown_writable = sorted(self.writable_keys - self.pv_names.keys())
        if unknown_writable:
            raise ValueError(
                f"Writable PV keys are not configured: {unknown_writable}"
            )
        self.connection_timeout_s = float(connection_timeout_s)
        self._pvs = {
            key: PV(name, auto_monitor=True)
            for key, name in self.pv_names.items()
        }

    def read_many(self, keys: Iterable[str]) -> dict[str, PVReading]:
        readings: dict[str, PVReading] = {}
        for key in keys:
            pv = self._pvs[key]
            pv_name = self.pv_names[key]
            try:
                connected = bool(
                    pv.connected
                    or pv.wait_for_connection(timeout=self.connection_timeout_s)
                )
                if not connected:
                    readings[key] = PVReading(
                        key, pv_name, None, False, None, "PV connection timeout"
                    )
                    continue
                value = pv.get(timeout=self.connection_timeout_s, use_monitor=True)
                timestamp = getattr(pv, "timestamp", None)
                if value is None:
                    readings[key] = PVReading(
                        key, pv_name, None, True, _timestamp(timestamp), "PV returned None"
                    )
                    continue
                readings[key] = PVReading(
                    key,
                    pv_name,
                    float(value),
                    True,
                    _timestamp(timestamp),
                )
            except Exception as exc:  # noqa: BLE001 - failures become HOLD data
                readings[key] = PVReading(
                    key, pv_name, None, bool(getattr(pv, "connected", False)), None, str(exc)
                )
        return readings

    def write(
        self,
        key: str,
        value: float,
        *,
        wait: bool = True,
        timeout_s: float = 1.0,
    ) -> bool:
        if key not in self.writable_keys:
            raise PermissionError(
                f"PV key is not an actuator setpoint authorized for writing: {key}"
            )
        pv = self._pvs[key]
        connected = bool(
            pv.connected or pv.wait_for_connection(timeout=self.connection_timeout_s)
        )
        if not connected:
            return False
        return bool(pv.put(float(value), wait=bool(wait), timeout=float(timeout_s)))


def _timestamp(value: object) -> float | None:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    return timestamp
