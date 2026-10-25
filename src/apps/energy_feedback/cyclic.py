"""Helpers for cyclic actuator coordinates such as RF phase."""

from __future__ import annotations

import math


def normalize_cyclic(value: float, *, lower: float, period: float) -> float:
    """Map a finite value into ``[lower, lower + period)``."""

    value = float(value)
    lower = float(lower)
    period = float(period)
    if (
        not all(math.isfinite(item) for item in (value, lower, period))
        or period <= 0
    ):
        raise ValueError("cyclic value, lower bound, and period must be finite")
    return (value - lower) % period + lower


def cyclic_delta(value: float, reference: float, *, period: float) -> float:
    """Return the shortest signed displacement from reference to value."""

    value = float(value)
    reference = float(reference)
    period = float(period)
    if (
        not all(math.isfinite(item) for item in (value, reference, period))
        or period <= 0
    ):
        raise ValueError("cyclic values and period must be finite")
    return (value - reference + period / 2.0) % period - period / 2.0
