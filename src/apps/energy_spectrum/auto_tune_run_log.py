from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


class ESAAutoTuneRunLog:
    """Immediately flushed CSV event log for one ESA Auto Find run."""

    DEFAULT_MAX_LOGS = 500

    FIELDNAMES = (
        "timestamp",
        "event",
        "stage",
        "status",
        "machine_id",
        "backend",
        "station_id",
        "objective",
        "actuator_pv",
        "x_reference_mm",
        "energy_mev",
        "initial_energy_mev",
        "seed_energy_mev",
        "final_energy_mev",
        "restored_energy_mev",
        "scan_min_mev",
        "scan_max_mev",
        "range_min_mev",
        "range_max_mev",
        "coarse_points",
        "fine_points",
        "points",
        "spacing_mev",
        "has_beam",
        "brightness",
        "center_mm",
        "dx_mm",
        "valid_frames",
        "total_frames",
        "fit_method",
        "fit_r_squared",
        "center_spread_mm",
        "beam_threshold",
        "beam_area_px",
        "beam_major_axis_px",
        "beam_minor_axis_px",
        "beam_aspect_ratio",
        "beam_orientation_rad",
        "frame_samples",
        "min_valid_frames",
        "verification_frame_samples",
        "verification_min_valid_frames",
        "frame_interval_s",
        "min_fit_r_squared",
        "beam_presence_sigma",
        "beam_presence_min_area_px",
        "settle_time_s",
        "center_step_mev",
        "center_tolerance_mm",
        "message",
    )

    def __init__(self, path: Path, start_values: Mapping[str, Any]):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("w", encoding="utf-8", newline="")
        self._writer = csv.DictWriter(self._stream, fieldnames=self.FIELDNAMES)
        self._writer.writeheader()
        self.write("start", **dict(start_values))

    @classmethod
    def create(
        cls,
        runs_dir: Path,
        start_values: Mapping[str, Any],
        *,
        now: datetime | None = None,
        max_logs: int = DEFAULT_MAX_LOGS,
    ) -> "ESAAutoTuneRunLog":
        timestamp = (now or datetime.now().astimezone()).strftime("%Y%m%d_%H%M%S")
        path = Path(runs_dir) / f"esa_auto_tune_{timestamp}.csv"
        suffix = 2
        while path.exists():
            path = Path(runs_dir) / f"esa_auto_tune_{timestamp}_{suffix}.csv"
            suffix += 1
        logger = cls(path, start_values)
        cls._prune_old_logs(Path(runs_dir), current_path=path, max_logs=max_logs)
        return logger

    @staticmethod
    def _prune_old_logs(runs_dir: Path, *, current_path: Path, max_logs: int) -> None:
        max_logs = max(int(max_logs), 1)
        candidates = []
        for path in Path(runs_dir).glob("esa_auto_tune_*.csv"):
            if path == current_path or not path.is_file():
                continue
            try:
                modified_ns = path.stat().st_mtime_ns
            except OSError:
                continue
            candidates.append((modified_ns, path.name, path))
        candidates.sort(reverse=True)
        for _modified_ns, _name, path in candidates[max_logs - 1:]:
            try:
                path.unlink()
            except OSError:
                continue

    @staticmethod
    def _timestamp() -> str:
        return datetime.now().astimezone().isoformat(timespec="milliseconds")

    def write(self, event: str, **values: Any) -> None:
        row = {field: "" for field in self.FIELDNAMES}
        row["timestamp"] = self._timestamp()
        row["event"] = str(event)
        for key, value in values.items():
            if key in row and value is not None:
                row[key] = value
        self._writer.writerow(row)
        self._stream.flush()

    def record_progress(self, payload: Mapping[str, Any]) -> None:
        stage = str(payload.get("stage", ""))
        energy = payload.get("current")
        self.write(
            "progress",
            stage=stage,
            energy_mev=energy,
            restored_energy_mev=energy if stage == "restore" else None,
            range_min_mev=payload.get("range_min"),
            range_max_mev=payload.get("range_max"),
            points=payload.get("points"),
            spacing_mev=payload.get("spacing"),
            has_beam=payload.get("has_beam"),
            brightness=payload.get("score"),
            center_mm=payload.get("center_mm"),
            dx_mm=payload.get("center_offset_mm"),
            valid_frames=payload.get("valid_frames"),
            total_frames=payload.get("total_frames"),
            fit_method=payload.get("fit_method"),
            fit_r_squared=payload.get("fit_r_squared"),
            center_spread_mm=payload.get("center_spread_mm"),
            beam_threshold=payload.get("beam_threshold"),
            beam_area_px=payload.get("beam_area_px"),
            beam_major_axis_px=payload.get("beam_major_axis_px"),
            beam_minor_axis_px=payload.get("beam_minor_axis_px"),
            beam_aspect_ratio=payload.get("beam_aspect_ratio"),
            beam_orientation_rad=payload.get("beam_orientation_rad"),
            message=payload.get("diagnostic"),
        )

    def record_result(self, payload: Mapping[str, Any]) -> None:
        center_lock = payload.get("center_lock_result") or {}
        status = str(payload.get("status", "DONE" if payload.get("ok") else "FAILED"))
        message = payload.get("error") or payload.get("message")
        self.write(
            "result",
            status=status,
            initial_energy_mev=payload.get("initial_value"),
            seed_energy_mev=center_lock.get("seed_energy"),
            final_energy_mev=payload.get("best_current"),
            dx_mm=center_lock.get("final_offset_mm"),
            fit_method=center_lock.get("fit_method"),
            center_step_mev=center_lock.get("center_step"),
            message=message,
        )

    def record_stop_requested(self) -> None:
        self.write("operator_stop", status="STOP_REQUESTED")

    def close(self) -> None:
        if self._stream.closed:
            return
        self._stream.flush()
        self._stream.close()
