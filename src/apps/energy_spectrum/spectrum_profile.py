from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import curve_fit


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


def project_image_profiles(image, pixel_width_mm):
    """Build the same cropped x/y projections used by the Energy Spectrum GUI."""
    image = np.asarray(image, dtype=float)
    if image.ndim != 2 or min(image.shape) < 3:
        raise SpectrumProfileError("ESA image must be a two-dimensional array of at least 3x3.")
    if not np.all(np.isfinite(image)):
        raise SpectrumProfileError("ESA image contains non-finite values.")
    pixel_width_mm = float(pixel_width_mm)
    if not np.isfinite(pixel_width_mm) or pixel_width_mm <= 0:
        raise SpectrumProfileError("ESA pixel width must be positive and finite.")

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


def gaussian(x, amplitude, center, sigma):
    return amplitude * np.exp(-((x - center) ** 2) / (2.0 * sigma ** 2))


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
):
    """Fit an ESA x projection with the GUI's Direct or Gaussian center definition."""
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
    if normalized_method not in {"gauss", "gauss fit"}:
        raise SpectrumProfileError(f"Unsupported spectrum fit method: {method!r}.")

    try:
        peak_index = int(np.argmax(normalized))
        sigma_initial = max(float(np.std(x_mm)), float(np.ptp(x_mm)) / 10.0, 1e-6)
        sigma_max = max(float(np.ptp(x_mm)), sigma_initial, 1e-6)
        parameters, _covariance = curve_fit(
            gaussian,
            x_mm,
            normalized,
            p0=[float(normalized[peak_index]), float(x_mm[peak_index]), sigma_initial],
            bounds=(
                [0.0, float(np.min(x_mm)), 1e-6],
                [np.inf, float(np.max(x_mm)), sigma_max],
            ),
        )
        fitted = gaussian(x_mm, *parameters)
        residual_sum = float(np.sum((normalized - fitted) ** 2))
        centered_sum = float(np.sum((normalized - np.mean(normalized)) ** 2))
        r_squared = 1.0 - residual_sum / centered_sum if centered_sum > 0 else 0.0
        return ProfileFit(
            center_mm=float(parameters[1]),
            sigma_mm=abs(float(parameters[2])),
            normalized_density=normalized,
            fitted_density=fitted,
            method="Gauss fit",
            r_squared=float(r_squared),
        )
    except (RuntimeError, ValueError, ZeroDivisionError, FloatingPointError) as exc:
        if not allow_direct_fallback:
            raise SpectrumProfileError(f"Gauss fit failed: {exc}") from exc
        return _direct_fit(x_mm, normalized, fallback_error=str(exc))
