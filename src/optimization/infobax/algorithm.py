"""Virtual full-scan emittance algorithm used as the InfoBAX output."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ...shared.beam_matrix import ELECTRON_REST_ENERGY_MEV
from .types import FinitePoolSpec


@dataclass(frozen=True)
class AlgorithmSamples:
    categories: np.ndarray
    emittances_mm_mrad: np.ndarray
    candidate_physical: np.ndarray
    all_candidates_physical: np.ndarray


@dataclass(frozen=True)
class OutputSummary:
    recommended_candidate: int | None
    best_probabilities: np.ndarray
    invalid_output_probability: float
    all_candidates_physical_probability: float
    candidate_physical_probabilities: np.ndarray
    entropy_nats: float


def categorical_entropy(categories: np.ndarray, category_count: int) -> float:
    values = np.asarray(categories, dtype=int)
    if values.ndim != 1 or len(values) == 0:
        raise ValueError("at least one category sample is required")
    counts = np.bincount(values, minlength=category_count).astype(float)
    probabilities = counts[counts > 0] / len(values)
    return -float(np.sum(probabilities * np.log(probabilities)))


def beta_gamma(energy_mev: float) -> float:
    gamma = 1.0 + float(energy_mev) / ELECTRON_REST_ENERGY_MEV
    beta = math.sqrt(1.0 - 1.0 / gamma**2)
    return beta * gamma


class BeamEmittanceAlgorithm:
    """Map one latent complete response pool to its best physical workpoint.

    Every posterior sample is retained.  A sample with no physical workpoint
    maps to the explicit final ``INVALID`` category.
    """

    def __init__(self, pool: FinitePoolSpec) -> None:
        self.pool = pool
        self.candidate_count = pool.candidate_count
        self.category_count = self.candidate_count + 1
        self.invalid_category = self.candidate_count
        self._design_pseudoinverse = np.linalg.pinv(pool.design_matrix)

    def evaluate(self, state_samples: np.ndarray) -> AlgorithmSamples:
        samples = np.asarray(state_samples, dtype=float)
        if samples.ndim != 2 or samples.shape[1] != self.pool.query_count:
            raise ValueError(
                "state_samples must have shape (sample_count, query_count)"
            )
        curves = samples.reshape(
            -1, self.pool.candidate_count, self.pool.scan_count
        )
        coefficients = curves @ self._design_pseudoinverse.T
        a_value = coefficients[..., 0]
        b_value = coefficients[..., 1]
        c_value = coefficients[..., 2]
        determinant = a_value * c_value - b_value**2
        coefficient_physical = (
            np.all(np.isfinite(coefficients), axis=2)
            & (a_value > 0.0)
            & (c_value > 0.0)
            & (determinant > 0.0)
        )
        curve_physical = np.all(np.isfinite(curves) & (curves > 0.0), axis=2)
        candidate_physical = coefficient_physical & curve_physical
        emittances = np.full(candidate_physical.shape, np.nan, dtype=float)
        emittances[candidate_physical] = beta_gamma(
            self.pool.energy_mev
        ) * np.sqrt(determinant[candidate_physical])

        any_physical = np.any(candidate_physical, axis=1)
        categories = np.full(len(samples), self.invalid_category, dtype=int)
        masked = np.where(candidate_physical, emittances, np.inf)
        categories[any_physical] = np.argmin(masked[any_physical], axis=1)
        return AlgorithmSamples(
            categories=categories,
            emittances_mm_mrad=emittances,
            candidate_physical=candidate_physical,
            all_candidates_physical=np.all(candidate_physical, axis=1),
        )

    def summarize(
        self,
        state_samples: np.ndarray,
        *,
        recommendation_invalid_threshold: float,
    ) -> OutputSummary:
        evaluated = self.evaluate(state_samples)
        counts = np.bincount(
            evaluated.categories, minlength=self.category_count
        ).astype(float)
        probabilities = counts / len(evaluated.categories)
        invalid_probability = float(probabilities[self.invalid_category])
        recommended = None
        if invalid_probability < recommendation_invalid_threshold:
            recommended = int(np.argmax(probabilities[: self.candidate_count]))
        return OutputSummary(
            recommended_candidate=recommended,
            best_probabilities=probabilities[: self.candidate_count],
            invalid_output_probability=invalid_probability,
            all_candidates_physical_probability=float(
                np.mean(evaluated.all_candidates_physical)
            ),
            candidate_physical_probabilities=np.mean(
                evaluated.candidate_physical, axis=0
            ),
            entropy_nats=categorical_entropy(
                evaluated.categories, self.category_count
            ),
        )

