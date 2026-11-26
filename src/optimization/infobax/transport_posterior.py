"""Experimental transport-consistent moment GP; opt-in through InfoBAXCore.

For A = Q R, each candidate's response curve is y = Q u and its three
beam coefficients are R^-1 u.  The GP lives on u, so transport consistency
holds for every draw.  Beam-matrix positivity is deliberately not imposed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
from scipy.linalg import cho_factor, cho_solve

from .posterior import matern52, stabilized_covariance
from .types import FinitePoolSpec, InfoBAXConfig, NumericalFailure, PointObservation


def _readonly(value):
    result = np.array(value, dtype=float, copy=True)
    result.setflags(write=False)
    return result


@dataclass(frozen=True)
class TransportMomentPosterior:
    """Response-space interface with sampling performed in moment coordinates."""

    mean: np.ndarray
    covariance: np.ndarray
    observation_matrix: np.ndarray
    observation_noise_std: np.ndarray
    latent_mean: np.ndarray
    latent_covariance: np.ndarray
    response_matrix: np.ndarray
    triangular_design: np.ndarray

    @property
    def query_count(self):
        return len(self.mean)

    @property
    def latent_dimension(self):
        return len(self.latent_mean)

    @property
    def coefficient_mean(self):
        return np.linalg.solve(self.triangular_design, self.latent_mean.reshape(-1, 3).T).T

    def sample(self, count: int, seed: int):
        if type(count) is not int or count <= 0:
            raise ValueError("sample count must be a positive integer")
        try:
            latent = np.random.default_rng(seed).multivariate_normal(
                self.latent_mean, stabilized_covariance(self.latent_covariance), size=count)
        except (ValueError, np.linalg.LinAlgError) as error:
            raise NumericalFailure("transport moment posterior sampling failed") from error
        # No response-space eigenvalue floor, clipping, rejection or projection.
        return latent @ self.response_matrix.T


class TransportMomentGP:
    """Fixed candidate kernel and three latent coefficients per workpoint.

    The amplitude and median use the same observed-only formulas as M0.
    Projecting its constant prior mean into the design span defines the mean
    of this model, rather than changing any fitted response or posterior draw.
    The n_scan/3 prior factor matches average response variance to M0's scale.
    """

    def __init__(self, pool: FinitePoolSpec, config: InfoBAXConfig):
        if not isinstance(pool, FinitePoolSpec) or not isinstance(config, InfoBAXConfig):
            raise TypeError("a finite pool and InfoBAXConfig are required")
        self.pool, self.config = pool, config
        self.basis, self.triangular = np.linalg.qr(pool.design_matrix, mode="reduced")
        if self.basis.shape != (pool.scan_count, 3) or np.linalg.matrix_rank(self.triangular) != 3:
            raise ValueError("transport design must have rank three")
        self.response_matrix = np.kron(np.eye(pool.candidate_count), self.basis)

    def fit(self, observations: Sequence[PointObservation]):
        if not observations or not all(isinstance(row, PointObservation) for row in observations):
            raise ValueError("at least one PointObservation is required")
        indices = np.array([row.query_index for row in observations], dtype=int)
        if len(set(indices.tolist())) != len(indices):
            raise ValueError("duplicate observations are not allowed")
        if np.any(indices < 0) or np.any(indices >= self.pool.query_count):
            raise IndexError("observation index outside finite pool")
        target = np.array([row.variance_mm2 for row in observations])
        noise = np.array([row.noise_std_mm2 for row in observations])
        median = float(np.median(target))
        scale = max(float(target.std()), 0.2*abs(median), float(np.median(self.pool.observation_noise_std_mm2)))
        lengths = np.full(self.pool.candidate_dimension, self.config.candidate_length_scale)
        candidate_kernel = matern52(self.pool.candidate_coordinates, self.pool.candidate_coordinates, lengths)
        prior_covariance = scale**2*(self.pool.scan_count/3)*np.kron(candidate_kernel, np.eye(3))
        prior_mean = np.tile(self.basis.T @ np.full(self.pool.scan_count, median), self.pool.candidate_count)
        selected = self.response_matrix[indices]
        cross = prior_covariance @ selected.T
        likelihood = selected @ cross + np.diag(noise**2+1e-10)
        try:
            factor = cho_factor(likelihood, lower=True, check_finite=True)
            mean = prior_mean + cross @ cho_solve(factor, target-selected @ prior_mean, check_finite=True)
            covariance = prior_covariance-cross @ cho_solve(factor, cross.T, check_finite=True)
            covariance = stabilized_covariance(covariance)
        except (ValueError, np.linalg.LinAlgError) as error:
            raise NumericalFailure("transport moment posterior conditioning failed") from error
        if not np.all(np.isfinite(mean)):
            raise NumericalFailure("transport moment posterior mean is non-finite")
        response_mean = self.response_matrix @ mean
        response_covariance = self.response_matrix @ covariance @ self.response_matrix.T
        response_covariance = (response_covariance+response_covariance.T)/2
        return TransportMomentPosterior(
            mean=_readonly(response_mean), covariance=_readonly(response_covariance),
            observation_matrix=_readonly(np.eye(self.pool.query_count)),
            observation_noise_std=_readonly(self.pool.observation_noise_std_mm2),
            latent_mean=_readonly(mean), latent_covariance=_readonly(covariance),
            response_matrix=_readonly(self.response_matrix), triangular_design=_readonly(self.triangular))
