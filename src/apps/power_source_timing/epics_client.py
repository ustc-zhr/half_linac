from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import epics
from PyQt5.QtCore import QObject, QThread, pyqtSignal

from .model import ValueKey
from .profile_runtime import TimingGroup


@dataclass(frozen=True)
class WaveformSnapshot:
    value: object | None
    connected: bool
    epics_timestamp: float | None
    received_monotonic: float | None


class WaveformMonitor:
    """Thread-safe latest-value cache for optional read-only waveform PVs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pvs: dict[str, epics.PV] = {}
        self._snapshots: dict[str, WaveformSnapshot] = {}
        self._generation = 0

    def bind(self, group: TimingGroup) -> None:
        self.close()
        with self._lock:
            generation = self._generation
            self._snapshots = {
                device: WaveformSnapshot(None, False, None, None)
                for device in group.waveforms
            }
        for device, pv_name in group.waveforms.items():
            self._pvs[device] = epics.PV(
                pv_name,
                auto_monitor=True,
                callback=self._value_callback(device, generation),
                connection_callback=self._connection_callback(device, generation),
            )

    def snapshots(self) -> dict[str, WaveformSnapshot]:
        with self._lock:
            return dict(self._snapshots)

    def close(self) -> None:
        pvs = list(self._pvs.values())
        self._pvs.clear()
        with self._lock:
            self._generation += 1
            self._snapshots.clear()
        for pv in pvs:
            try:
                pv.clear_callbacks()
                pv.disconnect()
            except Exception:
                pass

    def _value_callback(self, device: str, generation: int):
        def callback(value: Any = None, timestamp: Any = None, **_kwargs: Any) -> None:
            if generation != self._generation:
                return
            try:
                stored_value = value.copy()
            except AttributeError:
                stored_value = value
            try:
                epics_timestamp = float(timestamp) if timestamp is not None else None
            except (TypeError, ValueError):
                epics_timestamp = None
            with self._lock:
                if generation != self._generation:
                    return
                previous = self._snapshots.get(
                    device, WaveformSnapshot(None, False, None, None)
                )
                self._snapshots[device] = WaveformSnapshot(
                    value=stored_value,
                    connected=previous.connected,
                    epics_timestamp=epics_timestamp,
                    received_monotonic=time.monotonic(),
                )

        return callback

    def _connection_callback(self, device: str, generation: int):
        def callback(conn: bool | None = None, **kwargs: Any) -> None:
            if generation != self._generation:
                return
            connected = bool(conn if conn is not None else kwargs.get("connected", False))
            with self._lock:
                if generation != self._generation:
                    return
                previous = self._snapshots.get(
                    device, WaveformSnapshot(None, False, None, None)
                )
                self._snapshots[device] = WaveformSnapshot(
                    value=previous.value,
                    connected=connected,
                    epics_timestamp=previous.epics_timestamp,
                    received_monotonic=previous.received_monotonic,
                )

        return callback


class GroupMonitor(QObject):
    value_changed = pyqtSignal(str, str, object)
    connection_changed = pyqtSignal(str, str, bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pvs: dict[tuple[str, str], epics.PV] = {}
        self._generation = 0

    def bind(self, group: TimingGroup) -> None:
        self.close()
        generation = self._generation
        for device, fields in group.channels.items():
            for field, pv_name in fields.items():
                value_callback = self._value_callback(device, field, generation)
                connection_callback = self._connection_callback(device, field, generation)
                self._pvs[(device, field)] = epics.PV(
                    pv_name,
                    auto_monitor=True,
                    callback=value_callback,
                    connection_callback=connection_callback,
                )

    def close(self) -> None:
        self._generation += 1
        for pv in self._pvs.values():
            try:
                pv.clear_callbacks()
                pv.disconnect()
            except Exception:
                pass
        self._pvs.clear()

    def _value_callback(self, device: str, field: str, generation: int):
        def callback(value: Any = None, **_kwargs: Any) -> None:
            if generation == self._generation:
                self.value_changed.emit(device, field, value)

        return callback

    def _connection_callback(self, device: str, field: str, generation: int):
        def callback(conn: bool | None = None, **kwargs: Any) -> None:
            if generation != self._generation:
                return
            connected = conn if conn is not None else kwargs.get("connected", False)
            self.connection_changed.emit(device, field, bool(connected))

        return callback


class BatchWriteWorker(QThread):
    completed = pyqtSignal(object, str)

    def __init__(
        self,
        pv_values: Mapping[ValueKey, tuple[str, float]],
        *,
        connection_timeout: float = 2.0,
        put_timeout: float = 5.0,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._pv_values = dict(pv_values)
        self._connection_timeout = float(connection_timeout)
        self._put_timeout = float(put_timeout)

    def run(self) -> None:
        keys = list(self._pv_values)
        names = [self._pv_values[key][0] for key in keys]
        values = [self._pv_values[key][1] for key in keys]
        try:
            raw_results = epics.caput_many(
                names,
                values,
                wait="all",
                connection_timeout=self._connection_timeout,
                put_timeout=self._put_timeout,
            )
            results = {
                key: bool(index < len(raw_results) and raw_results[index] == 1)
                for index, key in enumerate(keys)
            }
            failed = [key for key, success in results.items() if not success]
            error = "" if not failed else "One or more EPICS writes failed."
        except Exception as exc:
            results = {key: False for key in keys}
            error = str(exc)
        self.completed.emit(results, error)


class SnapshotWorker(QThread):
    progress = pyqtSignal(str)

    def __init__(self, runtime, path=None, data=None, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        self.path = path
        self.data = data
        self.success = False
        self.message = ""

    def run(self) -> None:
        import math

        from .snapshot import make_snapshot, save_snapshot, targets, validate_snapshot

        entries = targets(self.runtime)
        completed = []
        current = "Snapshot validation"
        attempted = False
        index = -1
        restoring = self.data is not None
        try:
            values = validate_snapshot(self.runtime, self.data) if restoring else []
            epics.ca.use_initial_context()
            for index, (group, device, quantity, pv_name) in enumerate(entries):
                current = f"{group} {device.upper()} {quantity} ({pv_name})"
                attempted = False
                action = "Restoring" if restoring else "Reading"
                self.progress.emit(
                    f"{action} {index + 1}/{len(entries)}: "
                    f"{group} {device.upper()} {quantity}"
                )
                if self.isInterruptionRequested():
                    raise RuntimeError("Operation interrupted")
                pv = epics.PV(pv_name, auto_monitor=False)
                try:
                    if not pv.wait_for_connection(timeout=3):
                        raise RuntimeError("AO connection timed out")
                    if restoring:
                        attempted = True
                        if pv.put(values[index], wait=True, timeout=5) != 1:
                            raise RuntimeError("AO write did not complete")
                    actual = pv.get(use_monitor=False, timeout=3)
                    if actual is None or not math.isfinite(float(actual)):
                        raise RuntimeError(
                            "AO read failed or returned a non-finite value"
                        )
                    if restoring:
                        if not math.isclose(
                            float(actual),
                            values[index],
                            rel_tol=1.0e-9,
                            abs_tol=self.runtime.readback_tolerance_us,
                        ):
                            raise RuntimeError(
                                "AO verification failed: "
                                f"expected {values[index]:.12g}, got {actual}"
                            )
                    else:
                        values.append(float(actual))
                finally:
                    pv.disconnect()
                completed.append(current)
            if not restoring:
                current = "Saving snapshot file"
                save_snapshot(self.path, make_snapshot(self.runtime, values))
            self.success = True
            action = "Restored" if restoring else "Saved"
            suffix = f" to {self.path}" if self.path else ""
            self.message = f"{action} {len(entries)} timing AO parameters{suffix}"
        except Exception as exc:
            uncertain = (
                "\nThe current write may have taken effect; its result is not confirmed."
                if attempted
                else ""
            )
            remaining = [
                f"{group} {device.upper()} {quantity}"
                for group, device, quantity, _pv in entries[index + 1 :]
            ]
            self.message = (
                f"Failed: {current}\n{exc}{uncertain}\n\n"
                f"Completed ({len(completed)}):\n"
                + ("\n".join(completed) or "None")
                + "\n\nNot executed:\n"
                + ("\n".join(remaining) or "None")
            )
