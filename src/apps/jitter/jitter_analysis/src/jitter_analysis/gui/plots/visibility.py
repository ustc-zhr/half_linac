from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import math


def resolve_initial_visibility(
    keys: Sequence[str],
    previous_visibility: Mapping[str, bool] | None = None,
    explicit_visible_keys: Sequence[str] | None = None,
    default_visible_count: int = 0,
) -> dict[str, bool]:
    """Resolve per-series visibility with support for default display limits."""

    previous = {
        str(key): bool(value)
        for key, value in dict(previous_visibility or {}).items()
        if str(key).strip()
    }
    explicit = {
        str(key)
        for key in (explicit_visible_keys or [])
        if str(key).strip()
    }
    visible_limit = max(int(default_visible_count), 0)

    resolved: dict[str, bool] = {}
    for index, key in enumerate(keys):
        token = str(key).strip()
        if not token:
            continue
        if explicit:
            resolved[token] = token in explicit
        elif token in previous:
            resolved[token] = previous[token]
        else:
            resolved[token] = index < visible_limit
    return resolved


def slice_series_tail(
    x_values: Sequence[float],
    y_values: Sequence[float],
    max_points: int | None = None,
) -> tuple[Sequence[float], Sequence[float]]:
    """Return full series or its trailing window while keeping x/y aligned."""
    if max_points is None:
        return x_values, y_values

    limit = max(int(max_points), 0)
    if limit <= 0:
        return _slice_sequence_tail(x_values, 0), _slice_sequence_tail(y_values, 0)
    if len(x_values) <= limit and len(y_values) <= limit:
        return x_values, y_values
    return _slice_sequence_tail(x_values, limit), _slice_sequence_tail(y_values, limit)


def _slice_sequence_tail(values: Sequence[float], limit: int) -> Sequence[float]:
    try:
        return values[-limit:] if limit > 0 else values[:0]
    except TypeError:
        rows = list(values)
        return rows[-limit:] if limit > 0 else rows[:0]


def downsample_series_min_max(
    x_values: Sequence[float],
    y_values: Sequence[float],
    max_points: int,
) -> tuple[list[float], list[float], bool]:
    """Reduce display points while preserving local extrema in each bucket."""

    limit = max(int(max_points), 0)
    length = min(len(x_values), len(y_values))
    if limit <= 0:
        return [], [], length > 0
    if length <= limit:
        return list(x_values[:length]), list(y_values[:length]), False

    bucket_count = max(limit // 2, 1)
    bucket_size = max(int(math.ceil(length / bucket_count)), 1)
    sampled_indices: list[int] = []

    for start in range(0, length, bucket_size):
        stop = min(start + bucket_size, length)
        bucket = y_values[start:stop]
        if not bucket:
            continue

        min_offset = 0
        max_offset = 0
        min_value = float(bucket[0])
        max_value = float(bucket[0])
        for offset, raw_value in enumerate(bucket[1:], start=1):
            value = float(raw_value)
            if value < min_value:
                min_value = value
                min_offset = offset
            if value > max_value:
                max_value = value
                max_offset = offset
        for index in sorted({start + min_offset, start + max_offset}):
            sampled_indices.append(index)

    return (
        [x_values[index] for index in sampled_indices],
        [y_values[index] for index in sampled_indices],
        True,
    )


def padded_finite_range(
    values: Iterable[float],
    *,
    padding_fraction: float = 0.05,
    minimum_span: float = 1.0e-9,
) -> tuple[float, float] | None:
    """Return a padded finite min/max range for plotting."""

    finite_values = []
    for raw_value in values:
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            finite_values.append(value)

    if not finite_values:
        return None

    lower = min(finite_values)
    upper = max(finite_values)
    span = upper - lower
    safe_minimum_span = max(float(minimum_span), 0.0)
    safe_padding_fraction = max(float(padding_fraction), 0.0)

    if not math.isfinite(span) or span <= 0.0:
        scale = max(abs(lower), 1.0)
        half_span = max(scale * safe_padding_fraction, safe_minimum_span)
        return lower - half_span, upper + half_span

    padding = max(span * safe_padding_fraction, safe_minimum_span)
    return lower - padding, upper + padding
