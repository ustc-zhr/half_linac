from __future__ import annotations

import time

import numpy as np

from half_linac.src.shared.beam_diagnostics import detect_beam_presence

from .spectrum_profile import SpectrumProfileError, fit_projection_profile, project_image_profiles


class RFImageAcquisition:
    """RF scan-local FLAG image reader used by tuning, point samples, and backgrounds."""

    def __init__(
        self,
        image_pv,
        image_shape,
        pixel_width_mm,
        *,
        background=None,
        roi=None,
        flip_y=False,
        beam_presence_sigma=6.0,
        beam_presence_min_area_px=50,
    ):
        self.image_pv = image_pv
        self.image_shape = tuple(image_shape)
        self.pixel_width_mm = float(pixel_width_mm)
        self.background = None if background is None else np.asarray(background, dtype=float)
        self.roi = roi
        self.flip_y = bool(flip_y)
        self.beam_presence_sigma = float(beam_presence_sigma)
        self.beam_presence_min_area_px = int(beam_presence_min_area_px)
        expected_shape = (self.image_shape[1], self.image_shape[0])
        if self.background is not None and self.background.shape != expected_shape:
            raise ValueError(f"Background shape {self.background.shape} does not match image shape {expected_shape}.")
        if not np.isfinite(self.beam_presence_sigma) or self.beam_presence_sigma <= 0:
            raise ValueError("Beam-presence sigma threshold must be positive and finite.")
        if self.beam_presence_min_area_px < 1:
            raise ValueError("Beam-presence minimum area must be at least one pixel.")

    def read_raw(self):
        raw = self.image_pv.get()
        if raw is None:
            raise SpectrumProfileError("Flag image PV returned no data.")
        try:
            image = np.asarray(raw, dtype=float).reshape(
                self.image_shape[1], self.image_shape[0]
            )
            return np.flipud(image) if self.flip_y else image
        except ValueError as exc:
            raise SpectrumProfileError("Flag image PV shape does not match configured geometry.") from exc

    def read_analysis(self):
        image = self.read_raw()
        if self.background is not None:
            image = np.maximum(image - self.background, 0)
        return image

    def sample_profile(
        self,
        *,
        samples,
        min_valid,
        interval_s,
        fit_method,
        cancel_requested=None,
        allow_direct_fallback=False,
        min_fit_r_squared=0.0,
    ):
        raw_images, fits, strengths, presence_results = [], [], [], []
        for index in range(int(samples)):
            if cancel_requested and cancel_requested():
                raise InterruptedError("Image sampling stopped by operator.")
            raw = self.read_raw()
            try:
                analysis_image = (
                    np.maximum(raw - self.background, 0)
                    if self.background is not None
                    else raw
                )
                presence = detect_beam_presence(
                    analysis_image,
                    roi=self.roi,
                    sigma_threshold=self.beam_presence_sigma,
                    min_area_px=self.beam_presence_min_area_px,
                )
                if not presence.has_beam:
                    raise SpectrumProfileError(
                        "No significant beam region was detected."
                    )
                projection = project_image_profiles(
                    analysis_image,
                    self.pixel_width_mm,
                    self.roi,
                )
                profile = fit_projection_profile(
                    projection.x_mm,
                    projection.density_x,
                    fit_method,
                    allow_direct_fallback=allow_direct_fallback,
                )
                if (
                    profile.r_squared is None
                    or profile.r_squared < float(min_fit_r_squared)
                ):
                    raise SpectrumProfileError(
                        f"Gaussian fit R2 {profile.r_squared!r} is below "
                        f"{float(min_fit_r_squared):.3f}."
                    )
            except SpectrumProfileError:
                pass
            else:
                raw_images.append(raw)
                fits.append(profile)
                strengths.append(float(presence.brightness))
                presence_results.append(presence)
            if index + 1 < int(samples) and interval_s > 0:
                deadline = time.monotonic() + float(interval_s)
                while time.monotonic() < deadline:
                    if cancel_requested and cancel_requested():
                        raise InterruptedError("Image sampling stopped by operator.")
                    time.sleep(min(0.05, max(deadline - time.monotonic(), 0)))
        if len(fits) < int(min_valid):
            return None
        centers = [float(item.center_mm) for item in fits]
        center_spread_mm = float(np.ptp(centers))
        presence_diagnostics = presence_results[-1].diagnostics()
        return {
            "raw_image": np.mean(raw_images, axis=0),
            "center_mm": float(np.median(centers)),
            "brightness": float(np.median(strengths)),
            "fit_method": fits[0].method,
            "fit_r_squared": (float(np.median([item.r_squared for item in fits if item.r_squared is not None])) if any(item.r_squared is not None for item in fits) else None),
            "valid_frames": len(fits),
            "center_spread_mm": center_spread_mm,
            **presence_diagnostics,
        }
