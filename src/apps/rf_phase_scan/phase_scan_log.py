from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


class PhaseEnergyScanLog:
    """Immediately flushed point log plus a final JSON run summary."""

    FIELDNAMES = (
        "timestamp",
        "event",
        "status",
        "machine_id",
        "backend",
        "station_id",
        "element_id",
        "phase_pv",
        "phase_readback_pv",
        "energy_pv",
        "initial_phase_deg",
        "initial_energy_mev",
        "phase_mode",
        "index",
        "offset_deg",
        "requested_phase_unwrapped_deg",
        "command_phase_deg",
        "attempts",
        "search_low_mev",
        "search_high_mev",
        "matched_energy_mev",
        "center_offset_mm",
        "brightness",
        "valid_frames",
        "measurement_samples",
        "measurement_interval_s",
        "measurement_min_valid_samples",
        "fit_method",
        "fit_r_squared",
        "center_spread_mm",
        "beam_threshold",
        "beam_area_px",
        "beam_major_axis_px",
        "beam_minor_axis_px",
        "beam_aspect_ratio",
        "beam_orientation_rad",
        "phase_restored",
        "energy_restored",
        "message",
        "background_used",
        "background_path",
        "background_shape",
        "stop_reason",
        "baseline_energy_mev",
        "amplitude_mev",
        "crest_phase_unwrapped_deg",
        "crest_phase_command_deg",
        "rmse_mev",
        "r_squared",
    )

    def __init__(self, csv_path: Path, metadata: Mapping[str, Any]) -> None:
        self.csv_path = Path(csv_path)
        self.json_path = self.csv_path.with_suffix(".json")
        self.metadata = dict(metadata)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.csv_path.open("w", encoding="utf-8", newline="")
        self._writer = csv.DictWriter(self._stream, fieldnames=self.FIELDNAMES)
        self._writer.writeheader()
        self.write("start", **self.metadata)

    @classmethod
    def create(
        cls,
        runs_dir: Path,
        metadata: Mapping[str, Any],
        *,
        now: datetime | None = None,
    ) -> "PhaseEnergyScanLog":
        stamp = (now or datetime.now().astimezone()).strftime("%Y%m%d_%H%M%S")
        path = Path(runs_dir) / f"phase_energy_scan_{stamp}.csv"
        suffix = 2
        while path.exists():
            path = Path(runs_dir) / f"phase_energy_scan_{stamp}_{suffix}.csv"
            suffix += 1
        return cls(path, metadata)

    @staticmethod
    def _timestamp() -> str:
        return datetime.now().astimezone().isoformat(timespec="milliseconds")

    def write(self, event: str, **values: Any) -> None:
        row = {name: "" for name in self.FIELDNAMES}
        row["timestamp"] = self._timestamp()
        row["event"] = str(event)
        for key, value in {**self.metadata, **values}.items():
            if key in row and value is not None:
                row[key] = value
        self._writer.writerow(row)
        self._stream.flush()

    def record_point(self, point: Mapping[str, Any]) -> None:
        self.write("point", **dict(point))

    def finish(self, result: Mapping[str, Any]) -> None:
        fit = result.get("fit") or {}
        self.write(
            "result",
            status=result.get("status"),
            initial_phase_deg=result.get("initial_phase_deg"),
            initial_energy_mev=result.get("initial_energy_mev"),
            phase_restored=result.get("phase_restored"),
            energy_restored=result.get("energy_restored"),
            message=result.get("message"),
            stop_reason=result.get("message") if result.get("status") == "CANCELLED" else None,
            baseline_energy_mev=fit.get("baseline_energy_mev"),
            amplitude_mev=fit.get("amplitude_mev"),
            crest_phase_unwrapped_deg=fit.get("crest_phase_unwrapped_deg"),
            crest_phase_command_deg=fit.get("crest_phase_command_deg"),
            rmse_mev=fit.get("rmse_mev"),
            r_squared=fit.get("r_squared"),
        )
        payload = {
            "schema_version": "phase_energy_scan_v1",
            "created_at": self._timestamp(),
            **self.metadata,
            **dict(result),
        }
        self.json_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )

    def close(self) -> None:
        if self._stream.closed:
            return
        self._stream.flush()
        self._stream.close()

    def __enter__(self) -> "PhaseEnergyScanLog":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()
