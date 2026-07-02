from __future__ import annotations

from dataclasses import dataclass

try:
    import epics
except ImportError:  # pragma: no cover - optional runtime dependency
    epics = None


def require_pyepics():
    if epics is None:
        raise RuntimeError("pyepics is required for EPICS access")
    return epics


@dataclass(slots=True)
class ReadResult:
    value: float | int | str | None
    connected: bool


class PyEpicsClient:
    def __init__(self, timeout_sec: float = 1.0) -> None:
        self.timeout_sec = timeout_sec
        self._pv_cache: dict[str, object] = {}

    def _get_pv(self, pv_name: str):
        module = require_pyepics()
        pv = self._pv_cache.get(pv_name)
        if pv is None:
            pv = module.PV(pv_name)
            self._pv_cache[pv_name] = pv
            self._start_connection(pv)
        return pv

    def _start_connection(self, pv) -> None:
        connect = getattr(pv, "connect", None)
        if not callable(connect):
            return
        try:
            connect(timeout=0.0)
        except TypeError:
            try:
                connect()
            except Exception:
                return
        except Exception:
            return

    @staticmethod
    def _pv_connected(pv) -> bool:
        return bool(getattr(pv, "connected", False))

    def snapshot_connections(self, pv_names: list[str]) -> list[bool]:
        if not pv_names:
            return []

        module = require_pyepics()
        names = [str(pv_name) for pv_name in pv_names]
        pvs = [self._get_pv(pv_name) for pv_name in names]
        for pv in pvs:
            if not self._pv_connected(pv):
                self._start_connection(pv)

        poll = getattr(module, "poll", None)
        if callable(poll):
            try:
                poll(evt=1.0e-5, iot=0.0)
            except TypeError:
                poll()

        return [self._pv_connected(pv) for pv in pvs]

    def is_connected(self, pv_name: str) -> bool:
        pv = self._get_pv(pv_name)
        return bool(pv.wait_for_connection(timeout=self.timeout_sec))

    def read(self, pv_name: str) -> ReadResult:
        pv = self._get_pv(pv_name)
        connected = bool(pv.wait_for_connection(timeout=self.timeout_sec))
        value = pv.get(timeout=self.timeout_sec) if connected else None
        return ReadResult(value=value, connected=connected)

    def read_many(self, pv_names: list[str]) -> list[ReadResult]:
        if not pv_names:
            return []

        module = require_pyepics()
        names = [str(pv_name) for pv_name in pv_names]
        pvs = [self._get_pv(pv_name) for pv_name in names]
        values = self._bulk_get(module, names)
        if values is None:
            values = [
                pv.get(timeout=0.0) if self._pv_connected(pv) else None
                for pv in pvs
            ]

        return [
            ReadResult(
                value=value,
                connected=(value is not None) or self._pv_connected(pv),
            )
            for pv, value in zip(pvs, values)
        ]

    def _bulk_get(self, module, pv_names: list[str]) -> list[object] | None:
        caget_many = getattr(module, "caget_many", None)
        if not callable(caget_many):
            return None
        values = None
        called = False
        for args, kwargs in (
            ((pv_names,), {"timeout": self.timeout_sec}),
            ((pv_names,), {}),
        ):
            try:
                values = caget_many(*args, **kwargs)
                called = True
                break
            except TypeError:
                continue
            except Exception as exc:
                raise RuntimeError(f"caget_many failed for {len(pv_names)} PV(s): {exc}") from exc
        if not called:
            return None
        if values is None:
            raise RuntimeError("caget_many returned no values.")
        try:
            rows = list(values)
        except TypeError as exc:
            raise RuntimeError("caget_many returned a non-iterable result.") from exc
        if len(rows) != len(pv_names):
            raise RuntimeError(
                f"caget_many returned {len(rows)} value(s) for {len(pv_names)} PV(s)."
            )
        return rows

    def write_many(self, pv_values: list[tuple[str, float]]) -> list[bool]:
        if not pv_values:
            return []

        module = require_pyepics()
        names = [str(pv_name) for pv_name, _ in pv_values]
        values = [float(value) for _, value in pv_values]
        statuses = self._bulk_put(module, names, values)
        if statuses is None:
            return [
                bool(module.caput(pv_name, value, wait=True, timeout=self.timeout_sec))
                for pv_name, value in zip(names, values)
            ]
        return statuses

    def _bulk_put(self, module, pv_names: list[str], values: list[float]) -> list[bool] | None:
        caput_many = getattr(module, "caput_many", None)
        if not callable(caput_many):
            return None
        statuses = None
        called = False
        for args, kwargs in (
            ((pv_names, values), {"wait": True, "timeout": self.timeout_sec}),
            ((pv_names, values), {"wait": True}),
            ((pv_names, values), {}),
        ):
            try:
                statuses = caput_many(*args, **kwargs)
                called = True
                break
            except TypeError:
                continue
            except Exception as exc:
                raise RuntimeError(f"caput_many failed for {len(pv_names)} PV(s): {exc}") from exc
        if not called:
            return None
        if statuses is None:
            raise RuntimeError("caput_many returned no per-PV completion status.")
        if isinstance(statuses, bool):
            return [bool(statuses)] * len(pv_names)
        try:
            rows = list(statuses)
        except TypeError as exc:
            raise RuntimeError("caput_many returned a non-iterable status result.") from exc
        if len(rows) != len(pv_names):
            raise RuntimeError(
                f"caput_many returned {len(rows)} status value(s) for {len(pv_names)} PV(s)."
            )
        return [bool(row) for row in rows]

    def write(self, pv_name: str, value: float) -> bool:
        module = require_pyepics()
        return bool(module.caput(pv_name, value, wait=True, timeout=self.timeout_sec))
