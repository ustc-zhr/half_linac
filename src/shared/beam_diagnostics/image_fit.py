from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
from scipy.optimize import curve_fit

from .background_store import subtract_background


def gaussian(x_value, amplitude, center, sigma, offset):
    return amplitude * np.exp(-((x_value - center) ** 2) / (2 * sigma**2)) + offset


@dataclass(frozen=True)
class GaussianProjectionFit:
    axis: np.ndarray
    projection: np.ndarray
    normalized_projection: np.ndarray | None = None
    fitted_projection: np.ndarray | None = None
    amplitude: float | None = None
    center: float | None = None
    sigma: float | None = None
    offset: float | None = None
    residual_rms: float | None = None
    error: str | None = None

    @property
    def valid(self) -> bool:
        return self.sigma is not None and self.error is None

    @property
    def sigma_abs(self) -> float | None:
        if self.sigma is None:
            return None
        return abs(float(self.sigma))


@dataclass(frozen=True)
class BeamImageFitResult:
    x_axis: np.ndarray
    y_axis: np.ndarray
    cropped_image: np.ndarray
    x_projection: GaussianProjectionFit
    y_projection: GaussianProjectionFit
    status: str
    method: str = "Gaussian fit"
    message: str = ""

    @property
    def has_signal(self) -> bool:
        return self.status != "low_signal" and self.status != "empty_window"

    @property
    def valid(self) -> bool:
        return self.status == "valid" and self.x_projection.valid and self.y_projection.valid

    @property
    def sigx_mm(self) -> float | None:
        return self.x_projection.sigma_abs if self.valid else None

    @property
    def sigy_mm(self) -> float | None:
        return self.y_projection.sigma_abs if self.valid else None


def fit_beam_image(
    image,
    *,
    extent: Sequence[float],
    xlim: Sequence[float] | None = None,
    ylim: Sequence[float] | None = None,
    method: str = "Gaussian fit",
) -> BeamImageFitResult:
    normalized_method = str(method).strip().lower()
    if normalized_method in {"gaussian", "gaussian fit", "gauss", "gauss fit"}:
        resolved_method = "Gaussian fit"
    elif normalized_method in {"rms", "rms moments", "moments"}:
        resolved_method = "RMS moments"
    else:
        raise ValueError(f"Unsupported beam profile method: {method!r}.")

    image_array = np.asarray(image, dtype=float)
    if image_array.ndim != 2:
        raise ValueError(f"beam image must be 2D, got shape {image_array.shape}")
    if len(extent) != 4:
        raise ValueError("extent must contain xmin, xmax, ymin, ymax")

    xmin, xmax, ymin, ymax = [float(value) for value in extent]
    x_bounds = tuple(float(value) for value in (xlim or (xmin, xmax)))
    y_bounds = tuple(float(value) for value in (ylim or (ymin, ymax)))

    x_axis_full = np.linspace(xmin, xmax, image_array.shape[1])
    y_axis_full = np.linspace(ymin, ymax, image_array.shape[0])
    x_mask = np.logical_and(x_axis_full > x_bounds[0], x_axis_full < x_bounds[1])
    y_mask = np.logical_and(y_axis_full > y_bounds[0], y_axis_full < y_bounds[1])

    x_axis = x_axis_full[x_mask]
    y_axis = y_axis_full[y_mask]
    cropped_image = image_array[y_mask, :][:, x_mask]

    if cropped_image.size == 0 or x_axis.size == 0 or y_axis.size == 0:
        empty_x = GaussianProjectionFit(axis=x_axis, projection=np.array([], dtype=float))
        empty_y = GaussianProjectionFit(axis=y_axis, projection=np.array([], dtype=float))
        return BeamImageFitResult(
            x_axis=x_axis,
            y_axis=y_axis,
            cropped_image=cropped_image,
            x_projection=empty_x,
            y_projection=empty_y,
            status="empty_window",
            method=resolved_method,
            message="selected image window does not contain any pixels",
        )

    x_projection = np.sum(cropped_image, axis=0)
    y_projection = np.sum(cropped_image, axis=1)
    max_x = float(np.max(x_projection)) if x_projection.size else 0.0
    max_y = float(np.max(y_projection)) if y_projection.size else 0.0

    if max_x <= 0.0 or max_y <= 0.0:
        return BeamImageFitResult(
            x_axis=x_axis,
            y_axis=y_axis,
            cropped_image=cropped_image,
            x_projection=GaussianProjectionFit(axis=x_axis, projection=x_projection),
            y_projection=GaussianProjectionFit(axis=y_axis, projection=y_projection),
            status="low_signal",
            method=resolved_method,
            message="beam image projections do not contain positive signal",
        )

    projection_handler = (
        _fit_projection if resolved_method == "Gaussian fit" else _moment_projection
    )
    x_fit = projection_handler(x_axis, x_projection)
    y_fit = projection_handler(y_axis, y_projection)
    errors = [fit.error for fit in (x_fit, y_fit) if fit.error]
    if errors:
        return BeamImageFitResult(
            x_axis=x_axis,
            y_axis=y_axis,
            cropped_image=cropped_image,
            x_projection=x_fit,
            y_projection=y_fit,
            status="fit_failed",
            method=resolved_method,
            message="; ".join(errors),
        )

    return BeamImageFitResult(
        x_axis=x_axis,
        y_axis=y_axis,
        cropped_image=cropped_image,
        x_projection=x_fit,
        y_projection=y_fit,
        status="valid",
        method=resolved_method,
    )


def analyze_beam_image(
    image,
    *,
    extent: Sequence[float],
    background=None,
    xlim: Sequence[float] | None = None,
    ylim: Sequence[float] | None = None,
    method: str = "Gaussian fit",
) -> tuple[np.ndarray, BeamImageFitResult]:
    """Prepare one camera frame and run the shared beam-profile analysis."""
    image_array = np.asarray(image, dtype=float)
    if background is not None:
        image_array = subtract_background(image_array, background)
    result = fit_beam_image(
        image_array,
        extent=extent,
        xlim=xlim,
        ylim=ylim,
        method=method,
    )
    return image_array, result


def _moment_projection(axis: np.ndarray, projection: np.ndarray) -> GaussianProjectionFit:
    if axis.size == 0 or projection.size == 0:
        return GaussianProjectionFit(
            axis=axis,
            projection=projection,
            error="projection is empty",
        )

    weights = np.clip(np.asarray(projection, dtype=float), 0.0, None)
    total = float(np.sum(weights))
    peak = float(np.max(weights)) if weights.size else 0.0
    if not np.isfinite(total) or total <= 0.0 or not np.isfinite(peak) or peak <= 0.0:
        return GaussianProjectionFit(
            axis=axis,
            projection=projection,
            error="projection does not contain positive signal",
        )

    center = float(np.sum(axis * weights) / total)
    variance = float(np.sum(weights * (axis - center) ** 2) / total)
    normalized = weights / peak
    return GaussianProjectionFit(
        axis=axis,
        projection=projection,
        normalized_projection=normalized,
        center=center,
        sigma=float(np.sqrt(max(variance, 0.0))),
    )


def _fit_projection(axis: np.ndarray, projection: np.ndarray) -> GaussianProjectionFit:
    if axis.size == 0 or projection.size == 0:
        return GaussianProjectionFit(
            axis=axis,
            projection=projection,
            error="projection is empty",
        )

    max_projection = float(np.max(projection))
    if max_projection <= 0.0:
        return GaussianProjectionFit(
            axis=axis,
            projection=projection,
            error="projection does not contain positive signal",
        )

    normalized = projection / max_projection
    if axis.size < 4 or projection.size < 4:
        return GaussianProjectionFit(
            axis=axis,
            projection=projection,
            normalized_projection=normalized,
            error="projection has too few points for Gaussian fitting",
        )

    max_index = int(np.argmax(normalized))
    initial_guess = [
        float(np.max(normalized)),
        float(axis[max_index]),
        1.0,
        float(np.min(normalized)),
    ]

    try:
        popt, _pcov = curve_fit(gaussian, axis, normalized, p0=initial_guess)
    except (RuntimeError, ValueError, ZeroDivisionError, FloatingPointError) as exc:
        return GaussianProjectionFit(
            axis=axis,
            projection=projection,
            normalized_projection=normalized,
            error=str(exc),
        )

    fitted = gaussian(axis, popt[0], popt[1], popt[2], popt[3])
    residual_rms = float(np.sqrt(np.mean((fitted - normalized) ** 2)))
    return GaussianProjectionFit(
        axis=axis,
        projection=projection,
        normalized_projection=normalized,
        fitted_projection=fitted,
        amplitude=float(popt[0]),
        center=float(popt[1]),
        sigma=float(popt[2]),
        offset=float(popt[3]),
        residual_rms=residual_rms,
    )
