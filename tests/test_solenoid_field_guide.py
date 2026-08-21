from __future__ import annotations

import unittest

from half_linac.src.apps.solenoid_field_guide.calibration import (
    CalibrationError,
    default_calibration_path,
    load_calibrations,
    recommend_single,
)
from half_linac.src.apps.solenoid_field_guide.current_control import (
    VerificationConfig,
    apply_current,
    prepare_current_write,
)
from half_linac.src.shared.machine_profile import load_app_context


class FakeScalarIO:
    def __init__(self, readbacks=()):
        self.readbacks = list(readbacks)
        self.writes = []

    def write(self, pv_name, value):
        self.writes.append((pv_name, value))

    def read(self, _pv_name):
        if not self.readbacks:
            raise ValueError("no readback")
        if len(self.readbacks) == 1:
            return self.readbacks[0]
        return self.readbacks.pop(0)


class SolenoidFieldGuideTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_calibrations()
        cls.calibrations = cls.catalog.calibrations
        cls.real_context = load_app_context(
            "solenoid_field_guide", machine_id="half", control_backend="real"
        )

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


    def test_out_of_range_does_not_extrapolate(self):
        calibration = self.calibrations["SS02"]
        with self.assertRaises(CalibrationError):
            calibration.current_from_peak(calibration.peak_range[1] * 1.01)
        with self.assertRaises(CalibrationError):
            recommend_single(calibration, 0.0, "integral")

    def test_long_solenoid_sections_can_be_recommended_individually(self):
        for element_id in ("SL01-1", "SL01-2"):
            calibration = self.calibrations[element_id]
            result = recommend_single(calibration, calibration.reference_integral_field, "integral")
            self.assertEqual(result.element_id, element_id)
            self.assertAlmostEqual(result.current, calibration.reference_current, places=5)

    def test_default_path_exists(self):
        self.assertTrue(default_calibration_path().is_file())

    def test_current_control_resolves_setpoint_readback_and_limit(self):
        control = prepare_current_write(self.real_context, "SS01", 4.0)
        self.assertEqual(control.target.pv_name, "IN:PS:LE07:SS01:current:ao")
        self.assertEqual(control.readback_pv, "IN:PS:LE07:SS01:current:ai")
        self.assertEqual(control.target.machine_limit.high, 12.0)

    def test_apply_current_writes_and_verifies_readback(self):
        io = FakeScalarIO([3.8, 4.005])
        result = apply_current(
            self.real_context,
            "SS01",
            4.0,
            io=io,
            config=VerificationConfig(0.01, 0.1, 0.001),
        )
        self.assertTrue(result.succeeded)
        self.assertEqual(io.writes, [("IN:PS:LE07:SS01:current:ao", 4.0)])
        self.assertAlmostEqual(result.readback_current, 4.005)

    def test_apply_rejects_machine_limit_without_writing(self):
        io = FakeScalarIO([20.0])
        result = apply_current(self.real_context, "SS01", 20.0, io=io)
        self.assertEqual(result.status, "rejected")
        self.assertEqual(io.writes, [])

    def test_apply_is_blocked_on_vm_backend(self):
        context = load_app_context(
            "solenoid_field_guide", machine_id="half", control_backend="vm"
        )
        io = FakeScalarIO([4.0])
        result = apply_current(context, "SS01", 4.0, io=io)
        self.assertEqual(result.status, "rejected")
        self.assertIn("blocked", result.message)
        self.assertEqual(io.writes, [])

    def test_apply_reports_readback_mismatch(self):
        io = FakeScalarIO([3.0])
        result = apply_current(
            self.real_context,
            "SS01",
            4.0,
            io=io,
            config=VerificationConfig(0.01, 0.001, 0.001),
        )
        self.assertEqual(result.status, "mismatch")
        self.assertEqual(result.readback_current, 3.0)


if __name__ == "__main__":
    unittest.main()
