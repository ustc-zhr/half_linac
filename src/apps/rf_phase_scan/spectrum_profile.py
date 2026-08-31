from dataclasses import dataclass

import numpy as np
from scipy.optimize import curve_fit
from half_linac.src.shared.beam_diagnostics.roi import ImageROI, crop_image


class SpectrumProfileError(ValueError):
    pass


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


def project_image_profiles(image, pixel_width_mm, roi: ImageROI | None = None):
    image = np.asarray(image, dtype=float)
    if image.ndim != 2 or min(image.shape) < 3 or not np.all(np.isfinite(image)):
        raise SpectrumProfileError("ESA image must be a finite two-dimensional array of at least 3x3.")
    pixel_width_mm = float(pixel_width_mm)
    if not np.isfinite(pixel_width_mm) or pixel_width_mm <= 0:
        raise SpectrumProfileError("ESA pixel width must be positive and finite.")
    if roi is not None:
        original_ny, original_nx = image.shape
        image, selected, _ = crop_image(image, roi)
        origin_x, origin_y = selected.x, selected.y
        # ROI axes remain in the full-frame physical coordinate system.
        ny, nx = image.shape
        x_full = ((np.arange(nx) + origin_x) - original_nx / 2) * pixel_width_mm
        y_full = ((np.arange(ny) + origin_y) - original_ny / 2) * pixel_width_mm
        return ProjectedProfiles(x_full, y_full, image, image.sum(axis=0), image.sum(axis=1))
    ny, nx = image.shape
    width_mm = nx * pixel_width_mm
    height_mm = ny * pixel_width_mm
    x_full = np.linspace(-width_mm / 2, width_mm / 2, nx)
    y_full = np.linspace(-height_mm / 2, height_mm / 2, ny)
    x_mask = (x_full > -width_mm / 2) & (x_full < width_mm / 2)
    y_mask = (y_full > -height_mm / 2) & (y_full < height_mm / 2)
    cropped = image[y_mask, :][:, x_mask]
    return ProjectedProfiles(
        x_full[x_mask],
        y_full[y_mask],
        cropped,
        cropped.sum(axis=0),
        cropped.sum(axis=1),
    )


def _gaussian(x, amplitude, center, sigma):
    return amplitude * np.exp(-((x - center) ** 2) / (2 * sigma ** 2))


def fit_projection_profile(x_mm, density_x, method, *, allow_direct_fallback=True):
    x_mm = np.asarray(x_mm, dtype=float)
    density_x = np.asarray(density_x, dtype=float)
    if x_mm.ndim != 1 or x_mm.size < 3 or x_mm.size != density_x.size or not np.all(np.isfinite(density_x)):
        raise SpectrumProfileError("ESA x projection is invalid.")
    peak = float(np.max(density_x))
    if peak <= 0:
        raise SpectrumProfileError("ESA x projection is empty.")
    normalized = density_x / peak
    requested = str(method).strip().lower()

    def direct(error=None):
        probabilities = normalized / np.sum(normalized)
        center = float(np.sum(x_mm * probabilities))
        sigma = float(np.sqrt(max(np.sum(probabilities * (x_mm - center) ** 2), 0)))
        return ProfileFit(center, sigma, normalized, normalized.copy(), "direct", None, error)

    if requested == "direct":
        return direct()
    if requested not in {"gauss", "gauss fit"}:
        raise SpectrumProfileError(f"Unsupported spectrum fit method: {method!r}.")
    try:
        sigma0 = max(float(np.ptp(x_mm)) / 10, 1e-6)
        parameters, _ = curve_fit(
            _gaussian, x_mm, normalized,
            p0=[1, float(x_mm[np.argmax(normalized)]), sigma0],
            bounds=([0, float(np.min(x_mm)), 1e-6], [np.inf, float(np.max(x_mm)), max(float(np.ptp(x_mm)), sigma0)]),
        )
        fitted = _gaussian(x_mm, *parameters)
        residual = float(np.sum((normalized - fitted) ** 2))
        total = float(np.sum((normalized - np.mean(normalized)) ** 2))
        return ProfileFit(float(parameters[1]), abs(float(parameters[2])), normalized, fitted, "Gauss fit", 1 - residual / total if total else 0.0)
    except (RuntimeError, ValueError, FloatingPointError) as exc:
        if not allow_direct_fallback:
            raise SpectrumProfileError(f"Gauss fit failed: {exc}") from exc
        return direct(str(exc))
