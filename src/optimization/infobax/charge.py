"""ICT1 observations and their independent workpoint posterior."""

from dataclasses import dataclass
import math

import numpy as np
from scipy.linalg import cho_factor, cho_solve

from .posterior import GaussianStatePosterior, matern52, stabilized_covariance
from .types import InsufficientDataError, NumericalFailure


@dataclass(frozen=True)
class ICT1Config:
    baseline_nc: float
    retention: float
    query_noise_std: float
    prior_std: float = 0.10
    length_scale: float = 0.25
    noise_floor: float = 0.001

    def __post_init__(self):
        for name in ("baseline_nc", "retention", "query_noise_std", "prior_std", "length_scale", "noise_floor"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"ICT1 {name} must be finite and positive")
            object.__setattr__(self, name, value)
        if self.retention > 1 or self.query_noise_std < self.noise_floor:
            raise ValueError("Invalid ICT1 retention or query noise")

    @property
    def minimum_nc(self):
        return self.baseline_nc * self.retention


@dataclass(frozen=True)
class ICT1Observation:
    window_id: str
    candidate_index: int
    charge_nc: float
    standard_error_nc: float

    def __post_init__(self):
        if not isinstance(self.window_id, str) or not self.window_id:
            raise ValueError("An ICT1 window ID is required")
        if type(self.candidate_index) is not int or self.candidate_index < 0:
            raise ValueError("ICT1 candidate_index must be a non-negative integer")
        charge, error = float(self.charge_nc), float(self.standard_error_nc)
        if not math.isfinite(charge) or charge <= 0 or not math.isfinite(error) or error < 0:
            raise ValueError("Invalid ICT1 charge or measurement standard error")
        object.__setattr__(self, "charge_nc", charge)
        object.__setattr__(self, "standard_error_nc", error)


def fit_ict1_posterior(pool, config, observations):
    if not observations:
        raise InsufficientDataError("ICT1 posterior requires an observation")
    count = pool.candidate_count
    coordinates = pool.candidate_coordinates
    lengths = np.full(pool.candidate_dimension, config.length_scale)
    covariance = config.prior_std**2 * matern52(coordinates, coordinates, lengths)
    indices = np.asarray([item.candidate_index for item in observations])
    charges = np.asarray([item.charge_nc for item in observations]) / config.baseline_nc
    errors = np.asarray([item.standard_error_nc for item in observations]) / config.baseline_nc
    noise = np.maximum(errors, config.noise_floor)
    cross = covariance[:, indices]
    likelihood = covariance[np.ix_(indices, indices)] + np.diag(noise**2)
    try:
        factor = cho_factor(likelihood, lower=True)
        mean = (1.0 - config.retention) + cross @ cho_solve(factor, charges - 1.0)
        conditional = covariance - cross @ cho_solve(factor, cross.T)
    except (ValueError, np.linalg.LinAlgError) as error:
        raise NumericalFailure("ICT1 posterior factorization failed") from error
    if not np.all(np.isfinite(mean)):
        raise NumericalFailure("ICT1 posterior mean is non-finite")
    return GaussianStatePosterior(mean, stabilized_covariance(conditional), np.eye(count),
                                  np.full(count, config.query_noise_std))
