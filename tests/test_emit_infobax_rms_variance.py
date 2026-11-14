"""Resolution, positivity, uncertainty and contract tests for VM RMS variance."""

import unittest

import numpy as np

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

from half_linac.src.apps.emit_measure.infobax_measurement import (
    RmsVarianceConfig, measure_count_variance,
)
from half_linac.src.apps.emit_measure.infobax_vm import (
    HalfVmInfoBAXError, HalfVmInfoBAXMachine, build_half_vm_infobax_pool,
)
from tests.test_emit_infobax_vm import FakePvIo, FakeModelBackend, workpoints
from half_linac.src.shared.machine_profile import load_app_context


def count_image(seed=7, count=100000):
    rng = np.random.default_rng(seed)
    x = rng.normal(0.13, 0.4, count)
    y = rng.normal(-0.07, 0.3, count)
    image, _, _ = np.histogram2d(y, x, bins=(80, 120), range=((-1.6, 1.6), (-2.4, 2.4)))
    return image, x


class RmsVarianceTests(unittest.TestCase):
    def test_corrected_count_variance_recovers_particle_variance(self):
        image, x = count_image()
        result = measure_count_variance(image, pixel_shape=(120, 80), pixel_width_mm=0.04)
        self.assertLess(abs(result.variance_mm2 / np.var(x) - 1), 0.005)
        self.assertGreater(result.noise_std_mm2, 0)
        self.assertGreater(result.pixel_variance_mm2, result.variance_mm2)
        self.assertLess(abs(result.centroid_mm - x.mean()), 0.001)

    def test_plane_and_transposed_geometry_give_the_same_variance(self):
        image, _x = count_image()
        first = measure_count_variance(image, pixel_shape=(120, 80), pixel_width_mm=0.04, plane="y")
        second = measure_count_variance(image.T, pixel_shape=(80, 120), pixel_width_mm=0.04)
        self.assertAlmostEqual(first.variance_mm2, second.variance_mm2)
        self.assertAlmostEqual(first.noise_std_mm2, second.noise_std_mm2)

    def test_uncertainty_tracks_independent_particle_population_variation(self):
        results = [measure_count_variance(count_image(seed, 10000)[0], pixel_shape=(120, 80),
                                           pixel_width_mm=0.04) for seed in range(20)]
        empirical = np.std([row.variance_mm2 for row in results], ddof=1)
        predicted = np.mean([row.noise_std_mm2 for row in results])
        self.assertGreater(empirical / predicted, 0.5)
        self.assertLess(empirical / predicted, 1.5)

    def test_invalid_counts_and_unresolved_beams_are_not_clipped(self):
        image, _x = count_image()
        for raw in (image/3, -image, np.full(image.shape, np.nan), np.zeros(image.shape)):
            with self.assertRaises(ValueError):
                measure_count_variance(raw, pixel_shape=(120, 80), pixel_width_mm=0.04)
        image = np.zeros((5, 5))
        image[2, 2] = 10000
        with self.assertRaisesRegex(ValueError, "underresolved"):
            measure_count_variance(image, pixel_shape=(5, 5), pixel_width_mm=0.04)

    def test_edge_population_is_reported_as_failure(self):
        image = np.zeros((5, 5))
        image[2, 0] = 5000
        image[2, 4] = 5000
        with self.assertRaisesRegex(ValueError, "edge"):
            measure_count_variance(image, pixel_shape=(5, 5), pixel_width_mm=0.04)

    def test_noise_changes_with_count_and_scale(self):
        image, _x = count_image()
        first = measure_count_variance(image, pixel_shape=(120, 80), pixel_width_mm=0.04)
        more = measure_count_variance(image*4, pixel_shape=(120, 80), pixel_width_mm=0.04)
        larger = measure_count_variance(image, pixel_shape=(120, 80), pixel_width_mm=0.08)
        self.assertAlmostEqual(first.noise_std_mm2 / more.noise_std_mm2, 2.0, places=4)
        self.assertAlmostEqual(larger.noise_std_mm2 / first.noise_std_mm2, 4.0)


class RmsContractTests(unittest.TestCase):
    def test_vm_adapter_commits_per_frame_noise_with_a_matching_rms_contract(self):
        context = load_app_context("emit_measure", machine_id="half", control_backend="vm")
        io = FakePvIo(context)
        self.addCleanup(io.temporary.cleanup)
        kwargs = dict(scan_quad="QT02", screen="PRF07", plane="x", measurement_method="rms_count_variance")
        machine = HalfVmInfoBAXMachine(context, workpoints(), **kwargs,
                                      get=io.get, put=io.put, runtime_json=io.runtime_json)
        pool = build_half_vm_infobax_pool(
            context, workpoints(), **kwargs, scan_values=(-1, 0, 1), energy_mev=225,
            observation_noise_std_mm2=0.002, model_backend=FakeModelBackend(),
        )
        machine.validate_pool(pool)
        width, height = machine._geometry.shape
        pitch = machine._geometry.pixel_width_mm
        rng = np.random.default_rng(7)
        image, _, _ = np.histogram2d(
            rng.normal(0, 0.3, 10000), rng.normal(0, 0.4, 10000), bins=(height, width),
            range=((-height*pitch/2, height*pitch/2), (-width*pitch/2, width*pitch/2)),
        )
        io.values[io.image_pv] = image.ravel()
        machine.apply_query(0, -1)
        result = machine.measure_point(0, 0.002)
        self.assertLess(abs(result.variance_mm2/0.16-1), 0.05)
        self.assertNotEqual(result.noise_std_mm2, 0.002)
        self.assertEqual(result.metadata["forecast_noise_std_mm2"], 0.002)
        self.assertEqual(result.metadata["measurement_method"], "rms_count_variance")
        self.assertEqual(result.metadata["vm_calculation"], io.calculation)

    def test_rms_pool_cannot_be_mixed_with_gaussian_or_other_resolution_contract(self):
        context = load_app_context("emit_measure", machine_id="half", control_backend="vm")
        io = FakePvIo(context)
        self.addCleanup(io.temporary.cleanup)
        kwargs = dict(scan_quad="QT02", screen="PRF07", plane="x")
        machine = HalfVmInfoBAXMachine(context, workpoints(), **kwargs,
                                      get=io.get, put=io.put, runtime_json=io.runtime_json,
                                      measurement_method="rms_count_variance")
        pool = build_half_vm_infobax_pool(
            context, workpoints(), **kwargs, scan_values=(-1, 0, 1), energy_mev=225,
            observation_noise_std_mm2=0.002, model_backend=FakeModelBackend(),
            measurement_method="rms_count_variance",
        )
        machine.validate_pool(pool)
        gaussian = build_half_vm_infobax_pool(
            context, workpoints(), **kwargs, scan_values=(-1, 0, 1), energy_mev=225,
            observation_noise_std_mm2=0.002, model_backend=FakeModelBackend(),
        )
        with self.assertRaisesRegex(HalfVmInfoBAXError, "contract"):
            machine.validate_pool(gaussian)
        for settings in (RmsVarianceConfig(minimum_sigma_pixels=2), RmsVarianceConfig(noise_scale=0.7)):
            different = build_half_vm_infobax_pool(
                context, workpoints(), **kwargs, scan_values=(-1, 0, 1), energy_mev=225,
                observation_noise_std_mm2=0.002, model_backend=FakeModelBackend(),
                measurement_method="rms_count_variance", rms_config=settings,
            )
            with self.assertRaisesRegex(HalfVmInfoBAXError, "contract"):
                machine.validate_pool(different)


if __name__ == "__main__":
    unittest.main()
