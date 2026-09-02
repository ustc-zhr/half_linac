"""Shared screen/profile measurement contract.

The implementation is deliberately independent of EPICS and Qt. Applications
provide a callable that returns the latest image and may supply their existing
profile projection/fitting functions.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

import numpy as np

from .models import EnergyObservation


class BeamMeasurement(Protocol):
    """Measurement surface required by tuning stages."""

    def measure(self, actuator_value: float, **kwargs) -> EnergyObservation | None:
        """Return a stable observation or ``None`` when no valid beam exists."""


class ScreenProfileMeasurement:
    """Measure stable beam brightness and fitted horizontal center."""

    def __init__(
        self,
        read_image: Callable[[], Any],
        *,
        pixel_width_mm: float,
        x_reference_mm: float = 0.0,
        roi=None,
        frame_interval_s: float = 0.0,
        fit_method: str = "Gauss fit",
        allow_direct_fallback: bool = False,
        min_fit_r_squared: float | None = None,
        sleep: Callable[[float], None] | None = None,
        project_profiles: Callable[..., Any],
        fit_profile: Callable[..., Any],
    ):
        self.read_image = read_image
        self.pixel_width_mm = float(pixel_width_mm)
        self.x_reference_mm = float(x_reference_mm)
        self.roi = roi
        self.frame_interval_s = float(frame_interval_s)
        self.fit_method = str(fit_method)
        self.allow_direct_fallback = bool(allow_direct_fallback)
        self.min_fit_r_squared = min_fit_r_squared
        self.sleep = sleep or (lambda _duration: None)
        self.project_profiles = project_profiles
        self.fit_profile = fit_profile
        if self.pixel_width_mm <= 0:
            raise ValueError("pixel_width_mm must be positive.")
        if self.frame_interval_s < 0:
            raise ValueError("frame_interval_s must not be negative.")
        if min_fit_r_squared is not None and not 0 <= float(min_fit_r_squared) <= 1:
            raise ValueError("min_fit_r_squared must be in [0, 1].")

    def measure(
        self,
        actuator_value: float,
        *,
        samples: int = 3,
        min_valid: int = 2,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> EnergyObservation | None:
        samples = int(samples)
        min_valid = int(min_valid)
        if samples < 1 or not 1 <= min_valid <= samples:
            raise ValueError("min_valid must be between 1 and samples.")

        fits = []
        strengths = []
        for index in range(samples):
            if cancel_requested and cancel_requested():
                raise InterruptedError("Beam measurement stopped by operator.")
            image = np.asarray(self.read_image(), dtype=float)
            projection = self.project_profiles(image, self.pixel_width_mm, self.roi)
            try:
                profile = self.fit_profile(
                    projection.x_mm,
                    projection.density_x,
                    self.fit_method,
                    allow_direct_fallback=self.allow_direct_fallback,
                )
                if (
                    self.min_fit_r_squared is not None
                    and (
                        profile.r_squared is None
                        or profile.r_squared < float(self.min_fit_r_squared)
                    )
                ):
                    raise ValueError(
                        f"Profile fit R2 {profile.r_squared!r} is below "
                        f"{float(self.min_fit_r_squared):.3f}."
                    )
            except (ValueError, RuntimeError):
                profile = None
            if profile is not None:
                fits.append(profile)
                strengths.append(float(np.sum(projection.density_x)))
            if index + 1 < samples:
                self.sleep(self.frame_interval_s)

        if len(fits) < min_valid:
            return None
        centers = [float(profile.center_mm) for profile in fits]
        qualities = [
            float(profile.r_squared)
            for profile in fits
            if profile.r_squared is not None
        ]
        center_mm = float(np.median(centers))
        return EnergyObservation(
            actuator_value=float(actuator_value),
            has_beam=True,
            brightness=float(np.median(strengths)),
            center_mm=center_mm,
            center_offset_mm=center_mm - self.x_reference_mm,
            valid_frames=len(fits),
            total_frames=samples,
            fit_method=fits[0].method,
            fit_r_squared=float(np.median(qualities)) if qualities else None,
            diagnostics={"center_spread_mm": float(np.ptp(centers))},
        )
