from __future__ import annotations

import copy
import math
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

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
    observable_calibration_issue,
    read_closed_loop_inputs,
    shadow_calibration_issue,
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
    MachineProfileError,
    REAL_STATUS_COMMISSIONING,
    load_app_context,
    real_commissioning_status,
    validate_energy_feedback_workflow,
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
        monitor_received_at=(
            timestamp if monitor_received_at is None else monitor_received_at
        ),
    )


class FakeClient:
    def __init__(self, values: dict[str, PVReading]):
        self.values = values
        self.fresh_reads: list[bool] = []

    def read_many(self, keys, *, fresh=False):
        self.fresh_reads.append(bool(fresh))
        return {key: self.values[key] for key in keys}


class FakeHvClient:
    def __init__(self, setpoint_key: str, readback_key: str, *, verify_write: bool):
        self.setpoint_key = setpoint_key
        self.readback_key = readback_key
        self.value = 39.0
        self.verify_write = verify_write
        self.timestamp = 100.0
        self.writes: list[tuple[str, float]] = []
        self.fresh_reads: list[bool] = []

    def read_many(self, keys, *, fresh=False):
        self.fresh_reads.append(bool(fresh))
        return {key: reading(key, self.value, self.timestamp) for key in keys}

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
        "fct",
    )
    return {
        "id": "fir",
        "observable": {"label": "q", "unit": "mm"},
        "geometry": {},
        "calibration": dict(CALIBRATION),
        "resolved_signals": {},
        "stale_timeout_s": 3.0,
        "signal_keys": signal_keys,
        "bpm_signal_keys": signal_keys[:-1],
        "measurement_keys": signal_keys[:-1],
        "fct_key": "fct",
        "q_keys": ("bpm.BPM02.x", "bpm.BPM03.x", "bpm.BPM04.x"),
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
        sample_token=(timestamp,),
    )


class BpmDispersionProviderTests(unittest.TestCase):
    def test_q_formula_uses_three_horizontal_bpms(self):
        values = {
            "bpm.BPM02.x": reading("bpm.BPM02.x", 2.0),
            "bpm.BPM03.x": reading("bpm.BPM03.x", 4.0),
            "bpm.BPM04.x": reading("bpm.BPM04.x", 8.0),
            "fct": reading("fct", 10.0),
        }
        result = BpmDispersionProvider(FakeClient(values), provider_source()).sample(100.0)
        self.assertTrue(result.quality.valid, result.quality.reason)
        self.assertAlmostEqual(result.control_value, 8.0)
        self.assertIsNone(result.energy_mev)
        self.assertEqual(result.observable_unit, "mm")

    def test_source_timestamp_skew_is_diagnostic_only(self):
        values = {
            "bpm.BPM02.x": reading("bpm.BPM02.x", 2.0, 100.0),
            "bpm.BPM03.x": reading("bpm.BPM03.x", 4.0, 100.0),
            "bpm.BPM04.x": reading("bpm.BPM04.x", 8.0, 100.3),
            "fct": reading("fct", 10.0, 100.0),
        }
        provider = BpmDispersionProvider(FakeClient(values), provider_source())
        skewed = provider.sample(100.0)
        self.assertTrue(skewed.quality.valid, skewed.quality.reason)
        self.assertEqual(
            skewed.diagnostic_source_timestamps["bpm.BPM04.x"], 100.3
        )

        values["bpm.BPM04.x"] = reading("bpm.BPM04.x", 8.0, 100.0)
        values["fct"] = reading("fct", 0.2, 100.0)
        low_beam = provider.sample(100.0)
        self.assertTrue(low_beam.quality.valid, low_beam.quality.reason)

    def test_disconnected_fct_is_diagnostic_only_during_monitoring(self):
        values = {
            "bpm.BPM02.x": reading("bpm.BPM02.x", 2.0),
            "bpm.BPM03.x": reading("bpm.BPM03.x", 4.0),
            "bpm.BPM04.x": reading("bpm.BPM04.x", 8.0),
            "fct": PVReading(
                "fct",
                "PV:fct",
                None,
                False,
                None,
                "not connected",
            ),
        }

        result = BpmDispersionProvider(
            FakeClient(values), provider_source()
        ).sample(100.0)

        self.assertTrue(result.quality.valid, result.quality.reason)
        self.assertIn("disconnected", result.diagnostic_errors["fct"])

    def test_closed_loop_inputs_fail_closed_when_fct_is_disconnected(self):
        source = {
            "closed_loop_keys": ("fct",),
        }
        values = {
            "fct": PVReading(
                "fct",
                "PV:fct",
                None,
                False,
                None,
                "not connected",
            )
        }

        client = FakeClient(values)
        resolved, issue = read_closed_loop_inputs(client, source, 100.0)

        self.assertEqual(resolved, {})
        self.assertIn("fct", issue)
        self.assertIn("disconnected", issue)
        self.assertEqual(client.fresh_reads, [True])

    def test_closed_loop_inputs_fail_closed_when_client_raises(self):
        source = {"closed_loop_keys": ("fct",)}

        resolved, issue = read_closed_loop_inputs(FakeClient({}), source, 100.0)

        self.assertEqual(resolved, {})
        self.assertIn("closed-loop input read failure", issue)
        self.assertIn("fct", issue)

    def test_monitor_events_accept_legacy_bpm_source_timestamps(self):
        source = provider_source()
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
            }.items()
        }
        values["fct"] = reading("fct", 10.0, 100.0)

        result = BpmDispersionProvider(FakeClient(values), source).sample(100.0)

        self.assertTrue(result.quality.valid, result.quality.reason)
        self.assertIn("diagnostic only", result.quality.reason)
        self.assertEqual(result.sample_token, (100.0, 100.0, 100.0))
        self.assertEqual(
            result.diagnostic_source_timestamps["bpm.BPM02.x"], 1.0
        )

    def test_repeated_monitor_event_does_not_advance_fir_window(self):
        source = provider_source()
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
            }.items()
        }
        values["fct"] = reading("fct", 10.0, 100.0)

        provider = BpmDispersionProvider(FakeClient(values), source)
        result = provider.sample(100.0)
        coordinator = FirFeedbackCoordinator(
            source, window_samples=2, kp=0.0, ki=0.1, period_s=2.0
        )

        self.assertTrue(result.quality.valid, result.quality.reason)
        self.assertIsNone(coordinator.accept(result))
        self.assertEqual(coordinator.window.pending, 1)
        self.assertIsNone(coordinator.accept(result))
        self.assertEqual(coordinator.window.pending, 1)

        for key in source["bpm_signal_keys"]:
            values[key] = replace(values[key], monitor_received_at=91.0)
        self.assertIsNotNone(coordinator.accept(provider.sample(100.2)))

    def test_static_hv_setpoint_timestamp_is_not_a_measurement_hold(self):
        source = provider_source()
        source["signal_keys"] = (*source["signal_keys"], "guard.hv_setpoint")
        values = {
            "bpm.BPM02.x": reading("bpm.BPM02.x", 2.0),
            "bpm.BPM03.x": reading("bpm.BPM03.x", 4.0),
            "bpm.BPM04.x": reading("bpm.BPM04.x", 8.0),
            "fct": reading("fct", 10.0),
            "guard.hv_setpoint": reading("guard.hv_setpoint", 39.0, 1.0),
        }

        result = BpmDispersionProvider(FakeClient(values), source).sample(100.0)

        self.assertTrue(result.quality.valid, result.quality.reason)

    def test_source_timestamps_do_not_gate_any_irfel_signal(self):
        source = provider_source()
        values = {
            "bpm.BPM02.x": reading("bpm.BPM02.x", 2.0, 1.0),
            "bpm.BPM03.x": reading("bpm.BPM03.x", 4.0, 2.0),
            "bpm.BPM04.x": reading("bpm.BPM04.x", 8.0, 3.0),
            "fct": reading("fct", 10.0, 5.0),
        }

        result = BpmDispersionProvider(FakeClient(values), source).sample(100.0)

        self.assertTrue(result.quality.valid, result.quality.reason)
        self.assertIn("diagnostic only", result.quality.reason)


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
        source = {"calibration": dict(CALIBRATION)}
        coordinator = FirFeedbackCoordinator(
            source,
            window_samples=2,
            kp=0.0,
            ki=0.1,
            period_s=2.0,
        )
        diagnostics1 = {"fct": 10.0}
        diagnostics2 = {"fct": 12.0}
        self.assertIsNone(coordinator.accept(measurement(3.0, diagnostics1)))
        self.assertIsNone(coordinator.accept(measurement(99.0, diagnostics2)))
        self.assertEqual(coordinator.window.pending, 1)
        completed = coordinator.accept(
            measurement(5.0, diagnostics2, timestamp=100.1)
        )
        self.assertIsNotNone(completed)
        reference = coordinator.capture_latest()
        self.assertEqual(reference.target_q_mm, 4.0)
        self.assertEqual(reference.window_sequence, completed.sequence)
        self.assertEqual(
            check_session_guards(completed.aggregates, CALIBRATION),
            "",
        )

        low_fct = dict(completed.aggregates, fct=0.2)
        self.assertIn(
            "FCT",
            check_session_guards(low_fct, CALIBRATION),
        )


class IrfelProfileTests(unittest.TestCase):
    def test_compact_profile_expands_self_contained_safety_values(self):
        context = load_app_context(
            "energy_feedback", machine_id="irfel", control_backend="real"
        )
        raw = context.profile.workflows["energy_feedback"]
        self.assertEqual(raw["actuator"], {"element": "MODULATOR_HV1"})
        self.assertEqual(
            set(raw["feedback"]),
            {
                "sample_period_s",
                "control_period_s",
                "ki",
                "action_range_mm",
                "max_step_kv",
                "max_session_delta_kv",
            },
        )
        for implementation_key in (
            "control_groups",
            "controller",
            "operator_controls",
            "runtime",
            "logging",
        ):
            self.assertNotIn(implementation_key, raw)
        self.assertNotIn("source", raw["safety"])
        self.assertNotIn("hv", raw["safety"])
        self.assertNotIn("rf_channels", raw["safety"])
        self.assertEqual(
            raw["safety"]["beam_present"],
            {"element": "FCT1", "minimum": 1000},
        )
        self.assertEqual(
            raw["safety"]["rf_phase_elements"],
            {"acc1": "KLY1_CH3_PHASE", "buncher": "KLY1_CH4_PHASE"},
        )
        self.assertEqual(raw["safety"]["phase_limit_deg"], 3)
        self.assertAlmostEqual(
            raw["safety"]["setpoint_readback_tolerance_kv"], 0.02
        )
        self.assertEqual(
            raw["measurement"]["calibration"]["orbit_subtraction"],
            {"a": 0.0, "b": 0.0},
        )
        self.assertNotIn("fct", raw["measurement"])
        self.assertNotIn("observable", raw["measurement"])
        self.assertNotIn("poll_interval_s", raw["write_verification"])
        self.assertNotIn("bpm_timestamp_policy", raw["measurement"])
        self.assertNotIn("source_timestamp_policy", raw["measurement"])
        self.assertNotIn("max_timestamp_skew_s", raw["measurement"])

        config = load_runtime_config(context)
        workflow = config["workflow"]
        safety = workflow["safety"]
        limits = safety["limits"]
        group = workflow["control_groups"][0]
        actuator = group["actuators"][0]

        self.assertEqual(safety["id"], "irfel_energy_feedback")
        self.assertEqual(
            [channel["id"] for channel in safety["rf_channels"]],
            ["acc1", "buncher"],
        )
        self.assertTrue(
            all("amplitude" not in channel for channel in safety["rf_channels"])
        )
        self.assertNotIn("default_feedback_channel", safety)
        self.assertNotIn("hv_range_kv", limits)
        self.assertAlmostEqual(
            limits["hv_readback_tolerance_kv"],
            raw["safety"]["setpoint_readback_tolerance_kv"],
        )
        self.assertEqual(
            limits["phase_limit_deg"], {"acc1": 3, "buncher": 3}
        )
        self.assertNotIn("amplitude_ratio_limit_rel", limits)
        self.assertNotIn("feedback_amplitude_range_rel", limits)
        self.assertTrue(limits["require_valid_pv"])
        self.assertTrue(limits["hold_on_fault"])
        self.assertAlmostEqual(group["loop_gain"], raw["feedback"]["ki"])
        self.assertAlmostEqual(group["max_knob_step"], actuator["max_step"])
        self.assertAlmostEqual(
            actuator["max_step"], raw["feedback"]["max_step_kv"]
        )
        self.assertAlmostEqual(
            actuator["max_session_delta"],
            raw["feedback"]["max_session_delta_kv"],
        )
        self.assertEqual(actuator["element"], "MODULATOR_HV1")
        self.assertEqual(actuator["setpoint"], "voltage_set")
        self.assertEqual(actuator["readback"], "voltage_readback")
        self.assertEqual(actuator["unit"], "kV")
        self.assertAlmostEqual(actuator["coefficient"], 1.0)
        self.assertAlmostEqual(
            actuator["verify_tolerance"],
            raw["write_verification"]["readback_tolerance_kv"],
        )
        self.assertEqual(actuator["command_basis"], "setpoint")
        self.assertNotIn("operating_range", actuator)
        self.assertAlmostEqual(config["write_verification"]["poll_interval_s"], 0.05)
        self.assertEqual(config["write_verification"]["command_precision"], 3)
        self.assertAlmostEqual(config["runtime"]["stale_timeout_s"], 3.0)
        self.assertTrue(config["operator_controls"]["actuator_ratio"]["hidden"])

    def test_fail_closed_policies_cannot_be_disabled_in_the_profile(self):
        context = load_app_context(
            "energy_feedback", machine_id="irfel", control_backend="real"
        )
        for key in ("require_valid_pv", "hold_on_fault"):
            with self.subTest(key=key):
                workflow = copy.deepcopy(load_runtime_config(context)["workflow"])
                workflow["safety"]["limits"][key] = False
                with self.assertRaisesRegex(
                    MachineProfileError, rf"{key}.*must be true"
                ):
                    validate_energy_feedback_workflow(
                        context.profile, workflow, control_backend="real"
                    )

    def test_commissioning_calibration_allows_feedback(self):
        context = load_app_context(
            "energy_feedback", machine_id="irfel", control_backend="real"
        )
        raw = context.profile.workflows["energy_feedback"]
        config = load_runtime_config(context)
        self.assertEqual(config["measurement_kind"], "bpm_dispersion")
        timing = derive_control_timing(
            config["runtime"]["sample_period_s"],
            config["controller"]["control_period_s"],
        )
        self.assertAlmostEqual(
            config["runtime"]["sample_period_s"],
            raw["feedback"]["sample_period_s"],
        )
        self.assertAlmostEqual(
            timing.effective_control_period_s,
            raw["feedback"]["control_period_s"],
        )
        self.assertEqual(config["controller"]["window_aggregation"], "median")
        source = config["measurement_sources"][config["measurement_source_id"]]
        self.assertNotIn("bpm_timestamp_policy", source)
        self.assertNotIn("source_timestamp_policy", source)
        self.assertNotIn("max_timestamp_skew_s", source)
        calibration = source["calibration"]
        self.assertEqual(observable_calibration_issue(calibration), "")
        self.assertEqual(shadow_calibration_issue(calibration), "")
        self.assertTrue(calibration["ready"])
        self.assertEqual(
            calibration["version"], "irfel-fir-commissioning-20261105-v1"
        )
        self.assertAlmostEqual(calibration["response_q_mm_per_kv"], 15.0)
        self.assertAlmostEqual(calibration["fct_min"], 1000.0)
        self.assertAlmostEqual(calibration["q_action_range_mm"], 3.0)
        self.assertEqual(
            source["measurement_keys"],
            ("bpm.BPM02.x", "bpm.BPM03.x", "bpm.BPM04.x"),
        )
        self.assertEqual(source["signal_keys"], source["measurement_keys"])
        self.assertIn("fct", source["closed_loop_keys"])
        self.assertFalse(
            any(
                key.startswith("bpm.BPM01")
                or key.startswith("bpm.BPM05")
                or key.endswith(".y")
                or key.startswith("quad.")
                for key in source["signal_keys"]
            )
        )
        self.assertEqual(config["pvs"]["fct"], "IRFEL:BD:CT:CH1:WAV:ASUM")
        actuator = config["control_groups"][0].actuators[0]
        self.assertIsNone(actuator.operating_range)
        self.assertEqual(actuator.machine_limit, (0.0, 45.0))
        self.assertTrue(config["write_enabled"])
        self.assertTrue(config["feedback_ready"])
        self.assertEqual(config["feedback_block_reason"], "")
        self.assertEqual(
            real_commissioning_status(context, "energy_feedback"),
            REAL_STATUS_COMMISSIONING,
        )
        self.assertTrue(workflow_writes_allowed(context, "energy_feedback"))

    def test_resolved_safety_config_captures_and_accepts_field_reference(self):
        context = load_app_context(
            "energy_feedback", machine_id="irfel", control_backend="real"
        )
        config = load_runtime_config(context)
        source = config["measurement_sources"][config["measurement_source_id"]]
        self.assertFalse(
            any("amplitude" in key for key in source["closed_loop_keys"])
        )
        aggregate = {
            "hv_setpoint": 39.0,
            "hv_readback": 39.0,
            "rf.acc1.phase": -158.2,
            "rf.buncher.phase": -21.0,
        }
        checker = build_rf_hv_safety_checker(source, aggregate)

        self.assertEqual(source["rf_safety"]["profile_id"], "irfel_energy_feedback")
        self.assertNotIn("reference", source["rf_safety"])
        self.assertEqual(
            source["rf_safety"]["reference_capture"]["mode"],
            "set_current_as_target",
        )
        self.assertIsNone(checker.cfg.hv_min_kv)
        self.assertIsNone(checker.cfg.hv_max_kv)
        self.assertEqual(checker.ref.hv_kv, 39.0)
        self.assertEqual(checker.ref.channel_phases["acc1"], -158.2)
        self.assertEqual(checker.ref.channel_amplitudes, {})
        result = checker.check_aggregate(aggregate)
        self.assertTrue(result.ok, result.reason)
        outside_old_app_range = dict(
            aggregate, hv_setpoint=40.0, hv_readback=40.0
        )
        result = checker.check_aggregate(outside_old_app_range, hv_next=40.02)
        self.assertTrue(result.ok, result.reason)

    def test_missing_machine_hv_limit_means_no_software_absolute_limit(self):
        context = load_app_context(
            "energy_feedback", machine_id="irfel", control_backend="real"
        )
        element_type = type(context.profile.get_element("MODULATOR_HV1"))
        original_limits_for = element_type.limits_for

        def limits_without_hv_setpoint(element, channel):
            if element.id == "MODULATOR_HV1" and channel == "voltage_set":
                return {}
            return original_limits_for(element, channel)

        with patch.object(element_type, "limits_for", limits_without_hv_setpoint):
            actuator = load_runtime_config(context)["control_groups"][0].actuators[0]

        self.assertIsNone(actuator.operating_range)
        self.assertIsNone(actuator.machine_limit)

    def test_field_safety_reference_rejects_incomplete_snapshot(self):
        context = load_app_context(
            "energy_feedback", machine_id="irfel", control_backend="real"
        )
        config = load_runtime_config(context)
        source = config["measurement_sources"][config["measurement_source_id"]]
        with self.assertRaisesRegex(ValueError, "missing fields"):
            build_rf_hv_safety_checker(
                source,
                {"hv_setpoint": 39.0, "hv_readback": 39.0},
            )

    def test_calibration_readiness_is_layered(self):
        calibration = dict(CALIBRATION)
        self.assertEqual(observable_calibration_issue(calibration), "")
        self.assertEqual(shadow_calibration_issue(calibration), "")
        self.assertEqual(calibration_issue(calibration), "")

        missing_response = dict(calibration, response_q_mm_per_kv=None)
        self.assertEqual(observable_calibration_issue(missing_response), "")
        self.assertIn("response", shadow_calibration_issue(missing_response))

        missing_fct = dict(calibration, fct_min=None)
        self.assertEqual(shadow_calibration_issue(missing_fct), "")
        self.assertIn("fct_min", calibration_issue(missing_fct))

        invalid_observable = dict(calibration, a=math.nan)
        self.assertIn(
            "not finite",
            observable_calibration_issue(invalid_observable),
        )
        self.assertIn("version", calibration_issue(dict(calibration, version="")))

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
        self.assertEqual(verified_client.timestamp, 100.0)
        self.assertTrue(all(verified_client.fresh_reads))

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
