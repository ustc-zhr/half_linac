from __future__ import annotations

import math
import sys
import unittest
from dataclasses import replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from half_linac.src.apps.energy_feedback.bpm_dispersion import (
    BpmDispersionProvider,
    FirFeedbackCoordinator,
    build_rf_hv_safety_checker,
    calibration_issue,
    check_session_guards,
)
from half_linac.src.apps.energy_feedback.engine import (
    IncrementalPIController,
    NonOverlappingAggregateWindow,
    NonOverlappingMedianWindow,
    derive_control_timing,
)
from half_linac.src.apps.energy_feedback.models import (
    ActuatorCommand,
    EnergyMeasurement,
    FeedbackDecision,
    FeedbackObservable,
    MeasurementQuality,
    PVReading,
)
from half_linac.src.apps.energy_feedback.profile_runtime import load_runtime_config
from half_linac.src.apps.energy_feedback.writer import apply_feedback_decision
from half_linac.src.shared.machine_profile import (
    REAL_STATUS_COMMISSIONING,
    load_app_context,
    real_commissioning_status,
    workflow_writes_allowed,
)


CALIBRATION = {
    "ready": True,
    "version": "test-v1",
    "a": 0.5,
    "b": -0.25,
    "response_q_mm_per_kv": -2.0,
    "fct_min": 1.0,
    "q_action_range_mm": 20.0,
    "bpm05_max_offset_mm": 0.5,
    "quadrupole_max_delta_a": 0.1,
}


def reading(
    key: str,
    value: float,
    timestamp: float = 100.0,
    *,
    monitor_received_at: float | None = None,
) -> PVReading:
    return PVReading(
        key,
        f"PV:{key}",
        value,
        True,
        timestamp,
        monitor_received_at=monitor_received_at,
    )


class FakeClient:
    def __init__(self, values: dict[str, PVReading]):
        self.values = values

    def read_many(self, keys):
        return {key: self.values[key] for key in keys}


class FakeHvClient:
    def __init__(self, setpoint_key: str, readback_key: str, *, verify_write: bool):
        self.setpoint_key = setpoint_key
        self.readback_key = readback_key
        self.value = 39.0
        self.verify_write = verify_write
        self.writes: list[tuple[str, float]] = []

    def read_many(self, keys):
        return {key: reading(key, self.value) for key in keys}

    def write(self, key, value, *, wait=True, timeout_s=1.0):
        value = float(value)
        self.writes.append((key, value))
        if value == 39.0 or self.verify_write:
            self.value = value
        return True


def provider_source() -> dict[str, object]:
    signal_keys = (
        "bpm.BPM02.x",
        "bpm.BPM03.x",
        "bpm.BPM04.x",
        "bpm.BPM05.x",
        "fct",
    )
    return {
        "id": "fir",
        "observable": {"label": "q", "unit": "mm"},
        "geometry": {},
        "calibration": dict(CALIBRATION),
        "resolved_signals": {},
        "stale_timeout_s": 3.0,
        "max_timestamp_skew_s": 0.2,
        "signal_keys": signal_keys,
        "bpm_signal_keys": signal_keys[:-1],
        "fct_key": "fct",
        "q_keys": ("bpm.BPM02.x", "bpm.BPM03.x", "bpm.BPM04.x"),
        "source_timestamp_exempt_keys": (),
    }


def measurement(
    q: float,
    diagnostics: dict[str, float] | None = None,
    *,
    timestamp: float = 100.0,
) -> EnergyMeasurement:
    return EnergyMeasurement(
        sampled_at=timestamp,
        source_timestamp=timestamp,
        target_source_timestamp=None,
        energy_mev=None,
        target_mev=None,
        error_mev=None,
        diagnostics=diagnostics or {},
        diagnostic_errors={},
        quality=MeasurementQuality("OK", "valid", True),
        observable=FeedbackObservable("q", "mm", q, raw_value=q),
    )


class BpmDispersionProviderTests(unittest.TestCase):
    def test_q_formula_excludes_bpm05(self):
        values = {
            "bpm.BPM02.x": reading("bpm.BPM02.x", 2.0),
            "bpm.BPM03.x": reading("bpm.BPM03.x", 4.0),
            "bpm.BPM04.x": reading("bpm.BPM04.x", 8.0),
            "bpm.BPM05.x": reading("bpm.BPM05.x", 999.0),
            "fct": reading("fct", 10.0),
        }
        result = BpmDispersionProvider(FakeClient(values), provider_source()).sample(100.0)
        self.assertTrue(result.quality.valid, result.quality.reason)
        self.assertAlmostEqual(result.control_value, 8.0)
        self.assertIsNone(result.energy_mev)
        self.assertEqual(result.observable_unit, "mm")

    def test_timestamp_skew_and_fct_loss_clear_sample_quality(self):
        values = {
            "bpm.BPM02.x": reading("bpm.BPM02.x", 2.0, 100.0),
            "bpm.BPM03.x": reading("bpm.BPM03.x", 4.0, 100.0),
            "bpm.BPM04.x": reading("bpm.BPM04.x", 8.0, 100.3),
            "bpm.BPM05.x": reading("bpm.BPM05.x", 0.0, 100.0),
            "fct": reading("fct", 10.0, 100.0),
        }
        provider = BpmDispersionProvider(FakeClient(values), provider_source())
        skewed = provider.sample(100.0)
        self.assertFalse(skewed.quality.valid)
        self.assertIn("timestamp skew", skewed.quality.reason)

        values["bpm.BPM04.x"] = reading("bpm.BPM04.x", 8.0, 100.0)
        values["fct"] = reading("fct", 0.2, 100.0)
        lost = provider.sample(100.0)
        self.assertFalse(lost.quality.valid)
        self.assertIn("FCT", lost.quality.reason)

    def test_monitor_receive_policy_accepts_legacy_bpm_source_timestamps(self):
        source = provider_source()
        source["bpm_timestamp_policy"] = "monitor_receive"
        values = {
            key: reading(
                key,
                value,
                1.0,
                monitor_received_at=100.0,
            )
            for key, value in {
                "bpm.BPM02.x": 2.0,
                "bpm.BPM03.x": 4.0,
                "bpm.BPM04.x": 8.0,
                "bpm.BPM05.x": 0.0,
            }.items()
        }
        values["fct"] = reading("fct", 10.0, 100.0)

        result = BpmDispersionProvider(FakeClient(values), source).sample(100.0)

        self.assertTrue(result.quality.valid, result.quality.reason)
        self.assertIn("monitor receive time", result.quality.reason)
        self.assertEqual(
            result.diagnostic_source_timestamps["bpm.BPM02.x"], 1.0
        )

    def test_monitor_receive_policy_holds_when_bpm_events_stop(self):
        source = provider_source()
        source["bpm_timestamp_policy"] = "monitor_receive"
        values = {
            key: reading(
                key,
                value,
                1.0,
                monitor_received_at=90.0,
            )
            for key, value in {
                "bpm.BPM02.x": 2.0,
                "bpm.BPM03.x": 4.0,
                "bpm.BPM04.x": 8.0,
                "bpm.BPM05.x": 0.0,
            }.items()
        }
        values["fct"] = reading("fct", 10.0, 100.0)

        result = BpmDispersionProvider(FakeClient(values), source).sample(100.0)

        self.assertFalse(result.quality.valid)
        self.assertIn("BPM monitor event", result.quality.reason)
        self.assertIn("stale", result.quality.reason)

    def test_static_hv_setpoint_timestamp_is_not_a_measurement_hold(self):
        source = provider_source()
        source["signal_keys"] = (*source["signal_keys"], "guard.hv_setpoint")
        source["source_timestamp_exempt_keys"] = ("guard.hv_setpoint",)
        values = {
            "bpm.BPM02.x": reading("bpm.BPM02.x", 2.0),
            "bpm.BPM03.x": reading("bpm.BPM03.x", 4.0),
            "bpm.BPM04.x": reading("bpm.BPM04.x", 8.0),
            "bpm.BPM05.x": reading("bpm.BPM05.x", 0.0),
            "fct": reading("fct", 10.0),
            "guard.hv_setpoint": reading("guard.hv_setpoint", 39.0, 1.0),
        }

        result = BpmDispersionProvider(FakeClient(values), source).sample(100.0)

        self.assertTrue(result.quality.valid, result.quality.reason)


class WindowAndControllerTests(unittest.TestCase):
    def test_nonoverlapping_window_is_single_use_and_bad_sample_resets(self):
        window = NonOverlappingMedianWindow(3)
        self.assertIsNone(window.add(1.0, {"x": 1.0}, valid=True))
        self.assertIsNone(window.add(None, {}, valid=False))
        self.assertEqual(window.pending, 0)
        self.assertIsNone(window.add(3.0, {"x": 30.0}, valid=True))
        self.assertIsNone(window.add(1.0, {"x": 10.0}, valid=True))
        completed = window.add(2.0, {"x": 20.0}, valid=True)
        self.assertEqual(completed, (1, 2.0, {"x": 20.0}))
        self.assertEqual(window.pending, 0)
        self.assertIsNone(window.add(4.0, {"x": 40.0}, valid=True))

    def test_window_supports_median_and_mean_aggregation(self):
        results = {}
        for aggregation in ("median", "mean"):
            window = NonOverlappingAggregateWindow(3, aggregation=aggregation)
            window.add(1.0, {"x": 10.0}, valid=True)
            window.add(2.0, {"x": 20.0}, valid=True)
            results[aggregation] = window.add(100.0, {"x": 1000.0}, valid=True)

        self.assertEqual(results["median"], (1, 2.0, {"x": 20.0}))
        self.assertEqual(results["mean"][0], 1)
        self.assertAlmostEqual(results["mean"][1], 103.0 / 3.0)
        self.assertAlmostEqual(results["mean"][2]["x"], 1030.0 / 3.0)
        with self.assertRaisesRegex(ValueError, "median.*mean"):
            NonOverlappingAggregateWindow(3, aggregation="latest")

    def test_sampling_and_control_periods_are_resolved_independently(self):
        default = derive_control_timing(0.1, 10.0)
        self.assertEqual(default.window_samples, 100)
        self.assertAlmostEqual(default.effective_control_period_s, 10.0)

        rounded_up = derive_control_timing(0.3, 10.0)
        self.assertEqual(rounded_up.window_samples, 34)
        self.assertAlmostEqual(rounded_up.effective_control_period_s, 10.2)
        with self.assertRaisesRegex(ValueError, "must not be shorter"):
            derive_control_timing(1.0, 0.5)

    def test_incremental_i_pi_sign_and_frozen_state(self):
        controller = IncrementalPIController(kp=0.0, ki=0.1, period_s=10.0)
        integral = controller.propose(error=2.0, response=-2.0)
        self.assertAlmostEqual(integral.p_component, 0.0)
        self.assertAlmostEqual(integral.i_component, -1.0)

        controller.configure(kp=0.5, ki=0.1, period_s=10.0)
        first = controller.propose(error=2.0, response=-2.0)
        second_without_commit = controller.propose(error=1.0, response=-2.0)
        self.assertAlmostEqual(first.p_component, 0.0)
        self.assertAlmostEqual(second_without_commit.p_component, 0.0)
        controller.commit(first)
        after_commit = controller.propose(error=1.0, response=-2.0)
        self.assertAlmostEqual(after_commit.p_component, 0.25)

    def test_target_capture_and_session_guards(self):
        source = {
            "calibration": dict(CALIBRATION),
            "quadrupole_ids": ("QM05", "QM06"),
        }
        coordinator = FirFeedbackCoordinator(source, window_samples=2, kp=0.0, ki=0.1, period_s=2.0)
        diagnostics1 = {
            "bpm.BPM05.x": 1.0,
            "fct": 10.0,
            "quad.QM05": 5.0,
            "quad.QM06": 6.0,
        }
        diagnostics2 = {
            "bpm.BPM05.x": 1.2,
            "fct": 12.0,
            "quad.QM05": 5.0,
            "quad.QM06": 6.0,
        }
        self.assertIsNone(coordinator.accept(measurement(3.0, diagnostics1)))
        self.assertIsNone(coordinator.accept(measurement(99.0, diagnostics2)))
        self.assertEqual(coordinator.window.pending, 1)
        completed = coordinator.accept(
            measurement(5.0, diagnostics2, timestamp=100.1)
        )
        self.assertIsNotNone(completed)
        reference = coordinator.capture_latest()
        self.assertEqual(reference.target_q_mm, 4.0)
        self.assertAlmostEqual(reference.bpm05_x_mm, 1.1)
        self.assertEqual(reference.fct, 11.0)
        self.assertEqual(check_session_guards(completed.aggregates, reference, CALIBRATION), "")

        bad_bpm = dict(completed.aggregates, **{"bpm.BPM05.x": 2.0})
        self.assertIn("BPM05", check_session_guards(bad_bpm, reference, CALIBRATION))
        bad_quad = dict(completed.aggregates, **{"quad.QM05": 5.2})
        self.assertIn("QM05", check_session_guards(bad_quad, reference, CALIBRATION))


class IrfelProfileTests(unittest.TestCase):
    def test_unready_calibration_allows_acquisition_but_blocks_feedback(self):
        context = load_app_context(
            "energy_feedback", machine_id="irfel", control_backend="real"
        )
        config = load_runtime_config(context)
        self.assertEqual(config["measurement_kind"], "bpm_dispersion")
        timing = derive_control_timing(
            config["runtime"]["sample_period_s"],
            config["controller"]["control_period_s"],
        )
        self.assertAlmostEqual(config["runtime"]["sample_period_s"], 0.2)
        self.assertEqual(timing.window_samples, 50)
        self.assertAlmostEqual(timing.effective_control_period_s, 10.0)
        self.assertEqual(config["controller"]["window_aggregation"], "median")
        self.assertEqual(
            config["measurement_sources"][config["measurement_source_id"]][
                "bpm_timestamp_policy"
            ],
            "monitor_receive",
        )
        self.assertNotIn("bpm.BPM01.s", config["pvs"])
        self.assertFalse(
            any(
                key.endswith(".s")
                for key in config["measurement_sources"][
                    config["measurement_source_id"]
                ]["signal_keys"]
            )
        )
        self.assertEqual(config["pvs"]["fct"], "IRFEL:BD:CT:CH1:WAV:ASUM")
        source = config["measurement_sources"][config["measurement_source_id"]]
        self.assertEqual(
            source["source_timestamp_exempt_keys"],
            (source["guard_keys"]["hv_setpoint"],),
        )
        self.assertTrue(config["write_enabled"])
        self.assertFalse(config["feedback_ready"])
        self.assertIn("not marked ready", config["feedback_block_reason"])
        self.assertEqual(
            real_commissioning_status(context, "energy_feedback"),
            REAL_STATUS_COMMISSIONING,
        )
        self.assertTrue(workflow_writes_allowed(context, "energy_feedback"))

    def test_resolved_hv_safety_config_builds_and_accepts_reference(self):
        context = load_app_context(
            "energy_feedback", machine_id="irfel", control_backend="real"
        )
        config = load_runtime_config(context)
        source = config["measurement_sources"][config["measurement_source_id"]]
        checker = build_rf_hv_safety_checker(source)
        reference = source["rf_safety"]["reference"]
        aggregate = {
            "hv_setpoint": float(reference["hv_kv"]),
            "hv_readback": float(reference["hv_kv"]),
        }
        for channel_id, values in reference["channels"].items():
            aggregate[f"rf.{channel_id}.amplitude"] = float(values["amplitude"])
            aggregate[f"rf.{channel_id}.phase"] = float(values["phase_deg"])

        self.assertEqual(checker.cfg.hv_min_kv, 38.5)
        self.assertEqual(checker.cfg.hv_max_kv, 39.5)
        result = checker.check_aggregate(aggregate)
        self.assertTrue(result.ok, result.reason)

    def test_calibration_requires_all_finite_fields_and_version(self):
        calibration = dict(CALIBRATION)
        self.assertEqual(calibration_issue(calibration), "")
        calibration["a"] = math.nan
        self.assertIn("not finite", calibration_issue(calibration))
        calibration = dict(CALIBRATION, version="")
        self.assertIn("version", calibration_issue(calibration))

    def test_legacy_mev_measurement_remains_generic_observable_fallback(self):
        legacy = EnergyMeasurement(
            1.0,
            1.0,
            None,
            12.0,
            10.0,
            2.0,
            {},
            {},
            MeasurementQuality("OK", "valid", True),
        )
        self.assertEqual(legacy.control_value, 12.0)
        self.assertEqual(legacy.control_target, 10.0)
        self.assertEqual(legacy.control_error, 2.0)
        self.assertEqual(legacy.observable_unit, "MeV")

    def test_irfel_hv_write_is_verified_and_failed_verification_rolls_back(self):
        context = load_app_context(
            "energy_feedback", machine_id="irfel", control_backend="real"
        )
        group = load_runtime_config(context)["control_groups"][0]
        actuator = group.actuators[0]
        decision = FeedbackDecision(
            "SHADOW",
            "valid feedback suggestion",
            1.0,
            knob_delta=0.02,
            commands=(
                ActuatorCommand(
                    actuator.id,
                    39.0,
                    0.02,
                    39.02,
                    actuator.unit,
                ),
            ),
        )

        verified_client = FakeHvClient(
            actuator.setpoint_key, actuator.readback_key, verify_write=True
        )
        applied = apply_feedback_decision(
            verified_client,
            group,
            decision,
            timeout_s=0.01,
            poll_interval_s=0.001,
        )
        self.assertEqual(applied.state, "APPLIED")
        self.assertTrue(applied.write_verified)
        self.assertAlmostEqual(verified_client.value, 39.02)

        failed_client = FakeHvClient(
            actuator.setpoint_key, actuator.readback_key, verify_write=False
        )
        held = apply_feedback_decision(
            failed_client,
            group,
            decision,
            timeout_s=0.01,
            poll_interval_s=0.001,
        )
        self.assertEqual(held.state, "HOLD")
        self.assertFalse(held.write_verified)
        self.assertTrue(held.rollback_applied)
        self.assertAlmostEqual(failed_client.value, 39.0)
        self.assertEqual(failed_client.writes[-1], (actuator.setpoint_key, 39.0))


if __name__ == "__main__":
    unittest.main()
