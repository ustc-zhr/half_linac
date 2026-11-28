"""Stateful, offline ``propose -> observe -> recommend`` InfoBAX lifecycle."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

import numpy as np

from .acquisition import InfoBAXEstimator
from .algorithm import BeamEmittanceAlgorithm
from .charge import ICT1Config, ICT1Observation, fit_ict1_posterior
from .posterior import FinitePoolM0
from .types import (
    CHECKPOINT_SCHEMA,
    IMPLEMENTATION_VERSION,
    CheckpointError,
    ConstrainedQuerySuggestion,
    ConstrainedRecommendation,
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


MODEL_IMPLEMENTATION_VERSION = "finite-pool-models-v2"
ICT1_CHECKPOINT_SCHEMA = "half_infobax_checkpoint_v3"
ICT1_IMPLEMENTATION_VERSION = "finite-pool-ict1-v1"


def stable_seed(*values: object) -> int:
    """Derive the same cross-process seed used by the frozen research baseline."""

    payload = repr(values).encode("utf-8")
    return int(hashlib.sha256(payload).hexdigest()[:8], 16)


class InfoBAXCore:
    """Pure numerical finite-pool InfoBAX kernel (M0 remains the default).

    This class owns no machine I/O. A caller submits beam-size variance through
    :meth:`observe` and, when enabled, ICT1 measurements through :meth:`observe_ict1`.
    """

    def __init__(
        self,
        pool: FinitePoolSpec,
        config: InfoBAXConfig | None = None,
        *,
        model: str = "M0",
        initial_queries: tuple[int, ...] | None = None,
        ict1: ICT1Config | None = None,
    ) -> None:
        if not isinstance(pool, FinitePoolSpec):
            raise TypeError("pool must be a FinitePoolSpec")
        self.pool = pool
        self.config = InfoBAXConfig() if config is None else config
        if not isinstance(self.config, InfoBAXConfig):
            raise TypeError("config must be an InfoBAXConfig")
        if self.config.initial_actions > pool.query_count:
            raise ValueError("initial_actions exceeds the finite query pool")
        if model not in {"M0", "transport_moments"}:
            raise ValueError("unsupported posterior model")
        self.model = model
        if ict1 is not None and not isinstance(ict1, ICT1Config):
            raise TypeError("ict1 must be an ICT1Config")
        self.ict1 = ict1
        self._ict1_observations: dict[str, ICT1Observation] = {}
        self._explicit_initial = initial_queries is not None
        if initial_queries is not None:
            initial_queries = tuple(initial_queries)
            if (len(initial_queries) != self.config.initial_actions
                    or len(set(initial_queries)) != len(initial_queries)
                    or any(type(i) is not int or not 0 <= i < pool.query_count
                           for i in initial_queries)):
                raise ValueError("initial_queries must be unique valid indices matching initial_actions")
        self._observations: list[PointObservation] = []
        self._observed_indices: set[int] = set()
        self._initial_design = (self._make_initial_design() if initial_queries is None else initial_queries)

    @property
    def observations(self) -> tuple[PointObservation, ...]:
        return tuple(self._observations)

    @property
    def initial_design(self) -> tuple[int, ...]:
        return self._initial_design

    @property
    def ict1_observations(self) -> tuple[ICT1Observation, ...]:
        return tuple(self._ict1_observations.values())

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
        acquisition_seed: int | None = None,
        direct_prior_probabilities: tuple[float, ...] = (),
    ) -> QuerySuggestion:
        candidate_index, scan_index = self.pool.query_parts(query_index)
        suggestion_type = QuerySuggestion if acquisition_seed is None else ConstrainedQuerySuggestion
        extra = {} if acquisition_seed is None else {
            "acquisition_seed": acquisition_seed,
            "direct_prior_probabilities": direct_prior_probabilities,
        }
        return suggestion_type(
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
            **extra,
        )

    def _posterior(self):
        if self.model == "M0":
            factory = FinitePoolM0
        else:
            from .transport_posterior import TransportMomentGP
            factory = TransportMomentGP
        return factory(self.pool, self.config).fit(self._observations)

    def propose(self, *, check=None) -> QuerySuggestion:
        """Return the next unique initial-design or InfoBAX query."""

        if not self.available_indices:
            raise PoolExhaustedError("finite query pool is exhausted")
        for index in self._initial_design:
            if index not in self._observed_indices:
                return self._suggestion(index, phase="initial", status="ready")

        posterior = self._posterior()
        algorithm = BeamEmittanceAlgorithm(self.pool, constrain_ict1=self.ict1 is not None)
        active_step = len(self._observations) - self.config.initial_actions + 1
        seed = stable_seed(
            "acquisition", self.config.seed, self.model, "infobax", active_step
        )
        estimator_options = {}
        if self.ict1 is not None:
            estimator_options = {
                "ict1_posterior": fit_ict1_posterior(self.pool, self.ict1, self.ict1_observations),
                "scans_per_candidate": self.pool.scan_count,
            }
        estimator = InfoBAXEstimator(posterior, algorithm, self.config, seed=seed, **estimator_options)
        selected, diagnostics = (estimator.select(self.available_indices) if check is None
                                 else estimator.select(self.available_indices, check=check))
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
            **({} if self.ict1 is None else {
                "acquisition_seed": seed,
                "direct_prior_probabilities": tuple(map(float, estimator.direct_prior_probabilities)),
            }),
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

    def observe_ict1(self, observation: ICT1Observation) -> None:
        if self.ict1 is None:
            raise ValueError("ICT1 constraints are disabled")
        if not isinstance(observation, ICT1Observation):
            raise TypeError("observation must be an ICT1Observation")
        if observation.candidate_index >= self.pool.candidate_count:
            raise IndexError("ICT1 observation index outside finite pool")
        if observation.window_id in self._ict1_observations:
            raise DuplicateObservationError(f"ICT1 window {observation.window_id} has already been observed")
        self._ict1_observations[observation.window_id] = observation

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
        posterior = self._posterior()
        algorithm = BeamEmittanceAlgorithm(self.pool, constrain_ict1=self.ict1 is not None)
        active_step = len(self._observations) - self.config.initial_actions
        samples = posterior.sample(
            self.config.summary_samples,
            stable_seed("summary", self.config.seed, self.model, active_step),
        )
        ict1_samples = None
        if self.ict1 is not None:
            ict1_posterior = fit_ict1_posterior(self.pool, self.ict1, self.ict1_observations)
            ict1_samples = ict1_posterior.sample(
                self.config.summary_samples, stable_seed("ict1_summary", self.config.seed, self.model, active_step),
            )
        summary = algorithm.summarize(
            samples,
            ict1_samples,
            recommendation_invalid_threshold=(
                self.config.recommendation_invalid_threshold
            ),
        )
        recommended = summary.recommended_candidate
        result_type = RecommendationResult if self.ict1 is None else ConstrainedRecommendation
        extra = {} if self.ict1 is None else {
            "no_feasible_probability": summary.no_feasible_probability,
            "ict1_feasibility_probabilities": tuple(map(float, summary.ict1_feasibility_probabilities)),
        }
        return result_type(
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
            status=("ready" if recommended is not None else
                    "invalid_mass_too_high" if self.ict1 is None else "no_recommendable_candidate"),
            **extra,
        )

    def to_checkpoint(self) -> dict[str, Any]:
        """Return a versioned, JSON-compatible state without derived matrices."""

        payload = {
            "schema": CHECKPOINT_SCHEMA,
            "implementation": IMPLEMENTATION_VERSION,
            "pool_fingerprint": self.pool.fingerprint,
            "config": self.config.to_dict(),
            "observations": [item.to_dict() for item in self._observations],
        }

        if self.ict1 is not None:
            payload.update(schema=ICT1_CHECKPOINT_SCHEMA, implementation=ICT1_IMPLEMENTATION_VERSION,
                           model=self.model, initial_queries=list(self.initial_design),
                           ict1=self.ict1.to_dict(), ict1_observations=[asdict(item) for item in self.ict1_observations])
        elif self.model != "M0" or self._explicit_initial:
            payload.update(schema="half_infobax_checkpoint_v2",
                           implementation=MODEL_IMPLEMENTATION_VERSION,
                           model=self.model, initial_queries=list(self.initial_design))
        return payload

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
        schema = checkpoint.get("schema")
        constrained = schema == ICT1_CHECKPOINT_SCHEMA
        extended = constrained or schema == "half_infobax_checkpoint_v2"
        if extended:
            expected_fields |= {"model", "initial_queries"}
        if constrained:
            expected_fields |= {"ict1", "ict1_observations"}
        if set(checkpoint) != expected_fields:
            raise CheckpointError("checkpoint fields do not match this version")
        if not extended and checkpoint["schema"] != CHECKPOINT_SCHEMA:
            raise CheckpointError("checkpoint schema is not supported")
        implementation = MODEL_IMPLEMENTATION_VERSION if extended else IMPLEMENTATION_VERSION
        if constrained:
            implementation = ICT1_IMPLEMENTATION_VERSION
        if checkpoint["implementation"] != implementation:
            raise CheckpointError("checkpoint implementation is not supported")
        if checkpoint["pool_fingerprint"] != pool.fingerprint:
            raise CheckpointError("checkpoint finite-pool fingerprint does not match")
        config = InfoBAXConfig.from_dict(checkpoint["config"])
        observations = checkpoint["observations"]
        if not isinstance(observations, list):
            raise CheckpointError("checkpoint observations must be a list")
        try:
            kwargs = ({"model": checkpoint["model"], "initial_queries": checkpoint["initial_queries"]}
                      if extended else {})
            if constrained:
                kwargs["ict1"] = ICT1Config(**checkpoint["ict1"])
            core = cls(pool, config, **kwargs)
        except (TypeError, ValueError) as error:
            raise CheckpointError(f"invalid model or initialization: {error}") from error
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
        if constrained:
            if not isinstance(checkpoint["ict1_observations"], list):
                raise CheckpointError("checkpoint ICT1 observations must be a list")
            for row in checkpoint["ict1_observations"]:
                try:
                    core.observe_ict1(ICT1Observation(**row))
                except (TypeError, ValueError, IndexError, DuplicateObservationError) as error:
                    raise CheckpointError(f"invalid checkpoint ICT1 observation: {error}") from error
        return core
