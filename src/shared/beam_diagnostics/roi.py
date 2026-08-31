from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


class ROIError(ValueError):
    pass


@dataclass(frozen=True)
class ImageROI:
    x: int
    y: int
    width: int
    height: int

    def __post_init__(self):
        values = (self.x, self.y, self.width, self.height)
        if any(isinstance(value, bool) or not isinstance(value, (int, np.integer)) for value in values):
            raise ROIError("ROI coordinates and dimensions must be integers")
        if self.x < 0 or self.y < 0 or self.width <= 0 or self.height <= 0:
            raise ROIError("ROI coordinates must be non-negative and dimensions positive")

    def as_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


def full_frame_roi(shape) -> ImageROI:
    height, width = _shape(shape)
    return ImageROI(0, 0, width, height)


def clamp_roi(roi: ImageROI, shape) -> tuple[ImageROI, tuple[str, ...]]:
    height, width = _shape(shape)
    x = min(max(roi.x, 0), max(width - 1, 0))
    y = min(max(roi.y, 0), max(height - 1, 0))
    right = min(max(x + roi.width, x + 1), width)
    bottom = min(max(y + roi.height, y + 1), height)
    bounded = ImageROI(x, y, right - x, bottom - y)
    warnings = () if bounded == roi else (f"ROI {roi.as_dict()} clipped to image bounds {width}x{height}",)
    return bounded, warnings


def crop_image(image: Any, roi: ImageROI | None, *, clamp: bool = True):
    array = np.asarray(image)
    if array.ndim != 2:
        raise ROIError(f"image must be two-dimensional, got shape {array.shape}")
    if roi is None:
        return array, full_frame_roi(array.shape), ()
    selected, warnings = clamp_roi(roi, array.shape) if clamp else (roi, ())
    if selected.x + selected.width > array.shape[1] or selected.y + selected.height > array.shape[0]:
        raise ROIError("ROI is outside image bounds")
    return array[selected.y:selected.y + selected.height, selected.x:selected.x + selected.width], selected, warnings


def roi_extent(extent, roi: ImageROI, shape):
    """Return the physical extent corresponding to an image ROI."""
    xmin, xmax, ymin, ymax = (float(value) for value in extent)
    height, width = _shape(shape)
    return (
        xmin + roi.x / width * (xmax - xmin),
        xmin + (roi.x + roi.width) / width * (xmax - xmin),
        ymin + roi.y / height * (ymax - ymin),
        ymin + (roi.y + roi.height) / height * (ymax - ymin),
    )


def load_roi(path: str | Path) -> ImageROI | None:
    source = Path(path)
    if not source.is_file():
        return None
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
        return ImageROI(*(int(payload[key]) for key in ("x", "y", "width", "height")))
    except (OSError, KeyError, TypeError, ValueError, ROIError) as exc:
        raise ROIError(f"Could not load ROI {source}: {exc}") from exc


def save_roi(path: str | Path, roi: ImageROI) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(roi.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


def resolve_roi(*, runtime_path: str | Path, configured: dict[str, Any] | None, shape) -> tuple[ImageROI, str, tuple[str, ...]]:
    runtime = load_roi(runtime_path)
    source = "runtime" if runtime is not None else "configured" if configured else "full_frame"
    if runtime is not None:
        candidate = runtime
    elif configured:
        candidate = ImageROI(*(int(configured[key]) for key in ("x", "y", "width", "height")))
    else:
        candidate = full_frame_roi(shape)
    bounded, warnings = clamp_roi(candidate, shape)
    return bounded, source, warnings


def _shape(shape) -> tuple[int, int]:
    try:
        height, width = (int(shape[0]), int(shape[1]))
    except (TypeError, ValueError, IndexError) as exc:
        raise ROIError(f"invalid image shape: {shape!r}") from exc
    if height <= 0 or width <= 0:
        raise ROIError("image shape must be positive")
    return height, width
