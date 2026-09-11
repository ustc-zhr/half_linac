from __future__ import annotations

import epics
from PyQt5.QtCore import QObject, QThread, pyqtSignal

from .profile_runtime import LlrfGroup


class LlrfMonitor(QObject):
    value_changed = pyqtSignal(str, object)
    connection_changed = pyqtSignal(str, bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pvs: dict[str, epics.PV] = {}
        self._generation = 0

    def bind(self, group: LlrfGroup) -> None:
        self.close()
        generation = self._generation
        for quantity in group.quantities.values():
            for field, pv_name in (
                (quantity.set_channel, quantity.setpoint_pv),
                (quantity.readback_channel, quantity.readback_pv),
            ):
                self._pvs[field] = epics.PV(
                    pv_name,
                    auto_monitor=True,
                    callback=self._value_callback(field, generation),
                    connection_callback=self._connection_callback(field, generation),
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

    def _value_callback(self, field: str, generation: int):
        def callback(value=None, **_kwargs) -> None:
            if generation == self._generation:
                self.value_changed.emit(field, value)

        return callback

    def _connection_callback(self, field: str, generation: int):
        def callback(conn=None, **kwargs) -> None:
            if generation != self._generation:
                return
            connected = conn if conn is not None else kwargs.get("connected", False)
            self.connection_changed.emit(field, bool(connected))

        return callback


class WriteWorker(QThread):
    completed = pyqtSignal(str, float, bool, str)

    def __init__(
        self,
        quantity: str,
        pv_name: str,
        value: float,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.quantity = quantity
        self.pv_name = pv_name
        self.value = float(value)

    def run(self) -> None:
        try:
            success = bool(epics.caput(self.pv_name, self.value, wait=True, timeout=5))
            error = "" if success else "EPICS write did not complete."
        except Exception as exc:
            success = False
            error = str(exc)
        self.completed.emit(self.quantity, self.value, success, error)


class SnapshotWorker(QThread):
    progress = pyqtSignal(str)

    def __init__(self, runtime, path=None, data=None, parent=None):
        super().__init__(parent)
        self.runtime, self.path, self.data = runtime, path, data
        self.success = False
        self.message = ""

    def run(self):
        import math
        from .snapshot import targets, validate_snapshot, make_snapshot, save_snapshot

        entries = targets(self.runtime)
        completed = []
        current = "Snapshot validation"
        attempted = False
        index = -1
        try:
            values = validate_snapshot(self.runtime, self.data) if self.data is not None else []
            epics.ca.use_initial_context()
            for index, (device, name, spec) in enumerate(entries):
                current = f"{device} {name} ({spec.setpoint_pv})"
                attempted = False
                self.progress.emit(f"{'Restoring' if self.data is not None else 'Reading'} {index + 1}/{len(entries)}: {current}")
                if self.isInterruptionRequested():
                    raise RuntimeError("Operation interrupted")
                pv = epics.PV(spec.setpoint_pv, auto_monitor=False)
                try:
                    if not pv.wait_for_connection(timeout=3):
                        raise RuntimeError("AO connection timed out")
                    if self.data is not None:
                        attempted = True
                        if pv.put(values[index], wait=True, timeout=5) != 1:
                            raise RuntimeError("AO write did not complete")
                    actual = pv.get(use_monitor=False, timeout=3)
                    if actual is None or not math.isfinite(float(actual)):
                        raise RuntimeError("AO read failed or returned a non-finite value")
                    if self.data is not None:
                        if not math.isclose(float(actual), values[index], rel_tol=1e-7, abs_tol=1e-6):
                            raise RuntimeError(f"AO verification failed: expected {values[index]:.12g}, got {actual}")
                    else:
                        values.append(float(actual))
                finally:
                    pv.disconnect()
                completed.append(current)
            if self.data is None:
                current = "Saving snapshot file"
                save_snapshot(self.path, make_snapshot(self.runtime, values))
            self.success = True
            self.message = f"{'Restored' if self.data is not None else 'Saved'} {len(entries)} AO parameters" + (f" to {self.path}" if self.path else "")
        except Exception as exc:
            uncertain = "\nThe current write may have taken effect; its result is not confirmed." if attempted else ""
            remaining = [f"{d} {n}" for d, n, _ in entries[index + 1:]]
            self.message = (f"Failed: {current}\n{exc}{uncertain}\n\nCompleted ({len(completed)}):\n"
                            + ("\n".join(completed) or "None")
                            + "\n\nNot executed:\n" + ("\n".join(remaining) or "None"))
