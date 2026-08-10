from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from half_linac.src.apps.energy_spectrum.esa_auto_tuner import (
    ESA_AutoTuner,
    reference_x_pixel,
)
from half_linac.src.apps.energy_spectrum.spectrum_profile import (
    fit_projection_profile,
    gaussian,
    project_image_profiles,
)


class _DummyPV:
    def get(self):
        return np.zeros((10, 10)).ravel().tolist()


class _FakeAutoTuner(ESA_AutoTuner):
    def __init__(self, progress_callback=None, cancel_requested=None):
        super().__init__(
            flag_pv_obj=_DummyPV(),
            flag_pixel=(10, 10),
            bend_pv="FAKE:BEND",
            progress_callback=progress_callback,
            cancel_requested=cancel_requested,
        )
        self.current = None

    def _set_bend(self, current, *, allow_cancel=True):
        self.current = float(current)

    def _read_bend(self):
        return self.current

    def _get_flag_image(self):
        return np.zeros((10, 10))

    def _detect_beam(self, img):
        if self.current is None or self.current < 3 or self.current > 5:
            return False, 0.0, None
        score = 10.0 - abs(self.current - 4.0)
        return True, score, self.current


class _CenterObjectiveAutoTuner(ESA_AutoTuner):
    def __init__(self):
        super().__init__(
            flag_pv_obj=_DummyPV(),
            flag_pixel=(100, 100),
            bend_pv="FAKE:ENERGY",
            mode="center_x_reference",
            target_x_pixel=59.5,
        )
        self.current = 5.0
        self.visited = []

    def _set_bend(self, current, *, allow_cancel=True):
        self.current = float(current)
        self.visited.append(self.current)

    def _read_bend(self):
        return self.current

    def _get_flag_image(self):
        image = np.zeros((100, 100))
        center_x = int(round(self.current * 10.0))
        if 5 <= center_x <= 94:
            # Brightness peaks away from the calibrated x target on purpose.
            amplitude = 200.0 - 5.0 * abs(self.current - 3.0)
            image[45:55, center_x - 5:center_x + 5] = amplitude
        return image


class _HybridObjectiveAutoTuner(ESA_AutoTuner):
    def __init__(self, *, moving_center=True):
        super().__init__(
            flag_pv_obj=_DummyPV(),
            flag_pixel=(120, 100),
            bend_pv="FAKE:ENERGY",
            mode="brightness_gated_x_fit",
            target_x_pixel=59.5,
            frame_interval_s=0.0,
            brightness_fraction=0.4,
            max_center_spread_pixel=5.0,
            target_tolerance_pixel=2.0,
            min_fit_correlation=0.7,
        )
        self.current = 4.25
        self.moving_center = moving_center

    def _set_bend(self, current, *, allow_cancel=True):
        self.current = float(current)

    def _read_bend(self):
        return self.current

    def _get_flag_image(self):
        image = np.zeros((100, 120))
        if not 1.0 <= self.current <= 9.0:
            return image
        center_x = 60 if not self.moving_center else int(round(20.0 + 8.0 * self.current))
        # Peak brightness is at 3 MeV, while x_reference is reached at 5 MeV.
        amplitude = max(20.0, 220.0 - 30.0 * abs(self.current - 3.0))
        image[45:55, center_x - 5:center_x + 5] = amplitude
        return image


class _ProfileLockAutoTuner(ESA_AutoTuner):
    def __init__(self, *, moving_center=True, progress_callback=None):
        super().__init__(
            flag_pv_obj=_DummyPV(),
            flag_pixel=(120, 100),
            bend_pv="FAKE:ENERGY",
            mode="brightness_then_profile_lock",
            target_x_pixel=59.5,
            pixel_width_mm=0.1,
            profile_fit_method="Gauss fit",
            x_reference_mm=0.0,
            progress_callback=progress_callback,
            frame_interval_s=0.0,
            center_step=0.1,
            center_max_total_offset=1.5,
            center_tolerance_mm=0.08,
        )
        self.current = 2.5
        self.moving_center = moving_center

    def _set_bend(self, current, *, allow_cancel=True):
        self.current = float(current)

    def _read_bend(self):
        return self.current

    def _get_flag_image(self):
        image = np.zeros((100, 120))
        if not 1.0 <= self.current <= 5.0:
            return image
        x_mm = np.linspace(-6.0, 6.0, 120)
        y_mm = np.linspace(-5.0, 5.0, 100)
        center_mm = self.current - 4.0 if self.moving_center else -1.0
        amplitude = max(40.0, 300.0 - 50.0 * abs(self.current - 3.0))
        xx, yy = np.meshgrid(x_mm, y_mm)
        return amplitude * np.exp(
            -((xx - center_mm) ** 2 + yy ** 2) / (2.0 * 0.6 ** 2)
        )


class ESAAutoTunerTests(unittest.TestCase):
    def test_shared_projection_fit_matches_gaussian_and_direct_center_definitions(self):
        x_mm = np.linspace(-5.0, 5.0, 201)
        density = gaussian(x_mm, 3.0, 0.75, 0.6)

        gauss_fit = fit_projection_profile(x_mm, density, "Gauss fit")
        direct_fit = fit_projection_profile(x_mm, density, "direct")

        self.assertAlmostEqual(gauss_fit.center_mm, 0.75, places=6)
        self.assertAlmostEqual(direct_fit.center_mm, 0.75, places=6)
        self.assertGreater(gauss_fit.r_squared, 0.999)

    def test_shared_image_projection_uses_gui_coordinate_convention(self):
        image = np.zeros((10, 12))
        image[4:6, 7:9] = 1.0

        projection = project_image_profiles(image, 0.1)

        self.assertEqual(projection.image.shape, (8, 10))
        self.assertEqual(projection.density_x.shape, (10,))
        self.assertGreater(np.sum(projection.density_x), 0)

    def test_reference_x_mm_maps_to_calibrated_pixel_coordinate(self):
        self.assertAlmostEqual(reference_x_pixel(0.0, 1440, 0.02), 719.5)
        self.assertAlmostEqual(
            reference_x_pixel(1.0, 1440, 0.02),
            (1.0 + 14.4) / 28.8 * 1439,
        )

    def test_detect_beam_returns_consistent_triplet_when_no_beam(self):
        tuner = ESA_AutoTuner(
            flag_pv_obj=_DummyPV(),
            flag_pixel=(10, 10),
            bend_pv="FAKE:BEND",
        )
        result = tuner._detect_beam(np.zeros((10, 10)))
        self.assertEqual(len(result), 3)
        self.assertEqual(result, (False, 0.0, None))

    def test_hybrid_detection_uses_brightest_valid_spot_not_largest_noise_blob(self):
        tuner = ESA_AutoTuner(
            flag_pv_obj=_DummyPV(),
            flag_pixel=(200, 200),
            bend_pv="FAKE:BEND",
            mode="brightness_gated_x_fit",
        )
        image = np.zeros((200, 200))
        image[80:88, 46:54] = 500.0
        image[70:82, 134:146] = 150.0

        has_beam, _score, center_x = tuner._detect_beam(image)

        self.assertTrue(has_beam)
        self.assertAlmostEqual(center_x, 49.5)

    def test_coarse_and_fine_scan_use_triplet_detection_api(self):
        tuner = _FakeAutoTuner()
        interval = tuner.coarse_scan(0, 10, n_steps=11)
        self.assertEqual(interval, (3.0, 5.0))

        best = tuner.fine_scan(3.0, 5.0, n_steps=9)
        self.assertAlmostEqual(best, 4.0)

    def test_fine_scan_rejects_points_with_only_one_valid_frame(self):
        tuner = _FakeAutoTuner()
        tuner.current = 4.0
        calls = 0

        def detect_one_frame(_image):
            nonlocal calls
            calls += 1
            if calls % tuner.frame_samples == 1:
                return True, 10.0, 4.0
            return False, 0.0, None

        tuner._detect_beam = detect_one_frame

        self.assertIsNone(tuner.fine_scan(3.0, 5.0, n_steps=3))

    def test_run_returns_best_current_and_marks_done(self):
        tuner = _FakeAutoTuner()
        tuner.current = 2.0
        best = tuner.run(0, 10, coarse_steps=11, fine_steps=9)
        self.assertAlmostEqual(best, 4.0)
        self.assertAlmostEqual(tuner.get_best_current(), 4.0)
        self.assertEqual(tuner.get_last_status(), "DONE")

    def test_progress_callback_receives_scan_updates(self):
        updates = []
        tuner = _FakeAutoTuner(progress_callback=updates.append)
        tuner.current = 2.0
        best = tuner.run(0, 10, coarse_steps=11, fine_steps=9)

        self.assertAlmostEqual(best, 4.0)
        self.assertGreaterEqual(len(updates), 2)
        self.assertEqual(updates[0]["stage"], "coarse")
        self.assertIn(updates[-1]["stage"], {"final", "fine"})

        fine_range = next(
            update for update in updates if update["stage"] == "fine_range"
        )
        self.assertEqual(fine_range["range_min"], 3.0)
        self.assertEqual(fine_range["range_max"], 5.0)
        self.assertEqual(fine_range["points"], 9)
        self.assertEqual(fine_range["spacing"], 0.25)

    def test_operator_cancel_restores_initial_value(self):
        updates = []
        tuner = _FakeAutoTuner(
            progress_callback=updates.append,
            cancel_requested=lambda: True,
        )
        tuner.current = 4.25

        best = tuner.run(0, 10, coarse_steps=11, fine_steps=9)

        self.assertIsNone(best)
        self.assertEqual(tuner.get_last_status(), "CANCELLED")
        self.assertAlmostEqual(tuner.current, 4.25)
        self.assertEqual(updates[-1]["stage"], "restore")

    def test_scan_does_not_start_without_restorable_initial_value(self):
        tuner = _FakeAutoTuner()

        best = tuner.run(0, 10, coarse_steps=11, fine_steps=9)

        self.assertIsNone(best)
        self.assertEqual(tuner.get_last_status(), "FAILED")
        self.assertIsNone(tuner.current)

    def test_scan_error_restores_initial_value(self):
        updates = []
        tuner = _FakeAutoTuner(progress_callback=updates.append)
        tuner.current = 2.5

        def fail_image_read():
            raise RuntimeError("camera unavailable")

        tuner._get_flag_image = fail_image_read

        with self.assertRaisesRegex(RuntimeError, "camera unavailable"):
            tuner.run(0, 10, coarse_steps=11, fine_steps=9)

        self.assertEqual(tuner.get_last_status(), "FAILED")
        self.assertAlmostEqual(tuner.current, 2.5)
        self.assertEqual(updates[-1]["stage"], "restore")

    def test_center_objective_uses_x_reference_instead_of_peak_brightness(self):
        tuner = _CenterObjectiveAutoTuner()

        interval = tuner.coarse_scan(0, 10, n_steps=11)

        self.assertEqual(interval, (5.0, 7.0))
        self.assertEqual(tuner.visited[:11], list(np.linspace(0, 10, 11)))

        best = tuner.run(0, 10, coarse_steps=11, fine_steps=21)

        self.assertAlmostEqual(best, 6.0)
        self.assertAlmostEqual(tuner.best_center_offset_px, 0.0)

    def test_brightness_gated_fit_uses_bright_beam_motion_to_reach_reference(self):
        tuner = _HybridObjectiveAutoTuner()

        best = tuner.run(0, 10, coarse_steps=11, fine_steps=29)

        self.assertAlmostEqual(best, 5.0, delta=0.1)
        self.assertEqual(tuner.get_last_status(), "DONE")
        self.assertAlmostEqual(tuner.best_center_offset_px, 0.0, delta=1.0)
        self.assertGreater(abs(tuner.hybrid_fit["correlation"]), 0.99)
        self.assertGreaterEqual(tuner.hybrid_fit["points_used"], 3)

    def test_brightness_gated_fit_rejects_static_bright_noise_and_restores(self):
        tuner = _HybridObjectiveAutoTuner(moving_center=False)
        initial = tuner.current

        best = tuner.run(0, 10, coarse_steps=11, fine_steps=29)

        self.assertIsNone(best)
        self.assertEqual(tuner.get_last_status(), "FAILED")
        self.assertAlmostEqual(tuner.current, initial)
        self.assertIn("correlation is too weak", tuner.get_last_message())

    def test_profile_center_lock_moves_from_brightness_peak_to_fitted_center(self):
        updates = []
        tuner = _ProfileLockAutoTuner(progress_callback=updates.append)

        best = tuner.run(0, 6, coarse_steps=7, fine_steps=21)

        self.assertAlmostEqual(best, 4.0, delta=0.08)
        self.assertEqual(tuner.get_last_status(), "DONE")
        self.assertAlmostEqual(tuner.center_lock_result["seed_energy"], 3.0, delta=0.1)
        self.assertAlmostEqual(tuner.center_lock_result["final_offset_mm"], 0.0, delta=0.08)
        self.assertEqual(tuner.center_lock_result["fit_method"], "Gauss fit")
        stages = {update["stage"] for update in updates}
        self.assertIn("center_step", stages)
        self.assertIn("center_lock", stages)
        self.assertIn("verify", stages)
        verify = next(update for update in updates if update["stage"] == "verify")
        self.assertEqual(verify["valid_frames"], 5)
        self.assertEqual(verify["total_frames"], 5)
        self.assertIn("center_mm", verify)
        self.assertIn("center_offset_mm", verify)
        self.assertEqual(verify["fit_method"], "Gauss fit")

    def test_profile_center_lock_rejects_static_center_and_restores(self):
        tuner = _ProfileLockAutoTuner(moving_center=False)
        initial = tuner.current

        best = tuner.run(0, 6, coarse_steps=7, fine_steps=21)

        self.assertIsNone(best)
        self.assertEqual(tuner.get_last_status(), "FAILED")
        self.assertAlmostEqual(tuner.current, initial)
        self.assertIn("did not reach x_reference_mm", tuner.get_last_message())

    def test_profile_center_measurement_does_not_depend_on_2d_beam_detection(self):
        tuner = _ProfileLockAutoTuner()
        tuner._detect_beam = lambda _image: (False, None, 0.0)

        measurement = tuner._measure_profile_center(tuner.current, "test")

        self.assertIsNotNone(measurement)
        self.assertAlmostEqual(measurement["center_mm"], -1.5, delta=0.08)


if __name__ == "__main__":
    unittest.main()
