from __future__ import annotations

from typing import Mapping

import epics
from PyQt5.QtCore import QObject, QThread, pyqtSignal

from .profile_runtime import QuadTarget


class K1Monitor(QObject):
    value_changed = pyqtSignal(str, str, object)
    connection_changed = pyqtSignal(str, str, bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pvs: dict[tuple[str, str], epics.PV] = {}
        self._generation = 0

    def bind(self, targets: tuple[QuadTarget, ...]) -> None:
        self.close()
        generation = self._generation
        for target in targets:
            for field, pv_name in (
                ("setpoint", target.pv_name),
                ("readback", target.readback_pv),
            ):
                self._pvs[(target.element_id, field)] = epics.PV(
                    pv_name,
                    auto_monitor=True,
                    callback=self._value_callback(
                        target.element_id, field, generation
                    ),
                    connection_callback=self._connection_callback(
                        target.element_id, field, generation
                    ),
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

    def _value_callback(self, element_id: str, field: str, generation: int):
        def callback(value=None, **_kwargs) -> None:
            if generation == self._generation:
                self.value_changed.emit(element_id, field, value)

        return callback

    def _connection_callback(self, element_id: str, field: str, generation: int):
        def callback(conn=None, **kwargs) -> None:
            if generation != self._generation:
                return
            connected = conn if conn is not None else kwargs.get("connected", False)
            self.connection_changed.emit(element_id, field, bool(connected))

        return callback


class K1WriteWorker(QThread):
    completed = pyqtSignal(object, object, str)

    def __init__(
        self,
        targets: Mapping[str, tuple[str, float]],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.targets = dict(targets)

    def run(self) -> None:
        elements = list(self.targets)
        pv_names = [self.targets[element][0] for element in elements]
        values = [self.targets[element][1] for element in elements]
        try:
            raw = epics.caput_many(
                pv_names,
                values,
                wait="all",
                connection_timeout=2.0,
                put_timeout=5.0,
            )
            results = {
                element: bool(index < len(raw) and raw[index] == 1)
                for index, element in enumerate(elements)
            }
            error = "" if all(results.values()) else "One or more K1 writes failed."
        except Exception as exc:
            results = {element: False for element in elements}
            error = str(exc)
        self.completed.emit(dict(values=values, elements=elements), results, error)
