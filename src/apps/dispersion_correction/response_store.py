from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
import tempfile
from typing import Any

import numpy as np

from half_linac.src.apps.dispersion_correction.models import MachineSnapshot, RunConfig


def _json_values(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, dict):
        return {key: _json_values(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_values(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def _joint_targets_for_signature(section: dict[str, Any]) -> list[dict[str, Any]]:
    targets = section["joint_response_analysis"]["targets"]
    normalized = []
    for target in targets:
        item = dict(target)
        if "normalization_scale_mm" not in item and "tolerance_mm" in item:
            item["normalization_scale_mm"] = item.pop("tolerance_mm")
        normalized.append(item)
    return normalized


def _signature(config: dict[str, Any]) -> dict[str, Any]:
    joint = config["measurement"]["plane"] == "xy"
    section = config["section"]
    knobs = section["joint_response_analysis"]["knobs"] if joint else config["knobs"]
    energy = config["energy_knob"]
    options = config["backend"]["options"]
    return {
        "backend": config["backend"]["type"],
        "machine/backend channels": {
            key: options.get(key)
            for key in ("site", "profile_backend", "pv_map", "bpm_position_scale_to_mm", "model")
        },
        "section": section["id"],
        "plane": config["measurement"]["plane"],
        "BPM order": list(dict.fromkeys(config["monitor_bpms"] + config["target_bpms"])),
        "correction BPMs": config["target_bpms"],
        "joint targets": _joint_targets_for_signature(section) if joint else [],
        "knobs/weights/units": [
            {key: knob[key] for key in ("name", "devices", "unit", "scan_mode")}
            for knob in knobs
        ],
        "energy knob/calibration": {
            key: energy[key]
            for key in ("name", "actuator", "actuator_unit", "calibration", "delta",
                        "round_actuator_step_to_integer", "wrap_period", "wrap_origin")
        },
    }


@dataclass(frozen=True)
class SavedQResponse:
    created_at: str
    config: dict[str, Any]
    matrix: np.ndarray
    snapshot: MachineSnapshot
    measurement: dict[str, Any]
    singular_values: tuple[float, ...]

    @classmethod
    def capture(cls, config: RunConfig, matrix, snapshot: MachineSnapshot, measurement, singular_values):
        return cls(
            created_at=datetime.now().astimezone().isoformat(timespec="microseconds"),
            config=_json_values(asdict(config)),
            matrix=np.asarray(matrix, dtype=float).copy(),
            snapshot=snapshot,
            measurement=_json_values(asdict(measurement)),
            singular_values=tuple(float(value) for value in singular_values),
        )

    def compatibility_error(self, config: RunConfig) -> str | None:
        try:
            saved = _signature(self.config)
            current = _signature(_json_values(asdict(config)))
            mismatches = [key for key in current if current[key] != saved[key]]
            if mismatches:
                return "Incompatible " + ", ".join(mismatches)
            joint = config.measurement.plane == "xy"
            analysis = config.section.joint_response_analysis
            knobs = analysis.knobs if joint else config.knobs
            rows = len(analysis.targets) if joint else len(config.measurement_bpms)
            if self.matrix.shape != (rows, len(knobs)) or not np.all(np.isfinite(self.matrix)):
                return "Invalid response matrix dimensions or non-finite values"
            matrix = self.matrix
            if joint:
                matrix = matrix / np.asarray(
                    [item.normalization_scale_mm for item in analysis.targets]
                )[:, None]
            else:
                matrix = matrix[[name in config.target_bpms for name in config.measurement_bpms]]
            singular = np.linalg.svd(matrix, compute_uv=False)
            if not singular.size or singular[0] <= 0 or not np.any(singular / singular[0] > config.solver.svd_cut):
                return "No usable response modes"
            devices = {name for knob in knobs for name in knob.devices}
            if config.backend.type == "epics" and set(self.snapshot.device_values) != devices:
                return "Missing saved quadrupole baseline"
            if not np.isfinite(self.snapshot.energy_delta) or not all(
                np.isfinite(value) for value in self.snapshot.device_values.values()
            ):
                return "Invalid saved operating point"
        except (KeyError, TypeError, ValueError, np.linalg.LinAlgError):
            return "Invalid response record"
        return None

    def operating_point_summary(self, current_values=None, energy_delta=None) -> str:
        parts = []
        for name, saved in self.snapshot.device_values.items():
            if current_values is not None and name in current_values:
                current = float(current_values[name])
                parts.append(f"{name}: {saved:.6g} → {current:.6g} (Δ {current - saved:+.4g})")
            else:
                parts.append(f"{name}: saved {saved:.6g}; current unknown")
        if not parts:
            parts.append("No absolute quadrupole snapshot (offline model)")
        saved_energy = self.snapshot.energy_delta
        parts.append(
            f"Energy setting Δp/p: {saved_energy:.6g} → {energy_delta:.6g}"
            if energy_delta is not None else f"Saved energy setting Δp/p: {saved_energy:.6g}; current unknown"
        )
        return "; ".join(parts)


def save_response(directory: Path, record: SavedQResponse) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(record.created_at).strftime("%Y%m%d_%H%M%S_%f")
    destination = directory / f"q_response_{stamp}.json"
    payload = {"schema_version": 1, **_json_values(asdict(record))}
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(payload, stream, indent=2)
            stream.write("\n")
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def load_response(path: Path) -> SavedQResponse:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.pop("schema_version") != 1:
        raise ValueError("Unsupported Q response record version")
    datetime.fromisoformat(raw["created_at"])
    return SavedQResponse(
        created_at=raw["created_at"],
        config=raw["config"],
        matrix=np.asarray(raw["matrix"], dtype=float),
        snapshot=MachineSnapshot(**raw["snapshot"]),
        measurement=raw["measurement"],
        singular_values=tuple(raw["singular_values"]),
    )
