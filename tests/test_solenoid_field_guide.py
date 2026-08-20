from __future__ import annotations

import unittest

from half_linac.src.apps.solenoid_field_guide.calibration import (
    CalibrationError,
    default_calibration_path,
    load_calibrations,
    recommend_combined,
    recommend_single,
)


class SolenoidFieldGuideTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_calibrations()
        cls.calibrations = cls.catalog.calibrations

    def test_reference_data_loads_in_tesla_and_is_monotonic(self):
        self.assertEqual(set(self.calibrations), {"SS01", "SS02", "SM01", "SL01-1", "SL01-2"})
        for calibration in self.calibrations.values():
            self.assertGreater(calibration.peak_fields[-1], 0.01)
            self.assertLess(calibration.peak_fields[-1], 0.2)
            self.assertTrue(all(left < right for left, right in zip(calibration.currents, calibration.currents[1:])))

        self.assertEqual(
            {key: value.design_peak_field for key, value in self.calibrations.items()},
            {"SS01": 0.035, "SS02": 0.041, "SM01": 0.068, "SL01-1": 0.079, "SL01-2": 0.079},
        )
        self.assertEqual(self.calibrations["SS01"].machine_current_limit.high, 12.0)
        self.assertEqual(self.calibrations["SM01"].machine_current_limit.high, 100.0)

    def test_peak_round_trip_at_measured_point(self):
        calibration = self.calibrations["SS01"]
        current = calibration.currents[3]
        peak = calibration.peak_from_current(current)
        self.assertAlmostEqual(calibration.current_from_peak(peak), current)

    def test_integral_conversion_uses_reference_ratio(self):
        calibration = self.calibrations["SM01"]
        current = calibration.reference_current
        self.assertAlmostEqual(calibration.integral_from_current(current), calibration.reference_integral_field)
        result = recommend_single(calibration, calibration.reference_integral_field, "integral")
        self.assertAlmostEqual(result.current, current)

    def test_design_peak_fields_produce_design_integral_targets(self):
        calibration = self.calibrations["SS01"]
        expected = calibration.reference_integral_field * calibration.design_peak_field / calibration.reference_peak_field
        self.assertAlmostEqual(calibration.design_integral_field, expected)
        result = recommend_single(calibration, calibration.design_peak_field, "peak")
        self.assertAlmostEqual(result.peak_field, calibration.design_peak_field)

    def test_combined_design_integral_target_is_sum_of_sections(self):
        target = sum(
            self.calibrations[element_id].design_integral_field
            for element_id in ("SL01-1", "SL01-2")
        )
        result = recommend_combined(self.catalog, "SL", target)
        combined = sum(item.integral_field for item in result.magnets)
        self.assertAlmostEqual(combined, target)
        self.assertAlmostEqual(result.magnets[0].peak_field, self.calibrations["SL01-1"].design_peak_field, places=3)
        self.assertAlmostEqual(result.magnets[1].peak_field, self.calibrations["SL01-2"].design_peak_field, places=3)

    def test_out_of_range_does_not_extrapolate(self):
        calibration = self.calibrations["SS02"]
        with self.assertRaises(CalibrationError):
            calibration.current_from_peak(calibration.peak_range[1] * 1.01)
        with self.assertRaises(CalibrationError):
            recommend_single(calibration, 0.0, "integral")

    def test_combined_long_solenoid_uses_common_scale(self):
        first = self.calibrations["SL01-1"]
        second = self.calibrations["SL01-2"]
        target = first.reference_integral_field + second.reference_integral_field
        result = recommend_combined(self.catalog, "SL", target)
        self.assertAlmostEqual(result.scale, 1.0)
        self.assertAlmostEqual(result.magnets[0].current, first.reference_current, places=5)
        self.assertAlmostEqual(result.magnets[1].current, second.reference_current, places=5)

    def test_long_solenoid_sections_can_be_recommended_individually(self):
        for element_id in ("SL01-1", "SL01-2"):
            calibration = self.calibrations[element_id]
            result = recommend_single(calibration, calibration.reference_integral_field, "integral")
            self.assertEqual(result.element_id, element_id)
            self.assertAlmostEqual(result.current, calibration.reference_current, places=5)

    def test_default_path_exists(self):
        self.assertTrue(default_calibration_path().is_file())


if __name__ == "__main__":
    unittest.main()
