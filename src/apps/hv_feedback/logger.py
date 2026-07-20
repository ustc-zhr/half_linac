from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Dict, Optional

from .profile_runtime import amplitude_key, phase_key


class CSVLogger:
    def __init__(
        self,
        log_dir: str | Path,
        file_prefix: str,
        config: Dict[str, object],
        flush_every_n_rows: int = 1,
    ):
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        self.path = log_dir / f"{file_prefix}_{stamp}.csv"
        self.flush_every_n_rows = int(flush_every_n_rows)
        self._file = self.path.open("w", newline="", encoding="utf-8")
        self._writer: Optional[csv.DictWriter] = None
        self._rows_since_flush = 0
        channel_ids = [str(channel["id"]) for channel in config["rf_channels"]]  # type: ignore[index]
        channel_fields: list[str] = []
        reference_fields: list[str] = []
        for channel_id in channel_ids:
            channel_fields.extend((amplitude_key(channel_id), phase_key(channel_id)))
            reference_fields.extend(
                (
                    f"reference.{channel_id}.amplitude",
                    f"reference.{channel_id}.phase_deg",
                )
            )
        self._fieldnames = [
            "timestamp",
            "iso_time",
            "feedback_unit_id",
            "feedback_channel_id",
            "mode",
            "state",
            "event",
            "reason",
            "hv_setpoint",
            "hv_readback",
            *channel_fields,
            "reference_hv_kv",
            *reference_fields,
            "error_rel",
            "delta_hv_raw",
            "delta_hv",
            "hv_next",
            "saturated_step",
            "saturated_total",
        ]
        self._writer = csv.DictWriter(
            self._file,
            fieldnames=self._fieldnames,
            extrasaction="ignore",
        )
        self._writer.writeheader()

    def write(self, row: Dict[str, object]) -> None:
        if self._writer is None:
            raise RuntimeError("Logger is closed")
        timestamp = row.get("timestamp", time.time())
        try:
            ts_float = float(timestamp)  # type: ignore[arg-type]
        except Exception:
            ts_float = time.time()
        full_row = {key: "" for key in self._fieldnames}
        full_row.update(row)
        full_row["timestamp"] = ts_float
        full_row["iso_time"] = time.strftime(
            "%Y-%m-%d %H:%M:%S",
            time.localtime(ts_float),
        )
        self._writer.writerow(full_row)
        self._rows_since_flush += 1
        if self._rows_since_flush >= self.flush_every_n_rows:
            self._file.flush()
            self._rows_since_flush = 0

    def close(self) -> None:
        if self._writer is not None:
            self._file.flush()
            self._file.close()
            self._writer = None
