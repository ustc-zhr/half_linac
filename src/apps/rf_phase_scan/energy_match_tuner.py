from __future__ import annotations

import time

import numpy as np
from epics import caget, caput

from .image_acquisition import RFImageAcquisition
from half_linac.src.shared.energy_tuning import (
    BRIGHTNESS_PEAK,
    CENTER_LOCK,
    EnergyStageContext,
    EnergyTuningPipeline,
    StageResult,
    normalize_pipeline,
    pipeline_has,
)


class EnergyMatchCancelled(RuntimeError):
    pass


def reference_x_pixel(x_reference_mm, nx, pixel_width_mm):
    nx = int(nx)
    pixel_width_mm = float(pixel_width_mm)
    half_width_mm = nx * pixel_width_mm / 2
    if nx <= 0 or pixel_width_mm <= 0:
        raise ValueError("Screen width and pixel width must be positive.")
    if not -half_width_mm <= float(x_reference_mm) <= half_width_mm:
        raise ValueError("x_reference_mm lies outside the configured screen.")
    return (float(x_reference_mm) + half_width_mm) / (2 * half_width_mm) * (nx - 1)


def center_out_values(low, high, center, steps):
    """Return a bounded grid ordered from the requested center outwards."""
    values = np.linspace(float(low), float(high), int(steps))
    return tuple(
        float(value)
        for value in sorted(values, key=lambda value: (abs(value - center), value))
    )


class RFPhaseEnergyMatcher:
    """RF-local coordinated-energy matcher driven by fitted screen center."""

    def __init__(
        self,
        *,
        flag_pv_obj,
        flag_pixel,
        bend_pv,
        design_eta_m,
        progress_callback=None,
        remove_bg=False,
        bg_image=None,
        roi=None,
        flip_y=False,
        settle_time_s=1,
        restore_initial_on_failure=True,
        cancel_requested=None,
        restore_initial_on_cancel=True,
        frame_samples=3,
        min_valid_frames=2,
        verification_frame_samples=5,
        verification_min_valid_frames=3,
        frame_interval_s=0.2,
        pixel_width_mm=None,
        profile_fit_method="Gauss fit",
        min_fit_r_squared=0.7,
        x_reference_mm=0,
        center_tolerance_mm=0.2,
        max_iterations=6,
        max_correction_step_mev=25,
        objective="profile_lock",
        pipeline=None,
    ):
        if pixel_width_mm is None:
            raise ValueError("pixel_width_mm is required for energy matching.")
        self.bend_pv = str(bend_pv)
        self.pixel_width_mm = float(pixel_width_mm)
        self.design_eta_m = float(design_eta_m)
        if not np.isfinite(self.design_eta_m) or self.design_eta_m == 0:
            raise ValueError("design_eta_m must be finite and nonzero.")
        self.acquisition = RFImageAcquisition(
            flag_pv_obj,
            tuple(flag_pixel),
            self.pixel_width_mm,
            background=bg_image if remove_bg else None,
            roi=roi,
            flip_y=flip_y,
        )
        self.progress_callback = progress_callback
        self.settle_time_s = float(settle_time_s)
        self.restore_initial_on_failure = bool(restore_initial_on_failure)
        self.cancel_requested = cancel_requested
        self.restore_initial_on_cancel = bool(restore_initial_on_cancel)
        self.frame_samples = int(frame_samples)
        self.min_valid_frames = int(min_valid_frames)
        self.verification_frame_samples = int(verification_frame_samples)
        self.verification_min_valid_frames = int(verification_min_valid_frames)
        self.frame_interval_s = float(frame_interval_s)
        self.profile_fit_method = str(profile_fit_method)
        self.min_fit_r_squared = float(min_fit_r_squared)
        self.x_reference_mm = float(x_reference_mm)
        self.center_tolerance_mm = float(center_tolerance_mm)
        self.max_iterations = int(max_iterations)
        self.max_correction_step_mev = float(max_correction_step_mev)
        self.objective = str(objective).strip()
        self._legacy_objective = pipeline is None
        self.pipeline = normalize_pipeline(pipeline, legacy_objective=self.objective)
        if self.objective not in {"profile_lock", "brightness_then_profile_lock"}:
            raise ValueError("Unsupported energy-match objective.")
        if not 1 <= self.min_valid_frames <= self.frame_samples:
            raise ValueError("min_valid_frames must be between 1 and frame_samples.")
        if not 1 <= self.verification_min_valid_frames <= self.verification_frame_samples:
            raise ValueError(
                "verification_min_valid_frames must be between 1 and verification_frame_samples."
            )
        if self.frame_interval_s < 0 or self.settle_time_s < 0:
            raise ValueError("Sampling and settle intervals must not be negative.")
        if self.center_tolerance_mm <= 0 or self.max_correction_step_mev <= 0:
            raise ValueError("Center tolerance and maximum correction step must be positive.")
        if self.max_iterations < 1:
            raise ValueError("Maximum energy-match iterations must be at least one.")
        if not 0 <= self.min_fit_r_squared <= 1:
            raise ValueError("Minimum Gaussian fit R2 must be in [0, 1].")
        self.initial_energy = None
        self.best_energy = None
        self.center_lock_result = None
        self.pipeline_result = None
        self._pipeline_seed = None
        self.last_message = None
        self.status = "IDLE"

    def _cancelled(self):
        return self.cancel_requested is not None and self.cancel_requested()

    def _raise_if_cancelled(self):
        if self._cancelled():
            raise EnergyMatchCancelled("Energy match stopped by operator.")

    def _wait(self, duration_s, *, allow_cancel=True):
        deadline = time.monotonic() + max(float(duration_s), 0)
        while time.monotonic() < deadline:
            if allow_cancel:
                self._raise_if_cancelled()
            time.sleep(min(0.05, max(deadline - time.monotonic(), 0)))

    def _set_energy(self, energy, *, allow_cancel=True):
        if allow_cancel:
            self._raise_if_cancelled()
        if not caput(self.bend_pv, float(energy), wait=True, timeout=10):
            raise RuntimeError("Coordinated energy setpoint write failed during Energy Match.")
        self._wait(self.settle_time_s, allow_cancel=allow_cancel)

    def _restore(self):
        if self.initial_energy is not None:
            self._set_energy(self.initial_energy, allow_cancel=False)
            self._report("restore", self.initial_energy, False)

    def _report(self, stage, energy, has_beam, **details):
        if self.progress_callback is not None:
            self.progress_callback(
                {"stage": stage, "energy_mev": float(energy), "has_beam": bool(has_beam), **details}
            )

    def _measure(self, energy, stage, *, verification=False):
        samples = self.verification_frame_samples if verification else self.frame_samples
        min_valid = (
            self.verification_min_valid_frames if verification else self.min_valid_frames
        )
        try:
            result = self.acquisition.sample_profile(
                samples=samples,
                min_valid=min_valid,
                interval_s=self.frame_interval_s,
                fit_method=self.profile_fit_method,
                cancel_requested=self._cancelled,
                allow_direct_fallback=False,
                min_fit_r_squared=self.min_fit_r_squared,
            )
        except InterruptedError as exc:
            raise EnergyMatchCancelled(str(exc)) from exc
        if result is None:
            self._report(stage, energy, False, valid_frames=0, total_frames=samples)
            return None
        result.update(
            energy=float(energy),
            offset_mm=float(result["center_mm"] - self.x_reference_mm),
        )
        self._report(
            stage,
            energy,
            True,
            center_mm=result["center_mm"],
            center_offset_mm=result["offset_mm"],
            brightness=result["brightness"],
            valid_frames=result["valid_frames"],
            total_frames=samples,
            fit_method=result["fit_method"],
            fit_r_squared=result["fit_r_squared"],
        )
        return result

    def _at(self, energy, stage, *, verification=False):
        self._set_energy(energy)
        return self._measure(energy, stage, verification=verification)

    @staticmethod
    def _secant_energy(first, second):
        delta_offset = second["offset_mm"] - first["offset_mm"]
        if abs(delta_offset) <= np.finfo(float).eps:
            return None
        return float(
            second["energy"]
            - second["offset_mm"]
            * (second["energy"] - first["energy"])
            / delta_offset
        )

    def _predicted_energy(self, measurement):
        # ENY convention: E_beam = E_set * (1 + dx / eta).
        correction = (
            measurement["energy"]
            * measurement["offset_mm"]
            * 1e-3
            / self.design_eta_m
        )
        correction = float(
            np.clip(correction, -self.max_correction_step_mev, self.max_correction_step_mev)
        )
        return float(measurement["energy"] + correction)

    def _reacquire(self, low, high, center, steps):
        candidates = []
        self._report(
            "reacquire_range", center, False,
            range_min=float(low), range_max=float(high), points=int(steps),
        )
        for energy in center_out_values(low, high, center, steps):
            measurement = self._at(energy, "reacquire")
            if measurement is not None:
                candidates.append(measurement)
                if (
                    not pipeline_has(self.pipeline, CENTER_LOCK)
                    and abs(measurement["offset_mm"]) <= self.center_tolerance_mm
                ):
                    break
        if not candidates:
            return None
        if pipeline_has(self.pipeline, BRIGHTNESS_PEAK):
            # Use the brightest reliable beam as the seed, then lock its fitted center.
            return max(candidates, key=lambda item: item["brightness"])
        return min(candidates, key=lambda item: abs(item["offset_mm"]))

    def _track_center(self, seed, low, high):
        measurements = [seed]
        current = seed
        for _iteration in range(self.max_iterations):
            if abs(current["offset_mm"]) <= self.center_tolerance_mm:
                break
            candidate_energy = None
            if len(measurements) >= 2:
                candidate_energy = self._secant_energy(measurements[-2], measurements[-1])
            if candidate_energy is None or not np.isfinite(candidate_energy):
                candidate_energy = self._predicted_energy(current)
            candidate_energy = float(np.clip(candidate_energy, low, high))
            if abs(candidate_energy - current["energy"]) <= 1e-9:
                break
            candidate = self._at(candidate_energy, "correct")
            if candidate is None:
                self.last_message = "Beam profile was lost during center correction."
                return None
            measurements.append(candidate)
            current = candidate
        if abs(current["offset_mm"]) > self.center_tolerance_mm:
            best = min(measurements, key=lambda item: abs(item["offset_mm"]))
            self.last_message = (
                "Energy Match did not reach the center tolerance; "
                f"best dx={best['offset_mm']:+.3f} mm."
            )
            return None
        verified = self._at(current["energy"], "verify", verification=True)
        if verified is None or abs(verified["offset_mm"]) > self.center_tolerance_mm:
            self.last_message = "Final profile verification did not meet center tolerance."
            return None
        self.center_lock_result = {
            "seed_energy": float(seed["energy"]),
            "final_energy": float(verified["energy"]),
            "final_offset_mm": float(verified["offset_mm"]),
            "brightness": float(verified["brightness"]),
            "valid_frames": int(verified["valid_frames"]),
            "measurements": len(measurements),
            "fit_method": verified["fit_method"],
            "fit_r_squared": verified["fit_r_squared"],
        }
        return float(verified["energy"])

    def _run_brightness_peak_stage(self, low, high, config):
        points = int(config.get("points", config.get("reacquire_points", 9)))
        strategy = str(config.get("strategy", "center_outward")).strip()
        if strategy != "center_outward":
            return StageResult(
                False,
                None,
                f"Unsupported RF brightness-peak strategy: {strategy!r}.",
            )
        seed = self._reacquire(low, high, self._pipeline_start_energy, points)
        if seed is None:
            # Preserve the existing retry behavior when the first reacquire
            # pass loses the beam or returns no usable profile.
            seed = self._reacquire(low, high, self._pipeline_start_energy, points)
        if seed is None:
            return StageResult(
                False,
                None,
                self.last_message or "No valid beam profile was found in the Energy Match window.",
            )
        self._pipeline_seed = seed
        return StageResult(
            True,
            float(seed["energy"]),
            diagnostics={
                "brightness": float(seed["brightness"]),
                "center_offset_mm": float(seed["offset_mm"]),
                "exploration_strategy": strategy,
                "exploration_points": points,
            },
        )

    def _run_center_lock_stage(self, seed_energy, low, high, config):
        strategy = str(config.get("strategy", "secant_dispersion")).strip()
        if strategy != "secant_dispersion":
            return StageResult(
                False,
                None,
                f"Unsupported RF center-lock strategy: {strategy!r}.",
            )
        seed = self._pipeline_seed
        if seed is None or abs(float(seed["energy"]) - float(seed_energy)) > 1e-9:
            seed = self._at(float(seed_energy), "track")
        if seed is None:
            return StageResult(
                False,
                None,
                self.last_message or "No valid beam profile was found for center lock.",
            )
        matched = self._track_center(seed, low, high)
        if matched is None:
            return StageResult(
                False,
                None,
                self.last_message or "Energy Match center lock failed.",
            )
        return StageResult(
            True,
            float(matched),
            diagnostics={"strategy": strategy, **dict(self.center_lock_result or {})},
        )

    def _run_explicit_pipeline(
        self, low, high, start_energy, brightness_peak_config, center_lock_config
    ):
        self._pipeline_start_energy = float(start_energy)
        context = EnergyStageContext(
            low=float(low),
            high=float(high),
            initial_value=float(start_energy),
            config={
                "brightness_peak": dict(brightness_peak_config),
                "center_lock": dict(center_lock_config),
            },
            brightness_peak=self._run_brightness_peak_stage,
            center_lock=self._run_center_lock_stage,
        )
        return EnergyTuningPipeline(self.pipeline).run(context)

    def run(self, B_min, B_max, *, start_energy, tracking_reacquire_points=9,
            brightness_peak_config=None, center_lock_config=None):
        low, high = float(B_min), float(B_max)
        if low >= high:
            raise ValueError("Energy Match bounds must contain low < high.")
        self.status = "RUNNING"
        self.last_message = None
        self.center_lock_result = None
        self.pipeline_result = None
        self._pipeline_seed = None
        initial = caget(self.bend_pv)
        if initial is None or not np.isfinite(float(initial)):
            self.status = "FAILED"
            self.last_message = "Could not read the initial coordinated energy setpoint."
            return None
        self.initial_energy = float(initial)
        center = float(np.clip(float(start_energy), low, high))
        try:
            if self._legacy_objective:
                if pipeline_has(self.pipeline, BRIGHTNESS_PEAK):
                    seed = self._reacquire(low, high, center, tracking_reacquire_points)
                else:
                    seed = self._at(center, "track")
                if seed is None and pipeline_has(self.pipeline, BRIGHTNESS_PEAK):
                    seed = self._reacquire(low, high, center, tracking_reacquire_points)
                if seed is None:
                    self.last_message = "No valid beam profile was found in the Energy Match window."
                    raise RuntimeError(self.last_message)
                if pipeline_has(self.pipeline, CENTER_LOCK):
                    matched = self._track_center(seed, low, high)
                else:
                    matched = float(seed["energy"])
                    self._report("final", matched, True, **seed)
            else:
                self.pipeline_result = self._run_explicit_pipeline(
                    low,
                    high,
                    center,
                    brightness_peak_config or {
                        "strategy": "center_outward",
                        "points": tracking_reacquire_points,
                    },
                    center_lock_config or {"strategy": "secant_dispersion"},
                )
                if not self.pipeline_result.ok or self.pipeline_result.actuator_value is None:
                    raise RuntimeError(
                        self.pipeline_result.message or "Energy Match pipeline failed."
                    )
                matched = float(self.pipeline_result.actuator_value)
            if matched is None:
                raise RuntimeError(self.last_message or "Energy Match failed.")
            self.best_energy = matched
            self.status = "DONE"
            self._report("final", matched, True, **(self.center_lock_result or {}))
            return matched
        except EnergyMatchCancelled:
            self.status = "CANCELLED"
            self.last_message = "Energy Match stopped by operator."
            if self.restore_initial_on_cancel:
                self._restore()
            return None
        except RuntimeError:
            self.status = "FAILED"
            if self.restore_initial_on_failure:
                self._restore()
            return None
        except Exception:
            self.status = "FAILED"
            if self.restore_initial_on_failure:
                self._restore()
            raise

    def get_last_status(self):
        return self.status

    def get_last_message(self):
        return self.last_message
