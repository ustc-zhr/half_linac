"""Configured EPICS access for energy feedback.

Normal sampling may use the monitor cache. Safety and write-confirmation paths can
request an active Channel Access get without depending on monitor-event timing.
Writes remain limited to keys resolved from the machine profile.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable, Mapping
from typing import Protocol

from .models import PVReading


class PVClient(Protocol):
    def read_many(
        self, keys: Iterable[str], *, fresh: bool = False
    ) -> dict[str, PVReading]:
        """Read configured signals; fresh=True bypasses the monitor cache."""


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
        self._monitor_lock = threading.Lock()
        self._monitor_received_at: dict[str, float] = {}
        self._pvs = {}
        for key, name in self.pv_names.items():
            pv = PV(name, auto_monitor=True)
            self._pvs[key] = pv
            add_callback = getattr(pv, "add_callback", None)
            if callable(add_callback):
                add_callback(self._monitor_callback(key))

    def _monitor_callback(self, key: str):
        def record_event(**_kwargs) -> None:
            with self._monitor_lock:
                self._monitor_received_at[key] = time.time()

        return record_event

    def _monitor_time(self, key: str) -> float | None:
        with self._monitor_lock:
            return self._monitor_received_at.get(key)

    def read_many(
        self, keys: Iterable[str], *, fresh: bool = False
    ) -> dict[str, PVReading]:
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
                        key,
                        pv_name,
                        None,
                        False,
                        None,
                        "PV connection timeout",
                        self._monitor_time(key),
                    )
                    continue
                value = pv.get(
                    timeout=self.connection_timeout_s, use_monitor=not fresh
                )
                timestamp = getattr(pv, "timestamp", None)
                if value is None:
                    readings[key] = PVReading(
                        key,
                        pv_name,
                        None,
                        True,
                        _timestamp(timestamp),
                        "PV returned None",
                        self._monitor_time(key),
                    )
                    continue
                readings[key] = PVReading(
                    key,
                    pv_name,
                    float(value),
                    True,
                    _timestamp(timestamp),
                    monitor_received_at=self._monitor_time(key),
                )
            except Exception as exc:  # noqa: BLE001 - failures become HOLD data
                readings[key] = PVReading(
                    key,
                    pv_name,
                    None,
                    bool(getattr(pv, "connected", False)),
                    None,
                    str(exc),
                    self._monitor_time(key),
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
