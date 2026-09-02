import numpy as np
from matplotlib.colors import LogNorm, Normalize


BEAM_IMAGE_COLORMAPS = (
    "viridis",
    "plasma",
    "inferno",
    "magma",
    "gray",
    "jet",
)

DEFAULT_BEAM_IMAGE_COLORMAP = "viridis"


def resolve_image_display_scale(data, *, logarithmic=False, vmin=None, vmax=None):
    """Return display-only image data and normalization without changing analysis data."""
    image = np.asarray(data, dtype=float)
    finite_values = image[np.isfinite(image)]
    if finite_values.size == 0:
        return (
            np.ma.masked_invalid(image),
            Normalize(0.0, 1.0),
            "Image contains no finite intensity values.",
        )

    warning = None
    if logarithmic:
        positive_values = finite_values[finite_values > 0]
        if positive_values.size:
            auto_min = float(np.min(positive_values))
            auto_max = float(np.max(positive_values))
            resolved_min = auto_min if vmin is None or vmin <= 0 else float(vmin)
            resolved_max = auto_max if vmax is None or vmax <= 0 else float(vmax)
            if resolved_min >= resolved_max:
                resolved_min, resolved_max = auto_min, auto_max
                warning = "Invalid Log intensity limits; using the positive image range."
            elif (vmin is not None and vmin <= 0) or (vmax is not None and vmax <= 0):
                warning = "Log intensity limits must be positive; using automatic positive limits."
            if resolved_min >= resolved_max:
                resolved_min = resolved_max / 10.0
            display_image = np.ma.masked_less_equal(np.ma.masked_invalid(image), 0)
            return display_image, LogNorm(vmin=resolved_min, vmax=resolved_max), warning
        warning = "Log intensity unavailable because the image has no positive values."

    auto_min = float(np.min(finite_values))
    auto_max = float(np.max(finite_values))
    resolved_min = auto_min if vmin is None else float(vmin)
    resolved_max = auto_max if vmax is None else float(vmax)
    if resolved_min >= resolved_max:
        resolved_min, resolved_max = auto_min, auto_max
        warning = warning or "Invalid intensity limits; using the image range."
    if resolved_min >= resolved_max:
        resolved_max = resolved_min + 1.0
    return np.ma.masked_invalid(image), Normalize(resolved_min, resolved_max), warning
