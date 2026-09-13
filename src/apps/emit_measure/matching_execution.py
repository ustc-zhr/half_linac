"""Journaled K1 application/restoration with injectable device I/O."""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time


def finite(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("K1 read/write value must be finite")
    return value


def close(actual, expected):
    return math.isclose(finite(actual), finite(expected), rel_tol=1e-6, abs_tol=1e-6)


def save_execution(path, record):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


class K1Execution:
    def __init__(self, read, write, persist, check_allowed, cancelled=lambda: False,
                 timeout=5.0, poll=0.1):
        self.read, self.write, self.persist = read, write, persist
        self.check_allowed, self.cancelled = check_allowed, cancelled
        self.timeout, self.poll = timeout, poll

    def check(self):
        if self.cancelled():
            raise RuntimeError("Operation cancelled; use Restore previous K1 for attempted magnets")
        self.check_allowed()

    def write_verified(self, pv, value):
        self.check()
        if self.write(pv, value) != 1:
            raise RuntimeError(f"K1 write was not acknowledged: {pv}")
        deadline = time.monotonic() + self.timeout
        while True:
            self.check()
            actual = self.read(pv)
            if actual is not None and close(actual, value):
                return finite(actual)
            if time.monotonic() >= deadline:
                raise RuntimeError(f"K1 PV verification timed out: {pv}; expected {value}, got {actual}")
            time.sleep(self.poll)

    def apply(self, record, targets, expected):
        """Preflight all PVs, journal every attempted write before touching it."""
        record.update(status="preflight", targets=targets, original={}, attempted=[], verified=[], errors=[])
        try:
            self.check()
            for pv, value in expected.items():
                self.check()
                actual = self.read(pv)
                if actual is None or not close(actual, value):
                    raise ValueError(f"K1 baseline changed or unavailable: {pv}; expected {value}, got {actual}. Recalculate.")
            for name, target in targets.items():
                value = finite(self.read(target["pv"]))
                if not close(value, target["current"]):
                    raise ValueError(f"{name}: current K1 changed; recalculate")
                record["original"][name] = value
                finite(target["suggested"])
            record["status"] = "applying"
            self.persist(record)  # All original values must be durable before writes.
            for name, target in targets.items():
                self.check()
                if not close(self.read(target["pv"]), record["original"][name]):
                    raise ValueError(f"{name}: K1 changed during application; stopped")
                record["attempted"].append(name)
                self.persist(record)
                self.write_verified(target["pv"], target["suggested"])
                record["verified"].append(name)
                self.persist(record)
            record["status"] = "applied"
        except Exception as exc:
            record["status"] = "apply_failed"
            record["errors"].append(str(exc))
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.persist(record)
        return record

    def restore(self, record):
        record["status"] = "restoring"
        record["errors"] = []
        restored = record.setdefault("restored", [])
        self.persist(record)
        for name in reversed(record["attempted"]):
            if name in restored:
                continue
            try:
                self.check()
                self.write_verified(record["targets"][name]["pv"], record["original"][name])
                restored.append(name)
                self.persist(record)
            except Exception as exc:
                record["errors"].append(f"{name}: {exc}")
                if self.cancelled():
                    break
        record["status"] = "restored" if set(restored) >= set(record["attempted"]) else "restore_failed"
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.persist(record)
        return record
