import unittest
import numpy as np
from copy import deepcopy

from half_linac.src.shared.energy_tuning import (
    BRIGHTNESS_PEAK,
    CENTER_LOCK,
    EnergyTuningPipelineError,
    legacy_objective_for_pipeline,
    normalize_pipeline,
    CallableEnergyActuator,
    ScreenProfileMeasurement,
)
from half_linac.src.shared.beam_diagnostics import BeamPresenceResult
from half_linac.src.shared.machine_profile.energy_spectrum import (
    resolve_energy_spectrum_auto_tune,
)


class EnergyTuningPipelineTests(unittest.TestCase):
    def test_callable_actuator_adapts_read_and_set(self):
        values = [1.5]
        actuator = CallableEnergyActuator(lambda: values[0], lambda value: values.__setitem__(0, value))

        self.assertEqual(actuator.read(), 1.5)
        actuator.set(2)
        self.assertEqual(actuator.read(), 2.0)

    def test_screen_profile_measurement_returns_shared_observation(self):
        class Profile:
            center_mm = 0.25
            method = "Gauss fit"
            r_squared = 0.95

        class Projection:
            x_mm = np.array([-1.0, 0.0, 1.0])
            density_x = np.array([1.0, 3.0, 1.0])

        measurement = ScreenProfileMeasurement(
            lambda: np.zeros((2, 2)),
            pixel_width_mm=0.1,
            x_reference_mm=0.0,
            project_profiles=lambda *_args: Projection(),
            fit_profile=lambda *_args, **_kwargs: Profile(),
        )
        observation = measurement.measure(4.0, samples=2, min_valid=2)

        self.assertEqual(observation.actuator_value, 4.0)
        self.assertAlmostEqual(observation.center_offset_mm, 0.25)
        self.assertEqual(observation.valid_frames, 2)

    def test_screen_profile_measurement_requires_beam_presence(self):
        class Projection:
            x_mm = np.array([-1.0, 0.0, 1.0])
            density_x = np.array([1.0, 3.0, 1.0])

        measurement = ScreenProfileMeasurement(
            lambda: np.zeros((20, 20)),
            pixel_width_mm=0.1,
            detect_presence=lambda _image: BeamPresenceResult(False),
            project_profiles=lambda *_args: Projection(),
            fit_profile=lambda *_args, **_kwargs: None,
        )

        self.assertIsNone(measurement.measure(4.0, samples=3, min_valid=2))

    def test_screen_profile_measurement_reports_center_spread_without_rejecting(self):
        centers = iter((0.0, 0.2, 1.5))

        class Projection:
            x_mm = np.array([-1.0, 0.0, 1.0])
            density_x = np.array([1.0, 3.0, 1.0])

        def fit_profile(*_args, **_kwargs):
            return type(
                "Profile",
                (),
                {
                    "center_mm": next(centers),
                    "method": "Gauss fit",
                    "r_squared": 0.95,
                },
            )()

        measurement = ScreenProfileMeasurement(
            lambda: np.zeros((20, 20)),
            pixel_width_mm=0.1,
            project_profiles=lambda *_args: Projection(),
            fit_profile=fit_profile,
        )

        observation = measurement.measure(4.0, samples=3, min_valid=2)

        self.assertIsNotNone(observation)
        self.assertAlmostEqual(observation.diagnostics["center_spread_mm"], 1.5)
    def test_legacy_objectives_map_to_composable_stages(self):
        self.assertEqual(normalize_pipeline(legacy_objective="find_beam"), (BRIGHTNESS_PEAK,))
        self.assertEqual(normalize_pipeline(legacy_objective="profile_lock"), (CENTER_LOCK,))
        self.assertEqual(normalize_pipeline(
        legacy_objective="brightness_then_profile_lock"
        ), (BRIGHTNESS_PEAK, CENTER_LOCK))

    def test_explicit_pipeline_is_validated_and_round_trips_for_legacy_ui(self):
        pipeline = normalize_pipeline([BRIGHTNESS_PEAK, CENTER_LOCK])

        self.assertEqual(legacy_objective_for_pipeline(pipeline), "brightness_then_profile_lock")

        with self.assertRaisesRegex(EnergyTuningPipelineError, "center_lock must be the final"):
            normalize_pipeline([CENTER_LOCK, BRIGHTNESS_PEAK])
        with self.assertRaisesRegex(EnergyTuningPipelineError, "must not repeat"):
            normalize_pipeline([BRIGHTNESS_PEAK, BRIGHTNESS_PEAK])

    def test_energy_spectrum_resolver_prefers_pipeline_and_exposes_stage_groups(self):
        workflow = {
            "auto_tune_defaults": {
                "objective": "find_beam",
                "pipeline": [BRIGHTNESS_PEAK, CENTER_LOCK],
                "measurement": {"frame_samples": 3},
                "brightness_peak": {"fine_steps": 21},
                "center_lock": {"center_tolerance_mm": 0.2},
            }
        }

        resolved = resolve_energy_spectrum_auto_tune(workflow)

        self.assertEqual(resolved["pipeline"], [BRIGHTNESS_PEAK, CENTER_LOCK])
        self.assertEqual(resolved["objective"], "brightness_then_profile_lock")
        self.assertEqual(resolved["measurement"]["frame_samples"], 3)
        self.assertEqual(resolved["brightness_peak"]["fine_steps"], 21)
        self.assertEqual(resolved["center_lock"]["center_tolerance_mm"], 0.2)

    def test_legacy_energy_spectrum_objective_remains_readable(self):
        workflow = {
            "auto_tune_defaults": {"objective": "find_beam"},
        }

        resolved = resolve_energy_spectrum_auto_tune(deepcopy(workflow))

        self.assertEqual(resolved["objective"], "find_beam")
        self.assertNotIn("pipeline", resolved)


if __name__ == "__main__":
    unittest.main()
