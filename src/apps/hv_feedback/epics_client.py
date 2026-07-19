from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, Iterable, Optional


@dataclass
class PVValue:
    key: str
    name: str
    value: Optional[float]
    timestamp: float
    ok: bool
    error: str = ""


class BaseClient:
    def read_many(self, keys: Iterable[str]) -> Dict[str, PVValue]:
        raise NotImplementedError

    def get(self, key: str) -> PVValue:
        return self.read_many([key])[key]

    def put(self, key: str, value: float) -> None:
        raise NotImplementedError


class EpicsClient(BaseClient):
    """Small pyepics adapter; safety and write authorization live above it."""

    def __init__(self, pv_names: Dict[str, str], connection_timeout_s: float = 2.0):
        try:
            from epics import PV  # type: ignore
        except ImportError as exc:
            raise RuntimeError("pyepics is required for IRFEL HV feedback.") from exc

        self.pv_names = dict(pv_names)
        self._pvs = {key: PV(name) for key, name in self.pv_names.items()}
        deadline = time.time() + connection_timeout_s
        while time.time() < deadline:
            if all(pv.connected for pv in self._pvs.values()):
                break
            time.sleep(0.05)
        missing = [key for key, pv in self._pvs.items() if not pv.connected]
        if missing:
            names = ", ".join(f"{key}={self.pv_names[key]}" for key in missing)
            raise RuntimeError(f"EPICS PV connection failed: {names}")

    def read_many(self, keys: Iterable[str]) -> Dict[str, PVValue]:
        out: Dict[str, PVValue] = {}
        now = time.time()
        for key in keys:
            pv = self._pvs[key]
            name = self.pv_names[key]
            try:
                value = pv.get(timeout=1.0)
                if value is None:
                    out[key] = PVValue(key, name, None, now, False, "PV returned None")
                else:
                    out[key] = PVValue(key, name, float(value), now, True, "")
            except Exception as exc:  # noqa: BLE001
                out[key] = PVValue(key, name, None, now, False, str(exc))
        return out

    def put(self, key: str, value: float) -> None:
        pv = self._pvs[key]
        ok = pv.put(float(value), wait=True, timeout=2.0)
        if ok is False:
            raise RuntimeError(
                f"caput failed for {key}={self.pv_names[key]} value={value}"
            )
