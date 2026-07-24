from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from half_linac.src.apps.dispersion_correction.models import BPMReading, DispersionMeasurement


def momentum_delta(delta_value: float) -> float:
    """Return delta = dp/p. For this tool dp/p is treated as equal to dE/E."""
    return float(delta_value)


def rms(values: np.ndarray, valid: np.ndarray | None = None) -> float:
    data = np.asarray(values, dtype=float)
    if valid is not None:
        data = data[np.asarray(valid, dtype=bool)]
    data = data[np.isfinite(data)]
    if data.size == 0:
        return float("nan")
    return float(np.sqrt(np.mean(data * data)))


def weighted_rms(values: np.ndarray, weights: np.ndarray | None = None) -> float:
    data = np.asarray(values, dtype=float)
    finite = np.isfinite(data)
    data = data[finite]
    if data.size == 0:
        return float("nan")
    if weights is None:
        return rms(data)
    weights_array = np.asarray(weights, dtype=float)[finite]
    positive = weights_array > 0
    if not np.any(positive):
        return float("nan")
    data = data[positive]
    weights_array = weights_array[positive]
    return float(np.sqrt(np.average(data * data, weights=weights_array)))


def robust_average(readings: Sequence[BPMReading]) -> BPMReading:
    if not readings:
        raise ValueError("At least one BPM reading is required")
    names = readings[0].names
    for reading in readings:
        if reading.names != names:
            raise ValueError("All BPM readings must use the same BPM order")

    x_stack = np.vstack([reading.x_mm for reading in readings])
    y_stack = np.vstack([reading.y_mm for reading in readings])
    valid_stack = np.vstack([reading.valid for reading in readings])

    x_avg = _masked_median(x_stack, valid_stack)
    y_avg = _masked_median(y_stack, valid_stack)
    valid = np.any(valid_stack, axis=0) & np.isfinite(x_avg) & np.isfinite(y_avg)

    charges = [reading.charge for reading in readings if reading.charge is not None]
    losses = [reading.loss for reading in readings if reading.loss is not None]

    return BPMReading(
        names=names,
        x_mm=x_avg,
        y_mm=y_avg,
        valid=valid,
        charge=float(np.median(charges)) if charges else None,
        loss=float(np.median(losses)) if losses else None,
    )


def compute_effective_dispersion(
    bpm_names: Sequence[str],
    plus: BPMReading,
    minus: BPMReading,
    delta: float,
    plane: str = "x",
    target_values_mm: Sequence[float] | None = None,
    target_mask: Sequence[bool] | None = None,
) -> DispersionMeasurement:
    if delta <= 0:
        raise ValueError("delta must be positive")
    if plus.names != minus.names:
        raise ValueError("Plus and minus BPM readings must use the same BPM order")
    if tuple(bpm_names) != plus.names:
        raise ValueError("bpm_names must match BPM reading order")
    if plane != "x":
        raise ValueError("MVP supports horizontal effective dispersion only")

    numerator = plus.x_mm - minus.x_mm
    values = numerator / (2.0 * float(delta))
    valid = plus.valid & minus.valid & np.isfinite(values)
    return DispersionMeasurement(
        bpm_names=tuple(bpm_names),
        plane=plane,
        delta=float(delta),
        values_mm=values,
        valid=valid,
        plus=plus,
        minus=minus,
        target_values_mm=target_values_mm,
        target_mask=target_mask,
    )


def _masked_median(values: np.ndarray, valid: np.ndarray) -> np.ndarray:
    masked = np.where(valid, values, np.nan)
    with np.errstate(all="ignore"):
        return np.nanmedian(masked, axis=0)
