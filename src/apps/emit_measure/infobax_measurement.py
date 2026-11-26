"""Calibratable RMS-variance observations for VM particle-count images."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math

import numpy as np


@dataclass(frozen=True)
class RmsVarianceConfig:
    minimum_count: int = 1000
    minimum_sigma_pixels: float = 1.0
    maximum_edge_fraction: float = 0.001
    noise_scale: float = 1.0

    def __post_init__(self):
        if type(self.minimum_count) is not int or self.minimum_count < 4:
            raise ValueError("minimum_count must be an integer of at least 4")
        if not math.isfinite(self.minimum_sigma_pixels) or self.minimum_sigma_pixels < 1:
            raise ValueError("minimum_sigma_pixels must be finite and at least 1")
        if not math.isfinite(self.maximum_edge_fraction) or not 0 <= self.maximum_edge_fraction < 1:
            raise ValueError("maximum_edge_fraction must lie in [0, 1)")
        if not math.isfinite(self.noise_scale) or self.noise_scale <= 0:
            raise ValueError("noise_scale must be positive and finite")


@dataclass(frozen=True)
class RmsVarianceResult:
    variance_mm2: float
    noise_std_mm2: float
    count: int
    centroid_mm: float
    pixel_variance_mm2: float
    quantization_variance_mm2: float
    sigma_pixels: float
    edge_fraction: float
    noise_scale: float

    def to_dict(self):
        return asdict(self)


def measure_count_variance(raw, *, pixel_shape, pixel_width_mm, plane="x", config=None):
    """Estimate a variance and its sampling uncertainty from one count image.

    Subtracting d²/12 is a quantization approximation, validated separately
    over the allowed resolution range. No nonpositive result is clipped.
    The uncertainty assumes independently sampled particles, not camera ADUs.
    """
    selected = config or RmsVarianceConfig()
    if not isinstance(selected, RmsVarianceConfig):
        raise TypeError("config must be an RmsVarianceConfig")
    if plane not in {"x", "y"}:
        raise ValueError("plane must be 'x' or 'y'")
    pitch = float(pixel_width_mm)
    if not math.isfinite(pitch) or pitch <= 0:
        raise ValueError("pixel width must be positive and finite")
    width, height = pixel_shape
    if type(width) is not int or type(height) is not int or min(width, height) < 3:
        raise ValueError("pixel_shape must contain integer width/height of at least 3")
    image = np.asarray(raw, dtype=float)
    if image.size != width*height:
        raise ValueError("image size does not match pixel_shape")
    if not np.all(np.isfinite(image)) or np.any(image < 0):
        raise ValueError("VM particle counts must be finite and nonnegative")
    if np.any(image != np.floor(image)):
        raise ValueError("RMS count variance requires integer particle counts, not camera ADUs")
    image = image.reshape(height, width)
    count = int(image.sum())
    if count < selected.minimum_count:
        raise ValueError("particle count is below the configured minimum")
    projection = image.sum(axis=0 if plane == "x" else 1)
    positions = (np.arange(len(projection)) + 0.5 - len(projection)/2) * pitch
    centroid = float(positions @ projection / count)
    centered = positions - centroid
    second = float((centered**2) @ projection / count)
    fourth = float((centered**4) @ projection / count)
    sigma_pixels = math.sqrt(second) / pitch
    edge_fraction = float((projection[0] + projection[-1]) / count)
    if sigma_pixels < selected.minimum_sigma_pixels:
        raise ValueError("beam is underresolved for the configured RMS quantization model")
    if edge_fraction > selected.maximum_edge_fraction:
        raise ValueError("image edge population exceeds the configured limit")
    correction = pitch**2 / 12
    variance = second - correction
    sampling_variance = (fourth - (count-3)/(count-1) * second**2) / count
    if not math.isfinite(variance) or variance <= 0 or sampling_variance <= 0:
        raise ValueError("corrected variance or sampling uncertainty is nonpositive")
    return RmsVarianceResult(
        variance, selected.noise_scale * math.sqrt(sampling_variance), count, centroid, second,
        correction, sigma_pixels, edge_fraction, selected.noise_scale,
    )
