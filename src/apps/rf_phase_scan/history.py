from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class RFPhaseHistoryError(ValueError):
    """Raised when a saved RF phase result cannot be used for history review."""


@dataclass(frozen=True)
class PeakHistory:
    path: Path
    schema_version: str
    reviewed: bool
    created_at: str | None
    machine_id: str | None
    backend: str | None
    element_id: str | None
    status: str | None
    crest_phase_command_deg: float
    crest_phase_unwrapped_deg: float | None
    energy_gain_mev: float | None
    r_squared: float | None
    points_used: int | None
    source_json_path: Path | None


@dataclass(frozen=True)
class BatchHistoryItem:
    element_id: str
    display_name: str
    status: str
    crest_phase_deg: float | None
    applied_phase_deg: float | None
    energy_gain_mev: float | None
    r_squared: float | None
    accepted: bool | None
    reasons: tuple[str, ...]
    json_path: Path | None


@dataclass(frozen=True)
class BatchHistory:
    path: Path
    machine_id: str | None
    backend: str | None
    status: str
    updated_at: str | None
    error: str | None
    items: tuple[BatchHistoryItem, ...]


def _read_mapping(path: Path | str) -> tuple[Path, dict[str, Any]]:
    resolved = Path(path)
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RFPhaseHistoryError(f"Could not read {resolved}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RFPhaseHistoryError(f"{resolved} does not contain a JSON object.")
    return resolved, payload


def _finite(value: object, field: str, *, required: bool = False) -> float | None:
    if value is None and not required:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        if not required:
            return None
        raise RFPhaseHistoryError(f"Saved result field {field} is unavailable.") from exc
    if not math.isfinite(number):
        if not required:
            return None
        raise RFPhaseHistoryError(f"Saved result field {field} is not finite.")
    return number


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _source_path(review_path: Path, payload: Mapping[str, Any]) -> Path | None:
    raw = _optional_text(payload.get("source_json_path"))
    if raw is None:
        return None
    source = Path(raw)
    return source if source.is_absolute() else review_path.parent / source


def load_peak_history(path: Path | str) -> PeakHistory:
    resolved, payload = _read_mapping(path)
    schema = str(payload.get("schema_version", ""))
    if schema not in {"phase_energy_scan_v2", "phase_energy_review_v1"}:
        raise RFPhaseHistoryError(
            "Select a phase_energy_scan JSON or its _review.json result."
        )

    reviewed = schema == "phase_energy_review_v1"
    source = _source_path(resolved, payload) if reviewed else resolved
    metadata: Mapping[str, Any] = payload
    if reviewed and source is not None and source.is_file():
        try:
            _source, source_payload = _read_mapping(source)
        except RFPhaseHistoryError:
            pass
        else:
            if source_payload.get("schema_version") == "phase_energy_scan_v2":
                metadata = source_payload

    fit = payload.get("fit")
    if not isinstance(fit, Mapping):
        raise RFPhaseHistoryError("The saved result does not contain a phase-energy fit.")
    crest = _finite(
        fit.get("crest_phase_command_deg"),
        "fit.crest_phase_command_deg",
        required=True,
    )
    points_value = _finite(fit.get("points_used"), "fit.points_used")
    return PeakHistory(
        path=resolved,
        schema_version=schema,
        reviewed=reviewed,
        created_at=_optional_text(payload.get("created_at")),
        machine_id=_optional_text(metadata.get("machine_id")),
        backend=_optional_text(metadata.get("backend")),
        element_id=_optional_text(metadata.get("element_id")),
        status=("REVIEWED" if reviewed else _optional_text(payload.get("status"))),
        crest_phase_command_deg=float(crest),
        crest_phase_unwrapped_deg=_finite(
            fit.get("crest_phase_unwrapped_deg"),
            "fit.crest_phase_unwrapped_deg",
        ),
        energy_gain_mev=_finite(fit.get("amplitude_mev"), "fit.amplitude_mev"),
        r_squared=_finite(fit.get("r_squared"), "fit.r_squared"),
        points_used=(None if points_value is None else int(points_value)),
        source_json_path=source,
    )


def load_batch_history(path: Path | str) -> BatchHistory:
    resolved, payload = _read_mapping(path)
    if payload.get("schema_version") != "rf_crest_batch_v1":
        raise RFPhaseHistoryError("Select an RF Crest Batch batch.json file.")
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise RFPhaseHistoryError("The RF Crest Batch result has no item list.")

    items: list[BatchHistoryItem] = []
    for index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, Mapping):
            raise RFPhaseHistoryError(f"Batch item {index + 1} is invalid.")
        quality = raw_item.get("quality")
        quality = quality if isinstance(quality, Mapping) else {}
        raw_json_path = _optional_text(raw_item.get("json_path"))
        json_path = None
        if raw_json_path is not None:
            json_path = Path(raw_json_path)
            if not json_path.is_absolute():
                json_path = resolved.parent / json_path
        raw_reasons = quality.get("reasons")
        reasons = (
            tuple(str(reason) for reason in raw_reasons)
            if isinstance(raw_reasons, (list, tuple))
            else ()
        )
        accepted = quality.get("accepted")
        items.append(
            BatchHistoryItem(
                element_id=str(raw_item.get("element_id") or f"item-{index + 1}"),
                display_name=str(
                    raw_item.get("display_name")
                    or raw_item.get("element_id")
                    or f"Item {index + 1}"
                ),
                status=str(raw_item.get("status") or "unknown"),
                crest_phase_deg=_finite(
                    quality.get("crest_phase_deg"), "quality.crest_phase_deg"
                ),
                applied_phase_deg=_finite(
                    raw_item.get("applied_phase_deg"), "applied_phase_deg"
                ),
                energy_gain_mev=_finite(
                    quality.get("energy_gain_mev"), "quality.energy_gain_mev"
                ),
                r_squared=_finite(quality.get("r_squared"), "quality.r_squared"),
                accepted=(accepted if isinstance(accepted, bool) else None),
                reasons=reasons,
                json_path=json_path,
            )
        )
    return BatchHistory(
        path=resolved,
        machine_id=_optional_text(payload.get("machine_id")),
        backend=_optional_text(payload.get("backend")),
        status=str(payload.get("status") or "unknown"),
        updated_at=_optional_text(payload.get("updated_at")),
        error=_optional_text(payload.get("error")),
        items=tuple(items),
    )


def latest_scan_history_path(runs_dir: Path | str) -> Path | None:
    return _latest_path(Path(runs_dir), "phase_energy_scan_*.json")


def latest_batch_history_path(runs_dir: Path | str) -> Path | None:
    return _latest_path(Path(runs_dir), "batch.json")


def _latest_path(directory: Path, pattern: str) -> Path | None:
    if not directory.is_dir():
        return None
    try:
        paths = [path for path in directory.rglob(pattern) if path.is_file()]
        return max(paths, key=lambda path: path.stat().st_mtime) if paths else None
    except OSError:
        return None


def format_peak_history(history: PeakHistory) -> str:
    def number(value: float | None, suffix: str = "", precision: int = 2) -> str:
        return "—" if value is None else f"{value:+.{precision}f}{suffix}"

    identity = " / ".join(
        value
        for value in (history.machine_id, history.backend, history.element_id)
        if value
    ) or "Unknown source"
    lines = [
        "Historical result · read-only",
        f"Source: {identity}",
        f"Recorded: {history.created_at or '—'}",
        f"Status: {history.status or '—'}",
        "",
        f"Crest phase (command): {number(history.crest_phase_command_deg, '°')}",
        f"Crest phase (unwrapped): {number(history.crest_phase_unwrapped_deg, '°')}",
        f"Energy gain: {number(history.energy_gain_mev, ' MeV')}",
        f"R²: {number(history.r_squared, precision=4)}",
        f"Points used: {history.points_used if history.points_used is not None else '—'}",
        "",
        f"File: {history.path}",
    ]
    if history.reviewed:
        lines.insert(1, "Manual review result")
    return "\n".join(lines)
