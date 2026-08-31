from __future__ import annotations

import random
from itertools import product


def collect_random_knob_ranges(selected_knobs, rows):
    selected_by_id = {knob.id: knob for knob in selected_knobs}
    knob_ranges = []
    for knob_id, row in rows.items():
        if not bool(row.get("enabled", True)):
            continue
        knob = selected_by_id.get(knob_id)
        if knob is None:
            continue
        low_text = str(row.get("low_text", "")).strip()
        high_text = str(row.get("high_text", "")).strip()
        if not low_text or not high_text:
            raise ValueError(f"Low/High must be set for {knob.name}.")
        try:
            low = float(low_text)
            high = float(high_text)
        except ValueError as exc:
            raise ValueError(f"Low/High must be numeric for {knob.name}.") from exc
        if low > high:
            raise ValueError(f"Low must be <= High for {knob.name}.")
        if low < float(knob.limits.low) or high > float(knob.limits.high):
            raise ValueError(
                f"Random range for {knob.name} must stay within [{knob.limits.low}, {knob.limits.high}]."
            )
        knob_ranges.append(
            {
                "knob": knob,
                "low": low,
                "high": high,
            }
        )
    if not knob_ranges:
        raise ValueError("Enable at least one control PV row for Multi-Knob sampling.")
    return knob_ranges


def parse_manual_scan_values(text: str) -> list[float]:
    raw_tokens = []
    for chunk in text.replace("\n", ",").replace(";", ",").split(","):
        token = chunk.strip()
        if token:
            raw_tokens.append(token)
    if not raw_tokens:
        raise ValueError("Enter one or more scan values, for example: -0.2, -0.1, 0.0, 0.1")
    try:
        return [float(token) for token in raw_tokens]
    except ValueError as exc:
        raise ValueError("Scan values must be numeric, separated by commas.") from exc


def generate_values_by_step(start: float, stop: float, step: float) -> list[float]:
    if step <= 0:
        raise ValueError("Step must be positive.")
    if start == stop:
        return [start]

    direction = 1.0 if stop >= start else -1.0
    delta = direction * abs(step)
    values = []
    current = start
    epsilon = abs(step) * 1.0e-6
    while (direction > 0 and current <= stop + epsilon) or (direction < 0 and current >= stop - epsilon):
        values.append(current)
        current += delta

    if not values:
        values = [start]
    if abs(values[-1] - stop) > epsilon:
        values.append(stop)
    return values


def generate_values_by_points(start: float, stop: float, num_points: int) -> list[float]:
    if num_points <= 0:
        raise ValueError("Num points must be positive.")
    if num_points == 1:
        return [start]
    step = (stop - start) / float(num_points - 1)
    return [start + step * index for index in range(num_points)]


def generate_random_targets(knob_ranges, distribution: str, num_points: int, seed: int):
    if num_points <= 0:
        raise ValueError("Num points must be positive.")
    if distribution != "uniform":
        raise ValueError(f"Unsupported random distribution: {distribution}")
    rng = random.Random(seed)
    target_steps = []
    for _ in range(num_points):
        step_targets = {}
        for spec in knob_ranges:
            knob = spec["knob"]
            low = float(spec["low"])
            high = float(spec["high"])
            if low == high:
                value = low
            else:
                value = rng.uniform(low, high)
            step_targets[knob.id] = value
        target_steps.append(step_targets)
    return target_steps


def grid_point_count(knob_ranges, levels_per_knob: int) -> int:
    if levels_per_knob < 2:
        raise ValueError("Levels / Knob must be at least 2.")
    count = 1
    for spec in knob_ranges:
        count *= 1 if float(spec["low"]) == float(spec["high"]) else int(levels_per_knob)
    return count


def generate_grid_targets(
    knob_ranges,
    levels_per_knob: int,
    seed: int,
    *,
    max_points: int = 1000,
):
    varying_count = sum(float(spec["low"]) != float(spec["high"]) for spec in knob_ranges)
    if varying_count > 3:
        raise ValueError("Grid supports at most 3 control PVs with changing ranges.")
    point_count = grid_point_count(knob_ranges, levels_per_knob)
    if point_count > max_points:
        raise ValueError(
            f"Grid would generate {point_count} points; reduce Levels / Knob or use Uniform Random "
            f"(maximum {max_points} grid points)."
        )

    value_lists = []
    for spec in knob_ranges:
        low = float(spec["low"])
        high = float(spec["high"])
        value_lists.append(
            [low] if low == high else generate_values_by_points(low, high, int(levels_per_knob))
        )
    target_steps = [
        {
            spec["knob"].id: float(value)
            for spec, value in zip(knob_ranges, combination)
        }
        for combination in product(*value_lists)
    ]
    random.Random(seed).shuffle(target_steps)
    return target_steps


def generate_multi_knob_targets(
    knob_ranges,
    sampling_method: str,
    num_points: int,
    levels_per_knob: int,
    seed: int,
):
    if sampling_method == "uniform_random":
        return generate_random_targets(knob_ranges, "uniform", num_points, seed)
    if sampling_method == "grid":
        return generate_grid_targets(knob_ranges, levels_per_knob, seed)
    raise ValueError(f"Unsupported sampling method: {sampling_method}")


def random_preview_payload(knob_ranges, target_steps, sampling_method: str, preview_limit: int = 20):
    preview_count = min(len(target_steps), int(preview_limit))
    lines = []
    for index in range(preview_count):
        values = target_steps[index]
        parts = []
        for spec in knob_ranges:
            knob = spec["knob"]
            parts.append(f"{knob.name}={float(values[knob.id]):.6g}")
        lines.append(f"{index + 1:03d}: " + ", ".join(parts))
    if len(target_steps) > preview_count:
        lines.append(f"... {len(target_steps) - preview_count} more point(s)")

    method_label = "Uniform Random" if sampling_method == "uniform_random" else "Grid"
    summary = f"{len(target_steps)} point(s) across {len(knob_ranges)} knob(s)  |  {method_label}"
    detail = ", ".join(
        f"{spec['knob'].name}[{spec['low']:.6g}, {spec['high']:.6g}]"
        for spec in knob_ranges
    )
    return {
        "lines": lines,
        "summary": summary,
        "detail": detail,
    }


def single_knob_preview_payload(values, knob_name: str, unit: str, mode: str, center=None) -> dict[str, str]:
    summary = (
        f"{len(values)} point(s) for {knob_name}: "
        f"{min(values):.6g} to {max(values):.6g} {unit}"
    )
    if len(values) >= 2:
        first_step = values[1] - values[0]
        summary += f"  |  first step {first_step:.6g}"

    detail = ""
    if mode == "symmetric_points" and center is not None:
        detail = f"Preview center from {knob_name}: {float(center):.6g} {unit}"

    return {
        "summary": summary,
        "detail": detail,
    }
