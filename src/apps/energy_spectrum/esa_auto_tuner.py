import time
import numpy as np
from skimage import measure
from epics import caput


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
                 bg_image=None):
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

        self.best_current = None
        self.status = "IDLE"

        # parameters for center lock
        self.center_sigma = 0.10 # (10% FLAG width)

    # ==========================================================
    # Low-level helpers
    # ==========================================================
    def _set_bend(self, current):
        caput(self.bend_pv, float(current))
        time.sleep(0.5)

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
        aspect = region.major_axis_length / max(region.minor_axis_length, 1)
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
            self._set_bend(B)
            img = self._get_flag_image()
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
            self._set_bend(B)

            scores = []
            for _ in range(3):  # median over 3 pulses
                img = self._get_flag_image()
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
            fine_steps=15):
        """
        One-button ESA auto tuning
        """
        self.status = "RUNNING"

        interval = self.coarse_scan(B_min, B_max, coarse_steps)
        if interval is None:
            self.status = "FAILED"
            return None

        B1, B2 = interval
        best_B = self.fine_scan(B1, B2, fine_steps)

        if best_B is None:
            self.status = "FAILED"
            return None

        self._set_bend(best_B)
        self._report_progress("final", best_B, has_beam=True, score=None)
        self.best_current = best_B
        self.status = "DONE"
        return best_B

    def get_best_current(self):
        return self.best_current

    def get_last_status(self):
        return self.status


if __name__=='__main__':
    flag_pixel_machine = [1440,1080] 
    # flag_pixel_width = 0.08 #[mm]
    flag_pv = "IRFEL:BD:FLAG4:image1:ArrayData"
    bend_pv = ""
    esa_tuner = ESA_AutoTuner(
        flag_pv_obj=flag_pv,
        flag_pixel=flag_pixel_machine,
        bend_pv="HALF:IN:ESA:PRF01:CurrentSet",
        remove_bg=False,
        bg_image=None
    )

    best_I = esa_tuner.run(
        B_min=0,
        B_max=200,
        coarse_steps=40,
        fine_steps=15
    )

    if best_I is not None:
        print(f"ESA auto-tuned to {best_I:.3f} A")
