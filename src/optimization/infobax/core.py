"""Stateful, offline ``propose -> observe -> recommend`` InfoBAX lifecycle."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

import numpy as np

from .acquisition import InfoBAXEstimator
from .algorithm import BeamEmittanceAlgorithm
from .posterior import FinitePoolM0
from .types import (
    CHECKPOINT_SCHEMA,
    IMPLEMENTATION_VERSION,
    CheckpointError,
    DuplicateObservationError,
    EIGDiagnostic,
    FinitePoolSpec,
    InfoBAXConfig,
    InsufficientDataError,
    PointObservation,
    PoolExhaustedError,
    QuerySuggestion,
    RecommendationResult,
)


def stable_seed(*values: object) -> int:
    """Derive the same cross-process seed used by the frozen research baseline."""

    payload = repr(values).encode("utf-8")
    return int(hashlib.sha256(payload).hexdigest()[:8], 16)


class InfoBAXCore:
    """Pure numerical finite-pool M0 InfoBAX kernel.

    This class owns no machine I/O.  A caller executes the returned query and
    submits only its scalar beam-size variance through :meth:`observe`.
    """

    def __init__(
        self,
        pool: FinitePoolSpec,
        config: InfoBAXConfig | None = None,
    ) -> None:
        if not isinstance(pool, FinitePoolSpec):
            raise TypeError("pool must be a FinitePoolSpec")
        self.pool = pool
        self.config = InfoBAXConfig() if config is None else config
        if not isinstance(self.config, InfoBAXConfig):
            raise TypeError("config must be an InfoBAXConfig")
        if self.config.initial_actions > pool.query_count:
            raise ValueError("initial_actions exceeds the finite query pool")
        self._observations: list[PointObservation] = []
        self._observed_indices: set[int] = set()
        self._initial_design = self._make_initial_design()

    @property
    def observations(self) -> tuple[PointObservation, ...]:
        return tuple(self._observations)

    @property
    def initial_design(self) -> tuple[int, ...]:
        return self._initial_design

    @property
    def available_indices(self) -> tuple[int, ...]:
        return tuple(
            index
            for index in range(self.pool.query_count)
            if index not in self._observed_indices
        )

    def _make_initial_design(self) -> tuple[int, ...]:
        coordinates = self.pool.query_coordinates
        rng = np.random.default_rng(stable_seed("initial_design", self.config.seed))
        selected = [int(rng.integers(0, self.pool.query_count))]
        while len(selected) < self.config.initial_actions:
            remaining = np.asarray(
                [
                    index
                    for index in range(self.pool.query_count)
                    if index not in selected
                ]
            )
            distances = np.min(
                np.linalg.norm(
                    coordinates[remaining, None, :]
                    - coordinates[selected][None, :, :],
                    axis=2,
                ),
                axis=1,
            )
            selected.append(int(remaining[int(np.argmax(distances))]))
        return tuple(selected)

    def _suggestion(
        self,
        query_index: int,
        *,
        phase: str,
        status: str,
        raw_eig_nats: float | None = None,
        eig_standard_error_nats: float | None = None,
        negative_eig_fraction: float | None = None,
        evaluated_query_count: int = 0,
        diagnostics: tuple[EIGDiagnostic, ...] = (),
    ) -> QuerySuggestion:
        candidate_index, scan_index = self.pool.query_parts(query_index)
        return QuerySuggestion(
            query_index=query_index,
            candidate_index=candidate_index,
            candidate_id=self.pool.candidate_ids[candidate_index],
            scan_index=scan_index,
            scan_value=float(self.pool.scan_values[scan_index]),
            phase=phase,
            status=status,
            raw_eig_nats=raw_eig_nats,
            eig_standard_error_nats=eig_standard_error_nats,
            negative_eig_fraction=negative_eig_fraction,
            evaluated_query_count=evaluated_query_count,
            diagnostics=diagnostics,
        )

    def propose(self) -> QuerySuggestion:
        """Return the next unique initial-design or InfoBAX query."""

        if not self.available_indices:
            raise PoolExhaustedError("finite query pool is exhausted")
        for index in self._initial_design:
            if index not in self._observed_indices:
                return self._suggestion(index, phase="initial", status="ready")

        posterior = FinitePoolM0(self.pool, self.config).fit(self._observations)
        algorithm = BeamEmittanceAlgorithm(self.pool)
        active_step = len(self._observations) - self.config.initial_actions + 1
        seed = stable_seed(
            "acquisition", self.config.seed, "M0", "infobax", active_step
        )
        selected, diagnostics = InfoBAXEstimator(
            posterior, algorithm, self.config, seed=seed
        ).select(self.available_indices)
        negative_fraction = float(
            np.mean([item.negative_estimate for item in diagnostics])
        )
        status = (
            "degraded_negative_eig"
            if selected.raw_eig_nats < 0.0
            else "ready"
        )
        return self._suggestion(
            selected.query_index,
            phase="infobax",
            status=status,
            raw_eig_nats=selected.raw_eig_nats,
            eig_standard_error_nats=selected.eig_standard_error_nats,
            negative_eig_fraction=negative_fraction,
            evaluated_query_count=len(diagnostics),
            diagnostics=diagnostics,
        )

    def observe(self, observation: PointObservation) -> None:
        """Commit one independently measured finite-pool scalar observation."""

        if not isinstance(observation, PointObservation):
            raise TypeError("observation must be a PointObservation")
        if observation.query_index < 0 or observation.query_index >= self.pool.query_count:
            raise IndexError("observation index outside finite pool")
        if observation.query_index in self._observed_indices:
            raise DuplicateObservationError(
                f"query {observation.query_index} has already been observed"
            )
        self._observations.append(observation)
        self._observed_indices.add(observation.query_index)

    def _require_initial_design(self) -> None:
        missing = [
            index
            for index in self._initial_design
            if index not in self._observed_indices
        ]
        if missing:
            raise InsufficientDataError(
                "recommendation requires the complete initial design; "
                f"missing queries {missing}"
            )

    def recommend(self) -> RecommendationResult:
        """Summarize posterior optimum probabilities without performing I/O."""

        self._require_initial_design()
        posterior = FinitePoolM0(self.pool, self.config).fit(self._observations)
        algorithm = BeamEmittanceAlgorithm(self.pool)
        active_step = len(self._observations) - self.config.initial_actions
        samples = posterior.sample(
            self.config.summary_samples,
            stable_seed("summary", self.config.seed, "M0", active_step),
        )
        summary = algorithm.summarize(
            samples,
            recommendation_invalid_threshold=(
                self.config.recommendation_invalid_threshold
            ),
        )
        recommended = summary.recommended_candidate
        return RecommendationResult(
            recommended_candidate_index=recommended,
            recommended_candidate_id=(
                self.pool.candidate_ids[recommended]
                if recommended is not None
                else None
            ),
            best_probabilities=tuple(
                float(value) for value in summary.best_probabilities
            ),
            invalid_output_probability=summary.invalid_output_probability,
            all_candidates_physical_probability=(
                summary.all_candidates_physical_probability
            ),
            candidate_physical_probabilities=tuple(
                float(value) for value in summary.candidate_physical_probabilities
            ),
            entropy_nats=summary.entropy_nats,
            sample_count=self.config.summary_samples,
            status="ready" if recommended is not None else "invalid_mass_too_high",
        )

    def to_checkpoint(self) -> dict[str, Any]:
        """Return a versioned, JSON-compatible state without derived matrices."""

        return {
            "schema": CHECKPOINT_SCHEMA,
            "implementation": IMPLEMENTATION_VERSION,
            "pool_fingerprint": self.pool.fingerprint,
            "config": self.config.to_dict(),
            "observations": [item.to_dict() for item in self._observations],
        }

    @classmethod
    def from_checkpoint(
        cls,
        pool: FinitePoolSpec,
        checkpoint: Mapping[str, Any],
    ) -> "InfoBAXCore":
        """Restore a checkpoint only when schema and finite pool match exactly."""

        if not isinstance(checkpoint, Mapping):
            raise CheckpointError("checkpoint must be an object")
        expected_fields = {
            "schema",
            "implementation",
            "pool_fingerprint",
            "config",
            "observations",
        }
        if set(checkpoint) != expected_fields:
            raise CheckpointError("checkpoint fields do not match this version")
        if checkpoint["schema"] != CHECKPOINT_SCHEMA:
            raise CheckpointError("checkpoint schema is not supported")
        if checkpoint["implementation"] != IMPLEMENTATION_VERSION:
            raise CheckpointError("checkpoint implementation is not supported")
        if checkpoint["pool_fingerprint"] != pool.fingerprint:
            raise CheckpointError("checkpoint finite-pool fingerprint does not match")
        config = InfoBAXConfig.from_dict(checkpoint["config"])
        observations = checkpoint["observations"]
        if not isinstance(observations, list):
            raise CheckpointError("checkpoint observations must be a list")
        core = cls(pool, config)
        for row in observations:
            if not isinstance(row, Mapping) or set(row) != {
                "query_index",
                "variance_mm2",
                "noise_std_mm2",
            }:
                raise CheckpointError("checkpoint observation is malformed")
            try:
                core.observe(PointObservation(**dict(row)))
            except (TypeError, ValueError, IndexError, DuplicateObservationError) as error:
                raise CheckpointError(f"invalid checkpoint observation: {error}") from error
        return core
