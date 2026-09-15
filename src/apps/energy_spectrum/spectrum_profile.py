from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import curve_fit
from half_linac.src.shared.beam_diagnostics.roi import ImageROI, crop_image


class SpectrumProfileError(ValueError):
    """Raised when an ESA image cannot provide a trustworthy x profile."""


@dataclass(frozen=True)
class ProjectedProfiles:
    x_mm: np.ndarray
    y_mm: np.ndarray
    image: np.ndarray
    density_x: np.ndarray
    density_y: np.ndarray


@dataclass(frozen=True)
class ProfileFit:
    center_mm: float
    sigma_mm: float
    normalized_density: np.ndarray
    fitted_density: np.ndarray
    method: str
    r_squared: float | None
    fallback_error: str | None = None
    quality_warning: str | None = None


def project_image_profiles(image, pixel_width_mm, roi: ImageROI | None = None):
    """Build the same cropped x/y projections used by the Energy Spectrum GUI."""
    image = np.asarray(image, dtype=float)
    if image.ndim != 2 or min(image.shape) < 3:
        raise SpectrumProfileError("ESA image must be a two-dimensional array of at least 3x3.")
    if not np.all(np.isfinite(image)):
        raise SpectrumProfileError("ESA image contains non-finite values.")
    pixel_width_mm = float(pixel_width_mm)
    if not np.isfinite(pixel_width_mm) or pixel_width_mm <= 0:
        raise SpectrumProfileError("ESA pixel width must be positive and finite.")

    if roi is not None:
        original_ny, original_nx = image.shape
        image, selected, _ = crop_image(image, roi)
        ny, nx = image.shape
        x_full = ((np.arange(nx) + selected.x) - original_nx / 2) * pixel_width_mm
        y_full = ((np.arange(ny) + selected.y) - original_ny / 2) * pixel_width_mm
        return ProjectedProfiles(x_full, y_full, image, image.sum(axis=0), image.sum(axis=1))
    ny, nx = image.shape
    width_mm = nx * pixel_width_mm
    height_mm = ny * pixel_width_mm
    x_full = np.linspace(-0.5 * width_mm, 0.5 * width_mm, nx)
    y_full = np.linspace(-0.5 * height_mm, 0.5 * height_mm, ny)

    # The GUI uses strict xlim/ylim comparisons, which omit the outermost row/column.
    x_mask = (x_full > -0.5 * width_mm) & (x_full < 0.5 * width_mm)
    y_mask = (y_full > -0.5 * height_mm) & (y_full < 0.5 * height_mm)
    cropped = image[y_mask, :][:, x_mask]
    return ProjectedProfiles(
        x_mm=x_full[x_mask],
        y_mm=y_full[y_mask],
        image=cropped,
        density_x=np.sum(cropped, axis=0),
        density_y=np.sum(cropped, axis=1),
    )


def gaussian(x, amplitude, center, sigma, offset=0.0):
    return amplitude * np.exp(-((x - center) ** 2) / (2.0 * sigma ** 2)) + offset


def _direct_fit(x_mm, normalized_density, *, fallback_error=None):
    total = float(np.sum(normalized_density))
    if not np.isfinite(total) or total <= 0:
        raise SpectrumProfileError("ESA x projection is empty.")
    probabilities = normalized_density / total
    center = float(np.sum(x_mm * probabilities))
    variance = float(np.sum(probabilities * (x_mm - center) ** 2))
    return ProfileFit(
        center_mm=center,
        sigma_mm=float(np.sqrt(max(variance, 0.0))),
        normalized_density=normalized_density,
        fitted_density=normalized_density.copy(),
        method="direct",
        r_squared=None,
        fallback_error=fallback_error,
    )


def fit_projection_profile(
    x_mm,
    density_x,
    method,
    *,
    allow_direct_fallback=True,
    reject_poor_fit=True,
):
    """Measure the Gaussian center, whole-profile mean, or raw projection peak.

    Peak uses the maximum sampled bin for energy and the whole-profile RMS
    about the weighted mean for width, just like Direct.
    Set reject_poor_fit=False to display converged fits with quality warnings;
    automated center locking keeps the default rejection behavior.
    """
    x_mm = np.asarray(x_mm, dtype=float)
    density_x = np.asarray(density_x, dtype=float)
    if x_mm.ndim != 1 or density_x.ndim != 1 or x_mm.size != density_x.size:
        raise SpectrumProfileError("ESA x coordinates and projection must be equal-length vectors.")
    if x_mm.size < 3 or not np.all(np.isfinite(x_mm)) or not np.all(np.isfinite(density_x)):
        raise SpectrumProfileError("ESA x projection is too short or contains non-finite values.")
    peak = float(np.max(density_x))
    if not np.isfinite(peak) or peak <= 0:
        raise SpectrumProfileError("ESA x projection is empty.")
    normalized = density_x / peak
    normalized_method = str(method).strip().lower()
    if normalized_method == "direct":
        return _direct_fit(x_mm, normalized)
    if normalized_method == "peak":
        if float(np.ptp(normalized)) <= 1e-8:
            raise SpectrumProfileError("Projection has no distinguishable peak.")
        moments = _direct_fit(x_mm, normalized)
        return ProfileFit(
            center_mm=float(x_mm[int(np.argmax(normalized))]),
            sigma_mm=moments.sigma_mm,
            normalized_density=normalized,
            fitted_density=normalized.copy(),
            method="Peak",
            r_squared=None,
        )
    if normalized_method not in {"gauss", "gauss fit"}:
        raise SpectrumProfileError(f"Unsupported spectrum fit method: {method!r}.")

    try:
        if x_mm.size < 5 or np.any(np.diff(x_mm) <= 0):
            raise ValueError("Gaussian fitting needs at least five increasing x coordinates.")
        peak_index = int(np.argmax(normalized))
        baseline = float(np.percentile(normalized, 10))
        amplitude = float(normalized[peak_index] - baseline)
        if amplitude <= 1e-8:
            raise ValueError("Projection has no distinguishable peak above background.")
        # Initialize from the contiguous half-height peak, not the entire ROI.
        half_height = baseline + amplitude / 2.0
        left = right = peak_index
        while left > 0 and normalized[left] > half_height:
            left -= 1
        while right < x_mm.size - 1 and normalized[right] > half_height:
            right += 1
        pixel_width = float(np.median(np.diff(x_mm)))
        sigma_initial = max(float(x_mm[right] - x_mm[left]) / 2.35482, pixel_width)
        sigma_max = float(np.ptp(x_mm))
        parameters, _covariance = curve_fit(
            gaussian,
            x_mm,
            normalized,
            p0=[amplitude, float(x_mm[peak_index]), sigma_initial, baseline],
            bounds=(
                [0.0, float(np.min(x_mm)), pixel_width / 10.0, -np.inf],
                [np.inf, float(np.max(x_mm)), sigma_max, np.inf],
            ),
            maxfev=10000,
        )
        fitted = gaussian(x_mm, *parameters)
        if not np.all(np.isfinite(parameters)) or not np.all(np.isfinite(fitted)):
            raise ValueError("Gaussian fit returned non-finite parameters or curve.")
        residual_sum = float(np.sum((normalized - fitted) ** 2))
        centered_sum = float(np.sum((normalized - np.mean(normalized)) ** 2))
        r_squared = 1.0 - residual_sum / centered_sum if centered_sum > 0 else 0.0
        # Convergence alone can accept a broad pedestal while missing the beam.
        quality_warnings = []
        if not np.isfinite(r_squared) or r_squared < 0.7:
            quality_warnings.append(f"Poor Gaussian fit: R²={r_squared:.3f} < 0.700.")
        if abs(float(fitted[peak_index] - normalized[peak_index])) > 0.25 * amplitude:
            quality_warnings.append("Gaussian fit misses the projection peak by more than 25% of its height.")
        quality_warning = " ".join(quality_warnings) or None
        if quality_warning and reject_poor_fit:
            raise ValueError(quality_warning)
        return ProfileFit(
            center_mm=float(parameters[1]),
            sigma_mm=abs(float(parameters[2])),
            normalized_density=normalized,
            fitted_density=fitted,
            method="Gauss fit",
            r_squared=float(r_squared),
            quality_warning=quality_warning,
        )
    except (RuntimeError, ValueError, ZeroDivisionError, FloatingPointError) as exc:
        if not allow_direct_fallback:
            raise SpectrumProfileError(f"Gauss fit failed: {exc}") from exc
        return _direct_fit(x_mm, normalized, fallback_error=str(exc))
