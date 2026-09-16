"""Model-facing helpers for generic multi-screen emittance measurements."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

from half_linac.src.shared.beam_matrix import (
    BeamMatrixReconstruction,
    BeamMatrixUncertaintyEstimate,
    MeasurementMatrixDiagnostics,
    TransverseBeamMatrixReconstruction,
    build_measurement_matrix,
    diagnose_measurement_matrix,
    estimate_beam_matrix_uncertainty,
    extract_uncoupled_transverse_projections,
    reconstruct_transverse_beam_matrices,
)
from half_linac.src.shared.machine_profile import BeamModelBackend


@dataclass(frozen=True)
class MultiScreenOptics:
    """Transport conditions from one reference point to observation elements."""

    reference_element: str
    observation_elements: tuple[str, ...]
    transfer_matrices: tuple[np.ndarray, ...]
    x_projections: tuple[tuple[float, float], ...]
    y_projections: tuple[tuple[float, float], ...]

    @property
    def x_measurement_matrix(self) -> np.ndarray:
        return build_measurement_matrix(self.x_projections)

    @property
    def y_measurement_matrix(self) -> np.ndarray:
        return build_measurement_matrix(self.y_projections)

    @property
    def x_diagnostics(self) -> MeasurementMatrixDiagnostics:
        return diagnose_measurement_matrix(self.x_projections)

    @property
    def y_diagnostics(self) -> MeasurementMatrixDiagnostics:
        return diagnose_measurement_matrix(self.y_projections)


@dataclass(frozen=True)
class BeamSizeSample:
    """One recorded observation; disabled samples never enter reconstruction."""

    screen: str
    sigma_x_m: float | None
    sigma_y_m: float | None
    timestamp_s: float | None = None
    source: str = "unknown"
    quality: Mapping[str, object] | None = None
    enabled: bool = True

    def __post_init__(self) -> None:
        screen = self.screen.strip()
        if not screen:
            raise ValueError("screen must not be empty")
        object.__setattr__(self, "screen", screen)
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled must be a boolean")
        for name in ("sigma_x_m", "sigma_y_m"):
            if getattr(self, name) is None:
                if not self.enabled:
                    continue
                raise ValueError(f"{name} is required for an enabled sample")
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be finite and positive")
            object.__setattr__(self, name, value)
        if self.timestamp_s is not None:
            timestamp = float(self.timestamp_s)
            if not math.isfinite(timestamp):
                raise ValueError("timestamp_s must be finite or None")
            object.__setattr__(self, "timestamp_s", timestamp)
        source = str(self.source).strip()
        if not source:
            raise ValueError("source must not be empty")
        object.__setattr__(self, "source", source)
        if self.quality is not None:
            object.__setattr__(self, "quality", dict(self.quality))


@dataclass(frozen=True)
class ScreenBeamSizeEstimate:
    """Mean beam size and standard error for one observation screen."""

    screen: str
    sigma_x_m: float
    sigma_y_m: float
    sigma_x_error_m: float | None
    sigma_y_error_m: float | None
    sample_count: int


@dataclass(frozen=True)
class MultiScreenBeamSizeData:
    """Fit-ready beam sizes kept in the configured screen order."""

    estimates: tuple[ScreenBeamSizeEstimate, ...]

    @property
    def observation_elements(self) -> tuple[str, ...]:
        return tuple(estimate.screen for estimate in self.estimates)

    @property
    def rms_x_m(self) -> tuple[float, ...]:
        return tuple(estimate.sigma_x_m for estimate in self.estimates)

    @property
    def rms_y_m(self) -> tuple[float, ...]:
        return tuple(estimate.sigma_y_m for estimate in self.estimates)

    @property
    def rms_x_errors_m(self) -> tuple[float, ...] | None:
        return _usable_errors(
            tuple(estimate.sigma_x_error_m for estimate in self.estimates)
        )

    @property
    def rms_y_errors_m(self) -> tuple[float, ...] | None:
        return _usable_errors(
            tuple(estimate.sigma_y_error_m for estimate in self.estimates)
        )


@dataclass(frozen=True)
class MultiScreenOpticsScore:
    """First-order random-error score for one candidate measurement optics."""

    x_uncertainty: BeamMatrixUncertaintyEstimate
    y_uncertainty: BeamMatrixUncertaintyEstimate
    feasible: bool
    rejection_reasons: tuple[str, ...]

    @property
    def objective(self) -> float:
        if not self.feasible:
            return math.inf
        return max(
            self.x_uncertainty.relative_emittance_standard_deviation,
            self.y_uncertainty.relative_emittance_standard_deviation,
        )


@dataclass(frozen=True)
class OpticsObservability:
    """Quality classification for one pair of transverse measurement matrices."""

    status: str
    x: MeasurementMatrixDiagnostics
    y: MeasurementMatrixDiagnostics
    message: str

    @property
    def valid(self) -> bool:
        return self.status in {"good", "marginal"}


@dataclass(frozen=True)
class MultiScreenAcquisition:
    """Pure acquisition state shared by manual, VM, and future live adapters."""

    observation_elements: tuple[str, ...]
    target_samples_per_screen: int
    samples: tuple[BeamSizeSample, ...] = ()

    def __post_init__(self) -> None:
        observations = tuple(str(element).strip() for element in self.observation_elements)
        if len(observations) < 3:
            raise ValueError("at least 3 observation elements are required")
        if any(not element for element in observations):
            raise ValueError("observation element ids must not be empty")
        if len(set(observations)) != len(observations):
            raise ValueError("observation elements must be unique")
        if (
            not isinstance(self.target_samples_per_screen, int)
            or isinstance(self.target_samples_per_screen, bool)
            or self.target_samples_per_screen <= 0
        ):
            raise ValueError("target_samples_per_screen must be a positive integer")
        unknown = sorted(
            {sample.screen for sample in self.samples} - set(observations)
        )
        if unknown:
            raise ValueError(f"samples contain unknown screens: {', '.join(unknown)}")
        object.__setattr__(self, "observation_elements", observations)

    @classmethod
    def create(
        cls,
        observation_elements: Sequence[str],
        target_samples_per_screen: int,
    ) -> "MultiScreenAcquisition":
        return cls(tuple(observation_elements), target_samples_per_screen)

    @property
    def sample_counts(self) -> Mapping[str, int]:
        counts = {screen: 0 for screen in self.observation_elements}
        for sample in self.samples:
            if sample.enabled:
                counts[sample.screen] += 1
        return counts

    @property
    def next_screen(self) -> str | None:
        counts = self.sample_counts
        return next(
            (
                screen
                for screen in self.observation_elements
                if counts[screen] < self.target_samples_per_screen
            ),
            None,
        )

    @property
    def complete(self) -> bool:
        return self.next_screen is None

    def add_sample(self, sample: BeamSizeSample) -> "MultiScreenAcquisition":
        if sample.screen not in self.observation_elements:
            raise ValueError(f"screen {sample.screen!r} is not in this acquisition")
        return replace(self, samples=(*self.samples, sample))

    def set_sample_enabled(self, index: int, enabled: bool) -> "MultiScreenAcquisition":
        samples = list(self.samples)
        samples[index] = replace(samples[index], enabled=enabled)
        return replace(self, samples=tuple(samples))

    @property
    def can_reconstruct(self) -> bool:
        return all(count > 0 for count in self.sample_counts.values())

    def aggregate(self, *, require_target: bool = True) -> MultiScreenBeamSizeData:
        if not self.can_reconstruct or (require_target and not self.complete):
            counts = self.sample_counts
            missing = ", ".join(
                f"{screen} ({counts[screen]}/{self.target_samples_per_screen})"
                for screen in self.observation_elements
                if counts[screen] < self.target_samples_per_screen
            )
            raise ValueError(f"acquisition is incomplete: {missing}")

        estimates = []
        for screen in self.observation_elements:
            screen_samples = tuple(
                sample for sample in self.samples if sample.screen == screen and sample.enabled
            )
            x_values = np.asarray(
                [sample.sigma_x_m for sample in screen_samples],
                dtype=float,
            )
            y_values = np.asarray(
                [sample.sigma_y_m for sample in screen_samples],
                dtype=float,
            )
            estimates.append(
                ScreenBeamSizeEstimate(
                    screen=screen,
                    sigma_x_m=float(np.mean(x_values)),
                    sigma_y_m=float(np.mean(y_values)),
                    sigma_x_error_m=_standard_error(x_values),
                    sigma_y_error_m=_standard_error(y_values),
                    sample_count=len(screen_samples),
                )
            )
        return MultiScreenBeamSizeData(estimates=tuple(estimates))


@dataclass(frozen=True)
class MultiScreenMeasurementSession:
    """Frozen optics plus accepted samples for one multi-screen measurement."""

    machine: str
    backend: str
    preset: str | None
    model_line: str
    energy_mev: float | None
    optics: MultiScreenOptics
    acquisition: MultiScreenAcquisition
    created_at: str
    schema: str = "emit_multi_screen_v1"
    beam_width_method: str = "Gaussian fit"

    @classmethod
    def create(
        cls,
        *,
        machine: str,
        backend: str,
        preset: str | None,
        model_line: str,
        energy_mev: float | None,
        optics: MultiScreenOptics,
        target_samples_per_screen: int,
    ) -> "MultiScreenMeasurementSession":
        return cls(
            machine=str(machine),
            backend=str(backend),
            preset=None if preset is None else str(preset),
            model_line=str(model_line),
            energy_mev=None if energy_mev is None else float(energy_mev),
            optics=optics,
            acquisition=MultiScreenAcquisition.create(
                optics.observation_elements,
                target_samples_per_screen,
            ),
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def add_sample(self, sample: BeamSizeSample) -> "MultiScreenMeasurementSession":
        return replace(self, acquisition=self.acquisition.add_sample(sample))


def measurement_archive_payload(
    session: MultiScreenMeasurementSession,
    *,
    reconstruction: TransverseBeamMatrixReconstruction | None = None,
) -> dict[str, object]:
    """Convert a session into the versioned JSON archive representation."""

    def matrix_payload(matrix: np.ndarray) -> list[list[float]]:
        return [[float(value) for value in row] for row in matrix]

    def plane_payload(projections):
        return [
            {"r11": float(row[0]), "r12_m": float(row[1])}
            for row in projections
        ]

    payload: dict[str, object] = {
        "schema": session.schema,
        "beam_width_method": session.beam_width_method,
        "created_at": session.created_at,
        "machine": session.machine,
        "backend": session.backend,
        "preset": session.preset,
        "model_line": session.model_line,
        "energy_mev": session.energy_mev,
        "reference_element": session.optics.reference_element,
        "observation_elements": list(session.optics.observation_elements),
        "transport_matrices": [matrix_payload(matrix) for matrix in session.optics.transfer_matrices],
        "x_projections": plane_payload(session.optics.x_projections),
        "y_projections": [
            {"r33": float(row[0]), "r34_m": float(row[1])}
            for row in session.optics.y_projections
        ],
        "sampling": {
            "target_samples_per_screen": session.acquisition.target_samples_per_screen,
        },
        "samples": [
            {
                "screen": sample.screen,
                "sigma_x_m": sample.sigma_x_m,
                "sigma_y_m": sample.sigma_y_m,
                "timestamp_s": sample.timestamp_s,
                "source": sample.source,
                "quality": sample.quality,
                "enabled": sample.enabled,
            }
            for sample in session.acquisition.samples
        ],
    }
    if reconstruction is not None:
        payload["reconstruction"] = _reconstruction_payload(reconstruction)
    return payload


def save_multi_screen_archive(
    path: Path | str,
    session: MultiScreenMeasurementSession,
    *,
    reconstruction: TransverseBeamMatrixReconstruction | None = None,
) -> Path:
    """Atomically write a multi-screen measurement archive."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_text(
        json.dumps(
            measurement_archive_payload(session, reconstruction=reconstruction),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)
    return destination


def load_multi_screen_archive(path: Path | str) -> MultiScreenMeasurementSession:
    """Load and validate a versioned multi-screen archive without model access."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != "emit_multi_screen_v1":
        raise ValueError("unsupported multi-screen archive schema")
    elements = tuple(str(value).strip() for value in payload.get("observation_elements", ()))
    matrices_raw = payload.get("transport_matrices")
    x_raw = payload.get("x_projections")
    y_raw = payload.get("y_projections")
    if len(elements) < 3 or len(set(elements)) != len(elements):
        raise ValueError("archive must contain at least three unique screens")
    if not isinstance(matrices_raw, list) or len(matrices_raw) != len(elements):
        raise ValueError("archive transport matrix count does not match screens")
    matrices = tuple(np.asarray(matrix, dtype=float) for matrix in matrices_raw)
    if any(matrix.shape != (6, 6) or not np.all(np.isfinite(matrix)) for matrix in matrices):
        raise ValueError("archive transport matrices must be finite 6x6 arrays")
    if not isinstance(x_raw, list) or not isinstance(y_raw, list):
        raise ValueError("archive projections are missing")
    try:
        x_projections = tuple((float(row["r11"]), float(row["r12_m"])) for row in x_raw)
        y_projections = tuple((float(row["r33"]), float(row["r34_m"])) for row in y_raw)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("archive projections are malformed") from exc
    if len(x_projections) != len(elements) or len(y_projections) != len(elements):
        raise ValueError("archive projection count does not match screens")
    sampling = payload.get("sampling")
    if not isinstance(sampling, dict):
        raise ValueError("archive sampling configuration is missing")
    target = sampling.get("target_samples_per_screen")
    if isinstance(target, bool) or not isinstance(target, int):
        raise ValueError("archive target_samples_per_screen must be an integer")
    acquisition = MultiScreenAcquisition.create(elements, target)
    samples = payload.get("samples", ())
    if not isinstance(samples, list):
        raise ValueError("archive samples must be a list")
    for item in samples:
        if not isinstance(item, dict):
            raise ValueError("archive sample must be an object")
        acquisition = acquisition.add_sample(
            BeamSizeSample(
                item.get("screen", ""),
                item.get("sigma_x_m"),
                item.get("sigma_y_m"),
                item.get("timestamp_s"),
                item.get("source", "unknown"),
                item.get("quality"),
                enabled=item.get("enabled", True),
            )
        )
    optics = MultiScreenOptics(
        reference_element=str(payload.get("reference_element", "")),
        observation_elements=elements,
        transfer_matrices=matrices,
        x_projections=x_projections,
        y_projections=y_projections,
    )
    return MultiScreenMeasurementSession(
        machine=str(payload.get("machine", "")),
        backend=str(payload.get("backend", "")),
        preset=payload.get("preset"),
        model_line=str(payload.get("model_line", "")),
        energy_mev=payload.get("energy_mev"),
        optics=optics,
        acquisition=acquisition,
        created_at=str(payload.get("created_at", "")),
        beam_width_method=str(payload.get("beam_width_method", "Gaussian fit")),
    )


def _reconstruction_payload(result: TransverseBeamMatrixReconstruction) -> dict[str, object]:
    def plane_payload(plane):
        return {
            "status": plane.status,
            "message": plane.message,
            "measurement_count": plane.measurement_count,
            "rank": plane.rank,
            "degrees_of_freedom": plane.degrees_of_freedom,
            "condition_number": plane.condition_number,
            "solver": plane.solver,
            "moments": plane.moments,
            "geometric_emittance_m_rad": plane.geometric_emittance_m_rad,
            "geometric_emittance_standard_deviation_m_rad": (
                plane.geometric_emittance_standard_deviation_m_rad
            ),
            "beta_m": plane.beta_m,
            "alpha": plane.alpha,
            "gamma_per_m": plane.gamma_per_m,
            "fitted_variances_m2": plane.fitted_variances_m2,
            "residuals_m2": plane.residuals_m2,
            "residual_rms_m2": plane.residual_rms_m2,
            "chi_squared": plane.chi_squared,
            "reduced_chi_squared": plane.reduced_chi_squared,
            "warnings": plane.warnings,
        }

    return {"status": result.status, "x": plane_payload(result.x), "y": plane_payload(result.y)}


def reconstruction_from_archive_payload(
    payload: Mapping[str, object] | None,
) -> TransverseBeamMatrixReconstruction | None:
    """Restore the fit portion of a multi-screen archive, when present."""
    if not isinstance(payload, Mapping):
        return None

    def plane_payload(raw):
        if not isinstance(raw, Mapping):
            return None

        def tuple_values(name):
            value = raw.get(name, ())
            return tuple(float(item) for item in value) if isinstance(value, (list, tuple)) else ()

        def optional_float(name):
            value = raw.get(name)
            return None if value is None else float(value)

        return BeamMatrixReconstruction(
            status=str(raw.get("status", "invalid")),
            message=str(raw.get("message", "")),
            measurement_count=int(raw.get("measurement_count", 0)),
            rank=int(raw.get("rank", 0)),
            degrees_of_freedom=int(raw.get("degrees_of_freedom", 0)),
            singular_values=tuple_values("singular_values"),
            condition_number=float(raw.get("condition_number", float("inf"))),
            solver=str(raw.get("solver", "")),
            moments=tuple_values("moments") or None,
            geometric_emittance_m_rad=optional_float("geometric_emittance_m_rad"),
            geometric_emittance_standard_deviation_m_rad=optional_float(
                "geometric_emittance_standard_deviation_m_rad"
            ),
            beta_m=optional_float("beta_m"),
            alpha=optional_float("alpha"),
            gamma_per_m=optional_float("gamma_per_m"),
            fitted_variances_m2=tuple_values("fitted_variances_m2"),
            residuals_m2=tuple_values("residuals_m2"),
            residual_rms_m2=optional_float("residual_rms_m2"),
            chi_squared=optional_float("chi_squared"),
            reduced_chi_squared=optional_float("reduced_chi_squared"),
            warnings=tuple(str(item) for item in raw.get("warnings", ()) or ()),
        )

    x = plane_payload(payload.get("x"))
    y = plane_payload(payload.get("y"))
    return None if x is None or y is None else TransverseBeamMatrixReconstruction(x=x, y=y)


def build_multi_screen_optics(
    model_backend: BeamModelBackend,
    reference_element: str,
    observation_elements: Sequence[str],
    *,
    lattice_overrides: Mapping[str, Mapping[str, float | int | str]] | None = None,
) -> MultiScreenOptics:
    """Calculate reusable screen-to-reference maps using a configured model backend."""

    reference = str(reference_element).strip()
    observations = tuple(str(element).strip() for element in observation_elements)
    if not reference:
        raise ValueError("reference_element must not be empty")
    if len(observations) < 3:
        raise ValueError("at least 3 observation elements are required")
    if any(not element for element in observations):
        raise ValueError("observation element ids must not be empty")
    if len(set(observations)) != len(observations):
        raise ValueError("observation elements must be unique")

    matrices = []
    for element in observations:
        if element == reference:
            matrix = np.eye(6)
        else:
            matrix = np.asarray(
                model_backend.get_map(
                    reference,
                    element,
                    lattice_overrides=lattice_overrides,
                    seq="exit2exit",
                    twiss_only=True,
                ),
                dtype=float,
            )
        if matrix.shape != (6, 6):
            raise ValueError(
                f"model map for {reference} -> {element} must have shape (6, 6)"
            )
        matrices.append(matrix)

    x_projections, y_projections = extract_uncoupled_transverse_projections(matrices)
    return MultiScreenOptics(
        reference_element=reference,
        observation_elements=observations,
        transfer_matrices=tuple(matrices),
        x_projections=x_projections,
        y_projections=y_projections,
    )


def rebuild_multi_screen_optics(
    model_backend: BeamModelBackend,
    baseline: MultiScreenOptics,
    affected_observation_elements: Sequence[str],
    *,
    lattice_overrides: Mapping[str, Mapping[str, float | int | str]],
) -> MultiScreenOptics:
    """Recalculate only observations downstream of changed model elements."""

    affected = {str(element).strip() for element in affected_observation_elements}
    unknown = affected - set(baseline.observation_elements)
    if unknown:
        raise ValueError(
            "affected observations are not in the baseline optics: "
            + ", ".join(sorted(unknown))
        )
    matrices = []
    for element, baseline_matrix in zip(
        baseline.observation_elements,
        baseline.transfer_matrices,
    ):
        if element not in affected:
            matrices.append(baseline_matrix)
            continue
        matrix = np.asarray(
            model_backend.get_map(
                baseline.reference_element,
                element,
                lattice_overrides=lattice_overrides,
                seq="exit2exit",
                twiss_only=True,
            ),
            dtype=float,
        )
        if matrix.shape != (6, 6):
            raise ValueError(
                f"model map for {baseline.reference_element} -> {element} "
                "must have shape (6, 6)"
            )
        matrices.append(matrix)

    x_projections, y_projections = extract_uncoupled_transverse_projections(matrices)
    return MultiScreenOptics(
        reference_element=baseline.reference_element,
        observation_elements=baseline.observation_elements,
        transfer_matrices=tuple(matrices),
        x_projections=x_projections,
        y_projections=y_projections,
    )


def reconstruct_multi_screen_measurement(
    optics: MultiScreenOptics,
    beam_sizes: MultiScreenBeamSizeData,
) -> TransverseBeamMatrixReconstruction:
    """Reconstruct both planes after checking optics/data screen alignment."""

    if beam_sizes.observation_elements != optics.observation_elements:
        raise ValueError("beam-size data must match the optics observation order")
    return reconstruct_transverse_beam_matrices(
        optics.transfer_matrices,
        beam_sizes.rms_x_m,
        beam_sizes.rms_y_m,
        beam_sizes.rms_x_errors_m,
        beam_sizes.rms_y_errors_m,
    )


def assess_multi_screen_observability(
    optics: MultiScreenOptics,
    *,
    good_condition_number: float = 100.0,
    marginal_condition_number: float = 1_000.0,
) -> OpticsObservability:
    """Classify whether both planes are sufficiently observable for a measurement.

    The thresholds describe measurement quality, not the numerical solver limit.
    A rank-deficient plane is invalid; a full-rank plane above the marginal limit
    is reported as poor even though the least-squares solver may still run.
    """

    if not math.isfinite(good_condition_number) or good_condition_number <= 1.0:
        raise ValueError("good_condition_number must be finite and greater than 1")
    if (
        not math.isfinite(marginal_condition_number)
        or marginal_condition_number <= good_condition_number
    ):
        raise ValueError(
            "marginal_condition_number must be finite and above the good threshold"
        )

    x = optics.x_diagnostics
    y = optics.y_diagnostics
    diagnostics = (x, y)
    if any(item.rank < 3 for item in diagnostics):
        status = "invalid"
        message = "at least one plane is rank deficient"
    elif any(item.condition_number > marginal_condition_number for item in diagnostics):
        status = "poor"
        message = "at least one plane has a poor condition number"
    elif any(item.condition_number > good_condition_number for item in diagnostics):
        status = "marginal"
        message = "at least one plane has a marginal condition number"
    else:
        status = "good"
        message = "both planes are full rank and well conditioned"
    return OpticsObservability(status=status, x=x, y=y, message=message)


def assess_multi_screen_prefixes(
    optics: MultiScreenOptics,
    *,
    good_condition_number: float = 100.0,
    marginal_condition_number: float = 1_000.0,
) -> tuple[OpticsObservability, ...]:
    """Assess every ordered 3..N screen prefix of a multi-screen layout."""

    return tuple(
        assess_multi_screen_observability(
            MultiScreenOptics(
                reference_element=optics.reference_element,
                observation_elements=optics.observation_elements[:count],
                transfer_matrices=optics.transfer_matrices[:count],
                x_projections=optics.x_projections[:count],
                y_projections=optics.y_projections[:count],
            ),
            good_condition_number=good_condition_number,
            marginal_condition_number=marginal_condition_number,
        )
        for count in range(3, len(optics.observation_elements) + 1)
    )


def score_multi_screen_optics(
    optics: MultiScreenOptics,
    x_true_moments: Sequence[float],
    y_true_moments: Sequence[float],
    relative_rms_size_error: float,
    *,
    min_rms_size_m: float | None = None,
    max_rms_size_m: float | None = None,
) -> MultiScreenOpticsScore:
    """Score optics by the worse of the predicted X/Y emittance spreads."""

    if min_rms_size_m is not None and (
        not math.isfinite(min_rms_size_m) or min_rms_size_m <= 0.0
    ):
        raise ValueError("min_rms_size_m must be finite and positive or None")
    if max_rms_size_m is not None and (
        not math.isfinite(max_rms_size_m) or max_rms_size_m <= 0.0
    ):
        raise ValueError("max_rms_size_m must be finite and positive or None")
    if (
        min_rms_size_m is not None
        and max_rms_size_m is not None
        and min_rms_size_m >= max_rms_size_m
    ):
        raise ValueError("min_rms_size_m must be below max_rms_size_m")

    x_uncertainty = estimate_beam_matrix_uncertainty(
        optics.x_projections,
        x_true_moments,
        relative_rms_size_error,
    )
    y_uncertainty = estimate_beam_matrix_uncertainty(
        optics.y_projections,
        y_true_moments,
        relative_rms_size_error,
    )
    reasons = []
    for plane, sizes in (
        ("x", x_uncertainty.true_rms_sizes_m),
        ("y", y_uncertainty.true_rms_sizes_m),
    ):
        for screen, size in zip(optics.observation_elements, sizes):
            if min_rms_size_m is not None and size < min_rms_size_m:
                reasons.append(
                    f"{screen} {plane} RMS size {size:.6g} m is below {min_rms_size_m:.6g} m"
                )
            if max_rms_size_m is not None and size > max_rms_size_m:
                reasons.append(
                    f"{screen} {plane} RMS size {size:.6g} m exceeds {max_rms_size_m:.6g} m"
                )
    return MultiScreenOpticsScore(
        x_uncertainty=x_uncertainty,
        y_uncertainty=y_uncertainty,
        feasible=not reasons,
        rejection_reasons=tuple(reasons),
    )


def _standard_error(values: np.ndarray) -> float | None:
    if values.size < 2:
        return None
    return float(np.std(values, ddof=1) / math.sqrt(values.size))


def _usable_errors(
    errors: tuple[float | None, ...],
) -> tuple[float, ...] | None:
    if any(error is None or error <= 0.0 for error in errors):
        return None
    return tuple(float(error) for error in errors if error is not None)


__all__ = [
    "BeamSizeSample",
    "MultiScreenAcquisition",
    "MultiScreenBeamSizeData",
    "MultiScreenOptics",
    "MultiScreenOpticsScore",
    "MultiScreenMeasurementSession",
    "OpticsObservability",
    "ScreenBeamSizeEstimate",
    "assess_multi_screen_observability",
    "assess_multi_screen_prefixes",
    "build_multi_screen_optics",
    "rebuild_multi_screen_optics",
    "reconstruct_multi_screen_measurement",
    "score_multi_screen_optics",
    "load_multi_screen_archive",
    "measurement_archive_payload",
    "save_multi_screen_archive",
]
