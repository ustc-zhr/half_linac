from __future__ import annotations

import math
import statistics
from typing import Iterable, List, Optional


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def is_finite_number(value: object) -> bool:
    try:
        x = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return math.isfinite(x)


def safe_float(value: object) -> Optional[float]:
    if not is_finite_number(value):
        return None
    return float(value)  # type: ignore[arg-type]


def median(values: Iterable[float]) -> Optional[float]:
    vals = [float(v) for v in values if math.isfinite(float(v))]
    if not vals:
        return None
    return float(statistics.median(vals))


def phase_wrap_deg(angle: float) -> float:
    """Wrap angle to [-180, 180)."""
    return (angle + 180.0) % 360.0 - 180.0


def phase_diff_deg(angle: float, reference: float) -> float:
    return phase_wrap_deg(angle - reference)


def circular_mean_deg(values: Iterable[float]) -> Optional[float]:
    vals: List[float] = [float(v) for v in values if math.isfinite(float(v))]
    if not vals:
        return None
    s = sum(math.sin(math.radians(v)) for v in vals)
    c = sum(math.cos(math.radians(v)) for v in vals)
    if abs(s) < 1e-15 and abs(c) < 1e-15:
        return None
    return phase_wrap_deg(math.degrees(math.atan2(s, c)))
