"""Numerically audited mutual-information acquisition for a finite pool."""

from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np
from scipy.integrate import quad
from scipy.special import ndtr, ndtri

from .posterior import GaussianStatePosterior
from .types import EIGDiagnostic, InfoBAXConfig, NumericalFailure


def entropy_from_probabilities(probabilities: np.ndarray) -> float:
    values = np.asarray(probabilities, dtype=float)
    if values.ndim != 1 or np.any(values < 0.0) or not np.all(np.isfinite(values)):
        raise NumericalFailure("invalid categorical probabilities")
    total = float(np.sum(values))
    if not math.isclose(total, 1.0, rel_tol=1e-9, abs_tol=1e-12):
        raise NumericalFailure(
            f"categorical probabilities sum to {total}, not one"
        )
    positive = values[values > 0.0]
    return -float(np.sum(positive * np.log(positive)))


def categorical_probabilities(
    categories: np.ndarray, category_count: int
) -> np.ndarray:
    values = np.asarray(categories, dtype=int)
    if values.ndim != 1 or len(values) == 0:
        raise NumericalFailure("at least one category sample is required")
    if np.any(values < 0) or np.any(values >= category_count):
        raise NumericalFailure("category outside declared output space")
    return np.bincount(values, minlength=category_count).astype(float) / len(values)


def binary_sign_information_quadrature(observation_noise_std: float) -> float:
    """Independent one-dimensional reference used by the numerical tests."""

    noise = float(observation_noise_std)
    if noise <= 0.0 or not math.isfinite(noise):
        raise ValueError("observation noise must be positive")

    def integrand(standard_normal: float) -> float:
        probability = float(ndtr(standard_normal / noise))
        if probability <= 0.0 or probability >= 1.0:
            return 0.0
        entropy = -probability * math.log(probability) - (
            1.0 - probability
        ) * math.log1p(-probability)
        density = math.exp(-0.5 * standard_normal**2) / math.sqrt(2.0 * math.pi)
        return entropy * density

    points = [multiple * noise for multiple in range(-6, 7)]
    expectation = quad(
        integrand, -12.0, 12.0, points=points, epsabs=1e-13, limit=500
    )[0]
    return math.log(2.0) - float(expectation)


class InfoBAXEstimator:
    """Estimate information between a scalar query and categorical output."""

    def __init__(
        self,
        posterior: GaussianStatePosterior,
        algorithm: Any,
        config: InfoBAXConfig,
        *,
        seed: int,
    ) -> None:
        self.posterior = posterior
        self.algorithm = algorithm
        self.config = config
        self.seed = int(seed)
        probabilities = (
            np.arange(config.outer_fantasies) + 0.5
        ) / config.outer_fantasies
        self._fantasy_standard_normal = ndtri(probabilities)
        self._fantasy_weights = np.full(
            config.outer_fantasies, 1.0 / config.outer_fantasies
        )

        noise_rng = np.random.default_rng(self.seed + 2)
        self._base_state_samples = posterior.sample(config.inner_samples, self.seed)
        self._base_observation_noise = noise_rng.normal(
            size=(config.inner_samples, posterior.query_count)
        )
        direct_categories = algorithm.evaluate(
            posterior.sample(config.direct_prior_samples, self.seed + 1)
        ).categories
        self.direct_prior_probabilities = categorical_probabilities(
            direct_categories, algorithm.category_count
        )
        self.direct_prior_entropy = entropy_from_probabilities(
            self.direct_prior_probabilities
        )

    def _information_from_table(
        self, probability_table: np.ndarray
    ) -> tuple[float, float, float, np.ndarray]:
        table = np.asarray(probability_table, dtype=float)
        expected_shape = (
            self.config.outer_fantasies,
            self.algorithm.category_count,
        )
        if table.shape != expected_shape:
            raise NumericalFailure("conditional probability table has wrong shape")
        marginal = self._fantasy_weights @ table
        marginal_total = float(np.sum(marginal))
        if marginal_total <= 0.0 or not math.isfinite(marginal_total):
            raise NumericalFailure("conditional probability mixture is invalid")
        marginal /= marginal_total
        prior_entropy = entropy_from_probabilities(marginal)
        conditional_entropies = np.asarray(
            [entropy_from_probabilities(row) for row in table]
        )
        conditional_entropy = float(
            self._fantasy_weights @ conditional_entropies
        )
        return (
            prior_entropy - conditional_entropy,
            prior_entropy,
            conditional_entropy,
            marginal,
        )

    def estimate(self, query_index: int) -> EIGDiagnostic:
        index = int(query_index)
        if index < 0 or index >= self.posterior.query_count:
            raise IndexError("query index outside posterior pool")
        h_vector = self.posterior.observation_matrix[index]
        covariance = self.posterior.covariance
        state_cross = covariance @ h_vector
        latent_variance = float(h_vector @ state_cross)
        observation_noise = float(self.posterior.observation_noise_std[index])
        observation_variance = latent_variance + observation_noise**2
        if observation_variance <= 0.0 or not math.isfinite(observation_variance):
            raise NumericalFailure("non-positive fantasy observation variance")
        observation_mean = float(h_vector @ self.posterior.mean)
        gain = state_cross / observation_variance
        base_noisy_observation = (
            self._base_state_samples @ h_vector
            + observation_noise * self._base_observation_noise[:, index]
        )

        category_rows: list[np.ndarray] = []
        probability_rows: list[np.ndarray] = []
        for standard_value in self._fantasy_standard_normal:
            fantasy = observation_mean + math.sqrt(
                observation_variance
            ) * standard_value
            samples = self._base_state_samples + np.outer(
                fantasy - base_noisy_observation, gain
            )
            categories = self.algorithm.evaluate(samples).categories
            category_rows.append(categories)
            probability_rows.append(
                categorical_probabilities(
                    categories, self.algorithm.category_count
                )
            )
        raw_eig, prior_entropy, entropy_mean, marginal = self._information_from_table(
            np.vstack(probability_rows)
        )

        batch_estimates = []
        for batch_indices in np.array_split(
            np.arange(self.config.inner_samples), self.config.batch_count
        ):
            batch_table = np.vstack(
                [
                    categorical_probabilities(
                        row[batch_indices], self.algorithm.category_count
                    )
                    for row in category_rows
                ]
            )
            batch_estimates.append(self._information_from_table(batch_table)[0])
        standard_error = float(
            np.std(batch_estimates, ddof=1) / math.sqrt(len(batch_estimates))
        )
        prior_l1 = float(
            np.sum(np.abs(marginal - self.direct_prior_probabilities))
        )
        return EIGDiagnostic(
            query_index=index,
            raw_eig_nats=float(raw_eig),
            eig_standard_error_nats=standard_error,
            prior_entropy_nats=prior_entropy,
            posterior_entropy_mean_nats=entropy_mean,
            direct_prior_entropy_nats=self.direct_prior_entropy,
            prior_probability_l1=prior_l1,
            negative_estimate=bool(raw_eig < 0.0),
        )

    def select(
        self, available_indices: Sequence[int]
    ) -> tuple[EIGDiagnostic, tuple[EIGDiagnostic, ...]]:
        available = np.asarray(available_indices, dtype=int)
        if available.ndim != 1 or len(available) == 0:
            raise ValueError("no available query")
        if len(np.unique(available)) != len(available):
            raise ValueError("available queries must be unique")
        results = tuple(self.estimate(int(index)) for index in np.sort(available))
        selected = max(results, key=lambda result: result.raw_eig_nats)
        return selected, results

