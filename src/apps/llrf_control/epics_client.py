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
