"""Machine-independent transverse beam-matrix reconstruction.

The solver works on one uncoupled transverse plane.  A measurement condition
is represented only by the first row ``(r11, r12)`` of its linear transport
map.  Consequently, the same implementation can be used by multi-screen,
quadrupole-scan, or mixed-optics measurements.

All inputs and outputs use SI units: beam sizes in metres, ``r12`` in metres,
geometric emittance in metre-radians, and beta in metres.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

import numpy as np


MIN_MEASUREMENT_COUNT = 3
REQUIRED_RANK = 3
DEFAULT_MAX_CONDITION_NUMBER = 1.0e12
ELECTRON_REST_ENERGY_MEV = 0.51099895000


@dataclass(frozen=True)
class BeamMatrixReconstruction:
    """Result of reconstructing one 2x2 transverse covariance matrix."""

    status: str
    message: str
    measurement_count: int
    rank: int
    degrees_of_freedom: int
    singular_values: tuple[float, ...]
    condition_number: float
    solver: str
    moments: tuple[float, float, float] | None = None
    beam_matrix: tuple[tuple[float, float], tuple[float, float]] | None = None
    geometric_emittance_m_rad: float | None = None
    geometric_emittance_standard_deviation_m_rad: float | None = None
    beta_m: float | None = None
    alpha: float | None = None
    gamma_per_m: float | None = None
    fitted_variances_m2: tuple[float, ...] = ()
    residuals_m2: tuple[float, ...] = ()
    residual_rms_m2: float | None = None
    parameter_covariance: tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ] | None = None
    chi_squared: float | None = None
    reduced_chi_squared: float | None = None
    warnings: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return self.status == "valid"


@dataclass(frozen=True)
class TransverseBeamMatrixReconstruction:
    """Independent horizontal and vertical reconstruction results."""

    x: BeamMatrixReconstruction
    y: BeamMatrixReconstruction

    @property
    def status(self) -> str:
        if self.x.valid and self.y.valid:
            return "valid"
        if self.x.valid or self.y.valid:
            return "partial"
        return "invalid"

    @property
    def valid(self) -> bool:
        return self.status == "valid"


@dataclass(frozen=True)
class MeasurementMatrixDiagnostics:
    """Unit-independent observability diagnostics for one transverse plane."""

    measurement_count: int
    rank: int
    degrees_of_freedom: int
    singular_values: tuple[float, ...]
    condition_number: float
    measurement_matrix: tuple[tuple[float, float, float], ...]


@dataclass(frozen=True)
class BeamMatrixMonteCarloSummary:
    """Emittance recovery statistics for independent Gaussian size errors."""

    trial_count: int
    valid_trial_count: int
    invalid_trial_count: int
    invalid_status_counts: tuple[tuple[str, int], ...]
    relative_rms_size_error: float
    true_emittance_m_rad: float
    true_rms_sizes_m: tuple[float, ...]
    mean_emittance_m_rad: float | None
    standard_deviation_m_rad: float | None
    relative_bias: float | None
    relative_standard_deviation: float | None
    relative_rmse: float | None
    percentile_16_m_rad: float | None
    median_emittance_m_rad: float | None
    percentile_84_m_rad: float | None

    @property
    def valid_fraction(self) -> float:
        return self.valid_trial_count / self.trial_count


@dataclass(frozen=True)
class BeamMatrixUncertaintyEstimate:
    """First-order emittance uncertainty for a specified beam and size error."""

    relative_rms_size_error: float
    true_emittance_m_rad: float
    emittance_standard_deviation_m_rad: float
    relative_emittance_standard_deviation: float
    true_rms_sizes_m: tuple[float, ...]
    parameter_covariance: tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ]


def build_measurement_matrix(
    projections: Sequence[Sequence[float]],
) -> np.ndarray:
    """Build rows ``(r11**2, 2*r11*r12, r12**2)`` from transport projections."""

    values = np.asarray(projections, dtype=float)
    if values.ndim != 2 or values.shape[1] != 2:
        raise ValueError("projections must have shape (N, 2) with rows (r11, r12)")
    if values.shape[0] < MIN_MEASUREMENT_COUNT:
        raise ValueError(
            f"at least {MIN_MEASUREMENT_COUNT} measurement conditions are required"
        )
    if not np.all(np.isfinite(values)):
        raise ValueError("projections contain non-finite values")

    r11 = values[:, 0]
    r12 = values[:, 1]
    return np.column_stack((r11**2, 2.0 * r11 * r12, r12**2))


def diagnose_measurement_matrix(
    projections: Sequence[Sequence[float]],
) -> MeasurementMatrixDiagnostics:
    """Describe rank and conditioning after column normalization."""

    design = build_measurement_matrix(projections)
    column_scales = np.linalg.norm(design, axis=0)
    scaled_design = np.divide(
        design,
        column_scales,
        out=np.zeros_like(design),
        where=column_scales != 0.0,
    )
    singular_values = np.linalg.svd(scaled_design, compute_uv=False)
    rank = int(np.linalg.matrix_rank(scaled_design))
    count = int(design.shape[0])
    return MeasurementMatrixDiagnostics(
        measurement_count=count,
        rank=rank,
        degrees_of_freedom=max(0, count - rank),
        singular_values=tuple(float(value) for value in singular_values),
        condition_number=_condition_number(singular_values, rank),
        measurement_matrix=tuple(
            tuple(float(value) for value in row)
            for row in design
        ),
    )


def simulate_beam_matrix_uncertainty(
    projections: Sequence[Sequence[float]],
    true_moments: Sequence[float],
    relative_rms_size_error: float,
    *,
    trial_count: int = 10_000,
    seed: int | None = 0,
    max_condition_number: float | None = DEFAULT_MAX_CONDITION_NUMBER,
) -> BeamMatrixMonteCarloSummary:
    """Propagate independent relative RMS-size errors through reconstruction.

    Each trial perturbs every screen size independently with a Gaussian error
    whose standard deviation is ``relative_rms_size_error * true_size``.  Only
    physically valid reconstructions contribute to the returned distribution;
    rejected trials are reported separately by status.
    """

    design = build_measurement_matrix(projections)
    moments = np.asarray(true_moments, dtype=float)
    if moments.shape != (3,) or not np.all(np.isfinite(moments)):
        raise ValueError("true_moments must contain exactly 3 finite values")
    a, b, c = (float(value) for value in moments)
    determinant = a * c - b * b
    if a <= 0.0 or c <= 0.0 or determinant <= 0.0:
        raise ValueError("true_moments must define a positive-definite beam matrix")
    relative_error = float(relative_rms_size_error)
    if not math.isfinite(relative_error) or relative_error < 0.0:
        raise ValueError("relative_rms_size_error must be finite and non-negative")
    if (
        not isinstance(trial_count, int)
        or isinstance(trial_count, bool)
        or trial_count <= 0
    ):
        raise ValueError("trial_count must be a positive integer")

    true_variances = design @ moments
    if np.any(true_variances <= 0.0):
        raise ValueError("true_moments produce non-positive beam sizes")
    true_sizes = np.sqrt(true_variances)
    rng = np.random.default_rng(seed)
    recovered_emittances: list[float] = []
    invalid_counts: dict[str, int] = {}
    size_errors = true_sizes * relative_error if relative_error > 0.0 else None

    for noise in rng.normal(size=(trial_count, design.shape[0])):
        measured_sizes = true_sizes * (1.0 + relative_error * noise)
        if np.any(measured_sizes <= 0.0):
            invalid_counts["non_positive_measurement"] = (
                invalid_counts.get("non_positive_measurement", 0) + 1
            )
            continue
        result = reconstruct_beam_matrix(
            projections,
            measured_sizes,
            size_errors,
            max_condition_number=max_condition_number,
        )
        if not result.valid or result.geometric_emittance_m_rad is None:
            invalid_counts[result.status] = invalid_counts.get(result.status, 0) + 1
            continue
        recovered_emittances.append(result.geometric_emittance_m_rad)

    true_emittance = math.sqrt(determinant)
    recovered = np.asarray(recovered_emittances, dtype=float)
    statistics = _monte_carlo_statistics(recovered, true_emittance)
    valid_count = int(recovered.size)
    return BeamMatrixMonteCarloSummary(
        trial_count=trial_count,
        valid_trial_count=valid_count,
        invalid_trial_count=trial_count - valid_count,
        invalid_status_counts=tuple(sorted(invalid_counts.items())),
        relative_rms_size_error=relative_error,
        true_emittance_m_rad=true_emittance,
        true_rms_sizes_m=tuple(float(value) for value in true_sizes),
        **statistics,
    )


def estimate_beam_matrix_uncertainty(
    projections: Sequence[Sequence[float]],
    true_moments: Sequence[float],
    relative_rms_size_error: float,
    *,
    max_condition_number: float | None = DEFAULT_MAX_CONDITION_NUMBER,
) -> BeamMatrixUncertaintyEstimate:
    """Estimate emittance spread by linear covariance propagation."""

    design = build_measurement_matrix(projections)
    moments = np.asarray(true_moments, dtype=float)
    if moments.shape != (3,) or not np.all(np.isfinite(moments)):
        raise ValueError("true_moments must contain exactly 3 finite values")
    a, b, c = (float(value) for value in moments)
    determinant = a * c - b * b
    if a <= 0.0 or c <= 0.0 or determinant <= 0.0:
        raise ValueError("true_moments must define a positive-definite beam matrix")
    relative_error = float(relative_rms_size_error)
    if not math.isfinite(relative_error) or relative_error <= 0.0:
        raise ValueError("relative_rms_size_error must be finite and positive")

    true_variances = design @ moments
    if np.any(true_variances <= 0.0):
        raise ValueError("true_moments produce non-positive beam sizes")
    true_sizes = np.sqrt(true_variances)
    reconstruction = reconstruct_beam_matrix(
        projections,
        true_sizes,
        true_sizes * relative_error,
        max_condition_number=max_condition_number,
    )
    if not reconstruction.valid or reconstruction.parameter_covariance is None:
        raise ValueError(
            "measurement conditions do not support uncertainty estimation: "
            f"{reconstruction.status} {reconstruction.message}".strip()
        )

    true_emittance = math.sqrt(determinant)
    gradient = np.asarray(
        (c / (2.0 * true_emittance), -b / true_emittance, a / (2.0 * true_emittance)),
        dtype=float,
    )
    covariance = np.asarray(reconstruction.parameter_covariance, dtype=float)
    emittance_variance = float(gradient @ covariance @ gradient)
    emittance_standard_deviation = math.sqrt(max(0.0, emittance_variance))
    return BeamMatrixUncertaintyEstimate(
        relative_rms_size_error=relative_error,
        true_emittance_m_rad=true_emittance,
        emittance_standard_deviation_m_rad=emittance_standard_deviation,
        relative_emittance_standard_deviation=(
            emittance_standard_deviation / true_emittance
        ),
        true_rms_sizes_m=tuple(float(value) for value in true_sizes),
        parameter_covariance=reconstruction.parameter_covariance,
    )


def reconstruct_beam_matrix(
    projections: Sequence[Sequence[float]],
    rms_sizes_m: Sequence[float],
    rms_size_errors_m: Sequence[float] | None = None,
    *,
    max_condition_number: float | None = DEFAULT_MAX_CONDITION_NUMBER,
) -> BeamMatrixReconstruction:
    """Reconstruct one transverse beam matrix using (weighted) least squares.

    When ``rms_size_errors_m`` is supplied, uncertainties are propagated to
    measured variances with ``delta(sigma**2) = 2*sigma*delta(sigma)``.  The
    weighted design matrix is column-normalized before SVD so that the reported
    condition number is not an artifact of the units of the three moments.
    """

    design = build_measurement_matrix(projections)
    sizes = _positive_vector(rms_sizes_m, "rms_sizes_m", expected=design.shape[0])
    variances = sizes**2

    errors = None
    if rms_size_errors_m is not None:
        errors = _positive_vector(
            rms_size_errors_m,
            "rms_size_errors_m",
            expected=design.shape[0],
        )
        variance_errors = 2.0 * sizes * errors
        weighted_design = design / variance_errors[:, np.newaxis]
        weighted_variances = variances / variance_errors
        solver = "weighted_svd_lstsq"
    else:
        weighted_design = design
        weighted_variances = variances
        solver = "svd_lstsq"

    if max_condition_number is not None:
        if not math.isfinite(float(max_condition_number)) or max_condition_number <= 1.0:
            raise ValueError("max_condition_number must be greater than 1 or None")

    column_scales = np.linalg.norm(weighted_design, axis=0)
    if np.any(column_scales == 0.0):
        scaled_design = np.divide(
            weighted_design,
            column_scales,
            out=np.zeros_like(weighted_design),
            where=column_scales != 0.0,
        )
    else:
        scaled_design = weighted_design / column_scales

    scaled_moments, _residuals, rank_value, singular_values = np.linalg.lstsq(
        scaled_design,
        weighted_variances,
        rcond=None,
    )
    rank = int(rank_value)
    singular_values = np.asarray(singular_values, dtype=float)
    condition_number = _condition_number(singular_values, rank)
    measurement_count = int(design.shape[0])
    degrees_of_freedom = max(0, measurement_count - rank)

    if rank < REQUIRED_RANK:
        return _invalid_reconstruction(
            status="rank_deficient",
            message=f"measurement matrix has rank {rank}/{REQUIRED_RANK}",
            measurement_count=measurement_count,
            rank=rank,
            degrees_of_freedom=degrees_of_freedom,
            singular_values=singular_values,
            condition_number=condition_number,
            solver=solver,
        )
    if max_condition_number is not None and condition_number > max_condition_number:
        return _invalid_reconstruction(
            status="ill_conditioned",
            message=(
                f"column-normalized condition number {condition_number:.6g} exceeds "
                f"limit {float(max_condition_number):.6g}"
            ),
            measurement_count=measurement_count,
            rank=rank,
            degrees_of_freedom=degrees_of_freedom,
            singular_values=singular_values,
            condition_number=condition_number,
            solver=solver,
        )

    moments_array = scaled_moments / column_scales
    fitted_variances = design @ moments_array
    physical_residuals = fitted_variances - variances
    residual_rms = float(np.sqrt(np.mean(physical_residuals**2)))
    a, b, c = (float(value) for value in moments_array)
    determinant = float(a * c - b * b)

    chi_squared = None
    reduced_chi_squared = None
    if errors is not None:
        weighted_residuals = physical_residuals / (2.0 * sizes * errors)
        chi_squared = float(np.sum(weighted_residuals**2))
        if degrees_of_freedom > 0:
            reduced_chi_squared = chi_squared / degrees_of_freedom

    parameter_covariance = _parameter_covariance(
        scaled_design,
        column_scales,
        physical_residuals,
        degrees_of_freedom,
        errors_are_known=errors is not None,
    )
    common = dict(
        measurement_count=measurement_count,
        rank=rank,
        degrees_of_freedom=degrees_of_freedom,
        singular_values=tuple(float(value) for value in singular_values),
        condition_number=condition_number,
        solver=solver,
        fitted_variances_m2=tuple(float(value) for value in fitted_variances),
        residuals_m2=tuple(float(value) for value in physical_residuals),
        residual_rms_m2=residual_rms,
        parameter_covariance=parameter_covariance,
        chi_squared=chi_squared,
        reduced_chi_squared=reduced_chi_squared,
    )

    if a <= 0.0 or c <= 0.0 or not math.isfinite(determinant) or determinant <= 0.0:
        return BeamMatrixReconstruction(
            status="non_physical",
            message=(
                "reconstructed covariance is not positive definite: "
                f"A={a:.6g}, C={c:.6g}, determinant={determinant:.6g}"
            ),
            moments=(a, b, c),
            **common,
        )

    emittance = math.sqrt(determinant)
    emittance_standard_deviation = _emittance_standard_deviation(
        a,
        b,
        c,
        emittance,
        parameter_covariance,
    )
    warnings = ()
    if errors is None and degrees_of_freedom == 0:
        warnings = (
            "exactly three conditions provide no residual degrees of freedom; "
            "measurement uncertainty cannot be estimated from fit residuals",
        )

    return BeamMatrixReconstruction(
        status="valid",
        message="",
        moments=(a, b, c),
        beam_matrix=((a, b), (b, c)),
        geometric_emittance_m_rad=emittance,
        geometric_emittance_standard_deviation_m_rad=emittance_standard_deviation,
        beta_m=a / emittance,
        alpha=-b / emittance,
        gamma_per_m=c / emittance,
        warnings=warnings,
        **common,
    )


def extract_uncoupled_transverse_projections(
    transfer_matrices: Sequence[Sequence[Sequence[float]]],
    *,
    coupling_tolerance: float = 1.0e-12,
) -> tuple[tuple[tuple[float, float], ...], tuple[tuple[float, float], ...]]:
    """Extract X/Y projection rows from uncoupled 4x4 or 6x6 maps."""

    matrices = np.asarray(transfer_matrices, dtype=float)
    if matrices.ndim != 3 or matrices.shape[1] < 4 or matrices.shape[2] < 4:
        raise ValueError("transfer_matrices must have shape (N, M, M) with M >= 4")
    if matrices.shape[0] < MIN_MEASUREMENT_COUNT:
        raise ValueError(
            f"at least {MIN_MEASUREMENT_COUNT} transfer matrices are required"
        )
    if matrices.shape[1] != matrices.shape[2]:
        raise ValueError("transfer matrices must be square")
    if not np.all(np.isfinite(matrices)):
        raise ValueError("transfer_matrices contain non-finite values")
    if not math.isfinite(float(coupling_tolerance)) or coupling_tolerance < 0.0:
        raise ValueError("coupling_tolerance must be finite and non-negative")

    coupling = matrices[:, (0, 0, 2, 2), (2, 3, 0, 1)]
    if np.any(np.abs(coupling) > coupling_tolerance):
        maximum = float(np.max(np.abs(coupling)))
        raise ValueError(
            "transfer matrices contain x-y coupling terms; independent plane "
            f"reconstruction is invalid (maximum={maximum:.6g})"
        )

    x = tuple((float(matrix[0, 0]), float(matrix[0, 1])) for matrix in matrices)
    y = tuple((float(matrix[2, 2]), float(matrix[2, 3])) for matrix in matrices)
    return x, y


def reconstruct_transverse_beam_matrices(
    transfer_matrices: Sequence[Sequence[Sequence[float]]],
    rms_x_m: Sequence[float],
    rms_y_m: Sequence[float],
    rms_x_errors_m: Sequence[float] | None = None,
    rms_y_errors_m: Sequence[float] | None = None,
    *,
    coupling_tolerance: float = 1.0e-12,
    max_condition_number: float | None = DEFAULT_MAX_CONDITION_NUMBER,
) -> TransverseBeamMatrixReconstruction:
    """Reconstruct both uncoupled transverse planes from common transport maps."""

    x_projections, y_projections = extract_uncoupled_transverse_projections(
        transfer_matrices,
        coupling_tolerance=coupling_tolerance,
    )
    return TransverseBeamMatrixReconstruction(
        x=reconstruct_beam_matrix(
            x_projections,
            rms_x_m,
            rms_x_errors_m,
            max_condition_number=max_condition_number,
        ),
        y=reconstruct_beam_matrix(
            y_projections,
            rms_y_m,
            rms_y_errors_m,
            max_condition_number=max_condition_number,
        ),
    )


def normalized_emittance(
    geometric_emittance_m_rad: float,
    kinetic_energy_mev: float,
    *,
    rest_energy_mev: float = ELECTRON_REST_ENERGY_MEV,
) -> float:
    """Return normalized emittance using the exact relativistic beta*gamma."""

    values = (geometric_emittance_m_rad, kinetic_energy_mev, rest_energy_mev)
    if not all(math.isfinite(float(value)) for value in values):
        raise ValueError("emittance and energies must be finite")
    if geometric_emittance_m_rad < 0.0:
        raise ValueError("geometric_emittance_m_rad must be non-negative")
    if kinetic_energy_mev <= 0.0 or rest_energy_mev <= 0.0:
        raise ValueError("kinetic_energy_mev and rest_energy_mev must be positive")

    relativistic_gamma = 1.0 + kinetic_energy_mev / rest_energy_mev
    beta_gamma = math.sqrt(relativistic_gamma**2 - 1.0)
    return float(geometric_emittance_m_rad) * beta_gamma


def transport_beam_moments(
    transfer_matrix: Sequence[Sequence[float]],
    moments: Sequence[float],
) -> tuple[float, float, float]:
    """Transport ``(A, B, C)`` through one finite 2x2 linear map."""

    matrix = np.asarray(transfer_matrix, dtype=float)
    values = np.asarray(moments, dtype=float)
    if matrix.shape != (2, 2) or not np.all(np.isfinite(matrix)):
        raise ValueError("transfer_matrix must be a finite 2x2 matrix")
    if values.shape != (3,) or not np.all(np.isfinite(values)):
        raise ValueError("moments must contain exactly 3 finite values")
    sigma = np.asarray(
        ((values[0], values[1]), (values[1], values[2])),
        dtype=float,
    )
    transported = matrix @ sigma @ matrix.T
    return (
        float(transported[0, 0]),
        float(transported[0, 1]),
        float(transported[1, 1]),
    )


def _positive_vector(values, name: str, *, expected: int) -> np.ndarray:
    vector = np.asarray(values, dtype=float)
    if vector.ndim != 1 or vector.size != expected:
        raise ValueError(f"{name} must contain exactly {expected} values")
    if not np.all(np.isfinite(vector)):
        raise ValueError(f"{name} contains non-finite values")
    if np.any(vector <= 0.0):
        raise ValueError(f"{name} values must be positive")
    return vector


def _condition_number(singular_values: np.ndarray, rank: int) -> float:
    if rank < REQUIRED_RANK or singular_values.size < REQUIRED_RANK:
        return math.inf
    smallest = float(singular_values[-1])
    if smallest <= 0.0:
        return math.inf
    return float(singular_values[0] / smallest)


def _monte_carlo_statistics(
    recovered_emittances: np.ndarray,
    true_emittance: float,
) -> dict[str, float | None]:
    if recovered_emittances.size == 0:
        return {
            "mean_emittance_m_rad": None,
            "standard_deviation_m_rad": None,
            "relative_bias": None,
            "relative_standard_deviation": None,
            "relative_rmse": None,
            "percentile_16_m_rad": None,
            "median_emittance_m_rad": None,
            "percentile_84_m_rad": None,
        }

    mean = float(np.mean(recovered_emittances))
    standard_deviation = (
        float(np.std(recovered_emittances, ddof=1))
        if recovered_emittances.size > 1
        else 0.0
    )
    percentile_16, median, percentile_84 = np.percentile(
        recovered_emittances,
        (16.0, 50.0, 84.0),
    )
    return {
        "mean_emittance_m_rad": mean,
        "standard_deviation_m_rad": standard_deviation,
        "relative_bias": (mean - true_emittance) / true_emittance,
        "relative_standard_deviation": standard_deviation / true_emittance,
        "relative_rmse": float(
            np.sqrt(np.mean((recovered_emittances - true_emittance) ** 2))
            / true_emittance
        ),
        "percentile_16_m_rad": float(percentile_16),
        "median_emittance_m_rad": float(median),
        "percentile_84_m_rad": float(percentile_84),
    }


def _parameter_covariance(
    scaled_design: np.ndarray,
    column_scales: np.ndarray,
    physical_residuals: np.ndarray,
    degrees_of_freedom: int,
    *,
    errors_are_known: bool,
):
    if not errors_are_known and degrees_of_freedom == 0:
        return None

    covariance_scaled = np.linalg.inv(scaled_design.T @ scaled_design)
    if not errors_are_known:
        residual_variance = float(np.sum(physical_residuals**2) / degrees_of_freedom)
        covariance_scaled *= residual_variance
    inverse_scales = 1.0 / column_scales
    covariance = covariance_scaled * np.outer(inverse_scales, inverse_scales)
    return tuple(
        tuple(float(value) for value in row)
        for row in covariance
    )


def _emittance_standard_deviation(a, b, c, emittance, covariance):
    if covariance is None:
        return None
    gradient = np.asarray(
        (c / (2.0 * emittance), -b / emittance, a / (2.0 * emittance)),
        dtype=float,
    )
    variance = float(gradient @ np.asarray(covariance, dtype=float) @ gradient)
    return math.sqrt(max(0.0, variance))


def _invalid_reconstruction(
    *,
    status: str,
    message: str,
    measurement_count: int,
    rank: int,
    degrees_of_freedom: int,
    singular_values: np.ndarray,
    condition_number: float,
    solver: str,
) -> BeamMatrixReconstruction:
    return BeamMatrixReconstruction(
        status=status,
        message=message,
        measurement_count=measurement_count,
        rank=rank,
        degrees_of_freedom=degrees_of_freedom,
        singular_values=tuple(float(value) for value in singular_values),
        condition_number=condition_number,
        solver=solver,
    )


__all__ = [
    "BeamMatrixMonteCarloSummary",
    "BeamMatrixReconstruction",
    "BeamMatrixUncertaintyEstimate",
    "DEFAULT_MAX_CONDITION_NUMBER",
    "ELECTRON_REST_ENERGY_MEV",
    "MeasurementMatrixDiagnostics",
    "TransverseBeamMatrixReconstruction",
    "build_measurement_matrix",
    "diagnose_measurement_matrix",
    "estimate_beam_matrix_uncertainty",
    "extract_uncoupled_transverse_projections",
    "normalized_emittance",
    "reconstruct_beam_matrix",
    "reconstruct_transverse_beam_matrices",
    "simulate_beam_matrix_uncertainty",
    "transport_beam_moments",
]
