"""Exact finite-pool Gaussian posterior for the audited M0 baseline."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np
from scipy.linalg import cho_factor, cho_solve

from .types import FinitePoolSpec, InfoBAXConfig, NumericalFailure, PointObservation


def matern52(
    x1: np.ndarray, x2: np.ndarray, length_scales: np.ndarray
) -> np.ndarray:
    left = np.asarray(x1, dtype=float)
    right = np.asarray(x2, dtype=float)
    lengths = np.asarray(length_scales, dtype=float)
    scaled = (left[:, None, :] - right[None, :, :]) / lengths
    radius = np.sqrt(np.sum(np.square(scaled), axis=2))
    root5 = math.sqrt(5.0)
    return (1.0 + root5 * radius + 5.0 * radius**2 / 3.0) * np.exp(
        -root5 * radius
    )


def stabilized_covariance(covariance: np.ndarray) -> np.ndarray:
    matrix = 0.5 * (np.asarray(covariance) + np.asarray(covariance).T)
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1]:
        raise NumericalFailure("posterior covariance is not square")
    if not np.all(np.isfinite(matrix)):
        raise NumericalFailure("posterior covariance contains non-finite values")
    try:
        eigenvalues, eigenvectors = np.linalg.eigh(matrix)
    except np.linalg.LinAlgError as error:
        raise NumericalFailure("posterior covariance eigendecomposition failed") from error
    scale = max(float(np.max(np.abs(eigenvalues))), 1.0)
    eigenvalues = np.maximum(eigenvalues, scale * 1e-12)
    stabilized = (eigenvectors * eigenvalues) @ eigenvectors.T
    if not np.all(np.isfinite(stabilized)):
        raise NumericalFailure("stabilized posterior covariance is non-finite")
    return stabilized


@dataclass(frozen=True)
class GaussianStatePosterior:
    """Joint latent response state and every finite-pool observation vector."""

    mean: np.ndarray
    covariance: np.ndarray
    observation_matrix: np.ndarray
    observation_noise_std: np.ndarray

    @property
    def query_count(self) -> int:
        return int(self.observation_matrix.shape[0])

    def sample(self, count: int, seed: int) -> np.ndarray:
        if count <= 0:
            raise ValueError("sample count must be positive")
        try:
            return np.random.default_rng(seed).multivariate_normal(
                self.mean,
                stabilized_covariance(self.covariance),
                size=int(count),
            )
        except (ValueError, np.linalg.LinAlgError) as error:
            raise NumericalFailure("joint posterior sampling failed") from error


class FinitePoolM0:
    """Fixed-length-scale Matérn-5/2 GP over workpoint and scan coordinates."""

    def __init__(self, pool: FinitePoolSpec, config: InfoBAXConfig) -> None:
        self.pool = pool
        self.config = config

    def fit(self, observations: Sequence[PointObservation]) -> GaussianStatePosterior:
        if not observations:
            raise ValueError("at least one observation is required")
        indices = np.asarray([item.query_index for item in observations], dtype=int)
        target = np.asarray([item.variance_mm2 for item in observations], dtype=float)
        noise = np.asarray([item.noise_std_mm2 for item in observations], dtype=float)
        if len(np.unique(indices)) != len(indices):
            raise ValueError("duplicate observations are not allowed")
        if np.any(indices < 0) or np.any(indices >= self.pool.query_count):
            raise IndexError("observation index outside finite pool")

        coordinates = self.pool.query_coordinates
        target_mean = float(np.median(target))
        target_scale = max(
            float(np.std(target)),
            0.20 * abs(target_mean),
            float(np.median(self.pool.observation_noise_std_mm2)),
        )
        lengths = np.asarray(
            [self.config.candidate_length_scale] * self.pool.candidate_dimension
            + [self.config.scan_length_scale]
        )
        covariance = target_scale**2 * matern52(coordinates, coordinates, lengths)
        mean = np.full(self.pool.query_count, target_mean)
        observation_matrix = np.eye(self.pool.query_count)
        selected = observation_matrix[indices]
        likelihood = selected @ covariance @ selected.T
        likelihood += np.diag(np.square(noise) + 1e-10)
        try:
            factor = cho_factor(likelihood, lower=True, check_finite=True)
            residual = target - selected @ mean
            cross = covariance @ selected.T
            posterior_mean = mean + cross @ cho_solve(
                factor, residual, check_finite=True
            )
            posterior_covariance = covariance - cross @ cho_solve(
                factor, cross.T, check_finite=True
            )
        except (ValueError, np.linalg.LinAlgError) as error:
            raise NumericalFailure("M0 posterior factorization failed") from error
        if not np.all(np.isfinite(posterior_mean)):
            raise NumericalFailure("M0 posterior mean is non-finite")
        return GaussianStatePosterior(
            mean=np.asarray(posterior_mean),
            covariance=stabilized_covariance(posterior_covariance),
            observation_matrix=observation_matrix,
            observation_noise_std=np.asarray(
                self.pool.observation_noise_std_mm2, dtype=float
            ),
        )

