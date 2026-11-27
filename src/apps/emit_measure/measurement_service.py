"""Shared emittance point acquisition; no Qt or PV access on import.

The scan thread owns scan selection/reconstruction and delegates its physical
point operation here. InfoBAX uses the same operation with timed sampling.
"""
from __future__ import annotations
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, asdict
from functools import partial
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import math
import time
import numpy as np

from half_linac.src.shared.beam_diagnostics.image_fit import analyze_raw_beam_image, analyze_beam_image, assess_projection_quality, fit_beam_image, reshape_beam_image
from half_linac.src.shared.beam_diagnostics.background_store import subtract_background
from half_linac.src.shared.machine_profile import MachineProfileError
from .adaptive_scan import AdaptiveObservation


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
                                    separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class MeasurementSnapshot:
    image_pv: str
    pixel_shape: tuple[int, int]
    extent: tuple[float, float, float, float]
    samples: int
    settle_time: float
    sample_interval: float
    method: str = "Gaussian fit"
    flip_y: bool = False
    roi: object = None
    background: object = None
    fit_vmin: float | None = None
    screen: str = ""

    @classmethod
    def from_parameters(cls, p):
        roi = deepcopy(getattr(p, "roi", None))
        background = getattr(p, "background_image", None)
        if background is not None:
            background = np.array(background, copy=True)
            background.setflags(write=False)
        if type(p.samples) is not int or p.samples < 1:
            raise ValueError("Samples per point must be a positive integer")
        if any(not math.isfinite(v) or v < 0 for v in (p.settle_time, p.sample_interval)):
            raise ValueError("Invalid sampling times")
        return cls(p.flagImagePV, tuple(p.flag_pixel_shape), tuple(p.flag_image_extent),
                   p.samples, p.settle_time, p.sample_interval,
                   getattr(p, "beam_width_method", "Gaussian fit"),
                   getattr(p, "flag_image_flip_y", False), roi, background,
                   getattr(p, "fit_vmin", None), p.flag_name)

    def metadata(self):
        return {"image_pv": self.image_pv, "pixel_shape": self.pixel_shape,
                "extent": self.extent, "samples": self.samples,
                "settle_time": self.settle_time, "sample_interval": self.sample_interval,
                "method": self.method, "flip_y": self.flip_y,
                "roi": None if self.roi is None else self.roi.as_dict(),
                "background_sha256": None if self.background is None else
                hashlib.sha256(self.background.tobytes()).hexdigest(),
                "fit_vmin": self.fit_vmin, "screen": self.screen,
                "freshness": "timed_unverified"}

    @property
    def fingerprint(self):
        return fingerprint(self.metadata())


@dataclass(frozen=True)
class PointAcquisitionResult:
    k1: float
    frames: tuple[dict, ...]
    method: str
    ict1_window_id: str | None = None

    def to_dict(self):
        result = {"k1": self.k1, "frames": list(self.frames), "method": self.method,
                  "freshness": "timed_unverified"}
        if self.ict1_window_id is not None:
            result["ict1_window_id"] = self.ict1_window_id
        return result

    @classmethod
    def from_dict(cls, data):
        return cls(float(data["k1"]), tuple(data["frames"]), data["method"], data.get("ict1_window_id"))

    def observation(self, plane):
        from .infobax_session import PointMeasurement
        frames = [f for f in self.frames if "sig"+plane in f]
        if len(frames) < 3:
            raise ValueError("InfoBAX requires at least 3 frames per K1 point; adjust main-window Samples")
        if not all(f["quality"][plane]["usable"] for f in frames):
            raise ValueError(f"{plane.upper()} point failed the existing image-quality checks")
        values = np.asarray([f["sig"+plane]**2 for f in frames])
        return PointMeasurement(float(np.mean(values)), float(np.std(values, ddof=1)/np.sqrt(len(values))),
                                {"frames": frames, "frame_count": len(frames),
                                 "variance_samples_mm2": values.tolist(), "method": self.method,
                                 "freshness": "timed_unverified", "uncertainty": "repeatability_sem"})


def read_flag_image_fit(
    image_pv,
    pixel_shape,
    extent,
    *,
    background=None,
    roi=None,
    flip_y=False,
    full_frame_for_roi=False,
    method="Gaussian fit",
    fit_vmin=None,
    get=None, raw_archive_dir=None, on_raw=None,
):
    if get is None:
        import epics
        get = epics.caget
    raw_image = get(image_pv)
    if raw_image is None:
        raise RuntimeError(f"Failed to read flag image PV: {image_pv}.")
    if raw_archive_dir is not None:
        directory = Path(raw_archive_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"frame_{time.time_ns()}.npz"
        np.savez_compressed(path, raw=np.asarray(raw_image))
        if on_raw is not None:
            on_raw({"raw_path": str(path)})
    try:
        return analyze_raw_beam_image(
            raw_image,
            pixel_shape=pixel_shape,
            extent=extent,
            background=background,
            roi=roi,
            flip_y=flip_y,
            full_frame_for_roi=full_frame_for_roi,
            analyzer=partial(analyze_beam_image, method=method, fit_vmin=fit_vmin),
        )
    except (TypeError, ValueError) as exc:
        if "image size" in str(exc):
            raise RuntimeError(
                f"Flag image length mismatch for {image_pv}: got {np.asarray(raw_image).size}, "
                f"expected {pixel_shape[0] * pixel_shape[1]}."
            ) from exc
        raise RuntimeError(f"Flag image PV is not numeric array data: {image_pv}.") from exc
def trim_charge_values(values, trim_fraction):
    ordered = np.sort(np.asarray(values, dtype=float))
    trim_count = int(math.floor(len(ordered) * trim_fraction))
    used = ordered[trim_count:len(ordered) - trim_count] if trim_count else ordered
    return ordered, used, trim_count


class ScanChargeMonitor:
    """Collect charge samples for one channel during a quadrupole scan."""

    def __init__(self, settings):
        self.settings = settings
        self.pv = None
        self.connection_error = None
        self.samples = []
        self.attempts = 0

    @property
    def element_id(self):
        return self.settings["element_id"]

    def prepare(self, pv_factory):
        pv = pv_factory(self.settings["pv"], form="time", auto_monitor=False)
        if pv.wait_for_connection(timeout=2):
            self.pv = pv
        else:
            self.connection_error = f"{self.element_id} charge channel is disconnected."

    def read(self, *, window_opened_at=None, after_timestamp=None):
        if self.pv is None:
            if window_opened_at is None:
                return None
            self.attempts += 1
            sample = {"received_at": time.time(), "status": "invalid",
                      "error": self.connection_error or "ICT1 charge channel is disconnected"}
            self.samples.append(sample)
            return sample
        self.attempts += 1
        received_at = time.time()
        sample = {"received_at": received_at, "status": "invalid"}
        try:
            metadata = self.pv.get_with_metadata(
                use_monitor=False, timeout=2, with_ctrlvars=False,
            )
            if not isinstance(metadata, Mapping):
                raise ValueError("read returned no metadata")
            raw_value = float(metadata.get("value"))
            value = raw_value * float(self.settings["scale"])
            if window_opened_at is None:
                source_timestamp = float(metadata.get("timestamp", received_at))
            else:
                sample["raw_value"] = raw_value if math.isfinite(raw_value) else None
                sample["value"] = value if math.isfinite(value) else None
                for key, source in (("severity", "severity"), ("status_code", "status")):
                    sample[key] = int(metadata[source]) if metadata.get(source) is not None else None
                if metadata.get("timestamp") is None:
                    raise ValueError("missing source timestamp")
                source_timestamp = float(metadata["timestamp"])
                if not math.isfinite(source_timestamp):
                    raise ValueError("invalid source timestamp")
                sample["source_timestamp"] = source_timestamp
                sample["read_finished_at"] = time.time()
                if source_timestamp < window_opened_at or source_timestamp > sample["read_finished_at"]:
                    raise ValueError("sample timestamp is outside the stable window")
                if after_timestamp is not None and source_timestamp <= after_timestamp:
                    raise ValueError("duplicate or out-of-order source timestamp")
                if (sample["severity"] or 0) >= 2:
                    raise ValueError("charge PV alarm")
            stale_timeout = self.settings.get("stale_timeout_s")
            if not math.isfinite(value) or value <= 0:
                raise ValueError("value is not finite and positive")
            if stale_timeout is not None and received_at - source_timestamp > float(stale_timeout):
                raise ValueError("sample is stale")
            sample.update(
                status="valid", value=value, source_timestamp=source_timestamp,
                severity=sample.get("severity", metadata.get("severity")),
                status_code=sample.get("status_code", metadata.get("status")),
            )
        except Exception as exc:
            sample["error"] = str(exc)
        self.samples.append(sample)
        return sample

    def summary(self):
        valid = [sample["value"] for sample in self.samples if sample["status"] == "valid"]
        minimum_count = int(self.settings.get("minimum_samples", 3))
        minimum_fraction = float(self.settings.get("minimum_valid_fraction", 0.8))
        coverage = len(valid) / self.attempts if self.attempts else 0.0
        summary = {
            "element_id": self.element_id,
            "channel": self.settings["channel"],
            "unit": self.settings["unit"],
            "attempted_samples": self.attempts,
            "valid_samples": len(valid),
            "valid_fraction": coverage,
            "trim_fraction": float(self.settings["trim_fraction"]),
            "samples": self.samples,
        }
        if self.connection_error:
            summary["connection_error"] = self.connection_error
        if len(valid) < minimum_count or coverage < minimum_fraction:
            summary.update(
                status="invalid",
                error=(self.connection_error or
                       f"{self.element_id} charge samples are insufficient "
                       f"({len(valid)}/{self.attempts} valid)."),
            )
            return summary
        ordered, used, trim_count = trim_charge_values(valid, summary["trim_fraction"])
        summary.update(
            status="valid", value=float(np.mean(used)),
            mean=float(np.mean(ordered)), median=float(np.median(ordered)),
            minimum=float(np.min(ordered)), maximum=float(np.max(ordered)),
            standard_deviation=(float(np.std(ordered, ddof=1)) if len(ordered) > 1 else 0.0),
            trimmed_each_side=trim_count, used_samples=len(used),
        )
        return summary


def acquire_scan_point(self, k1, *, adaptive=False, read_image_fit, read_sizes, quality,
                       format_status, read_charge_samples=None, before_images=None):
    checkpoint = getattr(self, "measurement_check", None)
    if checkpoint is not None:
        checkpoint()
    if self.effective_k1_limit is not None and not self.effective_k1_limit.contains(k1):
        raise MachineProfileError(
            f"Planned K₁ = {float(k1):g} m⁻² is outside the effective limit "
            f"{self.effective_k1_limit.describe()} for {self.quad_name}.K₁."
        )
    if self.restore_quad_callback is not None:
        def check_running():
            if checkpoint is not None:
                checkpoint()
            if not self.is_running:
                raise RuntimeError("Scan stopped.")
        self.restore_quad_callback.move(k1, check_running)
    else:
        import epics
        epics.caput(self.quadPV, k1)
    if not self._sleep_or_stop(self.settle_time):
        return None

    ict1_window_id = before_images(float(k1)) if before_images is not None else None
    frame_results = []
    sigx_values = []
    sigy_values = []
    usable_sigx_values = []
    usable_sigy_values = []
    for sample_index in range(self.samples):
        if checkpoint is not None:
            checkpoint()
        if not self.is_running:
            return None
        self._emit_scan_progress(sample_index=sample_index + 1)
        if sample_index > 0 and not self._sleep_or_stop(self.sample_interval):
            return None

        retry = 0
        while self.is_running:
            frame_results.append({})
            try:
                image, fit_result = read_image_fit(
                    self.flagImagePV,
                    self.flag_pixel_shape,
                    self.flag_image_extent,
                    background=self.background_image,
                    roi=self.roi,
                    flip_y=self.flag_image_flip_y,
                    full_frame_for_roi=True,
                    method=self.beam_width_method,
                    fit_vmin=self.fit_vmin,
                    **({"raw_archive_dir": self.raw_archive_dir,
                        "on_raw": lambda metadata: frame_results[-1].update(metadata)}
                       if getattr(self, "raw_archive_dir", None) else {}),
                )
            except RuntimeError as exc:
                if not adaptive or retry >= self.adaptive_config.max_retries:
                    if adaptive:
                        self._emit_adaptive_status(
                            f"Rejected K₁ = {k1:g} m⁻²: image unavailable",
                            k1=k1,
                        )
                        return None
                    raise
                retry += 1
                self._emit_adaptive_status(
                    f"Retrying K₁ = {k1:g} m⁻² "
                    f"({retry}/{self.adaptive_config.max_retries})",
                    k1=k1,
                    retry_reason=str(exc),
                )
                if self.sample_interval > 0 and not self._sleep_or_stop(
                    self.sample_interval
                ):
                    return None
                continue
            size_pv = read_sizes(
                self.flagSigxPV,
                self.flagSigyPV,
            )
            point = {
                "method": None,
                "k1": k1,
                "flag": self.flag_name,
                "beam_image": image,
                "beam_fit": fit_result,
                "beam_extent": self.flag_image_extent,
                "show_roi_overlay": True,
                "size_pv": size_pv,
                "background_status": self.background_status,
            }
            if fit_result.valid:
                break
            self.trigger.emit(point)
            detail = f": {fit_result.message}" if fit_result.message else ""
            if not adaptive or retry >= self.adaptive_config.max_retries:
                if adaptive:
                    self._emit_adaptive_status(
                        f"Rejected K₁ = {k1:g} m⁻²: invalid image fit",
                        k1=k1,
                    )
                    return None
                raise RuntimeError(
                    f"Flag image fit failed for {self.flag_name} "
                    f"({format_status(fit_result.status)}){detail}."
                )
            retry += 1
            self._emit_adaptive_status(
                f"Retrying K₁ = {k1:g} m⁻² "
                f"({retry}/{self.adaptive_config.max_retries})",
                k1=k1,
            )
            if self.sample_interval > 0 and not self._sleep_or_stop(self.sample_interval):
                return None

        if not self.is_running:
            return None
        if read_charge_samples is not None:
            point.update(read_charge_samples())
        sigx = float(fit_result.sigx_mm)
        sigy = float(fit_result.sigy_mm)
        if self.scan_strategy == "adaptive_quality" or getattr(self, "require_point_quality", False):
            x_quality = quality(fit_result.x_projection)
            y_quality = quality(fit_result.y_projection)
        else:
            x_quality = {"status": "usable", "usable": True}
            y_quality = {"status": "usable", "usable": True}
        print("Quad K1=", k1, "sigmax=", sigx, "sigmay=", sigy)
        point["sigx"] = sigx
        point["sigy"] = sigy
        point["plane_quality"] = {"x": x_quality, "y": y_quality}
        if self.scan_strategy == "adaptive_quality" and (
            not x_quality["usable"] or not y_quality["usable"]
        ):
            self._emit_adaptive_status(
                f"Quality at K₁ = {k1:g} m⁻² "
                f"· X: {format_status(x_quality['status'])} "
                f"· Y: {format_status(y_quality['status'])}",
                k1=k1,
                plane_quality=point["plane_quality"],
            )
        point["adaptive_stage"] = "measurement" if adaptive else None
        self.k1l.append(k1)
        self.sigxl.append(sigx)
        self.sigyl.append(sigy)
        self.x_quality_usable.append(bool(x_quality["usable"]))
        self.y_quality_usable.append(bool(y_quality["usable"]))
        self.point_quality.append(
            {"k1": float(k1), "x": x_quality, "y": y_quality}
        )
        sigx_values.append(sigx)
        sigy_values.append(sigy)
        if x_quality["usable"]:
            usable_sigx_values.append(sigx)
        if y_quality["usable"]:
            usable_sigy_values.append(sigy)
        frame_results[-1].update(sigx=sigx, sigy=sigy, quality={"x": x_quality, "y": y_quality})
        self.trigger.emit(point)

    if not sigx_values:
        return None
    result = PointAcquisitionResult(float(k1), tuple(frame_results), self.beam_width_method, ict1_window_id)
    if not hasattr(self, "point_results"):
        self.point_results = []
    self.point_results.append(result.to_dict())
    callback = getattr(self, "on_point_result", None)
    if callback is not None:
        callback(result)
    self.completed_k1_points += 1
    self._emit_scan_progress()
    return AdaptiveObservation(
        k1=float(k1),
        sigx=float(np.mean(usable_sigx_values)) if usable_sigx_values else None,
        sigy=float(np.mean(usable_sigy_values)) if usable_sigy_values else None,
        sigx_err=self._sample_error(usable_sigx_values),
        sigy_err=self._sample_error(usable_sigy_values),
        x_usable=bool(usable_sigx_values),
        y_usable=bool(usable_sigy_values),
    )


class PointAcquirer:
    """Qt-free owner of the same point operation used by the normal scan."""
    def __init__(self, snapshot, *, move, check, get=None, progress=None, archive_dir=None, before_images=None):
        self.snapshot = snapshot
        self.move, self.check = move, check
        self.get, self.progress, self.archive_dir = get, progress, archive_dir
        self.before_images = before_images

    def acquire_point(self, k1):
        s = self.snapshot
        owner = _PointOwner(self)
        owner.raw_archive_dir = self.archive_dir
        def read(*args, **kwargs):
            return read_flag_image_fit(*args, get=self.get, **kwargs)
        result = acquire_scan_point(owner, float(k1), read_image_fit=read,
            read_sizes=lambda *a: (None, None), quality=assess_projection_quality,
            format_status=str, before_images=self.before_images)
        self.check()
        if result is None or not owner.point_results:
            raise RuntimeError("Point acquisition did not complete")
        return PointAcquisitionResult.from_dict(owner.point_results[-1])


class _PointOwner:
    def __init__(self, acquirer):
        self.acquirer = acquirer
        s = acquirer.snapshot
        self.flagImagePV, self.flag_pixel_shape, self.flag_image_extent = s.image_pv, s.pixel_shape, s.extent
        self.flag_image_flip_y, self.roi, self.background_image = s.flip_y, s.roi, s.background
        self.beam_width_method, self.fit_vmin = s.method, s.fit_vmin
        self.flag_name, self.background_status = s.screen, "Applied" if s.background is not None else "Off"
        self.samples, self.settle_time, self.sample_interval = s.samples, s.settle_time, s.sample_interval
        self.scan_strategy, self.effective_k1_limit, self.require_point_quality = "grid", None, True
        self.restore_quad_callback = SimpleNamespace(move=lambda k, check: acquirer.move(k, check))
        self.flagSigxPV = self.flagSigyPV = None
        self.k1l, self.sigxl, self.sigyl, self.point_quality = [], [], [], []
        self.x_quality_usable, self.y_quality_usable, self.point_results = [], [], []
        self.completed_k1_points = 0
        self.trigger = SimpleNamespace(emit=lambda payload: acquirer.progress(payload) if acquirer.progress else None)

    @property
    def is_running(self):
        self.acquirer.check()
        return True

    def _sleep_or_stop(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.acquirer.check()
            time.sleep(min(.05, max(0, end-time.monotonic())))
        self.acquirer.check()
        return True

    def _emit_scan_progress(self, **kwargs):
        self.acquirer.check()

    def _emit_adaptive_status(self, message, **kwargs):
        self.trigger.emit({"method":None, "adaptive_status": message, **kwargs})

    @staticmethod
    def _sample_error(values):
        return float(np.std(values, ddof=1)/np.sqrt(len(values))) if len(values)>1 else None


def solve_beam_matrix(A0, k1l, sigxx, energy_mev, max_condition=1e12):
    determinant = None
    rank = None
    condition_number = None
    residual_rms = None
    solver = "numpy.linalg.lstsq"
    try:
        A = np.asarray(A0, dtype=float).reshape(len(k1l), 3)
        b = np.asarray(sigxx, dtype=float).reshape(-1)
        if A.shape[0] != b.size:
            raise ValueError(
                f"design matrix rows ({A.shape[0]}) do not match measurements ({b.size})"
            )
        if not np.all(np.isfinite(A)) or not np.all(np.isfinite(b)):
            raise ValueError("least-squares input contains non-finite values")

        xx, _reported_residuals, rank_value, singular_values = np.linalg.lstsq(
            A,
            b,
            rcond=None,
        )
        rank = int(rank_value)
        fitted = A @ xx
        residual_rms = (
            float(np.sqrt(np.mean((fitted - b) ** 2)))
            if b.size
            else None
        )
        if singular_values.size and singular_values[-1] > 0:
            condition_number = float(singular_values[0] / singular_values[-1])

        if rank < 3:
            raise ValueError(
                f"rank-deficient fit: rank={rank}/{3}; "
                "scan points do not provide enough independent optics settings"
            )
        if condition_number is None or condition_number > max_condition:
            condition_text = "infinite" if condition_number is None else f"{condition_number:.3e}"
            raise ValueError(
                f"ill-conditioned fit: condition={condition_text} exceeds "
                f"{max_condition:.1e}"
            )

        sig11 = float(xx[0])
        sig12 = float(xx[1])
        sig22 = float(xx[2])

        determinant = float(sig11 * sig22 - sig12**2)
        if not math.isfinite(determinant) or determinant <= 0:
            raise ValueError(f"non-physical beam matrix determinant={determinant:.6g}")
        ex = math.sqrt(determinant)
        beta  = sig11/ex
        alpha = -sig12/ex
        gamma = sig22/ex

        gam0 = energy_mev*1e6/510998.95000
        exn = ex*gam0

        #print("exn,beta,alpha,gamma",exn,beta,alpha,gamma)
        tmp = SimpleNamespace()
        tmp.ex    = round(ex   ,4)
        tmp.exn   = round(exn  ,2)
        tmp.exn_raw = float(exn)
        tmp.beta  = round(beta ,2)
        tmp.alpha = round(alpha,2)
        tmp.gamma = round(gamma,2)
        tmp.status = "valid"
        tmp.message = ""
        tmp.determinant = determinant

    except (ValueError, ZeroDivisionError, np.linalg.LinAlgError) as exc:
        print(f"Warning: least-squares emittance solve failed: {exc}")
        tmp = SimpleNamespace()
        tmp.ex    = None
        tmp.exn   = None
        tmp.beta  = None
        tmp.alpha = None
        tmp.gamma = None
        if "non-physical" in str(exc):
            tmp.status = "non_physical"
        elif "rank-deficient" in str(exc):
            tmp.status = "rank_deficient"
        elif "ill-conditioned" in str(exc):
            tmp.status = "ill_conditioned"
        else:
            tmp.status = "failed"
        tmp.message = str(exc)
        tmp.determinant = determinant

    tmp.rank = rank
    tmp.condition_number = condition_number
    tmp.residual_rms = residual_rms
    tmp.solver = solver

    return tmp


def prepare_measurement(paras, *, model_prepared=False):
    """Bind live model/device state in the worker while it owns control."""
    import epics
    from .optimization import VerifiedQuadRestore
    from half_linac.src.shared.machine_profile import build_model_backend, build_model_snapshot
    image = epics.caget(paras.flagImagePV, timeout=10, use_monitor=False)
    if image is None or np.asarray(image).size != int(np.prod(paras.flag_pixel_shape)):
        raise ValueError(f"{paras.flag_name} image is unavailable or has the wrong dimensions")
    if not model_prepared:
        backend = build_model_backend(paras.app_context, line_name=paras.model_line)
        paras.model_elements = backend.get_line_elements(paras.quad_name, paras.flag_name)
        fields = tuple((str(e["NAME"]), "K1") for e in paras.model_elements
                       if str(e.get("TYPE", "")).upper()=="QUAD" and "K1" in e)
        snapshot = build_model_snapshot(paras.app_context, fields)
        paras.model_snapshot_metadata = snapshot.as_metadata()
        paras.model_lattice_overrides = snapshot.lattice_overrides
    paras.restore_quad = VerifiedQuadRestore(paras.app_context, paras.quad_name)
    paras.scan_metadata["initial_quad"] = {"element": paras.quad_name, "k1": paras.restore_quad.initial_k1,
        "current_set": paras.restore_quad.initial_current, "current_readback": paras.restore_quad.initial_readback}
    paras.scan_metadata["model_snapshot"] = getattr(paras, "model_snapshot_metadata", None)
    paras.defer_measurement_prepare = False
    return paras


def fit_grid_results(config, records):
    """Headless grid reconstruction with the same solver as scanThread."""
    rows, values, scans = [], [], []
    for i, record in enumerate(records):
        measurement = record["measurement"]
        frames = measurement.get("metadata", {}).get("frames", [])
        sigmas = [f["sig"+config.plane] for f in frames if "sig"+config.plane in f]
        if not sigmas:
            sigmas = [math.sqrt(measurement["variance_mm2"])]
        for sigma in sigmas:
            rows.append(config.design[i]); values.append(sigma**2); scans.append(config.scan_values[i])
    result = solve_beam_matrix(rows, scans, values, config.energy_mev)
    if result.status != "valid":
        raise ValueError(f"Full scan is not physical: {result.status}: {result.message}")
    return {"normalized_emittance_mm_mrad": result.exn_raw, "standard_error_mm_mrad": None,
            "condition_number": result.condition_number, "status": result.status,
            "solver": result.solver}


# Explicit strict-frame compatibility policy; the framework uses timed sampling.
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


class VerifiedFrameAcquirer:
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
        from .infobax_session import PointMeasurement
        return PointMeasurement(float(np.mean(values)), noise, {"frames": frames,
                                "frame_count": len(frames), "variance_samples_mm2": values})
