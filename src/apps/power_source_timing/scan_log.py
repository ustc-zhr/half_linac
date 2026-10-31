from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


class TimingEnergyScanLog:
    """Immediately flushed point log and final linked-scan JSON summary."""

    FIELDNAMES = (
        "timestamp",
        "event",
        "status",
        "machine_id",
        "backend",
        "group",
        "offset_us",
        "hv_target_us",
        "llrf_target_us",
        "ssa_target_us",
        "kly_target_us",
        "hv_readback_us",
        "llrf_readback_us",
        "ssa_readback_us",
        "kly_readback_us",
        "matched_energy_mev",
        "energy_setpoint_mev",
        "energy_standard_deviation_mev",
        "energy_standard_error_mev",
        "brightness",
        "valid_frames",
        "total_frames",
        "timing_restored",
        "energy_restored",
        "message",
    )

    def __init__(self, csv_path: Path, metadata: Mapping[str, Any]) -> None:
        self.csv_path = Path(csv_path)
        self.json_path = self.csv_path.with_suffix(".json")
        self.metadata = dict(metadata)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.csv_path.open("w", encoding="utf-8", newline="")
        self._writer = csv.DictWriter(self._stream, fieldnames=self.FIELDNAMES)
        self._writer.writeheader()
        self.write("start")

    @classmethod
    def create(
        cls,
        runs_dir: Path,
        metadata: Mapping[str, Any],
        *,
        now: datetime | None = None,
    ) -> "TimingEnergyScanLog":
        stamp = (now or datetime.now().astimezone()).strftime("%Y%m%d_%H%M%S")
        path = Path(runs_dir) / f"linked_timing_energy_scan_{stamp}.csv"
        suffix = 2
        while path.exists():
            path = Path(runs_dir) / (
                f"linked_timing_energy_scan_{stamp}_{suffix}.csv"
            )
            suffix += 1
        return cls(path, metadata)

    @staticmethod
    def _timestamp() -> str:
        return datetime.now().astimezone().isoformat(timespec="milliseconds")

    @staticmethod
    def _json_default(value: Any) -> Any:
        to_list = getattr(value, "tolist", None)
        if callable(to_list):
            return to_list()
        if isinstance(value, Path):
            return str(value)
        isoformat = getattr(value, "isoformat", None)
        if callable(isoformat):
            return isoformat()
        raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")

    @classmethod
    def _row(
        cls,
        metadata: Mapping[str, Any],
        event: str,
        values: Mapping[str, Any],
    ) -> dict[str, Any]:
        combined = {**dict(metadata), **dict(values)}
        row = {name: "" for name in cls.FIELDNAMES}
        row["timestamp"] = cls._timestamp()
        row["event"] = str(event)
        targets = dict(combined.get("targets_us") or {})
        readbacks = dict(combined.get("readbacks_us") or {})
        for device in ("hv", "llrf", "ssa", "kly"):
            row[f"{device}_target_us"] = targets.get(device, "")
            row[f"{device}_readback_us"] = readbacks.get(device, "")
        for key, value in combined.items():
            if key in row and value is not None:
                row[key] = value
        return row

    def write(self, event: str, **values: Any) -> None:
        self._writer.writerow(self._row(self.metadata, event, values))
        self._stream.flush()

    def record_point(self, point: Mapping[str, Any]) -> None:
        self.write("point", **dict(point))

    def finish(self, result: Mapping[str, Any]) -> None:
        self.write(
            "result",
            status=result.get("status"),
            targets_us=result.get("initial_delays_us"),
            readbacks_us=result.get("restore_readbacks_us"),
            timing_restored=result.get("timing_restored"),
            energy_restored=result.get("energy_restored"),
            message=result.get("message"),
        )
        payload = {
            "schema_version": "linked_timing_energy_scan_v1",
            "created_at": self._timestamp(),
            **self.metadata,
            **dict(result),
        }
        self.json_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=self._json_default),
            encoding="utf-8",
        )

    @classmethod
    def append_apply(
        cls,
        csv_path: str | Path,
        json_path: str | Path,
        metadata: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> None:
        csv_path = Path(csv_path)
        with csv_path.open("a", encoding="utf-8", newline="") as stream:
            csv.DictWriter(stream, fieldnames=cls.FIELDNAMES).writerow(
                cls._row(metadata, "apply", result)
            )
        path = Path(json_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["apply"] = {
            "recorded_at": cls._timestamp(),
            **dict(result),
        }
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=cls._json_default),
            encoding="utf-8",
        )

    def close(self) -> None:
        if not self._stream.closed:
            self._stream.flush()
            self._stream.close()
