from __future__ import annotations

import numpy as np
from scipy.optimize import lsq_linear

from half_linac.src.apps.dispersion_correction.models import ResponseMatrixResult


def solve_bounded_correction(
    response_matrix: np.ndarray,
    dispersion: np.ndarray,
    svd_cut: float,
    gain: float,
    limits: np.ndarray,
    max_step_fraction: float,
    current_values: np.ndarray,
    initial_values: np.ndarray,
    regularization: float = 1.0e-3,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Solve a normalized, bounded least-squares correction problem."""
    matrix = np.asarray(response_matrix, dtype=float)
    target = np.asarray(dispersion, dtype=float)
    limits = np.asarray(limits, dtype=float)
    current = np.asarray(current_values, dtype=float)
    initial = np.asarray(initial_values, dtype=float)
    if matrix.ndim != 2:
        raise ValueError("response_matrix must be 2D")
    if target.shape != (matrix.shape[0],):
        raise ValueError("dispersion length must match response rows")
    expected = (matrix.shape[1],)
    if limits.shape != expected or current.shape != expected or initial.shape != expected:
        raise ValueError("limits, current_values, and initial_values must match response columns")
    if np.any(limits <= 0):
        raise ValueError("limits must be positive")
    if not 0 < gain <= 1:
        raise ValueError("gain must be in (0, 1]")
    if not 0 < max_step_fraction <= 1:
        raise ValueError("max_step_fraction must be in (0, 1]")
    if regularization < 0:
        raise ValueError("regularization must be non-negative")

    step_limits = limits * max_step_fraction
    lower_delta = np.maximum(-step_limits, initial - limits - current)
    upper_delta = np.minimum(step_limits, initial + limits - current)
    if np.any(lower_delta > upper_delta):
        raise ValueError("current knob values are outside configured limits")

    lower = lower_delta / step_limits
    upper = upper_delta / step_limits
    normalized_matrix = matrix * step_limits[np.newaxis, :]
    u_mat, singular_values, _ = np.linalg.svd(normalized_matrix, full_matrices=False)
    if singular_values.size == 0:
        raise ValueError("response_matrix must not be empty")

    s_max = float(np.max(singular_values))
    retained = singular_values / s_max > svd_cut if s_max > 0 else np.zeros_like(singular_values, dtype=bool)
    if not np.any(retained):
        return np.zeros(matrix.shape[1], dtype=float), singular_values, float("inf")

    objective_matrix = u_mat[:, retained].T @ normalized_matrix
    objective_target = u_mat[:, retained].T @ (-gain * target)
    if regularization > 0:
        damping = regularization * s_max
        objective_matrix = np.vstack([objective_matrix, damping * np.eye(matrix.shape[1])])
        objective_target = np.concatenate([objective_target, np.zeros(matrix.shape[1])])

    solution = lsq_linear(
        objective_matrix,
        objective_target,
        bounds=(lower, upper),
        method="trf",
        lsmr_tol="auto",
    )
    if not solution.success:
        raise RuntimeError(f"Bounded least-squares solve failed: {solution.message}")

    delta = np.asarray(solution.x, dtype=float) * step_limits
    delta = np.minimum(np.maximum(delta, lower_delta), upper_delta)
    condition = condition_number(singular_values)
    return delta, singular_values, condition


def condition_number(singular_values: np.ndarray) -> float:
    values = np.asarray(singular_values, dtype=float)
    positive = values[values > 0]
    if positive.size == 0:
        return float("inf")
    smallest = float(np.min(positive))
    if smallest == 0:
        return float("inf")
    return float(np.max(positive) / smallest)


def response_mode_counts(
    result: ResponseMatrixResult,
    svd_cut: float,
) -> tuple[int, int, int, int]:
    """Return retained, required, target-row, and knob mode counts."""

    singular_values = np.asarray(result.singular_values, dtype=float)
    knob_count = len(result.knob_names)
    target_count = int(np.count_nonzero(result.measurement.target_mask))
    required_modes = min(target_count, knob_count)
    if (
        singular_values.size == 0
        or not np.all(np.isfinite(singular_values))
        or float(np.max(singular_values)) <= 0
    ):
        return 0, required_modes, target_count, knob_count
    largest = float(np.max(singular_values))
    retained_modes = int(
        np.count_nonzero(singular_values / largest > float(svd_cut))
    )
    return retained_modes, required_modes, target_count, knob_count


def automatic_response_block_reason(
    result: ResponseMatrixResult | None,
    svd_cut: float,
) -> str | None:
    if result is None:
        return None
    retained, required, target_count, knob_count = response_mode_counts(
        result,
        svd_cut,
    )
    if retained > 0:
        return None
    return (
        "Automatic correction is disabled because the measured Q response has "
        f"{retained}/{required} effective modes for {knob_count} knobs and "
        f"{target_count} target BPMs at svd_cut={svd_cut:g}."
    )


def rank_reduced_response_warning(
    result: ResponseMatrixResult | None,
    svd_cut: float,
) -> str | None:
    if result is None:
        return None
    retained, required, target_count, knob_count = response_mode_counts(
        result,
        svd_cut,
    )
    if retained == 0 or retained >= required:
        return None
    return (
        f"Rank-reduced response: {retained}/{required} effective modes for "
        f"{knob_count} knobs and {target_count} target BPMs. Automatic correction "
        "will act only on the controllable dispersion component and stop if the "
        "measured RMS does not improve."
    )


def response_result(
    matrix: np.ndarray,
    bpm_names: tuple[str, ...],
    knob_names: tuple[str, ...],
    measurement,
) -> ResponseMatrixResult:
    matrix_array = np.asarray(matrix, dtype=float)
    correction_rows = np.asarray(
        getattr(measurement, "target_mask", np.ones(matrix_array.shape[0])),
        dtype=bool,
    )
    singular_values = np.linalg.svd(
        matrix_array[correction_rows, :],
        compute_uv=False,
    )
    return ResponseMatrixResult(
        matrix=matrix_array,
        bpm_names=bpm_names,
        knob_names=knob_names,
        measurement=measurement,
        singular_values=singular_values,
        condition_number=condition_number(singular_values),
    )
