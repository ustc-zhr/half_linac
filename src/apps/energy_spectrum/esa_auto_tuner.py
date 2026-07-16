import time
import numpy as np
from skimage import measure
from epics import PV, caget, caput

from half_linac.src.apps.energy_spectrum.spectrum_profile import (
    SpectrumProfileError,
    fit_projection_profile,
    project_image_profiles,
)


class ESAAutoTuneCancelled(RuntimeError):
    """Raised internally when an operator requests a cooperative scan stop."""


def reference_x_pixel(x_reference_mm, nx, pixel_width_mm):
    """Map calibrated screen x to the pixel convention used by spectrum fitting."""
    nx = int(nx)
    pixel_width_mm = float(pixel_width_mm)
    if nx <= 0 or pixel_width_mm <= 0:
        raise ValueError("Screen width and pixel width must be positive.")
    x_reference_mm = float(x_reference_mm)
    half_width_mm = nx * pixel_width_mm / 2.0
    if not -half_width_mm <= x_reference_mm <= half_width_mm:
        raise ValueError(
            f"x_reference_mm={x_reference_mm:g} lies outside the screen range "
            f"[-{half_width_mm:g}, {half_width_mm:g}] mm."
        )
    return (x_reference_mm + half_width_mm) / (2.0 * half_width_mm) * (nx - 1)


class ESA_AutoTuner:
    """
    ESA automatic energy tuning module
    Designed for:
      - CCD FLAG
      - single pulse per frame
      - unknown beam energy
    """

    def __init__(self,
                 flag_pv_obj,
                 flag_pixel,
                 bend_pv,
                 mode="find_beam",
                 progress_callback=None,
                 remove_bg=False,
                 bg_image=None,
                 settle_time_s=0.5,
                 restore_initial_on_failure=True,
                 cancel_requested=None,
                 restore_initial_on_cancel=True,
                 target_x_pixel=None,
                 frame_samples=3,
                 min_valid_frames=2,
                 frame_interval_s=0.2,
                 brightness_fraction=0.4,
                 max_center_spread_pixel=np.inf,
                 target_tolerance_pixel=np.inf,
                 min_fit_correlation=0.7,
                 pixel_width_mm=None,
                 profile_fit_method="Gauss fit",
                 x_reference_mm=0.0,
                 center_step=0.05,
                 center_max_total_offset=1.0,
                 center_tolerance_mm=0.2):
        """
        Parameters
        ----------
        flag_pv_obj : epics.PV
            PV object for FLAG image
        flag_pixel : (nx, ny)
            FLAG pixel resolution  
        bend_pv : str
            EPICS PV name of ESA dipole current
        """
        self.flag_pv_obj = flag_pv_obj
        self.flag_pixel = flag_pixel
        self.bend_pv = bend_pv

        self.mode = str(mode).strip()
        if self.mode not in {
            "find_beam",
            "center_lock",
            "center_x_reference",
            "brightness_gated_x_fit",
            "brightness_then_profile_lock",
        }:
            raise ValueError(f"Unsupported ESA auto-tune mode: {self.mode!r}.")
        if target_x_pixel is None:
            target_x_pixel = (self.flag_pixel[0] - 1) / 2.0
        self.target_x_pixel = float(target_x_pixel)
        if not 0 <= self.target_x_pixel <= self.flag_pixel[0] - 1:
            raise ValueError("target_x_pixel must lie inside the flag image.")
        self.progress_callback = progress_callback
        self.remove_bg = remove_bg
        self.bg_image = bg_image
        self.settle_time_s = float(settle_time_s)
        self.restore_initial_on_failure = bool(restore_initial_on_failure)
        self.cancel_requested = cancel_requested
        self.restore_initial_on_cancel = bool(restore_initial_on_cancel)
        self.frame_samples = int(frame_samples)
        self.min_valid_frames = int(min_valid_frames)
        self.frame_interval_s = float(frame_interval_s)
        self.brightness_fraction = float(brightness_fraction)
        self.max_center_spread_pixel = float(max_center_spread_pixel)
        self.target_tolerance_pixel = float(target_tolerance_pixel)
        self.min_fit_correlation = float(min_fit_correlation)
        self.pixel_width_mm = None if pixel_width_mm is None else float(pixel_width_mm)
        self.profile_fit_method = str(profile_fit_method).strip()
        self.x_reference_mm = float(x_reference_mm)
        self.center_step = float(center_step)
        self.center_max_total_offset = float(center_max_total_offset)
        self.center_tolerance_mm = float(center_tolerance_mm)
        if self.frame_samples < 1:
            raise ValueError("frame_samples must be at least 1.")
        if not 1 <= self.min_valid_frames <= self.frame_samples:
            raise ValueError("min_valid_frames must be between 1 and frame_samples.")
        if not np.isfinite(self.frame_interval_s) or self.frame_interval_s < 0:
            raise ValueError("frame_interval_s must not be negative.")
        if not np.isfinite(self.brightness_fraction) or not 0 < self.brightness_fraction <= 1:
            raise ValueError("brightness_fraction must be in (0, 1].")
        if np.isnan(self.max_center_spread_pixel) or self.max_center_spread_pixel <= 0:
            raise ValueError("max_center_spread_pixel must be positive.")
        if np.isnan(self.target_tolerance_pixel) or self.target_tolerance_pixel <= 0:
            raise ValueError("target_tolerance_pixel must be positive.")
        if (
            not np.isfinite(self.min_fit_correlation)
            or not 0 <= self.min_fit_correlation <= 1
        ):
            raise ValueError("min_fit_correlation must be in [0, 1].")
        if self.mode == "brightness_then_profile_lock":
            if self.pixel_width_mm is None or not np.isfinite(self.pixel_width_mm):
                raise ValueError("pixel_width_mm is required for fitted-center lock.")
            if self.pixel_width_mm <= 0:
                raise ValueError("pixel_width_mm must be positive.")
            for name, value in (
                ("center_step", self.center_step),
                ("center_max_total_offset", self.center_max_total_offset),
                ("center_tolerance_mm", self.center_tolerance_mm),
            ):
                if not np.isfinite(value) or value <= 0:
                    raise ValueError(f"{name} must be positive and finite.")

        self.best_current = None
        self.best_center_offset_px = None
        self.initial_current = None
        self.coarse_observations = []
        self.fine_observations = []
        self.hybrid_fit = None
        self.hybrid_peak_brightness = None
        self.best_brightness = None
        self.center_lock_result = None
        self.last_profile_diagnostic = None
        self.last_message = None
        self.status = "IDLE"

    # ==========================================================
    # Low-level helpers
    # ==========================================================
    def _raise_if_cancelled(self):
        if self.cancel_requested is not None and self.cancel_requested():
            raise ESAAutoTuneCancelled("ESA auto tune stopped by operator.")

    def _wait(self, duration_s, *, allow_cancel=True):
        deadline = time.monotonic() + max(float(duration_s), 0.0)
        while True:
            if allow_cancel:
                self._raise_if_cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 0.05))

    def _wait_for_settle(self, *, allow_cancel=True):
        self._wait(self.settle_time_s, allow_cancel=allow_cancel)

    def _set_bend(self, current, *, allow_cancel=True):
        if allow_cancel:
            self._raise_if_cancelled()
        caput(self.bend_pv, float(current))
        self._wait_for_settle(allow_cancel=allow_cancel)

    def _read_bend(self):
        value = caget(self.bend_pv)
        if value is None:
            return None
        return float(value)

    def _restore_initial_bend(self):
        if self.initial_current is None:
            return
        self._set_bend(self.initial_current, allow_cancel=False)
        self._report_progress("restore", self.initial_current, has_beam=False, score=None)

    def _get_flag_image(self):
        tmp = self.flag_pv_obj.get()
        img = np.reshape(
            list(map(float, tmp)),
            (self.flag_pixel[1], self.flag_pixel[0])
        )

        if self.remove_bg and self.bg_image is not None:
            img = img - self.bg_image
            img[img < 0] = 0

        return img

    def _report_progress(self, stage, current, *, has_beam, score=None, cx=None):
        if self.progress_callback is None:
            return
        payload = {
            "stage": stage,
            "current": float(current),
            "has_beam": bool(has_beam),
            "score": None if score is None else float(score),
        }
        if cx is not None:
            payload["center_x_pixel"] = float(cx)
            payload["center_offset_pixel"] = float(cx - self.target_x_pixel)
        self.progress_callback(payload)

    def _report_fine_range(self, start, stop, points):
        if self.progress_callback is None:
            return
        spacing = 0.0 if points <= 1 else (float(stop) - float(start)) / (points - 1)
        self.progress_callback(
            {
                "stage": "fine_range",
                "current": float(start),
                "has_beam": False,
                "range_min": float(start),
                "range_max": float(stop),
                "points": int(points),
                "spacing": float(spacing),
            }
        )

    def _detect_beam(self, img):
        """
        Robust single-shot beam detection
        Returns
        -------
        has_beam : bool
        score    : float
        cx       : float or None
        """
        # 找出图像中显著高于背景的亮点区域（6σ原则）
        thr = np.mean(img) + 6 * np.std(img)
        binary = img > thr

        # 将显著亮点分成独立连通区域，再按所选模式确定候选束斑。
        labels = measure.label(binary)
        regions = measure.regionprops(labels)
        if not regions:
            return False, 0.0, None
        def valid_region(candidate):
            # 面积适中（50 ~ 100,000 像素），且不能太细长（长宽比 ≤ 6）。
            if candidate.area < 50 or candidate.area > 1e5:
                return False
            major_axis_length = getattr(candidate, "axis_major_length", None)
            minor_axis_length = getattr(candidate, "axis_minor_length", None)
            if major_axis_length is None or minor_axis_length is None:
                major_axis_length = candidate.major_axis_length
                minor_axis_length = candidate.minor_axis_length
            return major_axis_length / max(minor_axis_length, 1) <= 6

        if self.mode == "brightness_gated_x_fit":
            # Anchor the position fit to the brightest valid connected spot. A larger,
            # dimmer noise island elsewhere in the image must not provide the center.
            valid_regions = [candidate for candidate in regions if valid_region(candidate)]
            if not valid_regions:
                return False, 0.0, None
            region = max(
                valid_regions,
                key=lambda candidate: float(np.sum(img[labels == candidate.label])),
            )
        else:
            region = max(regions, key=lambda candidate: candidate.area)
            if not valid_region(region):
                return False, 0.0, None

        # -----------------------------
        # beam properties
        # -----------------------------
        if self.mode == "brightness_gated_x_fit":
            region_mask = labels == region.label
            raw_score = float(np.sum(img[region_mask]))
            region_y, region_x = np.nonzero(region_mask)
            weights = img[region_y, region_x]
            weight_sum = float(np.sum(weights))
            if not np.isfinite(weight_sum) or weight_sum <= 0:
                return False, 0.0, None
            cx = float(np.average(region_x, weights=weights))
        else:
            raw_score = np.sum(img[binary])
            cx = region.centroid[1]   # x = dispersion direction

        # -----------------------------
        # scoring
        # -----------------------------
        if self.mode in {"center_lock", "center_x_reference"}:
            # Beam brightness/shape determines whether this is a valid beam.
            # Once valid, distance to the calibrated target is the primary objective.
            score = -abs(cx - self.target_x_pixel)
        else:
            score = raw_score

        return True, score, cx

    def _sample_frames(self):
        observations = []
        for sample_index in range(self.frame_samples):
            self._raise_if_cancelled()
            img = self._get_flag_image()
            self._raise_if_cancelled()
            has_beam, score, cx = self._detect_beam(img)
            if has_beam:
                observations.append((float(score), float(cx)))
            if sample_index + 1 < self.frame_samples:
                self._wait(self.frame_interval_s)
        return observations

    def _stable_frame_summary(self, observations):
        if len(observations) < self.min_valid_frames:
            return None
        scores = np.asarray([item[0] for item in observations], dtype=float)
        centers = np.asarray([item[1] for item in observations], dtype=float)
        if np.ptp(centers) > self.max_center_spread_pixel:
            return None
        return float(np.median(scores)), float(np.median(centers))

    # ==========================================================
    # Scan stages
    # ==========================================================
    def coarse_scan(self, B_min, B_max, n_steps=40):
        self.status = "COARSE_SCAN"
        hits = []
        scan_values = np.linspace(B_min, B_max, n_steps)
        self.coarse_observations = []

        for B in scan_values:
            self._raise_if_cancelled()
            self._set_bend(B)
            img = self._get_flag_image()
            self._raise_if_cancelled()
            has_beam, score, cx = self._detect_beam(img)
            self._report_progress(
                "coarse", B, has_beam=has_beam, score=score, cx=cx
            )
            self.coarse_observations.append(
                {
                    "energy": float(B),
                    "has_beam": bool(has_beam),
                    "brightness": float(score) if has_beam else None,
                    "center_x_pixel": float(cx) if cx is not None else None,
                }
            )

            if has_beam:
                hits.append((float(B), float(score)))
                if self.mode in {"find_beam", "brightness_then_profile_lock"} and len(hits) >= 3:
                    break

        if not hits:
            return None

        if self.mode in {"center_lock", "center_x_reference"}:
            best_coarse = max(hits, key=lambda item: item[1])[0]
            coarse_step = abs(float(scan_values[1]) - float(scan_values[0]))
            return (
                max(float(B_min), best_coarse - coarse_step),
                min(float(B_max), best_coarse + coarse_step),
            )

        if self.mode == "brightness_gated_x_fit":
            peak_index = max(
                range(len(self.coarse_observations)),
                key=lambda index: (
                    self.coarse_observations[index]["brightness"]
                    if self.coarse_observations[index]["brightness"] is not None
                    else -np.inf
                ),
            )
            peak_brightness = self.coarse_observations[peak_index]["brightness"]
            gate = self.brightness_fraction * peak_brightness
            left = peak_index
            right = peak_index
            while left > 0:
                brightness = self.coarse_observations[left - 1]["brightness"]
                if brightness is None or brightness < gate:
                    break
                left -= 1
            while right + 1 < len(self.coarse_observations):
                brightness = self.coarse_observations[right + 1]["brightness"]
                if brightness is None or brightness < gate:
                    break
                right += 1
            # Keep one point of margin so the local x(E) fit can interpolate the target.
            left = max(0, left - 1)
            right = min(len(scan_values) - 1, right + 1)
            if left == right:
                left = max(0, left - 1)
                right = min(len(scan_values) - 1, right + 1)
            return float(scan_values[left]), float(scan_values[right])

        hit_values = [item[0] for item in hits]
        return min(hit_values), max(hit_values)

    def fine_scan(self, B1, B2, n_steps=15):
        if self.mode == "brightness_gated_x_fit":
            return self._brightness_gated_x_fit(B1, B2, n_steps)

        self.status = "FINE_SCAN"
        best_B = None
        best_score = -np.inf

        for B in np.linspace(B1, B2, n_steps):
            self._raise_if_cancelled()
            self._set_bend(B)

            if self.mode == "brightness_then_profile_lock":
                observations = self._sample_frames()
                scores = [item[0] for item in observations]
                centers = [item[1] for item in observations]
            else:
                scores = []
                centers = []
                for _ in range(3):  # median over 3 pulses
                    img = self._get_flag_image()
                    self._raise_if_cancelled()
                    has_beam, score, cx = self._detect_beam(img)
                    if has_beam:
                        scores.append(score)
                        centers.append(cx)

            if not scores:
                self._report_progress("fine", B, has_beam=False, score=None)
                continue

            score_med = np.median(scores)
            center_med = np.median(centers)
            self._report_progress(
                "fine", B, has_beam=True, score=score_med, cx=center_med
            )
            if score_med > best_score:
                best_score = score_med
                best_B = B
                self.best_brightness = float(score_med)
                self.best_center_offset_px = float(center_med - self.target_x_pixel)

        return best_B

    @staticmethod
    def _robust_linear_fit(energies, centers):
        energies = np.asarray(energies, dtype=float)
        centers = np.asarray(centers, dtype=float)
        keep = np.isfinite(energies) & np.isfinite(centers)
        if np.count_nonzero(keep) < 3:
            return None

        for _ in range(4):
            fit_energies = energies[keep]
            fit_centers = centers[keep]
            if np.ptp(fit_energies) <= 0:
                return None
            slope, intercept = np.polyfit(fit_energies, fit_centers, 1)
            residuals = centers - (slope * energies + intercept)
            fit_residuals = residuals[keep]
            median_residual = np.median(fit_residuals)
            mad = np.median(np.abs(fit_residuals - median_residual))
            residual_limit = max(3.0 * 1.4826 * mad, 1.0)
            refined = keep & (np.abs(residuals - median_residual) <= residual_limit)
            if np.count_nonzero(refined) < 3:
                return None
            if np.array_equal(refined, keep):
                break
            keep = refined

        fit_energies = energies[keep]
        fit_centers = centers[keep]
        slope, intercept = np.polyfit(fit_energies, fit_centers, 1)
        energy_scale = np.std(fit_energies)
        center_scale = np.std(fit_centers)
        if not np.isfinite(slope) or abs(slope) <= np.finfo(float).eps:
            return None
        if energy_scale <= np.finfo(float).eps or center_scale <= np.finfo(float).eps:
            return None
        correlation = float(np.corrcoef(fit_energies, fit_centers)[0, 1])
        if not np.isfinite(correlation):
            return None
        return {
            "slope_pixel_per_unit": float(slope),
            "intercept_pixel": float(intercept),
            "correlation": correlation,
            "points_used": int(np.count_nonzero(keep)),
            "keep_mask": keep,
        }

    def _brightness_gated_x_fit(self, B1, B2, n_steps):
        self.status = "FINE_SCAN"
        self.fine_observations = []

        for energy in np.linspace(B1, B2, n_steps):
            self._raise_if_cancelled()
            self._set_bend(energy)
            summary = self._stable_frame_summary(self._sample_frames())
            if summary is None:
                self._report_progress("fine", energy, has_beam=False, score=None)
                self.fine_observations.append(
                    {
                        "energy": float(energy),
                        "brightness": None,
                        "center_x_pixel": None,
                    }
                )
                continue
            brightness, center = summary
            self._report_progress(
                "fine", energy, has_beam=True, score=brightness, cx=center
            )
            self.fine_observations.append(
                {
                    "energy": float(energy),
                    "brightness": brightness,
                    "center_x_pixel": center,
                }
            )

        valid = [item for item in self.fine_observations if item["brightness"] is not None]
        if len(valid) < 3:
            self.last_message = "Hybrid fit found fewer than three stable beam points."
            return None
        self.hybrid_peak_brightness = max(item["brightness"] for item in valid)
        brightness_gate = self.brightness_fraction * self.hybrid_peak_brightness
        trusted = [item for item in valid if item["brightness"] >= brightness_gate]
        if len(trusted) < 3:
            self.last_message = "Hybrid brightness gate retained fewer than three points."
            return None

        energies = np.asarray([item["energy"] for item in trusted], dtype=float)
        centers = np.asarray([item["center_x_pixel"] for item in trusted], dtype=float)
        fit = self._robust_linear_fit(energies, centers)
        if fit is None:
            self.last_message = "Beam center did not move consistently with scanned energy."
            return None
        if abs(fit["correlation"]) < self.min_fit_correlation:
            self.last_message = (
                "Beam-center/energy correlation is too weak "
                f"({fit['correlation']:+.3f}; need |r| >= {self.min_fit_correlation:.3f})."
            )
            return None

        keep = fit.pop("keep_mask")
        used_energies = energies[keep]
        solved_energy = (
            self.target_x_pixel - fit["intercept_pixel"]
        ) / fit["slope_pixel_per_unit"]
        if (
            not np.isfinite(solved_energy)
            or solved_energy < np.min(used_energies)
            or solved_energy > np.max(used_energies)
        ):
            self.last_message = (
                "The calibrated x reference lies outside the trustworthy beam-fit range."
            )
            return None

        fit["solved_energy"] = float(solved_energy)
        fit["brightness_gate"] = float(brightness_gate)
        self.hybrid_fit = fit
        return float(solved_energy)

    def _verify_hybrid_solution(self, energy):
        summary = self._stable_frame_summary(self._sample_frames())
        if summary is None:
            self.last_message = "Final verification did not find a stable beam."
            self._report_progress("verify", energy, has_beam=False, score=None)
            return False
        brightness, center = summary
        self.best_center_offset_px = float(center - self.target_x_pixel)
        self._report_progress(
            "verify", energy, has_beam=True, score=brightness, cx=center
        )
        if brightness < self.brightness_fraction * self.hybrid_peak_brightness:
            self.last_message = "Final beam brightness fell below the trusted fit gate."
            return False
        if abs(self.best_center_offset_px) > self.target_tolerance_pixel:
            self.last_message = (
                "Final beam center missed the calibrated x reference by "
                f"{self.best_center_offset_px:+.2f} pixels."
            )
            return False
        return True

    def _center_mm_to_pixel(self, center_mm):
        half_width_mm = self.flag_pixel[0] * self.pixel_width_mm / 2.0
        return (
            (float(center_mm) + half_width_mm)
            / (2.0 * half_width_mm)
            * (self.flag_pixel[0] - 1)
        )

    def _measure_profile_center(self, energy, stage):
        centers_mm = []
        projection_strengths = []
        fit_methods = []
        fit_qualities = []
        fit_errors = []

        for sample_index in range(self.frame_samples):
            self._raise_if_cancelled()
            image = self._get_flag_image()
            self._raise_if_cancelled()
            try:
                projection = project_image_profiles(image, self.pixel_width_mm)
                profile_fit = fit_projection_profile(
                    projection.x_mm,
                    projection.density_x,
                    self.profile_fit_method,
                    allow_direct_fallback=True,
                )
            except SpectrumProfileError as exc:
                fit_errors.append(str(exc))
            else:
                centers_mm.append(float(profile_fit.center_mm))
                projection_strengths.append(float(np.sum(projection.density_x)))
                fit_methods.append(profile_fit.method)
                if profile_fit.r_squared is not None:
                    fit_qualities.append(float(profile_fit.r_squared))
            if sample_index + 1 < self.frame_samples:
                self._wait(self.frame_interval_s)

        if len(centers_mm) < self.min_valid_frames:
            detail = fit_errors[-1] if fit_errors else "insufficient fitted frames"
            self.last_profile_diagnostic = (
                f"{len(centers_mm)}/{self.frame_samples} fitted frames: {detail}"
            )
            self._report_progress(stage, energy, has_beam=False, score=None)
            return None
        center_spread_mm = float(np.ptp(centers_mm))

        center_mm = float(np.median(centers_mm))
        projection_strength = float(np.median(projection_strengths))
        self.last_profile_diagnostic = None
        center_pixel = self._center_mm_to_pixel(center_mm)
        self._report_progress(
            stage,
            energy,
            has_beam=True,
            score=projection_strength,
            cx=center_pixel,
        )
        return {
            "energy": float(energy),
            "center_mm": center_mm,
            "offset_mm": center_mm - self.x_reference_mm,
            "projection_strength": projection_strength,
            "center_spread_mm": center_spread_mm,
            "fit_method": fit_methods[0],
            "fit_r_squared": (
                float(np.median(fit_qualities)) if fit_qualities else None
            ),
        }

    def _interpolate_center_target(self, left, right):
        center_delta = right["center_mm"] - left["center_mm"]
        if abs(center_delta) <= np.finfo(float).eps:
            return None
        return float(
            left["energy"]
            + (self.x_reference_mm - left["center_mm"])
            * (right["energy"] - left["energy"])
            / center_delta
        )

    def _profile_measurement_at(self, energy, stage):
        self._set_bend(energy)
        return self._measure_profile_center(energy, stage)

    def _run_profile_center_lock(self, seed_energy, scan_min, scan_max):
        self.status = "CENTER_LOCK"
        local_min = max(float(scan_min), seed_energy - self.center_max_total_offset)
        local_max = min(float(scan_max), seed_energy + self.center_max_total_offset)
        measurements = []

        seed = self._profile_measurement_at(seed_energy, "center_seed")
        if seed is None:
            self.last_message = (
                "Brightness peak was found, but its projected profile could not be fitted: "
                f"{self.last_profile_diagnostic}."
            )
            return None
        measurements.append(seed)
        converged = seed if abs(seed["offset_mm"]) <= self.center_tolerance_mm else None
        direction = 0
        previous = seed
        if converged is None:
            for candidate_direction in (1, -1):
                candidate_energy = seed_energy + candidate_direction * self.center_step
                if not local_min <= candidate_energy <= local_max:
                    continue
                candidate = self._profile_measurement_at(candidate_energy, "center_step")
                if candidate is None:
                    continue
                measurements.append(candidate)
                if abs(candidate["offset_mm"]) <= self.center_tolerance_mm:
                    converged = candidate
                    direction = candidate_direction
                    previous = candidate
                    break
                if seed["offset_mm"] * candidate["offset_mm"] < 0:
                    interpolated_energy = self._interpolate_center_target(seed, candidate)
                    if interpolated_energy is not None:
                        converged = self._profile_measurement_at(
                            interpolated_energy,
                            "center_lock",
                        )
                        if (
                            converged is not None
                            and abs(converged["offset_mm"]) > self.center_tolerance_mm
                        ):
                            converged = None
                    direction = candidate_direction
                    previous = candidate
                    break
                if abs(candidate["offset_mm"]) < abs(seed["offset_mm"]):
                    direction = candidate_direction
                    previous = candidate
                    break

        max_steps = max(
            1,
            int(np.ceil(self.center_max_total_offset / self.center_step)),
        )
        steps = 1 if direction else 0
        while converged is None and direction and steps < max_steps:
            steps += 1
            candidate_energy = seed_energy + direction * steps * self.center_step
            if not local_min <= candidate_energy <= local_max:
                break
            candidate = self._profile_measurement_at(candidate_energy, "center_lock")
            if candidate is None:
                self.last_message = (
                    "Projected profile fitting failed during center search: "
                    f"{self.last_profile_diagnostic}."
                )
                return None
            measurements.append(candidate)
            if abs(candidate["offset_mm"]) <= self.center_tolerance_mm:
                converged = candidate
                break
            if previous["offset_mm"] * candidate["offset_mm"] < 0:
                interpolated_energy = self._interpolate_center_target(previous, candidate)
                if interpolated_energy is not None:
                    interpolated = self._profile_measurement_at(
                        interpolated_energy,
                        "center_lock",
                    )
                    if (
                        interpolated is not None
                        and abs(interpolated["offset_mm"]) <= self.center_tolerance_mm
                    ):
                        converged = interpolated
                break
            if abs(candidate["offset_mm"]) >= abs(previous["offset_mm"]):
                break
            previous = candidate

        if converged is None:
            best = min(measurements, key=lambda item: abs(item["offset_mm"]))
            self.last_message = (
                "Simple fitted-center search did not reach x_reference_mm; "
                f"best dx={best['offset_mm']:+.3f} mm."
            )
            return None

        self._set_bend(converged["energy"])
        verified = self._measure_profile_center(converged["energy"], "verify")
        if verified is None:
            self.last_message = "Final fitted-profile verification did not find a stable beam."
            return None
        if abs(verified["offset_mm"]) > self.center_tolerance_mm:
            self.last_message = (
                "Final fitted-profile center missed x_reference_mm by "
                f"{verified['offset_mm']:+.3f} mm."
            )
            return None

        self.best_center_offset_px = (
            self._center_mm_to_pixel(verified["center_mm"]) - self.target_x_pixel
        )
        self.center_lock_result = {
            "seed_energy": float(seed_energy),
            "final_energy": float(verified["energy"]),
            "final_offset_mm": float(verified["offset_mm"]),
            "measurements": len(measurements),
            "center_step": self.center_step,
            "direction": direction,
            "fit_method": verified["fit_method"],
            "fit_r_squared": verified["fit_r_squared"],
        }
        return float(verified["energy"])

    # ==========================================================
    # Public API
    # ==========================================================
    def run(self,
            B_min,
            B_max,
            coarse_steps=40,
            fine_steps=81):
        """
        One-button ESA auto tuning
        """
        self.status = "RUNNING"
        self.last_message = None
        self.hybrid_fit = None
        self.hybrid_peak_brightness = None
        self.best_brightness = None
        self.center_lock_result = None
        self.best_current = None
        self.best_center_offset_px = None
        self.initial_current = self._read_bend()
        if self.initial_current is None:
            self.status = "FAILED"
            self.last_message = "Could not read the initial ESA actuator value."
            return None
        try:
            interval = self.coarse_scan(B_min, B_max, coarse_steps)
            if interval is None:
                self.status = "FAILED"
                self.last_message = self.last_message or "Coarse scan did not detect a beam."
                if self.restore_initial_on_failure:
                    self._restore_initial_bend()
                return None

            B1, B2 = interval
            self._report_fine_range(B1, B2, fine_steps)
            best_B = self.fine_scan(B1, B2, fine_steps)

            if best_B is None:
                self.status = "FAILED"
                self.last_message = self.last_message or "Fine scan did not find a solution."
                if self.restore_initial_on_failure:
                    self._restore_initial_bend()
                return None

            if self.mode == "brightness_then_profile_lock":
                best_B = self._run_profile_center_lock(best_B, B_min, B_max)
                if best_B is None:
                    self.status = "FAILED"
                    if self.restore_initial_on_failure:
                        self._restore_initial_bend()
                    return None
            else:
                self._set_bend(best_B)
                if (
                    self.mode == "brightness_gated_x_fit"
                    and not self._verify_hybrid_solution(best_B)
                ):
                    self.status = "FAILED"
                    if self.restore_initial_on_failure:
                        self._restore_initial_bend()
                    return None
            self._report_progress("final", best_B, has_beam=True, score=None)
            self.best_current = best_B
            self.status = "DONE"
            return best_B
        except ESAAutoTuneCancelled:
            self.status = "CANCELLED"
            if self.restore_initial_on_cancel:
                self._restore_initial_bend()
            return None
        except Exception:
            self.status = "FAILED"
            if self.restore_initial_on_failure:
                self._restore_initial_bend()
            raise

    def get_best_current(self):
        return self.best_current

    def get_last_status(self):
        return self.status

    def get_last_message(self):
        return self.last_message


if __name__=='__main__':
    from half_linac.src.shared.machine_profile import (
        get_workflow,
        load_profile,
        resolve_channel,
        resolve_bend_write_channel,
    )

    profile = load_profile()
    workflow = get_workflow(profile, "energy_spectrum")
    preferred_backend = "real" if "real" in profile.control_backends else profile.machine.default_mode
    flag_element = str(workflow["flag_element"])
    flag_channel = str(workflow["flag_image_channel"])
    pixel_shape_by_backend = workflow.get("flag_pixel_shape", {})
    if not isinstance(pixel_shape_by_backend, dict):
        raise ValueError("workflows.energy_spectrum.flag_pixel_shape must provide per-backend values.")
    flag_pixel_machine = pixel_shape_by_backend.get(preferred_backend)
    if not isinstance(flag_pixel_machine, list) or len(flag_pixel_machine) != 2:
        raise ValueError(
            "workflows.energy_spectrum.flag_pixel_shape must provide [nx, ny] for the selected backend."
        )
    flag_pv = resolve_channel(profile, flag_element, flag_channel, preferred_backend)
    pixel_width_config = workflow.get("flag_pixel_width_mm", {})
    if isinstance(pixel_width_config, dict):
        pixel_width_mm = float(pixel_width_config[preferred_backend])
    else:
        pixel_width_mm = float(pixel_width_config)
    x_reference_config = workflow.get("x_reference_mm", 0.0)
    if isinstance(x_reference_config, dict):
        x_reference_mm = float(x_reference_config.get(preferred_backend, 0.0))
    else:
        x_reference_mm = float(x_reference_config)
    objective = str(workflow.get("auto_tune_objective", "find_beam"))
    target_x_pixel = reference_x_pixel(
        x_reference_mm,
        int(flag_pixel_machine[0]),
        pixel_width_mm,
    )
    enabled_backends = workflow.get("auto_tune_control_backends")
    if enabled_backends is not None and preferred_backend not in enabled_backends:
        raise RuntimeError(
            f"ESA auto tune is not enabled for control backend {preferred_backend!r}."
        )

    actuator = workflow.get("auto_tune_actuator")
    if isinstance(actuator, dict):
        bend_pv = resolve_channel(
            profile,
            str(actuator["element"]),
            str(actuator["channel"]),
            preferred_backend,
        )
        actuator_unit = str(actuator.get("unit", "a.u."))
        scan = workflow.get("auto_tune_scan")
        if not isinstance(scan, dict):
            raise RuntimeError(
                "Coordinated ESA auto tune requires workflows.energy_spectrum.auto_tune_scan."
            )
    else:
        bend_pv = resolve_bend_write_channel(
            profile,
            str(workflow["bend_element"]),
            preferred_backend,
        )
        actuator_unit = "A"
        scan = workflow.get("bend_scan", {})
    hybrid = workflow.get("auto_tune_hybrid", {})
    center_lock = workflow.get("auto_tune_center_lock", {})
    sampling = (
        center_lock if objective == "brightness_then_profile_lock" else hybrid
    )
    esa_tuner = ESA_AutoTuner(
        flag_pv_obj=PV(flag_pv),
        flag_pixel=flag_pixel_machine,
        bend_pv=bend_pv,
        mode=objective,
        target_x_pixel=target_x_pixel,
        settle_time_s=float(scan.get("settle_time_s", 0.5)),
        restore_initial_on_failure=bool(scan.get("restore_initial_on_failure", True)),
        frame_samples=int(sampling.get("frame_samples", 3)),
        min_valid_frames=int(sampling.get("min_valid_frames", 2)),
        frame_interval_s=float(sampling.get("frame_interval_s", 0.2)),
        brightness_fraction=float(sampling.get("brightness_fraction", 0.4)),
        max_center_spread_pixel=(
            float(hybrid.get("max_center_spread_mm", 1.0)) / pixel_width_mm
        ),
        target_tolerance_pixel=(
            float(hybrid.get("target_tolerance_mm", 1.0)) / pixel_width_mm
        ),
        min_fit_correlation=float(hybrid.get("min_fit_correlation", 0.7)),
        pixel_width_mm=pixel_width_mm,
        x_reference_mm=x_reference_mm,
        center_step=float(center_lock.get("center_step", 0.05)),
        center_max_total_offset=float(center_lock.get("max_total_offset", 1.0)),
        center_tolerance_mm=float(center_lock.get("center_tolerance_mm", 0.2)),
        remove_bg=False,
        bg_image=None,
    )

    best_I = esa_tuner.run(
        B_min=float(scan.get("min", 0)),
        B_max=float(scan.get("max", 200)),
        coarse_steps=int(scan.get("coarse_steps", 40)),
        fine_steps=int(scan.get("fine_steps", 81)),
    )

    if best_I is not None:
        print(f"ESA auto-tuned to {best_I:.3f} {actuator_unit}")
