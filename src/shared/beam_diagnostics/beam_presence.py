from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from skimage import measure

from .roi import ImageROI, crop_image


@dataclass(frozen=True)
class BeamPresenceResult:
    """Single-frame beam-presence result from a significant image region."""

    has_beam: bool
    brightness: float = 0.0
    center_x_pixel: float | None = None
    center_y_pixel: float | None = None
    threshold: float | None = None
    area_px: int | None = None
    major_axis_px: float | None = None
    minor_axis_px: float | None = None
    aspect_ratio: float | None = None
    orientation_rad: float | None = None

    def diagnostics(self) -> dict[str, float | int | None]:
        return {
            "beam_threshold": self.threshold,
            "beam_area_px": self.area_px,
            "beam_major_axis_px": self.major_axis_px,
            "beam_minor_axis_px": self.minor_axis_px,
            "beam_aspect_ratio": self.aspect_ratio,
            "beam_orientation_rad": self.orientation_rad,
        }


def detect_beam_presence(
    image: Any,
    *,
    roi: ImageROI | None = None,
    sigma_threshold: float = 6.0,
    min_area_px: int = 50,
) -> BeamPresenceResult:
    """Detect the brightest significant connected region in one image.

    Shape measurements are returned for diagnostics only. In particular, an
    elongated energy-spectrum spot is not rejected by an aspect-ratio limit.
    """

    sigma_threshold = float(sigma_threshold)
    min_area_px = int(min_area_px)
    if not np.isfinite(sigma_threshold) or sigma_threshold <= 0:
        raise ValueError("sigma_threshold must be positive and finite.")
    if min_area_px < 1:
        raise ValueError("min_area_px must be at least 1.")

    selected_image, selected_roi, _warnings = crop_image(image, roi)
    selected_image = np.asarray(selected_image, dtype=float)
    finite = np.isfinite(selected_image)
    if not finite.any():
        return BeamPresenceResult(False)

    background_values = selected_image[finite]
    threshold = float(
        np.mean(background_values) + sigma_threshold * np.std(background_values)
    )
    binary = finite & (selected_image > threshold)
    labels = measure.label(binary)
    regions = [region for region in measure.regionprops(labels) if region.area >= min_area_px]
    if not regions:
        return BeamPresenceResult(False, threshold=threshold)

    def integrated_brightness(region) -> float:
        return float(np.sum(selected_image[labels == region.label]))

    region = max(regions, key=integrated_brightness)
    region_mask = labels == region.label
    region_y, region_x = np.nonzero(region_mask)
    weights = selected_image[region_y, region_x]
    weight_sum = float(np.sum(weights))
    if not np.isfinite(weight_sum) or weight_sum <= 0:
        return BeamPresenceResult(False, threshold=threshold)

    major_axis = getattr(region, "axis_major_length", None)
    minor_axis = getattr(region, "axis_minor_length", None)
    if major_axis is None or minor_axis is None:
        major_axis = region.major_axis_length
        minor_axis = region.minor_axis_length
    major_axis = float(major_axis)
    minor_axis = float(minor_axis)
    aspect_ratio = major_axis / max(minor_axis, np.finfo(float).eps)

    return BeamPresenceResult(
        True,
        brightness=weight_sum,
        center_x_pixel=float(np.average(region_x, weights=weights) + selected_roi.x),
        center_y_pixel=float(np.average(region_y, weights=weights) + selected_roi.y),
        threshold=threshold,
        area_px=int(region.area),
        major_axis_px=major_axis,
        minor_axis_px=minor_axis,
        aspect_ratio=float(aspect_ratio),
        orientation_rad=float(region.orientation),
    )
