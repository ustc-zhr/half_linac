from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from .models import MachineProfileError


@dataclass(frozen=True)
class LimitRange:
    """An absolute or relative numeric range for one logical channel."""

    low: float | None = None
    high: float | None = None
    unit: str | None = None

    def __post_init__(self) -> None:
        low = _finite_optional(self.low, "low")
        high = _finite_optional(self.high, "high")
        unit = str(self.unit).strip() if self.unit is not None else None
        if unit == "":
            unit = None
        if low is not None and high is not None and low >= high:
            raise MachineProfileError("Limit range low must be less than high.")
        object.__setattr__(self, "low", low)
        object.__setattr__(self, "high", high)
        object.__setattr__(self, "unit", unit)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "LimitRange":
        return cls(low=value.get("low"), high=value.get("high"), unit=value.get("unit"))

    @classmethod
    def symmetric(cls, bound: float, unit: str | None = None) -> "LimitRange":
        value = float(bound)
        if not math.isfinite(value) or value <= 0:
            raise MachineProfileError("Symmetric limit must be a finite value greater than zero.")
        return cls(low=-value, high=value, unit=unit)

    def intersect(self, other: "LimitRange") -> "LimitRange":
        unit = _compatible_unit(self.unit, other.unit)
        low = _max_optional(self.low, other.low)
        high = _min_optional(self.high, other.high)
        if low is not None and high is not None and low >= high:
            raise MachineProfileError(
                f"Limit ranges do not overlap: {self.describe()} and {other.describe()}."
            )
        return LimitRange(low=low, high=high, unit=unit)

    def relative_to_absolute(self, center: float) -> "LimitRange":
        center_value = _finite_optional(center, "center")
        assert center_value is not None
        return LimitRange(
            low=None if self.low is None else center_value + self.low,
            high=None if self.high is None else center_value + self.high,
            unit=self.unit,
        )

    def absolute_to_relative(self, center: float) -> "LimitRange":
        center_value = _finite_optional(center, "center")
        assert center_value is not None
        return LimitRange(
            low=None if self.low is None else self.low - center_value,
            high=None if self.high is None else self.high - center_value,
            unit=self.unit,
        )

    def contains(self, value: float) -> bool:
        selected = _finite_optional(value, "value")
        assert selected is not None
        return (self.low is None or selected >= self.low) and (
            self.high is None or selected <= self.high
        )

    def clip(self, value: float) -> float:
        selected = _finite_optional(value, "value")
        assert selected is not None
        if self.low is not None:
            selected = max(selected, self.low)
        if self.high is not None:
            selected = min(selected, self.high)
        return selected

    def describe(self) -> str:
        low = "-inf" if self.low is None else f"{self.low:g}"
        high = "+inf" if self.high is None else f"{self.high:g}"
        suffix = f" {self.unit}" if self.unit else ""
        return f"[{low}, {high}]{suffix}"


def effective_limit(*limits: LimitRange | None) -> LimitRange:
    """Intersect configured ranges; no configured range means unbounded."""
    selected = [limit for limit in limits if limit is not None]
    if not selected:
        return LimitRange()
    result = selected[0]
    for limit in selected[1:]:
        result = result.intersect(limit)
    return result


def _compatible_unit(first: str | None, second: str | None) -> str | None:
    if first is None:
        return second
    if second is None:
        return first
    if first.casefold() != second.casefold():
        raise MachineProfileError(f"Cannot intersect limits with units {first!r} and {second!r}.")
    return first


def _finite_optional(value: float | None, label: str) -> float | None:
    if value is None:
        return None
    selected = float(value)
    if not math.isfinite(selected):
        raise MachineProfileError(f"Limit {label} must be finite.")
    return selected


def _max_optional(first: float | None, second: float | None) -> float | None:
    if first is None:
        return second
    if second is None:
        return first
    return max(first, second)


def _min_optional(first: float | None, second: float | None) -> float | None:
    if first is None:
        return second
    if second is None:
        return first
    return min(first, second)
