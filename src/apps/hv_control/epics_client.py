from __future__ import annotations

import epics
from PyQt5.QtCore import QObject, QThread, pyqtSignal


class HvMonitor(QObject):
    value_changed = pyqtSignal(str, str, object)
    connection_changed = pyqtSignal(str, str, bool)

    def __init__(self, modulators, parent=None):
        super().__init__(parent)
        self.modulators = modulators
        self._pvs = {}
        self._generation = 0

    def bind(self):
        self.close()
        generation = self._generation
        for mod in self.modulators:
            for field in ("voltage_set", "voltage_readback", "enable_1", "enable_2"):
                pv_name = getattr(mod, field)
                if not pv_name:
                    self.connection_changed.emit(mod.name, field, False)
                    continue
                key = (mod.name, field)
                self._pvs[key] = epics.PV(
                    pv_name, auto_monitor=True,
                    callback=self._value_callback(mod.name, field, generation),
                    connection_callback=self._connection_callback(mod.name, field, generation),
                )

    def close(self):
        self._generation += 1
        for pv in self._pvs.values():
            try:
                pv.clear_callbacks()
                pv.disconnect()
            except Exception:
                pass
        self._pvs.clear()

    def _value_callback(self, name, field, generation):
        def callback(value=None, **_kwargs):
            if generation == self._generation:
                self.value_changed.emit(name, field, value)
        return callback

    def _connection_callback(self, name, field, generation):
        def callback(conn=None, **kwargs):
            if generation == self._generation:
                self.connection_changed.emit(name, field, bool(conn if conn is not None else kwargs.get("connected", False)))
        return callback


class BatchWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished_result = pyqtSignal(bool, str)

    def __init__(self, operations, mode="write", path=None, parent=None):
        super().__init__(parent)
        self.operations = operations
        self.mode = mode
        self.path = path

    def run(self):
        import json
        from datetime import datetime, timezone
        completed, errors, values = [], [], {}
        try:
            total = len(self.operations)
            for index, item in enumerate(self.operations, start=1):
                name, field, pv_name, value = item
                self.progress.emit(index, total, f"{self.mode.title()} {index}/{total}: {name} {field}")
                if not pv_name:
                    errors.append(f"{name} {field}: PV is not configured")
                    continue
                pv = epics.PV(pv_name, auto_monitor=False)
                try:
                    if not pv.wait_for_connection(timeout=3):
                        raise RuntimeError("PV connection timed out")
                    if self.mode == "save":
                        raw = pv.get(use_monitor=False, timeout=3)
                        if raw is None:
                            raise RuntimeError("PV read failed")
                        values[name] = float(raw)
                    else:
                        if pv.put(value, wait=True, timeout=5) != 1:
                            raise RuntimeError("PV write did not complete")
                    completed.append(name)
                except Exception as exc:
                    errors.append(f"{name} {field}: {exc}")
                finally:
                    pv.disconnect()
            if self.mode == "save" and not errors:
                data = {
                    "version": 1,
                    "machine": self.path["machine"],
                    "backend": self.path["backend"],
                    "saved_at": datetime.now(timezone.utc).isoformat(),
                    "modulators": {name: {"voltage_set": value} for name, value in values.items()},
                }
                with open(self.path["file"], "w", encoding="utf-8") as stream:
                    json.dump(data, stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
            if errors:
                self.finished_result.emit(False, "\n".join(errors))
            else:
                action = "Saved" if self.mode == "save" else "Completed"
                self.finished_result.emit(True, f"{action} {len(completed)} operations")
        except Exception as exc:
            self.finished_result.emit(False, str(exc))
