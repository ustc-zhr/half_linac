"""Read-only diagnostics for archived emittance-optimization measurements."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Mapping

import numpy as np


# Presentation-only warning below the solver's hard 1e12 rejection limit.
DIAGNOSTIC_REVIEW_CONDITION = 1.0e10


def load_optimization_run(path):
    """Load an optimization archive without constructing a runnable session."""
    source = Path(path)
    if source.is_dir():
        source = source / "optimization.json"
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read optimization archive: {exc}") from exc
    if not isinstance(payload, Mapping) or payload.get("schema_version") != "emit_optimization_v2":
        raise ValueError("The selected file is not a supported emittance optimization archive.")
    if not isinstance(payload.get("records"), list):
        raise ValueError("The optimization archive has no measurement records.")
    return payload, source.parent


def load_measurement_diagnostics(record, *, run_dir=None):
    """Combine one optimization record with its private scan archive."""
    if not isinstance(record, Mapping):
        raise ValueError("Measurement record is invalid.")
    archive = _measurement_directory(record, run_dir)
    results_path = _find_scan_file(archive)
    metadata = {}
    points = np.empty((0, 3), dtype=float)
    messages = []

    if results_path is None:
        messages.append("Archived scan points are unavailable.")
    else:
        try:
            loaded = np.loadtxt(results_path, ndmin=2)
            if loaded.ndim != 2 or loaded.shape[1] < 3:
                raise ValueError("expected K1, sigma X and sigma Y columns")
            points = np.asarray(loaded[:, :3], dtype=float)
        except (OSError, ValueError) as exc:
            messages.append(f"Cannot read archived scan points: {exc}")
            points = np.empty((0, 3), dtype=float)
        metadata_path = results_path.with_name("metadata.json")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            messages.append(f"Detailed scan metadata is unavailable: {exc}")

    result = record.get("result") if isinstance(record.get("result"), Mapping) else {}
    fit_summary = metadata.get("fit_summary") if isinstance(metadata, Mapping) else None
    if isinstance(fit_summary, Mapping):
        fit = fit_summary.get("leastSquares", fit_summary)
    else:
        fit = {}
    planes = {}
    for plane in ("x", "y"):
        key = plane + "plane"
        archived = fit.get(key) if isinstance(fit, Mapping) else None
        terminal = result.get(key) if isinstance(result, Mapping) else None
        planes[plane] = dict(archived if isinstance(archived, Mapping) else
                             terminal if isinstance(terminal, Mapping) else {})

    point_quality = metadata.get("point_quality", []) if isinstance(metadata, Mapping) else []
    quality = _quality_by_row(points, point_quality)
    rating, reasons = _diagnostic_rating(record, metadata, planes, quality, messages)
    return {
        "record": dict(record),
        "archive": archive,
        "results_path": results_path,
        "metadata": metadata,
        "points": points,
        "point_quality": quality,
        "planes": planes,
        "rating": rating,
        "reasons": reasons,
        "messages": messages,
    }


def plane_metric(plane, key, default=None):
    """Read equivalent names used by terminal and metadata summaries."""
    aliases = {
        "exn": ("exn_raw", "exn", "normalized_emittance"),
        "ex": ("ex", "emittance"),
    }
    for candidate in aliases.get(key, (key,)):
        value = plane.get(candidate)
        if value is not None:
            return value
    return default


def _measurement_directory(record, run_dir):
    raw = record.get("archive")
    if raw:
        archive = Path(raw)
        if archive.exists():
            return archive
    if run_dir is not None:
        try:
            index = int(record.get("index"))
        except (TypeError, ValueError):
            index = 0
        if index > 0:
            candidate = Path(run_dir) / f"measurement_{index:03d}"
            if candidate.exists() or not raw:
                return candidate
        if raw:
            candidate = Path(run_dir) / Path(raw).name
            if candidate.exists():
                return candidate
    return Path(raw) if raw else Path("__missing_measurement_archive__")


def _find_scan_file(archive):
    candidates = [archive / "latest" / "scanResults.txt"]
    runs = archive / "runs"
    if runs.is_dir():
        candidates.extend(sorted(runs.glob("scan_*/scanResults.txt"), reverse=True))
    candidates.append(archive / "scanResults.txt")
    return next((path for path in candidates if path.is_file()), None)


def _quality_by_row(points, entries):
    if not isinstance(entries, list):
        entries = []
    rows = []
    for index, point in enumerate(points):
        entry = entries[index] if index < len(entries) and isinstance(entries[index], Mapping) else {}
        rows.append({
            "k1": float(point[0]),
            "x": dict(entry.get("x", {})) if isinstance(entry.get("x"), Mapping) else {},
            "y": dict(entry.get("y", {})) if isinstance(entry.get("y"), Mapping) else {},
        })
    return rows


def _diagnostic_rating(record, metadata, planes, quality, messages):
    reasons = list(messages)
    invalid = False
    result = record.get("result") if isinstance(record.get("result"), Mapping) else {}
    error = record.get("error") or result.get("error")
    if error:
        reasons.append(str(error))
        invalid = True
    if not record.get("valid", False):
        reasons.append("The optimization controller did not accept this measurement.")
        invalid = True

    strategy = metadata.get("scan_strategy") if isinstance(metadata, Mapping) else None
    review = False
    for plane_name, plane in planes.items():
        label = plane_name.upper()
        status = str(plane.get("status", "unavailable"))
        if status != "valid":
            reasons.append(f"{label} reconstruction status is {status}.")
            invalid = True
        validation = plane.get("validation_status")
        if strategy == "adaptive_quality" and validation != "validated":
            reasons.append(f"{label} adaptive validation status is {validation or 'unavailable'}.")
            invalid = True
        selection = plane.get("fit_selection")
        if isinstance(selection, Mapping) and selection.get("status") == "expanded_window":
            reasons.append(f"{label} fit needed points outside its preferred adaptive window.")
            review = True
        warnings = plane.get("coverage_warnings")
        if isinstance(warnings, list) and warnings:
            reasons.extend(f"{label}: {warning}" for warning in warnings)
            review = True
        condition = _finite(plane.get("condition_number"))
        if condition is not None and condition > DIAGNOSTIC_REVIEW_CONDITION:
            reasons.append(f"{label} fit is weakly conditioned ({condition:.3g}).")
            review = True

    rejected = sum(
        1 for row in quality
        if any(item and not item.get("usable", False) for item in (row["x"], row["y"]))
    )
    if rejected:
        reasons.append(f"{rejected} archived samples were rejected by image-quality checks.")
        review = True
    if not metadata:
        reasons.append("Detailed scan metadata is unavailable; only the terminal result can be reviewed.")
        review = True
    if not reasons:
        reasons.append("No review flags were found in the archived diagnostics.")
    return ("Invalid" if invalid else "Review" if review else "Good"), reasons


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
