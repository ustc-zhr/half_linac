"""Fresh-frame camera sampling for the standalone InfoBAX workflow (no I/O on import)."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import hashlib
import json
import math
import time
from typing import Callable

import numpy as np

from half_linac.src.shared.beam_diagnostics.background_store import subtract_background
from half_linac.src.shared.beam_diagnostics.image_fit import (
    assess_projection_quality, fit_beam_image, reshape_beam_image,
)
from .infobax_session import PointMeasurement


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
                                    separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class CameraSettings:
    shape: tuple[int, int]
    pixel_width_mm: float
    plane: str = "x"
    flip_y: bool = False
    roi: tuple[int, int, int, int] | None = None  # left, right, bottom, top; exclusive ends
    samples: int = 5
    timeout: float = 120.0
    sample_interval: float = 0.0
    saturation_level: float | None = None

    def __post_init__(self):
        if (len(self.shape) != 2 or any(type(n) is not int or n < 4 for n in self.shape)
                or self.plane not in {"x", "y"}
                or type(self.samples) is not int or self.samples < 3):
            raise ValueError("Invalid camera dimensions, plane or sample count (minimum 3)")
        for name in ("pixel_width_mm", "timeout", "sample_interval"):
            value = getattr(self, name)
            if not math.isfinite(value) or (value < 0 if name == "sample_interval" else value <= 0):
                raise ValueError(f"Invalid {name}")
        if self.roi is not None:
            l, r, b, t = self.roi
            if (any(type(n) is not int for n in self.roi)
                    or not 0 <= l < r <= self.shape[0] or not 0 <= b < t <= self.shape[1]):
                raise ValueError("ROI must be pixel bounds inside the image")
        if self.saturation_level is not None and (
                not math.isfinite(self.saturation_level) or self.saturation_level <= 0):
            raise ValueError("Invalid saturation level")


class CameraPointAcquirer:
    """Read ID/image/ID consistently, discard one post-settle frame, then sample.

    The deployment must verify the frame-ID channel belongs to the published
    image buffer. A generic camera acquisition counter is insufficient unless
    its association with that buffer has been established at the site.
    """

    def __init__(self, settings: CameraSettings, *, get: Callable, image_pv: str,
                 frame_id_pv: str, exposure_pv: str, background=None,
                 identity_verified: bool = False, clock=time.monotonic, sleeper=time.sleep):
        if not identity_verified or not all((image_pv, frame_id_pv, exposure_pv)):
            raise ValueError("Real acquisition requires a verified image/frame-ID association and exposure channel")
        self.settings, self.get = settings, get
        self.image_pv, self.frame_id_pv, self.exposure_pv = image_pv, frame_id_pv, exposure_pv
        self.clock, self.sleep = clock, sleeper
        self.background = None if background is None else np.array(background, dtype=float, copy=True)
        if self.background is not None:
            if self.background.shape != (settings.shape[1], settings.shape[0]) or not np.all(np.isfinite(self.background)):
                raise ValueError("Background dimensions or values are invalid")
            self.background.setflags(write=False)
        self.frozen_exposure = None
        self.last_id = None
        self.archive_dir = None
        self.statistics = {"image_reads": 0, "unstable_reads": 0, "discarded_frames": 0, "accepted_frames": 0}
        self.check = lambda: None

    @property
    def fingerprint(self):
        return fingerprint({"settings": asdict(self.settings), "image": self.image_pv,
                            "frame_id": self.frame_id_pv, "exposure": self.exposure_pv,
                            "background": None if self.background is None else
                            hashlib.sha256(self.background.tobytes()).hexdigest(),
                            "estimator": "pixel-centre-intensity-variance-sem-v1"})

    def read(self, pv):
        value = self.get(pv, timeout=min(self.settings.timeout, 2.0), use_monitor=False)
        if value is None:
            raise RuntimeError(f"Camera channel unavailable: {pv}")
        return value

    def frame_id(self):
        value = float(self.read(self.frame_id_pv))
        if not math.isfinite(value) or value < 0 or not value.is_integer():
            raise RuntimeError("Invalid frame ID")
        return int(value)

    def exposure(self):
        value = float(self.read(self.exposure_pv))
        if not math.isfinite(value) or value <= 0:
            raise RuntimeError("Invalid exposure")
        return value

    def arm(self):
        self.frozen_exposure = self.exposure()
        self.last_id = self.frame_id()

    def analyze(self, raw):
        s = self.settings
        image = reshape_beam_image(raw, s.shape, flip_y=s.flip_y)
        if not np.all(np.isfinite(image)):
            raise ValueError("Non-finite image")
        if s.saturation_level is not None and np.any(image >= s.saturation_level):
            raise ValueError("Saturated image")
        image = (np.maximum(image, 0) if self.background is None else
                 subtract_background(image, self.background))
        l, r, b, t = s.roi or (0, s.shape[0], 0, s.shape[1])
        image = image[b:t, l:r]
        d = s.pixel_width_mm
        extent = (l*d, r*d, b*d, t*d)
        rms = fit_beam_image(image, extent=extent, method="RMS moments", pixel_centers=True)
        diagnostic = fit_beam_image(image, extent=extent, method="Gaussian fit", pixel_centers=True)
        projections = {"x": rms.x_projection, "y": rms.y_projection}
        quality = {p: assess_projection_quality(v) for p, v in projections.items()}
        if not rms.valid or not quality[s.plane]["usable"]:
            raise ValueError(f"RMS quality rejected: {quality[s.plane]}")
        variances = {p: None if not v.valid else float(v.sigma_abs**2) for p, v in projections.items()}
        gaussian = {p: None if not v.valid else float(v.sigma_abs**2) for p, v in
                    (("x", diagnostic.x_projection), ("y", diagnostic.y_projection))}
        return variances[s.plane], {"rms_variances_mm2": variances,
                                    "gaussian_variances_mm2": gaussian, "quality": quality}

    def measure(self, query_index):
        if self.frozen_exposure is None:
            raise RuntimeError("Camera not armed")
        s = self.settings
        deadline = self.clock() + s.timeout
        barrier = self.frame_id()  # Called only after motion and settle have completed.
        if self.last_id is not None and barrier < self.last_id:
            raise RuntimeError("Camera frame counter reset")
        last = barrier
        discard = True
        values, frames = [], []
        next_sample = self.clock()
        while len(values) < s.samples:
            self.check()
            if self.clock() >= deadline:
                raise TimeoutError("Timed out waiting for independent camera frames")
            if self.exposure() != self.frozen_exposure:
                raise RuntimeError("Camera exposure changed during the run")
            before = self.frame_id()
            if before < last:
                raise RuntimeError("Camera frame counter reset")
            if before == last or self.clock() < next_sample:
                self.sleep(0.02)
                continue
            raw = np.array(self.read(self.image_pv), copy=True)
            self.statistics["image_reads"] += 1
            after = self.frame_id()
            if before != after:
                self.statistics["unstable_reads"] += 1
                self.sleep(0.02)
                continue
            last = after
            self.last_id = last
            if discard:
                discard = False
                self.statistics["discarded_frames"] += 1
                continue
            path = None
            if self.archive_dir is not None:
                directory = Path(self.archive_dir) / "frames"
                directory.mkdir(parents=True, exist_ok=True)
                path = directory / f"{time.time_ns()}_q{query_index}_f{last}.npz"
                np.savez_compressed(path, raw=raw, frame_id=last, exposure=self.frozen_exposure)
            variance, info = self.analyze(raw)
            self.statistics["accepted_frames"] += 1
            values.append(variance)
            frames.append({"frame_id": last, "exposure": self.frozen_exposure,
                           "raw_path": None if path is None else str(path), **info})
            next_sample = self.clock() + s.sample_interval
        noise = float(np.std(values, ddof=1) / np.sqrt(len(values)))
        if not math.isfinite(noise) or noise <= 0:
            raise ValueError("Repeated-frame variance uncertainty is zero or invalid")
        return PointMeasurement(float(np.mean(values)), noise, {"frames": frames,
                                "frame_count": len(frames), "variance_samples_mm2": values})
