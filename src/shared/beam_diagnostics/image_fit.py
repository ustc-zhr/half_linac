from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
from scipy.optimize import curve_fit

from .background_store import subtract_background


def gaussian(x_value, amplitude, center, sigma, offset):
    return amplitude * np.exp(-((x_value - center) ** 2) / (2 * sigma**2)) + offset


def reshape_beam_image(raw_image, pixel_shape, *, flip_y=False) -> np.ndarray:
    """Validate and reshape a flattened camera frame using ``(width, height)``."""
    width, height = (int(pixel_shape[0]), int(pixel_shape[1]))
    array = np.asarray(raw_image, dtype=float)
    expected = width * height
    if array.size != expected:
        raise ValueError(f"image size {array.size} does not match {(width, height)}")
    image = array.reshape((height, width))
    return np.flipud(image) if flip_y else image


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


def assess_projection_quality(
    projection: GaussianProjectionFit,
    *,
    min_sigma_pixels: float = 1.5,
    min_containment_sigma: float = 3.0,
    max_edge_ratio: float = 0.05,
    max_fit_residual: float = 0.15,
) -> dict[str, float | str | bool | None]:
    """Classify whether a fitted projection is suitable for a size sample."""
    payload: dict[str, float | str | bool | None] = {
        "status": "fit_failed",
        "usable": False,
        "sigma_pixels": None,
        "containment_sigma": None,
        "edge_ratio": None,
        "fit_residual": projection.residual_rms,
    }
    if not projection.valid or projection.center is None or projection.sigma_abs is None:
        return payload

    axis = np.asarray(projection.axis, dtype=float)
    values = np.asarray(projection.projection, dtype=float)
    if axis.size < 2 or values.size != axis.size:
        return payload
    pixel_width = float(np.median(np.abs(np.diff(axis))))
    sigma = float(projection.sigma_abs)
    center = float(projection.center)
    sigma_pixels = sigma / pixel_width if pixel_width > 0 else 0.0
    margin = min(center - float(axis[0]), float(axis[-1]) - center)
    containment = margin / sigma if sigma > 0 else 0.0

    baseline = float(projection.offset or 0.0) * float(np.max(values))
    signal = np.clip(values - baseline, 0.0, None)
    peak = float(np.max(signal)) if signal.size else 0.0
    edge_bins = max(2, min(5, signal.size // 20))
    edge_level = max(float(np.mean(signal[:edge_bins])), float(np.mean(signal[-edge_bins:])))
    edge_ratio = edge_level / peak if peak > 0 else 1.0
    residual = projection.residual_rms

    if containment < min_containment_sigma or edge_ratio > max_edge_ratio:
        status = "clipped"
    elif sigma_pixels < min_sigma_pixels:
        status = "underresolved"
    elif residual is not None and residual > max_fit_residual:
        status = "poor_fit"
    else:
        status = "usable"
    payload.update(
        {
            "status": status,
            "usable": status == "usable",
            "sigma_pixels": sigma_pixels,
            "containment_sigma": containment,
            "edge_ratio": edge_ratio,
        }
    )
    return payload


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
    roi=None,
    fit_vmin: float | None = None,
) -> tuple[np.ndarray, BeamImageFitResult]:
    """Prepare one camera frame and run the shared beam-profile analysis."""
    image_array = np.asarray(image, dtype=float)
    if background is not None:
        image_array = subtract_background(image_array, background)
    if roi is not None:
        from .roi import crop_image, roi_extent
        image_array, selected, _warnings = crop_image(image_array, roi)
        extent = roi_extent(extent, selected, np.asarray(image).shape)
    fit_image = image_array
    if fit_vmin is not None:
        if not np.isfinite(fit_vmin):
            raise ValueError("fit vmin must be finite")
        fit_image = np.where(image_array >= fit_vmin, image_array, 0.0)
    result = fit_beam_image(
        fit_image,
        extent=extent,
        xlim=xlim,
        ylim=ylim,
        method=method,
    )
    return image_array, result


def analyze_raw_beam_image(
    raw_image,
    *,
    pixel_shape,
    extent,
    background=None,
    roi=None,
    flip_y=False,
    full_frame_for_roi=False,
    analyzer=None,
):
    """Prepare a raw camera frame and run the standard beam-image analysis."""
    image = reshape_beam_image(raw_image, pixel_shape, flip_y=flip_y)
    if flip_y and background is not None:
        background = np.flipud(np.asarray(background))
    analyze = analyze_beam_image if analyzer is None else analyzer
    analysis = analyze(
        image,
        extent=extent,
        background=background,
        roi=roi,
    )
    if full_frame_for_roi and roi is not None:
        return image, analysis[1]
    return analysis


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

    offset = float(np.min(normalized))
    signal = normalized - offset
    total = float(np.sum(signal))
    if not np.isfinite(total) or total <= 0.0:
        return GaussianProjectionFit(
            axis=axis,
            projection=projection,
            normalized_projection=normalized,
            error="projection does not contain finite beam contrast",
        )
    # Seed in the camera's physical scale, including narrow beams and ROIs.
    center = float(np.sum(axis * signal) / total)
    sigma = float(np.sqrt(np.sum(signal * (axis - center) ** 2) / total))
    pixel_step = float(np.min(np.abs(np.diff(axis))))
    initial_guess = [
        float(np.max(signal)),
        center,
        max(sigma, pixel_step),
        offset,
    ]

    try:
        popt, _pcov = curve_fit(gaussian, axis, normalized, p0=initial_guess, maxfev=5000)
        if not np.all(np.isfinite(popt)) or popt[0] <= 0 or abs(popt[2]) <= 0:
            raise ValueError("Gaussian fit returned invalid beam parameters")
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
