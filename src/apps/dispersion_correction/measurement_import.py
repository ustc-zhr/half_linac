from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Iterable

import numpy as np

from half_linac.src.apps.dispersion_correction.models import ImportedDispersionDataset


REQUIRED_COLUMNS = ("bpm", "etax_mm")
OPTIONAL_SIGMA_COLUMN = "etax_sigma_mm"


def load_dispersion_csv(
    path: str | Path,
    *,
    section_id: str,
    allowed_bpms: Iterable[str],
) -> ImportedDispersionDataset:
    source_path = Path(path)
    allowed = {str(name).strip() for name in allowed_bpms if str(name).strip()}
    try:
        stream = source_path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise ValueError(f"Cannot open imported dispersion CSV: {source_path}") from exc

    names: list[str] = []
    values: list[float] = []
    uncertainties: list[float] = []
    seen: set[str] = set()
    with stream:
        reader = csv.DictReader(stream)
        columns = set(reader.fieldnames or ())
        missing = [column for column in REQUIRED_COLUMNS if column not in columns]
        if missing:
            raise ValueError(
                "Imported dispersion CSV is missing column(s): " + ", ".join(missing)
            )
        for line_number, row in enumerate(reader, start=2):
            bpm = str(row.get("bpm", "")).strip()
            if not bpm:
                raise ValueError(f"CSV line {line_number}: bpm must not be empty")
            if bpm in seen:
                raise ValueError(f"CSV line {line_number}: duplicate BPM {bpm}")
            if bpm not in allowed:
                raise ValueError(
                    f"CSV line {line_number}: BPM {bpm} is not in the current model section"
                )
            etax = _finite_float(row.get("etax_mm"), line_number, "etax_mm")
            raw_sigma = row.get(OPTIONAL_SIGMA_COLUMN)
            sigma = (
                float("nan")
                if raw_sigma is None or not str(raw_sigma).strip()
                else _finite_float(raw_sigma, line_number, OPTIONAL_SIGMA_COLUMN)
            )
            if math.isfinite(sigma) and sigma < 0:
                raise ValueError(
                    f"CSV line {line_number}: {OPTIONAL_SIGMA_COLUMN} must be non-negative"
                )
            names.append(bpm)
            values.append(etax)
            uncertainties.append(sigma)
            seen.add(bpm)

    if not names:
        raise ValueError("Imported dispersion CSV contains no data rows")
    return ImportedDispersionDataset(
        section_id=str(section_id),
        bpm_names=tuple(names),
        etax_mm=np.asarray(values, dtype=float),
        etax_sigma_mm=np.asarray(uncertainties, dtype=float),
        source_path=str(source_path.resolve()),
    )


def _finite_float(value: object, line_number: int, column: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"CSV line {line_number}: {column} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"CSV line {line_number}: {column} must be finite")
    return number
