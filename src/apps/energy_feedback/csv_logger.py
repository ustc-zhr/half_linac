"""Traceable CSV and metadata for energy feedback sessions."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from half_linac.src.shared.machine_profile import (
    AppContext,
    new_app_run_dir,
    resolve_app_runtime_paths,
)

from .models import EnergyMeasurement, FeedbackDecision, PVReading
from .profile_runtime import APP_DIR


CSV_FIELDS = (
    "sampled_at",
    "measurement_source",
    "source_timestamp",
    "target_source_timestamp",
    "machine",
    "backend",
    "quality",
    "quality_reason",
    "connected",
    "energy_mev",
    "target_mev",
    "error_mev",
    "diagnostics_json",
    "diagnostic_source_timestamps_json",
    "diagnostic_errors_json",
    "marker_value",
    "marker_source_timestamp",
    "decision",
    "decision_reason",
    "knob_delta",
    "actuator_readbacks_json",
    "commands_json",
    "write_verified",
    "rollback_applied",
    "write_detail",
    "control_parameters_json",
)


class EnergyFeedbackCsvLogger:
    def __init__(self, context: AppContext, config: Mapping[str, Any]) -> None:
        kind = "feedback" if config.get("write_enabled") else "monitor"
        run_dir = new_app_run_dir(APP_DIR, context, kind=kind)
        candidate = run_dir
        sequence = 1
        while candidate.exists():
            candidate = run_dir.with_name(f"{run_dir.name}_{sequence:02d}")
            sequence += 1
        candidate.mkdir(parents=True, exist_ok=False)
        self.run_dir = candidate
        prefix = str(config["logging"]["file_prefix"])
        self.csv_path = self.run_dir / f"{prefix}.csv"
        self._stream = self.csv_path.open("w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._stream, fieldnames=CSV_FIELDS)
        self._writer.writeheader()
        self._flush_every = int(config["logging"]["flush_every_n_rows"])
        self._rows = 0
        self._context = context
        self._config = config
        self._measurement_runtime_metadata: dict[str, Any] = {}
        self._write_metadata("running")

    def set_measurement_runtime_metadata(self, metadata: Mapping[str, Any]) -> None:
        self._measurement_runtime_metadata = dict(metadata)
        self._write_metadata("running")

    def append(
        self,
        measurement: EnergyMeasurement,
        decision: FeedbackDecision,
        actuator_readbacks: Mapping[str, PVReading] | None = None,
    ) -> None:
        commands = [
            {
                "actuator": command.actuator_id,
                "current": command.current,
                "delta": command.delta,
                "proposed": command.proposed,
                "unit": command.unit,
            }
            for command in decision.commands
        ]
        self._writer.writerow(
            {
                "sampled_at": _value(measurement.sampled_at),
                "measurement_source": self._config["measurement_source_id"],
                "source_timestamp": _value(measurement.source_timestamp),
                "target_source_timestamp": _value(
                    measurement.target_source_timestamp
                ),
                "machine": self._config["machine_id"],
                "backend": self._config["backend"],
                "quality": measurement.quality.state,
                "quality_reason": measurement.quality.reason,
                "connected": int(measurement.quality.connected),
                "energy_mev": _value(measurement.energy_mev),
                "target_mev": _value(measurement.target_mev),
                "error_mev": _value(measurement.error_mev),
                "diagnostics_json": json.dumps(
                    dict(measurement.diagnostics), sort_keys=True
                ),
                "diagnostic_source_timestamps_json": json.dumps(
                    dict(measurement.diagnostic_source_timestamps), sort_keys=True
                ),
                "diagnostic_errors_json": json.dumps(
                    dict(measurement.diagnostic_errors), sort_keys=True
                ),
                "marker_value": _value(measurement.marker_value),
                "marker_source_timestamp": _value(
                    measurement.marker_source_timestamp
                ),
                "decision": decision.state,
                "decision_reason": decision.reason,
                "knob_delta": _value(decision.knob_delta),
                "actuator_readbacks_json": json.dumps(
                    {
                        actuator_id: {
                            "pv": item.pv_name,
                            "value": item.value,
                            "connected": item.connected,
                            "source_timestamp": item.source_timestamp,
                            "error": item.error,
                        }
                        for actuator_id, item in (actuator_readbacks or {}).items()
                    },
                    sort_keys=True,
                ),
                "commands_json": json.dumps(commands, sort_keys=True),
                "write_verified": int(decision.write_verified),
                "rollback_applied": int(decision.rollback_applied),
                "write_detail": decision.write_detail,
                "control_parameters_json": json.dumps(
                    dict(decision.control_parameters), sort_keys=True
                ),
            }
        )
        self._rows += 1
        if self._rows % self._flush_every == 0:
            self._stream.flush()

    def close(self, state: str = "stopped") -> None:
        if self._stream.closed:
            return
        self._stream.flush()
        self._stream.close()
        self._write_metadata(state)

    def _write_metadata(self, state: str) -> None:
        payload = {
            "schema_version": 2,
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "machine": self._config["machine_id"],
            "backend": self._config["backend"],
            "mode": "write_enabled" if self._config.get("write_enabled") else "read_only",
            "measurement_provider": {
                "id": self._config["measurement_source_id"],
                "kind": self._config["measurement_kind"],
                "label": self._config["measurement_label"],
                "capabilities": list(
                    self._config["measurement_capabilities"]
                ),
            },
            "measurement_runtime": self._measurement_runtime_metadata,
            "state": state,
            "csv_path": str(self.csv_path),
            "signals": self._config["signals"],
            "configuration": self._config["workflow"],
            "profile_snapshot": {
                "schema_version": self._context.profile.schema_version,
                "machine": asdict(self._context.profile.machine),
                "control_backends": list(
                    self._context.profile.control_backends
                ),
                "runtime": (
                    asdict(self._context.profile.runtime)
                    if self._context.profile.runtime is not None
                    else None
                ),
                "elements": [
                    asdict(element) for element in self._context.profile.elements
                ],
                "workflows": self._context.profile.workflows,
            },
        }
        _write_json(self.run_dir / "metadata.json", payload)
        latest = resolve_app_runtime_paths(APP_DIR, self._context)[
            "latest_metadata_path"
        ]
        _write_json(latest, payload)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")


def _value(value: float | None) -> str:
    return "" if value is None else f"{float(value):.12g}"
