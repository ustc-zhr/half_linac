import time
import numpy as np
from skimage import measure
from epics import caget, caput


class ESAAutoTuneCancelled(RuntimeError):
    """Raised internally when an operator requests a cooperative scan stop."""


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
                 restore_initial_on_cancel=True):
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

        self.mode = mode   
        self.progress_callback = progress_callback
        self.remove_bg = remove_bg
        self.bg_image = bg_image
        self.settle_time_s = float(settle_time_s)
        self.restore_initial_on_failure = bool(restore_initial_on_failure)
        self.cancel_requested = cancel_requested
        self.restore_initial_on_cancel = bool(restore_initial_on_cancel)

        self.best_current = None
        self.initial_current = None
        self.status = "IDLE"

        # parameters for center lock
        self.center_sigma = 0.10 # (10% FLAG width)

    # ==========================================================
    # Low-level helpers
    # ==========================================================
    def _raise_if_cancelled(self):
        if self.cancel_requested is not None and self.cancel_requested():
            raise ESAAutoTuneCancelled("ESA auto tune stopped by operator.")

    def _wait_for_settle(self, *, allow_cancel=True):
        deadline = time.monotonic() + max(self.settle_time_s, 0.0)
        while True:
            if allow_cancel:
                self._raise_if_cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 0.05))

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

    def _report_progress(self, stage, current, *, has_beam, score=None):
        if self.progress_callback is None:
            return
        self.progress_callback(
            {
                "stage": stage,
                "current": float(current),
                "has_beam": bool(has_beam),
                "score": None if score is None else float(score),
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

        # 在这些亮点中，选最大连通区域。
        labels = measure.label(binary)
        regions = measure.regionprops(labels)
        if not regions:
            return False, 0.0, None
        region = max(regions, key=lambda r: r.area)

        # 该区域必须： 
        # 面积适中（50 ~ 100,000 像素）    
        if region.area < 50 or region.area > 1e5:
            return False, 0.0, None
        # 不能太细长（长宽比 ≤ 6）
        major_axis_length = getattr(region, "axis_major_length", None)
        minor_axis_length = getattr(region, "axis_minor_length", None)
        if major_axis_length is None or minor_axis_length is None:
            major_axis_length = region.major_axis_length
            minor_axis_length = region.minor_axis_length
        aspect = major_axis_length / max(minor_axis_length, 1)
        if aspect > 6:
            return False, 0.0, None

        # -----------------------------
        # beam properties
        # -----------------------------
        raw_score = np.sum(img[binary])
        cx = region.centroid[1]   # x = dispersion direction

        # -----------------------------
        # scoring
        # -----------------------------
        if self.mode == "center_lock":
            x0 = self.flag_pixel[0] / 2
            dx = abs(cx - x0) / x0
            penalty = np.exp(-(dx / self.center_sigma) ** 2)
            score = raw_score * penalty
        else:
            score = raw_score

        return True, score, cx

    # ==========================================================
    # Scan stages
    # ==========================================================
    def coarse_scan(self, B_min, B_max, n_steps=40):
        self.status = "COARSE_SCAN"
        hits = []

        for B in np.linspace(B_min, B_max, n_steps):
            self._raise_if_cancelled()
            self._set_bend(B)
            img = self._get_flag_image()
            self._raise_if_cancelled()
            has_beam, score, _ = self._detect_beam(img)
            self._report_progress("coarse", B, has_beam=has_beam, score=score)

            if has_beam:
                hits.append(B)
                if len(hits) >= 3:
                    break

        if not hits:
            return None

        return min(hits), max(hits)

    def fine_scan(self, B1, B2, n_steps=15):
        self.status = "FINE_SCAN"
        best_B = None
        best_score = -np.inf

        for B in np.linspace(B1, B2, n_steps):
            self._raise_if_cancelled()
            self._set_bend(B)

            scores = []
            for _ in range(3):  # median over 3 pulses
                img = self._get_flag_image()
                self._raise_if_cancelled()
                has_beam, score, _ = self._detect_beam(img)
                if has_beam:
                    scores.append(score)

            if not scores:
                self._report_progress("fine", B, has_beam=False, score=None)
                continue

            score_med = np.median(scores)
            self._report_progress("fine", B, has_beam=True, score=score_med)
            if score_med > best_score:
                best_score = score_med
                best_B = B

        return best_B

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
        self.initial_current = self._read_bend()
        if self.initial_current is None:
            self.status = "FAILED"
            return None
        try:
            interval = self.coarse_scan(B_min, B_max, coarse_steps)
            if interval is None:
                self.status = "FAILED"
                if self.restore_initial_on_failure:
                    self._restore_initial_bend()
                return None

            B1, B2 = interval
            best_B = self.fine_scan(B1, B2, fine_steps)

            if best_B is None:
                self.status = "FAILED"
                if self.restore_initial_on_failure:
                    self._restore_initial_bend()
                return None

            self._set_bend(best_B)
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
    esa_tuner = ESA_AutoTuner(
        flag_pv_obj=flag_pv,
        flag_pixel=flag_pixel_machine,
        bend_pv=bend_pv,
        remove_bg=False,
        bg_image=None
    )

    best_I = esa_tuner.run(
        B_min=float(scan.get("min", 0)),
        B_max=float(scan.get("max", 200)),
        coarse_steps=int(scan.get("coarse_steps", 40)),
        fine_steps=int(scan.get("fine_steps", 81)),
    )

    if best_I is not None:
        print(f"ESA auto-tuned to {best_I:.3f} {actuator_unit}")
