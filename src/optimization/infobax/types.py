"""Public data contracts for the offline finite-pool InfoBAX kernel."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np


CHECKPOINT_SCHEMA = "half_infobax_checkpoint_v1"
IMPLEMENTATION_VERSION = "p2-b0-m0-v1"


class InfoBAXError(RuntimeError):
    """Base class for explicit InfoBAX lifecycle failures."""


class DuplicateObservationError(InfoBAXError):
    """Raised when a finite-pool query is observed more than once."""


class PoolExhaustedError(InfoBAXError):
    """Raised when no unobserved finite-pool query remains."""


class InsufficientDataError(InfoBAXError):
    """Raised when a posterior operation is requested before initial design."""


class NumericalFailure(InfoBAXError):
    """Raised when posterior or information-gain numerics are invalid."""


class CheckpointError(InfoBAXError):
    """Raised when a checkpoint is incompatible or malformed."""


def _readonly_array(value: Any, *, name: str, ndim: int) -> np.ndarray:
    array = np.array(value, dtype=float, copy=True)
    if array.ndim != ndim:
        raise ValueError(f"{name} must have {ndim} dimensions")
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must contain only finite values")
    array.setflags(write=False)
    return array


@dataclass(frozen=True)
class FinitePoolSpec:
    """Immutable workpoint-by-scan pool consumed by the M0 InfoBAX model.

    ``candidate_coordinates`` and ``scan_coordinates`` are model coordinates,
    not machine setpoints.  They must already be normalized to ``[0, 1]``.
    The flattened query order is candidate-major:
    ``candidate_index * scan_count + scan_index``.
    """

    candidate_ids: Sequence[str]
    candidate_coordinates: np.ndarray
    scan_values: np.ndarray
    scan_coordinates: np.ndarray
    design_matrix: np.ndarray
    energy_mev: float
    observation_noise_std_mm2: float | np.ndarray
    measurement_contract_fingerprint: str | None = None

    def __post_init__(self) -> None:
        ids = tuple(self.candidate_ids)
        if not ids or any(not isinstance(value, str) or not value for value in ids):
            raise ValueError("candidate_ids must be non-empty strings")
        if len(set(ids)) != len(ids):
            raise ValueError("candidate_ids must be unique")

        candidates = _readonly_array(
            self.candidate_coordinates, name="candidate_coordinates", ndim=2
        )
        if candidates.shape[0] != len(ids) or candidates.shape[1] == 0:
            raise ValueError(
                "candidate_coordinates must have shape (candidate_count, dimension)"
            )
        if np.any((candidates < 0.0) | (candidates > 1.0)):
            raise ValueError("candidate_coordinates must be normalized to [0, 1]")
        if len(np.unique(candidates, axis=0)) != len(candidates):
            raise ValueError("candidate_coordinates must be unique")

        scan_values = _readonly_array(self.scan_values, name="scan_values", ndim=1)
        scan_coordinates = _readonly_array(
            self.scan_coordinates, name="scan_coordinates", ndim=1
        )
        if len(scan_values) < 3 or scan_coordinates.shape != scan_values.shape:
            raise ValueError(
                "scan_values and scan_coordinates must have equal length of at least 3"
            )
        if len(np.unique(scan_values)) != len(scan_values):
            raise ValueError("scan_values must be unique")
        if len(np.unique(scan_coordinates)) != len(scan_coordinates):
            raise ValueError("scan_coordinates must be unique")
        if np.any((scan_coordinates < 0.0) | (scan_coordinates > 1.0)):
            raise ValueError("scan_coordinates must be normalized to [0, 1]")

        design = _readonly_array(self.design_matrix, name="design_matrix", ndim=2)
        if design.shape != (len(scan_values), 3):
            raise ValueError("design_matrix must have shape (scan_count, 3)")
        if np.linalg.matrix_rank(design) < 3:
            raise ValueError("design_matrix must have rank 3")

        energy = float(self.energy_mev)
        if not math.isfinite(energy) or energy <= 0.0:
            raise ValueError("energy_mev must be positive and finite")

        query_count = len(ids) * len(scan_values)
        supplied_noise = np.asarray(self.observation_noise_std_mm2, dtype=float)
        if supplied_noise.ndim == 0:
            noise = np.full(query_count, float(supplied_noise))
        elif supplied_noise.shape in ((query_count,), (len(ids), len(scan_values))):
            noise = np.array(supplied_noise, dtype=float, copy=True).reshape(query_count)
        else:
            raise ValueError(
                "observation_noise_std_mm2 must be scalar or match the finite pool"
            )
        if not np.all(np.isfinite(noise)) or np.any(noise <= 0.0):
            raise ValueError("observation noise must be positive and finite")
        noise.setflags(write=False)

        object.__setattr__(self, "candidate_ids", ids)
        object.__setattr__(self, "candidate_coordinates", candidates)
        object.__setattr__(self, "scan_values", scan_values)
        object.__setattr__(self, "scan_coordinates", scan_coordinates)
        object.__setattr__(self, "design_matrix", design)
        object.__setattr__(self, "energy_mev", energy)
        object.__setattr__(self, "observation_noise_std_mm2", noise)
        contract = self.measurement_contract_fingerprint
        if contract is not None and (
            not isinstance(contract, str)
            or len(contract) != 64
            or any(character not in "0123456789abcdef" for character in contract)
        ):
            raise ValueError("measurement_contract_fingerprint must be a SHA-256 hex digest")

    @property
    def candidate_count(self) -> int:
        return len(self.candidate_ids)

    @property
    def candidate_dimension(self) -> int:
        return int(self.candidate_coordinates.shape[1])

    @property
    def scan_count(self) -> int:
        return int(len(self.scan_values))

    @property
    def query_count(self) -> int:
        return self.candidate_count * self.scan_count

    @property
    def query_coordinates(self) -> np.ndarray:
        candidates = np.repeat(
            self.candidate_coordinates, self.scan_count, axis=0
        )
        scans = np.tile(self.scan_coordinates, self.candidate_count)[:, None]
        return np.column_stack((candidates, scans))

    def query_parts(self, query_index: int) -> tuple[int, int]:
        index = int(query_index)
        if index < 0 or index >= self.query_count:
            raise IndexError("query index outside finite pool")
        return divmod(index, self.scan_count)

    @property
    def fingerprint(self) -> str:
        payload = {
            "candidate_ids": list(self.candidate_ids),
            "candidate_coordinates": self.candidate_coordinates.tolist(),
            "scan_values": self.scan_values.tolist(),
            "scan_coordinates": self.scan_coordinates.tolist(),
            "design_matrix": self.design_matrix.tolist(),
            "energy_mev": self.energy_mev,
            "observation_noise_std_mm2": self.observation_noise_std_mm2.tolist(),
        }
        # Keep unbound offline pools compatible with the frozen M0 evidence.
        if self.measurement_contract_fingerprint is not None:
            payload["measurement_contract_fingerprint"] = self.measurement_contract_fingerprint
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True)
class InfoBAXConfig:
    """Frozen numerical controls for the audited M0 baseline."""

    seed: int = 0
    initial_actions: int = 6
    outer_fantasies: int = 24
    inner_samples: int = 512
    batch_count: int = 4
    direct_prior_samples: int = 2048
    summary_samples: int = 1024
    candidate_length_scale: float = 0.32
    scan_length_scale: float = 0.30
    recommendation_invalid_threshold: float = 0.50

    def __post_init__(self) -> None:
        integer_fields = (
            "seed",
            "initial_actions",
            "outer_fantasies",
            "inner_samples",
            "batch_count",
            "direct_prior_samples",
            "summary_samples",
        )
        for name in integer_fields:
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{name} must be an integer")
        if self.seed < 0:
            raise ValueError("seed must be non-negative")
        if self.initial_actions < 1:
            raise ValueError("initial_actions must be positive")
        if self.outer_fantasies < 4 or self.inner_samples < 16:
            raise ValueError("InfoBAX requires at least 4 fantasies and 16 samples")
        if self.batch_count < 2 or self.batch_count > self.inner_samples // 8:
            raise ValueError("batch_count is incompatible with inner_samples")
        if self.direct_prior_samples < 16 or self.summary_samples < 16:
            raise ValueError("posterior sample counts must be at least 16")
        for name in ("candidate_length_scale", "scan_length_scale"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be positive and finite")
            object.__setattr__(self, name, value)
        threshold = float(self.recommendation_invalid_threshold)
        if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
            raise ValueError("recommendation_invalid_threshold must be in [0, 1]")
        object.__setattr__(self, "recommendation_invalid_threshold", threshold)

    def to_dict(self) -> dict[str, int | float]:
        return {
            "seed": self.seed,
            "initial_actions": self.initial_actions,
            "outer_fantasies": self.outer_fantasies,
            "inner_samples": self.inner_samples,
            "batch_count": self.batch_count,
            "direct_prior_samples": self.direct_prior_samples,
            "summary_samples": self.summary_samples,
            "candidate_length_scale": self.candidate_length_scale,
            "scan_length_scale": self.scan_length_scale,
            "recommendation_invalid_threshold": self.recommendation_invalid_threshold,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "InfoBAXConfig":
        if not isinstance(payload, Mapping):
            raise CheckpointError("checkpoint config must be an object")
        expected = set(cls().to_dict())
        if set(payload) != expected:
            raise CheckpointError("checkpoint config fields do not match this version")
        try:
            return cls(**dict(payload))
        except (TypeError, ValueError) as error:
            raise CheckpointError(f"invalid checkpoint config: {error}") from error


@dataclass(frozen=True)
class PointObservation:
    query_index: int
    variance_mm2: float
    noise_std_mm2: float

    def __post_init__(self) -> None:
        if not isinstance(self.query_index, int) or isinstance(self.query_index, bool):
            raise ValueError("query_index must be an integer")
        variance = float(self.variance_mm2)
        noise = float(self.noise_std_mm2)
        if not math.isfinite(variance) or variance <= 0.0:
            raise ValueError("variance_mm2 must be positive and finite")
        if not math.isfinite(noise) or noise <= 0.0:
            raise ValueError("noise_std_mm2 must be positive and finite")
        object.__setattr__(self, "variance_mm2", variance)
        object.__setattr__(self, "noise_std_mm2", noise)

    def to_dict(self) -> dict[str, int | float]:
        return {
            "query_index": self.query_index,
            "variance_mm2": self.variance_mm2,
            "noise_std_mm2": self.noise_std_mm2,
        }


@dataclass(frozen=True)
class EIGDiagnostic:
    query_index: int
    raw_eig_nats: float
    eig_standard_error_nats: float
    prior_entropy_nats: float
    posterior_entropy_mean_nats: float
    direct_prior_entropy_nats: float
    prior_probability_l1: float
    negative_estimate: bool


@dataclass(frozen=True)
class QuerySuggestion:
    query_index: int
    candidate_index: int
    candidate_id: str
    scan_index: int
    scan_value: float
    phase: str
    status: str
    raw_eig_nats: float | None
    eig_standard_error_nats: float | None
    negative_eig_fraction: float | None
    evaluated_query_count: int
    diagnostics: tuple[EIGDiagnostic, ...] = ()


@dataclass(frozen=True)
class ConstrainedQuerySuggestion(QuerySuggestion):
    acquisition_seed: int = 0
    direct_prior_probabilities: tuple[float, ...] = ()


@dataclass(frozen=True)
class RecommendationResult:
    recommended_candidate_index: int | None
    recommended_candidate_id: str | None
    best_probabilities: tuple[float, ...]
    invalid_output_probability: float
    all_candidates_physical_probability: float
    candidate_physical_probabilities: tuple[float, ...]
    entropy_nats: float
    sample_count: int
    status: str


@dataclass(frozen=True)
class ConstrainedRecommendation(RecommendationResult):
    no_feasible_probability: float
    ict1_feasibility_probabilities: tuple[float, ...]

