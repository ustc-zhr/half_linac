"""Pure adaptive quadrupole-scan planning helpers.

This module deliberately contains no Qt, EPICS, or machine-model calls.  It
only decides which K1 values are useful after a small seed scan, which keeps
the online execution path independently testable.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Sequence

import numpy as np


FINAL_VALIDATION_RESERVED_POINTS = 2


@dataclass(frozen=True)
class AdaptiveScanConfig:
    k1_min: float
    k1_max: float
    initial_points: int = 4
    target_points_per_plane: int = 7
    max_unique_points: int = 16
    waist_size_squared_ratio: float = 2.0
    reuse_tolerance: float = 0.01
    max_retries: int = 2

    def __post_init__(self):
        if not math.isfinite(self.k1_min) or not math.isfinite(self.k1_max):
            raise ValueError("Adaptive K1 bounds must be finite.")
        if self.k1_min >= self.k1_max:
            raise ValueError("Adaptive k1_min must be smaller than k1_max.")
        if self.initial_points < 3:
            raise ValueError("Adaptive scans require at least 3 initial points.")
        if self.target_points_per_plane < 3:
            raise ValueError("Adaptive scans require at least 3 target points per plane.")
        if self.max_unique_points < self.initial_points:
            raise ValueError("max_unique_points cannot be smaller than initial_points.")
        if self.waist_size_squared_ratio <= 1:
            raise ValueError("waist_size_squared_ratio must be greater than 1.")
        if self.reuse_tolerance < 0:
            raise ValueError("reuse_tolerance must be non-negative.")
        if self.max_retries < 0:
            raise ValueError("max_retries must be non-negative.")


@dataclass(frozen=True)
class AdaptiveObservation:
    k1: float
    sigx: float | None
    sigy: float | None
    sigx_err: float | None = None
    sigy_err: float | None = None


@dataclass(frozen=True)
class PlaneScanPlan:
    plane: str
    k1_from: float
    k1_to: float
    waist_k1: float
    values: tuple[float, ...]
    method: str


@dataclass(frozen=True)
class AdaptiveScanPlan:
    x: PlaneScanPlan
    y: PlaneScanPlan
    new_values: tuple[float, ...]
    validation_reserved_points: int


@dataclass(frozen=True)
class PlaneValidation:
    plane: str
    status: str
    message: str
    waist_k1: float | None
    left_points: int
    right_points: int
    low_growth_ratio: float | None
    high_growth_ratio: float | None
    warnings: tuple[str, ...]
    suggested_values: tuple[float, ...]

    @property
    def validated(self) -> bool:
        return self.status == "validated"

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "message": self.message,
            "waist_k1": self.waist_k1,
            "left_points": self.left_points,
            "right_points": self.right_points,
            "low_growth_ratio": self.low_growth_ratio,
            "high_growth_ratio": self.high_growth_ratio,
            "warnings": list(self.warnings),
            "suggested_values": list(self.suggested_values),
        }


@dataclass(frozen=True)
class AdaptiveScanValidation:
    x: PlaneValidation
    y: PlaneValidation
    new_values: tuple[float, ...]

    @property
    def status(self) -> str:
        valid_count = int(self.x.validated) + int(self.y.validated)
        if valid_count == 2:
            return "validated"
        if valid_count == 1:
            return "partial"
        return "unresolved"

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "x": self.x.as_dict(),
            "y": self.y.as_dict(),
            "supplement_values": list(self.new_values),
        }


def seed_values(k1_from: float, k1_to: float, config: AdaptiveScanConfig) -> tuple[float, ...]:
    """Return the bounded initial probe values."""
    start = float(np.clip(float(k1_from), config.k1_min, config.k1_max))
    end = float(np.clip(float(k1_to), config.k1_min, config.k1_max))
    if math.isclose(start, end, rel_tol=0.0, abs_tol=config.reuse_tolerance):
        raise ValueError("Adaptive initial K1 range is too small.")
    return tuple(float(value) for value in np.linspace(start, end, config.initial_points))


def build_adaptive_plan(
    observations: Sequence[AdaptiveObservation],
    config: AdaptiveScanConfig,
) -> AdaptiveScanPlan:
    """Plan a fine scan around the independently estimated X/Y waists."""
    if len(observations) < 3:
        raise ValueError("At least 3 observations are required to adapt the scan range.")

    x_plan = _plan_plane(observations, "x", config)
    y_plan = _plan_plane(observations, "y", config)
    measured = [float(point.k1) for point in observations]
    merged = _unique_values((*x_plan.values, *y_plan.values), config.reuse_tolerance)
    candidates = [
        value
        for value in merged
        if not _contains_close(measured, value, config.reuse_tolerance)
    ]
    available = max(
        0,
        config.max_unique_points
        - len(_unique_values(measured, config.reuse_tolerance)),
    )
    validation_reserved_points = min(FINAL_VALIDATION_RESERVED_POINTS, available)
    remaining = max(0, available - validation_reserved_points)
    if len(candidates) > remaining:
        # Preserve coverage of both plane windows while respecting the hard point budget.
        candidates = _evenly_select(candidates, remaining)
    return AdaptiveScanPlan(
        x=x_plan,
        y=y_plan,
        new_values=tuple(candidates),
        validation_reserved_points=validation_reserved_points,
    )


def validate_adaptive_scan(
    observations: Sequence[AdaptiveObservation],
    config: AdaptiveScanConfig,
) -> AdaptiveScanValidation:
    """Validate final X/Y waist coverage and suggest one bounded supplement pass."""
    if len(observations) < 3:
        raise ValueError("At least 3 observations are required to validate an adaptive scan.")

    x_validation = _validate_plane(observations, "x", config)
    y_validation = _validate_plane(observations, "y", config)
    measured = [float(point.k1) for point in observations]
    candidates = _unique_values(
        (*x_validation.suggested_values, *y_validation.suggested_values),
        config.reuse_tolerance,
    )
    candidates = [
        value
        for value in candidates
        if not _contains_close(measured, value, config.reuse_tolerance)
    ]
    remaining = max(
        0,
        config.max_unique_points
        - len(_unique_values(measured, config.reuse_tolerance)),
    )
    if len(candidates) > remaining:
        candidates = _evenly_select(candidates, remaining)
    return AdaptiveScanValidation(
        x=x_validation,
        y=y_validation,
        new_values=tuple(candidates),
    )


def _validate_plane(
    observations: Sequence[AdaptiveObservation],
    plane: str,
    config: AdaptiveScanConfig,
) -> PlaneValidation:
    sigma_name = f"sig{plane}"
    error_name = f"sig{plane}_err"
    valid = []
    for point in observations:
        sigma = getattr(point, sigma_name)
        error = getattr(point, error_name)
        if sigma is None or not math.isfinite(float(sigma)) or float(sigma) <= 0:
            continue
        valid.append((float(point.k1), float(sigma), _positive_error_or_none(error)))
    valid.sort(key=lambda item: item[0])
    if len(valid) < 3:
        return PlaneValidation(
            plane=plane,
            status="insufficient_points",
            message=f"fewer than 3 valid {plane.upper()} observations",
            waist_k1=None,
            left_points=0,
            right_points=0,
            low_growth_ratio=None,
            high_growth_ratio=None,
            warnings=(),
            suggested_values=(),
        )

    k1 = np.asarray([item[0] for item in valid], dtype=float)
    sigma = np.asarray([item[1] for item in valid], dtype=float)
    sigma_err = np.asarray(
        [item[2] if item[2] is not None else np.nan for item in valid],
        dtype=float,
    )
    try:
        coefficients = _weighted_quadratic_fit(k1, sigma, sigma_err)
    except ValueError as exc:
        return _unresolved_plane_validation(plane, k1, sigma, config, str(exc))

    a, b, _c = coefficients
    if not np.all(np.isfinite(coefficients)) or a <= 0:
        return _unresolved_plane_validation(
            plane,
            k1,
            sigma,
            config,
            "final sigma-squared fit is not convex",
        )

    waist = float(-b / (2 * a))
    tolerance = config.reuse_tolerance
    left_mask = k1 < waist - tolerance
    right_mask = k1 > waist + tolerance
    left_points = int(np.count_nonzero(left_mask))
    right_points = int(np.count_nonzero(right_mask))
    observed_min = max(float(np.min(sigma**2)), np.finfo(float).eps)
    low_ratio = (
        float(np.max(sigma[left_mask] ** 2) / observed_min)
        if left_points
        else None
    )
    high_ratio = (
        float(np.max(sigma[right_mask] ** 2) / observed_min)
        if right_points
        else None
    )

    needs_low = waist <= float(k1[0]) + tolerance or left_points == 0
    needs_high = waist >= float(k1[-1]) - tolerance or right_points == 0
    suggested = []
    if needs_low and not _contains_close(k1, config.k1_min, tolerance):
        suggested.append(config.k1_min)
    if needs_high and not _contains_close(k1, config.k1_max, tolerance):
        suggested.append(config.k1_max)

    if needs_low or needs_high:
        if needs_low and needs_high:
            status = "needs_both_sides"
            message = "fitted waist is not bracketed on either side"
        elif needs_low:
            status = "needs_low_k_coverage"
            message = "fitted waist lacks a lower-K measurement"
        else:
            status = "needs_high_k_coverage"
            message = "fitted waist lacks a higher-K measurement"
        if not suggested:
            status = "bound_limited"
            message += "; configured search bound has already been reached"
        return PlaneValidation(
            plane=plane,
            status=status,
            message=message,
            waist_k1=waist,
            left_points=left_points,
            right_points=right_points,
            low_growth_ratio=low_ratio,
            high_growth_ratio=high_ratio,
            warnings=(),
            suggested_values=tuple(suggested),
        )

    warnings = []
    if low_ratio is not None and low_ratio < config.waist_size_squared_ratio:
        warnings.append("limited low-K leverage")
        if not _contains_close(k1, config.k1_min, tolerance):
            suggested.append(config.k1_min)
    if high_ratio is not None and high_ratio < config.waist_size_squared_ratio:
        warnings.append("limited high-K leverage")
        if not _contains_close(k1, config.k1_max, tolerance):
            suggested.append(config.k1_max)
    message = "waist bracketed by measured K1 values"
    if warnings:
        message += "; " + ", ".join(warnings)
    return PlaneValidation(
        plane=plane,
        status="validated",
        message=message,
        waist_k1=waist,
        left_points=left_points,
        right_points=right_points,
        low_growth_ratio=low_ratio,
        high_growth_ratio=high_ratio,
        warnings=tuple(warnings),
        suggested_values=tuple(_unique_values(suggested, tolerance)),
    )


def _unresolved_plane_validation(
    plane: str,
    k1: np.ndarray,
    sigma: np.ndarray,
    config: AdaptiveScanConfig,
    message: str,
) -> PlaneValidation:
    min_index = int(np.argmin(sigma))
    suggested = []
    status = "fit_unresolved"
    if min_index == 0:
        status = "needs_low_k_coverage"
        if not _contains_close(k1, config.k1_min, config.reuse_tolerance):
            suggested.append(config.k1_min)
    elif min_index == len(k1) - 1:
        status = "needs_high_k_coverage"
        if not _contains_close(k1, config.k1_max, config.reuse_tolerance):
            suggested.append(config.k1_max)
    else:
        if not _contains_close(k1, config.k1_min, config.reuse_tolerance):
            suggested.append(config.k1_min)
        if not _contains_close(k1, config.k1_max, config.reuse_tolerance):
            suggested.append(config.k1_max)
    if not suggested and status != "fit_unresolved":
        status = "bound_limited"
        message += "; configured search bound has already been reached"
    return PlaneValidation(
        plane=plane,
        status=status,
        message=message,
        waist_k1=None,
        left_points=0,
        right_points=0,
        low_growth_ratio=None,
        high_growth_ratio=None,
        warnings=(),
        suggested_values=tuple(suggested),
    )


def _plan_plane(
    observations: Sequence[AdaptiveObservation],
    plane: str,
    config: AdaptiveScanConfig,
) -> PlaneScanPlan:
    if plane not in {"x", "y"}:
        raise ValueError(f"Unsupported plane {plane!r}.")
    sigma_name = f"sig{plane}"
    error_name = f"sig{plane}_err"
    valid = []
    for point in observations:
        sigma = getattr(point, sigma_name)
        error = getattr(point, error_name)
        if sigma is None or not math.isfinite(float(sigma)) or float(sigma) <= 0:
            continue
        valid.append((float(point.k1), float(sigma), _positive_error_or_none(error)))
    if len(valid) < 3:
        raise ValueError(f"At least 3 valid {plane.upper()} observations are required.")

    valid.sort(key=lambda item: item[0])
    k1 = np.asarray([item[0] for item in valid], dtype=float)
    sigma = np.asarray([item[1] for item in valid], dtype=float)
    sigma_err = np.asarray(
        [item[2] if item[2] is not None else np.nan for item in valid],
        dtype=float,
    )
    coefficients = _weighted_quadratic_fit(k1, sigma, sigma_err)
    a, b, c = coefficients

    if a > 0 and np.all(np.isfinite(coefficients)):
        waist = float(np.clip(-b / (2 * a), config.k1_min, config.k1_max))
        predicted_min = float(a * waist**2 + b * waist + c)
        observed_min = float(np.min(sigma) ** 2)
        floor = max(predicted_min, observed_min * 0.25, np.finfo(float).eps)
        limit = max(floor * config.waist_size_squared_ratio, observed_min)
        roots = np.roots((a, b, c - limit))
        if not np.iscomplex(roots).any():
            lower, upper = sorted(float(root.real) for root in roots)
            lower = max(lower, config.k1_min)
            upper = min(upper, config.k1_max)
            if upper - lower > config.reuse_tolerance:
                values = tuple(
                    float(value)
                    for value in np.linspace(lower, upper, config.target_points_per_plane)
                )
                return PlaneScanPlan(
                    plane=plane,
                    k1_from=lower,
                    k1_to=upper,
                    waist_k1=waist,
                    values=values,
                    method="quadratic",
                )

    # A poor/concave seed fit usually means that the waist lies outside the
    # initial interval. Extend in the direction in which the measured size falls.
    min_index = int(np.argmin(sigma))
    span = max(float(np.ptp(k1)), config.reuse_tolerance * 2)
    if min_index == 0:
        lower = max(config.k1_min, float(k1[0]) - span)
        upper = float(k1[-1])
        waist = lower
    elif min_index == len(k1) - 1:
        lower = float(k1[0])
        upper = min(config.k1_max, float(k1[-1]) + span)
        waist = upper
    else:
        lower = float(k1[0])
        upper = float(k1[-1])
        waist = float(k1[min_index])
    if upper - lower <= config.reuse_tolerance:
        raise ValueError(f"Cannot construct a bounded adaptive range for {plane.upper()}.")
    values = tuple(
        float(value)
        for value in np.linspace(lower, upper, config.target_points_per_plane)
    )
    return PlaneScanPlan(
        plane=plane,
        k1_from=lower,
        k1_to=upper,
        waist_k1=waist,
        values=values,
        method="directional_fallback",
    )


def _weighted_quadratic_fit(k1: np.ndarray, sigma: np.ndarray, sigma_err: np.ndarray) -> np.ndarray:
    design = np.column_stack((k1**2, k1, np.ones_like(k1)))
    variance_sigma_squared = (2 * sigma * sigma_err) ** 2
    valid_errors = np.isfinite(variance_sigma_squared) & (variance_sigma_squared > 0)
    if np.count_nonzero(valid_errors) == len(k1):
        weights = 1.0 / np.sqrt(variance_sigma_squared)
        design = design * weights[:, None]
        target = sigma**2 * weights
    else:
        target = sigma**2
    coefficients, _residuals, rank, _singular = np.linalg.lstsq(design, target, rcond=None)
    if rank < 3:
        raise ValueError("Seed scan does not span a quadratic fit.")
    return coefficients


def _positive_error_or_none(value) -> float | None:
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) and value > 0 else None


def _contains_close(values: Iterable[float], candidate: float, tolerance: float) -> bool:
    return any(math.isclose(float(value), candidate, rel_tol=0.0, abs_tol=tolerance) for value in values)


def _unique_values(values: Iterable[float], tolerance: float) -> list[float]:
    result = []
    for value in sorted(float(item) for item in values):
        if not _contains_close(result, value, tolerance):
            result.append(value)
    return result


def _evenly_select(values: Sequence[float], count: int) -> list[float]:
    if count <= 0:
        return []
    if count >= len(values):
        return list(values)
    indexes = np.linspace(0, len(values) - 1, count)
    return [values[int(round(index))] for index in indexes]
